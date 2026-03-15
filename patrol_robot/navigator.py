#!/usr/bin/env python3
"""
navigator.py
------------
Waypoint navigator for the patrol robot.
- Loads waypoints from config/waypoints.yaml via ament package share dir
- go_to(waypoint_name) / go_home() — blocking, return bool
- Built-in stuck recovery:
    1. Watchdog (2 Hz) detects no translation AND no rotation over STUCK_TIME_S
    2. Cancels current Nav2 goal
    3. Reads /scan → finds sector with lowest avg range (nearest obstacle)
    4. Rotates in-place toward the OPPOSITE direction
    5. Drives forward RECOVERY_FWD_TIME seconds
    6. Re-sends the same Nav2 goal
    7. Repeats up to MAX_RECOVERY_ATTEMPTS, then returns False
"""

import math
import time
import threading

import yaml
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.duration import Duration

from geometry_msgs.msg import PoseStamped, Twist
from sensor_msgs.msg import LaserScan
from nav2_msgs.action import NavigateToPose
from ament_index_python.packages import get_package_share_directory
import tf2_ros
import os


# ── Stuck detection ───────────────────────────────────────────────────────────
STUCK_TIME_S         = 6.0    # observation window (s)
STUCK_DIST_THRESHOLD = 0.08   # metres of travel required in window
STUCK_YAW_THRESHOLD  = 0.12   # radians of heading change required in window
STUCK_GRACE_S        = 4.0    # ignore first N seconds after goal is sent
WATCHDOG_HZ          = 2      # watchdog check rate

# ── Recovery motion ───────────────────────────────────────────────────────────
RECOVERY_OMEGA        = 0.6   # rotation speed during alignment (rad/s)
RECOVERY_ALIGN_THRESH = 0.15  # stop rotating when |error| < this (rad)
RECOVERY_LINEAR       = 0.15  # forward speed during escape (m/s)
RECOVERY_FWD_TIME     = 1.5   # seconds of forward escape motion
RECOVERY_SCAN_WINDOW  = 30    # ± degrees around each candidate heading
MAX_RECOVERY_ATTEMPTS = 5     # give up after this many failed recoveries
ESCAPE_CANDIDATES     = 12    # number of headings evaluated by scan analyser
# ─────────────────────────────────────────────────────────────────────────────


class Navigator:
    def __init__(self, node: Node, waypoints_file: str = None):
        self.node = node

        # ── Waypoints ─────────────────────────────────────────────────────────
        if waypoints_file is None:
            pkg_share = get_package_share_directory('patrol_robot')
            waypoints_file = os.path.join(pkg_share, 'config', 'waypoints.yaml')
        self.waypoints = self._load_waypoints(waypoints_file)

        # ── Nav2 action client ────────────────────────────────────────────────
        self._nav_client = ActionClient(node, NavigateToPose, 'navigate_to_pose')

        # ── TF2 ──────────────────────────────────────────────────────────────
        self._tf_buffer   = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, node)

        # ── LiDAR scan cache ─────────────────────────────────────────────────
        self._latest_scan: LaserScan = None
        self._scan_lock = threading.Lock()
        node.create_subscription(LaserScan, '/scan', self._scan_cb, 10)

        # ── cmd_vel publisher (recovery manoeuvres) ───────────────────────────
        self._cmd_vel_pub = node.create_publisher(Twist, '/cmd_vel', 10)

        # ── Active goal state ─────────────────────────────────────────────────
        self._active_gh             = None
        self._active_goal_pose      = None
        self._goal_send_time        = None
        self._recovery_count        = 0
        self._in_recovery           = False
        self._pose_history          = []
        self._watchdog_timer        = None

        # ── Blocking gate (set when navigation is fully done or failed) ───────
        self._nav_done_event = threading.Event()
        self._nav_succeeded  = False

    # ═════════════════════════════════════════════════════════════════════════
    # Public API
    # ═════════════════════════════════════════════════════════════════════════

    def go_to(self, waypoint_name: str) -> bool:
        """Navigate to a named waypoint. Blocks until done. Returns success."""
        if waypoint_name not in self.waypoints:
            self.node.get_logger().error(f'Unknown waypoint: {waypoint_name}')
            return False

        wp = self.waypoints[waypoint_name]
        pose = self._make_pose(wp['x'], wp['y'], wp['qz'], wp['qw'])

        self._wait_for_nav2()
        self._send_nav_goal(pose, reset_recovery=True)

        # Spin until the goal is done (success or failure)
        while not self._nav_done_event.is_set():
            rclpy.spin_once(self.node, timeout_sec=0.1)

        return self._nav_succeeded

    def go_home(self) -> bool:
        return self.go_to('home')

    # ═════════════════════════════════════════════════════════════════════════
    # Nav2 helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _wait_for_nav2(self):
        self.node.get_logger().info('Waiting for Nav2 action server...')
        while not self._nav_client.wait_for_server(timeout_sec=5.0):
            self.node.get_logger().warn('Nav2 not ready yet, retrying...')
        self.node.get_logger().info('Nav2 ready.')

    def _send_nav_goal(self, goal_pose: PoseStamped, reset_recovery: bool = True):
        if reset_recovery:
            self._recovery_count = 0

        self._active_goal_pose = goal_pose
        self._active_gh        = None
        self._goal_send_time   = time.time()
        self._pose_history.clear()
        self._in_recovery      = False
        self._nav_done_event.clear()
        self._nav_succeeded    = False

        nav_goal      = NavigateToPose.Goal()
        nav_goal.pose = goal_pose

        future = self._nav_client.send_goal_async(
            nav_goal, feedback_callback=self._nav_feedback_cb)
        future.add_done_callback(self._nav_accepted_cb)

        self._stop_watchdog()
        self._watchdog_timer = self.node.create_timer(
            1.0 / WATCHDOG_HZ, self._watchdog_tick)

    def _nav_accepted_cb(self, future):
        gh = future.result()
        if not gh.accepted:
            self.node.get_logger().error('Nav2 goal rejected.')
            self._stop_watchdog()
            self._nav_succeeded = False
            self._nav_done_event.set()
            return
        self._active_gh = gh
        gh.get_result_async().add_done_callback(self._nav_result_cb)

    def _nav_feedback_cb(self, feedback_msg):
        dist = feedback_msg.feedback.distance_remaining
        self.node.get_logger().info(
            f'  Nav2 distance remaining: {dist:.2f} m',
            throttle_duration_sec=5.0)

    def _nav_result_cb(self, future):
        # Ignore callbacks that fire during a recovery cancel
        if self._in_recovery:
            return
        self._stop_watchdog()
        status = future.result().status
        if status == 4:   # SUCCEEDED
            self.node.get_logger().info('Navigation SUCCEEDED.')
            self._nav_succeeded = True
        else:
            self.node.get_logger().error(f'Navigation FAILED (status={status}).')
            self._nav_succeeded = False
        self._nav_done_event.set()

    # ═════════════════════════════════════════════════════════════════════════
    # Stuck watchdog
    # ═════════════════════════════════════════════════════════════════════════

    def _watchdog_tick(self):
        if self._in_recovery or self._active_gh is None:
            return

        pose = self._get_robot_pose()
        if pose is None:
            return
        x, y, yaw = pose
        now = time.time()

        # Grace period — don't check immediately after sending goal
        if now - self._goal_send_time < STUCK_GRACE_S:
            return

        self._pose_history.append((now, x, y, yaw))
        cutoff = now - STUCK_TIME_S
        self._pose_history = [p for p in self._pose_history if p[0] >= cutoff]

        if len(self._pose_history) < 3:
            return

        oldest = self._pose_history[0]
        dist = math.sqrt((x - oldest[1]) ** 2 + (y - oldest[2]) ** 2)
        dyaw = abs(_angle_diff(yaw, oldest[3]))

        if dist < STUCK_DIST_THRESHOLD and dyaw < STUCK_YAW_THRESHOLD:
            self.node.get_logger().warn(
                f'[STUCK] dist={dist:.3f} m  Δyaw={math.degrees(dyaw):.1f}° '
                f'over {STUCK_TIME_S} s — triggering recovery '
                f'(attempt {self._recovery_count + 1}/{MAX_RECOVERY_ATTEMPTS})')
            self._trigger_recovery()

    def _stop_watchdog(self):
        if self._watchdog_timer is not None:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    # ═════════════════════════════════════════════════════════════════════════
    # Recovery
    # ═════════════════════════════════════════════════════════════════════════

    def _trigger_recovery(self):
        if self._recovery_count >= MAX_RECOVERY_ATTEMPTS:
            self.node.get_logger().error(
                f'Exceeded {MAX_RECOVERY_ATTEMPTS} recovery attempts — FAILED.')
            self._stop_watchdog()
            self._nav_succeeded = False
            self._nav_done_event.set()
            return

        self._recovery_count += 1
        self._in_recovery = True
        self._stop_watchdog()

        if self._active_gh is not None:
            self.node.get_logger().info('[RECOVERY] Cancelling Nav2 goal...')
            cancel_future = self._active_gh.cancel_goal_async()
            cancel_future.add_done_callback(self._recovery_after_cancel)
        else:
            threading.Thread(target=self._escape_thread, daemon=True).start()

    def _recovery_after_cancel(self, future):
        self.node.get_logger().info('[RECOVERY] Goal cancelled. Starting escape...')
        time.sleep(0.3)   # let Nav2 fully release /cmd_vel
        threading.Thread(target=self._escape_thread, daemon=True).start()

    def _escape_thread(self):
        """Rotate away from nearest obstacle, drive forward, re-send goal."""
        self.node.get_logger().info('[RECOVERY] Starting escape manoeuvre...')

        # 1. Find escape heading
        escape_yaw = self._find_escape_heading()

        # 2. Rotate toward escape heading
        pose = self._get_robot_pose()
        if pose is not None:
            heading_error = _angle_diff(escape_yaw, pose[2])
            self.node.get_logger().info(
                f'[RECOVERY] Escape yaw={math.degrees(escape_yaw):.1f}°  '
                f'current={math.degrees(pose[2]):.1f}°  '
                f'error={math.degrees(heading_error):.1f}°')

            deadline = time.time() + 5.0
            while abs(heading_error) > RECOVERY_ALIGN_THRESH \
                    and time.time() < deadline:
                omega = math.copysign(
                    min(RECOVERY_OMEGA, max(0.15, abs(heading_error) * 0.8)),
                    heading_error)
                self._pub_vel(0.0, omega)
                time.sleep(0.05)
                updated = self._get_robot_pose()
                if updated:
                    heading_error = _angle_diff(escape_yaw, updated[2])

            self._pub_vel(0.0, 0.0)
            time.sleep(0.1)

        # 3. Drive forward to escape inflation zone
        self.node.get_logger().info(
            f'[RECOVERY] Driving forward for {RECOVERY_FWD_TIME} s...')
        deadline = time.time() + RECOVERY_FWD_TIME
        while time.time() < deadline:
            self._pub_vel(RECOVERY_LINEAR, 0.0)
            time.sleep(0.05)

        self._pub_vel(0.0, 0.0)
        time.sleep(0.2)

        # 4. Re-send original Nav2 goal
        self.node.get_logger().info('[RECOVERY] Escape complete. Re-sending Nav2 goal...')
        self._in_recovery = False
        self._send_nav_goal(self._active_goal_pose, reset_recovery=False)

    def _find_escape_heading(self) -> float:
        """Return world-frame heading pointing AWAY from the nearest obstacle."""
        with self._scan_lock:
            scan = self._latest_scan

        pose     = self._get_robot_pose()
        robot_yaw = pose[2] if pose else 0.0

        if scan is None or len(scan.ranges) == 0:
            self.node.get_logger().warn(
                '[RECOVERY] No scan available — reversing along current heading.')
            return _normalise_angle(robot_yaw + math.pi)

        n    = len(scan.ranges)
        half = int(RECOVERY_SCAN_WINDOW / 360.0 * n)

        worst_avg     = float('inf')
        obstacle_frac = 0.0

        step = max(1, n // ESCAPE_CANDIDATES)
        for k in range(ESCAPE_CANDIDATES):
            centre  = k * step
            indices = [(centre + offset) % n
                       for offset in range(-half, half + 1)]
            valid   = [scan.ranges[i] for i in indices
                       if not math.isnan(scan.ranges[i])
                       and not math.isinf(scan.ranges[i])
                       and scan.range_min < scan.ranges[i] < scan.range_max]
            if not valid:
                continue
            avg = sum(valid) / len(valid)
            if avg < worst_avg:
                worst_avg     = avg
                obstacle_frac = centre / n

        # LiDAR index 0 = right → robot_yaw − π/2 in world frame
        obstacle_robot_angle = obstacle_frac * 2.0 * math.pi
        obstacle_world_angle = _normalise_angle(
            robot_yaw - math.pi / 2.0 + obstacle_robot_angle)
        escape_world_angle   = _normalise_angle(obstacle_world_angle + math.pi)

        self.node.get_logger().info(
            f'[RECOVERY] Nearest obstacle frac={obstacle_frac:.2f}  '
            f'world={math.degrees(obstacle_world_angle):.1f}°  '
            f'avg_range={worst_avg:.2f} m  '
            f'→ escape={math.degrees(escape_world_angle):.1f}°')
        return escape_world_angle

    # ═════════════════════════════════════════════════════════════════════════
    # Utilities
    # ═════════════════════════════════════════════════════════════════════════

    def _load_waypoints(self, path: str) -> dict:
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return data.get('waypoints', {})

    def _make_pose(self, x: float, y: float, qz: float, qw: float) -> PoseStamped:
        pose = PoseStamped()
        pose.header.frame_id    = 'map'
        pose.header.stamp       = self.node.get_clock().now().to_msg()
        pose.pose.position.x    = x
        pose.pose.position.y    = y
        pose.pose.orientation.x = 0.0
        pose.pose.orientation.y = 0.0
        pose.pose.orientation.z = qz
        pose.pose.orientation.w = qw
        return pose

    def _get_robot_pose(self):
        """Return (x, y, yaw) in map frame, or None if TF unavailable."""
        for frame in ('base_footprint', 'base_link'):
            try:
                tf = self._tf_buffer.lookup_transform(
                    'map', frame,
                    rclpy.time.Time(),
                    timeout=Duration(seconds=0.5))
                x   = tf.transform.translation.x
                y   = tf.transform.translation.y
                q   = tf.transform.rotation
                yaw = math.atan2(
                    2.0 * (q.w * q.z + q.x * q.y),
                    1.0 - 2.0 * (q.y * q.y + q.z * q.z))
                return x, y, yaw
            except (tf2_ros.LookupException,
                    tf2_ros.ConnectivityException,
                    tf2_ros.ExtrapolationException):
                continue
        return None

    def _scan_cb(self, msg: LaserScan):
        with self._scan_lock:
            self._latest_scan = msg

    def _pub_vel(self, linear: float, angular: float):
        twist = Twist()
        twist.linear.x  = linear
        twist.angular.z = angular
        self._cmd_vel_pub.publish(twist)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _angle_diff(a: float, b: float) -> float:
    """Signed difference a − b, wrapped to (−π, π]."""
    return _normalise_angle(a - b)


def _normalise_angle(a: float) -> float:
    while a >  math.pi: a -= 2.0 * math.pi
    while a <= -math.pi: a += 2.0 * math.pi
    return a
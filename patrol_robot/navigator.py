#!/usr/bin/env python3
import yaml
import math
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from ament_index_python.packages import get_package_share_directory  # ← add this
import os


class Navigator:
    def __init__(self, node: Node, waypoints_file: str = None):
        self.node = node
        self.nav = BasicNavigator()

        # ← use package share dir instead of __file__ relative path
        if waypoints_file is None:
            pkg_share = get_package_share_directory('patrol_robot')
            waypoints_file = os.path.join(pkg_share, 'config', 'waypoints.yaml')

        self.waypoints = self._load_waypoints(waypoints_file)

    def _load_waypoints(self, path: str) -> dict:
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return data.get('waypoints', {})

    def _make_pose(self, x: float, y: float, qz: float, qw: float) -> PoseStamped:
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.x = 0.0
        pose.pose.orientation.y = 0.0
        pose.pose.orientation.z = qz
        pose.pose.orientation.w = qw
        return pose

    def go_to(self, waypoint_name: str) -> bool:
        if waypoint_name not in self.waypoints:
            self.node.get_logger().error(f'Unknown waypoint: {waypoint_name}')
            return False

        wp = self.waypoints[waypoint_name]
        pose = self._make_pose(wp['x'], wp['y'], wp['qz'], wp['qw'])
        self.node.get_logger().info(f'Navigating to [{waypoint_name}]...')

        self.nav.goToPose(pose)
        while not self.nav.isTaskComplete():
            pass

        result = self.nav.getResult()
        success = (result == TaskResult.SUCCEEDED)
        self.node.get_logger().info(
            f'Navigation {"succeeded" if success else "FAILED"} → {waypoint_name}'
        )
        return success

    def go_home(self) -> bool:
        return self.go_to('home')
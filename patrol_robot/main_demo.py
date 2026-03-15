#!/usr/bin/env python3
"""
Patrol Robot — Main Demo
State machine: IDLE → NAVIGATING → SCANNING → RETURNING → IDLE
"""

import rclpy
from rclpy.node import Node
import os
import time

from .glowforge_monitor import GlowforgeMonitor
from .human_detector     import HumanDetector
from .motor_controller   import MotorController
from .navigator          import Navigator
from .alert_sender       import send_alert


# ── Config ────────────────────────────────────────
GLOWFORGE_EMAIL    = 'sunyhg@uw.edu'
GLOWFORGE_PASSWORD = 'sunyuhang'
IDLE_POLL_SEC      = 10

# Map: machine serial → waypoint name (add more for multiple machines)
MACHINE_WAYPOINTS = {
    'default': 'laser_station_1',   # fallback for any machine
    # 'GF-ABC123': 'laser_station_2',
}
# ─────────────────────────────────────────────────


class PatrolRobotNode(Node):
    def __init__(self):
        super().__init__('patrol_robot')
        self.get_logger().info('Patrol Robot initializing...')

        self.glowforge = GlowforgeMonitor(GLOWFORGE_EMAIL, GLOWFORGE_PASSWORD)
        self.motor     = MotorController(self)
        self.navigator = Navigator(self)
        self.detector  = HumanDetector(self)

        self.state = 'INIT'
        self.active_machine = None

    def run(self):
        # ── Login to Glowforge ──
        self.get_logger().info('Logging into Glowforge...')
        if not self.glowforge.login():
            self.get_logger().error('Glowforge login failed. Exiting.')
            return
        self.get_logger().info('Glowforge login successful.')

        self.state = 'IDLE'
        self.get_logger().info('Entering main loop...')

        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.1)

            # ══════════════════════════════════════════
            if self.state == 'IDLE':
                self.get_logger().info(
                    f'[IDLE] Polling Glowforge (every {IDLE_POLL_SEC}s)...'
                )
                machines = self.glowforge.get_running_machines()

                if machines:
                    self.active_machine = machines[0]   # monitor first running machine
                    name = self.active_machine['name']
                    user = self.active_machine['username']
                    self.get_logger().info(
                        f'[IDLE] ⚠ Machine RUNNING: {name} (user: {user})'
                    )
                    self.state = 'NAVIGATING'
                else:
                    self.get_logger().info('[IDLE] No machines running. Waiting...')
                    time.sleep(IDLE_POLL_SEC)

            # ══════════════════════════════════════════
            elif self.state == 'NAVIGATING':
                serial   = self.active_machine.get('serial', 'default')
                waypoint = MACHINE_WAYPOINTS.get(serial,
                           MACHINE_WAYPOINTS['default'])

                self.get_logger().info(f'[NAVIGATING] → {waypoint}')
                success = self.navigator.go_to(waypoint)

                if success:
                    self.get_logger().info('[NAVIGATING] Arrived. Opening motor...')
                    self.motor.open_mouth()
                    self.state = 'SCANNING'
                else:
                    self.get_logger().error('[NAVIGATING] Navigation failed! Returning home.')
                    self.state = 'RETURNING'

            # ══════════════════════════════════════════
            elif self.state == 'SCANNING':
                self.get_logger().info('[SCANNING] Running human detection (3s)...')
                human_present = self.detector.is_human_present()

                if human_present:
                    self.get_logger().info(
                        '[SCANNING] ✅ Human detected — machine is supervised. Returning.'
                    )
                else:
                    self.get_logger().warn(
                        '[SCANNING] ❌ No human detected — sending alert!'
                    )
                    send_alert(self.active_machine)

                self.state = 'RETURNING'

            # ══════════════════════════════════════════
            elif self.state == 'RETURNING':
                self.get_logger().info('[RETURNING] Closing motor...')
                self.motor.close_mouth()

                # Check if another machine is already running before going home
                machines = self.glowforge.get_running_machines()

                # Filter out the machine we just visited
                current_serial = self.active_machine.get('serial', '') if self.active_machine else ''
                next_machines = [m for m in machines if m.get('serial', '') != current_serial]

                if next_machines:
                    self.active_machine = next_machines[0]
                    name = self.active_machine['name']
                    user = self.active_machine['username']
                    self.get_logger().info(
                        f'[RETURNING] Another machine running: {name} (user: {user}) — skipping home.'
                    )
                    self.state = 'NAVIGATING'
                else:
                    self.get_logger().info('[RETURNING] No other machines running. Navigating home.')
                    self.navigator.go_home()
                    self.active_machine = None
                    self.state = 'IDLE'


def main():
    rclpy.init()
    node = PatrolRobotNode()
    try:
        node.run()
    except KeyboardInterrupt:
        node.get_logger().info('Stopped by user.')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
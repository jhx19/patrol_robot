#!/usr/bin/env python3
"""
Patrol Robot — Main Demo
State machine: IDLE → NAVIGATING → SCANNING → RETURNING → IDLE

ROS2 parameters:
  sim_data_file (str, default ''):
      Path to a fake JSON file for simulation mode.
      If empty, real Glowforge API is used.
"""

import rclpy
from rclpy.node import Node
import time

from .glowforge_monitor import GlowforgeMonitor
from .human_detector     import HumanDetector
from .motor_controller   import MotorController
from .navigator          import Navigator
from .alert_sender       import send_alert
from .credentials import load_credentials


# ── Config ────────────────────────────────────────────────────────────────────
IDLE_POLL_SEC      = 10

MACHINE_WAYPOINTS = {
    'WYC-332': 'glowforge_001',   # Glowforge-2F-01
    'VVD-329': 'glowforge_temp',   # Glowforge-2F-02
    'RRV-334': 'glowforge_003',   # Glowforge-2F-03
    'JRM-724': 'glowforge_004',   # Glowforge-2F-04
    'HVW-296': 'glowforge_005',   # Glowforge-2F-05
    'HCK-847': 'glowforge_006',   # Glowforge-2F-06
    'default': 'glowforge_001',   # safety fallback
}
# ─────────────────────────────────────────────────────────────────────────────


class PatrolRobotNode(Node):
    def __init__(self):
        super().__init__('patrol_robot')
        creds = load_credentials()

        # ── Sim mode parameter ────────────────────────────────────────────────
        self.declare_parameter('sim_data_file', '')
        sim_data_file = self.get_parameter('sim_data_file') \
                            .get_parameter_value().string_value

        if sim_data_file:
            self.get_logger().info(
                f'*** SIMULATION MODE *** data file: {sim_data_file}')
            self.glowforge = GlowforgeMonitor(sim_data_file=sim_data_file)
        else:
            self.get_logger().info('Real Glowforge API mode.')
            self.glowforge = GlowforgeMonitor(
                email=creds['glowforge']['email'],
                password=creds['glowforge']['password'],
            )

        self.motor     = MotorController(self)
        self.navigator = Navigator(self)
        self.detector  = HumanDetector(self)

        self.state          = 'INIT'
        self.active_machine = None

    def run(self):
        # Login (no-op in sim mode)
        if not self.glowforge._sim_mode:
            self.get_logger().info('Logging into Glowforge...')
            if not self.glowforge.login():
                self.get_logger().error('Glowforge login failed. Exiting.')
                return
            self.get_logger().info('Glowforge login successful.')

        self.state = 'IDLE'
        self.get_logger().info('Entering main loop...')

        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.1)

            # ══════════════════════════════════════════════════════════════════
            if self.state == 'IDLE':
                self.get_logger().info(
                    f'[IDLE] Polling Glowforge (every {IDLE_POLL_SEC}s)...')
                machines = self.glowforge.get_running_machines()

                if machines:
                    self.active_machine = machines[0]
                    self.get_logger().info(
                        f'[IDLE] ⚠ Machine RUNNING: '
                        f'{self.active_machine["name"]} '
                        f'(user: {self.active_machine["username"]})')
                    self.state = 'NAVIGATING'
                else:
                    self.get_logger().info('[IDLE] No machines running. Waiting...')
                    time.sleep(IDLE_POLL_SEC)

            # ══════════════════════════════════════════════════════════════════
            elif self.state == 'NAVIGATING':
                serial   = self.active_machine.get('serial', 'default')
                waypoint = MACHINE_WAYPOINTS.get(serial,
                           MACHINE_WAYPOINTS['default'])

                self.get_logger().info(f'[NAVIGATING] → {waypoint}')
                success = self.navigator.go_to(waypoint)

                if success:
                    self.get_logger().info('[NAVIGATING] Arrived. Opening mouth...')
                    self.motor.open_mouth()
                    self.state = 'SCANNING'
                else:
                    self.get_logger().error(
                        '[NAVIGATING] Navigation failed! Returning home.')
                    self.state = 'RETURNING'

            # ══════════════════════════════════════════════════════════════════
            elif self.state == 'SCANNING':
                self.get_logger().info('[SCANNING] Running human detection...')
                human_present = self.detector.is_human_present()

                if human_present:
                    self.get_logger().info(
                        '[SCANNING] ✅ Human detected — machine supervised.')
                else:
                    self.get_logger().warn(
                        '[SCANNING] ❌ No human — sending alert!')
                    send_alert(self.active_machine)

                self.state = 'RETURNING'

            # ══════════════════════════════════════════════════════════════════
            elif self.state == 'RETURNING':
                self.get_logger().info('[RETURNING] Closing mouth...')
                self.motor.close_mouth()

                # Check for another running machine before going home
                machines = self.glowforge.get_running_machines()
                current_serial = self.active_machine.get('serial', '') \
                                 if self.active_machine else ''
                next_machines = [m for m in machines
                                 if m.get('serial', '') != current_serial]

                if next_machines:
                    self.active_machine = next_machines[0]
                    self.get_logger().info(
                        f'[RETURNING] Another machine running: '
                        f'{self.active_machine["name"]} — skipping home.')
                    self.state = 'NAVIGATING'
                else:
                    self.get_logger().info(
                        '[RETURNING] No other machines. Navigating home.')
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
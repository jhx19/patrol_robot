#!/usr/bin/env python3
"""Controls the 3rd Dynamixel motor (gix) via JointTrajectory topic."""

import rclpy
from rclpy.node import Node
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from builtin_interfaces.msg import Duration
import time


OPEN_POSITION  = -1.7   # radians — mouth open
CLOSE_POSITION = -3.0   # radians — mouth closed
MOVE_DURATION  = 2      # seconds


class MotorController:
    def __init__(self, node: Node):
        self.node = node
        self.publisher = node.create_publisher(
            JointTrajectory,
            '/gix_controller/joint_trajectory',
            10
        )

    def _send_position(self, position: float):
        msg = JointTrajectory()
        msg.joint_names = ['gix']
        point = JointTrajectoryPoint()
        point.positions = [position]
        point.time_from_start = Duration(sec=MOVE_DURATION)
        msg.points = [point]
        self.publisher.publish(msg)
        self.node.get_logger().info(f'Motor → {position:.2f} rad')

    def open_mouth(self):
        self._send_position(OPEN_POSITION)
        time.sleep(MOVE_DURATION + 0.5)

    def close_mouth(self):
        self._send_position(CLOSE_POSITION)
        time.sleep(MOVE_DURATION + 0.5)
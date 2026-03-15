#!/usr/bin/env python3
"""Human detection client — calls /detect_human service repeatedly."""

import time
from std_srvs.srv import Trigger
from rclpy.node import Node


SCAN_DURATION_SEC   = 3.0
HUMAN_RATIO_THRESH  = 0.75   # 75% of frames must detect human


class HumanDetector:
    def __init__(self, node: Node):
        self.node = node
        self.client = node.create_client(Trigger, 'detect_human')

    def _call_once(self) -> bool:
        if not self.client.wait_for_service(timeout_sec=3.0):
            self.node.get_logger().warn('detect_human service not available')
            return False
        future = self.client.call_async(Trigger.Request())
        # Spin until done
        import rclpy
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=5.0)
        if future.result() is not None:
            return future.result().success
        return False

    def is_human_present(self) -> bool:
        """
        Scan for SCAN_DURATION_SEC seconds.
        Returns True if ≥75% of polled frames detect a human.
        """
        end_time = time.time() + SCAN_DURATION_SEC
        total, positives = 0, 0

        while time.time() < end_time:
            result = self._call_once()
            total += 1
            if result:
                positives += 1
            time.sleep(0.1)   # ~10 calls/sec

        if total == 0:
            return False

        ratio = positives / total
        self.node.get_logger().info(
            f'Scan result: {positives}/{total} frames with human ({ratio:.0%})'
        )
        return ratio >= HUMAN_RATIO_THRESH
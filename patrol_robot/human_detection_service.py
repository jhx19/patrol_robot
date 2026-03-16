#!/usr/bin/env python3
"""
Human Detection Service Node (ONNX-based, no PyTorch required)
- Subscribes to /image_raw and queues incoming frames
- Exposes a ROS2 service /detect_human (std_srvs/Trigger)
- When called, collects 10 distinct frames over 2 seconds
- Returns success=True if at least 7/10 frames contain a person
"""

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

import cv2
import numpy as np
import onnxruntime as ort
import os
import time
import queue

PERSON_CLASS_ID      = 0
CONFIDENCE_THRESHOLD = 0.5
INPUT_SIZE           = (640, 640)

# Detection window config
DETECTION_FRAMES     = 10     # number of distinct frames to collect
DETECTION_WINDOW_SEC = 2.0    # max time to wait for frames
DETECTION_THRESHOLD  = 7      # minimum frames with human to confirm presence


class HumanDetectionService(Node):
    def __init__(self):
        super().__init__('human_detection_service')

        # --- Parameters ---
        self.declare_parameter('model_path', os.path.expanduser('~/yolov8n.onnx'))
        self.declare_parameter('confidence_threshold', CONFIDENCE_THRESHOLD)
        self.declare_parameter('image_topic', '/image_raw')

        model_path          = self.get_parameter('model_path').get_parameter_value().string_value
        self.conf_threshold = self.get_parameter('confidence_threshold').get_parameter_value().double_value
        image_topic         = self.get_parameter('image_topic').get_parameter_value().string_value

        # --- Load ONNX model ---
        self.get_logger().info(f'Loading ONNX model from: {model_path}')
        if not os.path.exists(model_path):
            self.get_logger().error(f'Model not found at {model_path}!')
            raise FileNotFoundError(f'ONNX model not found: {model_path}')

        self.session    = ort.InferenceSession(
            model_path, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.get_logger().info('ONNX model loaded successfully.')

        # --- Frame queue (holds distinct incoming frames) ---
        self.bridge      = CvBridge()
        self.frame_queue = queue.Queue(maxsize=DETECTION_FRAMES)

        # --- Subscriber ---
        self.create_subscription(Image, image_topic, self._image_callback, 10)

        # --- Service ---
        self.create_service(Trigger, 'detect_human', self._handle_detect_human)

        self.get_logger().info(
            f'Human detection service ready. '
            f'Confirms human if {DETECTION_THRESHOLD}/{DETECTION_FRAMES} '
            f'frames detect a person within {DETECTION_WINDOW_SEC}s.')

    # ------------------------------------------------------------------
    # Camera callback — enqueue each distinct incoming frame
    # ------------------------------------------------------------------
    def _image_callback(self, msg: Image):
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        if not self.frame_queue.full():
            self.frame_queue.put(frame)

    # ------------------------------------------------------------------
    # Service handler
    # ------------------------------------------------------------------
    def _handle_detect_human(self, request, response):
        # Clear any stale frames from before this call
        while not self.frame_queue.empty():
            self.frame_queue.get()

        self.get_logger().info(
            f'[detect_human] Collecting {DETECTION_FRAMES} fresh frames '
            f'(up to {DETECTION_WINDOW_SEC}s)...')

        # Spin until we have enough frames or time runs out
        deadline = time.time() + DETECTION_WINDOW_SEC
        while self.frame_queue.qsize() < DETECTION_FRAMES \
                and time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)

        collected = self.frame_queue.qsize()

        if collected == 0:
            response.success = False
            response.message = 'No camera frames received'
            self.get_logger().warn(response.message)
            return response

        self.get_logger().info(
            f'  Collected {collected}/{DETECTION_FRAMES} frames — '
            f'running inference...')

        # Run YOLO on each collected frame
        human_count = 0
        i = 0
        while not self.frame_queue.empty():
            i += 1
            frame    = self.frame_queue.get()
            detected = self._detect_person(frame)
            if detected:
                human_count += 1
            self.get_logger().info(
                f'  Frame {i}/{collected}: '
                f'{"✅ human" if detected else "❌ no human"} '
                f'({human_count} detections so far)')

        human_found      = human_count >= DETECTION_THRESHOLD
        response.success = human_found
        response.message = (
            f'Human CONFIRMED: {human_count}/{collected} frames '
            f'(threshold: {DETECTION_THRESHOLD})'
            if human_found else
            f'Human NOT confirmed: only {human_count}/{collected} frames '
            f'(need {DETECTION_THRESHOLD})'
        )
        self.get_logger().info(f'detect_human → {response.message}')
        return response

    # ------------------------------------------------------------------
    # ONNX inference — single frame
    # ------------------------------------------------------------------
    def _detect_person(self, frame: np.ndarray) -> bool:
        blob, _, _   = self._preprocess(frame)
        outputs      = self.session.run(None, {self.input_name: blob})

        # YOLOv8 output: (1, 84, 8400) → transpose → (8400, 84)
        predictions  = outputs[0][0].T
        class_scores = predictions[:, 4:]
        class_ids    = np.argmax(class_scores, axis=1)
        confidences  = class_scores[np.arange(len(class_scores)), class_ids]

        person_mask  = (class_ids == PERSON_CLASS_ID) & \
                       (confidences >= self.conf_threshold)
        return bool(np.any(person_mask))

    def _preprocess(self, frame: np.ndarray):
        h, w               = frame.shape[:2]
        target_h, target_w = INPUT_SIZE
        scale              = min(target_w / w, target_h / h)
        new_w, new_h       = int(w * scale), int(h * scale)
        resized            = cv2.resize(frame, (new_w, new_h))

        pad_w  = (target_w - new_w) // 2
        pad_h  = (target_h - new_h) // 2
        padded = cv2.copyMakeBorder(
            resized,
            pad_h, target_h - new_h - pad_h,
            pad_w, target_w - new_w - pad_w,
            cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        img  = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        img  = img.astype(np.float32) / 255.0
        img  = np.transpose(img, (2, 0, 1))
        blob = np.expand_dims(img, axis=0)
        return blob, scale, (pad_w, pad_h)


def main():
    rclpy.init()
    try:
        node = HumanDetectionService()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError as e:
        print(f'[ERROR] {e}')
    finally:
        rclpy.shutdown()


if __name__ == '__main__':
    main()
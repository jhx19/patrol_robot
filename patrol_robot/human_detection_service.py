#!/usr/bin/env python3
"""
Human Detection Service Node (ONNX-based, no PyTorch required)
- Subscribes to /image_raw and caches the latest frame
- Exposes a ROS2 service /detect_human (std_srvs/Trigger)
- Runs YOLOv8n ONNX inference only when service is called
- Returns success=True if a person (class 0) is detected
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

# YOLOv8 COCO class 0 = person
PERSON_CLASS_ID = 0
CONFIDENCE_THRESHOLD = 0.5
IOU_THRESHOLD = 0.45
INPUT_SIZE = (640, 640)  # YOLOv8 default input size


class HumanDetectionService(Node):
    def __init__(self):
        super().__init__('human_detection_service')

        # --- Parameters ---
        self.declare_parameter('model_path', os.path.expanduser('~/yolov8n.onnx'))
        self.declare_parameter('confidence_threshold', CONFIDENCE_THRESHOLD)
        self.declare_parameter('image_topic', '/image_raw')

        model_path = self.get_parameter('model_path').get_parameter_value().string_value
        self.conf_threshold = self.get_parameter('confidence_threshold').get_parameter_value().double_value
        image_topic = self.get_parameter('image_topic').get_parameter_value().string_value

        # --- Load ONNX model ---
        self.get_logger().info(f'Loading ONNX model from: {model_path}')
        if not os.path.exists(model_path):
            self.get_logger().error(f'Model not found at {model_path}!')
            raise FileNotFoundError(f'ONNX model not found: {model_path}')

        self.session = ort.InferenceSession(
            model_path,
            providers=['CPUExecutionProvider']
        )
        self.input_name = self.session.get_inputs()[0].name
        self.get_logger().info('ONNX model loaded successfully.')

        # --- State ---
        self.bridge = CvBridge()
        self.latest_frame = None

        # --- Subscriber: cache latest camera frame ---
        self.create_subscription(Image, image_topic, self._image_callback, 10)

        # --- Service: run detection on demand ---
        self.create_service(Trigger, 'detect_human', self._handle_detect_human)

        self.get_logger().info('Human detection service ready. Call /detect_human to trigger.')

    # ------------------------------------------------------------------
    # Camera callback — just cache, no processing
    # ------------------------------------------------------------------
    def _image_callback(self, msg: Image):
        self.latest_frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')

    # ------------------------------------------------------------------
    # Service handler — run YOLO on cached frame
    # ------------------------------------------------------------------
    def _handle_detect_human(self, request, response):
        if self.latest_frame is None:
            response.success = False
            response.message = 'No camera frame received yet'
            self.get_logger().warn(response.message)
            return response

        human_found = self._detect_person(self.latest_frame)

        response.success = human_found
        response.message = 'Human detected' if human_found else 'No human detected'
        self.get_logger().info(f'detect_human → {response.message}')
        return response

    # ------------------------------------------------------------------
    # ONNX inference
    # ------------------------------------------------------------------
    def _detect_person(self, frame: np.ndarray) -> bool:
        # Preprocess
        blob, scale, pad = self._preprocess(frame)

        # Run inference
        outputs = self.session.run(None, {self.input_name: blob})

        # YOLOv8 output shape: (1, 84, 8400)
        # 84 = 4 (box) + 80 (class scores)
        predictions = outputs[0][0]  # shape: (84, 8400)
        predictions = predictions.T  # shape: (8400, 84)

        # Extract boxes and class scores
        boxes = predictions[:, :4]         # cx, cy, w, h
        class_scores = predictions[:, 4:]  # 80 classes

        # Get best class per detection
        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(len(class_scores)), class_ids]

        # Filter: only person class with sufficient confidence
        person_mask = (class_ids == PERSON_CLASS_ID) & (confidences >= self.conf_threshold)

        return bool(np.any(person_mask))

    def _preprocess(self, frame: np.ndarray):
        """Resize with letterboxing, normalize to [0,1], add batch dim."""
        h, w = frame.shape[:2]
        target_h, target_w = INPUT_SIZE

        # Scale keeping aspect ratio
        scale = min(target_w / w, target_h / h)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(frame, (new_w, new_h))

        # Letterbox padding
        pad_w = (target_w - new_w) // 2
        pad_h = (target_h - new_h) // 2
        padded = cv2.copyMakeBorder(
            resized, pad_h, target_h - new_h - pad_h,
            pad_w, target_w - new_w - pad_w,
            cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        # BGR → RGB, HWC → CHW, normalize
        img = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        blob = np.expand_dims(img, axis=0)  # (1, 3, 640, 640)

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
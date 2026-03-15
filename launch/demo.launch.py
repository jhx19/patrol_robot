from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([

        # 1. Human detection service (YOLO)
        Node(
            package='patrol_robot',
            executable='human_detection',
            name='human_detection_service',
            parameters=[{
                'model_path': '/home/ubuntu/turtlebot3_ws/yolov8n.onnx',
                'image_topic': '/image_raw',
            }]
        ),

        # 2. Main patrol state machine
        Node(
            package='patrol_robot',
            executable='patrol_robot',
            name='patrol_robot',
            output='screen',
        ),
    ])
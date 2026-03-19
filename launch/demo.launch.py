"""
demo.launch.py
--------------
Single-command launch for the patrol robot demo.
Uses a 2-probe event-driven chain with a retry loop for the initial pose.

Startup chain:

  1. Pi bringup + camera start immediately.

  2. PROBE 1 — wait_for_odom:
     Waits for /odom messages to flow from the Pi.
     The Pi's ros2_control only broadcasts odom after OpenCR firmware
     has fully initialised (serial, baudrate, IMU calibration, motors ACTIVE).
     → Triggers: Nav2 launch + Probe 2

  3. PROBE 2 — wait_for_map_tf (with initial pose retry loop):
     Publishes /initialpose every 2 seconds until AMCL confirms it by
     broadcasting the map→odom transform on /tf.

     Why a retry loop, not a one-shot publish:
       AMCL's subscription to /initialpose is created in on_activate().
       The /reinitialize_global_localization service (our previous probe signal)
       is also created in on_activate(), but service registration in the ROS2
       middleware and subscription registration are not atomic — the service
       can appear on the service list before the subscriber is fully wired up.
       A one-shot publish that lands in that window is silently dropped and
       AMCL never logs "initialPoseReceived", so pfInitialized_ stays false,
       AMCL keeps warning "Please set the initial pose", and the map frame
       never appears. The retry loop guarantees AMCL eventually receives and
       acknowledges the pose regardless of when its subscription becomes ready.

     → Triggers: pi_human_detection + pi_motor_power + rviz
                 + 3s settle → patrol_robot (MISSION START)
"""

import os

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    RegisterEventHandler,
    LogInfo,
    TimerAction,
)
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


# Initial pose values — must match waypoints.yaml home waypoint
HOME_X  =  5.3054
HOME_Y  =  0.1956
HOME_QZ =  0.9980
HOME_QW =  0.0625

# Formatted as a single-line YAML string for ros2 topic pub
INITIAL_POSE_MSG = (
    '{'
    '"header": {"frame_id": "map"}, '
    '"pose": {'
    '"pose": {'
    f'"position": {{"x": {HOME_X}, "y": {HOME_Y}, "z": 0.0}}, '
    f'"orientation": {{"x": 0.0, "y": 0.0, "z": {HOME_QZ}, "w": {HOME_QW}}}'
    '}, '
    '"covariance": [0.25, 0.0, 0.0, 0.0, 0.0, 0.0, '
    '0.0, 0.25, 0.0, 0.0, 0.0, 0.0, '
    '0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '
    '0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '
    '0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '
    '0.0, 0.0, 0.0, 0.0, 0.0, 0.06853]'
    '}}'
)


def generate_launch_description():

    pkg_share = get_package_share_directory('patrol_robot')

    # ── Launch arguments ──────────────────────────────────────────────────────
    robot_ip_arg = DeclareLaunchArgument(
        'robot_ip', default_value='10.155.234.215',
        description='IP address of the Raspberry Pi on the robot')

    robot_user_arg = DeclareLaunchArgument(
        'robot_user', default_value='ubuntu',
        description='SSH username on the Raspberry Pi')

    map_file_arg = DeclareLaunchArgument(
        'map_file',
        default_value=os.path.join(pkg_share, 'maps', 'gix_map.yaml'),
        description='Full path to the Nav2 map YAML file')

    rviz_config_arg = DeclareLaunchArgument(
        'rviz_config',
        default_value=os.path.join(pkg_share, 'config', 'patrol_robot.rviz'),
        description='RViz config file')

    sim_data_arg = DeclareLaunchArgument(
        'sim_data',
        default_value='',
        description=(
            'Filename of fake Glowforge data JSON for simulation mode. '
            'File must be in <package_share>/test/. '
            'Examples: sim1.json  sim2.json  '
            'Leave empty for real Glowforge API.'
        )
    )

    robot_ip    = LaunchConfiguration('robot_ip')
    robot_user  = LaunchConfiguration('robot_user')
    map_file    = LaunchConfiguration('map_file')
    rviz_config = LaunchConfiguration('rviz_config')
    sim_data    = LaunchConfiguration('sim_data')

    # ── Pi environment preamble ───────────────────────────────────────────────
    SBC_SETUP = (
        "source /opt/ros/humble/setup.bash && "
        "source ~/turtlebot3_ws/install/setup.bash && "
        "export LDS_MODEL=LDS-01 && "
        "export TURTLEBOT3_MODEL=waffle && "
        "export ROS_DOMAIN_ID=38 && "
    )

    # ── Pi: robot bringup ─────────────────────────────────────────────────────
    pi_bringup = ExecuteProcess(
        cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
             PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
             PythonExpression([
                 f"'bash -lc \\\"{SBC_SETUP}"
                 "ros2 launch turtlebot3_gix_bringup hardware.launch.py\\\"'"
             ])],
        output='screen',
        name='pi_bringup',
    )

    # ── Pi: camera node ───────────────────────────────────────────────────────
    pi_camera = ExecuteProcess(
        cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
             PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
             PythonExpression([
                 f"'bash -lc \\\"{SBC_SETUP}"
                 "ros2 run v4l2_camera v4l2_camera_node "
                 "--ros-args -p video_device:=/dev/video0 "
                 "-p image_size:=[640,480]\\\"'"
             ])],
        output='screen',
        name='pi_camera',
    )

    # ── PROBE 1: odom is live ─────────────────────────────────────────────────
    # Wait for the Pi's ros2_control to fully activate and publish /odom.
    wait_for_odom = ExecuteProcess(
        name='wait_for_odom',
        cmd=[
            'bash', '-c',
            'echo "[patrol] PROBE 1: waiting for Pi /odom..."; '
            'until ros2 topic info /odom 2>/dev/null '
            '      | grep -q "Publisher count: [1-9]"; '
            'do sleep 1; done; '
            # Confirm messages are actually flowing (not just topic registered)
            'ros2 topic echo --once /odom > /dev/null 2>&1; '
            'sleep 2; '
            'echo "[patrol] PROBE 1 done: odom live → launching Nav2"'
        ],
        output='screen'
    )

    # ── Nav2 stack ────────────────────────────────────────────────────────────
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(
                get_package_share_directory('turtlebot3_navigation2'),
                'launch', 'navigation2.launch.py'
            )
        ),
        launch_arguments={
            'map': map_file,
            'use_sim_time': 'false',
        }.items(),
    )

    # ── PROBE 2: initial pose retry loop until map TF confirms ────────────────
    #
    # Problem with one-shot publish:
    #   AMCL creates its /initialpose subscriber in on_activate(), at the same
    #   time as the /reinitialize_global_localization service. Service
    #   registration and subscription registration are not atomic in the ROS2
    #   middleware — the service can be visible on `ros2 service list` while
    #   the subscription is still being wired up. A single publish that lands
    #   in this window is silently dropped. AMCL never logs "initialPoseReceived"
    #   and pfInitialized_ stays false, causing the endless
    #   "Please set the initial pose..." warning.
    #
    # Solution:
    #   Publish /initialpose every 2 seconds in a loop.
    #   Exit when the map→odom TF appears on /tf (i.e., AMCL confirmed the pose
    #   and started broadcasting the map frame).
    #   This is robust to any subscription timing race and also to AMCL
    #   restarting or being slow to process the first message.
    wait_for_map_tf = ExecuteProcess(
        name='wait_for_map_tf',
        cmd=[
            'bash', '-c',
            'echo "[patrol] PROBE 2: publishing initial pose (retrying every 2s until map TF confirms)..."; '
            # Loop: publish pose, check TF, repeat until map frame appears
            'until ros2 topic echo --once --no-daemon /tf 2>/dev/null '
            '      | grep -q "frame_id: map"; '
            'do '
            f'  ros2 topic pub --once /initialpose '
            f'    geometry_msgs/msg/PoseWithCovarianceStamped '
            f'    \'{INITIAL_POSE_MSG}\' > /dev/null 2>&1; '
            '  echo "[patrol] PROBE 2: pose published, waiting for map TF..."; '
            '  sleep 2; '
            'done; '
            # Buffer so costmap TF listeners see several consecutive transforms
            'sleep 2; '
            'echo "[patrol] PROBE 2 done: map TF confirmed → Nav2 fully usable"'
        ],
        output='screen'
    )

    # ── Pi: human detection service ───────────────────────────────────────────
    pi_human_detection = ExecuteProcess(
        cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
             PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
             PythonExpression([
                 f"'bash -lc \\\"{SBC_SETUP}"
                 "ros2 run patrol_robot human_detection "
                 "--ros-args "
                 "-p model_path:=/home/ubuntu/turtlebot3_ws/yolov8n.onnx "
                 "-p image_topic:=/image_raw\\\"'"
             ])],
        output='screen',
        name='pi_human_detection',
    )

    # ── Pi: enable motor power ────────────────────────────────────────────────
    pi_motor_power = ExecuteProcess(
        cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
             PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
             PythonExpression([
                 f"'bash -lc \\\"{SBC_SETUP}"
                 "ros2 service call /motor_power "
                 "std_srvs/srv/SetBool \\\\\\\"{data: true}\\\\\\\"\\\"'"
             ])],
        output='screen',
        name='pi_motor_enable',
    )

    # ── RViz ──────────────────────────────────────────────────────────────────
    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        arguments=['-d', rviz_config],
        output='screen',
    )

    # ── Patrol robot state machine ────────────────────────────────────────────
    patrol_robot_node = Node(
        package='patrol_robot',
        executable='patrol_robot',
        name='patrol_robot',
        output='screen',
        parameters=[{
            'sim_data_file': PythonExpression([
                "'' if '", sim_data, "' == '' else '",
                os.path.join(pkg_share, 'test'), "/' + '", sim_data, "'"
            ])
        }],
    )

    # ── Event chain ───────────────────────────────────────────────────────────
    #
    #   pi_bringup ──────────────────────────────────────► (runs forever)
    #   pi_camera  ──────────────────────────────────────► (runs forever)
    #   wait_for_odom  (PROBE 1, starts immediately)
    #         │ odom messages flowing
    #         ├─► nav2_launch
    #         └─► wait_for_map_tf  (PROBE 2: retry loop)
    #                   │ map→odom TF confirmed (AMCL accepted the pose)
    #                   ├─► pi_human_detection
    #                   ├─► pi_motor_power
    #                   ├─► rviz_node
    #                   └─► [3 s settle]
    #                             └─► patrol_robot  ← MISSION START

    on_odom_ready = RegisterEventHandler(
        OnProcessExit(
            target_action=wait_for_odom,
            on_exit=[
                LogInfo(msg='[patrol] PROBE 1 passed: odom live → launching Nav2 + initial pose loop'),
                nav2_launch,
                wait_for_map_tf,
            ]
        )
    )

    on_map_tf_ready = RegisterEventHandler(
        OnProcessExit(
            target_action=wait_for_map_tf,
            on_exit=[
                LogInfo(msg='[patrol] PROBE 2 passed: map TF live → starting application'),
                pi_human_detection,
                pi_motor_power,
                rviz_node,
                TimerAction(period=3.0, actions=[
                    LogInfo(msg='[patrol] ─── MISSION BEGINS ───'),
                    patrol_robot_node,
                ]),
            ]
        )
    )

    return LaunchDescription([
        robot_ip_arg,
        robot_user_arg,
        map_file_arg,
        rviz_config_arg,
        sim_data_arg,

        LogInfo(msg='[patrol] Starting Pi bringup + camera + odom probe...'),
        pi_bringup,
        pi_camera,
        wait_for_odom,

        on_odom_ready,
        on_map_tf_ready,
    ])
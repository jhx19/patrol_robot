"""
demo.launch.py
--------------
Single-command launch for the patrol robot demo.

Usage:
  ros2 launch patrol_robot demo.launch.py

Optional arguments:
  robot_ip:=<Pi IP>          default: 192.168.0.200
  robot_user:=<Pi username>  default: ubuntu
  map_file:=<path/to/map.yaml>  default: <package_share>/maps/gix_map.yaml

What this launches
──────────────────
 On the Raspberry Pi (via SSH):
   t= 0s  turtlebot3_gix_bringup  robot.launch.py
   t= 0s  v4l2_camera_node    (camera feed)
   t=10s  human_detection     (YOLO / ONNX service)
   t=12s  motor_power enable  (SetBool service call)

 On the Remote PC (direct):
   t= 5s  Nav2 stack          (map_server, AMCL, planners, controllers)
   t=18s  RViz2               (pre-configured view)
   t=20s  patrol_robot        (main state machine)

Prerequisites
─────────────
  • Passwordless SSH from Remote PC → Pi must be set up:
      ssh-copy-id ubuntu@<robot_ip>
  • Map saved at: src/patrol_robot/maps/gix_map.yaml  (+ .pgm)
  • yolov8n.onnx at: ~/turtlebot3_ws/yolov8n.onnx on the Pi
"""

import os

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    TimerAction,
    LogInfo,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch.conditions import IfCondition


# ── Timing constants (seconds) ────────────────────────────────────────────────
T_PI_BRINGUP      =  0.0   # SSH: robot bringup + camera
T_NAV2            = 15.0   # Nav2 stack (wait for Pi odom TF to be ready)
T_PI_DETECTION    = 20.0   # SSH: human detection service on Pi
T_PI_MOTOR_ENABLE = 22.0   # SSH: enable motor power
T_RVIZ            = 28.0   # RViz (after Nav2 map loads)
T_PATROL          = 30.0   # patrol robot state machine
# ─────────────────────────────────────────────────────────────────────────────


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
            'Examples: fake_one_machine.json  fake_two_machines.json '
            'Leave empty for real Glowforge API.'
        )
    )

    robot_ip    = LaunchConfiguration('robot_ip')
    robot_user  = LaunchConfiguration('robot_user')
    map_file    = LaunchConfiguration('map_file')
    rviz_config = LaunchConfiguration('rviz_config')
    sim_data    = LaunchConfiguration('sim_data')

    # ── SSH helper: builds "ssh user@ip 'bash -c \"...\""' ───────────────────
    def ssh_cmd(user, ip, bash_command: str) -> list:
        """
        Returns an ExecuteProcess cmd list that SSHes into the Pi and runs
        bash_command inside a login shell (so .bashrc env vars are loaded).
        """
        return [
            'ssh', '-o', 'StrictHostKeyChecking=no',
            PythonExpression(["'", user, "@", ip, "'"]),
            PythonExpression([
                f"'bash -lc \\\"source /opt/ros/humble/setup.bash && "
                f"source ~/turtlebot3_ws/install/setup.bash && "
                f"{bash_command}\\\"'"
            ]),
        ]

    # ── Pi processes ──────────────────────────────────────────────────────────
    # All SSH commands on the Pi must source ROS2 and set env vars first.
    SBC_SETUP = (
        "source /opt/ros/humble/setup.bash && "
        "source ~/turtlebot3_ws/install/setup.bash && "
        "export LDS_MODEL=LDS-01 && "
        "export TURTLEBOT3_MODEL=waffle && "
        "export ROS_DOMAIN_ID=38 && "
    )

    # 1. Robot bringup (runs indefinitely)
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

    # 2. Camera node on Pi (runs indefinitely)
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

    # 3. Human detection service on Pi (delayed — wait for camera to come up)
    pi_human_detection = TimerAction(
        period=T_PI_DETECTION,
        actions=[
            LogInfo(msg='[LAUNCH] Starting human detection service on Pi...'),
            ExecuteProcess(
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
            ),
        ]
    )

    # 4. Enable motor power on Pi (one-shot service call)
    pi_motor_power = TimerAction(
        period=T_PI_MOTOR_ENABLE,
        actions=[
            LogInfo(msg='[LAUNCH] Enabling motor power on Pi...'),
            ExecuteProcess(
                cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
                     PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
                     PythonExpression([
                         f"'bash -lc \\\"{SBC_SETUP}"
                         "ros2 service call /motor_power "
                         "std_srvs/srv/SetBool \\\\\\\"{data: true}\\\\\\\"\\\"'"
                     ])],
                output='screen',
                name='pi_motor_enable',
            ),
        ]
    )

    # ── Remote PC: Nav2 stack ─────────────────────────────────────────────────
    # Uses turtlebot3_navigation2 which includes:
    #   map_server, AMCL, bt_navigator, planner_server,
    #   controller_server, lifecycle_manager
    nav2_launch = TimerAction(
        period=T_NAV2,
        actions=[
            LogInfo(msg='[LAUNCH] Starting Nav2 stack...'),
            IncludeLaunchDescription(
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
            ),
        ]
    )

    # ── Remote PC: Initial pose (home waypoint) ───────────────────────────────
    # Publishes home waypoint to /initialpose so AMCL knows the robot's
    # starting location. Robot must be physically placed at the home position.
    # Coordinates match waypoints.yaml: home (x=5.3054, y=0.1956, qz=0.9980, qw=0.0625)
    initial_pose = TimerAction(
        period=T_NAV2 + 3.0,
        actions=[
            LogInfo(msg='[LAUNCH] Publishing initial pose (home waypoint)...'),
            ExecuteProcess(
                cmd=[
                    'ros2', 'topic', 'pub', '--once',
                    '/initialpose',
                    'geometry_msgs/msg/PoseWithCovarianceStamped',
                    (
                        '{"header": {"frame_id": "map"}, '
                        '"pose": {'
                        '"pose": {'
                        '"position": {"x": 5.3054, "y": 0.1956, "z": 0.0}, '
                        '"orientation": {"x": 0.0, "y": 0.0, "z": 0.9980, "w": 0.0625}'
                        '}, '
                        '"covariance": [0.25, 0.0, 0.0, 0.0, 0.0, 0.0, '
                        '0.0, 0.25, 0.0, 0.0, 0.0, 0.0, '
                        '0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '
                        '0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '
                        '0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '
                        '0.0, 0.0, 0.0, 0.0, 0.0, 0.06853]'
                        '}}'
                    ),
                ],
                output='screen',
                name='initial_pose',
            ),
        ]
    )

    # ── Remote PC: RViz ───────────────────────────────────────────────────────
    rviz = TimerAction(
        period=T_RVIZ,
        actions=[
            LogInfo(msg='[LAUNCH] Starting RViz...'),
            Node(
                package='rviz2',
                executable='rviz2',
                name='rviz2',
                arguments=['-d', rviz_config],
                output='screen',
            ),
        ]
    )

    # ── Remote PC: Patrol robot state machine ─────────────────────────────────
    patrol_robot = TimerAction(
        period=T_PATROL,
        actions=[
            LogInfo(msg='[LAUNCH] Starting patrol robot state machine...'),
            Node(
                package='patrol_robot',
                executable='patrol_robot',
                name='patrol_robot',
                output='screen',
                parameters=[{
                    'sim_data_file': PythonExpression([
                        # If sim_data is set, resolve full path inside package share
                        # Otherwise pass empty string (real mode)
                        "'' if '", sim_data, "' == '' else '",
                        os.path.join(pkg_share, 'test'), "/' + '", sim_data, "'"
                    ])
                }],
            ),
        ]
    )

    # ── Assemble ──────────────────────────────────────────────────────────────
    return LaunchDescription([
        # Arguments
        robot_ip_arg,
        robot_user_arg,
        map_file_arg,
        rviz_config_arg,
        sim_data_arg,

        # t=0: Pi bringup + camera
        LogInfo(msg='[LAUNCH] Connecting to robot Pi and starting bringup...'),
        pi_bringup,
        pi_camera,

        # t=5s: Nav2
        nav2_launch,

        # t=8s: Initial pose (home waypoint → AMCL)
        initial_pose,

        # t=10s: Pi human detection
        pi_human_detection,

        # t=12s: Motor power enable
        pi_motor_power,

        # t=18s: RViz
        rviz,

        # t=20s: Patrol state machine
        patrol_robot,
    ])
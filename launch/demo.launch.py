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
   t= 0s  turtlebot3_bringup  robot.launch.py
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
T_NAV2            =  5.0   # Nav2 stack (waits for /scan to appear)
T_PI_DETECTION    = 10.0   # SSH: human detection service on Pi
T_PI_MOTOR_ENABLE = 12.0   # SSH: enable motor power
T_RVIZ            = 18.0   # RViz (after Nav2 map loads)
T_PATROL          = 20.0   # patrol robot state machine
# ─────────────────────────────────────────────────────────────────────────────


def generate_launch_description():

    pkg_share = get_package_share_directory('patrol_robot')

    # ── Launch arguments ──────────────────────────────────────────────────────
    robot_ip_arg = DeclareLaunchArgument(
        'robot_ip', default_value='192.168.0.200',
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

    robot_ip   = LaunchConfiguration('robot_ip')
    robot_user = LaunchConfiguration('robot_user')
    map_file   = LaunchConfiguration('map_file')
    rviz_config = LaunchConfiguration('rviz_config')

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

    # 1. Robot bringup (runs indefinitely)
    pi_bringup = ExecuteProcess(
        cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
             PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
             PythonExpression([
                 "'bash -lc \\\"source /opt/ros/humble/setup.bash && "
                 "source ~/turtlebot3_ws/install/setup.bash && "
                 "export TURTLEBOT3_MODEL=waffle && "
                 "export LDS_MODEL=LDS-02 && "
                 "ros2 launch turtlebot3_bringup robot.launch.py\\\"'"
             ])],
        output='screen',
        name='pi_bringup',
    )

    # 2. Camera node on Pi (runs indefinitely)
    pi_camera = ExecuteProcess(
        cmd=['ssh', '-o', 'StrictHostKeyChecking=no',
             PythonExpression(["'", robot_user, "@", robot_ip, "'"]),
             PythonExpression([
                 "'bash -lc \\\"source /opt/ros/humble/setup.bash && "
                 "source ~/turtlebot3_ws/install/setup.bash && "
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
                         "'bash -lc \\\"source /opt/ros/humble/setup.bash && "
                         "source ~/turtlebot3_ws/install/setup.bash && "
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
                         "'bash -lc \\\"source /opt/ros/humble/setup.bash && "
                         "source ~/turtlebot3_ws/install/setup.bash && "
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

        # t=0: Pi bringup + camera
        LogInfo(msg='[LAUNCH] Connecting to robot Pi and starting bringup...'),
        pi_bringup,
        pi_camera,

        # t=5s: Nav2
        nav2_launch,

        # t=10s: Pi human detection
        pi_human_detection,

        # t=12s: Motor power enable
        pi_motor_power,

        # t=18s: RViz
        rviz,

        # t=20s: Patrol state machine
        patrol_robot,
    ])
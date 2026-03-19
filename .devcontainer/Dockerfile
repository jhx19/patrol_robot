FROM ros:humble-ros-base-jammy

# ── System deps ───────────────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    python3-pip \
    python3-colcon-common-extensions \
    python3-rosdep \
    python3-vcstool \
    git \
    curl \
    wget \
    # SSH (needed to reach the Pi from inside the container)
    openssh-client \
    # ROS2 navigation & TurtleBot3
    ros-humble-turtlebot3 \
    ros-humble-turtlebot3-bringup \
    ros-humble-turtlebot3-navigation2 \
    ros-humble-nav2-bringup \
    ros-humble-nav2-simple-commander \
    ros-humble-cartographer \
    ros-humble-cartographer-ros \
    ros-humble-navigation2 \
    # ROS2 robot drivers
    ros-humble-ros2-control \
    ros-humble-ros2-controllers \
    # ROS2 sensor / messaging
    ros-humble-cv-bridge \
    ros-humble-sensor-msgs \
    ros-humble-geometry-msgs \
    ros-humble-trajectory-msgs \
    ros-humble-std-srvs \
    ros-humble-tf2-ros \
    ros-humble-tf2-tools \
    ros-humble-rviz2 \
    # Camera
    ros-humble-v4l2-camera \
    # OpenCV (system build — avoids headless/GUI conflicts)
    python3-opencv \
    libopencv-dev \
    # Audio alert (espeak TTS)
    espeak \
    pulseaudio \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ───────────────────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
    # YOLO ONNX inference (no PyTorch needed — ARM-compatible)
    onnxruntime \
    # Glowforge web-scraper deps
    requests \
    beautifulsoup4 \
    # General utilities
    pyyaml \
    setuptools

# ── rosdep init (idempotent) ──────────────────────────────────────────────────
RUN rosdep update --rosdistro humble

# ── Environment ───────────────────────────────────────────────────────────────
ENV TURTLEBOT3_MODEL=waffle
ENV LDS_MODEL=LDS-01
ENV ROS_DOMAIN_ID=38

# Auto-source ROS2 in every shell
RUN echo "source /opt/ros/humble/setup.bash" >> /etc/bash.bashrc

# ── Workspace ─────────────────────────────────────────────────────────────────
WORKDIR /turtlebot3_ws
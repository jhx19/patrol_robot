from setuptools import setup
import os
from glob import glob

package_name = 'patrol_robot'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'maps'), glob('maps/*')),
        (os.path.join('share', package_name, 'test'), glob('test/*.json')),
    ],
    install_requires=['setuptools'],
    entry_points={
        'console_scripts': [
            'patrol_robot      = patrol_robot.main_demo:main',
            'human_detection   = patrol_robot.human_detection_service:main',
        ],
    },
)
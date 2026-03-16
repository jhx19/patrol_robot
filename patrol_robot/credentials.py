#!/usr/bin/env python3
"""Load credentials from config/credentials.yaml via package share directory."""

import yaml
import os
from ament_index_python.packages import get_package_share_directory


def load_credentials() -> dict:
    pkg_share = get_package_share_directory('patrol_robot')
    cred_file = os.path.join(pkg_share, 'config', 'credentials.yaml')

    if not os.path.exists(cred_file):
        raise FileNotFoundError(
            f'credentials.yaml not found at {cred_file}. '
            f'Copy config/credentials.yaml and fill in your values.')

    with open(cred_file, 'r') as f:
        data = yaml.safe_load(f)

    return data
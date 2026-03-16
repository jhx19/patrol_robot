#!/usr/bin/env python3
"""
Glowforge machine status monitor.

Normal mode:   GlowforgeMonitor(email, password)
Simulate mode: GlowforgeMonitor(sim_data_file='/path/to/fake.json')

Fake JSON format — list of already-filtered running machine dicts:
[
  {
    "serial": "JRM-724",
    "name": "Glowforge-2F-04",
    "username": "Young Pyung L",
    "job_title": "laser TOP",
    "time_remaining": 69.0,
    "duration": 72.7
  },
  ...
]
"""

import json
import os
import requests
from bs4 import BeautifulSoup


MONITORED_SERIALS = {
    'WYC-332',   # Glowforge-2F-01
    'CVR-883',   # Glowforge-2F-02
    'RRV-334',   # Glowforge-2F-03
    'JRM-724',   # Glowforge-2F-04
    'HVW-296',   # Glowforge-2F-05
    'HCK-847',   # Glowforge-2F-06
}


class GlowforgeMonitor:
    def __init__(self, email: str = '', password: str = '',
                 sim_data_file: str = ''):
        """
        Args:
            email:         Glowforge account email (real mode)
            password:      Glowforge account password (real mode)
            sim_data_file: Path to fake JSON file (sim mode).
                           If provided, all API calls are skipped.
        """
        self._sim_data_file = sim_data_file
        self._sim_mode = bool(sim_data_file)

        if self._sim_mode:
            if not os.path.exists(sim_data_file):
                raise FileNotFoundError(
                    f'[GlowforgeMonitor] Sim data file not found: {sim_data_file}')
            print(f'[GlowforgeMonitor] *** SIMULATION MODE *** → {sim_data_file}')
            return

        # Real mode setup
        self.email    = email
        self.password = password
        self.session  = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        self._logged_in = False

    # ------------------------------------------------------------------ #
    #  Auth (real mode only)
    # ------------------------------------------------------------------ #
    def login(self) -> bool:
        if self._sim_mode:
            print('[GlowforgeMonitor] Sim mode — skipping login.')
            return True

        try:
            resp = self.session.get('https://accounts.glowforge.com/users/sign_in')
            soup = BeautifulSoup(resp.text, 'html.parser')

            csrf = None
            tag  = soup.find('meta', {'name': 'csrf-token'})
            if tag:
                csrf = tag.get('content')
            if not csrf:
                tag = soup.find('input', {'name': 'authenticity_token'})
                if tag:
                    csrf = tag.get('value')
            if not csrf:
                return False

            resp = self.session.post(
                'https://accounts.glowforge.com/users/sign_in',
                data={
                    'authenticity_token': csrf,
                    'user[email]':        self.email,
                    'user[password]':     self.password,
                    'commit':             'Sign In',
                },
                allow_redirects=True,
            )
            self._logged_in = 'app.glowforge.com' in resp.url
            return self._logged_in
        except Exception as e:
            print(f'[GlowforgeMonitor] Login error: {e}')
            return False

    # ------------------------------------------------------------------ #
    #  Raw data (real mode only)
    # ------------------------------------------------------------------ #
    def _get_machines(self) -> list:
        try:
            resp = self.session.get(
                'https://api.glowforge.com/gfcore/users/machines',
                headers={'Origin': 'https://app.glowforge.com'},
            )
            if resp.status_code == 401:
                if self.login():
                    return self._get_machines()
                return []
            return resp.json().get('machines', []) \
                if resp.status_code == 200 else []
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    #  Public interface
    # ------------------------------------------------------------------ #
    def get_running_machines(self) -> list[dict]:
        """
        Returns list of dicts for 2F machines currently printing.
        In sim mode, returns the contents of the fake JSON directly.
        Each dict: {serial, name, username, job_title, time_remaining, duration}
        """
        if self._sim_mode:
            with open(self._sim_data_file, 'r') as f:
                data = json.load(f)
            # Apply the same time_remaining filter in sim mode
            data = [m for m in data if m.get('time_remaining', 0) > 10]
            print(f'[GlowforgeMonitor] Sim → {len(data)} machine(s) with '
                  f'time_remaining > 10s from '
                  f'{os.path.basename(self._sim_data_file)}')
            return data

        # Real mode — filter to 2F monitored machines only
        running = []
        for m in self._get_machines():
            if m.get('serial') not in MONITORED_SERIALS:
                continue
            activity = m.get('activity')
            if not activity:
                continue
            state = activity.get('state', '')
            tr    = activity.get('time_remaining', 0)
            dur   = activity.get('duration', 0)
            if state == 'printing' and tr < dur and tr > 10:
                running.append({
                    'serial':         m.get('serial', ''),
                    'name':           m.get('display_name', 'Unknown'),
                    'username':       activity.get('username', 'Unknown'),
                    'job_title':      activity.get('title', 'Untitled'),
                    'time_remaining': tr,
                    'duration':       dur,
                })
        return running
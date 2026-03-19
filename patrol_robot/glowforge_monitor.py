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
import re
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

API_HEADERS = {
    'Accept':  '*/*',
    'Origin':  'https://app.glowforge.com',
    'Referer': 'https://app.glowforge.com/',
}


class GlowforgeMonitor:
    def __init__(self, email: str = '', password: str = '',
                 sim_data_file: str = ''):
        self._sim_data_file = sim_data_file
        self._sim_mode = bool(sim_data_file)

        if self._sim_mode:
            if not os.path.exists(sim_data_file):
                raise FileNotFoundError(
                    f'[GlowforgeMonitor] Sim data file not found: {sim_data_file}')
            print(f'[GlowforgeMonitor] *** SIMULATION MODE *** → {sim_data_file}')
            return

        self.email        = email
        self.password     = password
        self.bearer_token = None
        self.session      = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self._logged_in = False

    # ------------------------------------------------------------------ #
    #  Auth (real mode only)
    # ------------------------------------------------------------------ #
    def login(self) -> bool:
        if self._sim_mode:
            print('[GlowforgeMonitor] Sim mode — skipping login.')
            return True

        print('[GlowforgeMonitor] Logging in...')
        try:
            # Step 1: GET login page and extract CSRF token
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
                print('[GlowforgeMonitor] ❌ Could not find CSRF token')
                return False

            # Step 2: POST credentials
            resp = self.session.post(
                'https://accounts.glowforge.com/users/sign_in',
                data={
                    'authenticity_token': csrf,
                    'user[email]':        self.email,
                    'user[password]':     self.password,
                    'user[remember_me]':  '0',
                    'commit':             'Sign In',
                },
                allow_redirects=True,
            )
            if 'app.glowforge.com' not in resp.url:
                print('[GlowforgeMonitor] ❌ Login failed — check email/password')
                return False

            # Step 3: Load app page to establish full session + collect cookies
            print('[GlowforgeMonitor] Getting authentication tokens...')
            app_resp = self.session.get('https://app.glowforge.com/')

            # Step 4: Probe the machines API
            test = self.session.get(
                'https://api.glowforge.com/gfcore/users/machines',
                headers=API_HEADERS,
            )
            if test.status_code == 200:
                print('[GlowforgeMonitor] ✓ Login successful!')
                self._logged_in = True
                return True
            elif test.status_code == 401:
                # Session cookies weren't enough — try extracting a bearer token
                print('[GlowforgeMonitor] Extracting bearer token from page...')
                if self._extract_token_from_page(app_resp.text):
                    self._logged_in = True
                    return True
                print('[GlowforgeMonitor] ❌ Could not authenticate after login')
                return False
            else:
                print(f'[GlowforgeMonitor] ⚠ Unexpected status after login: {test.status_code}')
                return False

        except Exception as e:
            print(f'[GlowforgeMonitor] Login error: {e}')
            return False

    def _extract_token_from_page(self, html: str) -> bool:
        """Try to pull a JWT bearer token out of the app page HTML/JS."""
        patterns = [
            r'bearer["\s:]+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)',
            r'Authorization["\s:]+Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)',
            r'token["\s:]+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                self.bearer_token = match.group(1)
                self.session.headers['Authorization'] = f'Bearer {self.bearer_token}'
                print('[GlowforgeMonitor] ✓ Bearer token extracted successfully!')
                return True
        print('[GlowforgeMonitor] ⚠ Could not extract bearer token from page')
        return False

    # ------------------------------------------------------------------ #
    #  Raw data (real mode only)
    # ------------------------------------------------------------------ #
    def _get_machines(self) -> list:
        try:
            headers = dict(API_HEADERS)
            if self.bearer_token:
                headers['Authorization'] = f'Bearer {self.bearer_token}'

            resp = self.session.get(
                'https://api.glowforge.com/gfcore/users/machines',
                headers=headers,
            )
            if resp.status_code == 401:
                print('[GlowforgeMonitor] Session expired — re-logging in...')
                if self.login():
                    return self._get_machines()
                return []
            if resp.status_code != 200:
                print(f'[GlowforgeMonitor] Unexpected status code: {resp.status_code}')
                return []

            machines = resp.json().get('machines', [])
            print(f'[GlowforgeMonitor] API returned {len(machines)} total machine(s).')
            return machines

        except Exception as e:
            print(f'[GlowforgeMonitor] _get_machines error: {e}')
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
            data = [m for m in data if m.get('time_remaining', 0) > 10]
            print(f'[GlowforgeMonitor] Sim → {len(data)} machine(s) with '
                  f'time_remaining > 10s from '
                  f'{os.path.basename(self._sim_data_file)}')
            return data

        # ── Real mode ─────────────────────────────────────────────────
        all_machines = self._get_machines()
        running = []

        for m in all_machines:
            serial        = m.get('serial', '<no serial>')
            name          = m.get('display_name', '<no name>')
            machine_state = m.get('state', '<no state>')  # top-level: active / unavailable / etc.

            # Filter 1: monitored serial
            if serial not in MONITORED_SERIALS:
                print(f'[GlowforgeMonitor] SKIP "{name}" ({serial}): '
                      f'not in MONITORED_SERIALS')
                continue

            print(f'[GlowforgeMonitor] "{name}" ({serial}): '
                  f'machine_state="{machine_state}"')

            # Filter 2: activity block present
            activity = m.get('activity')
            if not activity:
                print(f'[GlowforgeMonitor]   SKIP: no activity block '
                      f'(machine is idle/offline)')
                continue

            state  = activity.get('state', '<no state>')
            status = activity.get('status', '<no status>')
            tr     = activity.get('time_remaining', None)
            dur    = activity.get('duration', None)

            print(f'[GlowforgeMonitor]   activity.state="{state}"  '
                  f'activity.status="{status}"  '
                  f'time_remaining={tr}  duration={dur}')

            # Filter 3: waiting = job loaded but not started yet
            if state == 'waiting':
                print(f'[GlowforgeMonitor]   SKIP: waiting for user to press start')
                continue

            # Filter 4: must be actively printing
            if state != 'printing':
                print(f'[GlowforgeMonitor]   SKIP: activity state is '
                      f'"{state}", not "printing"')
                continue

            # Filter 5: time values must be present
            if tr is None or dur is None:
                print(f'[GlowforgeMonitor]   SKIP: missing time_remaining or duration')
                continue

            # Filter 6: job must be in progress (not finished)
            if tr >= dur:
                print(f'[GlowforgeMonitor]   SKIP: time_remaining ({tr}) '
                      f'>= duration ({dur})')
                continue

            # Filter 7: enough time left to bother patrolling
            if tr <= 10:
                print(f'[GlowforgeMonitor]   SKIP: only {tr:.0f}s left — '
                      f'too close to done')
                continue

            print(f'[GlowforgeMonitor]   ✅ RUNNING: {tr:.0f}s remaining '
                  f'of {dur:.0f}s')
            running.append({
                'serial':         serial,
                'name':           name,
                'username':       activity.get('username', 'Unknown'),
                'job_title':      activity.get('title', 'Untitled'),
                'time_remaining': tr,
                'duration':       dur,
            })

        if not running:
            print('[GlowforgeMonitor] No monitored machines currently running.')

        return running
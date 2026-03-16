#!/usr/bin/env python3
"""Glowforge machine status monitor — query-only interface."""

import requests
import time
from bs4 import BeautifulSoup

MONITORED_SERIALS = {
    'WYC-332', 'CVR-883', 'RRV-334',
    'JRM-724', 'HVW-296', 'HCK-847',
}

class GlowforgeMonitor:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        self._logged_in = False

    # ------------------------------------------------------------------ #
    #  Auth
    # ------------------------------------------------------------------ #
    def login(self) -> bool:
        try:
            resp = self.session.get('https://accounts.glowforge.com/users/sign_in')
            soup = BeautifulSoup(resp.text, 'html.parser')

            csrf = None
            tag = soup.find('meta', {'name': 'csrf-token'})
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
                    'user[email]': self.email,
                    'user[password]': self.password,
                    'commit': 'Sign In'
                },
                allow_redirects=True
            )
            self._logged_in = 'app.glowforge.com' in resp.url
            return self._logged_in
        except Exception as e:
            print(f'[GlowforgeMonitor] Login error: {e}')
            return False

    # ------------------------------------------------------------------ #
    #  Raw data
    # ------------------------------------------------------------------ #
    def _get_machines(self) -> list:
        try:
            resp = self.session.get(
                'https://api.glowforge.com/gfcore/users/machines',
                headers={'Origin': 'https://app.glowforge.com'}
            )
            if resp.status_code == 401:
                if self.login():
                    return self._get_machines()
                return []
            return resp.json().get('machines', []) if resp.status_code == 200 else []
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    #  Public interface used by main_demo
    # ------------------------------------------------------------------ #
    def get_running_machines(self) -> list[dict]:
        running = []
        for m in self._get_machines():
            if m.get('serial') not in MONITORED_SERIALS:
                continue                          # ← skip non-2F machines
            activity = m.get('activity')
            if not activity:
                continue
            state = activity.get('state', '')
            tr  = activity.get('time_remaining', 0)
            dur = activity.get('duration', 0)
            if state == 'printing' and tr < dur:
                running.append({
                    'serial':         m.get('serial', ''),
                    'name':           m.get('display_name', 'Unknown'),
                    'username':       activity.get('username', 'Unknown'),
                    'job_title':      activity.get('title', 'Untitled'),
                    'time_remaining': tr,
                    'duration':       dur,
                })
        return running
"""Glowforge machine status monitor — query-only interface."""

import requests
import time
from bs4 import BeautifulSoup


class GlowforgeMonitor:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        self._logged_in = False

    # ------------------------------------------------------------------ #
    #  Auth
    # ------------------------------------------------------------------ #
    def login(self) -> bool:
        try:
            resp = self.session.get('https://accounts.glowforge.com/users/sign_in')
            soup = BeautifulSoup(resp.text, 'html.parser')

            csrf = None
            tag = soup.find('meta', {'name': 'csrf-token'})
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
                    'user[email]': self.email,
                    'user[password]': self.password,
                    'commit': 'Sign In'
                },
                allow_redirects=True
            )
            self._logged_in = 'app.glowforge.com' in resp.url
            return self._logged_in
        except Exception as e:
            print(f'[GlowforgeMonitor] Login error: {e}')
            return False

    # ------------------------------------------------------------------ #
    #  Raw data
    # ------------------------------------------------------------------ #
    def _get_machines(self) -> list:
        try:
            resp = self.session.get(
                'https://api.glowforge.com/gfcore/users/machines',
                headers={'Origin': 'https://app.glowforge.com'}
            )
            if resp.status_code == 401:
                if self.login():
                    return self._get_machines()
                return []
            return resp.json().get('machines', []) if resp.status_code == 200 else []
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    #  Public interface used by main_demo
    # ------------------------------------------------------------------ #
    def get_running_machines(self) -> list[dict]:
        """
        Returns list of dicts for machines currently printing.
        Each dict: {serial, name, username, job_title, time_remaining, duration}
        """
        running = []
        for m in self._get_machines():
            activity = m.get('activity')
            if not activity:
                continue
            state = activity.get('state', '')
            tr = activity.get('time_remaining', 0)
            dur = activity.get('duration', 0)
            if state == 'printing' and tr < dur:
                running.append({
                    'serial':         m.get('serial', ''),
                    'name':           m.get('display_name', 'Unknown'),
                    'username':       activity.get('username', 'Unknown'),
                    'job_title':      activity.get('title', 'Untitled'),
                    'time_remaining': tr,
                    'duration':       dur,
                })
        return running
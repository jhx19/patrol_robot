#!/usr/bin/env python3
"""
Audio alert via Bluetooth speaker using espeak TTS.

Prerequisites (handled by Dockerfile / setup instructions):
  sudo apt-get install espeak pulseaudio pulseaudio-module-bluetooth

Bluetooth pairing (run once on the Pi before first use):
  bluetoothctl
    > power on
    > agent on
    > scan on
    > pair <MAC>
    > trust <MAC>
    > connect <MAC>
    > quit

  # Set the BT speaker as the default PulseAudio sink:
  pactl set-default-sink $(pactl list short sinks | grep bluez | awk '{print $2}')

After that, espeak uses the default sink automatically.
"""

import subprocess
import shutil


# ── Tunable TTS settings ──────────────────────────────────────────────────────
_ESPEAK_SPEED  = 130    # words per minute
_ESPEAK_AMP    = 200    # amplitude 0-200
_ESPEAK_VOICE  = 'en'   # language / voice
_TIMEOUT_SEC   = 20     # kill espeak if it hangs
# ─────────────────────────────────────────────────────────────────────────────


def _build_message(machine_info: dict) -> str:
    name = machine_info.get('name', 'unknown machine')
    user = machine_info.get('username', 'unknown user')
    mins = int(machine_info.get('time_remaining', 0)) // 60
    secs = int(machine_info.get('time_remaining', 0)) % 60
    time_str = f'{mins} minutes and {secs} seconds' if mins else f'{secs} seconds'

    return (
        f'Safety alert! '
        f'Laser engraver {name} is running unattended. '
        f'{user}, please return to the makerspace immediately. '
        f'Time remaining on your job: {time_str}. '
        f'This message will repeat.'
    )


def play_audio_alert(machine_info: dict, repeat: int = 2) -> bool:
    """
    Speak an alert message through the default audio output (Bluetooth speaker).

    Args:
        machine_info: dict with keys name, username, time_remaining.
        repeat:       number of times to speak the message (default 2).

    Returns:
        True if audio was played successfully, False otherwise.
    """
    message = _build_message(machine_info)

    # ── Try espeak (preferred — works offline, low latency on Pi) ────────────
    if shutil.which('espeak'):
        success = True
        for i in range(repeat):
            result = subprocess.run(
                ['espeak',
                 '-v', _ESPEAK_VOICE,
                 '-s', str(_ESPEAK_SPEED),
                 '-a', str(_ESPEAK_AMP),
                 message],
                timeout=_TIMEOUT_SEC,
                capture_output=True,
            )
            if result.returncode != 0:
                print(f'[AudioAlert] espeak error: {result.stderr.decode().strip()}')
                success = False
        if success:
            print('[AudioAlert] espeak playback complete.')
        return success

    # ── Fallback: festival TTS ────────────────────────────────────────────────
    if shutil.which('festival'):
        print('[AudioAlert] espeak not found — falling back to festival.')
        success = True
        for _ in range(repeat):
            result = subprocess.run(
                ['festival', '--tts'],
                input=message.encode(),
                timeout=_TIMEOUT_SEC,
                capture_output=True,
            )
            if result.returncode != 0:
                print(f'[AudioAlert] festival error: {result.stderr.decode().strip()}')
                success = False
        return success

    # ── No TTS engine available ───────────────────────────────────────────────
    print(
        '[AudioAlert] ⚠ No TTS engine found (install espeak: '
        'sudo apt-get install espeak). Audio alert skipped.'
    )
    return False
import shutil
import subprocess
import sys
import threading
import time


DEFAULT_FIX_CAPTURE_CARD = {
    'enabled': False,
    'seconds': 2,
    'interval_minutes': 3,
    'device': '/dev/video0',
}


def normalise_fix_capture_card(fix_capture_card):
    if fix_capture_card is None:
        return dict(DEFAULT_FIX_CAPTURE_CARD)

    settings = dict(DEFAULT_FIX_CAPTURE_CARD)
    settings.update(fix_capture_card)
    settings['enabled'] = bool(settings.get('enabled', False))
    settings['seconds'] = max(int(settings.get('seconds', DEFAULT_FIX_CAPTURE_CARD['seconds'])), 1)
    settings['interval_minutes'] = max(int(settings.get('interval_minutes', DEFAULT_FIX_CAPTURE_CARD['interval_minutes'])), 1)
    settings['device'] = str(settings.get('device', DEFAULT_FIX_CAPTURE_CARD['device']))
    return settings


class CaptureCardFixer:
    def __init__(self):
        self._state_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._settings = normalise_fix_capture_card(None)
        self._process = None
        self._warned_missing_ffmpeg = False

    def update(self, fix_capture_card):
        with self._state_lock:
            self._settings = normalise_fix_capture_card(fix_capture_card)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='capture-card-fixer', daemon=True)
            self._thread.start()
        self._wake_event.set()

    def close(self):
        self._stop_event.set()
        self._wake_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _read_settings(self):
        with self._state_lock:
            return dict(self._settings)

    def _run_ffmpeg(self, settings):
        ffmpeg_path = shutil.which('ffmpeg')
        if ffmpeg_path is None:
            if not self._warned_missing_ffmpeg:
                sys.stderr.write('ffmpeg is not available, capture-card fix is disabled.\n')
                self._warned_missing_ffmpeg = True
            return False

        command = [
            ffmpeg_path,
            '-nostdin',
            '-hide_banner',
            '-loglevel',
            'error',
            '-y',
            '-f',
            'video4linux2',
            '-i',
            settings['device'],
            '-t',
            str(settings['seconds']),
            '-f',
            'null',
            '-',
        ]

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._process = process
        try:
            timeout = max(settings['seconds'] + 10, settings['seconds'] * 2)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        finally:
            self._process = None
        return True

    def _run(self):
        current_settings = None
        next_run_at = None

        while not self._stop_event.is_set():
            settings = self._read_settings()

            if not settings['enabled']:
                current_settings = settings
                next_run_at = None
                self._wake_event.wait(timeout=0.5)
                self._wake_event.clear()
                continue

            if current_settings != settings:
                current_settings = settings
                next_run_at = time.monotonic()

            timeout = max((next_run_at or time.monotonic()) - time.monotonic(), 0.0)
            if self._wake_event.wait(timeout=timeout):
                self._wake_event.clear()
                continue

            if self._stop_event.is_set():
                break

            self._run_ffmpeg(current_settings)
            next_run_at = time.monotonic() + (current_settings['interval_minutes'] * 60)

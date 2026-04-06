import os
import sys
import threading
import time


if os.name == 'nt':  # pragma: no cover - Windows-specific path
    import msvcrt
else:  # pragma: no cover - POSIX-specific imports used at runtime
    import select
    import termios
    import tty


class PauseController:
    def __init__(self, label='teletext', enabled=None):
        self._label = label
        self._stop_event = threading.Event()
        self._paused_event = threading.Event()
        self._paused_event.clear()
        self._thread = None
        self._tty_handle = None
        self._tty_fd = None
        self._tty_state = None
        self._enabled = False

        if enabled is None:
            enabled = self._detect_terminal_support()

        if not enabled:
            return

        try:
            self._open_terminal()
        except OSError:
            return

        self._enabled = True
        self._thread = threading.Thread(target=self._watch_keys, name=f'{label}-pause-watcher', daemon=True)
        self._thread.start()
        self._announce('Press P to pause/resume.')

    @property
    def enabled(self):
        return self._enabled

    def _detect_terminal_support(self):
        if os.name == 'nt':
            return sys.stdin.isatty()
        return True

    def _open_terminal(self):
        if os.name == 'nt':
            return
        self._tty_handle = open('/dev/tty', 'rb', buffering=0)
        self._tty_fd = self._tty_handle.fileno()
        self._tty_state = termios.tcgetattr(self._tty_fd)
        tty.setcbreak(self._tty_fd)

    def _announce(self, message):
        try:
            sys.stderr.write(f'\n[{self._label}] {message}\n')
            sys.stderr.flush()
        except Exception:
            pass

    def _toggle_paused(self):
        if self._paused_event.is_set():
            self._paused_event.clear()
            self._announce('Resumed.')
        else:
            self._paused_event.set()
            self._announce('Paused. Press P again to continue.')

    def _read_key(self):
        if os.name == 'nt':  # pragma: no cover - Windows-specific path
            if not msvcrt.kbhit():
                return None
            return msvcrt.getwch()
        readable, _, _ = select.select([self._tty_fd], [], [], 0.2)
        if not readable:
            return None
        data = os.read(self._tty_fd, 1)
        if not data:
            return None
        try:
            return data.decode('utf-8', errors='ignore')
        except Exception:
            return None

    def _watch_keys(self):  # pragma: no cover - interactive runtime path
        while not self._stop_event.is_set():
            key = self._read_key()
            if key is None:
                continue
            if key.lower() == 'p':
                self._toggle_paused()

    def is_paused(self):
        return self._paused_event.is_set()

    def set_paused(self, paused):
        if paused:
            self._paused_event.set()
        else:
            self._paused_event.clear()

    def wait_if_paused(self):
        while self._paused_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.1)

    def wrap_iterable(self, iterable):
        for item in iterable:
            self.wait_if_paused()
            yield item

    def close(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._tty_fd is not None and self._tty_state is not None:
            try:
                termios.tcsetattr(self._tty_fd, termios.TCSADRAIN, self._tty_state)
            except Exception:
                pass
        if self._tty_handle is not None:
            try:
                self._tty_handle.close()
            except Exception:
                pass

"""Checkpoint and pause utilities shared by both data stages."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name, suffix='.tmp', delete=False) as f:
            temp = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return {} if default is None else default
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid checkpoint {p}; restore its backup before resuming. File was left untouched.') from exc


class RunControl:
    """p + Enter pauses between jobs; r resumes; q safely stops."""
    def __init__(self, pause_file='pause.flag'):
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.pause_file = Path(pause_file)

    def start_console(self):
        print('Commands: p + Enter = pause; r + Enter = resume; q + Enter = stop. Ctrl+C also stops safely.')
        def listen():
            while not self.stop.is_set():
                try:
                    command = input().strip().lower()
                except (EOFError, OSError):
                    return
                if command == 'p':
                    self.paused.set()
                    print('Pausing after the current job(s). Progress will be saved.')
                elif command == 'r':
                    self.paused.clear()
                    if self.pause_file.exists():
                        self.pause_file.unlink()
                    print('Resumed.')
                elif command == 'q':
                    self.stop.set()
                    print('Stopping after current job(s).')
        threading.Thread(target=listen, daemon=True).start()

    def wait(self):
        announced = False
        while self.paused.is_set() or self.pause_file.exists():
            if self.stop.is_set():
                return False
            if not announced:
                print('PAUSED. Type r + Enter, or remove pause.flag, to continue.')
                announced = True
            self.stop.wait(0.25)
        return not self.stop.is_set()


class RunLock:
    """Prevent two processes from overwriting the same checkpoint."""
    def __init__(self, output):
        self.path = Path(str(output) + '.lock')
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f'{self.path} exists. Another scraper may be running. If the previous process crashed, remove this lock file before restarting.')
        os.write(self.fd, str(os.getpid()).encode())
        return self
    def __exit__(self, *args):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)

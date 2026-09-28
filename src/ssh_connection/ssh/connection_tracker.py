"""
Tracks which SSH hosts currently have an open terminal window.

A connection is considered active while the PowerShell process that hosts
the ssh session is still alive. PIDs are validated against their creation
time to guard against PID reuse.
"""

import threading
from typing import Dict, List, Optional, Set, Tuple

import psutil


class ConnectionTracker:
    """Registry of live SSH terminal processes, keyed by host name."""

    def __init__(self):
        self._lock = threading.Lock()
        # host -> list of (pid, create_time)
        self._connections: Dict[str, List[Tuple[int, float]]] = {}

    def register(self, host: str, pid: int) -> None:
        try:
            create_time = psutil.Process(pid).create_time()
        except psutil.Error:
            return
        with self._lock:
            self._connections.setdefault(host, []).append((pid, create_time))

    def unregister(self, host: str, pid: int) -> None:
        """Forget `pid` for `host` (e.g. a login that failed and was killed)."""
        with self._lock:
            entries = self._connections.get(host, [])
            self._connections[host] = [e for e in entries if e[0] != pid]

    def session_alive(self, host: str) -> bool:
        """Stricter than is_active(): the ssh process itself must still run.
        The terminal is `powershell -NoExit`, so it outlives a failed/closed
        ssh session (wrong token, VPN down) and is_active() stays True."""
        return any(ssh_child_alive(pid) for pid in self._live_pids(host))

    def is_active(self, host: str) -> bool:
        return bool(self._live_pids(host))

    def get_pid(self, host: str) -> Optional[int]:
        pids = self._live_pids(host)
        return pids[0] if pids else None

    def entries(self) -> List[Tuple[str, int]]:
        """(host, pid) of every live registered console."""
        with self._lock:
            hosts = list(self._connections.keys())
        return [(h, pid) for h in hosts for pid in self._live_pids(h)]

    def active_hosts(self) -> Set[str]:
        with self._lock:
            hosts = list(self._connections.keys())
        return {h for h in hosts if self.is_active(h)}

    def _live_pids(self, host: str) -> List[int]:
        with self._lock:
            entries = list(self._connections.get(host, []))
        alive = []
        dead = []
        for pid, create_time in entries:
            if self._alive(pid, create_time):
                alive.append(pid)
            else:
                dead.append((pid, create_time))
        if dead:
            with self._lock:
                current = self._connections.get(host, [])
                self._connections[host] = [e for e in current if e not in dead]
        return alive

    @staticmethod
    def _alive(pid: int, create_time: float) -> bool:
        try:
            proc = psutil.Process(pid)
            # Same PID but different creation time means the PID was reused
            return proc.is_running() and abs(proc.create_time() - create_time) < 1.0
        except psutil.Error:
            return False


# Process names counted as "the ssh session" below a console. A set so tests
# can point it at a harmless system binary run IN PLACE (never copy/rename a
# system executable to ssh.exe: antivirus flags that as masquerading).
SSH_PROCESS_NAMES = {"ssh.exe"}


def is_ssh_process(proc) -> bool:
    try:
        return proc.name().lower() in SSH_PROCESS_NAMES
    except psutil.Error:
        return False


def ssh_child_alive(pid: int) -> bool:
    """Whether the console process `pid` still has a running ssh.exe below it."""
    try:
        return any(is_ssh_process(c) and c.is_running()
                   for c in psutil.Process(pid).children(recursive=True))
    except psutil.Error:
        return False


# PIDs we are about to kill on purpose: the session monitor must not report
# their exit as "connessione chiusa inaspettatamente".
_expected_exits: Set[int] = set()
_expected_lock = threading.Lock()


def expect_exit(pid: int) -> None:
    with _expected_lock:
        _expected_exits.add(pid)


def exit_was_expected(pid: int) -> bool:
    """True (once) if `pid` was killed on purpose by us."""
    with _expected_lock:
        if pid in _expected_exits:
            _expected_exits.discard(pid)
            return True
        return False


def kill_console(pid: int) -> None:
    """Kill a terminal process together with its ssh child: killing only the
    PowerShell would leave ssh.exe (and its hidden console) behind."""
    try:
        proc = psutil.Process(pid)
        expect_exit(pid)
        for child in proc.children(recursive=True):
            try:
                expect_exit(child.pid)
                child.kill()
            except psutil.Error:
                pass
        proc.kill()
    except psutil.Error:
        pass


# Shared instance used across launcher and tray menu
tracker = ConnectionTracker()

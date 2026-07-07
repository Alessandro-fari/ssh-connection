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

    def is_active(self, host: str) -> bool:
        return bool(self._live_pids(host))

    def get_pid(self, host: str) -> Optional[int]:
        pids = self._live_pids(host)
        return pids[0] if pids else None

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


# Shared instance used across launcher and tray menu
tracker = ConnectionTracker()

"""
Background watchdog of the open SSH sessions, raising tray notifications for:

- connection_lost — an ssh process died on its own. Every terminal is
  `powershell -NoExit`, so the console outlives ssh: the monitor keeps a
  Win32 handle on each ssh.exe to read its exit code afterwards.
    * visible console: notify only on exit code 255 (ssh's own error code:
      network drop, ServerAlive timeout, connection reset). 0 / other codes
      are a normal logout; a vanished console means the user closed it.
    * hidden Init console: any exit is unexpected (the user cannot close
      it). The orphan console is killed, so the next Init starts clean.
    * exits we caused (kill_console / discard / shutdown) are skipped via
      connection_tracker.exit_was_expected().
- tunnel_lost — a LocalForward local port of a live session is not
  listening (any ssh.exe). Ports come from `ssh -G <host>`, which resolves
  wildcard Host blocks exactly like ssh does. Checked from the TCP table
  (psutil.net_connections), so no traffic ever reaches the remote DB/sshd.
  Reported when a port that was listening stops, or when a new session has
  not bound it within TUNNEL_GRACE seconds (typically "port already in use").
  A port is reported once until it recovers.

keepalive_failed is event-driven and lives in SshLauncher (it knows when
the keepalive command was sent).
"""

import ctypes
import ctypes.wintypes as wt
import logging
import subprocess
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import psutil

from .. import notifications
from .connection_tracker import exit_was_expected, is_ssh_process, kill_console, tracker
from .ssh_config_parser import SshConfigParser

kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = wt.HANDLE

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0

SSH_ERROR_EXIT = 255


class _Session:
    """One ssh.exe running inside a tracked console."""

    def __init__(self, host: str, hidden: bool, console_pid: int, ssh_pid: int):
        self.host = host
        self.hidden = hidden
        self.console_pid = console_pid
        self.ssh_pid = ssh_pid
        self.started = time.monotonic()
        # Holding the handle keeps the exit code readable after ssh exits.
        self.handle = kernel32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, ssh_pid)

    def exited(self) -> bool:
        if not self.handle:
            return not psutil.pid_exists(self.ssh_pid)
        return kernel32.WaitForSingleObject(self.handle, 0) == _WAIT_OBJECT_0

    def exit_code(self) -> Optional[int]:
        if not self.handle:
            return None
        code = wt.DWORD(0)
        if kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return code.value
        return None

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


class SessionMonitor:
    TICK_SECONDS = 3.0
    TUNNEL_CHECK_SECONDS = 10.0
    TUNNEL_GRACE = 30.0

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sessions: Dict[int, _Session] = {}      # console pid -> session
        self._tunnels: Dict[int, dict] = {}            # local port -> state
        self._last_tunnel_check = 0.0
        self._forward_cache: Dict[str, Tuple[Optional[float], List[int]]] = {}

    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="SessionMonitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for s in self._sessions.values():
            s.close()
        self._sessions.clear()

    def _run(self) -> None:
        while not self._stop.wait(self.TICK_SECONDS):
            try:
                self.tick()
            except Exception as e:
                logging.error(f"Session monitor tick failed: {e}", exc_info=True)

    # ------------------------------------------------------------------

    @staticmethod
    def _entries() -> Dict[int, Tuple[str, bool]]:
        """console pid -> (host, hidden) for every console we launched."""
        out = {pid: (host, False) for host, pid in tracker.entries()}
        try:
            from .init_orchestrator import InitOrchestrator
            for host, pid in InitOrchestrator.hidden_entries():
                out[pid] = (host, True)
        except Exception as e:
            logging.debug(f"Init entries unavailable: {e}")
        return out

    @staticmethod
    def _ssh_child(console_pid: int) -> Optional[int]:
        try:
            for c in psutil.Process(console_pid).children(recursive=True):
                if is_ssh_process(c):
                    return c.pid
        except psutil.Error:
            pass
        return None

    def tick(self) -> None:
        entries = self._entries()

        # Consoles dropped from the registries (discarded, unregistered).
        for cpid in [c for c in self._sessions if c not in entries]:
            self._sessions.pop(cpid).close()

        exited: List[_Session] = []
        for cpid, (host, hidden) in entries.items():
            sess = self._sessions.get(cpid)
            if sess is None:
                ssh_pid = self._ssh_child(cpid)
                if ssh_pid:
                    self._sessions[cpid] = _Session(host, hidden, cpid, ssh_pid)
                continue
            sess.hidden = hidden
            if sess.exited():
                exited.append(self._sessions.pop(cpid))

        if exited:
            # When the user closes a window, ssh dies a moment before the
            # console: give the console time to go too before judging.
            time.sleep(1.0)
            for sess in exited:
                try:
                    self._on_ssh_exit(sess)
                finally:
                    sess.close()

        self._check_tunnels()

    # ------------------------------------------------------------------
    # connection_lost

    @staticmethod
    def _env_of(host: str) -> Optional[str]:
        for env, hosts in SshConfigParser.parse_ssh_config().items():
            if host in hosts:
                return env
        try:
            from .init_orchestrator import INIT_ENVS
            for env, cfg in INIT_ENVS.items():
                if host in cfg["targets"]:
                    return env
        except Exception:
            pass
        return None

    def _on_ssh_exit(self, sess: _Session) -> None:
        code = sess.exit_code()
        expected = exit_was_expected(sess.ssh_pid) | exit_was_expected(sess.console_pid)
        console_alive = psutil.pid_exists(sess.console_pid)
        logging.info(f"ssh of {sess.host} exited (code={code}, hidden={sess.hidden}, "
                     f"expected={expected}, console_alive={console_alive})")
        if expected:
            return
        try:
            from .init_orchestrator import InitOrchestrator
            if sess.host in InitOrchestrator.busy_hosts():
                return  # an Init in progress reports its own failures
        except Exception:
            pass
        if sess.hidden:
            # Nobody can see or close it: clean up, so a new Init relaunches.
            kill_console(sess.console_pid)
            env = self._env_of(sess.host)
            hint = f" Rilancia Init {env}." if env else ""
            notifications.notify(
                "connection_lost", "Connessione chiusa inaspettatamente",
                f"{sess.host} (Init, in background) si è disconnesso.{hint}")
            return
        if not console_alive:
            return  # window closed by the user
        if code != SSH_ERROR_EXIT:
            return  # normal logout
        notifications.notify(
            "connection_lost", "Connessione chiusa inaspettatamente",
            f"{sess.host}: la sessione SSH è caduta (rete, VPN o timeout del server).")

    # ------------------------------------------------------------------
    # tunnel_lost

    def _forward_ports(self, host: str) -> List[int]:
        """Local ports of the LocalForwards `ssh -G host` resolves for `host`."""
        try:
            mtime = SshConfigParser.get_config_path().stat().st_mtime
        except OSError:
            mtime = None
        cached = self._forward_cache.get(host)
        if cached and cached[0] == mtime:
            return cached[1]
        ports: List[int] = []
        try:
            from .ssh_launcher import SshLauncher
            out = subprocess.run(
                [SshLauncher._ssh_executable(), "-G", host],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            ports = sorted({p for p in (self.parse_forward_port(l) for l in out.splitlines())
                            if p is not None})
        except Exception as e:
            logging.debug(f"ssh -G {host} failed: {e}")
        self._forward_cache[host] = (mtime, ports)
        return ports

    @staticmethod
    def parse_forward_port(line: str) -> Optional[int]:
        """'localforward [127.0.0.1]:1524 [db]:1524' -> 1524 (None otherwise)."""
        parts = line.split()
        if len(parts) < 3 or parts[0].lower() != "localforward":
            return None
        listen = parts[1].rsplit(":", 1)[-1].strip("[]")
        return int(listen) if listen.isdigit() else None

    @staticmethod
    def _listening_ssh_ports() -> Optional[Set[int]]:
        """Local TCP ports in LISTEN held by any ssh.exe (None if unreadable)."""
        try:
            conns = psutil.net_connections(kind="tcp")
        except (psutil.Error, OSError) as e:
            logging.debug(f"TCP table unavailable: {e}")
            return None
        ports: Set[int] = set()
        names: Dict[int, bool] = {}
        for c in conns:
            if c.status != psutil.CONN_LISTEN or not c.pid:
                continue
            if c.pid not in names:
                try:
                    names[c.pid] = is_ssh_process(psutil.Process(c.pid))
                except psutil.Error:
                    names[c.pid] = False
            if names[c.pid]:
                ports.add(c.laddr.port)
        return ports

    def _check_tunnels(self) -> None:
        now = time.monotonic()
        if now - self._last_tunnel_check < self.TUNNEL_CHECK_SECONDS:
            return
        self._last_tunnel_check = now

        wanted: Dict[int, _Session] = {}
        for sess in self._sessions.values():
            for port in self._forward_ports(sess.host):
                wanted.setdefault(port, sess)
        for port in [p for p in self._tunnels if p not in wanted]:
            del self._tunnels[port]
        if not wanted:
            return
        listening = self._listening_ssh_ports()
        if listening is None:
            return

        for port, sess in wanted.items():
            st = self._tunnels.setdefault(port, {"ok": False, "alerted": False})
            if port in listening:
                st["ok"], st["alerted"] = True, False
                continue
            if st["alerted"]:
                continue
            if st["ok"]:
                msg = f"La porta locale {port} ({sess.host}) non è più in ascolto."
            elif now - sess.started > self.TUNNEL_GRACE:
                msg = (f"La porta locale {port} ({sess.host}) non è attiva: "
                       f"probabilmente già occupata da un altro programma.")
            else:
                continue  # new session, still binding its forwards
            st["ok"], st["alerted"] = False, True
            notifications.notify("tunnel_lost", "Tunnel perso", msg)

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence

from ..config.config_loader import ConfigLoader
from .connection_tracker import tracker
from .console_injector import ConsoleInjector
from .ssh_config_parser import SshConfigParser


class SshLauncher:
    """SSH connection launcher with automated, terminal-targeted credential input"""

    # Max seconds to wait for the jump host's LocalForward tunnel port to
    # accept connections. Generous because the login may involve a manual
    # 2FA token entry by the user.
    TUNNEL_WAIT_SECONDS = 120.0

    # Typed into the jump host terminal once its shell prompt appears, so
    # the session (and its tunnels) stays alive.
    KEEPALIVE_COMMAND = "watch -n 240 date"

    @staticmethod
    def connect(name: str) -> None:
        """
        Connect to an SSH host by name (as defined in ~/.ssh/config).

        Runs in a background thread so the tray menu stays responsive.
        If the host's jump server (login_test / login_prod) is not connected
        yet, it is launched first and the target connection follows once its
        tunnels are up.
        """
        thread = threading.Thread(target=SshLauncher._connect_sync, args=(name,), daemon=True)
        thread.start()

    @staticmethod
    def _connect_sync(name: str) -> None:
        try:
            jump = SshLauncher._required_jump_host(name)
            if jump and not tracker.is_active(jump):
                endpoint = SshLauncher._host_endpoint(name)
                if endpoint and SshLauncher._port_open(*endpoint):
                    # Tunnel already up: the jump host was opened outside
                    # this app instance — no need to launch it again.
                    logging.info(f"Tunnel for {name} already up, skipping {jump} launch")
                else:
                    logging.info(f"Jump host {jump} not connected — launching it before {name}")
                    SshLauncher._launch(jump)
            if jump:
                # The target connects through a LocalForward tunnel of the
                # jump host: wait until that port actually accepts
                # connections (the login may include a manual 2FA token).
                SshLauncher._wait_for_tunnel(name)
            SshLauncher._launch(name)
        except Exception as e:
            logging.error(f"Error launching SSH connection to {name}: {e}", exc_info=True)

    @staticmethod
    def _required_jump_host(name: str) -> Optional[str]:
        """
        Return the jump host (login_*) for the environment `name` belongs to,
        or None if `name` is itself a jump host or no jump host is defined.
        """
        host_map = SshConfigParser.parse_ssh_config()
        for hosts in host_map.values():
            if name in hosts:
                for host in hosts:
                    if host.lower().startswith("login") and host != name:
                        return host
                return None
        return None

    @staticmethod
    def _host_endpoint(name: str):
        """
        Return (hostname, port) for `name` from ~/.ssh/config, or None if
        the host has no explicit HostName/Port (e.g. jump hosts).
        """
        hostname = None
        port = None
        in_block = False
        try:
            with open(SshConfigParser.get_config_path(), 'r', encoding='utf-8') as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue
                    key, _, value = line.partition(' ')
                    key = key.lower()
                    if key == 'host':
                        in_block = name in value.split()
                    elif in_block:
                        if key == 'hostname':
                            hostname = value.strip()
                        elif key == 'port':
                            port = int(value.strip())
        except (OSError, ValueError):
            return None
        if hostname and port:
            return hostname, port
        return None

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        import socket
        try:
            with socket.create_connection((host, port), timeout=0.7):
                return True
        except OSError:
            return False

    @classmethod
    def _wait_for_tunnel(cls, name: str) -> None:
        """
        If `name` connects via a local tunnel port, poll that port until it
        accepts connections (i.e. the jump host login has completed and its
        LocalForwards are bound) or the timeout expires.
        """
        import socket

        endpoint = cls._host_endpoint(name)
        if endpoint is None:
            time.sleep(2.0)
            return

        host, port = endpoint
        logging.info(f"Waiting for tunnel {host}:{port} for {name}...")
        deadline = time.monotonic() + cls.TUNNEL_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1.5):
                    logging.info(f"Tunnel {host}:{port} is up")
                    return
            except OSError:
                time.sleep(1.0)
        logging.warning(f"Tunnel {host}:{port} not available after {cls.TUNNEL_WAIT_SECONDS}s, trying anyway")

    @staticmethod
    def _ssh_executable() -> str:
        """
        Prefer Windows OpenSSH: it resolves ~/.ssh/config via %USERPROFILE%.
        Git's ssh (which may shadow it on PATH) uses HOME instead, which on
        this setup points elsewhere and would miss the config entirely.
        """
        system_ssh = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'OpenSSH' / 'ssh.exe'
        if system_ssh.exists():
            return str(system_ssh)
        return shutil.which('ssh') or 'ssh'

    @staticmethod
    def _launch(name: str, *, hidden: bool = False,
                secrets: Optional[Sequence[str]] = None,
                register: bool = True,
                keepalive: Optional[bool] = None,
                inject_timeout: float = 30.0) -> Optional[int]:
        """
        Open a new terminal running ssh to `name`, optionally register it in
        the connection tracker and inject credentials into that terminal only.

        - hidden: create the console with its window hidden (SW_HIDE) so it
          stays out of the taskbar/Alt-Tab while remaining a real console
          (injection via AttachConsole keeps working).
        - secrets: the sequence of secrets to answer prompts with; defaults
          to [password]. Pass e.g. [password, token] for a login needing 2FA.
        - register: whether to record the connection in the tracker (skip it
          for hidden helper connections that must not show up in the menu).
        - keepalive: force sending the keepalive command; defaults to the
          historical rule (only for login* hosts).

        Returns the console-owning PID on success (credentials injected or
        none needed), or None on failure.
        """
        config = ConfigLoader.load()
        username = config.get_username()
        password = config.get_password()

        target = f"{username}@{name}" if username else name
        logging.info(f"Launching SSH terminal for {target}{' (hidden)' if hidden else ''}")

        # Spawn PowerShell directly (no `cmd /c start`) so we own the PID of
        # the process that owns the new console — required both for targeted
        # password injection and for tracking the connection status.
        startupinfo = None
        if hidden:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        process = subprocess.Popen(
            ['powershell', '-NoExit', '-Command', f"& '{SshLauncher._ssh_executable()}' {target}"],
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            startupinfo=startupinfo,
            close_fds=True,
        )
        if register:
            tracker.register(name, process.pid)

        if hidden:
            # Belt-and-braces: Windows Terminal (when default) ignores
            # wShowWindow, so hide the console window explicitly too.
            ConsoleInjector.hide_console_window(process.pid)

        if secrets is None:
            secrets = [password] if password else []

        if not secrets:
            logging.info("No credentials to inject - skipping credential input")
        else:
            # Blocks until the prompt(s) appear in THAT console and the
            # secrets are written into its input buffer (focus-independent).
            if not ConsoleInjector.inject_secrets(process.pid, secrets, timeout=inject_timeout):
                return None

        want_keepalive = keepalive if keepalive is not None else name.lower().startswith("login")
        if want_keepalive:
            # Once the login completes (after any manual/injected 2FA token),
            # run the keepalive so the session and its tunnels don't expire.
            # Runs in background: the shell prompt may take a while to appear.
            threading.Thread(
                target=ConsoleInjector.run_command_at_shell_prompt,
                args=(process.pid, SshLauncher.KEEPALIVE_COMMAND),
                daemon=True,
            ).start()

        return process.pid

    @staticmethod
    def launch_for_init(name: str, secrets: Sequence[str], register: bool = True) -> Optional[int]:
        """
        Launch `name` for the Init flow: hidden console, given secrets
        injected, keepalive forced. Returns the PID on success, None on
        failure. Login hosts pass [password, token]; DB targets pass
        [password] and register=False so they stay invisible in the menu.
        """
        return SshLauncher._launch(
            name, hidden=True, secrets=list(secrets),
            register=register, keepalive=True,
            inject_timeout=60.0 if len(secrets) > 1 else 30.0,
        )

    @staticmethod
    def get_current_password() -> Optional[str]:
        """Get the current password from configuration"""
        config = ConfigLoader.load()
        return config.get_password()


if __name__ == "__main__":
    print("Testing SSH launcher...")
    config = ConfigLoader.load()
    host_map = SshConfigParser.parse_ssh_config()
    print(f"Hosts: {host_map}")
    # Uncomment to test an actual connection:
    # SshLauncher.connect("login_test")

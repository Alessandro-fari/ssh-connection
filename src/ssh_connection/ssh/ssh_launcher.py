import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

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
    def _launch(name: str) -> bool:
        """
        Open a new terminal running ssh to `name`, register it in the
        connection tracker and inject the password into that terminal only.

        Returns True if the password was injected successfully (or no
        password was needed), False otherwise.
        """
        config = ConfigLoader.load()
        username = config.get_username()
        password = config.get_password()

        target = f"{username}@{name}" if username else name
        logging.info(f"Launching SSH terminal for {target}")

        # Spawn PowerShell directly (no `cmd /c start`) so we own the PID of
        # the process that owns the new console — required both for targeted
        # password injection and for tracking the connection status.
        process = subprocess.Popen(
            ['powershell', '-NoExit', '-Command', f"& '{SshLauncher._ssh_executable()}' {target}"],
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        tracker.register(name, process.pid)

        if not password:
            logging.info("No password available - skipping credential input")
            return True

        # Blocks until the password prompt appears in THAT console and the
        # password is written into its input buffer (focus-independent).
        injected = ConsoleInjector.inject_password(process.pid, password)

        if injected and name.lower().startswith("login"):
            # Once the jump host login completes (after the manual 2FA
            # token), run the keepalive so the session and its tunnels
            # don't expire. Runs in background: the shell prompt may take
            # a while to appear while the user types the token.
            threading.Thread(
                target=ConsoleInjector.run_command_at_shell_prompt,
                args=(process.pid, SshLauncher.KEEPALIVE_COMMAND),
                daemon=True,
            ).start()

        return injected

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

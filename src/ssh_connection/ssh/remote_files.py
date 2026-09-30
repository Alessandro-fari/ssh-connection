"""
Remote folder browsing, file search and download on a configured host
(backs the "Cerca file" window).

Why paramiko and not ssh.exe: the terminals opened by the launcher are
interactive (the password is typed into their console), while here the app
needs the *output* of a command (`find`), folder listings and file
transfers. Windows
OpenSSH has no ControlMaster to piggyback on the open sessions, and
SSH_ASKPASS would mean handing the password to a helper process. paramiko
gives one authenticated connection per window, reused for every listing,
search and download (SFTP).

Browsing keeps the *logical* path the user typed or clicked through
(/app/nets/batchcommon/files/clear), like WinSCP, instead of the physical one
the symbolic links resolve to; links to folders are listed as folders.

Routing is the same as the terminals: `SshLauncher.ensure_route()` opens the
jump host if needed and waits for the LocalForward tunnel, then the
endpoint comes from `ssh -G host` (HostName/Port as OpenSSH resolves them,
e.g. localhost:2222). Credentials come from ConfigLoader, as for the
terminals.

Host keys: ~/.ssh/known_hosts is loaded read-only (never rewritten). A known
host with a different key is refused (BadHostKeyException); an unknown one
is accepted and logged — the same outcome as the launcher, which answers
"yes" to the host-key prompt.

Jump hosts (login_*) are not supported: their login needs a 2FA token.

All methods are blocking and meant for worker threads; a lock serializes
the use of the connection.
"""

import logging
import posixpath
import shlex
import subprocess
import stat as stat_mod
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..config.config_loader import ConfigLoader
from .ssh_launcher import SshLauncher

MAX_RESULTS = 2000
SEARCH_TIMEOUT = 180.0

_NODIR = "__SSHCM_NODIR__"


class RemoteError(Exception):
    """Error meant to be shown to the user as is (Italian message)."""


@dataclass
class RemoteFile:
    path: str
    size: int
    mtime: float
    is_dir: bool = False
    is_link: bool = False

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def folder(self) -> str:
        return posixpath.dirname(self.path)


def is_supported_host(name: str) -> bool:
    return not name.lower().startswith("login")


def route_ready(name: str) -> bool:
    """True when `name` is reachable right now without opening anything:
    its tunnel port already accepts connections (or it needs none). Used to
    list a folder as soon as a host is picked without popping up a jump host
    login just because the user browsed the host list."""
    endpoint = SshLauncher._host_endpoint(name)
    return endpoint is None or SshLauncher._port_open(*endpoint)


def join_path(folder: str, name: str) -> str:
    """Logical child path (no symlink resolution); '..' goes up one level."""
    if name == "..":
        return parent_path(folder)
    return posixpath.join(folder, name) if folder != "/" else "/" + name


def parent_path(folder: str) -> str:
    folder = folder.rstrip("/") or "/"
    return posixpath.dirname(folder) or "/"


def ssh_endpoint(name: str) -> Tuple[str, int, Optional[str], Optional[str]]:
    """(hostname, port, user, proxyjump) of `name` as `ssh -G` resolves it."""
    out = subprocess.run(
        [SshLauncher._ssh_executable(), "-G", name],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    values = {}
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        values.setdefault(key.lower(), value.strip())
    hostname = values.get("hostname") or name
    try:
        port = int(values.get("port") or 22)
    except ValueError:
        port = 22
    jump = values.get("proxyjump")
    return hostname, port, values.get("user"), (None if jump in (None, "", "none") else jump)


def glob_for(name_filter: str) -> str:
    """What the user typed -> `find -iname` pattern: plain text means
    'contains', wildcards are used as typed, empty means everything."""
    text = name_filter.strip()
    if not text:
        return "*"
    if any(c in text for c in "*?["):
        return text
    return f"*{text}*"


def shell_path(path: str) -> str:
    """Quote a remote path for sh, keeping a leading ~ expandable."""
    path = path.strip() or "~"
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def find_command(folder: str, name_filter: str, text: str = "",
                 recursive: bool = True, limit: int = MAX_RESULTS) -> str:
    """POSIX sh script listing matching files as 'mtime<TAB>size<TAB>path'.

    GNU find's -printf gives size and mtime in one pass; without it (BusyBox,
    AIX, ...) paths are listed with size/date 0. Permission errors are
    discarded: one unreadable subfolder must not spoil the result.

    `find -L` follows symbolic links: on the servers the application folders
    are chains of links (/app/nets/batchcommon -> BATCHCOMMON_3.2.0.3,
    .../files -> /files/nets/..., .../clear -> ...) and plain `find` would not
    even enter the starting folder. -L also makes -type f / size / mtime
    refer to the link target; GNU find detects link loops and skips them."""
    tests = [] if recursive else ["-maxdepth", "1"]
    tests += ["-type", "f", "-iname", shlex.quote(glob_for(name_filter))]
    if text.strip():
        # -exec as a test: keeps only the files containing the text
        tests += ["-exec", "grep", "-qiF", "-e", shlex.quote(text.strip()), "{}", "\\;"]
    t = " ".join(tests)
    return (
        f"P={shell_path(folder)}; "
        f"[ -d \"$P\" ] || {{ echo {_NODIR}; exit 0; }}; "
        f"if find -L \"$P\" -maxdepth 0 -printf '' >/dev/null 2>&1; then "
        f"find -L \"$P\" {t} -printf '%T@\\t%s\\t%p\\n' 2>/dev/null; "
        f"else find -L \"$P\" {t} -print 2>/dev/null | sed 's/^/0\\t0\\t/'; fi "
        f"| head -n {int(limit) + 1}"
    )


def parse_find_output(out: str) -> List[RemoteFile]:
    files = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            files.append(RemoteFile(parts[2], int(float(parts[1])), float(parts[0])))
        except ValueError:
            continue
    return files


class _AcceptUnknown:
    """Host key policy: accept unknown hosts (in memory only) and log it."""

    def missing_host_key(self, client, hostname, key):
        logging.warning(f"Host key of {hostname} not in known_hosts, accepted "
                        f"({key.get_name()}); known_hosts is left unchanged")


class RemoteSession:
    """One authenticated SSH connection to `host` (lazy, reused)."""

    def __init__(self, host: str):
        self.host = host
        self._client = None
        self._sftp = None
        self._closed = False   # dropped by the window: never (re)connect
        self._lock = threading.RLock()
        self._channel = None   # running command, closable by cancel()

    # ------------------------------------------------------------------
    # Connection

    def connect(self, status: Callable[[str], None] = lambda s: None) -> None:
        with self._lock:
            if self._client is not None and self._alive():
                return
            if self._closed:
                raise RemoteError("Operazione annullata.")
            self._close_client()      # drop a dead connection, if any
            if not is_supported_host(self.host):
                raise RemoteError(f"{self.host} è un jump host: il suo login richiede "
                                  f"il token, la ricerca file non è disponibile.")
            try:
                import paramiko
            except ImportError:
                raise RemoteError("Modulo 'paramiko' mancante: installarlo con "
                                  "'py -m pip install paramiko'.")

            status(f"Verifico il tunnel per {self.host}...")
            SshLauncher.ensure_route(self.host)
            hostname, port, cfg_user, jump = ssh_endpoint(self.host)
            if jump:
                raise RemoteError(f"{self.host} usa ProxyJump ({jump}): non supportato "
                                  f"dalla ricerca file.")
            config = ConfigLoader.load()
            user = config.get_username() or cfg_user
            password = config.get_password()
            if not user or not password:
                raise RemoteError("Utente o password non configurati "
                                  "(Impostazioni → Info → Apri utente e password).")

            status(f"Connessione a {self.host} ({hostname}:{port})...")
            client = paramiko.SSHClient()
            known = Path.home() / ".ssh" / "known_hosts"
            if known.exists():
                try:
                    # "system" keys: read-only, paramiko never saves them back
                    client.load_system_host_keys(str(known))
                except Exception as e:
                    logging.warning(f"Could not read {known}: {e}")
            client.set_missing_host_key_policy(_AcceptUnknown())
            try:
                client.connect(hostname, port=port, username=user, password=password,
                               look_for_keys=False, allow_agent=False,
                               timeout=15, banner_timeout=30, auth_timeout=30)
            except paramiko.BadHostKeyException:
                client.close()
                raise RemoteError(f"La chiave host di {self.host} non corrisponde a "
                                  f"known_hosts: connessione rifiutata.")
            except paramiko.AuthenticationException:
                client.close()
                raise RemoteError(f"Autenticazione rifiutata da {self.host}: "
                                  f"controlla utente e password.")
            except (OSError, paramiko.SSHException) as e:
                client.close()
                raise RemoteError(f"Connessione a {self.host} non riuscita: {e}. "
                                  f"Il tunnel del jump host è attivo?")
            if self._closed:
                # The window dropped this session while we were connecting
                # (host changed / window closed): don't leak the connection.
                client.close()
                raise RemoteError("Operazione annullata.")
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(30)
            self._client = client
            logging.info(f"File search: connected to {self.host} as {user}")

    def _alive(self) -> bool:
        t = self._client.get_transport() if self._client else None
        return bool(t and t.is_active())

    def close(self) -> None:
        """Final close (window closed / host changed): a connect() still
        running on a worker thread will not keep its connection."""
        self._closed = True
        self._close_client()

    def _close_client(self) -> None:
        with self._lock:
            for obj in (self._sftp, self._client):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:
                    pass
            self._sftp = None
            self._client = None

    def cancel(self) -> None:
        """Abort the running command (callable from any thread)."""
        ch = self._channel
        if ch is not None:
            try:
                ch.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Commands

    def _run(self, command: str, timeout: float) -> str:
        """Run `command` with sh, return stdout. Raises RemoteError."""
        transport = self._client.get_transport()
        ch = transport.open_session()
        self._channel = ch
        try:
            ch.exec_command("sh -c " + shlex.quote(command))
            ch.shutdown_write()
            chunks = []
            deadline = time.monotonic() + timeout
            while True:
                if ch.recv_ready():
                    chunks.append(ch.recv(65536))
                    continue
                if ch.closed or (ch.exit_status_ready() and ch.eof_received
                                 and not ch.recv_ready()):
                    break
                if time.monotonic() > deadline:
                    raise RemoteError("Tempo scaduto: restringi la cartella o il filtro.")
                time.sleep(0.05)
            if ch.closed and not ch.exit_status_ready():
                raise RemoteError("Operazione interrotta.")
            return b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            self._channel = None
            try:
                ch.close()
            except Exception:
                pass

    def find(self, folder: str, name_filter: str, text: str = "",
             recursive: bool = True) -> Tuple[List[RemoteFile], bool]:
        """Matching files, newest first, and whether the list was truncated."""
        with self._lock:
            out = self._run(find_command(folder, name_filter, text, recursive),
                            SEARCH_TIMEOUT)
        if out.strip() == _NODIR:
            raise RemoteError(f"La cartella {folder} non esiste su {self.host}.")
        files = parse_find_output(out)
        truncated = len(files) > MAX_RESULTS
        files = files[:MAX_RESULTS]
        files.sort(key=lambda f: f.mtime, reverse=True)
        return files, truncated

    def _sftp_client(self):
        if self._sftp is None:
            self._sftp = self._client.open_sftp()
        return self._sftp

    def resolve_folder(self, folder: str) -> str:
        """Absolute logical path of `folder`: '~' / '~/x' / relative paths are
        anchored to the remote home, '.' and '..' are collapsed textually
        (symbolic links are NOT resolved, so the path stays the one shown)."""
        folder = (folder or "").strip() or "~"
        with self._lock:
            if folder == "~" or folder.startswith("~/") or not folder.startswith("/"):
                home = self._sftp_client().normalize(".")
                rest = folder[2:] if folder.startswith("~/") else ("" if folder == "~" else folder)
                folder = posixpath.join(home, rest) if rest else home
        return posixpath.normpath(folder) if folder != "/" else "/"

    def listdir(self, folder: str) -> Tuple[str, List[RemoteFile]]:
        """(absolute logical path, entries) of `folder`, folders first.
        Links are followed to tell folders from files; a broken link is
        listed as a 0-byte file."""
        path = self.resolve_folder(folder)
        with self._lock:
            sftp = self._sftp_client()
            try:
                attrs = sftp.listdir_attr(path)
            except PermissionError:
                raise RemoteError(f"Permesso negato su {path}.")
            except FileNotFoundError:
                raise RemoteError(f"La cartella {path} non esiste su {self.host}.")
            except OSError as e:
                raise RemoteError(f"Impossibile leggere {path}: {e}")
            entries = []
            for a in attrs:
                full = join_path(path, a.filename)
                mode = a.st_mode or 0
                is_link = stat_mod.S_ISLNK(mode)
                if is_link:
                    try:
                        a2 = sftp.stat(full)          # follows the link
                        mode, size, mtime = a2.st_mode or 0, a2.st_size, a2.st_mtime
                    except OSError:
                        mode, size, mtime = 0, 0, a.st_mtime   # broken link
                else:
                    size, mtime = a.st_size, a.st_mtime
                entries.append(RemoteFile(full, int(size or 0), float(mtime or 0),
                                          is_dir=stat_mod.S_ISDIR(mode), is_link=is_link))
        entries.sort(key=lambda f: (not f.is_dir, f.name.lower()))
        return path, entries

    def download(self, remote_path: str, local_path: Path,
                 progress: Callable[[int, int], None] = lambda done, total: None) -> None:
        with self._lock:
            sftp = self._sftp_client()
            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = local_path.with_name(local_path.name + ".part")
            try:
                sftp.get(remote_path, str(tmp), callback=progress)
                tmp.replace(local_path)
            except OSError as e:
                tmp.unlink(missing_ok=True)
                raise RemoteError(f"Download di {remote_path} non riuscito: {e}")

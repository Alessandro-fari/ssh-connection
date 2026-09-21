"""
"Init" flow orchestration (per environment).

Each environment has its OWN menu button ("Init TEST" / "Init PROD") and its
OWN 2FA token: one click opens that environment's jump host plus the settlement
DB hosts hanging off its tunnels. All consoles are opened HIDDEN (no visible
window, no taskbar/Alt-Tab entry) — they exist only to keep the SSH sessions
and their LocalForward DB tunnels alive.

Why per environment: a single SecurID token authenticates only ONE login (the
server rejects the same token on a second login, whether sequential —
"Invalid username or password" — or simultaneous — "Session not started or
timedout"). So TEST and PROD each need their own freshly generated token.

Design notes:
- The jump host IS registered in the tracker (menu shows it active and normal
  per-host clicks reuse its tunnels); the DB targets are NOT, so they stay
  completely invisible in the menu.
- We keep our own list of every PID we spawned, both to block a duplicate run
  of the same environment and to kill the hidden consoles on exit (the user
  cannot close windows they can't see).
"""

import base64
import logging
import subprocess
import threading
from typing import Callable, List, Optional, Tuple

from ..config.config_loader import ConfigLoader
from .connection_tracker import tracker
from .console_injector import ConsoleInjector
from .ssh_launcher import SshLauncher

# One entry per environment. Each is opened by its OWN menu button with its
# OWN 2FA token — a single SecurID token authenticates only one login (the
# server rejects reuse), so TEST and PROD cannot share one token.
#   login   — jump host (password + token injected)
#   targets — DB/settlement hosts reachable through the jump host's tunnels;
#             opened hidden and unregistered (they only hold the tunnels open)
INIT_ENVS = {
    "TEST": {"login": "login_test", "targets": ("stlit1tf01", "stlit1te01")},
    "PROD": {"login": "login_prod", "targets": ("stlit1pf01", "stlit1pe01")},
}

NotifyFn = Callable[[str, str], None]


class InitOrchestrator:
    """Runs the per-environment Init flow and owns the hidden-console PIDs."""

    # Reentrant: start() holds the lock and calls _live_procs(), which
    # re-acquires it — a plain Lock would deadlock the tray's main thread.
    _lock = threading.RLock()
    _running = set()  # environments currently being opened
    # (host, pid, create_time) for every console we spawned during Init.
    _procs: List[Tuple[str, int, float]] = []

    @classmethod
    def start(cls, env: str, notify: NotifyFn) -> None:
        """Open one environment ("TEST"/"PROD") in a background thread."""
        env = env.upper()
        logging.info(f"Init.start({env}) called")
        if env not in INIT_ENVS:
            logging.error(f"Init.start(): unknown environment {env!r}")
            return
        with cls._lock:
            if env in cls._running:
                logging.info(f"Init.start(): {env} already running, ignoring click")
                notify(f"Init {env}", f"Init {env} già in corso")
                return
            cls._running.add(env)
        logging.info(f"Init.start(): spawning worker thread for {env}")
        threading.Thread(target=cls._run, args=(env, notify), daemon=True).start()

    @classmethod
    def _run(cls, env: str, notify: NotifyFn) -> None:
        cfg = INIT_ENVS[env]
        login = cfg["login"]
        targets = cfg["targets"]
        try:
            token = cls._ask_token(env)
            if not token:
                logging.info(f"Init {env} cancelled: no token entered")
                return

            password = ConfigLoader.load().get_password()
            if not password:
                notify(f"Init {env}", "Password non configurata")
                return

            opened: List[str] = []
            failed: List[str] = []

            # 1) Jump host: password + token in one go (single login → the
            #    positional prompt gating in inject_secrets handles the
            #    password prompt then the TOKEN prompt).
            if tracker.is_active(login):
                logging.info(f"Init {env}: {login} already active, reusing it")
                login_ok = True
            else:
                pid = SshLauncher.launch_for_init(login, secrets=[password, token])
                if pid:
                    cls._record(login, pid)
                    opened.append(login)
                    login_ok = True
                else:
                    failed.append(login)
                    login_ok = False

            # 2) DB targets (hidden, unregistered) once the login is up.
            if login_ok:
                for target in targets:
                    try:
                        SshLauncher._wait_for_tunnel(target)
                        pid = SshLauncher.launch_for_init(target, secrets=[password], register=False)
                        if pid:
                            cls._record(target, pid)
                            opened.append(target)
                        else:
                            failed.append(target)
                    except Exception as e:
                        logging.error(f"Init {env}: error opening {target}: {e}", exc_info=True)
                        failed.append(target)

            total = 1 + len(targets)
            if failed:
                notify(f"Init {env} parziale", f"{len(opened)}/{total} connessioni attive. "
                                               f"Fallite: {', '.join(failed)}")
            else:
                notify(f"Init {env}", f"{len(opened)}/{total} connessioni attive")
        except Exception as e:
            logging.error(f"Init {env} failed: {e}", exc_info=True)
            notify(f"Init {env}", f"Errore: {e}")
        finally:
            with cls._lock:
                cls._running.discard(env)

    # ------------------------------------------------------------------

    # Windows Forms token dialog, run as a SEPARATE PowerShell process.
    # Rationale: the tray runs a Win32 message loop on the main thread, and
    # creating a tkinter root + mainloop in a worker thread of the same
    # process destabilises that loop (the tray menu stops responding). A
    # separate STA process has its own message loop, always shows in the
    # foreground, and cannot interfere with the tray. It prints the token to
    # stdout on OK and nothing on cancel.
    _TOKEN_DIALOG_PS = r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# Win32 helpers to force the dialog to the foreground: a process spawned from
# the tray menu does not own the foreground, so ShowDialog alone opens behind
# other windows (invisible to the user).
$fg = @'
using System;
using System.Runtime.InteropServices;
public static class Fg {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
}
'@
Add-Type -TypeDefinition $fg

$header = [System.Drawing.Color]::FromArgb(43,108,176)
$bg     = [System.Drawing.Color]::FromArgb(244,246,249)
$okCol  = [System.Drawing.Color]::FromArgb(46,160,67)
$muted  = [System.Drawing.Color]::FromArgb(90,107,123)

$form = New-Object System.Windows.Forms.Form
$form.Text = 'SSH Init'
$form.ClientSize = New-Object System.Drawing.Size(380,208)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.BackColor = $bg
$form.Font = New-Object System.Drawing.Font('Segoe UI',9)

$band = New-Object System.Windows.Forms.Panel
$band.Size = New-Object System.Drawing.Size(380,52)
$band.Location = New-Object System.Drawing.Point(0,0)
$band.BackColor = $header
$form.Controls.Add($band)

$title = New-Object System.Windows.Forms.Label
$title.Text = '@@TITLE@@'
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font('Segoe UI',14,[System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.BackColor = $header
$title.Location = New-Object System.Drawing.Point(20,11)
$band.Controls.Add($title)

$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = 'Inserisci il token 2FA per aprire le connessioni'
$lbl.AutoSize = $true
$lbl.Location = New-Object System.Drawing.Point(22,68)
$form.Controls.Add($lbl)

$sub = New-Object System.Windows.Forms.Label
$sub.Text = '@@SUBTITLE@@'
$sub.ForeColor = $muted
$sub.AutoSize = $true
$sub.Location = New-Object System.Drawing.Point(22,88)
$form.Controls.Add($sub)

$tb = New-Object System.Windows.Forms.TextBox
$tb.UseSystemPasswordChar = $true
$tb.Font = New-Object System.Drawing.Font('Segoe UI',13)
$tb.Size = New-Object System.Drawing.Size(336,28)
$tb.Location = New-Object System.Drawing.Point(22,116)
$tb.TextAlign = 'Center'
$form.Controls.Add($tb)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'OK'
$ok.Size = New-Object System.Drawing.Size(96,32)
$ok.Location = New-Object System.Drawing.Point(262,160)
$ok.FlatStyle = 'Flat'
$ok.FlatAppearance.BorderSize = 0
$ok.BackColor = $okCol
$ok.ForeColor = [System.Drawing.Color]::White
$ok.Font = New-Object System.Drawing.Font('Segoe UI',9,[System.Drawing.FontStyle]::Bold)
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($ok)

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Annulla'
$cancel.Size = New-Object System.Drawing.Size(96,32)
$cancel.Location = New-Object System.Drawing.Point(158,160)
$cancel.FlatStyle = 'Flat'
$cancel.FlatAppearance.BorderSize = 1
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)

$form.AcceptButton = $ok
$form.CancelButton = $cancel
$form.Add_Shown({
    $form.Activate()
    [void][Fg]::ShowWindow($form.Handle, 9)   # SW_RESTORE
    [void][Fg]::BringWindowToTop($form.Handle)
    [void][Fg]::SetForegroundWindow($form.Handle)
    [void]$tb.Focus()
})

$res = $form.ShowDialog()
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Out.Write($tb.Text)
}
'''

    @staticmethod
    def _ask_token(env: str) -> Optional[str]:
        """
        Prompt for the 2FA token via a Windows Forms dialog running in a
        separate STA PowerShell process. Returns the token, or None if
        cancelled/closed/empty.
        """
        try:
            logging.info(f"Opening token dialog for {env} (powershell subprocess)")
            cfg = INIT_ENVS[env]
            subtitle = "  -  ".join((cfg["login"],) + cfg["targets"])
            script = (InitOrchestrator._TOKEN_DIALOG_PS
                      .replace("@@TITLE@@", f"Init {env}")
                      .replace("@@SUBTITLE@@", subtitle))
            encoded = base64.b64encode(
                script.encode("utf-16-le")).decode("ascii")
            # stdin=DEVNULL is required: after any console injection the
            # tray process's inherited STD_INPUT_HANDLE points to a console
            # freed by FreeConsole/AttachConsole. With stdout/stderr
            # redirected, subprocess would GetStdHandle+DuplicateHandle the
            # stale stdin and fail instantly (WinError 6/50) — the dialog
            # never opened on any Init after the first.
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            token = (proc.stdout or "").strip()
            logging.info(f"Token dialog closed (got token: {bool(token)}, "
                         f"stderr: {(proc.stderr or '').strip()[:200]!r})")
            return token or None
        except Exception as e:
            logging.error(f"Token dialog failed: {e}", exc_info=True)
            return None

    @classmethod
    def _record(cls, host: str, pid: int) -> None:
        import psutil
        try:
            ct = psutil.Process(pid).create_time()
        except psutil.Error:
            ct = 0.0
        with cls._lock:
            cls._procs.append((host, pid, ct))

    @classmethod
    def _live_procs(cls) -> List[Tuple[str, int, float]]:
        import psutil
        with cls._lock:
            entries = list(cls._procs)
        alive = []
        for host, pid, ct in entries:
            try:
                proc = psutil.Process(pid)
                if proc.is_running() and abs(proc.create_time() - ct) < 1.0:
                    alive.append((host, pid, ct))
            except psutil.Error:
                continue
        with cls._lock:
            cls._procs = alive
        return alive

    @classmethod
    def is_running(cls) -> bool:
        return bool(cls._running) or bool(cls._live_procs())

    @classmethod
    def shutdown(cls) -> None:
        """Kill every hidden console spawned by Init (called on app exit)."""
        import psutil
        for host, pid, ct in cls._live_procs():
            try:
                proc = psutil.Process(pid)
                if abs(proc.create_time() - ct) < 1.0:
                    proc.kill()
                    logging.info(f"Init shutdown: killed hidden console {host} (PID {pid})")
            except psutil.Error:
                continue
        with cls._lock:
            cls._procs = []

"""
"Avvio automatico con Windows" via the per-user Run registry key.

HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run needs no admin rights
and is what Task Manager's "Startup apps" tab lists. The registry itself is
the source of truth (not the prefs file), so a value removed from Task
Manager is reflected in the settings dialog.

build_release.py historically creates a shortcut in the user's Startup
folder instead. Both count as "enabled"; enabling never adds the registry
entry when the shortcut already exists, and disabling removes both, so the
application can never be started twice at logon.
"""

import logging
import sys
import winreg
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "SSH Connection Manager"
STARTUP_SHORTCUT = (Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu"
                    / "Programs/Startup/SSH Connection Manager.lnk")

# Passed on the command line so main.py skips the "starting..." message box
# at every logon.
AUTOSTART_FLAG = "--autostart"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {AUTOSTART_FLAG}'
    # Development: pythonw (no console window) + run.py of this checkout.
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    run_py = Path(__file__).resolve().parents[3] / "run.py"
    return f'"{pythonw if pythonw.exists() else exe}" "{run_py}" {AUTOSTART_FLAG}'


def _registered_exe() -> str:
    """Executable of the current Run entry ('' if none)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            cmd, _ = winreg.QueryValueEx(key, _VALUE_NAME)
    except OSError:
        return ""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    return cmd.split(" ", 1)[0]


def describe() -> str:
    """One line for the settings dialog: what starts at logon."""
    if STARTUP_SHORTCUT.exists():
        return ("Attivo tramite il collegamento nella cartella Esecuzione automatica "
                "(creato da build_release.py).")
    exe = _registered_exe()
    if exe:
        if not Path(exe).exists():
            return (f"Attenzione: all'accesso verrebbe lanciato {exe}, che non esiste più. "
                    f"Salva per aggiornarlo al percorso attuale.")
        return f"All'accesso a Windows viene avviato: {exe}"
    return f"Se attivo, all'accesso a Windows verrà avviato: {_command().split(chr(34))[1]}"


def refresh_path() -> None:
    """At startup: if the Run entry points to an executable that no longer
    exists (exe moved or renamed), point it at the one running now."""
    exe = _registered_exe()
    if exe and not Path(exe).exists():
        logging.info(f"Autostart entry pointed to missing {exe}: updating")
        set_enabled(True)


def is_enabled() -> bool:
    return _registry_enabled() or STARTUP_SHORTCUT.exists()


def _registry_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Enable/disable start at logon. Returns True on success."""
    if enabled and STARTUP_SHORTCUT.exists():
        return True   # the build's Startup shortcut already does it
    if not enabled:
        try:
            STARTUP_SHORTCUT.unlink(missing_ok=True)
        except OSError as e:
            logging.error(f"Could not remove {STARTUP_SHORTCUT}: {e}")
            return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        logging.info(f"Autostart {'enabled' if enabled else 'disabled'}")
        return True
    except OSError as e:
        logging.error(f"Could not change autostart: {e}")
        return False

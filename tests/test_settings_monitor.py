#!/usr/bin/env python3
"""
Tests for preferences (AppSettings), configurable hotkey, favorites/recents
in menu and popup, the settings dialog, and the session monitor
notifications (connection lost, tunnel lost, keepalive failed).

The ssh session is simulated by a harmless Windows binary (cmd.exe /
PING.EXE) run IN PLACE from System32 and recognised through
connection_tracker.SSH_PROCESS_NAMES. Never copy or rename a system
executable (e.g. to ssh.exe): antivirus software rightly flags that as
masquerading (MITRE T1036.003) and kills the test.

Run:  py tests/test_settings_monitor.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from ssh_connection import notifications
from ssh_connection.config.app_settings import AppSettings, MAX_RECENTS
from ssh_connection.gui.hotkey_manager import (MOD_CONTROL, MOD_SHIFT, VK_SPACE,
                                               format_binding, parse_binding)
from ssh_connection.ssh import connection_tracker as ct
from ssh_connection.ssh.connection_tracker import tracker
from ssh_connection.ssh.ssh_config_parser import SshConfigParser

EXAMPLE_CONFIG = project_root / "example_config" / "config"
TMP = Path(tempfile.mkdtemp(prefix="sshcm-test-"))  # prefs file only

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'OK' if condition else 'FAIL'}  {name}  {'' if condition else detail}")


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def fake_ssh(exe: str, args: str) -> subprocess.Popen:
    """A `powershell -NoExit` console (like ours) whose child plays the ssh
    session: a system binary run from its own path, matched by name."""
    ct.SSH_PROCESS_NAMES.clear()
    ct.SSH_PROCESS_NAMES.add(exe.lower())
    path = Path(os.environ["SystemRoot"]) / "System32" / exe
    return subprocess.Popen(
        ['powershell', '-NoExit', '-Command', f"& '{path}' {args}"],
        creationflags=subprocess.CREATE_NO_WINDOW)


class _Key:
    """Minimal stand-in for a Tk key event."""
    def __init__(self, keysym, keycode=0):
        self.keysym = keysym
        self.keycode = keycode


# ----------------------------------------------------------------------

def test_app_settings():
    print("\n[1] AppSettings")
    check("defaults without a file", AppSettings.get("hotkey") == "Ctrl+Shift+Space"
          and AppSettings.get("keepalive_interval") == 240)
    AppSettings.update(favorites=["a", "b", "a"])
    from ssh_connection.gui.search_dialog import save_prefs_env, load_prefs_env
    save_prefs_env("PROD")
    check("search_env saved", load_prefs_env() == "PROD")
    check("saving search_env keeps favorites (no clobber)", AppSettings.favorites() == ["a", "b"],
          str(AppSettings.favorites()))
    for h in ["h1", "h2", "h3", "h4", "h5", "h6", "h2"]:
        AppSettings.add_recent(h)
    rec = AppSettings.recents()
    check("recents MRU, capped", rec[0] == "h2" and len(rec) == MAX_RECENTS and rec.count("h2") == 1,
          str(rec))
    check("toggle favorite off", AppSettings.toggle_favorite("a") is False and AppSettings.favorites() == ["b"])
    check("toggle favorite on", AppSettings.toggle_favorite("c") is True and AppSettings.favorites() == ["b", "c"])
    AppSettings.update(keepalive_interval=5, tunnel_timeout="abc")
    check("numeric settings clamped / defaulted",
          AppSettings.get("keepalive_interval") == 30 and AppSettings.get("tunnel_timeout") == 120)
    AppSettings.update(notifications={"tunnel_lost": False, "bogus": True})
    n = AppSettings.get("notifications")
    check("notifications merged with defaults", n["tunnel_lost"] is False and n["init"] is True
          and "bogus" not in n, str(n))
    AppSettings.path.write_text("{broken", encoding="utf-8")
    check("corrupt prefs file falls back to defaults", AppSettings.favorites() == [])
    AppSettings.path.unlink()

    from ssh_connection.ssh.ssh_launcher import SshLauncher
    AppSettings.update(keepalive_interval=120, tunnel_timeout=45)
    check("keepalive command follows the setting", SshLauncher.keepalive_command() == "watch -n 120 date")
    check("tunnel wait follows the setting", SshLauncher.tunnel_wait_seconds() == 45.0)
    AppSettings.path.unlink()


def test_hotkey_binding():
    print("\n[2] Hotkey binding")
    check("parse default", parse_binding("Ctrl+Shift+Space") == (MOD_CONTROL | MOD_SHIFT, VK_SPACE))
    check("parse letter, case-insensitive", parse_binding("ctrl+alt+k") is not None)
    check("F-key alone allowed", parse_binding("F9") is not None)
    check("plain letter rejected", parse_binding("K") is None)
    check("unknown key rejected", parse_binding("Ctrl+Enter") is None)
    check("format round-trip", format_binding(MOD_CONTROL | MOD_SHIFT, "Space") == "Ctrl+Shift+Space")

    from ssh_connection.gui.settings_dialog import binding_from_keys
    check("capture: Ctrl+Shift+Space", binding_from_keys({"Ctrl", "Shift"}, "space", 0x20) == "Ctrl+Shift+Space")
    check("capture: VK wins over layout keysym (Shift+1 = 'exclam' on IT)",
          binding_from_keys({"Ctrl", "Shift"}, "exclam", 0x31) == "Ctrl+Shift+1")
    check("capture: modifier order is canonical",
          binding_from_keys({"Alt", "Ctrl"}, "k", 0x4B) == "Ctrl+Alt+K")
    check("capture: no modifier rejected", binding_from_keys(set(), "k", 0x4B) is None)
    check("capture: unsupported key rejected", binding_from_keys({"Ctrl"}, "Return", 0x0D) is None)

    from ssh_connection.gui.hotkey_manager import HotkeyManager
    hk = HotkeyManager(on_triggered=lambda: None, binding="Ctrl+Alt+Shift+F11")
    check("register an unusual combination", hk.start())
    check("rebind to another free combination", hk.rebind("Ctrl+Alt+Shift+F12") and hk.binding == "Ctrl+Alt+Shift+F12")
    # A second manager cannot take the same combination: rebind must fail
    # and restore the previous binding.
    hk2 = HotkeyManager(on_triggered=lambda: None, binding="Ctrl+Alt+Shift+F10")
    hk2.start()
    check("rebind to a taken combination fails", hk2.rebind("Ctrl+Alt+Shift+F12") is False)
    check("...and restores the previous one", hk2.binding == "Ctrl+Alt+Shift+F10")
    hk.stop()
    hk2.stop()


def test_menu_favorites():
    print("\n[3] Tray menu: favorites and recents")
    from ssh_connection.gui.tray_icon_manager import TrayIconManager
    AppSettings.update(favorites=["web-server-pe01", "gone-host"], recents=["app-server-tf01"])
    mgr = TrayIconManager()
    items = list(mgr._menu_items())
    labels = [getattr(i, "text", None) for i in items]
    check("Preferiti header at the top", labels[0] == "Preferiti", str(labels))
    check("favorite listed right below", labels[1] == "web-server-pe01", str(labels))
    check("host missing from config skipped", "gone-host" not in labels)
    rec = next(i for i in items if getattr(i, "text", None) == "Recenti")
    check("Recenti submenu", [m.text for m in rec.submenu.items] == ["app-server-tf01"])
    plan = {idx: (key, children) for idx, key, children in mgr._bitmap_plan}
    check("favorite gets its PROD bitmap", plan.get(1, (None,))[0] == "prod_idle", str(plan))
    check("recents get TEST bitmaps", plan.get(labels.index("Recenti"), (0, []))[1] == ["test_idle"], str(plan))
    check("settings entry renamed", "Impostazioni..." in labels, str(labels))
    AppSettings.path.unlink()

    from ssh_connection.ssh.ssh_launcher import SshLauncher
    real = SshLauncher.connect
    SshLauncher.connect = staticmethod(lambda name: None)
    try:
        mgr.connect_to_host("login_test")
        check("connecting records a recent", AppSettings.recents() == ["login_test"])
    finally:
        SshLauncher.connect = real
        AppSettings.path.unlink()


def test_popup_and_settings_dialog():
    print("\n[4] Search popup sections + settings dialog (opens windows briefly)")
    from ssh_connection.gui.search_dialog import SearchPopup
    from ssh_connection.gui.settings_dialog import SettingsDialog
    hosts = [("TEST", ["login_test", "app-server-tf01"]), ("PROD", ["login_prod", "db-pe01"])]
    AppSettings.update(favorites=["db-pe01"], recents=["login_test", "db-pe01"])
    popup = SearchPopup(host_provider=lambda: hosts, on_select=lambda h: None)
    popup.start()
    done = threading.Event()
    out = {}

    def on_ui(fn):
        def wrapped():
            try:
                fn()
            finally:
                done.set()
        done.clear()
        popup.run_on_ui(wrapped)
        return done.wait(10)

    popup.show("Tutti")
    time.sleep(0.5)
    on_ui(lambda: out.update(rows=[(r["type"], r.get("label"), r["host"]) for r in popup._rows]))
    rows = out["rows"]
    check("popup starts with Preferiti", rows[0][0] == "sep" and "Preferiti" in rows[0][1]
          and rows[1][2] == "db-pe01", str(rows[:4]))
    check("recents exclude favorites", rows[2] == ("sep", "Recenti", None) and rows[3][2] == "login_test",
          str(rows[:5]))
    check("first favorite preselected", on_ui(lambda: out.update(sel=popup._current())) and out["sel"] == 1)

    on_ui(lambda: popup._var_filter.set("l"))
    on_ui(lambda: out.update(rows=[r["host"] for r in popup._rows if r["type"] == "host"]))
    check("filtering drops quick sections", out["rows"].count("login_test") == 1, str(out["rows"]))

    on_ui(lambda: (popup._var_filter.set(""), popup._select(3), popup._toggle_favorite()))
    check("Ctrl+D toggles favorite", AppSettings.favorites() == ["db-pe01", "login_test"],
          str(AppSettings.favorites()))
    on_ui(popup._hide)

    saved = {}
    suspends = []
    dlg = SettingsDialog(ui_host=popup, host_provider=lambda: hosts,
                         on_save=lambda v: saved.update(v) or None,
                         version="9.9.9", ssh_config_path=EXAMPLE_CONFIG,
                         hotkey_suspend=suspends.append)
    dlg.prewarm()
    dlg.show()
    time.sleep(0.8)
    on_ui(lambda: out.update(visible=dlg._top.winfo_viewable(),
                             hk=dlg._var_hotkey.get(),
                             favs=list(dlg._favorites)))
    check("settings dialog shown", out["visible"] == 1)
    check("hotkey field shows the current binding", out["hk"] == "Ctrl+Shift+Space", out["hk"])
    check("favorites list loaded", out["favs"] == ["db-pe01", "login_test"], str(out["favs"]))

    # Hotkey capture: focus -> global hotkey suspended, keys typed, resumed.
    def capture():
        dlg._capture_start()
        dlg._on_key_press(_Key("Control_L"))
        dlg._on_key_press(_Key("Alt_L"))
        out["preview"] = dlg._var_hotkey.get()
        dlg._on_key_press(_Key("k", 0x4B))
        dlg._capture_end()
    on_ui(capture)
    check("capture previews held modifiers", out["preview"] == "Ctrl+Alt+...", out["preview"])
    check("captured combination", dlg._binding == "Ctrl+Alt+K", dlg._binding)
    check("global hotkey suspended during capture, then resumed", suspends == [True, False], str(suspends))

    def invalid_then_escape():
        dlg._capture_start()
        dlg._on_key_press(_Key("k", 0x4B))          # no modifier
        out["after_invalid"] = dlg._binding
        dlg._on_key_press(_Key("Escape", 0x1B))
        dlg._capture_end()
    on_ui(invalid_then_escape)
    check("key without modifier rejected, binding kept", out["after_invalid"] == "Ctrl+Alt+K")

    # Favorites: filter, select a row, Aggiungi; then Su / Rimuovi.
    def favorites():
        dlg._var_fav_filter.set("app")
        out["shown"] = list(dlg._shown_hosts)
        dlg._hosts_list.selection_clear(0, "end")
        dlg._hosts_list.selection_set(0)
        dlg._add_favorite()
        dlg._add_favorite()                           # twice: no duplicate
        out["after_add"] = list(dlg._favorites)
        dlg._fav_list.selection_clear(0, "end")
        dlg._fav_list.selection_set(2)
        dlg._move_favorite(-1)
        out["after_up"] = list(dlg._favorites)
        dlg._fav_list.selection_clear(0, "end")
        dlg._fav_list.selection_set(0)
        dlg._remove_favorite()
        out["after_remove"] = list(dlg._favorites)
    on_ui(favorites)
    check("host filter", out["shown"] == ["app-server-tf01"], str(out["shown"]))
    check("Aggiungi appends once", out["after_add"] == ["db-pe01", "login_test", "app-server-tf01"],
          str(out["after_add"]))
    check("Su reorders", out["after_up"] == ["db-pe01", "app-server-tf01", "login_test"], str(out["after_up"]))
    check("Rimuovi", out["after_remove"] == ["app-server-tf01", "login_test"], str(out["after_remove"]))

    def edit_and_save():
        dlg._var_keepalive.set("300")
        dlg._notif_vars["tunnel_lost"].set(False)
        dlg._save()
    on_ui(edit_and_save)
    check("hotkey saved", saved.get("hotkey") == "Ctrl+Alt+K", str(saved))
    check("keepalive saved", saved.get("keepalive_interval") == 300)
    check("notification switch saved", saved.get("notifications", {}).get("tunnel_lost") is False)
    check("favorites saved in order", saved.get("favorites") == ["app-server-tf01", "login_test"],
          str(saved.get("favorites")))
    on_ui(lambda: out.update(visible=dlg._top.winfo_viewable()))
    check("dialog hidden after save", out["visible"] == 0)

    dlg.show()
    time.sleep(0.3)
    def bad():
        dlg._var_tunnel.set("5")
        dlg._save()
        out["err"] = dlg._var_error.get()
        out["visible"] = dlg._top.winfo_viewable()
        dlg._reset_hotkey()
        out["reset"] = dlg._binding
    on_ui(bad)
    check("out-of-range value reported, dialog stays open", out["err"] and out["visible"] == 1, str(out))
    check("Predefinita restores Ctrl+Shift+Space", out["reset"] == "Ctrl+Shift+Space")
    on_ui(dlg._hide)
    popup.stop()
    AppSettings.path.unlink()


def test_monitor_connection_lost():
    print("\n[5] Session monitor: connection lost")
    import psutil
    from ssh_connection.ssh.session_monitor import SessionMonitor
    from ssh_connection.ssh.init_orchestrator import InitOrchestrator
    got = []
    notifications.set_sink(lambda t, m: got.append((t, m)))
    mon = SessionMonitor()
    mon._check_tunnels = lambda: None

    # Visible console, "ssh" exits with 255 -> notified.
    p = fake_ssh("cmd.exe", "/c 'ping -n 3 127.0.0.1 >nul & exit 255'")
    tracker.register("app-server-tf01", p.pid)
    wait_until(lambda: ct.ssh_child_alive(p.pid))
    mon.tick()
    check("session picked up", p.pid in mon._sessions)
    wait_until(lambda: not ct.ssh_child_alive(p.pid))
    mon.tick()
    check("exit 255 in a visible console notified", len(got) == 1 and "app-server-tf01" in got[0][1], str(got))
    ct.kill_console(p.pid)
    tracker._connections.clear()

    # Visible console, normal logout (exit 0) -> silent.
    got.clear()
    p = fake_ssh("cmd.exe", "/c 'ping -n 3 127.0.0.1 >nul & exit 0'")
    tracker.register("app-server-tf01", p.pid)
    wait_until(lambda: ct.ssh_child_alive(p.pid))
    mon.tick()
    wait_until(lambda: not ct.ssh_child_alive(p.pid))
    mon.tick()
    check("normal logout is silent", got == [], str(got))
    ct.kill_console(p.pid)
    tracker._connections.clear()

    # Killed on purpose (what kill_console does) -> silent.
    p = fake_ssh("PING.EXE", "-n 30 127.0.0.1")
    tracker.register("app-server-tf01", p.pid)
    wait_until(lambda: ct.ssh_child_alive(p.pid))
    mon.tick()
    ssh = next(c for c in psutil.Process(p.pid).children(recursive=True) if ct.is_ssh_process(c))
    ct.expect_exit(ssh.pid)
    ssh.kill()
    time.sleep(0.3)
    mon.tick()
    check("our own kill is silent", got == [], str(got))
    ct.kill_console(p.pid)
    tracker._connections.clear()

    # Hidden Init console: any exit notified, orphan console killed.
    p = fake_ssh("PING.EXE", "-n 30 127.0.0.1")
    InitOrchestrator._record("stlit1tf01", p.pid)
    wait_until(lambda: ct.ssh_child_alive(p.pid))
    mon.tick()
    for c in psutil.Process(p.pid).children(recursive=True):
        if ct.is_ssh_process(c):
            c.kill()
    time.sleep(0.3)
    mon.tick()
    check("hidden session drop notified with Init hint",
          len(got) == 1 and "Init TEST" in got[0][1], str(got))
    check("orphan hidden console killed", wait_until(lambda: p.poll() is not None, 5))
    InitOrchestrator._procs = []
    mon.stop()


def test_monitor_tunnels():
    print("\n[6] Session monitor: tunnel lost")
    from ssh_connection.ssh.session_monitor import SessionMonitor, _Session
    SM = SessionMonitor
    check("parse localforward", SM.parse_forward_port("localforward 1524 [fdb02x]:1524") == 1524)
    check("parse bind address", SM.parse_forward_port("localforward [127.0.0.1]:2222 [x]:22") == 2222)
    check("ignore other keys", SM.parse_forward_port("hostname 10.0.0.1") is None)

    got = []
    notifications.set_sink(lambda t, m: got.append((t, m)))
    mon = SessionMonitor()
    mon.TUNNEL_CHECK_SECONDS = 0
    sess = _Session("stlit1tf01", True, os.getpid(), os.getpid())
    mon._sessions = {1: sess}
    mon._forward_ports = lambda host: [1524]
    listening = {1524}
    mon._listening_ssh_ports = lambda: set(listening)
    mon._check_tunnels()
    check("listening port is healthy", got == [])
    listening.clear()
    mon._check_tunnels()
    check("port gone -> tunnel lost", len(got) == 1 and "1524" in got[0][1], str(got))
    mon._check_tunnels()
    check("reported once", len(got) == 1)
    listening.add(1524)
    mon._check_tunnels()
    listening.clear()
    mon._check_tunnels()
    check("re-armed after recovery", len(got) == 2)

    got.clear()
    mon2 = SessionMonitor()
    mon2.TUNNEL_CHECK_SECONDS = 0
    fresh = _Session("stlit1tf01", True, os.getpid(), os.getpid())
    mon2._sessions = {1: fresh}
    mon2._forward_ports = lambda host: [1524]
    mon2._listening_ssh_ports = lambda: set()
    mon2._check_tunnels()
    check("new session gets a grace period", got == [])
    fresh.started -= SM.TUNNEL_GRACE + 1
    mon2._check_tunnels()
    check("never bound after grace -> reported (port in use?)",
          len(got) == 1 and "occupata" in got[0][1], str(got))

    AppSettings.update(notifications={"tunnel_lost": False})
    got.clear()
    notifications.notify("tunnel_lost", "t", "m")
    check("disabled kind is not shown", got == [])
    AppSettings.path.unlink()
    sess.close()
    fresh.close()


def test_keepalive_failed():
    print("\n[7] Keepalive failed notification")
    from ssh_connection.ssh import ssh_launcher as sl
    from ssh_connection.ssh.console_injector import ConsoleInjector
    got = []
    notifications.set_sink(lambda t, m: got.append((t, m)))
    real = (ConsoleInjector.run_command_at_shell_prompt, ConsoleInjector.peek_tail, sl.ssh_child_alive)
    try:
        sl.SshLauncher.KEEPALIVE_VERIFY_SECONDS = 0
        ConsoleInjector.run_command_at_shell_prompt = classmethod(lambda cls, pid, cmd, timeout=0: False)
        sl.ssh_child_alive = lambda pid: True
        sl.SshLauncher._run_keepalive(1, "login_test")
        check("prompt never appeared -> notified", len(got) == 1 and "login_test" in got[0][1], str(got))

        got.clear()
        sl.ssh_child_alive = lambda pid: False
        sl.SshLauncher._run_keepalive(1, "login_test")
        check("session gone -> left to the monitor", got == [])

        ConsoleInjector.run_command_at_shell_prompt = classmethod(lambda cls, pid, cmd, timeout=0: True)
        ConsoleInjector.peek_tail = classmethod(
            lambda cls, pid, lines=6: "user@h:~$ watch -n 240 date\n-bash: watch: command not found\nuser@h:~$")
        sl.ssh_child_alive = lambda pid: True
        sl.SshLauncher._run_keepalive(1, "login_test")
        check("watch missing -> notified", len(got) == 1 and "watch" in got[0][1], str(got))

        got.clear()
        ConsoleInjector.peek_tail = classmethod(
            lambda cls, pid, lines=6: "Every 240.0s: date\n\nMon Sep 28 10:00:00 CEST 2026")
        sl.SshLauncher._run_keepalive(1, "login_test")
        check("running watch -> silent", got == [])
    finally:
        (ConsoleInjector.run_command_at_shell_prompt, ConsoleInjector.peek_tail, sl.ssh_child_alive) = real


def test_autostart_describe():
    print("\n[8] Autostart (read-only: the registry is never written here)")
    from ssh_connection.config import autostart
    text = autostart.describe()
    check("describe() says what starts at logon", isinstance(text, str) and len(text) > 10, text)
    check("dev command targets run.py with --autostart",
          "run.py" in autostart._command() and autostart.AUTOSTART_FLAG in autostart._command())


def test_credentials_file():
    print("\n[9] Credentials file (~/.m2/settings.xml) create + open")
    from ssh_connection.config.config_loader import ConfigLoader
    creds = TMP / "m2" / "settings.xml"
    real_path = ConfigLoader.maven_settings_path
    ConfigLoader.maven_settings_path = staticmethod(lambda: creds)
    try:
        check("created from template when missing", ConfigLoader.ensure_maven_settings() and creds.exists())
        check("template placeholders are not used as credentials",
              ConfigLoader._load_maven_credentials(creds) is None)
        creds.write_text(creds.read_text(encoding="utf-8")
                         .replace("INSERISCI_UTENTE", "DOM" + chr(92) + "mario.rossi")
                         .replace("INSERISCI_PASSWORD", "segreta"), encoding="utf-8")
        check("existing file never overwritten", ConfigLoader.ensure_maven_settings() is False
              and "segreta" in creds.read_text(encoding="utf-8"))
        c = ConfigLoader._load_maven_credentials(creds)
        check("filled template is read", c is not None and c.password == "segreta")
        plain = TMP / "plain.xml"
        plain.write_text("<settings><servers><server><id>a</id><username>u</username>"
                         "<password>p</password></server></servers></settings>", encoding="utf-8")
        c = ConfigLoader._load_maven_credentials(plain)
        check("settings.xml without namespace is read (old falsy-Element bug)",
              c is not None and (c.username, c.password) == ("u", "p"))

        # Dialog button: creates the file if needed and opens it in Notepad.
        import subprocess
        from ssh_connection.gui.settings_dialog import SettingsDialog
        opened = []
        real_popen = subprocess.Popen
        subprocess.Popen = lambda args, **kw: opened.append(args)
        creds.unlink()

        class _Var:
            def set(self, v): self.v = v
        dlg = SettingsDialog.__new__(SettingsDialog)
        dlg._var_info = _Var()
        try:
            dlg._open_credentials()
        finally:
            subprocess.Popen = real_popen
        check("button creates the missing file", creds.exists())
        check("button opens it in Notepad", opened == [["notepad.exe", str(creds)]], str(opened))
        check("button explains what to fill in", "INSERISCI_UTENTE" in dlg._var_info.v, dlg._var_info.v)
    finally:
        ConfigLoader.maven_settings_path = real_path


def main():
    AppSettings.path = TMP / "prefs.json"
    SshConfigParser.get_config_path = staticmethod(lambda: EXAMPLE_CONFIG)
    real_parse = SshConfigParser.parse_ssh_config
    SshConfigParser.parse_ssh_config = staticmethod(
        lambda config_path=None: real_parse(config_path or EXAMPLE_CONFIG))
    try:
        test_app_settings()
        test_hotkey_binding()
        test_menu_favorites()
        test_popup_and_settings_dialog()
        test_monitor_connection_lost()
        test_monitor_tunnels()
        test_keepalive_failed()
        test_autostart_describe()
        test_credentials_file()
    finally:
        ct.SSH_PROCESS_NAMES.clear()
        ct.SSH_PROCESS_NAMES.add("ssh.exe")
        notifications.set_sink(None)
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()

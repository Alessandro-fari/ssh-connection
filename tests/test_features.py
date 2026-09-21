#!/usr/bin/env python3
"""
Tests for connection tracking, jump-host resolution, dynamic menu and
targeted console password injection.

Run:  py tests/test_features.py
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from ssh_connection.ssh.ssh_config_parser import SshConfigParser
from ssh_connection.ssh.connection_tracker import ConnectionTracker, tracker
from ssh_connection.ssh.ssh_launcher import SshLauncher
from ssh_connection.ssh.console_injector import ConsoleInjector

EXAMPLE_CONFIG = project_root / "example_config" / "config"

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  OK  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


def test_parser_sections():
    print("\n[1] SshConfigParser on example config")
    host_map = SshConfigParser.parse_ssh_config(EXAMPLE_CONFIG)
    check("TEST section parsed", host_map.get("TEST") == ["login_test", "app-server-tf01", "web-server-te01"], str(host_map))
    check("PROD section parsed", host_map.get("PROD") == ["login_prod", "app-server-pf01", "web-server-pe01"], str(host_map))


def test_jump_host_resolution(monkey_config):
    print("\n[2] Jump host resolution")
    check("test machine -> login_test", SshLauncher._required_jump_host("app-server-tf01") == "login_test")
    check("prod machine -> login_prod", SshLauncher._required_jump_host("web-server-pe01") == "login_prod")
    check("login_test itself -> no jump", SshLauncher._required_jump_host("login_test") is None)
    check("unknown host -> no jump", SshLauncher._required_jump_host("nope") is None)


def test_connection_tracker():
    print("\n[3] ConnectionTracker")
    t = ConnectionTracker()
    proc = subprocess.Popen(
        ['powershell', '-Command', 'Start-Sleep -Seconds 15'],
        creationflags=subprocess.CREATE_NO_WINDOW)
    t.register("host-a", proc.pid)
    check("live process is active", t.is_active("host-a"))
    check("active_hosts contains host", t.active_hosts() == {"host-a"})
    check("unknown host inactive", not t.is_active("host-b"))
    proc.kill()
    proc.wait()
    time.sleep(0.2)
    check("dead process becomes inactive", not t.is_active("host-a"))
    check("active_hosts pruned", t.active_hosts() == set())


def test_dynamic_menu(monkey_config):
    print("\n[4] Dynamic tray menu")
    from ssh_connection.gui.tray_icon_manager import TrayIconManager
    mgr = TrayIconManager()
    items = list(mgr._menu_items())
    labels = [getattr(i, 'text', None) for i in items]
    check("TEST and PROD submenus present", "TEST" in labels and "PROD" in labels, str(labels))
    check("no Reboot entry", all(l != "Reboot" for l in labels if l), str(labels))
    check("Settings and Exit present", "Settings" in labels and "Exit" in labels)
    # The label carries the accelerator hint after a tab ("Cerca host...	Ctrl+Shift+Space"):
    # Windows right-aligns and greys whatever follows the tab.
    from ssh_connection.gui.hotkey_manager import HotkeyManager
    check("top-level Cerca host present",
          f"Cerca host...	{HotkeyManager.SHORTCUT_LABEL}" in labels, str(labels))

    test_menu = next(i for i in items if i.text == "TEST")
    sub_labels = [mi.text for mi in test_menu.submenu.items]
    check("Cerca... entry present", "Cerca..." in sub_labels, str(sub_labels))
    check("TEST host labels are plain text", "login_test" in sub_labels, str(sub_labels))

    plan = {idx: (key, children) for idx, key, children in mgr._bitmap_plan}
    test_idx = next(i for i, it in enumerate(items) if it.text == "TEST")
    prod_idx = next(i for i, it in enumerate(items) if it.text == "PROD")
    # "Cerca..." carries no bitmap (None); the host rows carry circle/square bitmaps.
    check("TEST plan uses circle bitmaps", all(k == "test_idle" for k in plan[test_idx][1] if k), str(plan))
    check("PROD plan uses square bitmaps", all(k == "prod_idle" for k in plan[prod_idx][1] if k), str(plan))

    # Simulate an active connection and rebuild the menu
    import psutil
    me = psutil.Process()
    tracker.register("login_test", me.pid)
    try:
        items = list(mgr._menu_items())
        test_menu = next(i for i in items if i.text.startswith("TEST"))
        plan = {idx: (key, children) for idx, key, children in mgr._bitmap_plan}
        test_idx = next(i for i, it in enumerate(items) if it.text.startswith("TEST"))
        check("active TEST host gets green bitmap", "test_active" in plan[test_idx][1], str(plan))
        check("TEST submenu title shows active count", test_menu.text == "TEST (1 attive)", test_menu.text)
        # Icon reflects active connections
        img = mgr.create_icon_image(1)
        check("icon image generated for active state", img.size == (64, 64))
    finally:
        tracker._connections.clear()


def test_menu_bitmaps():
    print("\n[4b] Native menu bitmaps on a real HMENU")
    import ctypes
    import ctypes.wintypes as wt
    from ssh_connection.gui.win32_menu_bitmaps import MenuBitmaps, _MENUITEMINFO, MIIM_BITMAP

    user32 = ctypes.windll.user32
    bm = MenuBitmaps()
    for key in ("test_idle", "test_active", "prod_idle", "prod_active"):
        check(f"bitmap created: {key}", bool(bm.get(key)))

    hmenu = user32.CreatePopupMenu()
    sub = user32.CreatePopupMenu()
    user32.AppendMenuW(sub, 0, 1, "host-a")
    user32.AppendMenuW(sub, 0, 2, "host-b")
    MF_POPUP = 0x10
    user32.AppendMenuW(hmenu, MF_POPUP, sub, "TEST")

    bm.apply_plan(hmenu, [(0, "test_idle", ["test_active", "test_idle"])])

    def item_bitmap(menu, pos):
        mii = _MENUITEMINFO()
        mii.cbSize = ctypes.sizeof(_MENUITEMINFO)
        mii.fMask = MIIM_BITMAP
        user32.GetMenuItemInfoW(menu, pos, True, ctypes.byref(mii))
        return mii.hbmpItem

    check("submenu title has bitmap", bool(item_bitmap(hmenu, 0)))
    check("child 0 has green bitmap", item_bitmap(sub, 0) == bm.get("test_active"))
    check("child 1 has idle bitmap", item_bitmap(sub, 1) == bm.get("test_idle"))
    user32.DestroyMenu(hmenu)


def test_console_injection():
    print("\n[5] Targeted console password injection (opens 2 console windows)")
    secret = "S3cret-Pa55word!"
    out_a = Path(tempfile.mktemp(suffix="_a.txt"))
    out_b = Path(tempfile.mktemp(suffix="_b.txt"))

    ps_script = (
        "$p = Read-Host 'Enter password'; "
        "Set-Content -Path '{out}' -Value $p -Encoding utf8"
    )
    # Console A: the target of the injection
    proc_a = subprocess.Popen(
        ['powershell', '-Command', ps_script.format(out=out_a)],
        creationflags=subprocess.CREATE_NEW_CONSOLE)
    time.sleep(1.0)
    # Console B: opened AFTER A, so it steals focus. It must receive nothing.
    proc_b = subprocess.Popen(
        ['powershell', '-Command', ps_script.format(out=out_b)],
        creationflags=subprocess.CREATE_NEW_CONSOLE)
    time.sleep(1.0)

    ok = ConsoleInjector.inject_password(proc_a.pid, secret, timeout=15)
    check("injector reports success", ok)

    proc_a.wait(timeout=10)
    typed = out_a.read_text(encoding="utf-8-sig").strip() if out_a.exists() else None
    check("password typed in TARGET console only", typed == secret, f"got: {typed!r}")
    check("focused (wrong) console got nothing", not out_b.exists())

    # cleanup console B
    proc_b.kill()
    for f in (out_a, out_b):
        if f.exists():
            f.unlink()


def main():
    # Point the parser (and everything built on it) at the example config
    SshConfigParser.get_config_path = staticmethod(lambda: EXAMPLE_CONFIG)
    real_parse = SshConfigParser.parse_ssh_config
    SshConfigParser.parse_ssh_config = staticmethod(
        lambda config_path=None: real_parse(config_path or EXAMPLE_CONFIG))

    test_parser_sections()
    test_jump_host_resolution(None)
    test_connection_tracker()
    test_dynamic_menu(None)
    test_menu_bitmaps()
    test_console_injection()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()

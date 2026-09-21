import ctypes
import logging
import os
import threading
from ctypes import wintypes
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from ..ssh.connection_tracker import tracker
from ..ssh.ssh_config_parser import SshConfigParser
from ..ssh.ssh_launcher import SshLauncher
from .hotkey_manager import HotkeyManager


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


# GetGUIThreadInfo flags signalling the thread is showing a (popup/system) menu
_GUI_INMENUMODE = 0x00000004
_GUI_SYSTEMMENUMODE = 0x00000008
_GUI_POPUPMENUMODE = 0x00000010
_GUI_MENU_FLAGS = _GUI_INMENUMODE | _GUI_SYSTEMMENUMODE | _GUI_POPUPMENUMODE


class TrayIconManager:
    """System tray icon manager for SSH connection management.

    The menu is regenerated on the fly: every refresh re-parses
    ~/.ssh/config, so new machines/ports are picked up without restarting
    the application (they apply to newly opened tunnels).

    Visual language of menu items (native colored bitmaps, not emoji):
      TEST  → circles:  cold-white (idle) / solid green (connected)
      PROD  → squares:  warm-amber (idle) / solid green (connected)
    Shape encodes the environment, fill/color encodes the live connection —
    so similarly named test/prod machines are hard to confuse.
    """

    REFRESH_SECONDS = 2.0

    def __init__(self):
        self.icon = None
        self.host_map = {}
        self._stop_event = threading.Event()
        self._last_state = None  # (config_mtime, frozenset(active_hosts))
        self._bitmaps = None
        self._bitmap_plan = []  # [(top_index, key, [child_keys])]
        self._hotkey = None
        self._search_popup = None
        try:
            from .win32_menu_bitmaps import MenuBitmaps
            self._bitmaps = MenuBitmaps()
        except Exception as e:
            import logging
            logging.warning(f"Menu bitmaps unavailable, using text-only menu: {e}")

    # ------------------------------------------------------------------
    # Icon image

    def create_icon_image(self, active_count: int = 0) -> Image.Image:
        """
        Tray icon: blue when idle, green when at least one connection is
        open, with a badge showing the number of active connections.
        """
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        if active_count > 0:
            fill, outline = (46, 160, 67), (17, 99, 41)  # green: connected
        else:
            fill, outline = (70, 130, 180), (25, 25, 112)  # steel blue: idle

        draw.ellipse([8, 8, 56, 56], fill=fill, outline=outline, width=2)
        draw.text((18, 25), "SSH", fill=(255, 255, 255))

        if active_count > 0:
            # Badge with active connection count (bottom-right)
            draw.ellipse([36, 36, 62, 62], fill=(255, 255, 255), outline=outline, width=2)
            draw.text((45, 41), str(min(active_count, 9)), fill=(17, 99, 41))

        return image

    # ------------------------------------------------------------------
    # Menu

    def _menu_items(self):
        """Generator invoked by pystray each time the menu is (re)built.

        Also records, position by position, which colored bitmap each item
        gets (`self._bitmap_plan`), applied to the native HMENU afterwards.
        """
        self.host_map = SshConfigParser.parse_ssh_config()
        active = tracker.active_hosts()

        def make_connect_callback(hostname):
            return lambda icon, item: self.connect_to_host(hostname)

        def make_search_callback(env_name):
            return lambda icon, item: self._open_search(env_name)

        plan = []
        items_out = []

        for section in ("TEST", "PROD"):
            hosts = self.host_map.get(section)
            if not hosts:
                continue
            env = section.lower()
            child_keys = []
            items = []

            # "Cerca..." entry at the top of the section submenu: opens the
            # host search dialog pre-filtered to this environment.
            items.append(pystray.MenuItem("Cerca...", make_search_callback(section)))
            child_keys.append(None)  # no status bitmap for the search entry

            for host in hosts:
                items.append(pystray.MenuItem(host, make_connect_callback(host)))
                child_keys.append(f"{env}_active" if host in active else f"{env}_idle")
            active_count = sum(1 for h in hosts if h in active)
            title = f"{section} ({active_count} attive)" if active_count else section
            plan.append((len(items_out), f"{env}_idle", child_keys))
            items_out.append(pystray.MenuItem(title, pystray.Menu(*items)))

        items_out.append(pystray.Menu.SEPARATOR)
        # A tab in a native menu string makes Windows right-align what
        # follows and draw it as the accelerator hint (grey, like "Ctrl+C"
        # in Explorer). pystray passes `text` straight to MENUITEMINFO
        # .dwTypeData, so this needs no extra Win32 call and shows up as
        # soon as the item is hovered/drawn.
        items_out.append(pystray.MenuItem(
            f"Cerca host...	{HotkeyManager.SHORTCUT_LABEL}",
            self._open_search_top))
        items_out.append(pystray.Menu.SEPARATOR)
        items_out.append(pystray.MenuItem("Init TEST", self.run_init_test))
        items_out.append(pystray.MenuItem("Init PROD", self.run_init_prod))
        items_out.append(pystray.Menu.SEPARATOR)
        items_out.append(pystray.MenuItem("Settings", self.open_settings))
        items_out.append(pystray.MenuItem("Exit", self.quit_application))

        self._bitmap_plan = plan
        yield from items_out

    def _decorate_menu(self) -> None:
        """Apply the colored status bitmaps to pystray's native HMENU."""
        if not (self._bitmaps and self.icon):
            return
        try:
            handle = getattr(self.icon, "_menu_handle", None)
            if handle:
                self._bitmaps.apply_plan(handle[0], self._bitmap_plan)
        except Exception as e:
            import logging
            logging.debug(f"Menu decoration failed: {e}")

    def create_menu(self) -> pystray.Menu:
        """Create the dynamic context menu for the tray icon"""
        return pystray.Menu(self._menu_items)

    # ------------------------------------------------------------------
    # Live refresh (config hot-reload + connection status)

    def _current_state(self):
        try:
            mtime = SshConfigParser.get_config_path().stat().st_mtime
        except OSError:
            mtime = None
        return (mtime, frozenset(tracker.active_hosts()))

    def _menu_is_open(self) -> bool:
        """True while the tray popup menu is displayed to the user.

        Rebuilding the native HMENU (update_menu / bitmap decoration) while
        the popup is on screen makes Windows redraw it under the cursor,
        which reads as flicker. We detect the open menu via the menu thread's
        GUI mode flags and defer the refresh until it closes.
        """
        try:
            hwnd = getattr(self.icon, "_menu_hwnd", None)
            if not hwnd:
                return False
            tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
            info = _GUITHREADINFO()
            info.cbSize = ctypes.sizeof(_GUITHREADINFO)
            if ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
                return bool(info.flags & _GUI_MENU_FLAGS)
        except Exception as e:
            logging.debug(f"Menu-open probe failed: {e}")
        return False

    def _refresh_loop(self) -> None:
        """Re-render menu and icon whenever the SSH config file changes on
        disk or a connection is opened/closed."""
        while not self._stop_event.wait(self.REFRESH_SECONDS):
            if self.icon is None:
                continue
            # Never touch the menu while the user has it open — it would
            # flicker. The change is picked up on the next tick after it closes
            # (state != _last_state keeps it pending).
            if self._menu_is_open():
                continue
            state = self._current_state()
            if state != self._last_state:
                self._last_state = state
                try:
                    active_count = len(state[1])
                    self.icon.icon = self.create_icon_image(active_count)
                    self.icon.title = (
                        f"SSH Connection Manager — {active_count} connessioni attive"
                        if active_count else "SSH Connection Manager"
                    )
                    self.icon.update_menu()
                except Exception as e:
                    logging.warning(f"Tray refresh failed: {e}")
            # Re-applied every tick: update_menu() rebuilds the native
            # menu handle, which drops previously set item bitmaps.
            self._decorate_menu()

    # ------------------------------------------------------------------
    # Actions

    def open_settings(self, icon: pystray.Icon, item) -> None:
        """Open the SSH config file in the default editor.

        Edits are picked up automatically (no restart needed): the menu
        refreshes as soon as the file is saved, and new tunnels/ports apply
        to the next connection you open.
        """
        try:
            ssh_config_path = SshConfigParser.get_config_path()
            if ssh_config_path.exists():
                os.startfile(ssh_config_path)
            else:
                print(f"SSH config file not found: {ssh_config_path}")
        except Exception as e:
            print(f"Error opening SSH config: {e}")

    def connect_to_host(self, host: str) -> None:
        """Connect to specified SSH host (non-blocking)"""
        print(f"Connecting to {host}...")
        SshLauncher.connect(host)

    def run_init_test(self, icon: pystray.Icon, item) -> None:
        """Init TEST: open login_test + its hidden DB hosts from a 2FA token."""
        self._run_init("TEST")

    def run_init_prod(self, icon: pystray.Icon, item) -> None:
        """Init PROD: open login_prod + its hidden DB hosts from a 2FA token."""
        self._run_init("PROD")

    def _run_init(self, env: str) -> None:
        logging.info(f"Init {env} menu item clicked")
        try:
            from ..ssh.init_orchestrator import InitOrchestrator
            InitOrchestrator.start(env, notify=self._notify)
        except Exception as e:
            logging.error(f"run_init {env} failed: {e}", exc_info=True)

    def _notify(self, title: str, message: str) -> None:
        """Show a tray balloon notification (best-effort)."""
        try:
            if self.icon:
                self.icon.notify(message, title)
        except Exception as e:
            logging.debug(f"Tray notification failed: {e}")

    # ------------------------------------------------------------------
    # Host search (dialog + global hotkey)

    def _search_hosts(self):
        """Host list for the popup: re-read at every open so config edits
        made while the app is running are picked up."""
        host_map = SshConfigParser.parse_ssh_config()
        return [(sec, host_map.get(sec, [])) for sec in ("TEST", "PROD")]

    def _on_search_selected(self, host: str) -> None:
        """Called on the popup thread when the user confirms a host.

        connect_to_host blocks (it spawns ssh and injects the console), so
        it must not run on the popup's Tk thread: offload it, otherwise the
        popup would stay frozen on screen until the terminal is up.
        """
        threading.Thread(target=self.connect_to_host, args=(host,),
                         daemon=True).start()

    def _ensure_search_popup(self):
        """Create (and pre-warm) the search popup on first use."""
        if self._search_popup is None:
            from .search_dialog import SearchPopup
            self._search_popup = SearchPopup(
                host_provider=self._search_hosts,
                on_select=self._on_search_selected)
            self._search_popup.start()
        return self._search_popup

    def _open_search(self, initial_env) -> None:
        """Show the host search popup.

        `initial_env` is 'TEST'/'PROD' to pre-filter (from a section
        submenu) or None to use the saved preference (from the global
        hotkey). The popup is pre-warmed at startup, so this call only
        posts a request to its thread and returns immediately — the tray
        menu never blocks and the window appears instantly.
        """
        try:
            self._ensure_search_popup().show(initial_env)
        except Exception as e:
            logging.error(f"Opening search popup failed: {e}", exc_info=True)

    def _open_search_top(self, icon=None, item=None) -> None:
        """Top-level 'Cerca host...' menu entry — same as the global hotkey,
        uses the saved environment preference (no forced env)."""
        self._open_search(None)

    def _on_global_hotkey(self) -> None:
        """Called on the hotkey thread when Ctrl+Shift+Space is pressed.

        Opens the search dialog using the saved environment preference.
        """
        logging.info("Global hotkey (Ctrl+Shift+Space) pressed")
        self._open_search(None)

    def quit_application(self, icon: pystray.Icon, item) -> None:
        """Quit the application"""
        print("Quitting application...")
        # Stop the global hotkey listener.
        if self._hotkey:
            self._hotkey.stop()
        if self._search_popup:
            self._search_popup.stop()
        # Hidden Init consoles have no window the user can close: kill them.
        try:
            from ..ssh.init_orchestrator import InitOrchestrator
            InitOrchestrator.shutdown()
        except Exception as e:
            logging.debug(f"Init shutdown failed: {e}")
        self._stop_event.set()
        icon.stop()

    # ------------------------------------------------------------------
    # Lifecycle

    def init_tray(self) -> None:
        """Initialize and start the system tray icon"""
        try:
            logging.info("Creating tray icon...")
            self.icon = pystray.Icon(
                "SSH Connection Manager",
                self.create_icon_image(),
                title="SSH Connection Manager",
                menu=self.create_menu(),
            )

            host_map = SshConfigParser.parse_ssh_config()
            test_count = len(host_map.get('TEST', []))
            prod_count = len(host_map.get('PROD', []))
            logging.info(f"Starting SSH Connection Manager with {test_count} TEST hosts and {prod_count} PROD hosts")
            print(f"Starting SSH Connection Manager with {test_count} TEST hosts and {prod_count} PROD hosts")

            refresher = threading.Thread(target=self._refresh_loop, daemon=True)
            refresher.start()

            # Build the search popup now (hidden): constructing the Tk
            # interpreter costs ~200 ms, and doing it here means the first
            # Ctrl+Shift+Space is as instant as every following one.
            try:
                self._ensure_search_popup()
            except Exception as e:
                logging.warning(f"Search popup pre-warm failed: {e}")

            # Global hotkey (Ctrl+Shift+Space) opens the host search popup.
            self._hotkey = HotkeyManager(on_triggered=self._on_global_hotkey)
            self._hotkey.start()

            # Run the tray icon (this blocks)
            self.icon.run()

        except Exception as e:
            error_msg = f"Error initializing tray icon: {e}"
            logging.error(error_msg, exc_info=True)
            print(error_msg)

            try:
                import sys
                if getattr(sys, 'frozen', False):
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0,
                        f"System tray initialization failed:\n\n{error_msg}",
                        "SSH Connection Manager - Tray Error",
                        0x10)  # MB_ICONERROR
            except Exception:
                pass
            raise

    def start_in_background(self) -> threading.Thread:
        """Start the tray icon in a background thread"""
        thread = threading.Thread(target=self.init_tray, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Stop the tray icon"""
        if self._hotkey:
            self._hotkey.stop()
        if self._search_popup:
            self._search_popup.stop()
        self._stop_event.set()
        if self.icon:
            self.icon.stop()


if __name__ == "__main__":
    manager = TrayIconManager()
    manager.init_tray()

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from .. import notifications
from ..__version__ import __version__
from ..config import autostart
from ..config.app_settings import AppSettings
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

    Top of the menu: the user's favorites (directly clickable, same status
    bitmaps) and a "Recenti" submenu with the last hosts connected to.
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
        self._settings_dialog = None
        self._file_search = None
        self._monitor = None
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

        env_of = {h: sec.lower() for sec in ("TEST", "PROD") for h in self.host_map.get(sec, [])}

        def status_key(host):
            env = env_of.get(host)
            return f"{env}_active" if host in active else f"{env}_idle"

        # Quick access: favorites directly at the top, then recents in a
        # submenu. Hosts no longer in ~/.ssh/config are skipped.
        favorites = [h for h in AppSettings.favorites() if h in env_of]
        if favorites:
            items_out.append(pystray.MenuItem("Preferiti", None, enabled=False))
            for host in favorites:
                plan.append((len(items_out), status_key(host), None))
                items_out.append(pystray.MenuItem(host, make_connect_callback(host)))
        recents = [h for h in AppSettings.recents() if h in env_of]
        if recents:
            plan.append((len(items_out), None, [status_key(h) for h in recents]))
            items_out.append(pystray.MenuItem("Recenti", pystray.Menu(
                *[pystray.MenuItem(h, make_connect_callback(h)) for h in recents])))
        if favorites or recents:
            items_out.append(pystray.Menu.SEPARATOR)

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
            f"Cerca host...	{self._hotkey_label()}",
            self._open_search_top))
        items_out.append(pystray.MenuItem("Cerca file...", self.open_file_search))
        items_out.append(pystray.Menu.SEPARATOR)
        items_out.append(pystray.MenuItem("Init TEST", self.run_init_test))
        items_out.append(pystray.MenuItem("Init PROD", self.run_init_prod))
        items_out.append(pystray.Menu.SEPARATOR)
        items_out.append(pystray.MenuItem("Impostazioni...", self.open_settings))
        items_out.append(pystray.MenuItem("Exit", self.quit_application))

        self._bitmap_plan = plan
        yield from items_out

    def _hotkey_label(self) -> str:
        if self._hotkey is not None:
            return self._hotkey.binding
        return AppSettings.get("hotkey")

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
        try:
            # Favorites/recents/hotkey live in the prefs file.
            prefs_mtime = AppSettings.path.stat().st_mtime
        except OSError:
            prefs_mtime = None
        return (mtime, prefs_mtime, frozenset(tracker.active_hosts()))

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
                    active_count = len(state[-1])
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

    def open_settings(self, icon=None, item=None) -> None:
        """Open the settings dialog (pre-warmed, instantaneous). Falls back
        to opening ~/.ssh/config in the default editor if the dialog cannot
        be created (the old behaviour of this entry)."""
        try:
            self._ensure_settings_dialog().show()
        except Exception as e:
            logging.error(f"Settings dialog failed, opening SSH config: {e}", exc_info=True)
            try:
                os.startfile(SshConfigParser.get_config_path())
            except Exception as e2:
                logging.error(f"Error opening SSH config: {e2}")

    def _ensure_settings_dialog(self):
        if self._settings_dialog is None:
            from .settings_dialog import SettingsDialog
            self._settings_dialog = SettingsDialog(
                ui_host=self._ensure_search_popup(),
                host_provider=self._search_hosts,
                on_save=self._apply_settings,
                version=__version__,
                ssh_config_path=SshConfigParser.get_config_path(),
                hotkey_suspend=self._suspend_hotkey)
            self._settings_dialog.prewarm()
        return self._settings_dialog

    def open_file_search(self, icon=None, item=None) -> None:
        """Tray entry "Cerca file..." (pystray actions take at most 2 args)."""
        self.open_file_search_on(None)

    def open_file_search_on(self, host) -> None:
        """Open the "Cerca file" window, on `host` or on the last one used."""
        try:
            self._ensure_file_search().show(host)
        except Exception as e:
            logging.error(f"File search window failed: {e}", exc_info=True)

    def _ensure_file_search(self):
        if self._file_search is None:
            from .file_search_dialog import FileSearchDialog
            self._file_search = FileSearchDialog(
                ui_host=self._ensure_search_popup(),
                host_provider=self._search_hosts)
        return self._file_search

    def _install_token_prompt(self) -> None:
        """Init TEST / PROD ask their 2FA token in a pre-built, themed Tk
        window (instant) instead of a PowerShell WinForms process."""
        from ..ssh.init_orchestrator import INIT_ENVS, InitOrchestrator
        from .token_dialog import TokenDialog
        dialog = TokenDialog(self._ensure_search_popup(), INIT_ENVS)
        dialog.prewarm()
        InitOrchestrator.token_prompt = dialog.ask

    def _suspend_hotkey(self, suspend: bool) -> None:
        """While the settings dialog captures a new combination the global
        hotkey is released, otherwise pressing it would open the search
        popup instead of reaching the capture field."""
        if self._hotkey is None:
            return
        if suspend:
            self._hotkey.stop()
        else:
            self._hotkey.start()

    def _apply_settings(self, values: dict):
        """Called on the Tk thread by the settings dialog. Returns None on
        success, or an error message to show in the dialog."""
        values = dict(values)
        binding = values.pop("hotkey")
        if self._hotkey is not None and not self._hotkey.rebind(binding):
            return (f"La scorciatoia {binding} è già usata da un altro programma: "
                    f"scegline un'altra (resta attiva {self._hotkey.binding}).")
        want_autostart = values.pop("autostart")
        # Enabling is always re-applied: it refreshes the path of the exe.
        if (want_autostart or autostart.is_enabled()) and not autostart.set_enabled(want_autostart):
            return "Impossibile modificare l'avvio automatico (registro di Windows)."
        AppSettings.update(hotkey=binding, **values)
        from . import theme
        theme.use(values.get("dark_theme", False))    # repaints the open windows
        logging.info(f"Settings saved: hotkey={binding}, autostart={want_autostart}, "
                     f"keepalive={values.get('keepalive_interval')}, "
                     f"tunnel_timeout={values.get('tunnel_timeout')}, "
                     f"notifications={values.get('notifications')}, "
                     f"dark_theme={values.get('dark_theme')}")
        return None

    def connect_to_host(self, host: str) -> None:
        """Connect to specified SSH host (non-blocking)"""
        print(f"Connecting to {host}...")
        AppSettings.add_recent(host)
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
            InitOrchestrator.start(
                env, notify=lambda title, msg: notifications.notify("init", title, msg))
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
                on_select=self._on_search_selected,
                on_file_search=self.open_file_search_on,
                status_provider=tracker.active_hosts)
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
        """Called on the hotkey thread when the search shortcut is pressed.

        Opens the search dialog using the saved environment preference.
        """
        logging.info(f"Global hotkey ({self._hotkey_label()}) pressed")
        self._open_search(None)

    def quit_application(self, icon: pystray.Icon, item) -> None:
        """Quit the application"""
        print("Quitting application...")
        # Stop the global hotkey listener.
        if self._hotkey:
            self._hotkey.stop()
        if self._monitor:
            self._monitor.stop()
        if self._search_popup:
            self._search_popup.stop()
        # Temporary copies of remote files opened from "Cerca file".
        try:
            from .file_search_dialog import purge_open_dir
            purge_open_dir()
        except Exception as e:
            logging.debug(f"Purging opened files failed: {e}")
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

            # Tray balloons for Init results and session problems
            # (filtered by the per-kind switches in the settings).
            notifications.set_sink(self._notify)
            try:
                autostart.refresh_path()
            except Exception as e:
                logging.debug(f"Autostart path refresh failed: {e}")
            try:
                from ..ssh.session_monitor import SessionMonitor
                self._monitor = SessionMonitor()
                self._monitor.start()
            except Exception as e:
                logging.warning(f"Session monitor unavailable: {e}")

            # Build the search popup now (hidden): constructing the Tk
            # interpreter costs ~200 ms, and doing it here means the first
            # Ctrl+Shift+Space is as instant as every following one.
            try:
                self._ensure_search_popup()
            except Exception as e:
                logging.warning(f"Search popup pre-warm failed: {e}")
            try:
                self._ensure_settings_dialog()   # built hidden on the same Tk thread
            except Exception as e:
                logging.warning(f"Settings dialog pre-warm failed: {e}")
            try:
                self._install_token_prompt()     # Init token window, same Tk thread
            except Exception as e:
                logging.warning(f"Token prompt pre-warm failed: {e}")

            # Global hotkey (configurable, default Ctrl+Shift+Space) opens
            # the host search popup.
            self._hotkey = HotkeyManager(on_triggered=self._on_global_hotkey,
                                         binding=AppSettings.get("hotkey"))
            if not self._hotkey.start():
                # The icon is not running yet: notify once it is.
                threading.Timer(3.0, self._notify, args=(
                    "Scorciatoia non disponibile",
                    f"{self._hotkey.binding} è già usata da un altro programma. "
                    f"Cambiala da Impostazioni.")).start()

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
        if self._monitor:
            self._monitor.stop()
        if self._search_popup:
            self._search_popup.stop()
        self._stop_event.set()
        if self.icon:
            self.icon.stop()


if __name__ == "__main__":
    manager = TrayIconManager()
    manager.init_tray()

"""
Settings dialog (tkinter), Windows 11 style: navigation pane on the left,
pages made of "setting cards" on the right, Salva / Annulla at the bottom.

Same pre-warm pattern as the search popup, and actually the SAME Tk
interpreter: the dialog is a Toplevel of the SearchPopup root, built and
driven exclusively through `SearchPopup.run_on_ui()`. Tcl is not
thread-safe and a second Tk() on another thread would be a second
interpreter with its own mainloop — sharing one thread avoids both issues.
The window is built hidden at startup; opening it only reloads the values
and deiconifies it, so it appears instantly. Closing only hides it.

Pages: Generale (hotkey, autostart, keepalive, tunnel timeout), Aspetto
(dark theme — previewed at once, reverted by Annulla — and the PROD console
scheme), Host (jump host addresses, add / edit / delete hosts of
~/.ssh/config through `SshConfigDocument`), Utente e password (fields
written into ~/.m2/settings.xml), Preferiti, Notifiche, Info.

Nothing is written before "Salva": host edits change an in-memory copy of
the config (written with a backup in config.bak), the credentials are
written only when changed, the preferences go to `on_save(values)` — it
runs on the Tk thread and returns None or an error message (e.g. hotkey
already taken), which is shown and keeps the dialog open.

Hotkey field: the user clicks it and presses the combination. While it has
the focus the global hotkey is suspended (`hotkey_suspend(True)`), otherwise
pressing the current combination would be swallowed by RegisterHotKey and
open the search popup instead of reaching the field.
"""

import logging
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..config import autostart
from ..config.app_settings import DEFAULTS, LIMITS, NOTIFICATION_KINDS, AppSettings
from ..config.config_loader import ConfigLoader
from ..ssh import console_themes
from ..ssh.ssh_config_editor import ENVS, ConfigError, HostEntry, SshConfigDocument
from . import theme
from .hotkey_manager import KEYS, MODIFIERS, parse_binding
from .search_dialog import _force_foreground
from .theme import px

LOG_FILE = Path.home() / "ssh_connection_debug.log"

PAGES = (("general", "Generale", "settings"), ("appearance", "Aspetto", "color"),
         ("hosts", "Host", "host"), ("account", "Utente e password", "account"),
         ("favorites", "Preferiti", "star"), ("notifications", "Notifiche", "bell"),
         ("info", "Info", "info"))

# Tk keysym of a modifier key -> our modifier name
_MOD_KEYSYMS = {
    "Control_L": "Ctrl", "Control_R": "Ctrl",
    "Shift_L": "Shift", "Shift_R": "Shift",
    "Alt_L": "Alt", "Alt_R": "Alt",
    "Win_L": "Win", "Win_R": "Win",
}

_HOTKEY_HINT = "Clicca il campo e premi la combinazione (es. Ctrl+Shift+Space)."

_VK_TO_KEY = {vk: name for name, vk in KEYS.items()}


def binding_from_keys(mods: Set[str], keysym: str, keycode: Optional[int] = None) -> Optional[str]:
    """Pressed modifiers + final key -> 'Ctrl+Shift+Space', or None if the
    key is not supported or the combination is not valid.

    On Windows Tk's `keycode` is the virtual-key code: it is preferred over
    the keysym, which depends on the layout and on Shift (Shift+1 is
    'exclam' on an Italian keyboard, but VK 0x31 either way)."""
    if keycode in _VK_TO_KEY:
        key = _VK_TO_KEY[keycode]
    elif keysym == "space":
        key = "Space"
    elif len(keysym) == 1 and keysym.isalnum():
        key = keysym.upper()
    elif keysym.upper() in KEYS:          # F1..F12
        key = keysym.upper()
    else:
        return None
    binding = "+".join([n for n, _ in MODIFIERS if n in mods] + [key])
    return binding if parse_binding(binding) else None


class SettingsDialog:
    def __init__(self, ui_host,
                 host_provider: Callable[[], Sequence[Tuple[str, List[str]]]],
                 on_save: Callable[[Dict], Optional[str]],
                 version: str,
                 ssh_config_path: Path,
                 hotkey_suspend: Optional[Callable[[bool], None]] = None):
        self._ui = ui_host
        self._host_provider = host_provider
        self._on_save = on_save
        self._version = version
        self._ssh_config_path = ssh_config_path
        self._hotkey_suspend = hotkey_suspend
        self._top = None
        self._envs: List[Tuple[str, List[str]]] = []
        self._env_of: Dict[str, str] = {}
        self._shown_hosts: List[str] = []   # rows of the all-hosts list
        self._favorites: List[str] = []     # working copy, saved on "Salva"
        self._pressed: Set[str] = set()
        self._binding = DEFAULTS["hotkey"]
        self._capturing = False
        self._doc: Optional[SshConfigDocument] = None   # working copy of ~/.ssh/config
        self._creds_loaded: Tuple[str, str] = ("", "")
        self._saved = False
        self._pages: Dict[str, object] = {}

    # ------------------------------------------------------------------
    # Public API (any thread)

    def prewarm(self) -> None:
        self._ui.run_on_ui(self._build)

    def show(self, page: Optional[str] = None) -> None:
        self._ui.run_on_ui(lambda: self._do_show(page))

    # ------------------------------------------------------------------
    # Tk thread: build

    def _build(self) -> None:
        if self._top is not None:
            return
        import tkinter as tk

        from . import widgets as w

        top = tk.Toplevel(self._ui.root)
        self._top = top
        top.title("SSH Connection Manager - Impostazioni")
        theme.toplevel(top)
        top.minsize(px(860), px(600))
        top.withdraw()
        top.protocol("WM_DELETE_WINDOW", self._hide)
        top.bind("<Escape>", lambda e: self._hide())

        # --- navigation pane ----------------------------------------------
        nav = theme.style(tk.Frame(top, width=px(230), pady=px(16)), "window", "nav")
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        w.label(nav, "SSH Connection Manager", "hint", "nav", size=9,
                anchor="w").pack(fill="x", padx=px(18))
        w.label(nav, "Impostazioni", "title", "nav", size=16, weight="semibold",
                anchor="w").pack(fill="x", padx=px(18), pady=(0, px(14)))
        self._sidebar = w.Sidebar(nav, PAGES, self._show_page, width=230)
        self._sidebar.pack(fill="both", expand=True)

        # --- content + footer ---------------------------------------------
        right = theme.style(tk.Frame(top), "window")
        right.pack(side="left", fill="both", expand=True)
        foot = theme.style(tk.Frame(right, padx=px(24), pady=px(14)), "window")
        foot.pack(side="bottom", fill="x")
        w.Divider(right, "bg").pack(side="bottom", fill="x")
        w.Button(foot, "Salva", self._save, "primary", width=10).pack(side="right")
        w.Button(foot, "Annulla", self._hide, width=10).pack(side="right", padx=(0, px(8)))
        self._var_error = tk.StringVar()
        self._error_label = w.label(foot, "", "error", size=9, anchor="w", justify="left",
                                    wraplength=px(420), textvariable=self._var_error)
        self._error_label.pack(side="left", fill="x", expand=True)
        self._content = theme.style(tk.Frame(right, padx=px(28), pady=px(20)), "window")
        self._content.pack(fill="both", expand=True)

        for key, title, _ in PAGES:
            page = theme.style(tk.Frame(self._content), "window")
            w.page_title(page, title).pack(fill="x", pady=(0, px(14)))
            getattr(self, f"_page_{key}")(page, w, tk)
            self._pages[key] = page
        self._sidebar.select("general")
        top.update_idletasks()

    def _show_page(self, key: str) -> None:
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()

    def _page_general(self, page, w, tk) -> None:
        card = w.Card(page)
        card.pack(fill="x")
        self._var_hotkey = tk.StringVar()
        self._var_hotkey_hint = tk.StringVar(value=_HOTKEY_HINT)

        def hotkey_control(parent):
            box = theme.style(tk.Frame(parent), "window", "card")
            entry = theme.style(tk.Entry(box, textvariable=self._var_hotkey, font=theme.font(11),
                                         justify="center", cursor="hand2", width=18,
                                         state="readonly"), "entry")
            entry.pack(side="left", ipady=px(4))
            w.Button(box, "Predefinita", self._reset_hotkey, surface="card").pack(
                side="left", padx=(px(8), 0))
            self._hotkey_entry = entry
            return box
        w.setting_row(card, "Scorciatoia ricerca host", control=hotkey_control, first=True,
                      textvariable=self._var_hotkey_hint)
        entry = self._hotkey_entry
        entry.bind("<FocusIn>", self._capture_start)
        entry.bind("<FocusOut>", self._capture_end)
        entry.bind("<KeyPress>", self._on_key_press)
        entry.bind("<KeyRelease>", self._on_key_release)

        self._var_autostart = tk.BooleanVar()
        self._var_autostart_hint = tk.StringVar()
        w.setting_row(card, "Avvia automaticamente con Windows",
                      control=lambda p: w.Toggle(p, self._var_autostart),
                      textvariable=self._var_autostart_hint)

        w.section_title(page, "Connessioni").pack(fill="x", pady=(px(18), px(6)))
        card = w.Card(page)
        card.pack(fill="x")
        self._var_keepalive = tk.StringVar()
        self._var_tunnel = tk.StringVar()

        def spin(var, key, step):
            lo, hi = LIMITS[key]
            return lambda p: theme.style(tk.Spinbox(p, from_=lo, to=hi, increment=step, width=7,
                                                    textvariable=var, font=theme.font(10),
                                                    justify="right"), "spinbox")
        w.setting_row(card, "Intervallo keepalive (secondi)",
                      "Ogni quanto i jump host eseguono 'date' per tenere viva la sessione e i "
                      "tunnel.", control=spin(self._var_keepalive, "keepalive_interval", 30),
                      first=True)
        w.setting_row(card, "Timeout attesa tunnel (secondi)",
                      "Quanto aspettare il tunnel del jump host, compreso il tempo per il token.",
                      control=spin(self._var_tunnel, "tunnel_timeout", 10))
        w.label(page, "Keepalive e timeout valgono dalla prossima connessione aperta.", "hint",
                size=9, anchor="w").pack(fill="x", pady=(px(6), 0))

    def _page_appearance(self, page, w, tk) -> None:
        card = w.Card(page)
        card.pack(fill="x")
        self._var_dark = tk.BooleanVar()
        w.setting_row(card, "Tema scuro",
                      "One Half Dark, come Windows Terminal. Vale per Cerca host, Cerca file e "
                      "Impostazioni; l'anteprima è immediata, Annulla la toglie.",
                      control=lambda p: w.Toggle(p, self._var_dark,
                                                 command=lambda: theme.use(self._var_dark.get())),
                      first=True)
        self._var_prod_theme = tk.StringVar()

        def combo(parent):
            from tkinter import ttk
            self._prod_theme_combo = theme.style(
                ttk.Combobox(parent, textvariable=self._var_prod_theme, state="readonly",
                             width=24, font=theme.font(10)), "combo")
            return self._prod_theme_combo
        w.setting_row(card, "Tema delle console PROD",
                      "Schema colori dei terminali PROD (anche quelli definiti in Windows "
                      "Terminal). Tutte le console hanno il titolo [TEST]/[PROD] host.",
                      control=combo)

    def _page_hosts(self, page, w, tk) -> None:
        from tkinter import ttk
        w.section_title(page, "Jump host").pack(fill="x", pady=(0, px(6)))
        card = w.Card(page)
        card.pack(fill="x")
        self._var_jump: Dict[str, tk.StringVar] = {}
        self._jump_rows: Dict[str, tuple] = {}
        for i, env in enumerate(ENVS):
            var = tk.StringVar()
            self._var_jump[env] = var
            row, entry, desc = w.setting_row(
                card, env, f"Indirizzo del jump host {env}.", first=(i == 0),
                control=lambda p, v=var: theme.style(
                    tk.Entry(p, textvariable=v, font=theme.font(10), width=22), "entry"))
            entry.grid_configure(ipady=px(4))
            self._jump_rows[env] = (row.grid_slaves(row=0, column=0)[0], entry, desc)

        head = theme.style(tk.Frame(page), "window")
        head.pack(fill="x", pady=(px(18), px(6)))
        w.section_title(head, "Host").pack(side="left")
        bar = theme.style(tk.Frame(page), "window")
        bar.pack(fill="x", pady=(0, px(6)))
        self._var_host_search = tk.StringVar()
        w.SearchEntry(bar, self._var_host_search, "Cerca host...").pack(
            side="left", fill="x", expand=True)
        self._var_host_search.trace_add("write", lambda *_: self._refresh_host_table())
        w.Button(bar, "Elimina", self._delete_host).pack(side="right")
        w.Button(bar, "Modifica...", self._edit_host).pack(side="right", padx=(px(8), px(8)))
        w.Button(bar, "Aggiungi host...", self._add_host, "primary").pack(side="right",
                                                                          padx=(px(8), 0))

        self._var_hosts_hint = tk.StringVar()
        w.label(page, "", "hint", size=9, anchor="w", justify="left", wraplength=px(620),
                textvariable=self._var_hosts_hint).pack(side="bottom", fill="x", pady=(px(6), 0))
        tframe = theme.style(tk.Frame(page), "field_frame")
        tframe.pack(fill="both", expand=True)
        cols = (("env", "Ambiente", 80), ("dest", "Server", 170), ("port", "Porta locale", 90),
                ("desc", "Descrizione", 220))
        tree = theme.style(ttk.Treeview(tframe, columns=[c for c, _, _ in cols],
                                        show="tree headings", selectmode="browse", height=8),
                           "tree")
        tree.heading("#0", text="Nome", anchor="w")
        tree.column("#0", width=px(150), stretch=False)
        for c, title, width in cols:
            tree.heading(c, text=title, anchor="w")
            tree.column(c, width=px(width), anchor="w", stretch=(c == "desc"))
        tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb = ttk.Scrollbar(tframe, orient="vertical", command=tree.yview)
        sb.pack(side="right", fill="y", pady=1)
        tree.configure(yscrollcommand=sb.set)
        tree.bind("<Double-Button-1>", lambda e: self._edit_host())
        tree.bind("<Return>", lambda e: self._edit_host())
        tree.bind("<Delete>", lambda e: self._delete_host())
        self._host_tree = tree

    def _page_account(self, page, w, tk) -> None:
        card = w.Card(page)
        card.pack(fill="x")
        self._var_user = tk.StringVar()
        self._var_password = tk.StringVar()
        row, entry, _ = w.setting_row(
            card, "Nome utente",
            "Con o senza dominio (es. DOMINIO\\nome.cognome): il dominio viene tolto al login.",
            control=lambda p: theme.style(tk.Entry(p, textvariable=self._var_user,
                                                   font=theme.font(10), width=28), "entry"),
            first=True)
        entry.grid_configure(ipady=px(4))

        def password_control(parent):
            box = theme.style(tk.Frame(parent), "window", "card")
            self._password_entry = theme.style(
                tk.Entry(box, textvariable=self._var_password, font=theme.font(10), width=24,
                         show="•"), "entry")
            self._password_entry.pack(side="left", ipady=px(4))
            w.IconButton(box, "eye", self._toggle_password, "card",
                         tooltip="Mostra / nascondi").pack(side="left", padx=(px(4), 0))
            return box
        w.setting_row(card, "Password",
                      "Usata per il login su tutti gli host; viene digitata solo nel terminale "
                      "della connessione.", control=password_control)
        self._var_creds_path = tk.StringVar()
        w.setting_row(card, "File", control=lambda p: w.Button(
            p, "Apri file", self._open_credentials, surface="card"),
            textvariable=self._var_creds_path)
        self._var_info = tk.StringVar()
        w.label(page, "", "hint", size=9, anchor="w", justify="left", wraplength=px(620),
                textvariable=self._var_info).pack(fill="x", pady=(px(8), 0))

    def _page_favorites(self, page, w, tk) -> None:
        self._var_fav_filter = tk.StringVar()
        bar = theme.style(tk.Frame(page), "window")
        bar.pack(fill="x")
        w.SearchEntry(bar, self._var_fav_filter, "Filtra host...").pack(
            side="left", fill="x", expand=True)
        w.Button(bar, "Aggiungi ai preferiti", self._add_favorite, "primary").pack(
            side="left", padx=(px(8), 0))
        self._var_fav_filter.trace_add("write", lambda *_: self._refresh_hosts())
        self._hosts_list = w.ListView(page, height=6, detail_width=60)
        self._hosts_list.pack(fill="both", expand=True, pady=(px(6), 0))
        self._hosts_list.bind("<Double-Button-1>", lambda e: self._add_favorite())

        w.section_title(page, "Preferiti — in cima al menu e alla ricerca, in quest'ordine").pack(
            fill="x", pady=(px(16), px(6)))
        favrow = theme.style(tk.Frame(page), "window")
        favrow.pack(fill="both", expand=True)
        self._fav_list = w.ListView(favrow, height=5, detail_width=150)
        self._fav_list.pack(side="left", fill="both", expand=True)
        self._fav_list.bind("<Double-Button-1>", lambda e: self._remove_favorite())
        side = theme.style(tk.Frame(favrow), "window")
        side.pack(side="left", fill="y", padx=(px(8), 0))
        for text, cmd in (("Su", lambda: self._move_favorite(-1)),
                          ("Giù", lambda: self._move_favorite(1)),
                          ("Rimuovi", self._remove_favorite)):
            w.Button(side, text, cmd, width=8).pack(fill="x", pady=(0, px(6)))

        rec = theme.style(tk.Frame(page), "window")
        rec.pack(fill="x", pady=(px(10), 0))
        self._var_recents = tk.StringVar()
        w.label(rec, "", "hint", size=9, textvariable=self._var_recents).pack(side="left")
        w.Button(rec, "Svuota recenti", self._clear_recents).pack(side="right")

    def _page_notifications(self, page, w, tk) -> None:
        w.label(page, "Notifiche di Windows (balloon della tray) da mostrare:", "hint", size=9,
                anchor="w").pack(fill="x", pady=(0, px(8)))
        card = w.Card(page)
        card.pack(fill="x")
        self._notif_vars = {}
        for i, (kind, text) in enumerate(NOTIFICATION_KINDS.items()):
            v = tk.BooleanVar()
            self._notif_vars[kind] = v
            w.setting_row(card, text, control=lambda p, v=v: w.Toggle(p, v), first=(i == 0))

    def _page_info(self, page, w, tk) -> None:
        card = w.Card(page)
        card.pack(fill="x")
        w.setting_row(card, "SSH Connection Manager", f"Versione {self._version}", first=True)
        for title, path in (("Config SSH", self._ssh_config_path),
                            ("Utente e password", ConfigLoader.maven_settings_path()),
                            ("Preferenze", AppSettings.path),
                            ("Log", LOG_FILE)):
            opener = (self._open_credentials if title == "Utente e password"
                      else lambda p=path: self._open(p))
            w.setting_row(card, title, str(path),
                          control=lambda parent, o=opener: w.Button(parent, "Apri", o,
                                                                    surface="card", width=7))
        w.label(page, "Modificare i file a mano resta possibile: le pagine Host e Utente e "
                      "password lo fanno per te senza toccare il resto del file.", "hint", size=9,
                anchor="w", justify="left", wraplength=px(620)).pack(fill="x", pady=(px(8), 0))

    # ------------------------------------------------------------------
    # Tk thread: values

    def _load_values(self) -> None:
        cfg = AppSettings.all()
        self._saved = False
        self._set_binding(cfg["hotkey"])
        self._var_hotkey_hint.set(_HOTKEY_HINT)
        self._var_keepalive.set(str(cfg["keepalive_interval"]))
        self._var_tunnel.set(str(cfg["tunnel_timeout"]))
        self._var_autostart.set(autostart.is_enabled())
        self._var_autostart_hint.set(autostart.describe())
        names = console_themes.scheme_names()
        scheme = cfg["prod_console_theme"]
        if scheme not in names:
            names.append(scheme)      # keep a saved scheme even if WT dropped it
        self._prod_theme_combo["values"] = names
        self._var_prod_theme.set(scheme)
        self._var_dark.set(cfg["dark_theme"])
        for kind, v in self._notif_vars.items():
            v.set(cfg["notifications"].get(kind, True))

        try:
            self._envs = [(n, list(h)) for n, h in (self._host_provider() or [])]
        except Exception as e:
            logging.error(f"Host provider failed: {e}", exc_info=True)
            self._envs = []
        self._env_of = {h: env for env, hosts in self._envs for h in hosts}
        # Favorites no longer in the config are kept (and shown as such):
        # dropping them silently would lose them after a temporary config edit.
        self._favorites = list(cfg["favorites"])
        self._var_fav_filter.set("")
        self._refresh_hosts()
        self._refresh_favorites()
        self._var_recents.set(f"Recenti: {len(cfg['recents'])}")
        self._var_error.set("")

        self._load_config_doc()
        creds = ConfigLoader.maven_settings_path()
        self._creds_loaded = ConfigLoader.read_maven_credentials_raw()
        self._var_user.set(self._creds_loaded[0])
        self._var_password.set(self._creds_loaded[1])
        self._password_entry.configure(show="•")
        self._var_creds_path.set(str(creds))
        self._var_info.set("" if creds.exists() else
                           f"{creds} non esiste ancora: verrà creato al salvataggio.")

    def _load_config_doc(self) -> None:
        try:
            self._doc = SshConfigDocument.load(self._ssh_config_path)
        except ConfigError as e:
            self._doc = None
            self._var_hosts_hint.set(str(e))
        for env in ENVS:
            title, entry, desc = self._jump_rows[env]
            jump = self._doc.jump_host(env) if self._doc else None
            entry_host = self._doc.host(jump) if jump else None
            title.configure(text=jump or env)
            self._var_jump[env].set(entry_host.hostname if entry_host else "")
            entry.configure(state="normal" if entry_host else "disabled")
            if desc is not None:
                desc.configure(text=f"Indirizzo del jump host {env}: gli host {env} passano dai "
                                    f"suoi tunnel." if entry_host else
                               f"Nessun host login_... nella sezione {env} del config.")
        self._refresh_host_table()

    def _do_show(self, page: Optional[str] = None) -> None:
        self._build()
        self._load_values()
        if page in self._pages:
            self._sidebar.select(page)
        top = self._top
        if not top.winfo_viewable():
            w, h = px(960), px(680)
            x = (top.winfo_screenwidth() - w) // 2
            y = max(40, (top.winfo_screenheight() - h) // 3)
            top.geometry(f"{w}x{h}+{x}+{y}")
        top.deiconify()
        top.lift()
        top.attributes("-topmost", True)
        top.update_idletasks()
        try:
            _force_foreground(int(top.wm_frame(), 16))
        except Exception:
            pass
        top.focus_force()

    def _hide(self) -> str:
        self._capture_end()
        if not self._saved and theme.is_dark() != AppSettings.get("dark_theme"):
            theme.use(AppSettings.get("dark_theme"))       # drop the preview
        if self._top is not None:
            self._top.withdraw()
        return "break"

    # ------------------------------------------------------------------
    # Hotkey capture

    def _set_binding(self, binding: str) -> None:
        self._binding = binding
        self._var_hotkey.set(binding)

    def _reset_hotkey(self) -> None:
        self._set_binding(DEFAULTS["hotkey"])
        self._var_hotkey_hint.set("Ripristinata la scorciatoia predefinita.")
        self._top.focus_set()

    def _capture_start(self, _event=None) -> None:
        if self._capturing:
            return
        self._capturing = True
        self._pressed.clear()
        if self._hotkey_suspend:
            self._hotkey_suspend(True)
        self._var_hotkey_hint.set("Premi la combinazione... (Esc annulla)")

    def _capture_end(self, _event=None) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self._pressed.clear()
        self._var_hotkey.set(self._binding)
        if self._hotkey_suspend:
            self._hotkey_suspend(False)
        self._var_hotkey_hint.set(_HOTKEY_HINT)

    def _on_key_press(self, event) -> str:
        keysym = event.keysym
        if keysym in _MOD_KEYSYMS:
            self._pressed.add(_MOD_KEYSYMS[keysym])
            # Live preview of the modifiers being held.
            self._var_hotkey.set("+".join(n for n, _ in MODIFIERS if n in self._pressed) + "+...")
            return "break"
        if keysym == "Escape" and not self._pressed:
            self._top.focus_set()           # cancel: FocusOut restores the value
            return "break"
        if keysym == "Tab" and not self._pressed:
            return None                     # let Tab move the focus
        binding = binding_from_keys(self._pressed, keysym, event.keycode)
        if binding is None:
            self._var_hotkey_hint.set(
                "Combinazione non valida: serve almeno un modificatore (Ctrl, Shift, Alt, Win) "
                "e un tasto tra lettere, numeri, Spazio, F1-F12.")
            self._var_hotkey.set(self._binding)
            return "break"
        self._set_binding(binding)
        self._var_hotkey_hint.set(f"Nuova scorciatoia: {binding}  (Salva per applicarla)")
        self._top.focus_set()               # done: leave the field
        return "break"

    def _on_key_release(self, event) -> str:
        if event.keysym in _MOD_KEYSYMS:
            self._pressed.discard(_MOD_KEYSYMS[event.keysym])
            if self._capturing and not self._pressed:
                self._var_hotkey.set(self._binding)
        return "break"

    # ------------------------------------------------------------------
    # Hosts page

    def _host_entries(self) -> List[HostEntry]:
        return [h for h in (self._doc.hosts() if self._doc else []) if not h.is_jump]

    def _refresh_host_table(self, select: Optional[str] = None) -> None:
        tree = self._host_tree
        tree.delete(*tree.get_children())
        needle = self._var_host_search.get().strip().lower()
        seen = set()
        for h in self._host_entries():
            if needle and needle not in h.name.lower() and needle not in h.description.lower():
                continue
            iid = h.name if h.name not in seen else f"{h.name}#{len(seen)}"
            seen.add(iid)
            if h.kind == "tunnel":
                dest = h.dest if h.dest_port in (None, 22) else f"{h.dest}:{h.dest_port}"
                port = str(h.port)
            else:
                dest = h.hostname + (f":{h.port}" if h.port else "")
                port = "diretto"
            desc = h.description + ("  (definito due volte!)" if h.duplicate else "")
            tree.insert("", "end", iid=iid, text=h.name, values=(h.env, dest, port, desc))
        n = len(tree.get_children())
        pending = "  Modifiche non ancora salvate: premi Salva." if (self._doc and
                                                                   self._doc.dirty) else ""
        self._var_hosts_hint.set(
            f"{n} host. Si salva con Salva: prima di scrivere ~/.ssh/config ne viene fatta una "
            f"copia (config.bak); commenti e altre righe restano come sono.{pending}")
        if select and tree.exists(select):
            tree.selection_set(select)
            tree.focus(select)
            tree.see(select)

    def _selected_host(self) -> Optional[HostEntry]:
        sel = self._host_tree.selection()
        if not sel or self._doc is None:
            return None
        name = sel[0].split("#")[0]
        return next((h for h in self._host_entries() if h.name == name), None)

    def _add_host(self) -> None:
        if self._doc is None:
            return
        HostForm(self, None).open()

    def _edit_host(self) -> None:
        entry = self._selected_host()
        if entry is None:
            self._var_error.set("Seleziona un host da modificare.")
            return
        if entry.duplicate or not entry.editable:
            self._var_error.set(f"{entry.name} è definito più volte o insieme ad altri nomi: "
                                f"correggilo a mano nel config (Info → Config SSH → Apri).")
            return
        HostForm(self, entry).open()

    def _delete_host(self) -> None:
        from tkinter import messagebox
        entry = self._selected_host()
        if entry is None:
            self._var_error.set("Seleziona un host da eliminare.")
            return
        tunnel = " e il tunnel sul jump host" if entry.kind == "tunnel" else ""
        if not messagebox.askyesno(
                "Elimina host", f"Eliminare {entry.name}?\n\nVengono tolti il blocco Host, "
                f"la sua descrizione{tunnel}. Il file si aggiorna quando premi Salva.",
                parent=self._top):
            return
        try:
            self._doc.delete_host(entry.name)
        except ConfigError as e:
            self._var_error.set(str(e))
            return
        self._var_error.set("")
        if entry.name in self._favorites:
            self._favorites.remove(entry.name)
            self._refresh_favorites()
        self._refresh_host_table()

    # ------------------------------------------------------------------
    # Account page

    def _toggle_password(self) -> None:
        e = self._password_entry
        e.configure(show="" if e.cget("show") else "•")

    # ------------------------------------------------------------------
    # Favorites

    def _refresh_hosts(self) -> None:
        needle = self._var_fav_filter.get().strip().lower()
        lb = self._hosts_list
        lb.delete(0, "end")
        self._shown_hosts = []
        for env, hosts in self._envs:
            for h in hosts:
                if needle and needle not in h.lower():
                    continue
                self._shown_hosts.append(h)
                mark = "★  " if h in self._favorites else ""
                lb.insert("end", f"  {mark}{h}", detail=env)
        if self._shown_hosts:
            lb.selection_set(0)

    def _refresh_favorites(self, select: Optional[int] = None) -> None:
        lb = self._fav_list
        lb.delete(0, "end")
        for h in self._favorites:
            env = self._env_of.get(h)
            lb.insert("end", f"  {h}", detail=env or "non più nel config")
        if not self._favorites:
            lb.insert("end", "  Nessun preferito: scegli un host sopra e premi Aggiungi",
                      tags=("muted",))
        elif select is not None:
            select = max(0, min(select, len(self._favorites) - 1))
            lb.selection_set(select)
            lb.see(select)

    def _add_favorite(self) -> None:
        sel = self._hosts_list.curselection()
        if not sel:
            return
        host = self._shown_hosts[sel[0]]
        if host not in self._favorites:
            self._favorites.append(host)
        self._refresh_favorites(select=self._favorites.index(host))
        self._refresh_hosts_keep(sel[0])

    def _refresh_hosts_keep(self, idx: int) -> None:
        self._refresh_hosts()
        self._hosts_list.selection_clear(0, "end")
        if idx < len(self._shown_hosts):
            self._hosts_list.selection_set(idx)
            self._hosts_list.see(idx)

    def _selected_favorite(self) -> Optional[int]:
        sel = self._fav_list.curselection()
        if not sel or not self._favorites:
            return None
        return sel[0]

    def _remove_favorite(self) -> None:
        idx = self._selected_favorite()
        if idx is None:
            return
        del self._favorites[idx]
        self._refresh_favorites(select=idx)
        self._refresh_hosts_keep(self._hosts_list.curselection()[0]
                                 if self._hosts_list.curselection() else 0)

    def _move_favorite(self, delta: int) -> None:
        idx = self._selected_favorite()
        if idx is None:
            return
        new = idx + delta
        if not 0 <= new < len(self._favorites):
            return
        favs = self._favorites
        favs[idx], favs[new] = favs[new], favs[idx]
        self._refresh_favorites(select=new)

    def _clear_recents(self) -> None:
        AppSettings.update(recents=[])
        self._var_recents.set("Recenti: 0")

    def _open_credentials(self) -> None:
        """Open ~/.m2/settings.xml (username/password of every SSH login),
        creating it from a template first if it does not exist."""
        path = ConfigLoader.maven_settings_path()
        try:
            created = ConfigLoader.ensure_maven_settings()
        except OSError as e:
            logging.error(f"Cannot create {path}: {e}")
            self._var_info.set(f"Impossibile creare {path}: {e}")
            return
        if created:
            self._var_info.set(
                f"Creato {path}: sostituisci INSERISCI_UTENTE e INSERISCI_PASSWORD e salva "
                f"(oppure compila i campi qui sopra). Vale dalla prossima connessione.")
        else:
            self._var_info.set("Le credenziali sono lette dal primo <server> del file. "
                               "Le modifiche valgono dalla prossima connessione.")
        # Notepad, not the default .xml handler (often a browser, read-only).
        try:
            import subprocess
            subprocess.Popen(["notepad.exe", str(path)], stdin=subprocess.DEVNULL)
        except OSError:
            self._open(path)

    @staticmethod
    def _open(path: Path) -> None:
        try:
            os.startfile(path)
        except Exception as e:
            logging.error(f"Cannot open {path}: {e}")

    # ------------------------------------------------------------------
    # Save

    def collect(self) -> Tuple[Optional[Dict], Optional[str]]:
        """Validated preference values from the widgets, or (None, error message)."""
        if parse_binding(self._binding) is None:
            return None, "Scorciatoia non valida: serve almeno un modificatore (Ctrl, Shift, Alt, Win)."
        values = {"hotkey": self._binding}
        for key, var, label in (("keepalive_interval", self._var_keepalive, "Intervallo keepalive"),
                                ("tunnel_timeout", self._var_tunnel, "Timeout tunnel")):
            lo, hi = LIMITS[key]
            try:
                n = int(var.get())
            except ValueError:
                return None, f"{label}: inserire un numero intero."
            if not lo <= n <= hi:
                return None, f"{label}: valore ammesso tra {lo} e {hi} secondi."
            values[key] = n
        values["notifications"] = {k: v.get() for k, v in self._notif_vars.items()}
        values["favorites"] = list(self._favorites)
        values["autostart"] = self._var_autostart.get()
        values["prod_console_theme"] = self._var_prod_theme.get() or console_themes.NO_THEME
        values["dark_theme"] = self._var_dark.get()
        return values, None

    def _apply_jump_hosts(self) -> Optional[str]:
        """Jump host addresses typed in the Host page -> working copy."""
        if self._doc is None:
            return None
        for env in ENVS:
            jump = self._doc.jump_host(env)
            entry = self._doc.host(jump) if jump else None
            new = self._var_jump[env].get().strip()
            if entry is None or new == entry.hostname:
                continue
            try:
                self._doc.set_jump_hostname(env, new)
            except ConfigError as e:
                return f"Jump host {jump}: {e}"
        return None

    def _write_files(self) -> Optional[str]:
        """~/.ssh/config (if hosts changed) and settings.xml (if the
        credentials changed). Returns an error message or None."""
        if self._doc is not None and self._doc.dirty:
            try:
                backup = self._doc.save()
                logging.info(f"SSH config saved from the settings (backup: {backup})")
            except ConfigError as e:
                return str(e)
        creds = (self._var_user.get().strip(), self._var_password.get())
        if creds != self._creds_loaded:
            if not creds[0] or not creds[1]:
                return "Utente e password: compila entrambi i campi (o lasciali com'erano)."
            try:
                ConfigLoader.save_maven_credentials(*creds)
                logging.info("Credentials saved in settings.xml from the settings dialog")
            except ValueError as e:
                return f"Utente e password non salvati: {e}"
            self._creds_loaded = creds
        return None

    def _save(self) -> None:
        values, error = self.collect()
        if error is None:
            error = self._apply_jump_hosts()
        if error is None:
            try:
                error = self._on_save(values)
            except Exception as e:
                logging.error(f"Saving settings failed: {e}", exc_info=True)
                error = f"Errore nel salvataggio: {e}"
        if error is None:
            error = self._write_files()
        if error:
            self._var_error.set(error)
            return
        self._saved = True
        self._hide()


class HostForm:
    """Modal form to add or edit a host (working copy of the config)."""

    def __init__(self, dialog: SettingsDialog, entry: Optional[HostEntry]):
        self._dlg = dialog
        self._entry = entry
        self._doc = dialog._doc

    def open(self) -> None:
        import tkinter as tk

        from . import widgets as w
        dlg, entry = self._dlg, self._entry
        new = entry is None
        kind = "tunnel" if new else entry.kind
        top = tk.Toplevel(dlg._top)
        self._top = top
        top.title("Nuovo host" if new else f"Modifica {entry.name}")
        theme.toplevel(top)
        top.transient(dlg._top)
        top.resizable(False, False)
        body = theme.style(tk.Frame(top, padx=px(24), pady=px(20)), "window")
        body.pack(fill="both", expand=True)
        w.page_title(body, "Nuovo host" if new else entry.name).pack(fill="x")
        subtitle = ("Raggiunto tramite un tunnel del jump host: l'app aggiunge da sola il "
                    "LocalForward e la voce Host." if kind == "tunnel" else
                    "Host raggiunto direttamente (HostName e porta propri).")
        w.label(body, subtitle, "hint", size=9, anchor="w", justify="left",
                wraplength=px(440)).pack(fill="x", pady=(px(2), px(14)))

        card = w.Card(body)
        card.pack(fill="x")
        self._vars: Dict[str, tk.StringVar] = {}

        def field(key, title, desc, value="", width=26, first=False):
            var = tk.StringVar(value=value)
            self._vars[key] = var
            _, e, _ = w.setting_row(card, title, desc, first=first, control=lambda p: theme.style(
                tk.Entry(p, textvariable=var, font=theme.font(10), width=width), "entry"))
            e.grid_configure(ipady=px(4))
            return e

        self._var_env = tk.StringVar(value=entry.env if entry else
                                     (AppSettings.get("search_env")
                                      if AppSettings.get("search_env") in ENVS else "TEST"))
        if new:
            w.setting_row(card, "Ambiente", "Sezione del config e jump host da usare.",
                          first=True, control=lambda p: w.Segmented(
                              p, ENVS, self._var_env, command=self._update_port_hint,
                              surface="card"))
        name_entry = field("name", "Nome", "Il nome usato nel menu e con ssh (es. gwit1te05).",
                           "" if new else entry.name, first=not new)
        field("description", "Descrizione", "Facoltativa, scritta come commento sopra l'host.",
              "" if new else entry.description)
        if kind == "tunnel":
            dest = "" if new or entry.dest == entry.name else entry.dest
            field("dest", "Server di destinazione",
                  "Nome o IP visto dal jump host. Vuoto = uguale al nome.", dest)
            field("dest_port", "Porta SSH del server", "", "22" if new else str(entry.dest_port),
                  width=8)
            self._port_row_desc = tk.StringVar()
            var = tk.StringVar(value="" if new else str(entry.port))
            self._vars["local_port"] = var
            _, e, _ = w.setting_row(card, "Porta locale del tunnel", control=lambda p: theme.style(
                tk.Entry(p, textvariable=var, font=theme.font(10), width=8), "entry"),
                textvariable=self._port_row_desc)
            e.grid_configure(ipady=px(4))
            self._update_port_hint()
        else:
            field("hostname", "HostName", "Nome o indirizzo IP del server.", entry.hostname)
            field("port", "Porta", "Vuoto = 22.", str(entry.port or ""), width=8)

        self._var_error = tk.StringVar()
        w.label(body, "", "error", size=9, anchor="w", justify="left", wraplength=px(440),
                textvariable=self._var_error).pack(fill="x", pady=(px(10), 0))
        btns = theme.style(tk.Frame(body), "window")
        btns.pack(fill="x", pady=(px(10), 0))
        w.Button(btns, "Aggiungi" if new else "Applica", self._ok, "primary", width=10).pack(
            side="right")
        w.Button(btns, "Annulla", top.destroy, width=10).pack(side="right", padx=(0, px(8)))
        top.bind("<Escape>", lambda e: top.destroy())
        top.bind("<Return>", lambda e: self._ok())

        top.update_idletasks()
        x = dlg._top.winfo_rootx() + (dlg._top.winfo_width() - top.winfo_reqwidth()) // 2
        y = dlg._top.winfo_rooty() + max(0, (dlg._top.winfo_height() - top.winfo_reqheight()) // 3)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.attributes("-topmost", True)
        top.grab_set()
        name_entry.focus_set()

    def _update_port_hint(self) -> None:
        if not hasattr(self, "_port_row_desc"):
            return
        try:
            free = self._doc.next_free_port(self._var_env.get())
        except Exception:
            free = None
        own = "" if self._entry is None else "Cambiala solo se serve. "
        self._port_row_desc.set(f"{own}Vuoto = prima libera ({free})." if free else own)

    def _ok(self) -> None:
        v = {k: var.get().strip() for k, var in self._vars.items()}
        doc, entry = self._doc, self._entry
        try:
            if entry is None:
                result = doc.add_host(self._var_env.get(), v["name"], dest=v["dest"],
                                      dest_port=v["dest_port"] or 22,
                                      local_port=v["local_port"] or None,
                                      description=v["description"])
            elif entry.kind == "tunnel":
                result = doc.update_host(entry.name, new_name=v["name"],
                                         dest=v["dest"] or v["name"],
                                         dest_port=v["dest_port"] or 22,
                                         local_port=v["local_port"] or None,
                                         description=v["description"])
            else:
                result = doc.update_host(entry.name, new_name=v["name"], hostname=v["hostname"],
                                         port=v["port"] or None, description=v["description"])
        except ConfigError as e:
            self._var_error.set(str(e))
            return
        dlg = self._dlg
        if entry is not None and result is not None and result.name != entry.name \
                and entry.name in dlg._favorites:
            dlg._favorites[dlg._favorites.index(entry.name)] = result.name
            dlg._refresh_favorites()
        dlg._var_error.set("")
        dlg._refresh_host_table(select=result.name if result else None)
        self._top.destroy()

"""
Settings dialog (tkinter), replacing the old "Settings" entry that only
opened ~/.ssh/config in Notepad (still reachable from the "Info" tab).

Same pre-warm pattern as the search popup, and actually the SAME Tk
interpreter: the dialog is a Toplevel of the SearchPopup root, built and
driven exclusively through `SearchPopup.run_on_ui()`. Tcl is not
thread-safe and a second Tk() on another thread would be a second
interpreter with its own mainloop — sharing one thread avoids both issues.
The window is built hidden at startup; opening it only reloads the values
and deiconifies it, so it appears instantly. Closing only hides it.

Hotkey field: the user clicks it and presses the combination. While it has
the focus the global hotkey is suspended (`hotkey_suspend(True)`), otherwise
pressing the current combination would be swallowed by RegisterHotKey and
open the search popup instead of reaching the field.

Favorites tab: all hosts on top (with a filter), an "Aggiungi" button, and
the current favorites below with "Rimuovi" / "Su" / "Giù".

Saving calls `on_save(values)` on the Tk thread; it returns None on success
or an error message (e.g. hotkey already taken by another program), which
is shown and keeps the dialog open.
"""

import logging
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..config import autostart
from ..config.app_settings import DEFAULTS, LIMITS, NOTIFICATION_KINDS, AppSettings
from ..config.config_loader import ConfigLoader
from .hotkey_manager import KEYS, MODIFIERS, parse_binding
from .search_dialog import (_C_BG, _C_HEADER, _C_MUTED, _C_SEL_BG, _C_SEL_FG,
                            _C_TEXT, _force_foreground)

LOG_FILE = Path.home() / "ssh_connection_debug.log"

# Tk keysym of a modifier key -> our modifier name
_MOD_KEYSYMS = {
    "Control_L": "Ctrl", "Control_R": "Ctrl",
    "Shift_L": "Shift", "Shift_R": "Shift",
    "Alt_L": "Alt", "Alt_R": "Alt",
    "Win_L": "Win", "Win_R": "Win",
}


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

    # ------------------------------------------------------------------
    # Public API (any thread)

    def prewarm(self) -> None:
        self._ui.run_on_ui(self._build)

    def show(self) -> None:
        self._ui.run_on_ui(self._do_show)

    # ------------------------------------------------------------------
    # Tk thread

    def _build(self) -> None:
        if self._top is not None:
            return
        import tkinter as tk
        from tkinter import ttk

        top = tk.Toplevel(self._ui.root)
        self._top = top
        top.title("SSH Connection Manager - Impostazioni")
        top.configure(bg=_C_BG)
        top.resizable(False, False)
        top.withdraw()
        top.protocol("WM_DELETE_WINDOW", self._hide)
        top.bind("<Escape>", lambda e: self._hide())

        band = tk.Frame(top, bg=_C_HEADER, height=48)
        band.pack(fill="x")
        band.pack_propagate(False)
        tk.Label(band, text="Impostazioni", bg=_C_HEADER, fg="white",
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=20)

        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        def tab(title):
            f = tk.Frame(nb, bg=_C_BG, padx=14, pady=12)
            nb.add(f, text=title)
            return f

        lbl = dict(bg=_C_BG, fg=_C_TEXT, font=("Segoe UI", 9))
        hint = dict(bg=_C_BG, fg=_C_MUTED, font=("Segoe UI", 8))
        chk = dict(bg=_C_BG, activebackground=_C_BG, font=("Segoe UI", 9), anchor="w")
        btn = dict(relief="flat", bg="#dfe4ea", font=("Segoe UI", 9), cursor="hand2")

        def listbox(parent, height, **kw):
            frame = tk.Frame(parent, bg=_C_BG)
            lb = tk.Listbox(frame, height=height, activestyle="none",
                            exportselection=False, relief="solid", bd=1,
                            highlightthickness=0, font=("Segoe UI", 9), fg=_C_TEXT,
                            selectbackground=_C_SEL_BG, selectforeground=_C_SEL_FG, **kw)
            lb.pack(side="left", fill="both", expand=True)
            sb = tk.Scrollbar(frame, command=lb.yview)
            sb.pack(side="right", fill="y")
            lb.config(yscrollcommand=sb.set)
            return frame, lb

        # --- Generale ---------------------------------------------------
        g = tab("Generale")
        g.columnconfigure(1, weight=1)
        tk.Label(g, text="Scorciatoia ricerca host", **lbl).grid(row=0, column=0, columnspan=2, sticky="w")
        hk = tk.Frame(g, bg=_C_BG)
        hk.grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 0))
        self._var_hotkey = tk.StringVar()
        entry = tk.Entry(hk, textvariable=self._var_hotkey, font=("Segoe UI", 11),
                         relief="solid", bd=1, justify="center", cursor="hand2",
                         readonlybackground="white", state="readonly")
        entry.pack(side="left", fill="x", expand=True, ipady=3)
        self._hotkey_entry = entry
        tk.Button(hk, text="Predefinita", command=self._reset_hotkey,
                  **btn).pack(side="left", padx=(8, 0))
        self._var_hotkey_hint = tk.StringVar()
        tk.Label(g, textvariable=self._var_hotkey_hint, **hint).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 12))
        entry.bind("<FocusIn>", self._capture_start)
        entry.bind("<FocusOut>", self._capture_end)
        entry.bind("<KeyPress>", self._on_key_press)
        entry.bind("<KeyRelease>", self._on_key_release)

        lo, hi = LIMITS["keepalive_interval"]
        tk.Label(g, text="Intervallo keepalive (secondi)", **lbl).grid(row=3, column=0, sticky="w")
        self._var_keepalive = tk.StringVar()
        tk.Spinbox(g, from_=lo, to=hi, increment=30, width=7,
                   textvariable=self._var_keepalive).grid(row=3, column=1, sticky="w", padx=8)

        lo, hi = LIMITS["tunnel_timeout"]
        tk.Label(g, text="Timeout attesa tunnel (secondi)", **lbl).grid(row=4, column=0, sticky="w", pady=(6, 0))
        self._var_tunnel = tk.StringVar()
        tk.Spinbox(g, from_=lo, to=hi, increment=10, width=7,
                   textvariable=self._var_tunnel).grid(row=4, column=1, sticky="w", padx=8, pady=(6, 0))
        tk.Label(g, text="Keepalive e timeout valgono dalla prossima connessione aperta.",
                 **hint).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 12))

        self._var_autostart = tk.BooleanVar()
        tk.Checkbutton(g, text="Avvia automaticamente con Windows",
                       variable=self._var_autostart, **chk).grid(row=6, column=0, columnspan=2, sticky="w")
        self._var_autostart_hint = tk.StringVar()
        tk.Label(g, textvariable=self._var_autostart_hint, justify="left", wraplength=400,
                 **hint).grid(row=7, column=0, columnspan=2, sticky="w")

        # --- Notifiche --------------------------------------------------
        n = tab("Notifiche")
        tk.Label(n, text="Mostra una notifica per:", **lbl).pack(anchor="w", pady=(0, 4))
        self._notif_vars = {}
        for kind, label in NOTIFICATION_KINDS.items():
            v = tk.BooleanVar()
            self._notif_vars[kind] = v
            tk.Checkbutton(n, text=label, variable=v, **chk).pack(anchor="w", fill="x")

        # --- Preferiti --------------------------------------------------
        f = tab("Preferiti")
        row = tk.Frame(f, bg=_C_BG)
        row.pack(fill="x")
        tk.Label(row, text="Filtra host", **lbl).pack(side="left")
        self._var_fav_filter = tk.StringVar()
        tk.Entry(row, textvariable=self._var_fav_filter, relief="solid", bd=1,
                 font=("Segoe UI", 9)).pack(side="right", fill="x", expand=True, padx=(12, 0))
        self._var_fav_filter.trace_add("write", lambda *_: self._refresh_hosts())
        frame, self._hosts_list = listbox(f, 7)
        frame.pack(fill="both", expand=True, pady=(4, 4))
        self._hosts_list.bind("<Double-Button-1>", lambda e: self._add_favorite())

        actions = tk.Frame(f, bg=_C_BG)
        actions.pack(fill="x")
        tk.Button(actions, text="Aggiungi ai preferiti  ↓", command=self._add_favorite,
                  bg="#2b6cb0", fg="white", activebackground="#245a93",
                  activeforeground="white", relief="flat", font=("Segoe UI", 9, "bold"),
                  cursor="hand2").pack(side="left")

        tk.Label(f, text="Preferiti (in cima al menu e alla ricerca, in quest'ordine)",
                 **lbl).pack(anchor="w", pady=(10, 0))
        favrow = tk.Frame(f, bg=_C_BG)
        favrow.pack(fill="both", expand=True, pady=(4, 4))
        frame, self._fav_list = listbox(favrow, 5)
        frame.pack(side="left", fill="both", expand=True)
        self._fav_list.bind("<Double-Button-1>", lambda e: self._remove_favorite())
        side = tk.Frame(favrow, bg=_C_BG)
        side.pack(side="left", fill="y", padx=(8, 0))
        for text, cmd in (("Rimuovi", self._remove_favorite),
                          ("Su", lambda: self._move_favorite(-1)),
                          ("Giù", lambda: self._move_favorite(1))):
            tk.Button(side, text=text, command=cmd, width=9, **btn).pack(pady=(0, 4))

        rec = tk.Frame(f, bg=_C_BG)
        rec.pack(fill="x")
        self._var_recents = tk.StringVar()
        tk.Label(rec, textvariable=self._var_recents, **hint).pack(side="left")
        tk.Button(rec, text="Svuota recenti", command=self._clear_recents,
                  **btn).pack(side="right")

        # --- Info -------------------------------------------------------
        i = tab("Info")
        tk.Label(i, text=f"SSH Connection Manager  v{self._version}",
                 bg=_C_BG, fg=_C_TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        for title, path in (("Config SSH", self._ssh_config_path),
                            ("Utente e password", ConfigLoader.maven_settings_path()),
                            ("Preferenze", AppSettings.path),
                            ("Log", LOG_FILE)):
            tk.Label(i, text=f"{title}:  {path}", **hint).pack(anchor="w", pady=(6, 0))
        links = tk.Frame(i, bg=_C_BG)
        links.pack(anchor="w", pady=(12, 0))
        tk.Button(links, text="Apri utente e password", command=self._open_credentials,
                  **btn).pack(side="left")
        tk.Button(links, text="Apri config SSH",
                  command=lambda: self._open(self._ssh_config_path), **btn).pack(side="left", padx=8)
        tk.Button(links, text="Apri log", command=lambda: self._open(LOG_FILE),
                  **btn).pack(side="left")
        self._var_info = tk.StringVar()
        tk.Label(i, textvariable=self._var_info, justify="left", wraplength=420,
                 **hint).pack(anchor="w", pady=(10, 0))

        # --- footer -----------------------------------------------------
        self._var_error = tk.StringVar()
        tk.Label(top, textvariable=self._var_error, bg=_C_BG, fg="#c0392b",
                 font=("Segoe UI", 8), anchor="w", wraplength=440).pack(fill="x", padx=18, pady=(6, 0))
        btns = tk.Frame(top, bg=_C_BG)
        btns.pack(fill="x", padx=16, pady=10)
        tk.Button(btns, text="Salva", command=self._save, bg="#2ea043", fg="white",
                  relief="flat", font=("Segoe UI", 9, "bold"), width=12,
                  activebackground="#278a39", activeforeground="white",
                  cursor="hand2").pack(side="right")
        tk.Button(btns, text="Annulla", command=self._hide, relief="flat",
                  bg="#dfe4ea", font=("Segoe UI", 9), width=12,
                  cursor="hand2").pack(side="right", padx=(0, 8))
        top.update_idletasks()

    def _load_values(self) -> None:
        cfg = AppSettings.all()
        self._set_binding(cfg["hotkey"])
        self._var_hotkey_hint.set("Clicca il campo e premi la combinazione (es. Ctrl+Shift+Space).")
        self._var_keepalive.set(str(cfg["keepalive_interval"]))
        self._var_tunnel.set(str(cfg["tunnel_timeout"]))
        self._var_autostart.set(autostart.is_enabled())
        self._var_autostart_hint.set(autostart.describe())
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
        creds = ConfigLoader.maven_settings_path()
        self._var_info.set("" if creds.exists() else
                           f"{creds} non esiste: 'Apri utente e password' lo crea da un modello.")

    def _do_show(self) -> None:
        self._build()
        self._load_values()
        top = self._top
        w, h = 500, 560
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
        self._var_hotkey_hint.set("Clicca il campo e premi la combinazione (es. Ctrl+Shift+Space).")

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
                mark = "★ " if h in self._favorites else "   "
                lb.insert("end", f"{mark}{h}   ({env})")
        if self._shown_hosts:
            lb.selection_set(0)

    def _refresh_favorites(self, select: Optional[int] = None) -> None:
        lb = self._fav_list
        lb.delete(0, "end")
        for h in self._favorites:
            env = self._env_of.get(h)
            lb.insert("end", f"  {h}   ({env})" if env else f"  {h}   (non più nel config)")
        if not self._favorites:
            lb.insert("end", "  Nessun preferito: seleziona un host sopra e premi Aggiungi")
            lb.itemconfig(0, fg=_C_MUTED, selectforeground=_C_MUTED, selectbackground="white")
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
                f"Creato {path}: sostituisci INSERISCI_UTENTE e INSERISCI_PASSWORD e salva. "
                f"Vale dalla prossima connessione, senza riavviare.")
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

    def collect(self) -> Tuple[Optional[Dict], Optional[str]]:
        """Validated values from the widgets, or (None, error message)."""
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
        return values, None

    def _save(self) -> None:
        values, error = self.collect()
        if error is None:
            try:
                error = self._on_save(values)
            except Exception as e:
                logging.error(f"Saving settings failed: {e}", exc_info=True)
                error = f"Errore nel salvataggio: {e}"
        if error:
            self._var_error.set(error)
            return
        self._hide()

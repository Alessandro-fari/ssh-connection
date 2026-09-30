"""
Look of the Tk windows (host popup, "Cerca file", Impostazioni): light or
dark theme, and DPI scaling.

DPI: the process declares itself DPI aware (`enable_dpi_awareness`, called
by main before any window exists). Without it Windows renders the windows
at 96 DPI and stretches the bitmap on a 125%/150% screen, which is why the
text looked blurry. Once aware, Tk scales the fonts (sizes in points) by
itself, but sizes in pixels (geometry, fixed widths, wraplength, Treeview
rows) do not follow: they go through `px()`.

Themes: "light" is the original palette; "dark" follows the One Half Dark
scheme of Windows Terminal. The font is the same in both (Segoe UI).
Widgets are not built with colours: they are registered with a *role*
(`style(widget, "button")`) and the palette of the current theme is applied
to them, so `use(dark)` can repaint every open window at once. Colours that
change with the state (status in error, busy search button, TEST/PROD
label) switch role with `set_role`. ttk widgets use the native "vista"
theme in light mode (the original look) and "clam" in dark mode, the only
one whose colours can be changed.

Everything here runs on the Tk thread (the SearchPopup one).
"""

import ctypes
import logging
from typing import Callable, Dict, List

PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#f4f6f9", "text": "#1e1e1e", "muted": "#5a6b7b",
        "header_bg": "#2b6cb0", "header_fg": "#ffffff",
        "field": "#ffffff", "border": "#9aa5b1", "focus": "#2b6cb0",
        "sep_bg": "#e6eaf0", "sel_bg": "#2b6cb0", "sel_fg": "#ffffff",
        "btn": "#dfe4ea", "btn_fg": "#1e1e1e", "btn_active": "#cdd5de", "btn_disabled": "#9aa5b1",
        "primary": "#2b6cb0", "primary_fg": "#ffffff", "primary_active": "#245a93",
        "success": "#2ea043", "success_fg": "#ffffff", "success_active": "#278a39",
        "danger": "#c05621", "danger_fg": "#ffffff", "danger_active": "#a44a1c",
        "error": "#c0392b", "test": "#2f855a", "prod": "#b7791f",
        "banner_bg": "#fdf3d7", "banner_fg": "#1e1e1e", "banner_btn": "#f5e2a8",
        "heading": "#e6eaf0",
    },
    # One Half Dark (Windows Terminal): background #282c34, foreground
    # #dcdfe4, blue #61afef, green #98c379, yellow #e5c07b, red #e06c75.
    "dark": {
        "bg": "#282c34", "text": "#dcdfe4", "muted": "#8b929e",
        "header_bg": "#21252b", "header_fg": "#61afef",
        "field": "#21252b", "border": "#4b5263", "focus": "#61afef",
        "sep_bg": "#2c313a", "sel_bg": "#61afef", "sel_fg": "#282c34",
        "btn": "#3a3f4b", "btn_fg": "#dcdfe4", "btn_active": "#4b5263", "btn_disabled": "#6b717d",
        "primary": "#61afef", "primary_fg": "#282c34", "primary_active": "#7cbcf2",
        "success": "#98c379", "success_fg": "#282c34", "success_active": "#aad08f",
        "danger": "#e06c75", "danger_fg": "#282c34", "danger_active": "#e8878f",
        "error": "#e06c75", "test": "#98c379", "prod": "#e5c07b",
        "banner_bg": "#3a3526", "banner_fg": "#e5c07b", "banner_btn": "#4d4530",
        "heading": "#2c313a",
    },
}


def _button(p, kind):
    return dict(bg=p[kind], fg=p[kind + "_fg"], activebackground=p[kind + "_active"],
                activeforeground=p[kind + "_fg"], disabledforeground=p["btn_disabled"],
                highlightthickness=0, bd=0)


# role -> options of the Tk (not ttk) widget, from the palette
ROLES: Dict[str, Callable[[Dict[str, str]], dict]] = {
    "window": lambda p: dict(bg=p["bg"]),
    "header": lambda p: dict(bg=p["header_bg"]),
    "header_title": lambda p: dict(bg=p["header_bg"], fg=p["header_fg"]),
    "label": lambda p: dict(bg=p["bg"], fg=p["text"]),
    "hint": lambda p: dict(bg=p["bg"], fg=p["muted"]),
    "error": lambda p: dict(bg=p["bg"], fg=p["error"]),
    "env_test": lambda p: dict(bg=p["bg"], fg=p["test"]),
    "env_prod": lambda p: dict(bg=p["bg"], fg=p["prod"]),
    "entry": lambda p: dict(bg=p["field"], fg=p["text"], insertbackground=p["text"],
                            readonlybackground=p["field"], disabledbackground=p["bg"],
                            selectbackground=p["sel_bg"], selectforeground=p["sel_fg"],
                            relief="flat", bd=0, highlightthickness=1,
                            highlightbackground=p["border"], highlightcolor=p["focus"]),
    "listbox": lambda p: dict(bg=p["field"], fg=p["text"], selectbackground=p["sel_bg"],
                              selectforeground=p["sel_fg"], relief="flat", bd=0,
                              highlightthickness=1, highlightbackground=p["border"],
                              highlightcolor=p["border"]),
    "spinbox": lambda p: dict(bg=p["field"], fg=p["text"], insertbackground=p["text"],
                              buttonbackground=p["btn"], readonlybackground=p["field"],
                              selectbackground=p["sel_bg"], selectforeground=p["sel_fg"],
                              relief="flat", bd=0, highlightthickness=1,
                              highlightbackground=p["border"], highlightcolor=p["focus"]),
    "check": lambda p: dict(bg=p["bg"], fg=p["text"], activebackground=p["bg"],
                            activeforeground=p["text"], selectcolor=p["field"],
                            highlightthickness=0),
    "button": lambda p: dict(bg=p["btn"], fg=p["btn_fg"], activebackground=p["btn_active"],
                             activeforeground=p["btn_fg"], disabledforeground=p["btn_disabled"],
                             highlightthickness=0, bd=0),
    "primary": lambda p: _button(p, "primary"),
    "success": lambda p: _button(p, "success"),
    "danger": lambda p: _button(p, "danger"),
    "banner": lambda p: dict(bg=p["banner_bg"]),
    "banner_label": lambda p: dict(bg=p["banner_bg"], fg=p["banner_fg"]),
    "banner_button": lambda p: dict(bg=p["banner_btn"], fg=p["banner_fg"],
                                    activebackground=p["banner_btn"],
                                    activeforeground=p["banner_fg"], highlightthickness=0, bd=0),
    "combo": lambda p: {},        # ttk: styled by _apply_ttk, popdown list below
    "tree": lambda p: {},
}

_state = {"dark": False, "scale": 1.0, "root": None, "native_ttk": None}
_widgets: Dict[str, tuple] = {}         # Tk path -> (widget, role)
_toplevels: List = []
_listeners: List[Callable[[], None]] = []


# ----------------------------------------------------------------------
# DPI

def enable_dpi_awareness() -> None:
    """Declare the process DPI aware (system DPI), before any window is
    created: crisp text instead of a stretched 96-DPI bitmap. System-aware
    rather than per-monitor because Tk 8.6 does not handle WM_DPICHANGED:
    on a monitor with another DPI Windows still scales the window for us."""
    try:
        # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE (Windows 10 1607+)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)     # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError) as e:
            logging.debug(f"DPI awareness not available: {e}")


def px(n: float) -> int:
    """Pixels at 96 DPI -> pixels on this screen."""
    return int(round(n * _state["scale"]))


# ----------------------------------------------------------------------
# Theme

def is_dark() -> bool:
    return _state["dark"]


def color(name: str) -> str:
    return PALETTES["dark" if _state["dark"] else "light"][name]


def init(root, dark: bool) -> None:
    """Once, on the Tk root just created: DPI scale, ttk and option database."""
    _state["root"] = root
    # 96 when the process is not DPI aware (tests, smoke runs): scale 1.
    _state["scale"] = max(1.0, root.winfo_fpixels("1i") / 96.0)
    from tkinter import ttk
    _state["native_ttk"] = ttk.Style(root).theme_use()
    _state["dark"] = bool(dark)
    _apply_global(root)
    toplevel(root)


def style(widget, role: str):
    """Register `widget` with `role` and paint it with the current palette."""
    _widgets[str(widget)] = (widget, role)
    _paint(widget, role)
    return widget


set_role = style


def toplevel(win) -> None:
    """Register a Tk root / Toplevel: background and title bar follow the theme."""
    _toplevels.append(win)
    style(win, "window")
    _title_bar(win)


def on_change(callback: Callable[[], None]) -> None:
    """`callback()` after every theme switch (e.g. to refill listboxes
    whose rows carry their own colours)."""
    _listeners.append(callback)


def use(dark: bool) -> None:
    """Switch theme and repaint every registered widget (Tk thread)."""
    dark = bool(dark)
    root = _state["root"]
    if root is None or dark == _state["dark"]:
        _state["dark"] = dark
        return
    _state["dark"] = dark
    _apply_global(root)
    for path, (widget, role) in list(_widgets.items()):
        try:
            if widget.winfo_exists():
                _paint(widget, role)
            else:
                del _widgets[path]
        except Exception as e:
            logging.debug(f"Theme repaint of {path} failed: {e}")
    for win in _toplevels:
        _title_bar(win)
    for cb in list(_listeners):
        try:
            cb()
        except Exception as e:
            logging.error(f"Theme listener failed: {e}", exc_info=True)


def _paint(widget, role: str) -> None:
    opts = ROLES[role](PALETTES["dark" if _state["dark"] else "light"])
    if opts:
        keys = set(widget.keys())
        widget.configure(**{k: v for k, v in opts.items() if k in keys})
    if role == "combo":
        _style_popdown(widget)


def _style_popdown(combo) -> None:
    """Colours of the drop-down list of a ttk.Combobox (a Tk listbox that
    ttk creates once and never restyles)."""
    try:
        pop = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        combo.tk.call(f"{pop}.f.l", "configure",
                      "-background", color("field"), "-foreground", color("text"),
                      "-selectbackground", color("sel_bg"),
                      "-selectforeground", color("sel_fg"))
    except Exception as e:
        logging.debug(f"Combobox popdown styling failed: {e}")


def _apply_global(root) -> None:
    from tkinter import font as tkfont
    from tkinter import ttk
    p = PALETTES["dark" if _state["dark"] else "light"]
    s = ttk.Style(root)
    if _state["dark"]:
        s.theme_use("clam")
        s.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["field"],
                    bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                    troughcolor=p["bg"], selectbackground=p["sel_bg"],
                    selectforeground=p["sel_fg"], insertcolor=p["text"], arrowcolor=p["text"])
        s.map(".", background=[("disabled", p["bg"]), ("active", p["btn_active"])],
              foreground=[("disabled", p["btn_disabled"])])
        s.configure("TCombobox", fieldbackground=p["field"], background=p["btn"],
                    foreground=p["text"], arrowcolor=p["text"], insertcolor=p["text"])
        s.map("TCombobox", fieldbackground=[("readonly", p["field"])],
              foreground=[("readonly", p["text"])], background=[("active", p["btn_active"])],
              selectbackground=[("readonly", p["field"])],
              selectforeground=[("readonly", p["text"])], bordercolor=[("focus", p["focus"])])
        s.configure("Vertical.TScrollbar", background=p["btn"], troughcolor=p["bg"],
                    bordercolor=p["bg"], arrowcolor=p["text"], gripcount=0)
        s.map("Vertical.TScrollbar", background=[("active", p["btn_active"])])
        s.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
        s.configure("TNotebook.Tab", background=p["btn"], foreground=p["text"],
                    bordercolor=p["border"], padding=(px(10), px(3)))
        s.map("TNotebook.Tab", background=[("selected", p["bg"]), ("active", p["btn_active"])],
              lightcolor=[("selected", p["bg"])])
        s.configure("Treeview", background=p["field"], fieldbackground=p["field"],
                    foreground=p["text"], bordercolor=p["border"])
        s.map("Treeview", background=[("selected", p["sel_bg"])],
              foreground=[("selected", p["sel_fg"])])
        s.configure("Treeview.Heading", background=p["heading"], foreground=p["text"],
                    bordercolor=p["border"], relief="flat")
        s.map("Treeview.Heading", background=[("active", p["btn_active"])])
    else:
        s.theme_use(_state["native_ttk"] or "vista")
    # Tk 8.6 keeps Treeview rows at a fixed pixel height: follow the font.
    linespace = tkfont.nametofont("TkDefaultFont", root=root).metrics("linespace")
    s.configure("Treeview", rowheight=max(px(20), linespace + px(4)))
    # Drop-down lists created from now on (the existing ones: _style_popdown).
    for opt, key in (("background", "field"), ("foreground", "text"),
                     ("selectBackground", "sel_bg"), ("selectForeground", "sel_fg")):
        root.option_add(f"*TCombobox*Listbox.{opt}", p[key])


def _title_bar(win) -> None:
    """Dark / light title bar (Windows 10 20H1+ and 11; ignored elsewhere)."""
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        value = ctypes.c_int(1 if _state["dark"] else 0)
        for attr in (20, 19):       # DWMWA_USE_IMMERSIVE_DARK_MODE (19 before 20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception as e:
        logging.debug(f"Title bar theming failed: {e}")

"""
Windows 11 style building blocks for the Tk windows, on top of `theme`.

- Button / IconButton: flat buttons with hover (and a tooltip for icons).
- Card + setting_row: the "settings card" of Windows 11 (title and
  description on the left, the control on the right).
- Toggle: on/off switch bound to a BooleanVar (antialiased images).
- Segmented: a row of choices bound to a StringVar (Tutti / TEST / PROD).
- SearchEntry: entry with a search glyph and a placeholder.
- ListView: a Treeview with the part of the tk.Listbox API the dialogs use
  (insert/delete/curselection/selection_set/see/<<ListboxSelect>>...), so
  rows get a real height, images and styled separator rows.
- Sidebar: navigation pane with icon, label and the accent indicator.
- Images (status dots, folder / file) are drawn with PIL, 4x oversampled.

Every widget is created and used on the Tk thread only.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageTk

from . import theme
from .theme import color, font, px

# Segoe Fluent Icons / MDL2 glyphs, with a text fallback
GLYPHS = {
    "back": ("", "◀"), "up": ("", "▲"), "home": ("", "⌂"),
    "refresh": ("", "⟳"), "search": ("", ""), "settings": ("", "⚙"),
    "color": ("", "◐"), "host": ("", "▣"), "account": ("", "☺"),
    "star": ("", "★"), "bell": ("", "!"), "info": ("", "i"),
    "add": ("", "+"), "edit": ("", "✎"), "delete": ("", "✕"),
    "eye": ("", "👁"), "open": ("", "↗"), "copy": ("", "⧉"),
    "download": ("", "↓"), "folder": ("", "▭"), "close": ("", "✕"),
    "chevron_up": ("", "▲"), "chevron_down": ("", "▼"),
}


def glyph(name: str) -> Tuple[str, Optional[tuple]]:
    """(text, font) of an icon: the Fluent glyph when the font exists."""
    code, fallback = GLYPHS[name]
    f = theme.icon_font(11)
    return (code, f) if f else (fallback, font(10))


# ----------------------------------------------------------------------
# Images

_images: Dict[tuple, ImageTk.PhotoImage] = {}

GREEN, GREEN_EDGE = (46, 160, 67, 255), (24, 110, 44, 255)
TEST_FILL, TEST_EDGE = (245, 246, 250, 255), (150, 155, 165, 255)
PROD_FILL, PROD_EDGE = (255, 244, 214, 255), (227, 179, 65, 255)


def _rgba(hex_color: str, alpha: int = 255):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _photo(key: tuple, size: Tuple[int, int], draw: Callable[[ImageDraw.ImageDraw, int], None],
           master=None) -> ImageTk.PhotoImage:
    key = key + (theme.is_dark(), size)
    if key not in _images:
        k = 4
        big = Image.new("RGBA", (size[0] * k, size[1] * k), (0, 0, 0, 0))
        draw(ImageDraw.Draw(big), k)
        _images[key] = ImageTk.PhotoImage(big.resize(size, Image.LANCZOS), master=master)
    return _images[key]


def status_image(env: str, active: bool, master=None) -> ImageTk.PhotoImage:
    """Same visual language as the tray menu: TEST circles, PROD squares,
    green when connected."""
    s = px(14)

    def draw(d, k):
        m, w = 2 * k, max(k, s * k // 9)
        box = [m, m, s * k - m - 1, s * k - m - 1]
        fill, edge = ((GREEN, GREEN_EDGE) if active else
                      (TEST_FILL, TEST_EDGE) if env == "TEST" else (PROD_FILL, PROD_EDGE))
        if env == "PROD":
            d.rounded_rectangle(box, radius=2 * k, fill=fill, outline=edge, width=w)
        else:
            d.ellipse(box, fill=fill, outline=edge, width=w)
    return _photo(("status", env, active), (s, s), draw, master)


def folder_image(master=None) -> ImageTk.PhotoImage:
    s = px(18)

    def draw(d, k):
        u = s * k / 18.0
        d.rounded_rectangle([1 * u, 3 * u, 8 * u, 7 * u], radius=1.5 * u, fill=(214, 158, 46, 255))
        d.rounded_rectangle([1 * u, 5 * u, 17 * u, 15.5 * u], radius=1.8 * u,
                            fill=(233, 181, 62, 255))
        d.rounded_rectangle([1 * u, 7 * u, 17 * u, 15.5 * u], radius=1.8 * u,
                            fill=(246, 199, 86, 255))
    return _photo(("folder",), (s, s), draw, master)


def file_image(master=None) -> ImageTk.PhotoImage:
    s = px(18)

    def draw(d, k):
        u = s * k / 18.0
        edge = _rgba(color("faint"))
        d.rounded_rectangle([3.5 * u, 1.5 * u, 14.5 * u, 16.5 * u], radius=1.8 * u,
                            fill=_rgba(color("field")), outline=edge, width=max(1, int(u)))
        for y in (6, 9, 12):
            d.line([6 * u, y * u, 12 * u, y * u], fill=edge, width=max(1, int(u)))
    return _photo(("file",), (s, s), draw, master)


def toggle_image(on: bool, master=None) -> ImageTk.PhotoImage:
    w, h = px(40), px(20)

    def draw(d, k):
        box = [k, k, w * k - k - 1, h * k - k - 1]
        r = (h * k - 2 * k) / 2
        if on:
            d.rounded_rectangle(box, radius=r, fill=_rgba(color("accent")))
            knob = _rgba(color("accent_fg"))
            cx = w * k - k - r
        else:
            d.rounded_rectangle(box, radius=r, outline=_rgba(color("toggle_off")), width=k + 1)
            knob = _rgba(color("toggle_off"))
            cx = k + r
        rr = r * 0.55
        cy = h * k / 2
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=knob)
    return _photo(("toggle", on), (w, h), draw, master)


# ----------------------------------------------------------------------
# Buttons

def Button(parent, text: str, command, kind: str = "button", surface: str = "bg",
           width: Optional[int] = None, bold: bool = False, **kw):
    """Flat button; kind 'button' (secondary), 'primary', 'danger', 'subtle'."""
    holder = None
    if kind == "button":
        # tk.Button draws no highlight border on Windows: a 1 px frame does.
        holder = theme.style(tk.Frame(parent, padx=1, pady=1), "btn_border", surface)
    b = tk.Button(holder or parent, text=text, command=command, cursor="hand2",
                  font=font(10, "semibold" if bold or kind == "primary" else "normal"),
                  padx=px(14), pady=px(4), **kw)
    if width:
        b.configure(width=width)
    theme.style(b, kind, surface)
    if holder is not None:
        b.pack(fill="both", expand=True)
        # the caller lays out the button: lay out the frame instead
        for name in ("pack", "grid", "place", "pack_forget", "grid_forget", "place_forget",
                     "pack_configure", "grid_configure"):
            setattr(b, name, getattr(holder, name))
    return b


def IconButton(parent, name: str, command, surface: str = "bg", tooltip: str = "",
               size: int = 11):
    text, f = glyph(name)
    if theme.icon_font(size):
        f = theme.icon_font(size)
    b = tk.Button(parent, text=text, command=command, font=f, cursor="hand2",
                  width=2, padx=px(4), pady=px(3))
    theme.style(b, "subtle", surface)
    if tooltip:
        Tooltip(b, tooltip)
    return b


class Tooltip:
    """Small hint shown after half a second over a widget."""

    def __init__(self, widget, text: str):
        self._w, self.text, self._tip, self._job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self._w.after(500, self._show)

    def _cancel(self):
        if self._job:
            self._w.after_cancel(self._job)
            self._job = None

    def _show(self):
        self._job = None
        if self._tip or not self.text:
            return
        tip = tk.Toplevel(self._w)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, font=font(9), bg=color("card"), fg=color("text"),
                 padx=px(8), pady=px(4), highlightthickness=1,
                 highlightbackground=color("card_border")).pack()
        x = self._w.winfo_rootx()
        y = self._w.winfo_rooty() + self._w.winfo_height() + px(4)
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _e=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ----------------------------------------------------------------------
# Layout

def Card(parent, surface: str = "bg", **kw):
    return theme.style(tk.Frame(parent, **kw), "card", surface)


def Divider(parent, surface: str = "card"):
    return theme.style(tk.Frame(parent, height=1), "divider", surface)


def label(parent, text: str = "", role: str = "label", surface: str = "bg", size: int = 10,
          weight: str = "normal", **kw):
    return theme.style(tk.Label(parent, text=text, font=font(size, weight), **kw), role, surface)


def page_title(parent, text: str, surface: str = "bg"):
    return label(parent, text, "title", surface, size=18, weight="semibold", anchor="w")


def section_title(parent, text: str, surface: str = "bg"):
    return label(parent, text, "label", surface, size=10, weight="semibold", anchor="w")


def setting_row(card, title: str, description: str = "",
                control: Optional[Callable[[tk.Widget], tk.Widget]] = None,
                first: bool = False, textvariable=None):
    """One row of a settings card. `control(parent)` builds the widget on
    the right. Returns (row frame, control widget, description label)."""
    if not first:
        Divider(card).pack(fill="x")
    row = theme.style(tk.Frame(card, padx=px(16), pady=px(12)), "window", "card")
    row.pack(fill="x")
    row.columnconfigure(0, weight=1)
    label(row, title, surface="card", anchor="w").grid(row=0, column=0, sticky="w")
    desc = None
    if description or textvariable is not None:
        desc = label(row, description, "hint", "card", size=9, anchor="w", justify="left",
                     wraplength=px(430), textvariable=textvariable)
        desc.grid(row=1, column=0, sticky="w")
    widget = None
    if control is not None:
        widget = control(row)
        widget.grid(row=0, column=1, rowspan=2, sticky="e", padx=(px(12), 0))
    return row, widget, desc


# ----------------------------------------------------------------------
# Inputs

class Toggle(tk.Label):
    """Windows 11 on/off switch bound to a BooleanVar."""

    def __init__(self, parent, variable: tk.BooleanVar, surface: str = "card",
                 command: Optional[Callable[[], None]] = None, **kw):
        super().__init__(parent, cursor="hand2", takefocus=1, bd=0, **kw)
        self._var, self._command = variable, command
        self._text_on, self._text_off = "Attivato", "Disattivato"
        theme.style(self, "label", surface)
        self.configure(compound="right", font=font(10), padx=0)
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<space>", lambda e: self.toggle())
        variable.trace_add("write", lambda *_: self._redraw())
        theme.on_change(self._redraw)
        self._redraw()

    def toggle(self) -> None:
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _redraw(self) -> None:
        try:
            on = bool(self._var.get())
        except tk.TclError:
            on = False
        self.configure(image=toggle_image(on, self),
                       text=(self._text_on if on else self._text_off) + "  ")


class Segmented(tk.Frame):
    """Row of mutually exclusive choices bound to a StringVar."""

    def __init__(self, parent, values: Sequence[str], variable: tk.StringVar,
                 command: Optional[Callable[[], None]] = None, surface: str = "bg"):
        super().__init__(parent)
        theme.style(self, "field_frame", surface)
        self._var, self._command, self._items = variable, command, {}
        for v in values:
            b = tk.Label(self, text=v, font=font(9, "semibold"), padx=px(10), pady=px(3),
                         cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, v=v: self._pick(v))
            self._items[v] = b
        variable.trace_add("write", lambda *_: self._redraw())
        self._redraw()

    def _pick(self, value: str) -> None:
        self._var.set(value)
        if self._command:
            self._command()

    def _redraw(self) -> None:
        cur = self._var.get()
        for v, b in self._items.items():
            theme.style(b, "seg_selected" if v == cur else "seg", "field")


class SearchEntry(tk.Frame):
    """Entry with a search glyph on the left and a placeholder."""

    def __init__(self, parent, variable: tk.StringVar, placeholder: str = "",
                 surface: str = "bg", size: int = 10, icon: bool = True):
        super().__init__(parent)
        theme.style(self, "field_frame", surface)
        if icon:
            text, f = glyph("search")
            if text:
                theme.style(tk.Label(self, text=text, font=f, padx=px(8)),
                            "field_icon", "field").pack(side="left")
        self.entry = theme.style(tk.Entry(self, textvariable=variable, font=font(size)),
                                 "entry_bare", "field")
        self.entry.pack(side="left", fill="both", expand=True, ipady=px(5),
                        padx=(0 if icon else px(8), px(8)))
        self._ph = theme.style(tk.Label(self, text=placeholder, font=font(size), anchor="w"),
                               "placeholder", "field")
        self._ph.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._var = variable
        variable.trace_add("write", lambda *_: self._sync())
        # the frame border follows the focus of the inner entry
        self.entry.bind("<FocusIn>", lambda e: self.configure(
            highlightbackground=color("focus")), add="+")
        self.entry.bind("<FocusOut>", lambda e: self.configure(
            highlightbackground=color("border")), add="+")
        self.after_idle(self._sync)

    def _sync(self) -> None:
        if self._var.get():
            self._ph.place_forget()
        else:
            self._ph.place(in_=self.entry, x=px(1), rely=0.5, anchor="w")

    def focus_set(self):
        self.entry.focus_set()

    def focus_force(self):
        self.entry.focus_force()


# ----------------------------------------------------------------------
# ListView

class ListView(tk.Frame):
    """Treeview with the tk.Listbox API used by the dialogs.

    Rows are addressed by index (iid = str(index)); `delete(0, "end")`
    clears. <<ListboxSelect>> is generated only when the user changes the
    selection (click, keys), like a Listbox, not for selection_set()."""

    def __init__(self, parent, height: int = 8, surface: str = "bg",
                 detail_width: int = 0, font_size: int = 10):
        super().__init__(parent)
        theme.style(self, "field_frame", surface)
        cols = ("detail",) if detail_width else ()
        self.tree = ttk.Treeview(self, show="tree", selectmode="browse", height=height,
                                 columns=cols)
        self.tree.column("#0", stretch=True, width=px(120))
        if detail_width:
            self.tree.column("detail", width=px(detail_width), anchor="e", stretch=False)
        self.tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y", pady=1)
        self.tree.configure(yscrollcommand=sb.set)
        self._n = 0
        self._font_size = font_size
        self._expected: tuple = ()
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        theme.on_change(self._config_tags)
        self._config_tags()

    def _config_tags(self) -> None:
        t = self.tree
        t.tag_configure("row", font=font(self._font_size))
        t.tag_configure("sep", foreground=color("sep_fg"), font=font(8, "semibold"))
        t.tag_configure("muted", foreground=color("faint"), font=font(self._font_size))

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        if sel != self._expected:
            self._expected = sel
            self.tree.event_generate("<<ListboxSelect>>")

    # --- Listbox API ------------------------------------------------------

    def insert(self, index, text: str, image=None, tags: Sequence[str] = ("row",),
               detail: str = "") -> int:
        iid = str(self._n)
        kw = {"text": text, "tags": tuple(tags)}
        if image is not None:
            kw["image"] = image
        if detail:
            kw["values"] = (detail,)
        self.tree.insert("", "end", iid=iid, **kw)
        self._n += 1
        return self._n - 1

    def delete(self, first, last=None) -> None:
        self.tree.delete(*self.tree.get_children())
        self._n = 0
        self._expected = ()

    def size(self) -> int:
        return self._n

    def get(self, index: int) -> str:
        return self.tree.item(str(index), "text")

    def curselection(self) -> tuple:
        return tuple(int(i) for i in self.tree.selection())

    def selection_set(self, index, last=None) -> None:
        if 0 <= int(index) < self._n:
            self._expected = (str(index),)
            self.tree.selection_set(str(index))
            self.tree.focus(str(index))

    def selection_clear(self, first=None, last=None) -> None:
        self._expected = ()
        self.tree.selection_set(())

    def activate(self, index) -> None:
        if 0 <= int(index) < self._n:
            self.tree.focus(str(index))

    def see(self, index) -> None:
        if 0 <= int(index) < self._n:
            self.tree.see(str(index))

    def set_tags(self, index: int, tags: Sequence[str]) -> None:
        self.tree.item(str(index), tags=tuple(tags))

    def bind(self, sequence=None, func=None, add=None):
        return self.tree.bind(sequence, func, add)

    def focus_set(self):
        self.tree.focus_set()

    def focus_force(self):
        self.tree.focus_force()


# ----------------------------------------------------------------------
# Sidebar

class Sidebar(tk.Frame):
    """Navigation pane: items (key, label, glyph); `on_select(key)`."""

    def __init__(self, parent, items: Sequence[Tuple[str, str, str]],
                 on_select: Callable[[str], None], width: int = 210):
        super().__init__(parent, width=px(width))
        theme.style(self, "window", "nav")
        self.pack_propagate(False)
        self._on_select = on_select
        self._items: Dict[str, List[tk.Widget]] = {}
        self._current = None
        for key, text, icon in items:
            row = tk.Frame(self, cursor="hand2")
            row.pack(fill="x", padx=px(6), pady=px(1))
            ind = tk.Frame(row, width=px(3))
            ind.pack(side="left", fill="y", pady=px(8))
            g, gf = glyph(icon)
            ic = tk.Label(row, text=g, font=gf, width=2, padx=px(6), pady=px(7))
            ic.pack(side="left")
            lb = tk.Label(row, text=text, font=font(10), anchor="w")
            lb.pack(side="left", fill="x", expand=True)
            self._items[key] = [row, ind, ic, lb]
            for w in (row, ic, lb):
                w.bind("<Button-1>", lambda e, k=key: self.select(k))
                w.bind("<Enter>", lambda e, k=key: self._hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._hover(k, False))
        theme.on_change(self._repaint)
        self._repaint()

    def select(self, key: str) -> None:
        self._current = key
        self._repaint()
        self._on_select(key)

    @property
    def current(self) -> Optional[str]:
        return self._current

    def _hover(self, key: str, inside: bool) -> None:
        if key == self._current:
            return
        bg = color("subtle_hover") if inside else color("nav")
        row, ind, ic, lb = self._items[key]
        for w in (row, ind, ic, lb):
            w.configure(bg=bg)

    def _repaint(self) -> None:
        for key, (row, ind, ic, lb) in self._items.items():
            sel = key == self._current
            role = "nav_item_selected" if sel else "nav_item"
            for w in (row, ic, lb):
                theme.style(w, role, "nav")
            theme.style(ind, "nav_indicator_selected" if sel else "nav_indicator", "nav")
            lb.configure(font=font(10, "semibold" if sel else "normal"))

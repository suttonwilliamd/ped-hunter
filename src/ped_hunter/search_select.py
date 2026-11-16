"""A searchable dropdown: click to open the list, type to filter it.

The field is a ttk.Combobox (so ``get``/``set``/``textvariable`` and
``<<ComboboxSelected>>`` keep working) with its own list that never takes the
keyboard away from the field: typing narrows the list, arrows move through it,
Enter picks, Esc cancels. Only listed values can be chosen; anything else typed
is put back to the last valid choice when the field is left.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

MAX_ROWS = 10
NO_MATCH = "No match"


def filter_values(values, query: str) -> list[str]:
    """Values containing every typed word (any case); names starting with the text first."""
    words = query.casefold().split()
    if not words:
        return list(values)
    start = " ".join(words)
    hits = [value for value in values if all(word in value.casefold() for word in words)]
    return ([value for value in hits if value.casefold().startswith(start)]
            + [value for value in hits if not value.casefold().startswith(start)])


class SearchSelect(ttk.Combobox):
    def __init__(self, parent, *, values, on_change=None, colors=None, list_font=None, **kwargs):
        super().__init__(parent, values=list(values), state="normal", **kwargs)
        self._on_change = on_change
        self._colors = colors or {}
        self._list_font = list_font
        self._committed: str | None = None
        self._shown: list[str] = []
        self._popup: tk.Toplevel | None = None
        self._listbox: tk.Listbox | None = None
        self._blur_job = None
        # Our bindings run before the stock combobox ones, which would open
        # the native list (it grabs the keyboard, so typing could not filter).
        tag = f"SearchSelect:{self}"
        self.bindtags((tag, *self.bindtags()))
        self.bind_class(tag, "<ButtonPress-1>", self._pressed)
        self.bind_class(tag, "<KeyRelease>", self._typed)
        self.bind_class(tag, "<Down>", lambda _e: self._move(1))
        self.bind_class(tag, "<Up>", lambda _e: self._move(-1))
        self.bind_class(tag, "<Next>", lambda _e: self._move(MAX_ROWS))
        self.bind_class(tag, "<Prior>", lambda _e: self._move(-MAX_ROWS))
        self.bind_class(tag, "<Return>", self._enter)
        self.bind_class(tag, "<KP_Enter>", self._enter)
        self.bind_class(tag, "<Tab>", lambda _e: self._leave_by_key())
        self.bind_class(tag, "<Escape>", self._escape)
        self.bind_class(tag, "<MouseWheel>", self._wheel)
        self.bind_class(tag, "<FocusIn>", self._focus_in)
        self.bind_class(tag, "<FocusOut>", self._focus_out)
        self.bind_class(tag, "<Destroy>", lambda _e: self.close())
        top = self.winfo_toplevel()
        top.bind("<ButtonPress-1>", self._clicked_elsewhere, add="+")
        top.bind("<Configure>", self._window_moved, add="+")

    # ----- state ---------------------------------------------------------
    def is_open(self) -> bool:
        return self._popup is not None and self._popup.winfo_viewable()

    def choices(self) -> list[str]:
        return list(self.cget("values") or ())

    def _valid(self, text: str) -> str | None:
        wanted = text.strip().casefold()
        return next((value for value in self.choices() if value.casefold() == wanted), None)

    # ----- list ----------------------------------------------------------
    def _build_popup(self) -> None:
        colors = self._colors
        self._popup = popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        try:
            popup.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(popup, bg=colors.get("border", "#394654"), padx=1, pady=1)
        frame.pack(fill="both", expand=True)
        self._listbox = listbox = tk.Listbox(
            frame, activestyle="none", exportselection=False, takefocus=0, borderwidth=0, highlightthickness=0,
            bg="#0a0f1a", fg=colors.get("text", "#edf1f5"), selectbackground="#155e59", selectforeground="#ffffff",
            disabledforeground=colors.get("muted", "#a5b0bc"), font=self._list_font or self.cget("font") or "TkDefaultFont")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._scrollbar = scrollbar
        # A click picks; the list never takes focus from the field.
        listbox.bind("<ButtonPress-1>", self._list_pressed)
        listbox.bind("<Motion>", lambda event: self._highlight(listbox.nearest(event.y)))
        listbox.bind("<MouseWheel>", self._wheel)

    def open(self, query: str | None = None) -> None:
        """Show the list: everything, or the values matching ``query``."""
        if self._popup is None or not self._popup.winfo_exists():
            self._build_popup()
        listbox = self._listbox
        self._shown = filter_values(self.choices(), query or "")
        listbox.configure(state="normal")
        listbox.delete(0, "end")
        if self._shown:
            listbox.insert("end", *self._shown)
            current = self._valid(self.get())
            self._highlight(self._shown.index(current) if query is None and current in self._shown else 0)
        else:
            listbox.insert("end", NO_MATCH)
            listbox.configure(state="disabled")
        rows = max(1, min(MAX_ROWS, len(self._shown)))
        listbox.configure(height=rows)
        if len(self._shown) > MAX_ROWS:
            self._scrollbar.pack(side="right", fill="y")
        else:
            self._scrollbar.pack_forget()
        self._place_popup()

    def _place_popup(self) -> None:
        popup = self._popup
        popup.update_idletasks()
        font = tkfont.Font(font=self._listbox.cget("font"))
        longest = max((font.measure(value) for value in self._shown), default=0) + font.measure("0") * 4
        width = max(self.winfo_width(), min(longest, self.winfo_width() * 2, self.winfo_screenwidth() // 2))
        height = popup.winfo_reqheight()
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height()
        if y + height > self.winfo_screenheight() and self.winfo_rooty() - height >= 0:
            y = self.winfo_rooty() - height
        x = max(0, min(x, self.winfo_screenwidth() - width))
        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.lift()

    def close(self) -> None:
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.withdraw()

    def _highlight(self, index: int) -> None:
        if not self._shown:
            return
        index = max(0, min(len(self._shown) - 1, index))
        listbox = self._listbox
        listbox.selection_clear(0, "end")
        listbox.selection_set(index)
        listbox.see(index)

    def highlighted(self) -> str | None:
        if not self.is_open() or not self._shown:
            return None
        selection = self._listbox.curselection()
        return self._shown[selection[0]] if selection else None

    # ----- choosing ------------------------------------------------------
    def commit(self, value: str) -> None:
        """Choose a listed value, as a click on its row does."""
        changed = value != self._committed or self.get() != value
        self.set(value)
        self._committed = value
        self.close()
        self.icursor("end")
        self.selection_clear()
        if changed:
            self._changed()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()
        self.event_generate("<<ComboboxSelected>>")

    def _settle(self) -> None:
        """Leaving the field: keep an exact item, otherwise restore the last valid one."""
        self.close()
        text = self.get()
        valid = self._valid(text)
        if valid is None and not text.strip() and "None" in self.choices():
            valid = "None"
        if valid is None:
            if self._committed is not None and text != self._committed:
                self.set(self._committed)
                self._changed()
            return
        changed = valid != self._committed
        if text != valid:
            self.set(valid)
        self._committed = valid
        if changed:
            self._changed()

    # ----- events --------------------------------------------------------
    def _pressed(self, event):
        if "disabled" in self.state():
            return "break"
        on_arrow = "arrow" in str(self.identify(event.x, event.y))
        if self.is_open():
            if on_arrow:
                self.close()
                return "break"
            return None  # place the caret in the text
        self.focus_set()
        self.open()
        self.selection_range(0, "end")
        self.icursor("end")
        return "break"

    def _typed(self, event):
        if event.keysym in {"Up", "Down", "Next", "Prior", "Return", "KP_Enter", "Escape", "Tab", "Left", "Right",
                            "Home", "End", "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                            "Caps_Lock", "Win_L", "Win_R"}:
            return None
        if not event.char and event.keysym not in {"BackSpace", "Delete"}:
            return None
        self.open(self.get())
        return None

    def _move(self, step: int):
        if not self.is_open():
            self.open()
            return "break"
        selection = self._listbox.curselection()
        self._highlight((selection[0] if selection else -1) + step)
        return "break"

    def _enter(self, _event):
        if not self.is_open():
            self._settle()
            return None
        value = self.highlighted()
        if value is not None:
            self.commit(value)
        return "break"

    def _leave_by_key(self):
        value = self.highlighted()
        if value is not None and self._valid(self.get()) is None:
            self.commit(value)
        return None

    def _escape(self, _event):
        if not self.is_open():
            return None
        self.close()
        if self._committed is not None:
            self.set(self._committed)
            self.icursor("end")
        return "break"

    def _wheel(self, event):
        if self.is_open():
            self._listbox.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        return None if event.widget is not self else "break"  # never change the value by scrolling

    def _focus_in(self, _event):
        if self._blur_job is not None:
            self.after_cancel(self._blur_job)
            self._blur_job = None
        if not self.is_open():
            self._committed = self._valid(self.get()) or self.get()

    def _focus_out(self, _event):
        if self._blur_job is None:
            self._blur_job = self.after(120, self._blurred)

    def _blurred(self) -> None:
        self._blur_job = None
        try:
            focused = self.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        if focused is not self:
            self._settle()

    def _list_pressed(self, event):
        if self._shown:
            self.commit(self._shown[self._listbox.nearest(event.y)])
            self.focus_set()
        return "break"

    def _clicked_elsewhere(self, event):
        if self.is_open() and event.widget is not self and event.widget is not self._listbox:
            self._settle()

    def _window_moved(self, event):
        if self.is_open() and event.widget is self.winfo_toplevel():
            self.close()

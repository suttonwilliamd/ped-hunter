"""Deliberate off-HUD context view. Import/pure document creation do no native I/O.

Construction is reached only by the existing context action, never automatically
on refresh, diagnostics changes, or theme changes. Native behavior remains QA-gated.
"""
from __future__ import annotations
import tkinter as tk


def context_document(title: str, details: str) -> str:
    """Preserve the complete diagnostic record; do not parse, truncate or reprice."""
    if not isinstance(title, str) or not isinstance(details, str):
        raise TypeError('Context title and details must be text')
    return title + '\n\nStructured diagnostic record\n\n' + details


def show_context(parent, title: str, details: str):
    """One nonmodal, read-only, two-axis-scrollable view per HUD.

    All widgets have explicit parents. No default root, filesystem, clipboard
    automation, external viewer, native discovery or modal grab is involved.
    """
    document = context_document(title, details)
    existing = getattr(parent, '_context_dialog', None)
    if existing is not None and existing.winfo_exists():
        _replace_document(existing._context_text, document)
        existing.deiconify()
        existing.lift()
        existing._context_text.focus_set()
        return existing

    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    parent._context_dialog = dialog
    try:
        return _build_context(dialog, parent, document)
    except Exception:
        # A failed first population must not leave an untracked hidden window.
        # Normal owned-child cleanup only; never close/restart the parent.
        if getattr(parent, '_context_dialog', None) is dialog:
            parent._context_dialog = None
        dialog.destroy()
        raise


def _build_context(dialog, parent, document):
    dialog.title('Current chart context')
    dialog.transient(parent)
    dialog.geometry('780x560')
    dialog.minsize(400, 260)
    dialog.resizable(True, True)
    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)
    text = tk.Text(dialog, wrap='none', undo=False, takefocus=True, font=('Consolas', 10),
                   exportselection=False, padx=10, pady=10)
    vertical = tk.Scrollbar(dialog, orient='vertical', command=text.yview)
    horizontal = tk.Scrollbar(dialog, orient='horizontal', command=text.xview)
    text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    text.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')
    dialog._context_text = text

    def close(event=None):
        if getattr(parent, '_context_dialog', None) is dialog:
            parent._context_dialog = None
        dialog.destroy()
        # Restore the existing HUD's keyboard route, not an application window.
        parent.canvas.focus_set()
        return 'break'

    tk.Button(dialog, text='Close', command=close, takefocus=True).grid(
        row=2, column=0, columnspan=2, pady=8)
    dialog.protocol('WM_DELETE_WINDOW', close)
    dialog.bind('<Escape>', close)
    _replace_document(text, document)
    parent._context_dialog = dialog
    dialog.deiconify()
    text.focus_set()
    return dialog


def _replace_document(text, document):
    text.configure(state='normal')
    try:
        text.delete('1.0', 'end')
        text.insert('1.0', document)
        text.mark_set('insert', '1.0')
        text.xview_moveto(0)
        text.yview_moveto(0)
    finally:
        text.configure(state='disabled')

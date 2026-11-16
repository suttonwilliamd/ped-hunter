"""Loot corrections: leave a loot item out of a run's totals, or count it again.

Paged original loot receipts; editing accounting never resumes a run.
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk


def _when(value):
    from .app import _short_time
    return _short_time(value, seconds=True)


class EventHistoryWindow(tk.Toplevel):
    PAGE_SIZE = 100

    def __init__(self, app):
        super().__init__(app)
        self.app, self.store = app, app.store
        self.title('PED Hunter · Loot corrections')
        self.transient(app)
        scale = app.display_scale
        self.geometry(f'{round(900 * scale)}x{round(580 * scale)}')
        self.minsize(round(640 * scale), round(440 * scale))
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.busy = False
        self._before = None
        self._back = []
        self._rows = {}
        self._has_older = False
        self._job = None
        self.run = tk.StringVar(self)
        self.state_filter = tk.StringVar(self, value='All loot')
        self.event_id = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self._runs = {}
        preferred = app._session_id_value() or app._selected_session_id()
        selected = None
        for record in self.store.list_event_history_sessions():
            try:
                snapshot = json.loads(record['loadout_snapshot'] or '{}')
            except (TypeError, ValueError):
                snapshot = {}
            name = snapshot.get('name', '') if isinstance(snapshot, dict) else ''
            label = f"{_when(record['started_at'])} · {name or 'no setup'}"
            if label in self._runs:
                label += f" · {record['id']}"
            self._runs[label] = record['id']
            if record['id'] == preferred:
                selected = label
        self.run.set(selected or next(iter(self._runs), ''))
        self.configure(bg=app.colors['bg'])
        frame = ttk.Frame(self, style='Root.TFrame', padding=round(8 * scale))
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text='Run').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.run_combo = ttk.Combobox(frame, textvariable=self.run, values=tuple(self._runs), state='readonly')
        self.run_combo.grid(row=0, column=1, columnspan=3, sticky='ew')
        self.run_combo.bind('<<ComboboxSelected>>', lambda _event: self.newest())
        self.filter_combo = ttk.Combobox(frame, textvariable=self.state_filter, state='readonly',
                                         values=('All loot', 'Counted', 'Left out'), width=16)
        self.filter_combo.grid(row=1, column=0, sticky='w', pady=8)
        self.filter_combo.bind('<<ComboboxSelected>>', lambda _event: self.newest())
        # Events > "Don't count this loot…" preselects an item by its id; the id is not shown.
        self.id_entry = ttk.Entry(frame, textvariable=self.event_id, width=16)
        self.id_entry.bind('<Return>', lambda _event: self.find_event())
        self.find_button = ttk.Button(frame, text='Find', command=self.find_event)
        ttk.Label(frame, text="Leave a loot item out of this run's totals, or count it again. The item stays stored, "
                  'tracking keeps running, and costs and skills are not affected.',
                  style='Muted.TLabel', wraplength=round(860 * scale)).grid(row=2, column=0, columnspan=4, sticky='w', pady=(0, 6))
        table = ttk.Frame(frame, style='Root.TFrame')
        table.grid(row=3, column=0, columnspan=4, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('id', 'time', 'item', 'value', 'state'), show='headings', selectmode='browse')
        for name, title, width in [('id', 'ID', 70), ('time', 'Time', 150),
                                   ('item', 'Item', 260), ('value', 'Value', 120), ('state', 'In totals', 100)]:
            self.tree.heading(name, text=title, anchor='w')
            self.tree.column(name, width=round(width * scale), minwidth=round(55 * scale), stretch=name == 'item')
        self.tree.configure(displaycolumns=('time', 'item', 'value', 'state'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self._selection_changed())
        self.details = tk.Text(frame, height=2, wrap='word', state='disabled', relief='flat', padx=6, pady=4,
                               font=app._ui_font(9), bg=app.colors['panel_2'], fg=app.colors['text'])
        self.details.grid(row=4, column=0, columnspan=4, sticky='ew', pady=8)
        navigation = ttk.Frame(frame, style='Root.TFrame')
        navigation.grid(row=5, column=0, columnspan=4, sticky='ew')
        navigation.columnconfigure(3, weight=1)
        self.newest_button = ttk.Button(navigation, text='Newest', command=self.newest)
        self.newer_button = ttk.Button(navigation, text='◂ Newer', command=self.newer)
        self.older_button = ttk.Button(navigation, text='Older ▸', command=self.older)
        for col, button in enumerate((self.newest_button, self.newer_button, self.older_button)):
            button.grid(row=0, column=col, padx=(0, 5))
        self.exclude_button = ttk.Button(navigation, text='Leave out of totals', style='Accent.TButton', command=lambda: self.change_selected(True))
        self.restore_button = ttk.Button(navigation, text='Count again', command=lambda: self.change_selected(False))
        self.exclude_button.grid(row=0, column=4, padx=5)
        self.restore_button.grid(row=0, column=5)
        ttk.Label(frame, textvariable=self.status, wraplength=round(620 * scale)).grid(
            row=6, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self._load_page()

    def select_run(self, session_id):
        """Show this run's loot (used when opened from the Events tab)."""
        label = next((label for label, sid in self._runs.items() if sid == session_id), None)
        if label is not None and label != self.run.get() and not self.busy:
            self.run.set(label)
            self.newest()

    def _session_id(self):
        return self._runs.get(self.run.get())

    def _filter(self):
        return {'All loot': None, 'Counted': False, 'Left out': True}[self.state_filter.get()]

    @staticmethod
    def _payload(row):
        try:
            value = json.loads(row['payload'])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _value(self, payload):
        """The item's TT value in the display currency (enough decimals for tiny loot)."""
        try:
            return self.app._money(float(payload.get('value')), decimals=4)
        except (TypeError, ValueError):
            return '?'

    def _load_page(self, selected=None):
        sid = self._session_id()
        rows = self.store.loot_event_history(sid, before_id=self._before, excluded=self._filter(),
                                              limit=self.PAGE_SIZE + 1) if sid else []
        self._has_older = len(rows) > self.PAGE_SIZE
        self._rows = {str(row['id']): row for row in rows[:self.PAGE_SIZE]}
        self.tree.delete(*self.tree.get_children())
        for key, row in self._rows.items():
            payload = self._payload(row)
            self.tree.insert('', 'end', iid=key, values=(row['id'], _when(row['timestamp']),
                payload.get('item_name', 'Unknown item'), self._value(payload),
                'Left out' if row['excluded'] else 'Counted'))
        if selected is not None and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))
        self.status.set(f'{len(self._rows)} loot items shown, newest first. Older ▸ shows earlier ones.')
        self._selection_changed()

    def newest(self):
        if self.busy:
            return
        self._before, self._back = None, []
        self._load_page()

    def older(self):
        if self.busy or not self._has_older:
            return
        self._back.append(self._before)
        self._before = min(row['id'] for row in self._rows.values())
        self._load_page()

    def newer(self):
        if self.busy or not self._back:
            return
        self._before = self._back.pop()
        self._load_page()

    def find_event(self):
        if self.busy:
            return
        try:
            event_id = int(self.event_id.get())
        except ValueError:
            self.status.set('That loot item could not be found.')
            return
        rows = self.store.loot_event_history(self._session_id(), event_id=event_id)
        if not rows:
            self.status.set('That loot item is not in the selected run. Nothing changed.')
            return
        self.state_filter.set('All loot')
        self._before, self._back = event_id + 1, []
        self._load_page(event_id)
        self.tree.see(str(event_id))

    def _selection_changed(self):
        selection = self.tree.selection()
        row = self._rows.get(selection[0]) if selection else None
        self.exclude_button.configure(state='normal' if row and not row['excluded'] and not self.busy else 'disabled')
        self.restore_button.configure(state='normal' if row and row['excluded'] and not self.busy else 'disabled')
        self.older_button.configure(state='normal' if self._has_older and not self.busy else 'disabled')
        self.newer_button.configure(state='normal' if self._back and not self.busy else 'disabled')
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        if row:
            self.details.insert('1.0', f"Chat log line:\n{row['raw_message']}")
        self.details.configure(state='disabled')

    def change_selected(self, excluded):
        if self.busy:
            return
        selection = self.tree.selection()
        row = self._rows.get(selection[0]) if selection else None
        if row is None:
            return
        sid, event_id = self._session_id(), row['id']
        payload = self._payload(row)
        item = f"{payload.get('item_name', 'Unknown item')} · {self._value(payload)}"
        question = (f"Leave {item} out of this run's totals?" if excluded
                    else f"Count {item} in this run's totals again?")
        after = 'You can count it again any time.' if excluded else 'Nothing else changes.'
        if not messagebox.askyesno('Leave out of totals?' if excluded else 'Count again?',
                                   f"{question}\n{_when(row['timestamp'])}\n\n"
                                   'Loot, net, return and the charts are recalculated for this run '
                                   f'and your lifetime totals. {after}', parent=self):
            return
        self.busy = True
        for control in (self.run_combo, self.filter_combo, self.id_entry, self.find_button, self.newest_button):
            control.configure(state='disabled')
        self._selection_changed()
        self.status.set('Recalculating this run; tracking keeps running…')
        result = self._job = queue.Queue(maxsize=1)
        store = self.store

        def rebuild():
            try:
                result.put(store.set_loot_excluded(sid, event_id, excluded))
            except Exception as exc:
                result.put(exc)

        threading.Thread(target=rebuild, daemon=True, name='ped-hunter-history-edit').start()
        self.after(25, lambda: self._poll(result, sid, event_id, excluded))

    def _poll(self, result, sid, event_id, excluded):
        try:
            outcome = result.get_nowait()
        except queue.Empty:
            self.after(25, lambda: self._poll(result, sid, event_id, excluded))
            return
        self.busy = False
        self._job = None
        self.run_combo.configure(state='readonly')
        self.filter_combo.configure(state='readonly')
        for control in (self.id_entry, self.find_button, self.newest_button):
            control.configure(state='normal')
        self._load_page(event_id)
        if isinstance(outcome, Exception):
            self.status.set(f'The change could not be made: {outcome}')
            messagebox.showerror('Loot correction failed', str(outcome), parent=self)
            return
        try:
            self.app._history_accounting_changed(sid)
        except Exception as exc:
            self.status.set(f'The change was saved, but the display could not refresh: {exc}')
            raise
        outcome_text = 'left out of the totals' if excluded else 'counted in the totals again'
        self.status.set(f'That item is now {outcome_text}. Nothing was deleted.')

    def _close(self):
        if self.busy:
            self.status.set('Finishing the change; this window can close in a moment.')
        else:
            self.destroy()

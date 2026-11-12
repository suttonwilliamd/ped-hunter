"""Paged original loot receipts; editing accounting never resumes a run."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk


class EventHistoryWindow(tk.Toplevel):
    PAGE_SIZE = 100

    def __init__(self, app):
        super().__init__(app)
        self.app, self.store = app, app.store
        self.title('PED Hunter · Stored loot history')
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
        self.state_filter = tk.StringVar(self, value='All receipts')
        self.event_id = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self._runs = {}
        preferred = app._selected_session_id() or app._session_id_value()
        selected = None
        for record in self.store.list_event_history_sessions():
            try:
                snapshot = json.loads(record['loadout_snapshot'] or '{}')
            except (TypeError, ValueError):
                snapshot = {}
            name = snapshot.get('name', '') if isinstance(snapshot, dict) else ''
            label = f"{record['started_at']} · {name} · {record['id']}"
            self._runs[label] = record['id']
            if record['id'] == preferred:
                selected = label
        self.run.set(selected or next(iter(self._runs), ''))
        frame = ttk.Frame(self, style='Root.TFrame', padding=10)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text='Stored run').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.run_combo = ttk.Combobox(frame, textvariable=self.run, values=tuple(self._runs), state='readonly')
        self.run_combo.grid(row=0, column=1, columnspan=3, sticky='ew')
        self.run_combo.bind('<<ComboboxSelected>>', lambda _event: self.newest())
        self.filter_combo = ttk.Combobox(frame, textvariable=self.state_filter, state='readonly',
                                         values=('All receipts', 'Included', 'Excluded'), width=16)
        self.filter_combo.grid(row=1, column=0, sticky='w', pady=8)
        self.filter_combo.bind('<<ComboboxSelected>>', lambda _event: self.newest())
        ttk.Label(frame, text='Event ID').grid(row=1, column=1, sticky='e', padx=6)
        self.id_entry = ttk.Entry(frame, textvariable=self.event_id, width=16)
        self.id_entry.grid(row=1, column=2, sticky='ew')
        self.id_entry.bind('<Return>', lambda _event: self.find_event())
        self.find_button = ttk.Button(frame, text='Find', command=self.find_event)
        self.find_button.grid(row=1, column=3, padx=(6, 0))
        ttk.Label(frame, text='Original TT values in PED. Exclusion changes accounting, not log files or equipment.',
                  wraplength=round(600 * scale)).grid(row=2, column=0, columnspan=4, sticky='w', pady=(0, 6))
        table = ttk.Frame(frame)
        table.grid(row=3, column=0, columnspan=4, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('id', 'time', 'item', 'value', 'state'), show='headings', selectmode='browse')
        for name, title, width in [('id', 'Event ID', 85), ('time', 'Timestamp', 170),
                                   ('item', 'Item', 260), ('value', 'Value (TT PED)', 120), ('state', 'Accounting', 100)]:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=round(width * scale), minwidth=round(55 * scale), stretch=name == 'item')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self._selection_changed())
        self.details = tk.Text(frame, height=4, wrap='word', state='disabled',
                               font=app._ui_font(9), bg=app.colors['panel_2'], fg=app.colors['text'])
        self.details.grid(row=4, column=0, columnspan=4, sticky='ew', pady=8)
        navigation = ttk.Frame(frame)
        navigation.grid(row=5, column=0, columnspan=4, sticky='ew')
        navigation.columnconfigure(3, weight=1)
        self.newest_button = ttk.Button(navigation, text='Newest', command=self.newest)
        self.newer_button = ttk.Button(navigation, text='Newer', command=self.newer)
        self.older_button = ttk.Button(navigation, text='Older', command=self.older)
        for col, button in enumerate((self.newest_button, self.newer_button, self.older_button)):
            button.grid(row=0, column=col, padx=(0, 5))
        self.exclude_button = ttk.Button(navigation, text='Exclude selected', command=lambda: self.change_selected(True))
        self.restore_button = ttk.Button(navigation, text='Restore selected', command=lambda: self.change_selected(False))
        self.exclude_button.grid(row=0, column=4, padx=5)
        self.restore_button.grid(row=0, column=5)
        ttk.Label(frame, textvariable=self.status, wraplength=round(620 * scale)).grid(
            row=6, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self._load_page()

    def _session_id(self):
        return self._runs.get(self.run.get())

    def _filter(self):
        return {'All receipts': None, 'Included': False, 'Excluded': True}[self.state_filter.get()]

    @staticmethod
    def _payload(row):
        try:
            value = json.loads(row['payload'])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _load_page(self, selected=None):
        sid = self._session_id()
        rows = self.store.loot_event_history(sid, before_id=self._before, excluded=self._filter(),
                                              limit=self.PAGE_SIZE + 1) if sid else []
        self._has_older = len(rows) > self.PAGE_SIZE
        self._rows = {str(row['id']): row for row in rows[:self.PAGE_SIZE]}
        self.tree.delete(*self.tree.get_children())
        for key, row in self._rows.items():
            payload = self._payload(row)
            self.tree.insert('', 'end', iid=key, values=(row['id'], row['timestamp'] or '',
                payload.get('item_name', 'Unknown receipt'), payload.get('value', '?'),
                'Excluded' if row['excluded'] else 'Included'))
        if selected is not None and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))
        self.status.set(f'{len(self._rows)} receipts shown · originals retained · use Older or an exact event ID')
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
            self.status.set('Enter a numeric event ID from the selected stored run.')
            return
        rows = self.store.loot_event_history(self._session_id(), event_id=event_id)
        if not rows:
            self.status.set('That loot event is not retained in the selected run. Nothing changed.')
            return
        self.state_filter.set('All receipts')
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
            self.details.insert('1.0', f"Original event {row['id']} · {self._session_id()}\n"
                                f"{row['raw_message']}\nOriginal payload: {row['payload']}")
        self.details.configure(state='disabled')

    def change_selected(self, excluded):
        if self.busy:
            return
        selection = self.tree.selection()
        row = self._rows.get(selection[0]) if selection else None
        if row is None:
            return
        sid, event_id = self._session_id(), row['id']
        verb = 'Exclude' if excluded else 'Restore'
        payload = self._payload(row)
        if not messagebox.askyesno(f'{verb} loot receipt?',
            f"{verb} event {event_id} in {sid}?\n{row['timestamp'] or ''}\n"
            f"{payload.get('item_name', 'Unknown receipt')} · {payload.get('value', '?')} TT PED\n\n"
            'Original data is retained. Run totals, charts and attribution will be rebuilt. '
            'Fan observations are retained separately for each exclusion set. Tracking is not restarted.', parent=self):
            return
        self.busy = True
        for control in (self.run_combo, self.filter_combo, self.id_entry, self.find_button, self.newest_button):
            control.configure(state='disabled')
        self._selection_changed()
        self.status.set('Rebuilding accounting and saved projections; ingestion continues unchanged…')
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
            self.status.set(f'History edit failed: {outcome}')
            messagebox.showerror('History edit failed', str(outcome), parent=self)
            return
        try:
            self.app._history_accounting_changed(sid)
        except Exception as exc:
            self.status.set(f'Receipt changed, but display refresh failed: {exc}')
            raise
        self.status.set(f"Event {event_id} {'excluded' if excluded else 'restored'}; original retained, projections rebuilt.")

    def _close(self):
        if self.busy:
            self.status.set('Wait for the atomic history edit to finish before closing this window.')
        else:
            self.destroy()

"""Встроенный Tkinter-интерфейс Terminal Simulator для систем без Qt."""
import json
import queue
import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

from core import Session, tok
from process_manager import ProcessManager
from session_store import (DEFAULT_TTL_HOURS, BackgroundCleanupWorker, SessionStore,
                           session_label)

STATE = Path.home() / ".terminal_sim" / "state.json"
DB_STATE = Path.home() / ".terminal_sim" / "sessions.sqlite3"
THEMES = {
    "ps": {
        "bg": "#0c0c0c", "fg": "#cccccc", "bar": "#202020",
        "colors": {"out": "#cccccc", "ok": "#16c60c", "info": "#3a96dd", "warn": "#f9a825",
                   "err": "#e74856", "dim": "#767676", "head": "#61d6d6", "ts": "#f9f1a5",
                   "grid": "#767676", "bprompt": "#cccccc", "dir": "#3b78ff"},
    },
    "bash": {
        "bg": "#1b1d23", "fg": "#d7dae0", "bar": "#2b2d33",
        "colors": {"out": "#d7dae0", "ok": "#6fdc8c", "info": "#82aaff", "warn": "#ffcb6b",
                   "err": "#ff6b6b", "dim": "#8a8f99", "head": "#7fdbca", "ts": "#d7dae0",
                   "grid": "#8a8f99", "bprompt": "#82aaff", "dir": "#82aaff"},
    },
}


class TerminalSimulator:
    def __init__(self, root):
        self.root = root
        self.root.title("Terminal Simulator")
        self.root.geometry("1000x640")
        self.root.minsize(520, 320)
        self.time_scale = 0.25
        self.active_id = None
        self.history_index, self.generators, self.busy, self.dead, self.bar_marks = {}, {}, {}, {}, {}
        self.task_ids = {}
        self.process_manager = ProcessManager()
        self.bar_mark_counter = 0
        self.font_size = 12

        saved = self.load_state()
        self.store = SessionStore(DB_STATE)
        self.store.migrate_json(saved)
        records = self.store.list_sessions(opened=True)
        if not records:
            closed = self.store.list_sessions()
            if closed:
                self.store.set_open(closed[-1]["id"], True, 0)
                records = self.store.list_sessions(opened=True)
            else:
                self.store.create(Session("ps"), session_type="temporary")
                records = self.store.list_sessions(opened=True)
        self.sessions = {}
        self.records = {row["id"]: row for row in records}
        for row in records:
            try:
                session = Session.from_dict(self.store.load_state(row["id"]))
            except (KeyError, TypeError, ValueError):
                session = Session(row["shell_profile"])
            sid = row["id"]
            self.sessions[sid] = session
            self.history_index[sid] = None
            self.generators[sid] = None
            self.busy[sid] = self.dead[sid] = False
            self.bar_marks[sid] = {}
            self.task_ids[sid] = None
            self.task_ids[sid] = None

        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="Новая временная вкладка (Ctrl+T)", command=self.new_temporary)
        self.menu.add_command(label="Новая постоянная вкладка (Ctrl+Shift+N)", command=self.new_permanent)
        self.menu.add_command(label="Восстановить закрытую вкладку (Ctrl+Shift+T)", command=self.restore_closed)
        self.menu.add_command(label="Закрыть вкладку (Ctrl+W)", command=self.close_tab)
        self.menu.add_command(label="Палитра команд (Ctrl+Shift+P)", command=self.command_palette)
        self.menu.add_command(label="Сохранить сессии (Ctrl+Shift+S)", command=self.save)
        self.menu.add_command(label="Журнал очистки сессий", command=self.show_cleanup_history)
        self.menu.add_separator()
        self.menu.add_command(label="Скорость сети (МБ/с)…", command=self.ask_speed)
        self.menu.add_command(label="Скорость симуляции…", command=self.ask_scale)
        self.menu.add_command(label="Шрифт крупнее (Ctrl+=)", command=lambda: self.zoom(1))
        self.menu.add_command(label="Шрифт мельче (Ctrl+-)", command=lambda: self.zoom(-1))
        self.menu.add_separator()
        self.menu.add_command(label="Сбросить текущую сессию", command=self.reset_session)

        self.header = tk.Frame(root, bg="#202020", height=36)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        tk.Button(self.header, text="☰", command=self.show_menu, relief="flat",
                  bg="#202020", fg="#ffffff", activebackground="#3a3a3a",
                  activeforeground="#ffffff").pack(side="left", padx=8)
        self.title = tk.Label(self.header, anchor="w", bg="#202020", fg="#ffffff",
                              font=("Segoe UI", 10))
        self.title.pack(side="left", fill="x", expand=True)
        self.body = tk.Frame(root)
        self.body.pack(fill="both", expand=True)
        self.tabbar = tk.Frame(self.body, bg="#202020")
        self.tabbar.pack(fill="x")
        self.tab_buttons = {}
        self.tab_frames = {}
        self.tab_order = []
        self.drag_origin = None
        tk.Button(self.tabbar, text="+", command=self.new_temporary, relief="flat",
                  bg="#303030", fg="#ffffff", activebackground="#484848",
                  activeforeground="#ffffff").pack(side="right", padx=4, pady=3)
        self.widgets = {}
        self.input_starts = {}
        for sid, session in self.sessions.items():
            kind = session.kind
            theme = THEMES[kind]
            terminal = tk.Text(self.body, wrap="none", undo=False, padx=10, pady=10,
                               bg=theme["bg"], fg=theme["fg"], insertbackground=theme["fg"],
                               selectbackground="#264f78", relief="flat",
                               font=("Consolas", self.font_size))
            for style, color in theme["colors"].items():
                terminal.tag_configure(style, foreground=color)
            terminal.bind("<KeyPress>", lambda event, session_id=sid: self.on_key(event, session_id))
            terminal.bind("<<Paste>>", lambda event, session_id=sid: self.on_paste(event, session_id))
            self.widgets[sid] = terminal
            self.render_banner(sid)
            self.add_tab_button(sid)
        self.switch(next(iter(self.sessions)))
        self.root.after(60000, self.refresh_tab_labels)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-t>", lambda _event: self.new_temporary_event())
        self.root.bind("<Control-w>", lambda _event: self.close_tab_event())
        self.root.bind("<Control-Shift-T>", lambda _event: self.restore_closed_event())
        self.root.bind("<Control-Shift-N>", lambda _event: self.new_permanent_event())
        self.root.bind("<Control-Shift-P>", lambda _event: self.command_palette_event())
        self.root.bind("<Control-Shift-S>", lambda _event: self.save_event())
        self.root.bind("<Control-Tab>", lambda _event: self.next_tab(1))
        self.root.bind("<Control-Shift-Tab>", lambda _event: self.next_tab(-1))
        for number in range(1, 10):
            self.root.bind("<Control-%d>" % number, lambda _event, i=number - 1: self.select_tab(i))
        self.root.bind("<Control-equal>", lambda _event: self.zoom(1))
        self.root.bind("<Control-minus>", lambda _event: self.zoom(-1))
        self.root.bind("<Control-c>", self.copy_or_cancel)
        self.cleanup_worker = BackgroundCleanupWorker(self.store)
        self.cleanup_worker.start()
        self.root.after(500, self.process_cleanup_results)

    def load_state(self):
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            messagebox.showwarning("Состояние сессии", "Не удалось загрузить сохранённое состояние:\n%s" % error)
            return {}

    def show_menu(self):
        self.menu.tk_popup(self.root.winfo_rootx() + 8, self.root.winfo_rooty() + 36)
        self.menu.grab_release()

    def add_tab_button(self, session_id):
        record = self.records[session_id]
        frame = tk.Frame(self.tabbar, bg="#303030")
        name = tk.Button(frame, text=self.tab_text(session_id), command=lambda sid=session_id: self.switch(sid),
                         relief="flat", bg="#303030", fg="#ffffff", activebackground="#484848",
                         activeforeground="#ffffff", padx=8)
        name.pack(side="left")
        close = tk.Button(frame, text="×", command=lambda sid=session_id: self.close_session_tab(sid),
                          relief="flat", bg="#303030", fg="#bbbbbb", activebackground="#8b3030",
                          activeforeground="#ffffff", padx=5)
        close.pack(side="left")
        frame.pack(side="left", padx=2, pady=3)
        name.bind("<ButtonPress-1>", lambda event, sid=session_id: self.begin_tab_drag(event, sid))
        name.bind("<B1-Motion>", lambda event, sid=session_id: self.drag_tab(event, sid))
        frame.bind("<Button-3>", lambda event, sid=session_id: self.tab_menu(event, sid))
        name.bind("<Button-3>", lambda event, sid=session_id: self.tab_menu(event, sid))
        self.tab_frames[session_id] = frame
        self.tab_buttons[session_id] = name
        self.tab_order.append(session_id)

    def begin_tab_drag(self, event, session_id):
        self.drag_origin = (session_id, event.x_root)

    def drag_tab(self, event, session_id):
        if not self.drag_origin or self.drag_origin[0] != session_id:
            return
        frame = self.tab_frames[session_id]
        target = self.tabbar.winfo_containing(event.x_root, event.y_root)
        while target is not None and target not in self.tab_frames.values():
            target = target.master
        if target is None or target == frame:
            return
        target_id = next((sid for sid, candidate in self.tab_frames.items() if candidate == target), None)
        if target_id is None:
            return
        old = self.tab_order.index(session_id)
        new = self.tab_order.index(target_id)
        self.tab_order.pop(old)
        self.tab_order.insert(new, session_id)
        original = {sid: index for index, sid in enumerate(self.tab_order)}
        self.tab_order.sort(key=lambda sid: (not self.records[sid].get("is_pinned", 0), original[sid]))
        for sid in self.tab_order:
            self.tab_frames[sid].pack_forget()
            self.tab_frames[sid].pack(side="left", padx=2, pady=3)
        for index, sid in enumerate(self.tab_order):
            self.store.set_open(sid, True, index)
        self.drag_origin = (session_id, event.x_root)

    def tab_menu(self, event, session_id):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Переименовать", command=lambda: self.rename_session(session_id))
        row = self.records[session_id]
        if row["session_type"] == "temporary":
            menu.add_command(label="Сделать постоянной", command=lambda: self.make_permanent(session_id))
            menu.add_command(label="Продлить на 12 часов", command=lambda: self.extend_session(session_id, 12))
            menu.add_command(label="Отключить автоудаление",
                             command=lambda: self.set_auto_cleanup(session_id, False))
        else:
            menu.add_command(label="Открепить вкладку" if row.get("is_pinned") else "Закрепить вкладку",
                             command=lambda: self.pin_session(session_id, not bool(row.get("is_pinned"))))
        menu.add_command(label="Дублировать в новую временную сессию",
                         command=lambda: self.duplicate_session(session_id))
        menu.add_command(label="Закрыть вкладку", command=lambda: self.close_session_tab(session_id))
        menu.add_separator()
        menu.add_command(label="Удалить сессию и виртуальные данные…",
                         command=lambda: self.delete_session(session_id))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _open_record(self, row):
        sid = row["id"]
        session = Session.from_dict(self.store.load_state(sid))
        self.records[sid] = row
        self.sessions[sid] = session
        self.history_index[sid] = None
        self.generators[sid] = None
        self.busy[sid] = self.dead[sid] = False
        self.bar_marks[sid] = {}
        theme = THEMES[session.kind]
        terminal = tk.Text(self.body, wrap="none", undo=False, padx=10, pady=10,
                           bg=theme["bg"], fg=theme["fg"], insertbackground=theme["fg"],
                           selectbackground="#264f78", relief="flat",
                           font=("Consolas", self.font_size))
        for style, color in theme["colors"].items():
            terminal.tag_configure(style, foreground=color)
        terminal.bind("<KeyPress>", lambda event, session_id=sid: self.on_key(event, session_id))
        terminal.bind("<<Paste>>", lambda event, session_id=sid: self.on_paste(event, session_id))
        self.widgets[sid] = terminal
        self.add_tab_button(sid)
        self.render_banner(sid)
        self.switch(sid)

    def create_session(self, session_type):
        active = self.sessions[self.active_id]
        session = Session(active.kind)
        name = simpledialog.askstring("Новая сессия", "Имя сессии:",
                                      initialvalue=("PowerShell" if active.kind == "ps" else "Ubuntu"),
                                      parent=self.root)
        if name is None:
            return
        name = name.strip() or ("PowerShell" if active.kind == "ps" else "Ubuntu")
        ttl = DEFAULT_TTL_HOURS
        if session_type == "temporary":
            ttl = simpledialog.askfloat(
                "Срок временной сессии",
                "Срок в часах (1, 6, 12, 24, 168 или свой срок; 0 — без автоудаления):",
                initialvalue=DEFAULT_TTL_HOURS, minvalue=0, maxvalue=87600, parent=self.root)
            if ttl is None:
                return
        sid = self.store.create(session, name=name, session_type=session_type,
                                ttl_hours=ttl)
        self.store.set_open(sid, True, len(self.tab_order))
        row = next(item for item in self.store.list_sessions(opened=True) if item["id"] == sid)
        self._open_record(row)
        return True

    def create_session_record(self, session_type, name=None, ttl_hours=DEFAULT_TTL_HOURS):
        kind = self.sessions[self.active_id].kind if self.active_id else "ps"
        name = name or ("PowerShell" if kind == "ps" else "Ubuntu")
        sid = self.store.create(Session(kind), name=name, session_type=session_type,
                                ttl_hours=ttl_hours)
        self.store.set_open(sid, True, len(self.tab_order))
        row = next(item for item in self.store.list_sessions(opened=True) if item["id"] == sid)
        self._open_record(row)
        return sid

    def new_temporary(self):
        self.create_session("temporary")

    def new_permanent(self):
        self.create_session("permanent")

    def new_temporary_event(self):
        self.new_temporary()
        return "break"

    def new_permanent_event(self):
        self.new_permanent()
        return "break"

    def restore_closed(self):
        self.cleanup_expired(show_message=False, schedule=False)
        rows = self.store.list_sessions(opened=False)
        if not rows:
            self.root.bell()
            return
        row = rows[-1]
        self.store.set_open(row["id"], True, len(self.tab_order))
        row["is_open"] = 1
        self._open_record(row)

    def restore_closed_event(self):
        self.restore_closed()
        return "break"

    def command_palette(self):
        command = simpledialog.askstring(
            "Палитра команд",
            "Команда приложения (например: session list):",
            parent=self.root)
        if command and not self.run_session_command(self.active_id, command):
            messagebox.showinfo("Палитра команд",
                                "Поддерживаются команды session create/list/use/extend/"
                                "make-permanent/delete/cleanup.",
                                parent=self.root)

    def command_palette_event(self):
        self.command_palette()
        return "break"

    def save_event(self):
        self.save()
        return "break"

    def select_tab(self, index):
        if index < len(self.tab_order):
            self.switch(self.tab_order[index])
        return "break"

    def next_tab(self, direction):
        if not self.tab_order:
            return "break"
        index = self.tab_order.index(self.active_id)
        self.switch(self.tab_order[(index + direction) % len(self.tab_order)])
        return "break"

    def close_tab_event(self):
        self.close_tab()
        return "break"

    def close_tab(self):
        self.close_session_tab(self.active_id)

    def close_session_tab(self, session_id, persist=True):
        previously_active = self.active_id
        closed_index = self.tab_order.index(session_id)
        if self.busy.get(session_id):
            if not messagebox.askyesno("Команда выполняется",
                                       "Остановить виртуальную команду и закрыть вкладку?",
                                       parent=self.root):
                return
            generator = self.generators.get(session_id)
            if generator is not None:
                generator.close()
            task_id = self.task_ids.get(session_id)
            if task_id is not None:
                self.process_manager.finish(task_id)
                self.task_ids[session_id] = None
            self.busy[session_id] = False
        if persist:
            self.store.save(session_id, self.sessions[session_id])
            self.store.set_open(session_id, False)
        self.tab_order.remove(session_id)
        self.tab_frames.pop(session_id).destroy()
        self.widgets.pop(session_id).destroy()
        self.tab_buttons.pop(session_id, None)
        self.sessions.pop(session_id)
        self.records.pop(session_id)
        self.history_index.pop(session_id)
        self.generators.pop(session_id)
        self.busy.pop(session_id)
        self.task_ids.pop(session_id)
        self.dead.pop(session_id)
        self.bar_marks.pop(session_id)
        if not self.tab_order:
            self.active_id = None
            self.store.create(Session("ps"), session_type="temporary")
            row = self.store.list_sessions(opened=True)[-1]
            self._open_record(row)
        else:
            if previously_active in self.sessions:
                self.switch(previously_active)
            else:
                self.switch(self.tab_order[min(closed_index, len(self.tab_order) - 1)])
        for index, sid in enumerate(self.tab_order):
            self.store.set_open(sid, True, index)

    def rename_session(self, session_id):
        value = simpledialog.askstring("Переименовать", "Новое имя:",
                                       initialvalue=self.records[session_id]["name"], parent=self.root)
        if value is None:
            return
        try:
            self.store.rename(session_id, value)
        except ValueError as error:
            messagebox.showerror("Имя сессии", str(error), parent=self.root)
            return
        self.records[session_id]["name"] = value.strip()
        self.tab_buttons[session_id].configure(text=self.tab_text(session_id))
        self.refresh_title()
        self.refresh_tab_labels(schedule=False)

    def make_permanent(self, session_id):
        self.store.make_permanent(session_id)
        self.records[session_id].update(session_type="permanent", expires_at=None)
        self.refresh_tab_labels(schedule=False)
        self.refresh_title()

    def pin_session(self, session_id, pinned):
        try:
            self.store.set_pinned(session_id, pinned)
        except ValueError as error:
            messagebox.showerror("Закрепление вкладки", str(error), parent=self.root)
            return
        self.records[session_id]["is_pinned"] = int(pinned)
        original = {sid: index for index, sid in enumerate(self.tab_order)}
        self.tab_order.sort(key=lambda sid: (not self.records[sid].get("is_pinned", 0), original[sid]))
        for index, sid in enumerate(self.tab_order):
            self.tab_frames[sid].pack_forget()
            self.tab_frames[sid].pack(side="left", padx=2, pady=3)
            self.store.set_open(sid, True, index)
        self.refresh_tab_labels(schedule=False)
        self.refresh_title()

    def duplicate_session(self, session_id):
        source = self.sessions.get(session_id)
        if source is None:
            source = Session.from_dict(self.store.load_state(session_id))
        duplicate = Session.from_dict(json.loads(json.dumps(source.to_dict(), ensure_ascii=False)))
        name = self.records[session_id]["name"] + " copy"
        new_id = self.store.create(duplicate, name=name, session_type="temporary",
                                   ttl_hours=DEFAULT_TTL_HOURS)
        self.store.set_open(new_id, True, len(self.tab_order))
        row = next(item for item in self.store.list_sessions(opened=True) if item["id"] == new_id)
        self._open_record(row)

    def set_auto_cleanup(self, session_id, enabled):
        self.store.set_auto_cleanup(session_id, enabled)
        self.records[session_id]["auto_cleanup_enabled"] = int(enabled)
        self.records[session_id]["expires_at"] = next(
            row["expires_at"] for row in self.store.list_sessions() if row["id"] == session_id)
        self.refresh_tab_labels(schedule=False)
        self.refresh_title()

    def extend_session(self, session_id, hours):
        self.store.extend(session_id, hours)
        self.records[session_id]["expires_at"] = next(
            row["expires_at"] for row in self.store.list_sessions() if row["id"] == session_id)
        self.refresh_tab_labels(schedule=False)
        self.refresh_title()

    def delete_session(self, session_id, confirm=True):
        row = self.records[session_id]
        if confirm and not messagebox.askyesno("Удалить сессию", "Удалить сессию «%s» и её виртуальные данные?" %
                                               row["name"], parent=self.root):
            return False
        if session_id in self.sessions:
            if self.busy.get(session_id):
                if not messagebox.askyesno(
                        "Команда выполняется", "Остановить виртуальную команду и удалить сессию?",
                        parent=self.root):
                    return False
                generator = self.generators.get(session_id)
                if generator is not None:
                    generator.close()
                self.busy[session_id] = False
                task_id = self.task_ids.get(session_id)
                if task_id is not None:
                    self.process_manager.finish(task_id)
                    self.task_ids[session_id] = None
            try:
                self.store.save(session_id, self.sessions[session_id])
            except KeyError as error:
                messagebox.showerror("Удаление сессии", str(error), parent=self.root)
                return False
            except sqlite3.Error as error:
                messagebox.showerror("Удаление сессии",
                                     "Ошибка базы данных при сохранении:\n%s" % error,
                                     parent=self.root)
                return False
        try:
            deleted = self.store.delete(session_id)
        except OSError as error:
            messagebox.showerror("Удаление сессии",
                                 "Сессия не удалена полностью:\n%s" % error, parent=self.root)
            return False
        except sqlite3.Error as error:
            messagebox.showerror("Удаление сессии",
                                 "Ошибка базы данных при удалении:\n%s" % error, parent=self.root)
            return False
        if not deleted:
            messagebox.showerror("Удаление сессии", "Сессия не найдена в базе данных.",
                                 parent=self.root)
            return False
        if session_id in self.sessions:
            self.close_session_tab(session_id, persist=False)
        else:
            self.records.pop(session_id, None)
        if self.active_id is None:
            self.store.create(Session("ps"), session_type="temporary")
            self._open_record(self.store.list_sessions(opened=True)[-1])
        return True

    def cleanup_expired(self, show_message=True, schedule=True):
        self.cleanup_worker.request_check()
        return []

    def process_cleanup_results(self):
        while True:
            try:
                removed, errors, worker_error = self.cleanup_worker.results.get_nowait()
            except queue.Empty:
                break
            for session_id, error in errors:
                if session_id in self.records:
                    self.records[session_id]["cleanup_status"] = "failed"
                messagebox.showerror("Ошибка автоочистки",
                                     "Сессия %s не очищена:\n%s" % (session_id, error),
                                     parent=self.root)
            finalized = []
            for session_id in removed:
                try:
                    if self.store.finalize_cleanup(session_id):
                        finalized.append(session_id)
                        if session_id in self.sessions:
                            self.expire_session_ui(session_id)
                except (OSError, ValueError) as error:
                    messagebox.showerror(
                        "Ошибка завершения очистки",
                        "Сессия %s: %s" % (session_id, error), parent=self.root)
            if finalized:
                messagebox.showinfo("Очистка сессий",
                                    "Удалено просроченных временных сессий: %d" % len(finalized),
                                    parent=self.root)
            if worker_error and not errors:
                messagebox.showerror("Фоновая очистка", worker_error, parent=self.root)
        self.root.after(500, self.process_cleanup_results)

    def expire_session_ui(self, session_id):
        if session_id not in self.sessions:
            return
        if self.generators[session_id] is not None:
            self.generators[session_id].close()
        task_id = self.task_ids.get(session_id)
        if task_id is not None:
            self.process_manager.finish(task_id)
        closed_index = self.tab_order.index(session_id)
        was_active = session_id == self.active_id
        self.tab_order.remove(session_id)
        self.tab_frames.pop(session_id).destroy()
        self.widgets.pop(session_id).destroy()
        self.tab_buttons.pop(session_id, None)
        for mapping in (self.sessions, self.records, self.history_index, self.generators,
                        self.busy, self.dead, self.bar_marks, self.task_ids):
            mapping.pop(session_id, None)
        if not self.tab_order:
            self.active_id = None
            new_id = self.store.create(Session("ps"), session_type="temporary")
            row = next(item for item in self.store.list_sessions(opened=False)
                       if item["id"] == new_id)
            self.store.set_open(new_id, True, 0)
            row["is_open"] = 1
            self._open_record(row)
        elif was_active:
            self.switch(self.tab_order[min(closed_index, len(self.tab_order) - 1)])

    def show_cleanup_history(self):
        logs = self.store.cleanup_history()
        window = tk.Toplevel(self.root)
        window.title("Журнал очистки сессий")
        window.geometry("760x420")
        text = tk.Text(window, wrap="word")
        text.pack(fill="both", expand=True)
        for row in logs:
            text.insert("end", "%s  %s  %s\n%s\n%s\n\n" % (
                row["started_at"], row["event"], row["status"], row["actions"], row["errors"]))
        text.configure(state="disabled")

    def refresh_tab_labels(self, schedule=True):
        for sid, button in self.tab_buttons.items():
            button.configure(text=self.tab_text(sid))
        if self.active_id in self.sessions:
            self.refresh_title()
        if schedule:
            self.root.after(60000, self.refresh_tab_labels)

    def tab_text(self, session_id):
        label = session_label(self.records[session_id])
        return label + (" • RUNNING" if self.busy.get(session_id) else "")

    def render_banner(self, session_id):
        kind = self.sessions[session_id].kind
        if kind == "ps":
            lines = [
                "Windows PowerShell",
                "(C) Корпорация Майкрософт (Microsoft Corporation). Все права защищены.",
                "",
                "Установите последнюю версию PowerShell для новых функций и улучшений! https://aka.ms/PSWindows",
                "",
            ]
        else:
            lines = [
                "Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.0-45-generic x86_64)",
                "",
                " * Documentation:  https://help.ubuntu.com",
                " * Support:        https://ubuntu.com/pro",
                "",
                "Last login: Thu Oct 29 06:01:12 2026 from 10.0.2.2",
            ]
        for line in lines:
            self.write(session_id, [(line, "out")])
        self.write(session_id, [])
        self.prompt(session_id)

    def switch(self, session_id):
        self.active_id = session_id
        kind = self.sessions[session_id].kind
        for sid, widget in self.widgets.items():
            widget.pack_forget()
            shell = self.sessions[sid].kind
            widget.configure(bg=THEMES[shell]["bg"], fg=THEMES[shell]["fg"],
                             insertbackground=THEMES[shell]["fg"])
        self.widgets[session_id].pack(fill="both", expand=True)
        self.widgets[session_id].focus_set()
        for sid, button in self.tab_buttons.items():
            button.configure(text=self.tab_text(sid))
            button.configure(bg="#505050" if sid == session_id else "#303030")
        self.refresh_title()

    def toggle_shell(self):
        kinds = [sid for sid, session in self.sessions.items()
                 if session.kind != self.sessions[self.active_id].kind]
        if kinds:
            self.switch(kinds[0])
        return "break"

    def refresh_title(self):
        session = self.sessions[self.active_id]
        kind = session.kind
        record = self.records[self.active_id]
        if kind == "bash":
            prompt = session.prompt()[0][0]
            title = "root@vm: " + prompt.split(":", 1)[1].rstrip("# ")
        else:
            title = "Администратор: Windows PowerShell"
        expiry = record.get("expires_at")
        if record["session_type"] == "temporary" and expiry:
            title = "%s — ВРЕМЕННАЯ до %s" % (record["name"], expiry[:16].replace("T", " "))
        elif record["session_type"] == "temporary":
            title = "%s — ВРЕМЕННАЯ без автоудаления" % record["name"]
        else:
            title = "%s — ПОСТОЯННАЯ" % record["name"]
        title += " — " + ("PowerShell" if kind == "ps" else "Bash") + " • VIRTUAL"
        self.title.configure(text=title)
        self.header.configure(bg=THEMES[kind]["bar"])
        for child in self.header.winfo_children():
            if isinstance(child, tk.Button):
                child.configure(bg=THEMES[kind]["bar"])

    def write(self, session_id, segments, newline=True):
        widget = self.widgets[session_id]
        widget.configure(state="normal")
        widget.mark_set("insert", "end-1c")
        for text, style in segments:
            widget.insert("insert", text, style)
        if newline:
            widget.insert("insert", "\n")
        widget.see("end")

    def prompt(self, session_id):
        self.write(session_id, self.sessions[session_id].prompt(), newline=False)
        widget = self.widgets[session_id]
        widget.mark_set("input_start", "end-1c")
        widget.mark_gravity("input_start", "left")
        self.input_starts[session_id] = widget.index("input_start")
        widget.see("end")
        if session_id == self.active_id:
            self.refresh_title()

    def input_text(self, session_id):
        widget = self.widgets[session_id]
        return widget.get("input_start", "end-1c").replace("\n", " ")

    def on_key(self, event, session_id):
        if session_id != self.active_id:
            return "break"
        if self.busy[session_id]:
            return "break"
        if self.dead[session_id]:
            if event.keysym in ("Return", "KP_Enter"):
                self.reset_terminal(session_id)
            return "break"
        widget = self.widgets[session_id]
        start = widget.index("input_start")
        cursor = widget.index("insert")
        if event.keysym in ("Return", "KP_Enter"):
            self.submit(session_id)
            return "break"
        if event.keysym == "Home":
            widget.mark_set("insert", "input_start")
            return "break"
        if event.keysym == "BackSpace" and widget.compare(cursor, "<=", start):
            return "break"
        if event.keysym == "Left" and widget.compare(cursor, "<=", start):
            return "break"
        if event.keysym == "Up":
            self.history(session_id, -1)
            return "break"
        if event.keysym == "Down":
            self.history(session_id, 1)
            return "break"
        if event.keysym == "Tab":
            self.autocomplete(session_id)
            return "break"
        if widget.compare(cursor, "<", start):
            if event.keysym in ("Delete", "BackSpace"):
                return "break"
            widget.mark_set("insert", "end-1c")
        return None

    def on_paste(self, event, session_id):
        if self.busy[session_id] or self.dead[session_id]:
            return "break"
        widget = self.widgets[session_id]
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return "break"
        text = text.replace("\r", "").split("\n", 1)[0]
        widget.insert("insert", text)
        return "break"

    def submit(self, session_id):
        widget = self.widgets[session_id]
        command = self.input_text(session_id).strip()
        widget.mark_set("insert", "end-1c")
        widget.insert("insert", "\n")
        self.history_index[session_id] = None
        self.bar_marks[session_id].clear()
        if not command:
            self.prompt(session_id)
            return
        if self.run_session_command(session_id, command):
            return
        self.busy[session_id] = True
        self.task_ids[session_id] = self.process_manager.start(session_id, command)
        self.refresh_tab_labels(schedule=False)
        self.generators[session_id] = self.sessions[session_id].execute(command)
        self.step(session_id)

    def run_session_command(self, session_id, command):
        command = command.strip()
        if command.lower().startswith("simulator>"):
            command = command[len("simulator>"):].strip()
        args = tok(command)
        if not args or args[0].lower() != "session":
            return False
        if len(args) < 2 or args[1].lower() != "cleanup":
            self.cleanup_expired(show_message=False, schedule=False)
        result = []
        if len(args) < 2:
            result = ["Usage: session create|list|use|extend|make-permanent|delete|cleanup"]
        else:
            action = args[1].lower()
            rows = self.store.list_sessions()
            if action == "list":
                result = ["%s  %s  %s  %s" % (
                    row["id"], row["name"], row["session_type"].upper(),
                    row["expires_at"] or "no expiry") for row in rows] or ["No sessions."]
            elif action == "create" and len(args) >= 3 and args[2].lower() in ("temporary", "permanent"):
                new_id = self.create_session_record(args[2].lower())
                result = ["Created virtual session %s" % new_id]
            elif action == "use" and len(args) >= 3:
                target = self.find_session(args[2], rows)
                if target is None:
                    result = ["Session not found: %s" % args[2]]
                else:
                    if not target["is_open"]:
                        self.store.set_open(target["id"], True, len(self.tab_order))
                        target["is_open"] = 1
                        self._open_record(target)
                    else:
                        self.switch(target["id"])
                    result = ["Active session: %s" % target["name"]]
            elif action == "extend" and len(args) >= 4:
                target = self.find_session(args[2], rows)
                hours = self.parse_hours(args[3])
                if target is None:
                    result = ["Session not found: %s" % args[2]]
                elif hours is None or hours <= 0:
                    result = ["Invalid duration. Use for example 12h or 7d."]
                else:
                    self.store.extend(target["id"], hours)
                    result = ["Session expiry extended."]
            elif action in ("make-permanent", "make_permanent") and len(args) >= 3:
                target = self.find_session(args[2], rows)
                if target is None:
                    result = ["Session not found: %s" % args[2]]
                else:
                    self.store.make_permanent(target["id"])
                    result = ["Session is now permanent."]
            elif action == "delete" and len(args) >= 3:
                target = self.find_session(args[2], rows)
                if target is None:
                    result = ["Session not found: %s" % args[2]]
                else:
                    if target["id"] in self.sessions:
                        deleted = self.delete_session(target["id"], confirm=False)
                    else:
                        try:
                            deleted = self.store.delete(target["id"])
                        except OSError as error:
                            deleted = False
                            messagebox.showerror("Удаление сессии", str(error), parent=self.root)
                        except sqlite3.Error as error:
                            deleted = False
                            messagebox.showerror("Удаление сессии",
                                                 "Ошибка базы данных:\n%s" % error, parent=self.root)
                    result = (["Session deleted."] if deleted
                              else ["Session was not deleted; see the error message."])
            elif action == "cleanup":
                self.cleanup_expired(show_message=False, schedule=False)
                result = ["Cleanup check requested; results will appear when it completes."]
            else:
                result = ["Unsupported session command."]
        output_id = session_id if session_id in self.sessions else self.active_id
        if output_id is not None:
            self.sessions[output_id].history.append(command)
            self.write(output_id, [(line, "out") for line in result])
            self.prompt(output_id)
        self.save()
        return True

    @staticmethod
    def find_session(value, rows):
        folded = value.casefold()
        if folded in ("temporary", "permanent"):
            return next((row for row in reversed(rows)
                         if row["session_type"] == folded), None)
        return next((row for row in rows
                     if row["id"].casefold() == folded or row["name"].casefold() == folded), None)

    @staticmethod
    def parse_hours(value):
        value = value.lower()
        try:
            if value.endswith("h"):
                return float(value[:-1])
            if value.endswith("d"):
                return float(value[:-1]) * 24
            return float(value)
        except ValueError:
            return None

    def step(self, session_id):
        generator = self.generators[session_id]
        if generator is None:
            return
        try:
            while True:
                event = next(generator)
                if event[0] == "out":
                    self.write(session_id, event[1])
                elif event[0] == "bar":
                    self.update_bar(session_id, event[1], event[2])
                elif event[0] == "wait":
                    self.widgets[session_id].see("end")
                    delay = max(5, int(event[1] * 1000 * self.time_scale))
                    self.root.after(delay, lambda sid=session_id: self.step(sid))
                    return
                elif event[0] == "clear":
                    self.widgets[session_id].delete("1.0", "end")
                    self.bar_marks[session_id].clear()
                elif event[0] == "exit":
                    self.dead[session_id] = True
        except StopIteration:
            self.finish(session_id)

    def update_bar(self, session_id, key, segments):
        widget = self.widgets[session_id]
        mark = self.bar_marks[session_id].get(key)
        if mark is None:
            mark = "bar_%d" % self.bar_mark_counter
            self.bar_mark_counter += 1
            self.bar_marks[session_id][key] = mark
            widget.mark_set(mark, "end-1c")
            widget.mark_gravity(mark, "left")
            self.write(session_id, segments)
            return
        end = widget.index("%s lineend" % mark)
        widget.delete(mark, end)
        widget.mark_set("insert", mark)
        for text, style in segments:
            widget.insert("insert", text, style)

    def finish(self, session_id):
        self.generators[session_id] = None
        task_id = self.task_ids.get(session_id)
        if task_id is not None:
            self.process_manager.finish(task_id)
            self.task_ids[session_id] = None
        if self.dead[session_id]:
            if self.sessions[session_id].kind == "bash":
                self.write(session_id, [("logout", "out")])
            self.write(session_id, [("[Процесс завершён с кодом %d]" % self.sessions[session_id].last_exit, "dim")])
            self.write(session_id, [("Нажмите Enter, чтобы перезапустить оболочку.", "dim")], newline=False)
            self.busy[session_id] = False
            self.widgets[session_id].mark_set("insert", "end-1c")
        else:
            self.busy[session_id] = False
            self.prompt(session_id)
        self.refresh_tab_labels(schedule=False)
        self.save()

    def history(self, session_id, direction):
        entries = self.sessions[session_id].history
        if not entries:
            return
        index = len(entries) if self.history_index[session_id] is None else self.history_index[session_id]
        index += direction
        if index >= len(entries):
            self.history_index[session_id] = None
            self.replace_input(session_id, "")
        else:
            self.history_index[session_id] = max(0, index)
            self.replace_input(session_id, entries[self.history_index[session_id]])

    def replace_input(self, session_id, text):
        widget = self.widgets[session_id]
        widget.delete("input_start", "end-1c")
        widget.insert("end-1c", text, "out")
        widget.mark_set("insert", "end-1c")

    def autocomplete(self, session_id):
        line = self.input_text(session_id)
        word, candidates = self.sessions[session_id].complete(line)
        if not candidates:
            return
        base = line[:len(line) - len(word)]
        if len(candidates) == 1:
            suffix = "" if candidates[0].endswith("/") else " "
            self.replace_input(session_id, base + candidates[0] + suffix)
            return
        common = candidates[0]
        for candidate in candidates[1:]:
            limit = min(len(common), len(candidate))
            i = 0
            while i < limit and common[i] == candidate[i]:
                i += 1
            common = common[:i]
        if len(common) > len(word):
            self.replace_input(session_id, base + common)
        else:
            self.write(session_id, [("  ".join(candidates), "out")])
            self.prompt(session_id)
            self.replace_input(session_id, line)

    def copy_or_cancel(self, _event):
        session_id = self.active_id
        if self.busy[session_id]:
            generator = self.generators[session_id]
            if generator is not None:
                generator.close()
            task_id = self.task_ids.get(session_id)
            if task_id is not None:
                self.process_manager.finish(task_id)
                self.task_ids[session_id] = None
            self.write(session_id, [("^C", "out")])
            self.finish(session_id)
        else:
            try:
                self.root.clipboard_append(self.widgets[session_id].selection_get())
            except tk.TclError:
                pass
        return "break"

    def ask_speed(self):
        speed = simpledialog.askfloat("Скорость сети", "МБ/с (по умолчанию 30):",
                                      initialvalue=self.sessions[self.active_id].speed,
                                      minvalue=0.01, maxvalue=1000, parent=self.root)
        if speed is not None:
            for session in self.sessions.values():
                session.speed = speed

    def ask_scale(self):
        scale = simpledialog.askfloat("Скорость симуляции",
                                      "1.0 = реальное время, 0.1 = в 10 раз быстрее:",
                                      initialvalue=self.time_scale, minvalue=0.01,
                                      maxvalue=5, parent=self.root)
        if scale is not None:
            self.time_scale = scale

    def zoom(self, delta):
        self.font_size = max(8, min(32, self.font_size + delta))
        for widget in self.widgets.values():
            widget.configure(font=("Consolas", self.font_size))
        return "break"

    def reset_terminal(self, session_id):
        kind = self.sessions[session_id].kind
        self.sessions[session_id] = Session(kind)
        self.widgets[session_id].configure(state="normal")
        self.widgets[session_id].delete("1.0", "end")
        self.dead[session_id] = False
        self.busy[session_id] = False
        self.bar_marks[session_id].clear()
        self.history_index[session_id] = None
        self.render_banner(session_id)
        self.save()

    def reset_session(self):
        self.reset_terminal(self.active_id)

    def save(self):
        for session_id, session in self.sessions.items():
            self.store.save(session_id, session)

    def close(self):
        running = [self.records[task.session_id]["name"] + ": " + task.description
                   for task in self.process_manager.active_tasks()
                   if task.session_id in self.records]
        if running and not messagebox.askyesno(
                "Выполняются виртуальные команды",
                "Остановить команды и закрыть приложение?\n" + "\n".join(running),
                parent=self.root):
            return
        for sid, value in self.busy.items():
            if value and self.generators[sid] is not None:
                self.generators[sid].close()
                task_id = self.task_ids.get(sid)
                if task_id is not None:
                    self.process_manager.finish(task_id)
        self.cleanup_worker.stop()
        self.save()
        self.root.destroy()


def main():
    root = tk.Tk()
    TerminalSimulator(root)
    root.mainloop()

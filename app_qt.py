"""Qt-интерфейс Terminal Simulator. Запускается через app.py.
Все команды выполняются только внутри ядра core.py, настоящие процессы не запускаются."""
import json
import os
import queue
import sqlite3
import sys
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QImage, QKeySequence, QPainter,
                               QShortcut, QTextCursor, QTextCharFormat, QTextDocument)
    from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPlainTextEdit,
                                       QMessageBox, QSizeGrip, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget)
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    try:
        from PyQt6.QtCore import Qt, QTimer, QUrl
        from PyQt6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QImage, QKeySequence, QPainter,
                                 QShortcut, QTextCursor, QTextCharFormat, QTextDocument)
        from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPlainTextEdit,
                                     QMessageBox, QSizeGrip, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget)
    except ModuleNotFoundError as error:
        if error.name != "PyQt6":
            raise
        raise ModuleNotFoundError("Neither PySide6 nor PyQt6 is installed.", name="qt_bindings") from error

from core import Session, tok
from process_manager import ProcessManager
from session_store import (DEFAULT_TTL_HOURS, BackgroundCleanupWorker, SessionStore,
                           session_label)

STATE = Path.home() / ".terminal_sim" / "state.json"
DB_STATE = Path.home() / ".terminal_sim" / "sessions.sqlite3"
SHOTS = Path.home() / "TerminalSim_screens"
WIN = sys.platform == "win32"
END, SOB, EOB = QTextCursor.MoveOperation.End, QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveOperation.EndOfBlock
KA = QTextCursor.MoveMode.KeepAnchor
K = Qt.Key

THEMES = {
    "ps": dict(bg="#0c0c0c", fg="#cccccc", bar="#202020", barfg="#ffffff", size=12,
               fam=["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"],
               c=dict(out="#cccccc", ok="#16c60c", info="#3a96dd", warn="#f9a825", err="#e74856", dim="#767676",
                      head="#61d6d6", ts="#f9f1a5", grid="#767676", bprompt="#cccccc", dir="#3b78ff")),
    "bash": dict(bg="#1b1d23", fg="#d7dae0", bar="#2b2d33", barfg="#b4b8c2", size=12,
                 fam=["Ubuntu Mono", "DejaVu Sans Mono", "Menlo", "Consolas", "Courier New"],
                 c=dict(out="#d7dae0", ok="#6fdc8c", info="#82aaff", warn="#ffcb6b", err="#ff6b6b", dim="#8a8f99",
                        head="#7fdbca", ts="#d7dae0", grid="#8a8f99", bprompt="#82aaff", dir="#82aaff")),
}
BANNER = {
    "ps": ["Windows PowerShell", "(C) Корпорация Майкрософт (Microsoft Corporation). Все права защищены.", "",
           "Установите последнюю версию PowerShell для новых функций и улучшений! https://aka.ms/PSWindows", ""],
    "bash": ["Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.0-45-generic x86_64)", "",
             " * Documentation:  https://help.ubuntu.com", " * Support:        https://ubuntu.com/pro", "",
             "Last login: Thu Oct 29 06:01:12 2026 from 10.0.2.2"],
}
MENU_CSS = ("QMenu{background:#2b2b2b;color:#eee;border:1px solid #444;padding:4px}"
            "QMenu::item{padding:6px 22px}QMenu::item:selected{background:#3d3d3d}QMenu::separator{height:1px;background:#444;margin:4px 8px}")


class Terminal(QPlainTextEdit):
    def __init__(self, kind, session, app, session_id):
        super().__init__()
        self.kind, self.s, self.app, self.th, self.session_id = kind, session, app, THEMES[kind], session_id
        self.busy = self.dead = False
        self.input_pos, self.hist_i, self.cur, self.gen = 0, None, None, None
        self.marks, self.bars = [], {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QPlainTextEdit{background:%s;color:%s;selection-background-color:#264f78;border:none}" % (self.th["bg"], self.th["fg"]))
        self.document().setDocumentMargin(10)
        self.apply_font(self.th["size"])
        self.reset()

    def focusNextPrevChild(self, nxt):
        return False

    def apply_font(self, size):
        self.th["size"] = size
        f = QFont()
        f.setFamilies(self.th["fam"])
        f.setPointSize(size)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(f)
        self.setCursorWidth(QFontMetrics(f).horizontalAdvance("M"))

    def reset(self):
        self.document().clear()
        self.marks, self.bars, self.cur, self.dead = [], {}, None, False
        for ln in BANNER[self.kind]:
            self.put([(ln, "out")])
        self.put([])
        self.prompt()

    # ---------- вывод ----------
    def fmt(self, st):
        f = QTextCharFormat()
        f.setForeground(QColor(self.th["c"].get(st, self.th["fg"])))
        if st in ("bprompt", "dir"):
            f.setFontWeight(QFont.Weight.Bold)
        return f

    def put(self, segs, nl=True):
        c = QTextCursor(self.document())
        c.movePosition(END)
        for t, st in segs:
            c.insertText(t, self.fmt(st))
        if nl:
            c.insertBlock()

    def bar(self, key, segs):
        doc = self.document()
        if key not in self.bars:
            self.put(segs)
            self.bars[key] = doc.blockCount() - 2
            return
        c = QTextCursor(doc.findBlockByNumber(self.bars[key]))
        c.movePosition(SOB)
        c.movePosition(EOB, KA)
        c.removeSelectedText()
        for t, st in segs:
            c.insertText(t, self.fmt(st))

    def prompt(self):
        self.put(self.s.prompt(), nl=False)
        self.moveCursor(END)
        self.setCurrentCharFormat(self.fmt("out"))
        self.input_pos = self.textCursor().position()
        self.busy = False
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        self.app.refresh_title(self.session_id, self.s)

    # ---------- выполнение ----------
    def submit(self):
        c = self.textCursor()
        c.setPosition(self.input_pos)
        c.movePosition(END, KA)
        text = c.selectedText().replace("\u2029", " ").strip()
        self.moveCursor(END)
        self.put([])
        self.hist_i, self.bars = None, {}
        if not text:
            self.prompt()
            return
        if self.app.run_session_command(self.session_id, text):
            return
        self.cur = self.document().blockCount() - 2
        self.busy = True
        self.app.start_virtual_task(self.session_id, text)
        self.app.refresh_tab_labels()
        self.gen = self.s.execute(text)
        self.step()

    def step(self):
        try:
            while True:
                ev = next(self.gen)
                if ev[0] == "out":
                    self.put(ev[1])
                elif ev[0] == "bar":
                    self.bar(ev[1], ev[2])
                elif ev[0] == "wait":
                    self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
                    self.timer.start(max(5, int(ev[1] * 1000 * self.app.time_scale)))
                    return
                elif ev[0] == "clear":
                    self.document().clear()
                    self.bars, self.marks, self.cur = {}, [], None
                elif ev[0] == "exit":
                    self.dead = True
        except StopIteration:
            self.finish()

    def finish(self):
        if self.cur is not None:
            self.marks.append((self.cur, max(self.cur, self.document().blockCount() - 2)))
        self.cur = None
        self.app.finish_virtual_task(self.session_id)
        if self.dead:
            if self.kind == "bash":
                self.put([("logout", "out")])
            self.put([("[Процесс завершён с кодом %d]" % self.s.last_exit, "dim")])
            self.put([("Нажмите Enter, чтобы перезапустить оболочку.", "dim")], nl=False)
            self.busy = False
            self.moveCursor(END)
        else:
            self.prompt()
        self.app.refresh_tab_labels()
        self.app.save()

    def keyPressEvent(self, e):
        k, ctrl = e.key(), bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        c = self.textCursor()
        if ctrl and k == K.Key_C:
            if self.busy:
                self.timer.stop()
                self.gen.close()
                self.put([("^C", "out")])
                self.finish()
                return
            if c.hasSelection():
                self.copy()
                return
        if self.busy:
            return
        if self.dead:
            if k in (K.Key_Return, K.Key_Enter):
                self.reset()
            return
        if k in (K.Key_Return, K.Key_Enter):
            return self.submit()
        if k in (K.Key_Backspace, K.Key_Left) and not c.hasSelection() and c.position() <= self.input_pos:
            return
        if k == K.Key_Home:
            c.setPosition(self.input_pos, KA if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else QTextCursor.MoveMode.MoveAnchor)
            self.setTextCursor(c)
            return
        if k in (K.Key_Up, K.Key_Down):
            return self.history(-1 if k == K.Key_Up else 1)
        if k == K.Key_Tab:
            return self.autocomplete()
        if c.position() < self.input_pos or (c.hasSelection() and min(c.position(), c.anchor()) < self.input_pos):
            c.clearSelection()
            c.movePosition(END)
            self.setTextCursor(c)
        super().keyPressEvent(e)

    def insertFromMimeData(self, src):
        if not self.busy:
            self.moveCursor(END)
            self.insertPlainText(src.text().replace("\r", "").split("\n")[0])

    def current_input(self):
        c = self.textCursor()
        c.setPosition(self.input_pos)
        c.movePosition(END, KA)
        return c.selectedText().replace("\u2029", " ")

    def replace_input(self, text):
        c = QTextCursor(self.document())
        c.setPosition(self.input_pos)
        c.movePosition(END, KA)
        c.insertText(text, self.fmt("out"))
        self.moveCursor(END)

    def history(self, d):
        h = self.s.history
        if not h:
            return
        i = len(h) if self.hist_i is None else self.hist_i
        i += d
        if i >= len(h):
            self.hist_i = None
            self.replace_input("")
        else:
            self.hist_i = max(0, i)
            self.replace_input(h[self.hist_i])

    def autocomplete(self):
        line = self.current_input()
        word, cands = self.s.complete(line)
        if not cands:
            return
        base = line[:len(line) - len(word)]
        if len(cands) == 1:
            self.replace_input(base + cands[0] + ("" if cands[0].endswith("/") else " "))
            return
        common = os.path.commonprefix(cands)
        if len(common) > len(word):
            self.replace_input(base + common)
            return
        self.moveCursor(END)
        self.put([])
        self.put([("  ".join(cands), "out")])
        self.prompt()
        self.replace_input(line)

    # ---------- кадры для скриншотов ----------
    def render_blocks(self, b0, b1, block_cursor):
        doc = self.document()
        c = QTextCursor(doc.findBlockByNumber(b0))
        last = doc.findBlockByNumber(min(b1, doc.blockCount() - 1))
        c.setPosition(last.position() + last.length() - 1, KA)
        d = QTextDocument()
        d.setDefaultFont(self.font())
        d.setDocumentMargin(10)
        cur = QTextCursor(d)
        cur.insertFragment(c.selection())
        if block_cursor:
            cur.movePosition(END)
            cur.insertText("█", self.fmt("out"))
        w = self.viewport().width()
        d.setTextWidth(w)
        img = QImage(w, int(d.size().height()) + 2, QImage.Format.Format_ARGB32)
        img.fill(QColor(self.th["bg"]))
        p = QPainter(img)
        d.drawContents(p)
        p.end()
        return img


class Bar(QWidget):
    def __init__(self, app, kind):
        super().__init__()
        self.app, self.kind, th = app, kind, THEMES[kind]
        self.setFixedHeight(36 if kind == "ps" else 34)
        self.setStyleSheet("background:%s;" % th["bar"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = QLabel("Администратор: Windows PowerShell" if kind == "ps" else "root@vm: ~")
        menu = QToolButton()
        menu.setText("⋯")
        menu.setMenu(app.menu)
        menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu.setStyleSheet("QToolButton{background:transparent;color:%s;border:none;font-size:16px;padding:0 12px}"
                           "QToolButton:hover{background:rgba(255,255,255,30)}QToolButton::menu-indicator{image:none}" % th["barfg"])
        menu.setFixedHeight(self.height())
        if kind == "ps":
            icon = QLabel(">_")
            icon.setFixedSize(18, 18)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet("background:#012456;color:#fff;font:bold 9px 'Consolas';border-radius:2px")
            self.title.setStyleSheet("color:#fff;font:13px 'Segoe UI';")
            lay.addSpacing(12)
            lay.addWidget(icon)
            lay.addSpacing(10)
            lay.addWidget(self.title)
            lay.addStretch()
            lay.addWidget(menu)
            glyph = ("\uE921", "\uE922", "\uE8BB") if WIN else ("–", "□", "✕")
            font = "font-family:'Segoe MDL2 Assets';" if WIN else ""
            for g, cb, hov in ((glyph[0], app.showMinimized, "#3a3a3a"), (glyph[1], app.toggle_max, "#3a3a3a"), (glyph[2], app.close, "#c42b1c")):
                lay.addWidget(self.cap(g, cb, hov, font))
        else:
            dots = QWidget()
            dl = QHBoxLayout(dots)
            dl.setContentsMargins(14, 0, 0, 0)
            dl.setSpacing(8)
            for col, cb in (("#ff5f56", app.close), ("#ffbd2e", app.showMinimized), ("#27c93f", app.toggle_max)):
                b = QToolButton()
                b.setFixedSize(12, 12)
                b.setStyleSheet("QToolButton{background:%s;border:none;border-radius:6px}QToolButton:hover{background:%s}" % (col, QColor(col).lighter(120).name()))
                b.clicked.connect(cb)
                dl.addWidget(b)
            dots.setFixedWidth(80)
            self.title.setStyleSheet("color:%s;font:13px 'Ubuntu','Segoe UI','DejaVu Sans';" % th["barfg"])
            menu.setFixedWidth(80)
            lay.addWidget(dots)
            lay.addStretch()
            lay.addWidget(self.title)
            lay.addStretch()
            lay.addWidget(menu)

    def cap(self, g, cb, hov, font):
        b = QToolButton()
        b.setText(g)
        b.setFixedSize(46, self.height())
        b.setStyleSheet("QToolButton{background:transparent;color:#fff;border:none;%sfont-size:10px}QToolButton:hover{background:%s}" % (font, hov))
        b.clicked.connect(cb)
        return b

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.app.toggle_max()


class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Terminal Simulator")
        self.resize(1000, 640)
        self.setMinimumSize(520, 320)
        self.time_scale, self.prefix, self.shot_n = 0.25, "Snimok", 1
        try:
            saved = json.loads(STATE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = {}
        except (OSError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "Состояние сессии",
                                "Не удалось прочитать старое состояние:\n%s" % error)
            saved = {}
        self.store = SessionStore(DB_STATE)
        self.store.migrate_json(saved)
        rows = self.store.list_sessions(opened=True)
        if not rows:
            closed = self.store.list_sessions()
            if closed:
                self.store.set_open(closed[-1]["id"], True, 0)
                rows = self.store.list_sessions(opened=True)
            else:
                self.store.create(Session("ps"), session_type="temporary")
                rows = self.store.list_sessions(opened=True)
        self.records = {row["id"]: row for row in rows}
        self.active_id = None
        self.process_manager = ProcessManager()
        self.task_ids = {}
        self.menu = self.build_menu()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bars, self.stack, self.term, self.barw = QStackedWidget(), QStackedWidget(), {}, {}
        self.tabs = QTabBar()
        self.tabs.setMovable(True)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabMoved.connect(self._tab_moved)
        self.tabs.tabBarDoubleClicked.connect(self.rename_tab_index)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_context_menu)
        self.new_tab_button = QToolButton()
        self.new_tab_button.setText("+")
        self.new_tab_button.clicked.connect(self.new_temporary)
        tab_row = QWidget()
        tab_layout = QHBoxLayout(tab_row)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)
        tab_layout.addWidget(self.tabs)
        tab_layout.addWidget(self.new_tab_button)
        lay.addWidget(tab_row)
        lay.addWidget(self.bars)
        lay.addWidget(self.stack)
        for row in rows:
            try:
                session = Session.from_dict(self.store.load_state(row["id"]))
            except (KeyError, TypeError, ValueError):
                session = Session(row["shell_profile"])
            self._add_session(row, session)
        self.toastl = QLabel(self)
        self.toastl.setStyleSheet("background:#000c;color:#fff;padding:6px 12px;border-radius:6px;font:12px 'Segoe UI'")
        self.toastl.hide()
        self.grip = QSizeGrip(self)
        for key, fn in (("Ctrl+Shift+S", self.save_state), ("Ctrl+Shift+P", self.command_palette),
                        ("F12", self.shot_cmds),
                        ("Ctrl+=", lambda: self.zoom(1)), ("Ctrl+-", lambda: self.zoom(-1)),
                        ("Ctrl+T", self.new_temporary), ("Ctrl+Shift+N", self.new_permanent),
                        ("Ctrl+Shift+T", self.restore_closed), ("Ctrl+W", self.close_tab),
                        ("Ctrl+Tab", lambda: self.next_tab(1)),
                        ("Ctrl+Shift+Tab", lambda: self.next_tab(-1))):
            QShortcut(QKeySequence(key), self).activated.connect(fn)
        for number in range(1, 10):
            QShortcut(QKeySequence("Ctrl+%d" % number), self).activated.connect(
                lambda i=number - 1: self.switch_index(i))
        if self.tabs.count():
            self.switch(self.tabs.tabData(0))
        self.cleanup_worker = BackgroundCleanupWorker(self.store)
        self.cleanup_worker.start()
        self.cleanup_poll = QTimer(self)
        self.cleanup_poll.setInterval(500)
        self.cleanup_poll.timeout.connect(self.process_cleanup_results)
        self.cleanup_poll.start()
        self.expiry_timer = QTimer(self)
        self.expiry_timer.setInterval(60000)
        self.expiry_timer.timeout.connect(self.refresh_tab_labels)
        self.expiry_timer.start()

    # ---------- меню ----------
    def build_menu(self):
        m = QMenu(self)
        m.setStyleSheet(MENU_CSS)
        items = [("Скрин окна", self.shot_window), ("Нарезка: каждая команда отдельно\tF12", self.shot_cmds),
                 ("Нарезка: по N строк…", self.shot_lines), ("Нарезка: по высоте окна", self.shot_screens),
                 ("Открыть папку со скринами", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(SHOTS)))),
                 ("Префикс имён файлов…", self.ask_prefix), None,
                 ("Новая временная вкладка\tCtrl+T", self.new_temporary),
                 ("Новая постоянная вкладка\tCtrl+Shift+N", self.new_permanent),
                 ("Восстановить закрытую вкладку\tCtrl+Shift+T", self.restore_closed),
                 ("Закрыть текущую вкладку\tCtrl+W", self.close_tab),
                 ("Палитра команд\tCtrl+Shift+P", self.command_palette),
                 ("Сохранить сессии\tCtrl+Shift+S", self.save_state),
                 ("Журнал очистки сессий", self.show_cleanup_history),
                 ("Скорость сети (МБ/с)…", self.ask_speed),
                 ("Скорость симуляции…", self.ask_scale), ("Шрифт крупнее\tCtrl+=", lambda: self.zoom(1)),
                 ("Шрифт мельче\tCtrl+-", lambda: self.zoom(-1)), None,
                 ("Сбросить текущую сессию", self.reset_session)]
        for it in items:
            if it is None:
                m.addSeparator()
            else:
                m.addAction(it[0]).triggered.connect(lambda _=False, f=it[1]: f())
        return m

    def _add_session(self, row, session, index=None):
        sid, kind = row["id"], session.kind
        self.records[sid] = row
        self.task_ids[sid] = None
        self.barw[sid] = Bar(self, kind)
        self.term[sid] = Terminal(kind, session, self, sid)
        self.bars.addWidget(self.barw[sid])
        self.stack.addWidget(self.term[sid])
        self.tabs.addTab(self.tab_text(sid))
        tab_index = self.tabs.count() - 1
        self.tabs.setTabData(tab_index, sid)
        close_button = QToolButton()
        close_button.setText("×")
        close_button.clicked.connect(lambda _=False, session_id=sid: self.close_session_tab(session_id))
        self.tabs.setTabButton(tab_index, QTabBar.ButtonPosition.RightSide, close_button)
        if index is None:
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
        elif index < self.tabs.count() - 1:
            self.tabs.moveTab(self.tabs.count() - 1, index)
            self.tabs.setCurrentIndex(index)

    def switch(self, session_id):
        if not session_id or session_id not in self.term:
            return
        self.active_id = session_id
        self.stack.setCurrentWidget(self.term[session_id])
        kind = self.term[session_id].kind
        self.bars.setCurrentWidget(self.barw[session_id])
        self.term[session_id].setFocus()
        index = next((i for i in range(self.tabs.count()) if self.tabs.tabData(i) == session_id), -1)
        if index >= 0 and self.tabs.currentIndex() != index:
            self.tabs.setCurrentIndex(index)
        self.refresh_title(session_id, self.term[session_id].s)

    def toggle_shell(self):
        return self.next_tab(1)

    def _tab_changed(self, index):
        if index >= 0:
            self.switch(self.tabs.tabData(index))

    def start_virtual_task(self, session_id, description):
        self.finish_virtual_task(session_id)
        self.task_ids[session_id] = self.process_manager.start(session_id, description)

    def finish_virtual_task(self, session_id):
        task_id = self.task_ids.get(session_id)
        if task_id is not None:
            self.process_manager.finish(task_id)
            self.task_ids[session_id] = None

    def switch_index(self, index):
        if index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def next_tab(self, delta):
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % count)

    def _tab_moved(self, old, new):
        for index in range(self.tabs.count()):
            sid = self.tabs.tabData(index)
            if sid:
                self.store.set_open(sid, True, index)

    def new_temporary(self):
        self.create_session("temporary")

    def new_permanent(self):
        self.create_session("permanent")

    def create_session(self, session_type):
        active = self.term[self.active_id].s if self.active_id else Session("ps")
        kind = active.kind
        name, ok = QInputDialog.getText(self, "Новая сессия", "Имя сессии:",
                                        text="PowerShell" if kind == "ps" else "Ubuntu")
        if not ok:
            return
        name = name.strip() or ("PowerShell" if kind == "ps" else "Ubuntu")
        ttl = DEFAULT_TTL_HOURS
        if session_type == "temporary":
            ttl, ok = QInputDialog.getDouble(
                self, "Срок временной сессии",
                "Срок в часах (0 — без автоудаления):", DEFAULT_TTL_HOURS, 0, 87600, 1)
            if not ok:
                return
        sid = self.store.create(Session(kind), name=name, session_type=session_type,
                                ttl_hours=ttl)
        row = next(item for item in self.store.list_sessions() if item["id"] == sid)
        row["is_open"] = 1
        self.store.set_open(sid, True, self.tabs.count())
        self._add_session(row, Session.from_dict(self.store.load_state(sid)))

    def restore_closed(self):
        self.cleanup_expired(show_message=False, schedule=False)
        rows = self.store.list_sessions(opened=False)
        if not rows:
            return
        row = rows[-1]
        self.store.set_open(row["id"], True, self.tabs.count())
        row["is_open"] = 1
        self._add_session(row, Session.from_dict(self.store.load_state(row["id"])))

    def command_palette(self):
        command, ok = QInputDialog.getText(
            self, "Палитра команд",
            "Команда приложения (например: session list):")
        if ok and command and not self.run_session_command(self.active_id, command):
            QMessageBox.information(
                self, "Палитра команд",
                "Поддерживаются команды session create/list/use/extend/"
                "make-permanent/delete/cleanup.")

    def save_state(self):
        self.save()
        self.toast("Сессии сохранены")

    def create_session_record(self, session_type, name=None, ttl_hours=DEFAULT_TTL_HOURS):
        kind = self.term[self.active_id].kind if self.active_id else "ps"
        name = name or ("PowerShell" if kind == "ps" else "Ubuntu")
        sid = self.store.create(Session(kind), name=name, session_type=session_type,
                                ttl_hours=ttl_hours)
        row = next(item for item in self.store.list_sessions() if item["id"] == sid)
        self.store.set_open(sid, True, self.tabs.count())
        row["is_open"] = 1
        self._add_session(row, Session.from_dict(self.store.load_state(sid)))
        return sid

    def close_tab(self):
        self.close_tab_index(self.tabs.currentIndex())

    def close_session_tab(self, session_id):
        index = next((i for i in range(self.tabs.count())
                      if self.tabs.tabData(i) == session_id), -1)
        self.close_tab_index(index)

    def close_tab_index(self, index, persist=True):
        if index < 0:
            return
        previously_active = self.active_id
        closed_index = index
        sid = self.tabs.tabData(index)
        terminal = self.term[sid]
        if terminal.busy:
            answer = QMessageBox.question(
                self, "Команда выполняется",
                "Остановить виртуальную команду и закрыть вкладку?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            terminal.timer.stop()
            if terminal.gen is not None:
                terminal.gen.close()
            terminal.busy = False
            self.finish_virtual_task(sid)
        if persist:
            self.store.save(sid, terminal.s)
            self.store.set_open(sid, False)
        self.tabs.removeTab(index)
        self.bars.removeWidget(self.barw.pop(sid))
        self.stack.removeWidget(self.term.pop(sid))
        self.records.pop(sid)
        self.save()
        if not self.term:
            self.create_default_session()
        elif previously_active in self.term:
            self.switch(previously_active)
        else:
            self.switch(self.tabs.tabData(min(closed_index, self.tabs.count() - 1)))
        for tab_index in range(self.tabs.count()):
            self.store.set_open(self.tabs.tabData(tab_index), True, tab_index)

    def create_default_session(self):
        sid = self.store.create(Session("ps"), session_type="temporary")
        row = next(item for item in self.store.list_sessions() if item["id"] == sid)
        self.store.set_open(sid, True, 0)
        row["is_open"] = 1
        self._add_session(row, Session.from_dict(self.store.load_state(sid)))

    def rename_tab_index(self, index):
        if index < 0:
            return
        sid = self.tabs.tabData(index)
        name, ok = QInputDialog.getText(self, "Переименовать вкладку", "Имя:",
                                        text=self.records[sid]["name"])
        if ok and name.strip():
            self.store.rename(sid, name)
            self.records[sid]["name"] = name.strip()
            self.tabs.setTabText(index, self.tab_text(sid))

    def _tab_context_menu(self, point):
        index = self.tabs.tabAt(point)
        if index < 0:
            return
        sid = self.tabs.tabData(index)
        menu = QMenu(self)
        menu.addAction("Переименовать", lambda: self.rename_tab_index(index))
        if self.records[sid]["session_type"] == "temporary":
            menu.addAction("Сделать постоянной", lambda: self.make_permanent(sid))
            menu.addAction("Продлить на 12 часов", lambda: self.extend_session(sid, 12))
            menu.addAction("Отключить автоудаление", lambda: self.set_auto_cleanup(sid, False))
        else:
            menu.addAction("Открепить вкладку" if self.records[sid].get("is_pinned")
                           else "Закрепить вкладку",
                           lambda: self.pin_session(sid, not bool(self.records[sid].get("is_pinned"))))
        menu.addAction("Дублировать в новую временную сессию",
                       lambda: self.duplicate_session(sid))
        menu.addAction("Удалить сессию и виртуальные данные…", lambda: self.delete_session(sid))
        menu.exec(self.tabs.mapToGlobal(point))

    def make_permanent(self, sid):
        self.store.make_permanent(sid)
        self.records[sid].update(session_type="permanent", expires_at=None)
        index = next(i for i in range(self.tabs.count()) if self.tabs.tabData(i) == sid)
        self.tabs.setTabText(index, self.tab_text(sid))

    def pin_session(self, sid, pinned):
        try:
            self.store.set_pinned(sid, pinned)
        except ValueError as error:
            QMessageBox.warning(self, "Закрепление вкладки", str(error))
            return
        self.records[sid]["is_pinned"] = int(pinned)
        pinned_ids = [self.tabs.tabData(i) for i in range(self.tabs.count())
                      if self.records[self.tabs.tabData(i)].get("is_pinned")]
        unpinned_ids = [self.tabs.tabData(i) for i in range(self.tabs.count())
                        if not self.records[self.tabs.tabData(i)].get("is_pinned")]
        desired = pinned_ids + unpinned_ids
        for target, session_id in enumerate(desired):
            current = next(i for i in range(self.tabs.count())
                           if self.tabs.tabData(i) == session_id)
            if current != target:
                self.tabs.moveTab(current, target)
        self._tab_moved(0, 0)
        self.refresh_tab_labels()

    def duplicate_session(self, sid):
        source = self.term.get(sid)
        session = source.s if source else Session.from_dict(self.store.load_state(sid))
        duplicate = Session.from_dict(json.loads(json.dumps(session.to_dict(), ensure_ascii=False)))
        new_id = self.store.create(duplicate, name=self.records[sid]["name"] + " copy",
                                   session_type="temporary", ttl_hours=DEFAULT_TTL_HOURS)
        row = next(item for item in self.store.list_sessions() if item["id"] == new_id)
        self.store.set_open(new_id, True, self.tabs.count())
        row["is_open"] = 1
        self._add_session(row, Session.from_dict(self.store.load_state(new_id)))

    def set_auto_cleanup(self, sid, enabled):
        self.store.set_auto_cleanup(sid, enabled)
        self.records[sid]["auto_cleanup_enabled"] = int(enabled)
        self.records[sid]["expires_at"] = next(row["expires_at"] for row in self.store.list_sessions()
                                               if row["id"] == sid)
        self.refresh_tab_labels()

    def extend_session(self, sid, hours):
        self.store.extend(sid, hours)
        self.records[sid]["expires_at"] = next(row["expires_at"] for row in self.store.list_sessions()
                                               if row["id"] == sid)
        self.refresh_tab_labels()

    def delete_session(self, sid, confirm=True):
        row = self.records.get(sid)
        if row is None:
            row = self.find_session(sid, self.store.list_sessions())
        if row is None:
            return False
        if confirm:
            answer = QMessageBox.question(
                self, "Удалить сессию",
                "Удалить сессию «%s» и её виртуальные данные?" % row["name"],
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if sid not in self.term:
            try:
                deleted = self.store.delete(sid)
            except OSError as error:
                QMessageBox.critical(self, "Удаление сессии",
                                     "Сессия не удалена полностью:\n%s" % error)
                return False
            except sqlite3.Error as error:
                QMessageBox.critical(self, "Удаление сессии",
                                     "Ошибка базы данных при удалении:\n%s" % error)
                return False
            if not deleted:
                QMessageBox.critical(self, "Удаление сессии", "Сессия не найдена в базе данных.")
                return False
            self.records.pop(sid, None)
            return True
        terminal = self.term[sid]
        if terminal.busy:
            answer = QMessageBox.question(
                self, "Команда выполняется",
                "Остановить виртуальную команду и удалить сессию?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
            terminal.timer.stop()
            if terminal.gen is not None:
                terminal.gen.close()
            terminal.busy = False
            self.finish_virtual_task(sid)
        try:
            self.store.save(sid, terminal.s)
            deleted = self.store.delete(sid)
        except KeyError as error:
            QMessageBox.critical(self, "Удаление сессии", str(error))
            return False
        except OSError as error:
            QMessageBox.critical(self, "Удаление сессии",
                                 "Сессия не удалена полностью:\n%s" % error)
            return False
        except sqlite3.Error as error:
            QMessageBox.critical(self, "Удаление сессии",
                                 "Ошибка базы данных при удалении:\n%s" % error)
            return False
        if not deleted:
            QMessageBox.critical(self, "Удаление сессии", "Сессия не найдена в базе данных.")
            return False
        index = next(i for i in range(self.tabs.count()) if self.tabs.tabData(i) == sid)
        self.close_tab_index(index, persist=False)
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
                QMessageBox.critical(
                    self, "Ошибка автоочистки",
                    "Сессия %s не очищена:\n%s" % (session_id, error))
            finalized = []
            for session_id in removed:
                try:
                    if self.store.finalize_cleanup(session_id):
                        finalized.append(session_id)
                        if session_id in self.term:
                            self.remove_expired_session_ui(session_id)
                except (OSError, ValueError) as error:
                    QMessageBox.critical(
                        self, "Ошибка завершения очистки",
                        "Сессия %s: %s" % (session_id, error))
            if finalized:
                QMessageBox.information(
                    self, "Очистка сессий",
                    "Удалено просроченных временных сессий: %d" % len(finalized))
            if worker_error and not errors:
                QMessageBox.critical(self, "Фоновая очистка", worker_error)

    def remove_expired_session_ui(self, session_id):
        index = next(i for i in range(self.tabs.count())
                     if self.tabs.tabData(i) == session_id)
        terminal = self.term[session_id]
        terminal.timer.stop()
        if terminal.gen is not None:
            terminal.gen.close()
        self.finish_virtual_task(session_id)
        was_active = self.active_id == session_id
        self.tabs.removeTab(index)
        self.bars.removeWidget(self.barw.pop(session_id))
        self.stack.removeWidget(self.term.pop(session_id))
        self.records.pop(session_id, None)
        self.task_ids.pop(session_id, None)
        if not self.term:
            self.create_default_session()
        elif was_active:
            self.switch(self.tabs.tabData(min(index, self.tabs.count() - 1)))

    def show_cleanup_history(self):
        rows = self.store.cleanup_history()
        text = "\n\n".join("%s  %s  %s\n%s\n%s" % (
            row["started_at"], row["event"], row["status"], row["actions"], row["errors"])
            for row in rows) or "No cleanup events yet."
        QMessageBox.information(self, "Журнал очистки сессий", text)

    def run_session_command(self, session_id, command):
        original_command = command.strip()
        if original_command.lower().startswith("simulator>"):
            original_command = original_command[len("simulator>"):].strip()
        args = tok(original_command)
        if not args or args[0].lower() != "session":
            return False
        result = []
        rows = self.store.list_sessions()
        if len(args) < 2:
            result = ["Usage: session create|list|use|extend|make-permanent|delete|cleanup"]
        else:
            action = args[1].lower()
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
                        self.store.set_open(target["id"], True, self.tabs.count())
                        target["is_open"] = 1
                        self._add_session(target, Session.from_dict(self.store.load_state(target["id"])))
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
                    deleted = self.delete_session(target["id"], confirm=False)
                    result = (["Session deleted."] if deleted
                              else ["Session was not deleted; see the error message."])
            elif action == "cleanup":
                self.cleanup_expired(show_message=False, schedule=False)
                result = ["Cleanup check requested; results will appear when it completes."]
            else:
                result = ["Unsupported session command."]
        output_id = session_id if session_id in self.term else self.active_id
        if output_id is not None and output_id in self.term:
            terminal = self.term[output_id]
            terminal.s.history.append(original_command)
            for line in result:
                terminal.put([(line, "out")])
            terminal.prompt()
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

    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def refresh_title(self, session_id, s):
        if session_id not in self.barw:
            return
        kind = s.kind
        record = self.records[session_id]
        if kind == "bash":
            title = "root@vm: " + s.prompt()[0][0].split(":", 1)[1].rstrip("# ")
        else:
            title = "Администратор: Windows PowerShell"
        suffix = "ВРЕМЕННАЯ" if record["session_type"] == "temporary" else "ПОСТОЯННАЯ"
        self.barw[session_id].title.setText("%s — %s — %s • VIRTUAL" %
                                            (record["name"], title, suffix))
        index = next((i for i in range(self.tabs.count()) if self.tabs.tabData(i) == session_id), -1)
        if index >= 0:
            self.tabs.setTabText(index, self.tab_text(session_id))

    def refresh_tab_labels(self):
        for index in range(self.tabs.count()):
            sid = self.tabs.tabData(index)
            if sid in self.records:
                self.tabs.setTabText(index, self.tab_text(sid))

    def tab_text(self, session_id):
        label = session_label(self.records[session_id])
        return label + (" • RUNNING" if self.term[session_id].busy else "")

    def zoom(self, d):
        for t in self.term.values():
            t.apply_font(max(8, min(32, t.th["size"] + d)))

    def ask_speed(self):
        v, ok = QInputDialog.getDouble(self, "Скорость сети", "МБ/с (по умолчанию 30):", self.term[self.active_id].s.speed, 0.01, 1000, 2)
        if ok:
            for t in self.term.values():
                t.s.speed = v

    def ask_scale(self):
        v, ok = QInputDialog.getDouble(self, "Скорость симуляции", "1.0 = реальное время, 0.1 = в 10 раз быстрее:", self.time_scale, 0.01, 5, 2)
        if ok:
            self.time_scale = v

    def ask_prefix(self):
        v, ok = QInputDialog.getText(self, "Префикс", "Имя файла скриншота (например Lab2_Start):", text=self.prefix)
        if ok and v.strip():
            self.prefix = v.strip()

    def reset_session(self):
        t = self.term[self.active_id]
        t.s = Session(t.kind)
        t.reset()
        self.save()

    # ---------- скриншоты ----------
    def toast(self, text):
        self.toastl.setText(text)
        self.toastl.adjustSize()
        self.toastl.move((self.width() - self.toastl.width()) // 2, self.height() - 60)
        self.toastl.show()
        self.toastl.raise_()
        QTimer.singleShot(2500, self.toastl.hide)

    def save_img(self, img, name):
        SHOTS.mkdir(parents=True, exist_ok=True)
        p, i = SHOTS / name, 2
        while p.exists():
            p = SHOTS / ("%s_%d%s" % (Path(name).stem, i, Path(name).suffix))
            i += 1
        img.save(str(p))
        return p

    def compose(self, body):
        bar = self.bars.currentWidget().grab().toImage()
        img = QImage(self.width(), bar.height() + body.height(), QImage.Format.Format_ARGB32)
        img.fill(QColor(THEMES[self.active_id and self.term[self.active_id].kind or "ps"]["bg"]))
        p = QPainter(img)
        p.drawImage(0, 0, bar)
        p.drawImage(0, bar.height(), body)
        p.setPen(QColor("#3a3a3a"))
        p.drawRect(0, 0, img.width() - 1, img.height() - 1)
        p.end()
        return img

    def shot_window(self):
        self.toastl.hide()
        p = self.save_img(self.grab().toImage(), "%02d_%s.png" % (self.shot_n, self.prefix))
        self.shot_n += 1
        self.toast("Сохранено: " + p.name)

    def chunks(self, ranges):
        t = self.term[self.active_id]
        n = 0
        for i, (a, b) in enumerate(ranges, 1):
            self.save_img(self.compose(t.render_blocks(a, b, b >= t.document().blockCount() - 1)), "%s_%02d.png" % (self.prefix, i))
            n += 1
        self.toast("Сохранено файлов: %d (папка TerminalSim_screens)" % n if n else "Нечего сохранять")

    def shot_cmds(self):
        t = self.term[self.active_id]
        self.chunks([(a, b + 1) for a, b in t.marks])

    def shot_lines(self, n=None):
        if n is None:
            n, ok = QInputDialog.getInt(self, "Нарезка", "Строк в одном кусочке:", 20, 1, 500)
            if not ok:
                return
        total = self.term[self.active_id].document().blockCount()
        self.chunks([(i, min(i + n - 1, total - 1)) for i in range(0, total, n)])

    def shot_screens(self):
        t = self.term[self.active_id]
        self.shot_lines(max(1, t.viewport().height() // QFontMetrics(t.font()).lineSpacing()))

    # ---------- состояние ----------
    def save(self):
        for session_id, terminal in self.term.items():
            self.store.save(session_id, terminal.s)

    def resizeEvent(self, e):
        self.grip.move(self.width() - self.grip.width(), self.height() - self.grip.height())
        self.grip.raise_()

    def closeEvent(self, e):
        running = [self.records[sid]["name"] for sid, terminal in self.term.items() if terminal.busy]
        if running:
            answer = QMessageBox.question(
                self, "Выполняются виртуальные команды",
                "Остановить команды и закрыть приложение?\n" + "\n".join(running),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            for terminal in self.term.values():
                if terminal.busy:
                    terminal.timer.stop()
                    if terminal.gen is not None:
                        terminal.gen.close()
                    terminal.busy = False
                    self.finish_virtual_task(terminal.session_id)
        self.cleanup_worker.stop()
        self.save()
        super().closeEvent(e)


def main():
    qa = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(qa.exec())

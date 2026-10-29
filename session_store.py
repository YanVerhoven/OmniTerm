"""SQLite persistence and lifecycle management for virtual terminal sessions."""
import json
import math
import os
import sqlite3
import stat
import threading
import queue
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_TTL_HOURS = 12
CLEANUP_INTERVAL_SECONDS = 600


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat()


def session_label(record):
    shell = "PowerShell" if record["shell_profile"] == "ps" else "Bash"
    label = "%s — %s • VIRTUAL" % (record["name"], shell)
    if record.get("is_pinned"):
        label = "[PIN] " + label
    if record["session_type"] == "temporary":
        expiry = record.get("expires_at")
        if not record.get("auto_cleanup_enabled") or not expiry:
            label += " • БЕЗ АВТООЧИСТКИ"
        else:
            seconds = (datetime.fromisoformat(expiry) - _now()).total_seconds()
            if seconds <= 0:
                remaining = "истекает"
            else:
                minutes = math.ceil(seconds / 60)
                days, rest = divmod(minutes, 24 * 60)
                hours, mins = divmod(rest, 60)
                remaining = ("%dд " % days if days else "") + ("%dч " % hours if hours else "")
                remaining += "%dм" % mins if mins else ""
            label += " • %s" % remaining.strip()
    return label


class SessionStore:
    def __init__(self, path):
        self.last_cleanup_errors = []
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    shell_profile TEXT NOT NULL,
                    session_mode TEXT NOT NULL DEFAULT 'virtual',
                    session_type TEXT NOT NULL DEFAULT 'temporary',
                    created_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    expires_at TEXT,
                    auto_cleanup_enabled INTEGER NOT NULL DEFAULT 1,
                    cleanup_status TEXT NOT NULL DEFAULT 'active',
                    cleanup_error TEXT,
                    keep_workspace INTEGER NOT NULL DEFAULT 0,
                    workspace_path TEXT,
                    last_working_directory TEXT NOT NULL DEFAULT '',
                    state_json TEXT NOT NULL,
                    is_open INTEGER NOT NULL DEFAULT 1,
                    tab_order INTEGER NOT NULL DEFAULT 0,
                    closed_at TEXT,
                    is_pinned INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS cleanup_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    actions TEXT NOT NULL,
                    errors TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event TEXT NOT NULL DEFAULT 'cleanup'
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry
                    ON sessions(session_type, auto_cleanup_enabled, expires_at);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            if "closed_at" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN closed_at TEXT")
            if "workspace_path" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN workspace_path TEXT")
            if "is_pinned" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")
            has_legacy_log = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cleanup_log'"
            ).fetchone()
            if has_legacy_log:
                db.execute(
                    """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                    SELECT started_at, session_id, actions, errors, status, 'migrated'
                    FROM cleanup_log
                    WHERE NOT EXISTS (
                        SELECT 1 FROM cleanup_logs newer
                        WHERE newer.started_at=cleanup_log.started_at
                        AND newer.session_id=cleanup_log.session_id
                        AND newer.actions=cleanup_log.actions
                    )"""
                )
        self.workspaces = self.path.parent / "workspaces"
        self.workspaces.mkdir(parents=True, exist_ok=True)
        is_junction = getattr(os.path, "isjunction", lambda path: False)
        if self.workspaces.is_symlink() or is_junction(self.workspaces):
            raise OSError("Managed workspaces root cannot be a symlink or junction")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, session, name=None, session_type="temporary", ttl_hours=DEFAULT_TTL_HOURS,
               session_id=None, is_open=True, keep_workspace=False):
        if session_type not in ("temporary", "permanent"):
            raise ValueError("session_type must be temporary or permanent")
        if ttl_hours is not None and ttl_hours < 0:
            raise ValueError("ttl_hours cannot be negative")
        now = _now()
        sid = session_id or uuid.uuid4().hex
        expires = (now + timedelta(hours=ttl_hours)
                   if session_type == "temporary" and ttl_hours else None)
        state = session.to_dict()
        workspace = self._workspace_for(sid)
        workspace.mkdir()
        with self._connect() as db:
            order = db.execute("SELECT COALESCE(MAX(tab_order), -1) + 1 FROM sessions").fetchone()[0]
            db.execute(
                """INSERT INTO sessions
                (id, name, shell_profile, session_mode, session_type, created_at, last_activity_at,
                 expires_at, auto_cleanup_enabled, cleanup_status, keep_workspace,
                 workspace_path, last_working_directory, state_json, is_open, tab_order)
                VALUES (?, ?, ?, 'virtual', ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                (sid, name or ("PowerShell" if session.kind == "ps" else "Ubuntu"), session.kind,
                 session_type, _stamp(now), _stamp(now), _stamp(expires) if expires else None,
                 int(bool(expires)), int(keep_workspace), str(workspace), session.cwd,
                 json.dumps(state, ensure_ascii=False), int(is_open), order),
            )
        return sid

    def _workspace_for(self, session_id):
        if not session_id or Path(session_id).name != session_id or session_id in (".", ".."):
            raise ValueError("Invalid session identifier for workspace")
        return self.workspaces / session_id

    def _remove_workspace(self, workspace_path, session_id):
        if not workspace_path:
            return "No managed workspace was assigned."
        root = self.workspaces.resolve()
        candidate = Path(workspace_path)
        if not candidate.is_absolute():
            raise ValueError("Workspace path is not absolute")
        if candidate.name != session_id or candidate.name in (".", ".."):
            raise ValueError("Workspace path does not match the session identifier")
        is_junction = getattr(os.path, "isjunction", lambda path: False)
        if candidate.is_symlink() or is_junction(candidate):
            raise ValueError("Refusing to remove a workspace symlink or junction")
        resolved = candidate.resolve(strict=False)
        if os.path.normcase(str(resolved.parent)) != os.path.normcase(str(root)):
            raise ValueError("Workspace is outside the managed workspaces directory")
        if not resolved.exists():
            return "Managed workspace does not exist."
        self._remove_tree_without_following_links(resolved)
        return "Removed managed workspace: %s" % resolved

    @classmethod
    def _remove_tree_without_following_links(cls, root):
        with os.scandir(root) as entries:
            for entry in entries:
                path = Path(entry.path)
                is_junction = bool(getattr(entry, "is_junction", lambda: False)())
                if is_junction:
                    path.rmdir()
                elif entry.is_symlink():
                    path.unlink()
                elif entry.is_dir(follow_symlinks=False):
                    cls._remove_tree_without_following_links(path)
                else:
                    path.unlink()
        root.rmdir()

    def log_cleanup(self, session_id, actions, errors=(), status="checked", event="cleanup"):
        with self._connect() as db:
            cur = db.execute(
                """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (_stamp(_now()), session_id, json.dumps(list(actions), ensure_ascii=False),
                 json.dumps(list(errors), ensure_ascii=False), status, event),
            )
            return cur.lastrowid

    def cleanup_history(self, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM cleanup_logs ORDER BY id DESC LIMIT ?", (limit,))]


    def save(self, session_id, session):
        now = _stamp(_now())
        with self._connect() as db:
            cur = db.execute(
                """UPDATE sessions SET state_json=?, last_activity_at=?, last_working_directory=?
                WHERE id=?""",
                (json.dumps(session.to_dict(), ensure_ascii=False), now, session.cwd, session_id),
            )
            if not cur.rowcount:
                raise KeyError("Unknown session: %s" % session_id)

    def set_open(self, session_id, is_open, tab_order=None):
        with self._connect() as db:
            if tab_order is None:
                db.execute("UPDATE sessions SET is_open=?, closed_at=? WHERE id=?",
                           (int(is_open), None if is_open else _stamp(_now()), session_id))
            else:
                db.execute("UPDATE sessions SET is_open=?, tab_order=?, closed_at=? WHERE id=?",
                           (int(is_open), tab_order, None if is_open else _stamp(_now()), session_id))

    def rename(self, session_id, name):
        name = name.strip()
        if not name:
            raise ValueError("Session name cannot be empty")
        with self._connect() as db:
            cur = db.execute("UPDATE sessions SET name=? WHERE id=?", (name, session_id))
            if not cur.rowcount:
                raise KeyError("Unknown session: %s" % session_id)

    def list_sessions(self, opened=None):
        query = "SELECT * FROM sessions"
        params = ()
        if opened is not None:
            query += " WHERE is_open=?"
            params = (int(opened),)
        query += (" ORDER BY is_pinned DESC, tab_order, created_at" if opened is not False
                  else " ORDER BY closed_at DESC, last_activity_at DESC")
        with self._connect() as db:
            return [dict(row) for row in db.execute(query, params)]

    def load_state(self, session_id):
        with self._connect() as db:
            row = db.execute("SELECT state_json FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown session: %s" % session_id)
        return json.loads(row["state_json"])

    def extend(self, session_id, hours):
        if hours <= 0:
            raise ValueError("hours must be positive")
        with self._connect() as db:
            row = db.execute("SELECT session_type, expires_at FROM sessions WHERE id=?",
                             (session_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown session: %s" % session_id)
            if row["session_type"] != "temporary":
                raise ValueError("Permanent sessions do not expire")
            base = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else _now()
            if base < _now():
                base = _now()
            expires = _stamp(base + timedelta(hours=hours))
            db.execute("UPDATE sessions SET expires_at=?, auto_cleanup_enabled=1, cleanup_status='active', cleanup_error=NULL WHERE id=?",
                       (expires, session_id))

    def make_permanent(self, session_id):
        with self._connect() as db:
            cur = db.execute(
                """UPDATE sessions SET session_type='permanent', expires_at=NULL,
                auto_cleanup_enabled=0, cleanup_status='active', cleanup_error=NULL WHERE id=?""",
                (session_id,),
            )
            if not cur.rowcount:
                raise KeyError("Unknown session: %s" % session_id)

    def set_pinned(self, session_id, pinned):
        with self._connect() as db:
            row = db.execute("SELECT session_type FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown session: %s" % session_id)
            if pinned and row["session_type"] != "permanent":
                raise ValueError("Only permanent sessions can be pinned")
            db.execute("UPDATE sessions SET is_pinned=? WHERE id=?", (int(pinned), session_id))

    def set_auto_cleanup(self, session_id, enabled, ttl_hours=DEFAULT_TTL_HOURS):
        if enabled and ttl_hours <= 0:
            raise ValueError("ttl_hours must be positive when cleanup is enabled")
        with self._connect() as db:
            row = db.execute("SELECT session_type FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown session: %s" % session_id)
            if row["session_type"] != "temporary":
                raise ValueError("Auto-cleanup applies only to temporary sessions")
            expires = _stamp(_now() + timedelta(hours=ttl_hours)) if enabled else None
            db.execute(
                """UPDATE sessions SET auto_cleanup_enabled=?, expires_at=?,
                cleanup_status='active', cleanup_error=NULL WHERE id=?""",
                (int(enabled), expires, session_id),
            )

    def delete(self, session_id, reason="manual"):
        now = _stamp(_now())
        failure = None
        cause = None
        with self._connect() as db:
            row = db.execute("SELECT id, workspace_path, keep_workspace FROM sessions WHERE id=?",
                             (session_id,)).fetchone()
            if row is None:
                return False
            actions = ["Removed virtual session metadata and state."]
            if row["keep_workspace"]:
                actions.append("Kept workspace by user setting.")
            else:
                try:
                    actions.append(self._remove_workspace(row["workspace_path"], session_id))
                except (OSError, ValueError) as error:
                    message = "%s: %s" % (type(error).__name__, error)
                    db.execute("UPDATE sessions SET cleanup_status='failed', cleanup_error=? WHERE id=?",
                               (message, session_id))
                    db.execute(
                        """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                        VALUES (?, ?, ?, ?, 'failed', ?)""",
                        (now, session_id, json.dumps(actions, ensure_ascii=False),
                         json.dumps([message], ensure_ascii=False), reason),
                    )
                    failure = OSError("Session workspace cleanup failed: %s" % message)
                    cause = error
            if failure is None:
                db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                db.execute(
                    """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                    VALUES (?, ?, ?, '[]', 'completed', ?)""",
                    (now, session_id, json.dumps(actions, ensure_ascii=False), reason),
                )
        if failure is not None:
            raise failure from cause
        return True

    def cleanup_expired(self, now=None, defer_finalize=False):
        now = now or _now()
        stamp = _stamp(now)
        removed = []
        errors = []
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, workspace_path, keep_workspace FROM sessions WHERE session_type='temporary'
                AND auto_cleanup_enabled=1 AND expires_at IS NOT NULL AND expires_at<=?""",
                (stamp,),
            ).fetchall()
            db.execute(
                """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                VALUES (?, '__cleanup_worker__', ?, '[]', 'running', 'check-start')""",
                (stamp, json.dumps(["Checking for expired temporary sessions."])),
            )
            for row in rows:
                session_id = row["id"]
                actions = ["Expired temporary session discovered."]
                db.execute("UPDATE sessions SET cleanup_status='cleaning', cleanup_error=NULL WHERE id=?",
                           (session_id,))
                try:
                    if row["keep_workspace"]:
                        actions.append("Workspace retained by keep_workspace setting.")
                    else:
                        actions.append(self._remove_workspace(row["workspace_path"], session_id))
                except (OSError, ValueError) as error:
                    message = "%s: %s" % (type(error).__name__, error)
                    errors.append((session_id, message))
                    db.execute("UPDATE sessions SET cleanup_status='failed', cleanup_error=? WHERE id=?",
                               (message, session_id))
                    db.execute(
                        """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                        VALUES (?, ?, ?, ?, 'failed', 'workspace-cleanup')""",
                        (_stamp(_now()), session_id, json.dumps(actions, ensure_ascii=False),
                         json.dumps([message], ensure_ascii=False)),
                    )
                    continue
                if defer_finalize:
                    db.execute(
                        "UPDATE sessions SET cleanup_status='workspace_cleaned', cleanup_error=NULL WHERE id=?",
                        (session_id,),
                    )
                else:
                    db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                removed.append(session_id)
                status = "pending" if defer_finalize else "completed"
                event = "awaiting-ui-finalize" if defer_finalize else "expired-session-cleanup"
                if not defer_finalize:
                    actions.append("Removed session metadata and virtual state.")
                db.execute(
                    """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                    VALUES (?, ?, ?, '[]', ?, ?)""",
                    (_stamp(_now()), session_id, json.dumps(actions, ensure_ascii=False), status, event),
                )
            db.execute(
                """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                VALUES (?, '__cleanup_worker__', ?, ?, ?, 'check-finished')""",
                (_stamp(_now()), json.dumps(["Cleanup check completed.", "Removed sessions: %d" % len(removed)]),
                 json.dumps([{"session_id": sid, "error": error} for sid, error in errors],
                            ensure_ascii=False),
                 "failed" if errors else "completed"),
            )
        self.last_cleanup_errors = errors
        return removed

    def finalize_cleanup(self, session_id):
        now = _stamp(_now())
        with self._connect() as db:
            row = db.execute(
                """SELECT cleanup_status, session_type, auto_cleanup_enabled, expires_at
                FROM sessions WHERE id=?""",
                (session_id,),
            ).fetchone()
            if row is None:
                return False
            if row["cleanup_status"] != "workspace_cleaned":
                return False
            if (row["session_type"] != "temporary" or not row["auto_cleanup_enabled"]
                    or not row["expires_at"] or datetime.fromisoformat(row["expires_at"]) > _now()):
                db.execute("UPDATE sessions SET cleanup_status='active' WHERE id=?", (session_id,))
                db.execute(
                    """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                    VALUES (?, ?, ?, '[]', 'cancelled', 'cleanup-cancelled')""",
                    (now, session_id, json.dumps(
                        ["Finalization cancelled because session expiry or type changed."],
                        ensure_ascii=False)),
                )
                return False
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            db.execute(
                """INSERT INTO cleanup_logs(started_at, session_id, actions, errors, status, event)
                VALUES (?, ?, ?, '[]', 'completed', 'expired-session-finalized')""",
                (now, session_id, json.dumps(
                    ["Removed expired session metadata and virtual state after UI/task teardown."],
                    ensure_ascii=False)),
            )
        return True

    def migrate_json(self, saved):
        if not isinstance(saved, dict):
            return
        if self.list_sessions():
            return
        for kind in ("ps", "bash"):
            data = saved.get(kind)
            if not isinstance(data, dict):
                continue
            from core import Session
            try:
                session = Session.from_dict(data)
            except (KeyError, TypeError, ValueError):
                continue
            self.create(session, session_id="legacy-" + kind, session_type="permanent")


class BackgroundCleanupWorker:
    def __init__(self, store, interval_seconds=CLEANUP_INTERVAL_SECONDS):
        if interval_seconds <= 0:
            raise ValueError("Cleanup interval must be positive")
        self.store = store
        self.interval_seconds = interval_seconds
        self.results = queue.Queue()
        self._stop = threading.Event()
        self._trigger = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="session-cleanup", daemon=True)
        self._thread.start()

    def request_check(self):
        self._trigger.set()

    def stop(self, timeout=5):
        self._stop.set()
        self._trigger.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self):
        while not self._stop.is_set():
            try:
                removed = self.store.cleanup_expired(defer_finalize=True)
                errors = self.store.last_cleanup_errors
                self.results.put((removed, errors, None))
            except (OSError, sqlite3.Error, ValueError) as error:
                message = "%s: %s" % (type(error).__name__, error)
                try:
                    self.store.log_cleanup("__cleanup_worker__", ["Cleanup check failed."],
                                           [message], status="failed", event="check-error")
                except sqlite3.Error as audit_error:
                    message += "; cleanup audit write failed: %s" % audit_error
                self.results.put(([], [("__cleanup_worker__", message)], message))
            if self._stop.is_set():
                break
            self._trigger.wait(self.interval_seconds)
            self._trigger.clear()

"""Private local SQLite store. Telegram IDs, session secrets and messages stay here."""
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Annotation, Chat, Settings


def utcnow():
    return datetime.now(timezone.utc)


def private_json(path: Path, value: dict):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".private-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / "crm.sqlite3"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS chats (
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, annotation TEXT NOT NULL DEFAULT '{}',
              llm TEXT, present INTEGER NOT NULL DEFAULT 1, seen_scan TEXT
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches (
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def metadata(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_metadata(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return Settings.model_validate(self.metadata("settings", {}))

    def save_settings(self, settings: Settings):
        self.set_metadata("settings", settings.model_dump(mode="json"))

    def upsert(self, chat: Chat, scan_id=None):
        with self.db:
            self.db.execute("""INSERT INTO chats(id,payload,present,seen_scan) VALUES (?,?,1,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,present=1,seen_scan=excluded.seen_scan""",
                (chat.id, chat.model_dump_json(), scan_id))

    def finish_scan(self, scan_id):
        with self.db:
            self.db.execute("UPDATE chats SET present=0 WHERE seen_scan IS NULL OR seen_scan != ?", (scan_id,))
        self.set_metadata("last_scan_at", utcnow().isoformat())

    def get(self, chat_id):
        row = self.db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        return self._decode(row) if row else None

    def all(self):
        return [self._decode(row) for row in self.db.execute("SELECT * FROM chats WHERE present=1")]

    @staticmethod
    def _decode(row):
        return {"chat": Chat.model_validate_json(row["payload"]),
                "annotation": Annotation.model_validate_json(row["annotation"]),
                "llm": json.loads(row["llm"]) if row["llm"] else None}

    def annotate(self, chat_id, annotation: Annotation):
        with self.db:
            cursor = self.db.execute("UPDATE chats SET annotation=? WHERE id=?", (annotation.model_dump_json(), chat_id))
        if not cursor.rowcount:
            raise KeyError(chat_id)

    def save_llm(self, chat_id, result):
        with self.db:
            self.db.execute("UPDATE chats SET llm=? WHERE id=?", (json.dumps(result, ensure_ascii=False), chat_id))

    def create_batch(self, payload):
        batch_id = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO batches VALUES (?,?,?)", (batch_id, json.dumps(payload, ensure_ascii=False), utcnow().isoformat()))
            self.db.execute("DELETE FROM batches WHERE julianday(created_at) < julianday('now', '-7 days')")
        return batch_id

    def batch(self, batch_id):
        row = self.db.execute("SELECT payload,created_at FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise ValueError("Пачка не найдена. Скопируйте диалоги заново.")
        if (utcnow() - datetime.fromisoformat(row["created_at"])).total_seconds() > 7 * 86400:
            raise ValueError("Пачка старше 7 дней. Создайте новую.")
        return json.loads(row["payload"])

    def credentials(self):
        path = self.directory / "credentials.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def save_credentials(self, **updates):
        private_json(self.directory / "credentials.json", {**self.credentials(), **updates})

    def clear_demo(self):
        with self.db:
            for row in self.db.execute("SELECT id,payload FROM chats").fetchall():
                if json.loads(row["payload"]).get("demo"):
                    self.db.execute("DELETE FROM chats WHERE id=?", (row["id"],))


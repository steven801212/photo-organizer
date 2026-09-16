from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import hmac
import os
import secrets
import sqlite3
from threading import RLock


AUTH_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','user')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    event_type TEXT NOT NULL,
    details TEXT NOT NULL,
    source_ip TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def valid_username(value: str) -> bool:
    return 1 <= len(value) <= 32 and value[0].isalnum() and all(c.isalnum() or c in "-_" for c in value)


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt, expected = encoded.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


class AuthStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(data_dir / "accounts.db", timeout=10, check_same_thread=False)
        self.lock = RLock()
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(AUTH_SCHEMA)
        self.connection.execute("PRAGMA busy_timeout=10000")

    def close(self):
        with self.lock:
            self.connection.close()

    def bootstrap(self, username: str, password: str) -> None:
        with self.lock:
            if self.connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
                return
            if not valid_username(username) or not password:
                raise ValueError("管理員帳號格式錯誤，密碼不可空白")
            self.create_user(username, password, "admin")

    def create_user(self, username: str, password: str, role: str = "user") -> int:
        if not valid_username(username) or not password or role not in {"admin", "user"}:
            raise ValueError("帳號格式錯誤或密碼不可空白")
        with self.lock:
            cursor = self.connection.execute(
                "INSERT INTO accounts(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                (username, password_hash(password), role, utcnow().isoformat()),
            )
            self.connection.commit()
        return int(cursor.lastrowid)

    def list_users(self):
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT id,username,role,active,created_at FROM accounts ORDER BY username"
            ).fetchall()]

    def set_active(self, user_id: int, active: bool) -> bool:
        with self.lock:
            cursor = self.connection.execute("UPDATE accounts SET active=? WHERE id=?", (int(active), user_id))
            if not active: self.connection.execute("DELETE FROM sessions WHERE account_id=?", (user_id,))
            self.connection.commit(); return cursor.rowcount == 1

    def reset_password(self, user_id: int, password: str) -> bool:
        if not password: raise ValueError("密碼不可空白")
        with self.lock:
            cursor = self.connection.execute("UPDATE accounts SET password_hash=? WHERE id=?", (password_hash(password), user_id))
            self.connection.execute("DELETE FROM sessions WHERE account_id=?", (user_id,))
            self.connection.commit(); return cursor.rowcount == 1

    def login(self, username: str, password: str):
        with self.lock:
            row = self.connection.execute("SELECT * FROM accounts WHERE username=? AND active=1", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        expires = utcnow() + timedelta(hours=12)
        with self.lock:
            self.connection.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow().isoformat(),))
            self.connection.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                                    (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, expires.isoformat()))
            self.connection.commit()
        return {"token": token, "csrf": csrf, "username": row["username"], "role": row["role"]}

    def session(self, token: str | None):
        if not token:
            return None
        with self.lock:
            return self.connection.execute(
                """SELECT accounts.id,accounts.username,accounts.role,sessions.csrf_token,sessions.expires_at
                   FROM sessions JOIN accounts ON accounts.id=sessions.account_id
                   WHERE sessions.token_hash=? AND accounts.active=1 AND sessions.expires_at>?""",
                (hashlib.sha256(token.encode()).hexdigest(), utcnow().isoformat()),
            ).fetchone()

    def logout(self, token: str | None):
        if token:
            with self.lock:
                self.connection.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                self.connection.commit()

    def audit(self, account_id: int, event: str, details: str, source_ip: str):
        with self.lock:
            self.connection.execute("INSERT INTO audit_events(account_id,event_type,details,source_ip,created_at) VALUES(?,?,?,?,?)",
                                    (account_id, event, details, source_ip, utcnow().isoformat()))
            self.connection.commit()

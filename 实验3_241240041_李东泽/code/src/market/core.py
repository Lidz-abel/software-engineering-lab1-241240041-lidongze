"""Authentication, authorization, logging and common transaction helpers."""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from .database import Database
from .models import DomainError, Session, password_value, text, username_value


def command(function):
    """Commit business changes atomically; persist safe failure logs separately."""

    @wraps(function)
    def execute(self, token, *args, **kwargs):
        actor = None
        try:
            with self.database.transaction() as connection:
                actor = self.actor(connection, token)
                result = function(self, connection, actor, *args, **kwargs)
                return result
        except (DomainError, sqlite3.IntegrityError) as error:
            reason = str(error) if isinstance(error, DomainError) else "数据约束冲突"
            with self.database.transaction() as connection:
                self.log(
                    connection,
                    actor,
                    function.__name__,
                    "COMMAND",
                    None,
                    "",
                    "",
                    "FAILED",
                    reason,
                )
            raise DomainError(reason) from error

    return execute


class Core:
    """Shared foundation; clients receive tokens instead of trusted actor IDs."""

    def __init__(self, path, clock=None):
        self.database = Database(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def now(self):
        """Use an injectable UTC clock for exact timeout boundary testing."""
        return self.clock().isoformat(timespec="microseconds")

    def actor(self, connection, token):
        """Resolve a non-expired session and reject accounts disabled after login."""
        actor = connection.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
            "WHERE s.token=? AND s.expires_at>?",
            (token, self.now()),
        ).fetchone()
        if actor is None or actor["status"] != "ACTIVE":
            raise DomainError("请重新登录，或联系管理员确认账号状态")
        return actor

    @staticmethod
    def admin(actor):
        """Protect administrative operations in the service, not only in the UI."""
        if actor["role"] != "ADMIN":
            raise DomainError("仅管理员可以执行此操作")

    @staticmethod
    def owner(actor, seller_id):
        """Restrict ordinary users to their own records."""
        if actor["id"] != seller_id:
            raise DomainError("只能操作本人发布的商品")

    @staticmethod
    def one(connection, table, record_id):
        """Read one record using internal, fixed table identifiers."""
        allowed = {
            "users",
            "products",
            "reservations",
            "cancel_requests",
            "reports",
            "disputes",
            "categories",
            "notifications",
        }
        if table not in allowed:
            raise RuntimeError("Untrusted table identifier")
        row = connection.execute(
            f"SELECT * FROM {table} WHERE id=?", (record_id,)
        ).fetchone()
        if row is None:
            raise DomainError("记录不存在或已失效")
        return row

    def log(
        self,
        connection,
        actor,
        action,
        kind,
        object_id,
        before="",
        after="",
        result="SUCCESS",
        reason="",
    ):
        """Store only safe metadata, never password values or session tokens."""
        connection.execute(
            "INSERT INTO operation_logs(operator_id,action,object_type,object_id,"
            "before_state,after_state,result,reason,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                actor["id"] if actor else None,
                action,
                kind,
                object_id,
                before,
                after,
                result,
                reason,
                self.now(),
            ),
        )

    def notify(self, connection, receiver_id, event_key, content):
        """An event key makes retries unable to duplicate a notification."""
        connection.execute(
            "INSERT OR IGNORE INTO notifications(receiver_id,event_key,content,created_at) "
            "VALUES (?,?,?,?)",
            (receiver_id, event_key, content, self.now()),
        )

    @staticmethod
    def password_hash(password, salt):
        """Use PBKDF2 with a random per-account salt, not a fast plain hash."""
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            210000,
        ).hex()

    def register(self, username, password, contact):
        """Public registration cannot select the administrator role."""
        return self._create_user(username, password, contact, "USER")

    def bootstrap_admin(self, username, password):
        """Provision the first administrator only, through a local CLI command."""
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM users WHERE role='ADMIN'").fetchone():
                raise DomainError("管理员已存在，请使用管理员界面管理账号")
            return self._insert_user(
                connection, username, password, "平台管理员", "ADMIN"
            )

    def _create_user(self, username, password, contact, role):
        with self.database.transaction() as connection:
            return self._insert_user(connection, username, password, contact, role)

    def _insert_user(self, connection, username, password, contact, role):
        username = username_value(username)
        password = password_value(password)
        contact = text(contact, "联系方式", 100)
        salt = secrets.token_hex(16)
        try:
            cursor = connection.execute(
                "INSERT INTO users(username,password_hash,salt,contact,role,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    username,
                    self.password_hash(password, salt),
                    salt,
                    contact,
                    role,
                    self.now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise DomainError("用户名已存在") from error
        self.log(connection, None, "register", "USER", cursor.lastrowid)
        return cursor.lastrowid

    def login(self, username, password):
        """Issue an eight-hour random session; log failed logins without secrets."""
        with self.database.transaction() as connection:
            actor = connection.execute(
                "SELECT * FROM users WHERE username=?",
                (str(username).strip(),),
            ).fetchone()
            valid = actor is not None and actor["status"] == "ACTIVE"
            if valid:
                valid = hmac.compare_digest(
                    self.password_hash(str(password), actor["salt"]),
                    actor["password_hash"],
                )
            if not valid:
                self.log(
                    connection,
                    actor,
                    "login",
                    "USER",
                    None,
                    result="FAILED",
                    reason="账号或密码错误，或账号已禁用",
                )
            else:
                token = secrets.token_urlsafe(32)
                expiry = (self.clock() + timedelta(hours=8)).isoformat(
                    timespec="microseconds"
                )
                connection.execute(
                    "INSERT INTO sessions(token,user_id,expires_at) VALUES (?,?,?)",
                    (token, actor["id"], expiry),
                )
                self.log(connection, actor, "login", "USER", actor["id"])
                return Session(token, actor["id"], actor["username"], actor["role"])
        raise DomainError("账号或密码错误，或账号已禁用")

    def logout(self, token):
        """Revoke a session immediately."""
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE token=?", (token,))

    @command
    def update_contact(self, connection, actor, contact):
        """Update private contact data; logs contain no contact contents."""
        contact = text(contact, "联系方式", 100)
        connection.execute(
            "UPDATE users SET contact=? WHERE id=?", (contact, actor["id"])
        )
        self.log(connection, actor, "update_contact", "USER", actor["id"])

    @command
    def change_password(self, connection, actor, old_password, new_password):
        """Rotate the password and invalidate every existing session."""
        if not hmac.compare_digest(
            self.password_hash(old_password, actor["salt"]), actor["password_hash"]
        ):
            raise DomainError("原密码不正确")
        salt = secrets.token_hex(16)
        hashed = self.password_hash(password_value(new_password), salt)
        connection.execute(
            "UPDATE users SET salt=?,password_hash=? WHERE id=?",
            (salt, hashed, actor["id"]),
        )
        connection.execute("DELETE FROM sessions WHERE user_id=?", (actor["id"],))
        self.log(connection, actor, "change_password", "USER", actor["id"])

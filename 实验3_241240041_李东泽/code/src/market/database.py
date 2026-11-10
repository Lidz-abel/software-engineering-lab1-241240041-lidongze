"""SQLite schema and explicit transaction ownership for all writes."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 password_hash TEXT NOT NULL, salt TEXT NOT NULL, contact TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('USER','ADMIN')),
 status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISABLED')),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, seller_id INTEGER NOT NULL REFERENCES users(id),
 category_id INTEGER NOT NULL REFERENCES categories(id),
 status TEXT NOT NULL CHECK(status IN ('DRAFT','PENDING_AUDIT','AUDIT_REJECTED',
 'PUBLISHED','RESERVED','CONFIRMED','COMPLETED','DELISTED','VIOLATION_DELISTED','FROZEN')),
 current_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_versions (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 version_no INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
 price_cents INTEGER NOT NULL CHECK(price_cents > 0), condition TEXT NOT NULL,
 handover_place TEXT NOT NULL, image_path TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, UNIQUE(product_id, version_no)
);
CREATE TABLE IF NOT EXISTS audits (
 id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES product_versions(id),
 auditor_id INTEGER NOT NULL REFERENCES users(id), result TEXT NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 buyer_id INTEGER NOT NULL REFERENCES users(id), request_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING_SELLER','CONFIRMED','CANCEL_PENDING',
 'CANCELLED','REJECTED','EXPIRED','COMPLETED','FROZEN')),
 title_snapshot TEXT NOT NULL, price_snapshot INTEGER NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 UNIQUE(buyer_id, request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_reservation
 ON reservations(product_id) WHERE status IN
 ('PENDING_SELLER','CONFIRMED','CANCEL_PENDING','FROZEN');
CREATE TABLE IF NOT EXISTS cancel_requests (
 id INTEGER PRIMARY KEY, reservation_id INTEGER NOT NULL REFERENCES reservations(id),
 applicant_id INTEGER NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING', handling_note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY, reporter_id INTEGER NOT NULL REFERENCES users(id),
 product_id INTEGER NOT NULL REFERENCES products(id), reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING', handling_result TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_report
 ON reports(reporter_id,product_id) WHERE status='PENDING';
CREATE TABLE IF NOT EXISTS disputes (
 id INTEGER PRIMARY KEY, reservation_id INTEGER NOT NULL REFERENCES reservations(id),
 applicant_id INTEGER NOT NULL REFERENCES users(id), description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'OPEN', conclusion TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_dispute
 ON disputes(reservation_id) WHERE status='OPEN';
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, receiver_id INTEGER NOT NULL REFERENCES users(id),
 event_key TEXT NOT NULL UNIQUE, content TEXT NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_logs (
 id INTEGER PRIMARY KEY, operator_id INTEGER REFERENCES users(id),
 action TEXT NOT NULL, object_type TEXT NOT NULL, object_id INTEGER,
 before_state TEXT NOT NULL, after_state TEXT NOT NULL,
 result TEXT NOT NULL, reason TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_log_update BEFORE UPDATE ON operation_logs
 BEGIN SELECT RAISE(ABORT, 'operation logs are append only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_log_delete BEFORE DELETE ON operation_logs
 BEGIN SELECT RAISE(ABORT, 'operation logs are append only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_version BEFORE UPDATE ON product_versions
 BEGIN SELECT RAISE(ABORT, 'product versions are immutable'); END;
"""


class Database:
    """One connection per operation, usable by independent desktop instances."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                [(name,) for name in ("教材书籍", "数码产品", "生活用品", "运动器材")],
            )

    @contextmanager
    def connect(self):
        """Close connections even on validation errors; leave transactions explicit."""
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        """Reserve the writer before checking states to prevent last-item races."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

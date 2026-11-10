"""Read models with explicit visibility, bounded queries and safe CSV export."""

import csv
from pathlib import Path

from .core import command
from .models import DomainError, money_cents

PRODUCT_SELECT = """
SELECT p.*,v.title,v.description,v.price_cents,v.condition,v.handover_place,v.image_path,
 c.name AS category,u.username AS seller
FROM products p JOIN product_versions v
 ON v.product_id=p.id AND v.version_no=p.current_version
JOIN categories c ON c.id=p.category_id JOIN users u ON u.id=p.seller_id
"""


class QueryMixin:
    """No read operation returns password hashes, session tokens or unrelated contacts."""

    def categories(self, include_disabled=False):
        """Categories are non-sensitive; public UI needs only enabled options."""
        with self.database.connect() as connection:
            sql = "SELECT * FROM categories"
            if not include_disabled:
                sql += " WHERE enabled=1"
            return [dict(row) for row in connection.execute(sql + " ORDER BY id")]

    def products(
        self,
        token=None,
        scope="public",
        keyword="",
        category_id=None,
        minimum="",
        maximum="",
        status="",
        limit=500,
    ):
        """Combine filters with parameter binding; enforce scope before caller filters."""
        conditions = []
        parameters = []
        with self.database.connect() as connection:
            actor = self.actor(connection, token) if token else None
            if scope == "mine":
                if actor is None:
                    raise DomainError("请先登录")
                conditions.append("p.seller_id=?")
                parameters.append(actor["id"])
            elif scope == "admin":
                if actor is None:
                    raise DomainError("请先登录")
                self.admin(actor)
            elif scope == "public":
                conditions.append("p.status IN ('PUBLISHED','RESERVED','CONFIRMED')")
                conditions.append("u.status='ACTIVE'")
            else:
                raise DomainError("未知查询范围")
            if keyword:
                conditions.append(
                    "(v.title LIKE ? ESCAPE '\\' OR v.description LIKE ? ESCAPE '\\')"
                )
                escaped = (
                    keyword.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                parameters.extend([f"%{escaped}%"] * 2)
            if category_id is not None:
                conditions.append("p.category_id=?")
                parameters.append(category_id)
            for value, operator in ((minimum, ">="), (maximum, "<=")):
                if str(value).strip():
                    conditions.append(f"v.price_cents{operator}?")
                    parameters.append(money_cents(value))
            if minimum and maximum and money_cents(minimum) > money_cents(maximum):
                raise DomainError("最低价不能大于最高价")
            if status:
                conditions.append("p.status=?")
                parameters.append(status)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            parameters.append(max(1, min(int(limit), 1000)))
            rows = connection.execute(
                PRODUCT_SELECT + where + " ORDER BY p.id DESC LIMIT ?", parameters
            )
            return [dict(row) for row in rows]

    def product_history(self, token, product_id):
        """Only the owner and administrator can inspect unpublished historical versions."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            product = self.one(connection, "products", product_id)
            if actor["role"] != "ADMIN":
                self.owner(actor, product["seller_id"])
            versions = connection.execute(
                "SELECT * FROM product_versions WHERE product_id=? ORDER BY version_no",
                (product_id,),
            ).fetchall()
            audits = connection.execute(
                "SELECT a.*,v.version_no FROM audits a "
                "JOIN product_versions v ON v.id=a.version_id "
                "WHERE v.product_id=? ORDER BY a.id",
                (product_id,),
            ).fetchall()
            return {
                "versions": [dict(row) for row in versions],
                "audits": [dict(row) for row in audits],
            }

    def reservations(self, token):
        """Return both buyer and seller perspectives in one bounded view."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            sql = (
                "SELECT r.*,p.seller_id,b.username AS buyer,s.username AS seller "
                "FROM reservations r JOIN products p ON p.id=r.product_id "
                "JOIN users b ON b.id=r.buyer_id JOIN users s ON s.id=p.seller_id"
            )
            parameters = []
            if actor["role"] != "ADMIN":
                sql += " WHERE r.buyer_id=? OR p.seller_id=?"
                parameters = [actor["id"], actor["id"]]
            return [
                dict(row)
                for row in connection.execute(
                    sql + " ORDER BY r.id DESC LIMIT 1000", parameters
                )
            ]

    def trade_details(self, token, reservation_id):
        """Release counterpart contact only in confirmed transactions to participants."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            reservation = self.one(connection, "reservations", reservation_id)
            product = self.one(connection, "products", reservation["product_id"])
            if actor["id"] not in (reservation["buyer_id"], product["seller_id"]):
                raise DomainError("仅交易双方可以查看联系方式")
            result = dict(reservation)
            result["contact"] = "卖家确认后显示联系方式"
            if reservation["status"] in {"CONFIRMED", "CANCEL_PENDING", "COMPLETED"}:
                counterpart = (
                    product["seller_id"]
                    if actor["id"] == reservation["buyer_id"]
                    else reservation["buyer_id"]
                )
                result["contact"] = self.one(connection, "users", counterpart)[
                    "contact"
                ]
            result["cancel_requests"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM cancel_requests WHERE reservation_id=? ORDER BY id",
                    (reservation_id,),
                )
            ]
            return result

    def notifications(self, token):
        """The receiver constraint cannot be overridden by a UI filter."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM notifications WHERE receiver_id=? ORDER BY id DESC LIMIT 1000",
                    (actor["id"],),
                )
            ]

    @command
    def mark_read(self, connection, actor, notification_id):
        """Mark a personal notification as read, including retry-safe repeats."""
        notification = self.one(connection, "notifications", notification_id)
        if notification["receiver_id"] != actor["id"]:
            raise DomainError("无权操作他人通知")
        connection.execute(
            "UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,)
        )

    def admin_records(self, token, kind):
        """Whitelist table names and omit all password material from user lists."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            self.admin(actor)
            if kind == "users":
                sql = "SELECT id,username,role,status,created_at FROM users ORDER BY id"
            elif kind in {"reports", "disputes", "categories"}:
                sql = f"SELECT * FROM {kind} ORDER BY id DESC LIMIT 1000"
            else:
                raise DomainError("未知管理对象")
            return [dict(row) for row in connection.execute(sql)]

    def logs(self, token, action="", result="", start="", end=""):
        """Admins see all; ordinary users see only their own key operations."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            conditions = []
            parameters = []
            if actor["role"] != "ADMIN":
                conditions.append("operator_id=?")
                parameters.append(actor["id"])
            for column, value, operator in (
                ("action", action, "="),
                ("result", result, "="),
                ("occurred_at", start, ">="),
                ("occurred_at", end, "<="),
            ):
                if value:
                    conditions.append(f"{column}{operator}?")
                    parameters.append(value)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM operation_logs"
                    + where
                    + " ORDER BY id DESC LIMIT 1000",
                    parameters,
                )
            ]

    def statistics(self, token):
        """Aggregate product states, categories and completed snapshot totals."""
        with self.database.connect() as connection:
            actor = self.actor(connection, token)
            where = "" if actor["role"] == "ADMIN" else " WHERE seller_id=?"
            parameters = [] if not where else [actor["id"]]
            states = connection.execute(
                "SELECT status,COUNT(*) AS count FROM products"
                + where
                + " GROUP BY status",
                parameters,
            ).fetchall()
            category_where = "" if not where else " WHERE p.seller_id=?"
            categories = connection.execute(
                "SELECT c.name,COUNT(*) AS count FROM products p JOIN categories c "
                "ON c.id=p.category_id" + category_where + " GROUP BY c.id",
                parameters,
            ).fetchall()
            trade_where = "" if not where else " AND p.seller_id=?"
            total = connection.execute(
                "SELECT COUNT(*) AS count,COALESCE(SUM(r.price_snapshot),0) AS cents "
                "FROM reservations r JOIN products p ON p.id=r.product_id "
                "WHERE r.status='COMPLETED'" + trade_where,
                parameters,
            ).fetchone()
            return {
                "states": [dict(row) for row in states],
                "categories": [dict(row) for row in categories],
                "completed": dict(total),
            }

    @staticmethod
    def export_csv(path, records):
        """Export only already-authorized query results, escaping spreadsheet formulas."""
        if not records:
            raise DomainError("没有可导出的记录")
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            for record in records:
                safe = {}
                for key, value in record.items():
                    if isinstance(value, str) and value.lstrip().startswith(
                        ("=", "+", "-", "@")
                    ):
                        value = "'" + value
                    safe[key] = value
                writer.writerow(safe)

"""Moderation and account restrictions with transaction-aware trade freezing."""

from .core import command
from .models import DomainError, require_state, text


class GovernanceMixin:
    """Administrative decisions preserve records and notify affected parties."""

    @command
    def report_product(self, connection, actor, product_id, reason):
        """Only visible products may be reported, once per pending reporter case."""
        product = self.one(connection, "products", product_id)
        require_state(product, {"PUBLISHED", "RESERVED", "CONFIRMED"})
        if product["seller_id"] == actor["id"]:
            raise DomainError("不能举报自己的商品")
        cursor = connection.execute(
            "INSERT INTO reports(reporter_id,product_id,reason,created_at) VALUES (?,?,?,?)",
            (actor["id"], product_id, text(reason, "举报原因", 1000), self.now()),
        )
        self.log(connection, actor, "report_product", "REPORT", cursor.lastrowid)
        return cursor.lastrowid

    def freeze_product(self, connection, actor, product, reason):
        """Freeze an active reservation and create a case the admin can resolve."""
        reservation = connection.execute(
            "SELECT * FROM reservations WHERE product_id=? AND status IN "
            "('PENDING_SELLER','CONFIRMED','CANCEL_PENDING','FROZEN')",
            (product["id"],),
        ).fetchone()
        target = "VIOLATION_DELISTED"
        if reservation:
            target = "FROZEN"
            if reservation["status"] != "FROZEN":
                self.finish_reservation(
                    connection,
                    actor,
                    reservation,
                    product,
                    "FROZEN",
                    "FROZEN",
                    "freeze_trade",
                    reason,
                )
            connection.execute(
                "INSERT OR IGNORE INTO disputes"
                "(reservation_id,applicant_id,description,created_at) "
                "VALUES (?,?,?,?)",
                (reservation["id"], actor["id"], reason, self.now()),
            )
        connection.execute(
            "UPDATE products SET status=? WHERE id=?", (target, product["id"])
        )
        self.notify(
            connection,
            product["seller_id"],
            f"govern:{product['id']}:{self.now()}",
            reason,
        )
        self.log(
            connection,
            actor,
            "freeze_product",
            "PRODUCT",
            product["id"],
            product["status"],
            target,
            reason=reason,
        )

    @command
    def handle_report(self, connection, actor, report_id, valid, conclusion):
        """A valid report removes visibility and opens dispute handling for active trades."""
        self.admin(actor)
        report = self.one(connection, "reports", report_id)
        require_state(report, {"PENDING"})
        conclusion = text(conclusion, "处理结论", 1000)
        product = self.one(connection, "products", report["product_id"])
        if valid and product["status"] != "COMPLETED":
            self.freeze_product(connection, actor, product, conclusion)
        target = "VALID" if valid else "INVALID"
        connection.execute(
            "UPDATE reports SET status=?,handling_result=? WHERE id=?",
            (target, conclusion, report_id),
        )
        self.notify(
            connection,
            report["reporter_id"],
            f"report:{report_id}",
            f"举报 #{report_id} 处理为 {target}：{conclusion}",
        )
        self.log(
            connection,
            actor,
            "handle_report",
            "REPORT",
            report_id,
            "PENDING",
            target,
            reason=conclusion,
        )

    @command
    def set_user_enabled(self, connection, actor, user_id, enabled, reason):
        """Revoke disabled sessions and freeze involved trades without losing history."""
        self.admin(actor)
        user = self.one(connection, "users", user_id)
        if user["role"] == "ADMIN":
            raise DomainError("此原型不允许禁用管理员")
        reason = text(reason, "账号处理原因", 500)
        target = "ACTIVE" if enabled else "DISABLED"
        if user["status"] == target:
            raise DomainError("账号已处于目标状态")
        connection.execute("UPDATE users SET status=? WHERE id=?", (target, user_id))
        if not enabled:
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            products = connection.execute(
                "SELECT DISTINCT p.* FROM products p LEFT JOIN reservations r ON r.product_id=p.id "
                "AND r.status IN ('PENDING_SELLER','CONFIRMED','CANCEL_PENDING','FROZEN') "
                "WHERE (p.seller_id=? OR r.buyer_id=?) AND p.status IN "
                "('PENDING_AUDIT','PUBLISHED','RESERVED','CONFIRMED','FROZEN')",
                (user_id, user_id),
            ).fetchall()
            for product in products:
                self.freeze_product(connection, actor, product, reason)
        self.log(
            connection,
            actor,
            "set_user_enabled",
            "USER",
            user_id,
            user["status"],
            target,
            reason=reason,
        )
        self.notify(
            connection,
            user_id,
            f"account:{user_id}:{self.now()}",
            f"账号状态更新为 {target}：{reason}",
        )

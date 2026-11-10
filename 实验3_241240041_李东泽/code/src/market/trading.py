"""Atomic reservations, cancellation negotiation, timeouts and dispute handling."""

from datetime import timedelta

from .core import command
from .models import DomainError, require_state, text


class TradingMixin:
    """Every public command runs inside the same transaction as product updates."""

    def reservation_party(self, connection, actor, reservation_id, seller=False):
        """Validate record participation before exposing or changing a trade."""
        reservation = self.one(connection, "reservations", reservation_id)
        product = self.one(connection, "products", reservation["product_id"])
        expected = product["seller_id"] if seller else reservation["buyer_id"]
        if actor["id"] != expected:
            raise DomainError("无权操作该预约")
        return reservation, product

    def finish_reservation(
        self,
        connection,
        actor,
        reservation,
        product,
        target,
        product_target,
        action,
        reason="",
    ):
        """One shared state mutation path keeps logs and both parties synchronized."""
        connection.execute(
            "UPDATE reservations SET status=? WHERE id=?",
            (target, reservation["id"]),
        )
        connection.execute(
            "UPDATE products SET status=? WHERE id=?",
            (product_target, product["id"]),
        )
        self.log(
            connection,
            actor,
            action,
            "RESERVATION",
            reservation["id"],
            reservation["status"],
            target,
            reason=reason,
        )
        event = f"trade:{reservation['id']}:{target}:{self.now()}"
        for receiver in (reservation["buyer_id"], product["seller_id"]):
            self.notify(
                connection,
                receiver,
                f"{event}:{receiver}",
                f"预约 #{reservation['id']} 更新为 {target}。{reason}",
            )

    @command
    def reserve(self, connection, actor, product_id, request_id):
        """Return the original result on an exact retry, even after state changes."""
        request_id = text(request_id, "请求编号", 100)
        previous = connection.execute(
            "SELECT * FROM reservations WHERE buyer_id=? AND request_id=?",
            (actor["id"], request_id),
        ).fetchone()
        if previous:
            if previous["product_id"] != product_id:
                raise DomainError("同一请求编号不能用于不同商品")
            return previous["id"]
        product = self.one(connection, "products", product_id)
        if product["seller_id"] == actor["id"]:
            raise DomainError("不能预约自己的商品")
        seller = self.one(connection, "users", product["seller_id"])
        if seller["status"] != "ACTIVE":
            raise DomainError("卖家账号不可用")
        require_state(product, {"PUBLISHED"})
        version = connection.execute(
            "SELECT * FROM product_versions WHERE product_id=? AND version_no=?",
            (product_id, product["current_version"]),
        ).fetchone()
        expiry = (self.clock() + timedelta(hours=24)).isoformat(timespec="microseconds")
        cursor = connection.execute(
            "INSERT INTO reservations(product_id,buyer_id,request_id,status,title_snapshot,"
            "price_snapshot,created_at,expires_at) VALUES (?,?,?,'PENDING_SELLER',?,?,?,?)",
            (
                product_id,
                actor["id"],
                request_id,
                version["title"],
                version["price_cents"],
                self.now(),
                expiry,
            ),
        )
        connection.execute(
            "UPDATE products SET status='RESERVED' WHERE id=?", (product_id,)
        )
        self.log(
            connection,
            actor,
            "reserve",
            "RESERVATION",
            cursor.lastrowid,
            "",
            "PENDING_SELLER",
        )
        self.notify(
            connection,
            product["seller_id"],
            f"reserve:{cursor.lastrowid}",
            f"商品 #{product_id} 收到预约，请在24小时内处理",
        )
        return cursor.lastrowid

    @command
    def respond_reservation(
        self, connection, actor, reservation_id, approved, reason="确认预约"
    ):
        """A seller arriving exactly at expiry loses to the server timeout rule."""
        reservation, product = self.reservation_party(
            connection, actor, reservation_id, True
        )
        require_state(reservation, {"PENDING_SELLER"})
        require_state(product, {"RESERVED"})
        if reservation["expires_at"] <= self.now():
            raise DomainError("预约已到期，请先刷新触发超时处理")
        reason = text(reason, "处理说明", 500)
        target = "CONFIRMED" if approved else "REJECTED"
        product_target = "CONFIRMED" if approved else "PUBLISHED"
        self.finish_reservation(
            connection,
            actor,
            reservation,
            product,
            target,
            product_target,
            "respond_reservation",
            reason,
        )

    @command
    def cancel_reservation(self, connection, actor, reservation_id, reason):
        """Before confirmation cancel directly; afterwards open a negotiated request."""
        reason = text(reason, "取消原因", 500)
        reservation, product = self.reservation_party(connection, actor, reservation_id)
        require_state(reservation, {"PENDING_SELLER", "CONFIRMED"})
        if reservation["status"] == "PENDING_SELLER":
            self.finish_reservation(
                connection,
                actor,
                reservation,
                product,
                "CANCELLED",
                "PUBLISHED",
                "cancel_reservation",
                reason,
            )
            return None
        cursor = connection.execute(
            "INSERT INTO cancel_requests(reservation_id,applicant_id,reason,created_at) "
            "VALUES (?,?,?,?)",
            (reservation_id, actor["id"], reason, self.now()),
        )
        self.finish_reservation(
            connection,
            actor,
            reservation,
            product,
            "CANCEL_PENDING",
            "CONFIRMED",
            "cancel_request",
            reason,
        )
        return cursor.lastrowid

    @command
    def handle_cancellation(self, connection, actor, reservation_id, approved, note):
        """Do not permit completion while a cancellation is pending."""
        reservation, product = self.reservation_party(
            connection, actor, reservation_id, True
        )
        require_state(reservation, {"CANCEL_PENDING"})
        note = text(note, "处理说明", 500)
        connection.execute(
            "UPDATE cancel_requests SET status=?,handling_note=? "
            "WHERE reservation_id=? AND status='PENDING'",
            ("APPROVED" if approved else "REJECTED", note, reservation_id),
        )
        self.finish_reservation(
            connection,
            actor,
            reservation,
            product,
            "CANCELLED" if approved else "CONFIRMED",
            "PUBLISHED" if approved else "CONFIRMED",
            "handle_cancellation",
            note,
        )

    @command
    def complete_trade(self, connection, actor, reservation_id):
        """The buyer records successful offline delivery; completion is terminal."""
        reservation, product = self.reservation_party(connection, actor, reservation_id)
        require_state(reservation, {"CONFIRMED"})
        require_state(product, {"CONFIRMED"})
        self.finish_reservation(
            connection,
            actor,
            reservation,
            product,
            "COMPLETED",
            "COMPLETED",
            "complete_trade",
        )

    def expire_reservations(self):
        """Idempotent local scheduler, also callable without a graphical window."""
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM reservations WHERE status='PENDING_SELLER' AND expires_at<=?",
                (self.now(),),
            ).fetchall()
            for reservation in rows:
                product = self.one(connection, "products", reservation["product_id"])
                self.finish_reservation(
                    connection,
                    None,
                    reservation,
                    product,
                    "EXPIRED",
                    "PUBLISHED",
                    "expire_reservation",
                    "24小时未响应",
                )
            return len(rows)

    @command
    def open_dispute(self, connection, actor, reservation_id, description):
        """Either party can freeze a confirmed trade for administrative resolution."""
        reservation = self.one(connection, "reservations", reservation_id)
        product = self.one(connection, "products", reservation["product_id"])
        if actor["id"] not in (reservation["buyer_id"], product["seller_id"]):
            raise DomainError("仅交易双方可以提出争议")
        require_state(reservation, {"CONFIRMED", "CANCEL_PENDING"})
        description = text(description, "争议说明", 1000)
        cursor = connection.execute(
            "INSERT INTO disputes(reservation_id,applicant_id,description,created_at) "
            "VALUES (?,?,?,?)",
            (reservation_id, actor["id"], description, self.now()),
        )
        self.finish_reservation(
            connection,
            actor,
            reservation,
            product,
            "FROZEN",
            "FROZEN",
            "open_dispute",
            description,
        )
        return cursor.lastrowid

    @command
    def resolve_dispute(self, connection, actor, dispute_id, resume, conclusion):
        """Resume a cleared trade or cancel with the product kept off the public market."""
        self.admin(actor)
        dispute = self.one(connection, "disputes", dispute_id)
        require_state(dispute, {"OPEN"})
        conclusion = text(conclusion, "处理结论", 1000)
        reservation = self.one(connection, "reservations", dispute["reservation_id"])
        product = self.one(connection, "products", reservation["product_id"])
        require_state(reservation, {"FROZEN"})
        if resume:
            for user_id in (reservation["buyer_id"], product["seller_id"]):
                if self.one(connection, "users", user_id)["status"] != "ACTIVE":
                    raise DomainError("交易方已禁用，不能恢复交易")
            require_state(product, {"FROZEN"})
        connection.execute(
            "UPDATE disputes SET status='RESOLVED',conclusion=? WHERE id=?",
            (conclusion, dispute_id),
        )
        connection.execute(
            "UPDATE cancel_requests SET status='REJECTED',handling_note=? "
            "WHERE reservation_id=? AND status='PENDING'",
            ("转入争议处理：" + conclusion, reservation["id"]),
        )
        self.finish_reservation(
            connection,
            actor,
            reservation,
            product,
            "CONFIRMED" if resume else "CANCELLED",
            "CONFIRMED" if resume else "VIOLATION_DELISTED",
            "resolve_dispute",
            conclusion,
        )

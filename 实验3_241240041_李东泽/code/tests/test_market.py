"""Executable business acceptance tests using isolated file-backed databases."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from market.models import DomainError, money_cents
from market.service import Marketplace


class MarketTests(unittest.TestCase):
    """Each case starts with independent users and a controlled UTC clock."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "market.db"
        self.instant = datetime(2026, 11, 10, 8, tzinfo=timezone.utc)
        self.market = Marketplace(self.path, lambda: self.instant)
        self.market.bootstrap_admin("admin", "Admin123!")
        self.seller_id = self.market.register("seller", "Seller123!", "seller-contact")
        self.buyer_id = self.market.register("buyer", "Buyer123!", "buyer-contact")
        self.market.register("other", "Other123!", "other-contact")
        self.admin = self.market.login("admin", "Admin123!").token
        self.seller = self.market.login("seller", "Seller123!").token
        self.buyer = self.market.login("buyer", "Buyer123!").token
        self.other = self.market.login("other", "Other123!").token

    def product(self, published=True):
        """Create a genuine draft and optionally execute the review workflow."""
        item = self.market.create_product(
            self.seller, 1, "软件工程", "教材，线下面交", "28.50", "九成新", "图书馆"
        )
        if published:
            self.market.submit_product(self.seller, item)
            self.market.audit_product(self.admin, item, 1, True, "允许发布")
        return item

    def trade(self, confirmed=False):
        """Prepare a reservation through public service methods."""
        product = self.product()
        reservation = self.market.reserve(self.buyer, product, "request-1")
        if confirmed:
            self.market.respond_reservation(self.seller, reservation, True)
        return product, reservation

    def row(self, table, record_id):
        """Inspect stored facts independently of the GUI read model."""
        with self.market.database.connect() as connection:
            return dict(self.market.one(connection, table, record_id))

    def test_case_insensitive_username_unique(self):
        with self.assertRaises(DomainError):
            self.market.register("BUYER", "Password!", "contact")

    def test_registration_does_not_create_admin(self):
        self.assertEqual(self.row("users", self.buyer_id)["role"], "USER")

    def test_invalid_login_is_logged(self):
        with self.assertRaises(DomainError):
            self.market.login("buyer", "wrong")
        logs = self.market.logs(self.admin, action="login", result="FAILED")
        self.assertEqual(len(logs), 1)
        self.assertNotIn("wrong", str(logs))

    def test_password_not_stored_plaintext(self):
        row = self.row("users", self.buyer_id)
        self.assertNotEqual(row["password_hash"], "Buyer123!")
        self.assertEqual(len(row["salt"]), 32)

    def test_logout_revokes_token(self):
        self.market.logout(self.buyer)
        with self.assertRaises(DomainError):
            self.market.notifications(self.buyer)

    def test_session_expiry(self):
        self.instant += timedelta(hours=8)
        with self.assertRaises(DomainError):
            self.market.notifications(self.buyer)

    def test_password_rotation_revokes_sessions(self):
        self.market.change_password(self.buyer, "Buyer123!", "NewPassword!")
        with self.assertRaises(DomainError):
            self.market.notifications(self.buyer)
        self.market.login("buyer", "NewPassword!")

    def test_invalid_money(self):
        for value in ("NaN", "Infinity", "-1", "0", "0.001", "1000000", "abc"):
            with self.subTest(value=value), self.assertRaises(DomainError):
                money_cents(value)
        self.assertEqual(money_cents("0.29"), 29)

    def test_private_draft_hidden(self):
        self.product(False)
        self.assertEqual(self.market.products(), [])
        self.assertEqual(len(self.market.products(self.seller, scope="mine")), 1)

    def test_non_admin_cannot_audit(self):
        product = self.product(False)
        self.market.submit_product(self.seller, product)
        with self.assertRaises(DomainError):
            self.market.audit_product(self.buyer, product, 1, True, "违规审核")
        self.assertEqual(self.row("products", product)["status"], "PENDING_AUDIT")

    def test_withdraw_then_stale_audit(self):
        product = self.product(False)
        self.market.submit_product(self.seller, product)
        self.market.withdraw_product(self.seller, product)
        with self.assertRaises(DomainError):
            self.market.audit_product(self.admin, product, 1, True, "旧页面")

    def test_rejected_product_can_resubmit(self):
        product = self.product(False)
        self.market.submit_product(self.seller, product)
        self.market.audit_product(self.admin, product, 1, False, "描述不充分")
        self.market.submit_product(self.seller, product)
        self.market.audit_product(self.admin, product, 1, True, "补充确认")
        self.assertEqual(self.row("products", product)["status"], "PUBLISHED")

    def test_version_history_and_stale_edit(self):
        product = self.product()
        values = (1, "新版教材", "描述更新", "30", "九成新", "宿舍门口")
        self.market.edit_product(self.seller, product, 1, *values)
        with self.assertRaises(DomainError):
            self.market.edit_product(self.seller, product, 1, *values)
        history = self.market.product_history(self.seller, product)
        self.assertEqual(len(history["versions"]), 2)
        self.assertEqual(history["versions"][0]["price_cents"], 2850)
        self.assertEqual(self.row("products", product)["status"], "DRAFT")

    def test_owner_authorization(self):
        product = self.product()
        with self.assertRaises(DomainError):
            self.market.delist_product(self.buyer, product)
        with self.assertRaises(DomainError):
            self.market.product_history(self.buyer, product)

    def test_combined_filters_and_sql_injection(self):
        self.product()
        self.assertEqual(
            len(
                self.market.products(
                    keyword="软件", category_id=1, minimum="28", maximum="29"
                )
            ),
            1,
        )
        self.assertEqual(self.market.products(keyword="' OR 1=1 --"), [])
        self.assertEqual(self.market.products(keyword="%"), [])

    def test_duplicate_request_returns_original(self):
        product, reservation = self.trade()
        self.assertEqual(
            self.market.reserve(self.buyer, product, "request-1"), reservation
        )
        self.assertEqual(len(self.market.reservations(self.buyer)), 1)
        self.assertEqual(len(self.market.notifications(self.seller)), 2)

    def test_request_key_cannot_change_payload(self):
        _, _ = self.trade()
        second = self.product()
        with self.assertRaises(DomainError):
            self.market.reserve(self.buyer, second, "request-1")

    def test_self_reservation_rejected(self):
        product = self.product()
        with self.assertRaises(DomainError):
            self.market.reserve(self.seller, product, "self")

    def test_concurrent_last_item(self):
        product = self.product()
        barrier = threading.Barrier(2)

        def compete(token):
            barrier.wait()
            try:
                return self.market.reserve(token, product, "concurrent")
            except DomainError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(compete, (self.buyer, self.other)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.row("products", product)["status"], "RESERVED")

    def test_database_unique_active_reservation(self):
        product, _ = self.trade()
        with self.market.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO reservations(product_id,buyer_id,request_id,status,"
                    "title_snapshot,price_snapshot,created_at,expires_at) "
                    "VALUES (?,?,'bypass','PENDING_SELLER','x',100,'a','b')",
                    (product, self.buyer_id),
                )

    def test_cancel_before_confirmation(self):
        product, reservation = self.trade()
        self.market.cancel_reservation(self.buyer, reservation, "不再需要")
        self.assertEqual(self.row("products", product)["status"], "PUBLISHED")
        self.assertEqual(self.row("reservations", reservation)["status"], "CANCELLED")

    def test_pending_cancel_blocks_completion(self):
        product, reservation = self.trade(True)
        self.market.cancel_reservation(self.buyer, reservation, "临时离校")
        with self.assertRaises(DomainError):
            self.market.complete_trade(self.buyer, reservation)
        self.assertEqual(self.row("products", product)["status"], "CONFIRMED")
        self.market.handle_cancellation(self.seller, reservation, True, "同意")
        self.assertEqual(self.row("products", product)["status"], "PUBLISHED")

    def test_rejected_cancellation_can_complete(self):
        product, reservation = self.trade(True)
        self.market.cancel_reservation(self.buyer, reservation, "取消")
        self.market.handle_cancellation(self.seller, reservation, False, "已交付")
        self.market.complete_trade(self.buyer, reservation)
        self.assertEqual(self.row("products", product)["status"], "COMPLETED")
        with self.assertRaises(DomainError):
            self.market.cancel_reservation(self.buyer, reservation, "撤销完成")

    def test_timeout_exact_boundary_and_repeat(self):
        product, reservation = self.trade()
        self.instant += timedelta(hours=24)
        self.assertEqual(self.market.expire_reservations(), 1)
        self.assertEqual(self.market.expire_reservations(), 0)
        self.assertEqual(self.row("reservations", reservation)["status"], "EXPIRED")
        self.assertEqual(self.row("products", product)["status"], "PUBLISHED")

    def test_seller_cannot_confirm_after_deadline(self):
        _, reservation = self.trade()
        self.instant += timedelta(hours=24)
        self.seller = self.market.login("seller", "Seller123!").token
        with self.assertRaises(DomainError):
            self.market.respond_reservation(self.seller, reservation, True)

    def test_contact_only_after_confirmation(self):
        _, reservation = self.trade()
        self.assertNotEqual(
            self.market.trade_details(self.buyer, reservation)["contact"],
            "seller-contact",
        )
        self.market.respond_reservation(self.seller, reservation, True)
        self.assertEqual(
            self.market.trade_details(self.buyer, reservation)["contact"],
            "seller-contact",
        )
        with self.assertRaises(DomainError):
            self.market.trade_details(self.other, reservation)

    def test_snapshot_and_statistics(self):
        _, reservation = self.trade(True)
        self.market.complete_trade(self.buyer, reservation)
        stats = self.market.statistics(self.seller)
        self.assertEqual(stats["completed"], {"count": 1, "cents": 2850})

    def test_report_freezes_and_resolves(self):
        product, reservation = self.trade(True)
        report = self.market.report_product(self.other, product, "疑似违规")
        self.market.handle_report(self.admin, report, True, "暂停交易核查")
        self.assertEqual(self.row("reservations", reservation)["status"], "FROZEN")
        case = self.market.admin_records(self.admin, "disputes")[0]
        self.market.resolve_dispute(self.admin, case["id"], False, "取消违规交易")
        self.assertEqual(self.row("products", product)["status"], "VIOLATION_DELISTED")

    def test_dispute_resume(self):
        product, reservation = self.trade(True)
        case = self.market.open_dispute(self.buyer, reservation, "约定地点有误")
        self.market.resolve_dispute(self.admin, case, True, "双方重新确认")
        self.assertEqual(self.row("products", product)["status"], "CONFIRMED")

    def test_account_disable_freezes_active_trade(self):
        product, reservation = self.trade(True)
        self.market.set_user_enabled(self.admin, self.buyer_id, False, "异常行为")
        with self.assertRaises(DomainError):
            self.market.notifications(self.buyer)
        self.assertEqual(self.row("products", product)["status"], "FROZEN")
        self.assertEqual(self.row("reservations", reservation)["status"], "FROZEN")

    def test_disabled_category_rejected(self):
        self.market.set_category(self.admin, 1, False)
        with self.assertRaises(DomainError):
            self.product(False)

    def test_logs_private_and_append_only(self):
        self.product()
        logs = self.market.logs(self.buyer)
        self.assertTrue(all(row["operator_id"] == self.buyer_id for row in logs))
        with self.market.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM operation_logs")

    def test_failed_operation_rolls_back_and_logs(self):
        product, _ = self.trade()
        with self.assertRaises(DomainError):
            self.market.reserve(self.other, product, "loser")
        self.assertEqual(len(self.market.reservations(self.other)), 0)
        self.assertEqual(
            len(self.market.logs(self.other, action="reserve", result="FAILED")), 1
        )

    def test_notification_ownership(self):
        self.product()
        note = self.market.notifications(self.seller)[0]
        with self.assertRaises(DomainError):
            self.market.mark_read(self.buyer, note["id"])
        self.market.mark_read(self.seller, note["id"])
        self.assertEqual(self.market.notifications(self.seller)[0]["is_read"], 1)

    def test_export_formula_escape(self):
        path = Path(self.temporary.name) / "export.csv"
        self.market.export_csv(path, [{"name": "=1+1", "id": 1}])
        self.assertIn("'=1+1", path.read_text(encoding="utf-8-sig"))

    def test_persistence_after_reopen(self):
        self.product()
        reopened = Marketplace(self.path)
        self.assertEqual(len(reopened.products()), 1)


if __name__ == "__main__":
    unittest.main()

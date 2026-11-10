"""Additional state-race, privacy and administrative recovery regression cases."""

import unittest

import test_market as fixtures

from market.models import DomainError


class EdgeTests(unittest.TestCase):
    """Reuse isolated fixtures; explicitly exercise administrative recovery branches."""

    setUp = fixtures.MarketTests.setUp
    product = fixtures.MarketTests.product
    trade = fixtures.MarketTests.trade
    row = fixtures.MarketTests.row

    def test_frozen_trade_does_not_expire_back_to_public(self):
        product, reservation = self.trade()
        self.market.set_user_enabled(self.admin, self.buyer_id, False, "核查")
        self.market.expire_reservations()
        self.assertEqual(self.row("products", product)["status"], "FROZEN")
        self.assertEqual(self.row("reservations", reservation)["status"], "FROZEN")

    def test_cannot_resume_dispute_with_disabled_party(self):
        _, reservation = self.trade(True)
        self.market.set_user_enabled(self.admin, self.buyer_id, False, "核查")
        case = self.market.admin_records(self.admin, "disputes")[0]
        with self.assertRaises(DomainError):
            self.market.resolve_dispute(self.admin, case["id"], True, "恢复")
        self.assertEqual(self.row("reservations", reservation)["status"], "FROZEN")

    def test_report_duplicate_and_final_state(self):
        product = self.product()
        report = self.market.report_product(self.buyer, product, "核查")
        with self.assertRaises(DomainError):
            self.market.report_product(self.buyer, product, "重复")
        self.market.handle_report(self.admin, report, False, "未发现违规")
        with self.assertRaises(DomainError):
            self.market.handle_report(self.admin, report, True, "旧页面")
        self.assertEqual(self.row("products", product)["status"], "PUBLISHED")

    def test_contact_update_is_private(self):
        self.market.update_contact(self.seller, "private-contact")
        self.product()
        self.assertNotIn("private-contact", str(self.market.products()))
        self.assertNotIn("private-contact", str(self.market.logs(self.admin)))

    def test_copy_violating_record_preserves_original(self):
        product = self.product()
        report = self.market.report_product(self.buyer, product, "违规")
        self.market.handle_report(self.admin, report, True, "下架")
        copied = self.market.copy_product(self.seller, product)
        self.assertEqual(self.row("products", product)["status"], "VIOLATION_DELISTED")
        self.assertEqual(self.row("products", copied)["status"], "DRAFT")

    def test_seller_cannot_complete_on_buyers_behalf(self):
        _, reservation = self.trade(True)
        with self.assertRaises(DomainError):
            self.market.complete_trade(self.seller, reservation)

    def test_pending_request_reject_releases_product(self):
        product, reservation = self.trade()
        self.market.respond_reservation(self.seller, reservation, False, "暂不出售")
        self.assertEqual(self.row("products", product)["status"], "PUBLISHED")
        second = self.market.reserve(self.other, product, "new-buyer")
        self.assertNotEqual(second, reservation)

    def test_admin_view_does_not_expose_password_material(self):
        users = self.market.admin_records(self.admin, "users")
        self.assertNotIn("password_hash", str(users))
        self.assertNotIn("salt", str(users))

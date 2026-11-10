"""Product versions, category management and auditable publication workflow."""

from pathlib import Path

from .core import command
from .models import DomainError, money_cents, require_state, text


class CatalogMixin:
    """Product identity remains stable; every edit creates a new immutable version."""

    @staticmethod
    def product_data(title, description, price, condition, place, image_path=""):
        """Validate the complete draft before entering any write path."""
        image_path = str(image_path).strip()
        if image_path:
            candidate = Path(image_path)
            if not candidate.is_file() or candidate.suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
            }:
                raise DomainError("图片必须是存在的 PNG/JPG/GIF 文件")
            image_path = str(candidate.resolve())
        return (
            text(title, "标题", 80),
            text(description, "描述", 2000),
            money_cents(price),
            text(condition, "成色", 40),
            text(place, "交付地点", 100),
            image_path,
        )

    def check_category(self, connection, category_id):
        """Disabled categories cannot accept new or resubmitted products."""
        category = self.one(connection, "categories", category_id)
        if not category["enabled"]:
            raise DomainError("该分类已停用，请重新选择")

    def insert_version(self, connection, product_id, version_no, values):
        """Preserve a full historical snapshot, including rejected versions."""
        connection.execute(
            "INSERT INTO product_versions(product_id,version_no,title,description,"
            "price_cents,condition,handover_place,image_path,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (product_id, version_no, *values, self.now()),
        )

    @command
    def create_product(
        self,
        connection,
        actor,
        category_id,
        title,
        description,
        price,
        condition,
        place,
        image_path="",
    ):
        """Save a private draft; public queries never reveal unapproved content."""
        self.check_category(connection, category_id)
        values = self.product_data(
            title, description, price, condition, place, image_path
        )
        cursor = connection.execute(
            "INSERT INTO products(seller_id,category_id,status,created_at) VALUES (?,?,?,?)",
            (actor["id"], category_id, "DRAFT", self.now()),
        )
        self.insert_version(connection, cursor.lastrowid, 1, values)
        self.log(
            connection,
            actor,
            "create_product",
            "PRODUCT",
            cursor.lastrowid,
            "",
            "DRAFT",
        )
        return cursor.lastrowid

    @command
    def edit_product(
        self,
        connection,
        actor,
        product_id,
        expected_version,
        category_id,
        title,
        description,
        price,
        condition,
        place,
        image_path="",
    ):
        """Published edits return to draft; stale version submissions are rejected."""
        product = self.one(connection, "products", product_id)
        self.owner(actor, product["seller_id"])
        require_state(product, {"DRAFT", "AUDIT_REJECTED", "PUBLISHED", "DELISTED"})
        if product["current_version"] != expected_version:
            raise DomainError("商品版本已更新，请刷新后编辑")
        self.check_category(connection, category_id)
        values = self.product_data(
            title, description, price, condition, place, image_path
        )
        next_version = expected_version + 1
        self.insert_version(connection, product_id, next_version, values)
        connection.execute(
            "UPDATE products SET category_id=?,current_version=?,status='DRAFT' WHERE id=?",
            (category_id, next_version, product_id),
        )
        self.log(
            connection,
            actor,
            "edit_product",
            "PRODUCT",
            product_id,
            product["status"],
            "DRAFT",
        )

    @command
    def submit_product(self, connection, actor, product_id):
        """Queue exactly the current version for administrative review."""
        product = self.one(connection, "products", product_id)
        self.owner(actor, product["seller_id"])
        require_state(product, {"DRAFT", "AUDIT_REJECTED", "DELISTED"})
        self.check_category(connection, product["category_id"])
        connection.execute(
            "UPDATE products SET status='PENDING_AUDIT' WHERE id=?", (product_id,)
        )
        self.log(
            connection,
            actor,
            "submit_product",
            "PRODUCT",
            product_id,
            product["status"],
            "PENDING_AUDIT",
        )

    @command
    def withdraw_product(self, connection, actor, product_id):
        """Let the owner withdraw before the administrator commits a decision."""
        product = self.one(connection, "products", product_id)
        self.owner(actor, product["seller_id"])
        require_state(product, {"PENDING_AUDIT"})
        connection.execute(
            "UPDATE products SET status='DRAFT' WHERE id=?", (product_id,)
        )
        self.log(
            connection,
            actor,
            "withdraw_product",
            "PRODUCT",
            product_id,
            "PENDING_AUDIT",
            "DRAFT",
        )

    @command
    def audit_product(
        self, connection, actor, product_id, expected_version, approved, reason
    ):
        """Review a specific version; prevent approving an obsolete review page."""
        self.admin(actor)
        product = self.one(connection, "products", product_id)
        require_state(product, {"PENDING_AUDIT"})
        if product["seller_id"] == actor["id"]:
            raise DomainError("不能审核本人发布的商品")
        if product["current_version"] != expected_version:
            raise DomainError("审核版本已变化，请刷新")
        reason = text(reason, "审核说明", 500)
        version = connection.execute(
            "SELECT id FROM product_versions WHERE product_id=? AND version_no=?",
            (product_id, expected_version),
        ).fetchone()
        target = "PUBLISHED" if approved else "AUDIT_REJECTED"
        connection.execute(
            "INSERT INTO audits(version_id,auditor_id,result,reason,created_at) VALUES (?,?,?,?,?)",
            (
                version["id"],
                actor["id"],
                "APPROVED" if approved else "REJECTED",
                reason,
                self.now(),
            ),
        )
        connection.execute(
            "UPDATE products SET status=? WHERE id=?", (target, product_id)
        )
        self.notify(
            connection,
            product["seller_id"],
            f"audit:{version['id']}:{self.now()}",
            f"商品 #{product_id} 审核结果：{target}。{reason}",
        )
        self.log(
            connection,
            actor,
            "audit_product",
            "PRODUCT",
            product_id,
            "PENDING_AUDIT",
            target,
            reason=reason,
        )

    @command
    def delist_product(self, connection, actor, product_id):
        """Active trades must be handled before a seller can delist."""
        product = self.one(connection, "products", product_id)
        self.owner(actor, product["seller_id"])
        require_state(product, {"PUBLISHED"})
        connection.execute(
            "UPDATE products SET status='DELISTED' WHERE id=?", (product_id,)
        )
        self.log(
            connection,
            actor,
            "delist_product",
            "PRODUCT",
            product_id,
            "PUBLISHED",
            "DELISTED",
        )

    @command
    def copy_product(self, connection, actor, product_id):
        """Rectification creates a new draft instead of rewriting violation history."""
        product = self.one(connection, "products", product_id)
        self.owner(actor, product["seller_id"])
        version = connection.execute(
            "SELECT * FROM product_versions WHERE product_id=? AND version_no=?",
            (product_id, product["current_version"]),
        ).fetchone()
        self.check_category(connection, product["category_id"])
        cursor = connection.execute(
            "INSERT INTO products(seller_id,category_id,status,created_at) VALUES (?,?,'DRAFT',?)",
            (actor["id"], product["category_id"], self.now()),
        )
        values = tuple(
            version[key]
            for key in (
                "title",
                "description",
                "price_cents",
                "condition",
                "handover_place",
                "image_path",
            )
        )
        self.insert_version(connection, cursor.lastrowid, 1, values)
        self.log(
            connection,
            actor,
            "copy_product",
            "PRODUCT",
            cursor.lastrowid,
            reason=f"复制自商品 #{product_id}",
        )
        return cursor.lastrowid

    @command
    def add_category(self, connection, actor, name):
        """Enforce globally unique category names in storage."""
        self.admin(actor)
        cursor = connection.execute(
            "INSERT INTO categories(name) VALUES (?)",
            (text(name, "分类名称", 30),),
        )
        self.log(connection, actor, "add_category", "CATEGORY", cursor.lastrowid)
        return cursor.lastrowid

    @command
    def set_category(self, connection, actor, category_id, enabled):
        """Retain referenced categories rather than deleting historical relationships."""
        self.admin(actor)
        self.one(connection, "categories", category_id)
        connection.execute(
            "UPDATE categories SET enabled=? WHERE id=?",
            (int(bool(enabled)), category_id),
        )
        self.log(
            connection,
            actor,
            "set_category",
            "CATEGORY",
            category_id,
            after=str(bool(enabled)),
        )

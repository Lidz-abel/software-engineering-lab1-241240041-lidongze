"""CLI entry point for GUI startup, bootstrap, demo data and timeout sweeps."""

import argparse
import getpass
from pathlib import Path

from .models import DomainError
from .service import Marketplace


def seed_demo(service):
    """Initialize a fresh demo only; never overwrite an existing user's records."""
    with service.database.connect() as connection:
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise DomainError("演示初始化仅适用于空数据库")
    service.bootstrap_admin("admin", "Admin123!")
    service.register("seller", "Seller123!", "seller@example.edu")
    service.register("buyer", "Buyer123!", "buyer@example.edu")
    seller = service.login("seller", "Seller123!")
    admin = service.login("admin", "Admin123!")
    for category, title, price, condition in (
        (1, "软件工程教材（附学习笔记）", "28.00", "九成新"),
        (2, "有线机械键盘", "99.00", "八成新"),
        (3, "宿舍护眼台灯", "35.50", "九成新"),
        (4, "羽毛球拍两支", "65.00", "八成新"),
    ):
        product = service.create_product(
            seller.token,
            category,
            title,
            "校园自用闲置，支持当面检查，图书馆门口交付。",
            price,
            condition,
            "图书馆南门",
        )
        service.submit_product(seller.token, product)
        service.audit_product(admin.token, product, 1, True, "信息完整，允许发布")
    service.logout(seller.token)
    service.logout(admin.token)
    print("演示数据已创建：admin/Admin123!、seller/Seller123!、buyer/Buyer123!")


def main():
    """Run the desktop by default, with explicit local provisioning subcommands."""
    parser = argparse.ArgumentParser(description="拾光集市 · 校园二手交易系统")
    parser.add_argument("--db", default=str(Path.home() / ".shiguang" / "market.db"))
    parser.add_argument(
        "--demo", action="store_true", help="仅向空数据库初始化演示数据"
    )
    parser.add_argument("--init-admin", metavar="USERNAME", help="创建首个管理员")
    parser.add_argument("--sweep", action="store_true", help="执行超时扫描后退出")
    arguments = parser.parse_args()
    service = Marketplace(arguments.db)
    try:
        if arguments.demo:
            seed_demo(service)
        elif arguments.init_admin:
            password = getpass.getpass("管理员密码：")
            service.bootstrap_admin(arguments.init_admin, password)
            print("管理员已创建")
        elif arguments.sweep:
            print(f"已处理 {service.expire_reservations()} 条超时预约")
        else:
            from .gui import MarketWindow  # pylint: disable=import-outside-toplevel

            MarketWindow(service).run()
    except DomainError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

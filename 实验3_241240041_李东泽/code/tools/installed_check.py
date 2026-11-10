"""Validate the installed wheel without relying on the editable source package."""

import contextlib
import io
from pathlib import Path
import tempfile

import market
from market.__main__ import seed_demo
from market.service import Marketplace


def main():
    """Check package provenance, initialize demo data and run an installed transaction."""
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        module = Path(market.__file__).resolve()
        assert "site-packages" in str(module), module
        print(f"INSTALLED_MODULE: {module}")
        with tempfile.TemporaryDirectory() as directory:
            service = Marketplace(Path(directory) / "installed.db")
            seed_demo(service)
            assert len(service.products()) == 4
            buyer = service.login("buyer", "Buyer123!")
            seller = service.login("seller", "Seller123!")
            item = service.products()[0]["id"]
            reservation = service.reserve(buyer.token, item, "installed-check")
            service.respond_reservation(seller.token, reservation, True)
            service.complete_trade(buyer.token, reservation)
            assert service.statistics(seller.token)["completed"]["count"] == 1
            print(
                "INSTALLED_WHEEL_OK: seed, login, query, reserve, confirm, complete, statistics"
            )
    output = stream.getvalue()
    destination = (
        Path(__file__).resolve().parents[2] / "验证材料" / "installed-wheel.txt"
    )
    destination.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

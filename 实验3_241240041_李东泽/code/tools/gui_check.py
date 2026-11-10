"""Exercise real Tk widgets and capture only this application's native window."""

from pathlib import Path
import tempfile

from PIL import ImageGrab

from market.__main__ import seed_demo
from market.gui import MarketWindow
from market.service import Marketplace


def main():
    """Use an isolated demo database to verify tabs and capture reproducible screens."""
    evidence = Path(__file__).resolve().parents[2] / "验证材料"
    evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        service = Marketplace(Path(directory) / "gui.db")
        seed_demo(service)
        window = MarketWindow(service)
        window.root.update()
        window.root.geometry("1280x820+40+40")

        def capture(name):
            window.root.update()
            left = window.root.winfo_rootx()
            top = window.root.winfo_rooty()
            box = (
                left,
                top,
                left + window.root.winfo_width(),
                top + window.root.winfo_height(),
            )
            ImageGrab.grab(bbox=box).save(evidence / name)

        window.root.after(700, lambda: capture("01-游客商品广场.png"))

        def admin_screen():
            window.session = service.login("admin", "Admin123!")
            window.refresh()
            window.notebook.select(window.tables["用户管理"].master.master)
            capture("02-管理员用户管理.png")

        def seller_screen():
            window.session = service.login("seller", "Seller123!")
            window.refresh()
            window.notebook.select(window.tables["我的商品"].master.master)
            capture("03-卖家商品管理.png")

        window.root.after(1400, admin_screen)
        window.root.after(2100, seller_screen)
        window.root.after(2800, window.close)
        window.run()
    print("GUI_SMOKE_OK: guest/admin/seller widgets rendered and captured")


if __name__ == "__main__":
    main()

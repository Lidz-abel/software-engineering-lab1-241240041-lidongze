"""Handwritten Tkinter desktop UI for users, sellers and administrators."""

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from uuid import uuid4

from .models import DomainError, label, money


class MarketWindow:
    """A single window with service-side authorization behind every button."""

    def __init__(self, service):
        self.service = service
        self.session = None
        self.root = tk.Tk()
        self.root.title("拾光集市 · 校园二手交易")
        self.root.geometry("1280x820")
        self.root.minsize(1060, 680)
        self.root.configure(background="#f2f4fa")
        self.tables = {}
        self.records = {}
        self.filters = {}
        self.status = tk.StringVar(
            value="欢迎来到拾光集市。游客可浏览，登录后可发布与预约。"
        )
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f2f4fa")
        style.configure("TLabel", background="#f2f4fa", font=("Microsoft YaHei", 10))
        style.configure("TButton", font=("Microsoft YaHei", 10), padding=7)
        style.configure("Treeview", font=("Microsoft YaHei", 10), rowheight=34)
        style.configure("Treeview.Heading", font=("Microsoft YaHei", 10, "bold"))
        style.configure("TNotebook.Tab", padding=(16, 10))
        self.build_header()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=12)
        self.build_pages()
        ttk.Label(self.root, textvariable=self.status, wraplength=1200).pack(
            fill="x", padx=24, pady=(0, 12)
        )
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(1000, self.tick)
        self.refresh()

    def build_header(self):
        """Brand, session context and authentication entry points."""
        header = tk.Frame(self.root, background="#5559d9", height=90)
        header.pack(fill="x")
        tk.Label(
            header,
            text="拾光集市",
            background="#5559d9",
            foreground="white",
            font=("Microsoft YaHei", 26, "bold"),
        ).pack(side="left", padx=24, pady=18)
        tk.Label(
            header,
            text="让闲置，在校园里重新发光",
            background="#5559d9",
            foreground="white",
            font=("Microsoft YaHei", 12),
        ).pack(side="left")
        for title, callback in (
            ("登录", self.login),
            ("注册", self.register),
            ("退出登录", self.logout),
            ("账号设置", self.account),
        ):
            ttk.Button(
                header, text=title, command=lambda fn=callback: self.safe(fn)
            ).pack(side="right", padx=5)

    def page(self, name, columns):
        """Reusable sortable table, toolbar and scrollbars; no generated UI code."""
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=name)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 12))
        holder = ttk.Frame(frame)
        holder.pack(fill="both", expand=True)
        table = ttk.Treeview(
            holder, columns=[key for key, _ in columns], show="headings"
        )
        for key, title in columns:
            table.heading(
                key, text=title, command=lambda k=key, t=table: self.sort(t, k)
            )
            table.column(key, width=130, minwidth=70, stretch=True)
        vertical = ttk.Scrollbar(holder, orient="vertical", command=table.yview)
        horizontal = ttk.Scrollbar(holder, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.tables[name] = table
        self.records[name] = []
        return toolbar

    def button(self, parent, title, callback):
        """Route UI callbacks through the common error boundary."""
        ttk.Button(parent, text=title, command=lambda: self.safe(callback)).pack(
            side="left", padx=3, pady=2
        )

    def build_pages(self):
        """Expose all modules, while keeping administrative tabs session-dependent."""
        columns = [
            ("id", "编号"),
            ("title", "商品名称"),
            ("category", "分类"),
            ("price_cents", "价格 / 元"),
            ("status", "状态"),
            ("seller", "卖家"),
        ]
        toolbar = self.page("商品广场", columns)
        category_choices = {"全部分类": None}
        category_choices.update(
            {row["name"]: row["id"] for row in self.service.categories()}
        )
        self.category_choices = category_choices
        self.category_filter = ttk.Combobox(
            toolbar, values=list(category_choices), state="readonly", width=10
        )
        self.category_filter.set("全部分类")
        self.category_filter.pack(side="left", padx=3)
        for key, title, width in (
            ("keyword", "关键词", 14),
            ("minimum", "最低价", 8),
            ("maximum", "最高价", 8),
            ("status", "状态代码", 14),
        ):
            ttk.Label(toolbar, text=title).pack(side="left", padx=3)
            entry = ttk.Entry(toolbar, width=width)
            entry.pack(side="left")
            self.filters[key] = entry
        self.button(toolbar, "查询", self.refresh)
        self.button(toolbar, "详情", lambda: self.details("商品广场"))
        self.button(toolbar, "预约", self.reserve)
        self.button(toolbar, "举报", self.report)
        toolbar = self.page("我的商品", columns)
        actions = (
            ("发布草稿", self.editor),
            ("编辑", self.edit_selected),
            ("提交审核", lambda: self.product_action("submit_product")),
            ("撤回", lambda: self.product_action("withdraw_product")),
            ("下架", lambda: self.product_action("delist_product")),
            ("复制为草稿", lambda: self.product_action("copy_product")),
            ("历史版本", self.history),
            ("刷新", self.refresh),
        )
        for title, callback in actions:
            self.button(toolbar, title, callback)
        toolbar = self.page(
            "预约与交易",
            [
                ("id", "预约号"),
                ("product_id", "商品号"),
                ("title_snapshot", "商品快照"),
                ("price_snapshot", "约定价格"),
                ("buyer", "买家"),
                ("seller", "卖家"),
                ("status", "状态"),
                ("expires_at", "响应截止 / UTC"),
            ],
        )
        for title, callback in (
            ("交易详情", self.trade_details),
            ("卖家确认", lambda: self.respond(True)),
            ("卖家拒绝", lambda: self.respond(False)),
            ("买家取消", self.cancel),
            ("同意取消", lambda: self.handle_cancel(True)),
            ("拒绝取消", lambda: self.handle_cancel(False)),
            ("买家确认完成", self.complete),
            ("提出争议", self.dispute),
        ):
            self.button(toolbar, title, callback)
        toolbar = self.page(
            "通知",
            [
                ("id", "编号"),
                ("content", "通知内容"),
                ("is_read", "已读"),
                ("created_at", "时间 / UTC"),
            ],
        )
        self.button(toolbar, "标为已读", self.mark_read)
        self.button(toolbar, "刷新", self.refresh)
        toolbar = self.page(
            "操作日志",
            [
                ("id", "编号"),
                ("action", "操作"),
                ("object_type", "对象"),
                ("object_id", "对象号"),
                ("result", "结果"),
                ("reason", "说明"),
                ("occurred_at", "时间 / UTC"),
            ],
        )
        self.log_action = ttk.Entry(toolbar, width=24)
        self.log_action.pack(side="left", padx=5)
        self.log_action.insert(0, "")
        self.button(toolbar, "按操作名查询", self.refresh)
        self.button(toolbar, "导出 CSV", lambda: self.export("操作日志"))
        self.button(toolbar, "统计汇总", self.statistics)
        toolbar = self.page("商品审核", columns + [("current_version", "版本")])
        self.button(toolbar, "查看内容", lambda: self.details("商品审核"))
        self.button(toolbar, "审核通过", lambda: self.audit(True))
        self.button(toolbar, "审核驳回", lambda: self.audit(False))
        self.button(toolbar, "刷新", self.refresh)
        toolbar = self.page(
            "举报治理",
            [
                ("id", "编号"),
                ("product_id", "商品号"),
                ("reason", "原因"),
                ("status", "状态"),
                ("handling_result", "处理结果"),
            ],
        )
        self.button(toolbar, "举报成立", lambda: self.handle_report(True))
        self.button(toolbar, "举报不成立", lambda: self.handle_report(False))
        toolbar = self.page(
            "争议处理",
            [
                ("id", "编号"),
                ("reservation_id", "预约号"),
                ("description", "说明"),
                ("status", "状态"),
                ("conclusion", "处理结论"),
            ],
        )
        self.button(toolbar, "恢复交易", lambda: self.resolve(True))
        self.button(toolbar, "取消并违规下架", lambda: self.resolve(False))
        toolbar = self.page(
            "用户管理",
            [
                ("id", "编号"),
                ("username", "用户名"),
                ("role", "角色"),
                ("status", "状态"),
            ],
        )
        self.button(toolbar, "禁用用户", lambda: self.set_user(False))
        self.button(toolbar, "恢复用户", lambda: self.set_user(True))
        toolbar = self.page(
            "分类管理", [("id", "编号"), ("name", "名称"), ("enabled", "启用")]
        )
        self.button(toolbar, "添加分类", self.add_category)
        self.button(toolbar, "切换启用状态", self.toggle_category)

    @staticmethod
    def sort(table, column):
        """Stable local sorting does not change server-side result visibility."""
        rows = [(table.set(item, column), item) for item in table.get_children()]
        for index, (_, item) in enumerate(sorted(rows)):
            table.move(item, "", index)

    def safe(self, callback):
        """Surface expected business failures and keep the application responsive."""
        try:
            return callback()
        except (DomainError, OSError, ValueError) as error:
            self.status.set(str(error))
            messagebox.showerror("操作未完成", str(error), parent=self.root)
            return None

    def token(self):
        """Require a session for any private UI action."""
        if self.session is None:
            raise DomainError("请先登录")
        return self.session.token

    def selected(self, page):
        """Resolve the selected row against the last authorized query result."""
        selection = self.tables[page].selection()
        if not selection:
            raise DomainError("请先选择一条记录")
        record_id = int(selection[0])
        return next(row for row in self.records[page] if row["id"] == record_id)

    def fill(self, page, records):
        """Display only requested columns, formatting money and status explicitly."""
        table = self.tables[page]
        table.delete(*table.get_children())
        self.records[page] = records
        for record in records:
            values = []
            for column in table["columns"]:
                value = record.get(column, "")
                if column in {"price_cents", "price_snapshot"}:
                    value = money(value)
                elif column == "status":
                    value = label(value)
                values.append(value)
            table.insert("", "end", iid=str(record["id"]), values=values)

    def refresh(self):
        """Refresh all visible data and remove records from a previous session."""
        token = self.session.token if self.session else None
        self.service.expire_reservations()
        self.fill(
            "商品广场",
            self.service.products(
                token,
                category_id=self.category_choices.get(self.category_filter.get()),
                **{key: entry.get().strip() for key, entry in self.filters.items()},
            ),
        )
        for name in self.tables:
            if name != "商品广场":
                self.fill(name, [])
        if self.session:
            self.fill("我的商品", self.service.products(token, scope="mine"))
            self.fill("预约与交易", self.service.reservations(token))
            self.fill("通知", self.service.notifications(token))
            self.fill(
                "操作日志",
                self.service.logs(token, action=self.log_action.get().strip()),
            )
        is_admin = self.session is not None and self.session.role == "ADMIN"
        for name in ("商品审核", "举报治理", "争议处理", "用户管理", "分类管理"):
            self.notebook.tab(
                self.tables[name].master.master,
                state="normal" if is_admin else "hidden",
            )
        if is_admin:
            self.fill(
                "商品审核",
                self.service.products(token, scope="admin", status="PENDING_AUDIT"),
            )
            for page, kind in (
                ("举报治理", "reports"),
                ("争议处理", "disputes"),
                ("用户管理", "users"),
                ("分类管理", "categories"),
            ):
                self.fill(page, self.service.admin_records(token, kind))
        identity = self.session.username if self.session else "游客"
        self.status.set(f"当前：{identity}  |  数据已刷新  |  线下交易，无在线支付")

    def login(self):
        """Authenticate through a masked password prompt."""
        username = simpledialog.askstring("登录", "用户名", parent=self.root)
        if username is None:
            return
        password = simpledialog.askstring("登录", "密码", show="*", parent=self.root)
        if password is None:
            return
        self.session = self.service.login(username, password)
        self.refresh()

    def register(self):
        """Public registration creates a normal buyer/seller account."""
        username = simpledialog.askstring("注册", "用户名（3～24位字母、数字或下划线）")
        if username is None:
            return
        password = simpledialog.askstring("注册", "密码（至少8位）", show="*")
        if password is None:
            return
        contact = simpledialog.askstring("注册", "联系方式（确认交易后向对方展示）")
        if contact is None:
            return
        self.service.register(username, password, contact)
        messagebox.showinfo("注册完成", "请使用新账号登录")

    def logout(self):
        """Clear both the service token and cached private rows."""
        if self.session:
            self.service.logout(self.session.token)
        self.session = None
        self.refresh()

    def account(self):
        """Maintain private contact details or rotate the current password."""
        token = self.token()
        choice = messagebox.askyesno("账号设置", "是：修改联系方式；否：修改密码")
        if choice:
            contact = simpledialog.askstring("联系方式", "新的联系方式")
            if contact is not None:
                self.service.update_contact(token, contact)
        else:
            old = simpledialog.askstring("修改密码", "原密码", show="*")
            new = simpledialog.askstring("修改密码", "新密码", show="*")
            if old is not None and new is not None:
                self.service.change_password(token, old, new)
                self.session = None
        self.refresh()

    def editor(self, product=None):
        """Handwritten draft editor with explicit category and local image selection."""
        token = self.token()
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑商品" if product else "发布商品草稿")
        dialog.geometry("640x560")
        dialog.transient(self.root)
        entries = {}
        fields = (
            ("title", "标题"),
            ("description", "描述"),
            ("price", "价格 / 元"),
            ("condition", "成色"),
            ("place", "线下交付地点"),
            ("image_path", "本地图片路径"),
        )
        for index, (key, title) in enumerate(fields):
            ttk.Label(dialog, text=title).grid(
                row=index, column=0, sticky="w", padx=18, pady=12
            )
            entry = ttk.Entry(dialog, width=55)
            entry.grid(row=index, column=1, padx=12, pady=12)
            if product:
                source = {
                    "price": money(product["price_cents"]),
                    "place": product["handover_place"],
                }.get(key, product.get(key, ""))
                entry.insert(0, source)
            entries[key] = entry
        categories = self.service.categories()
        choices = {f"{row['id']} · {row['name']}": row["id"] for row in categories}
        ttk.Label(dialog, text="分类").grid(row=6, column=0, padx=18)
        category = ttk.Combobox(
            dialog, values=list(choices), state="readonly", width=52
        )
        category.grid(row=6, column=1, padx=12, pady=12)
        if categories:
            category.current(0)
        if product:
            for choice, category_id in choices.items():
                if category_id == product["category_id"]:
                    category.set(choice)

        def browse():
            path = filedialog.askopenfilename(
                filetypes=[("商品图片", "*.png *.jpg *.jpeg *.gif")]
            )
            if path:
                entries["image_path"].delete(0, "end")
                entries["image_path"].insert(0, path)

        def save():
            if category.get() not in choices:
                raise DomainError("请选择一个可用分类")
            values = {key: entry.get() for key, entry in entries.items()}
            if product:
                self.service.edit_product(
                    token,
                    product["id"],
                    product["current_version"],
                    choices[category.get()],
                    **values,
                )
            else:
                self.service.create_product(token, choices[category.get()], **values)
            dialog.destroy()
            self.refresh()

        ttk.Button(dialog, text="选择图片", command=browse).grid(
            row=7, column=0, pady=15
        )
        ttk.Button(dialog, text="保存草稿", command=lambda: self.safe(save)).grid(
            row=7, column=1
        )

    def edit_selected(self):
        """Preserve the selected version number for stale-page detection."""
        self.editor(self.selected("我的商品"))

    def product_action(self, action):
        """Dispatch only fixed, locally constructed product commands."""
        product = self.selected("我的商品")
        getattr(self.service, action)(self.token(), product["id"])
        self.refresh()

    def show_record(self, title, record):
        """Scrollable read-only details, including long descriptions and histories."""
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("760x560")
        content = tk.Text(
            window, wrap="word", font=("Microsoft YaHei", 11), padx=15, pady=15
        )
        content.pack(fill="both", expand=True)
        content.insert("1.0", json.dumps(record, ensure_ascii=False, indent=2))
        content.configure(state="disabled")

    def details(self, page):
        """Show public product data, never a seller's unconfirmed contact."""
        self.show_record("商品详情", self.selected(page))

    def history(self):
        """Inspect immutable versions and version-specific audit decisions."""
        product = self.selected("我的商品")
        self.show_record(
            "版本与审核历史", self.service.product_history(self.token(), product["id"])
        )

    def reserve(self):
        """Create an intent after a visible confirmation, with a unique request ID."""
        product = self.selected("商品广场")
        if messagebox.askyesno(
            "确认预约",
            f"预约 {product['title']}，价格 {money(product['price_cents'])} 元？",
        ):
            self.service.reserve(self.token(), product["id"], uuid4().hex)
            self.refresh()

    def report(self):
        """Collect a reason before creating a moderation case."""
        product = self.selected("商品广场")
        reason = simpledialog.askstring("举报商品", "请说明举报原因")
        if reason is not None:
            self.service.report_product(self.token(), product["id"], reason)
            self.refresh()

    def respond(self, approved):
        """Seller decision; a stale table cannot override the service state guard."""
        row = self.selected("预约与交易")
        reason = simpledialog.askstring("处理预约", "处理说明")
        if reason is not None:
            self.service.respond_reservation(self.token(), row["id"], approved, reason)
            self.refresh()

    def cancel(self):
        """The service decides direct cancellation versus negotiated cancellation."""
        row = self.selected("预约与交易")
        reason = simpledialog.askstring("取消预约", "取消原因")
        if reason is not None:
            self.service.cancel_reservation(self.token(), row["id"], reason)
            self.refresh()

    def handle_cancel(self, approved):
        """Resolve a buyer cancellation request as the seller."""
        row = self.selected("预约与交易")
        note = simpledialog.askstring("处理取消申请", "处理说明")
        if note is not None:
            self.service.handle_cancellation(self.token(), row["id"], approved, note)
            self.refresh()

    def complete(self):
        """Require explicit buyer acknowledgement of successful offline delivery."""
        row = self.selected("预约与交易")
        if messagebox.askyesno("完成交易", "确认已线下交付？完成后不能撤销。"):
            self.service.complete_trade(self.token(), row["id"])
            self.refresh()

    def trade_details(self):
        """Display authorized contact and cancellation history."""
        row = self.selected("预约与交易")
        self.show_record(
            "交易详情", self.service.trade_details(self.token(), row["id"])
        )

    def dispute(self):
        """Freeze a trade and request platform intervention."""
        row = self.selected("预约与交易")
        reason = simpledialog.askstring("提出争议", "请描述争议与所需处理")
        if reason is not None:
            self.service.open_dispute(self.token(), row["id"], reason)
            self.refresh()

    def resolve(self, resume):
        """Administrative dispute resolution keeps a written conclusion."""
        row = self.selected("争议处理")
        conclusion = simpledialog.askstring("解决争议", "处理结论")
        if conclusion is not None:
            self.service.resolve_dispute(self.token(), row["id"], resume, conclusion)
            self.refresh()

    def audit(self, approved):
        """Attach each review to the selected immutable version."""
        row = self.selected("商品审核")
        reason = simpledialog.askstring("商品审核", "审核说明")
        if reason is not None:
            self.service.audit_product(
                self.token(), row["id"], row["current_version"], approved, reason
            )
            self.refresh()

    def handle_report(self, valid):
        """Record a moderation conclusion and refresh affected products and trades."""
        row = self.selected("举报治理")
        conclusion = simpledialog.askstring("举报处理", "处理结论")
        if conclusion is not None:
            self.service.handle_report(self.token(), row["id"], valid, conclusion)
            self.refresh()

    def set_user(self, enabled):
        """Administrative account restrictions always require a reason."""
        row = self.selected("用户管理")
        reason = simpledialog.askstring("账号状态", "处理原因")
        if reason is not None:
            self.service.set_user_enabled(self.token(), row["id"], enabled, reason)
            self.refresh()

    def add_category(self):
        """Add a unique category without deleting existing relationships."""
        name = simpledialog.askstring("新增分类", "分类名称")
        if name is not None:
            self.service.add_category(self.token(), name)
            self.refresh()

    def toggle_category(self):
        """Disable future classification while retaining historical references."""
        row = self.selected("分类管理")
        self.service.set_category(self.token(), row["id"], not row["enabled"])
        self.refresh()

    def mark_read(self):
        """Mark only the selected personal message as read."""
        row = self.selected("通知")
        self.service.mark_read(self.token(), row["id"])
        self.refresh()

    def export(self, page):
        """Export the current authorized result set to a user-selected path."""
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if path:
            self.service.export_csv(Path(path), self.records[page])
            self.status.set(f"已导出至 {path}")

    def statistics(self):
        """Show platform totals for admins and own seller totals for normal users."""
        self.show_record("统计汇总", self.service.statistics(self.token()))

    def tick(self):
        """Sweep timeouts every 30 seconds while the desktop application is running."""
        try:
            count = self.service.expire_reservations()
            if count:
                self.status.set(f"已自动处理 {count} 条超时预约，请刷新查看")
        finally:
            self.root.after(30000, self.tick)

    def close(self):
        """Revoke the active desktop session before closing."""
        if self.session:
            self.service.logout(self.session.token)
        self.root.destroy()

    def run(self):
        """Enter the native desktop event loop."""
        self.root.mainloop()

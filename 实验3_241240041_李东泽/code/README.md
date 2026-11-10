# 拾光集市：校园二手交易系统

实验三，李东泽，241240041。Python 3.11+，Tkinter 桌面界面，SQLite 持久化。
运行时仅依赖 Python 标准库；Windows 官方 Python 安装程序包含 Tkinter。

## 安装与运行

在本目录打开终端：

```powershell
python -m pip install .
python -m market --db demo.db --demo
python -m market --db demo.db
```

已有数据库不能再次执行 `--demo`。演示账号如下，正式使用请修改密码：

|角色|用户名|密码|
|---|---|---|
|管理员|admin|Admin123!|
|卖家（普通用户）|seller|Seller123!|
|买家（普通用户）|buyer|Buyer123!|

离线安装：`python -m pip install dist/shiguang_market-1.0.0-py3-none-any.whl`。
直接源码启动：在本目录执行 `$env:PYTHONPATH='src'; python -m market --db demo.db`。
默认数据库位于用户目录 `.shiguang/market.db`，`--db` 可以切换独立数据集。

## 正式账号与演示流程

`python -m market --db campus.db --init-admin administrator` 通过隐藏密码输入创建首个管理员。
其他用户从界面注册。同一普通用户既能发布商品，也能预约别人的商品。
公开注册接口不能创建管理员；初始管理员只能创建一次。

1. 卖家登录，在“我的商品”保存草稿并提交审核。
2. 管理员登录，在“商品审核”查看内容、通过或驳回，填写审核说明。
3. 买家登录，在“商品广场”组合查询，查看详情并预约已发布商品。
4. 卖家在“预约与交易”确认预约，双方可在详情查看对方联系方式。
5. 买家申请取消后，由卖家同意或拒绝。待处理时不能确认完成。
6. 线下交付后，由买家确认完成，完成状态不可逆。
7. 演示举报成立冻结交易、管理员处理争议、账号禁用联动冻结。
8. “操作日志”按操作名查询和 CSV 导出，普通用户仅能查看自己的日志。

## 构建与验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python tools/verify.py
python tools/gui_check.py
```

`verify.py` 顺序执行源码统计、字节码编译、unittest、Pylint、PEP 517 构建。
任一步失败返回非零退出码；实际日志位于上级 `验证材料`，发行包位于 `dist`。
`gui_check.py` 在临时数据库渲染游客、管理员、卖家页面并截图，需要 Windows 和 Pillow 11.2+。
此 GUI 冒烟验证不等于人工逐项交互测试。
`python -m market --db campus.db --sweep` 独立执行24小时超时扫描。

## 模块和约束

`models.py` 对应 UML 枚举及输入规约；`database.py` 对应关系、外键和唯一约束；
`core.py` 对应认证、授权及事务；`catalog.py` 对应商品、版本和审核；
`trading.py` 对应预约、取消、争议；`governance.py` 对应举报与用户治理；
`queries.py` 提供权限受控查询与汇总；`gui.py` 提供手写桌面 UI；`service.py` 是统一门面。

写命令使用 `BEGIN IMMEDIATE`，商品和预约同步提交，失败同步回滚。
部分唯一索引保证同一商品最多一条有效预约。请求编号按买家唯一，同键更换商品会被拒绝。
金额保存为整数分，预约保存标题与价格快照。版本只增不改，日志禁止更新与删除。
密码采用随机盐 PBKDF2-HMAC-SHA256；登录会话八小时到期。

## 与实验二的差异及边界

Vue/Spring Boot 调整为 Python/Tkinter，MySQL/Redis/对象存储调整为 SQLite 和本地图片路径，
以适应单机桌面演示及后续测试。保留主要领域状态。角色字段区分管理员和普通用户，
普通用户在不同交易中承担买家或卖家身份。详情窗口采用字段名便于实验检查。

不含支付、物流、聊天、短信或校园认证。本地图片路径不是可移植图片存储。
应用运行时每30秒扫描超时，离线时不执行，重开或 `--sweep` 后补处理。
SQLite 文件拥有者可以直接修改文件；本原型没有服务器级隔离。
查询及 CSV 导出最多1000行，当前没有分页。公开注册字段校验失败不记入业务日志。

## 官方参考

- https://docs.python.org/3/library/sqlite3.html
- https://docs.python.org/3/library/tkinter.html
- https://build.pypa.io/en/latest/how-to/basic-usage.html
- https://pylint.readthedocs.io/

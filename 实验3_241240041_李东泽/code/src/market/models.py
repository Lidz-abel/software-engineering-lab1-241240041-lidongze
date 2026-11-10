"""Domain states, immutable session view and shared input validation."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class DomainError(ValueError):
    """A safe, user-facing business rule failure."""


class ProductStatus(StrEnum):
    """Lifecycle of a product, including administrative states."""

    DRAFT = "DRAFT"
    PENDING_AUDIT = "PENDING_AUDIT"
    AUDIT_REJECTED = "AUDIT_REJECTED"
    PUBLISHED = "PUBLISHED"
    RESERVED = "RESERVED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    DELISTED = "DELISTED"
    VIOLATION_DELISTED = "VIOLATION_DELISTED"
    FROZEN = "FROZEN"


class ReservationStatus(StrEnum):
    """States participating in the partial unique reservation index."""

    PENDING_SELLER = "PENDING_SELLER"
    CONFIRMED = "CONFIRMED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"
    FROZEN = "FROZEN"


@dataclass(frozen=True)
class Session:
    """Only authentication issues tokens; services revalidate every operation."""

    token: str
    user_id: int
    username: str
    role: str


LABELS = {
    "DRAFT": "草稿",
    "PENDING_AUDIT": "待审核",
    "AUDIT_REJECTED": "审核驳回",
    "PUBLISHED": "已发布",
    "RESERVED": "已预约",
    "CONFIRMED": "已确认",
    "COMPLETED": "已完成",
    "DELISTED": "已下架",
    "VIOLATION_DELISTED": "违规下架",
    "FROZEN": "冻结",
    "PENDING_SELLER": "待卖家确认",
    "CANCEL_PENDING": "取消申请中",
    "CANCELLED": "已取消",
    "REJECTED": "已拒绝",
    "EXPIRED": "已超时",
    "PENDING": "待处理",
    "APPROVED": "已通过",
    "VALID": "举报成立",
    "INVALID": "举报不成立",
    "ACTIVE": "正常",
    "DISABLED": "已禁用",
    "OPEN": "处理中",
    "RESOLVED": "已解决",
}


def label(value):
    """Translate a status for display without altering its stored identifier."""
    return LABELS.get(str(value), str(value))


def text(value, name, maximum=200, minimum=1):
    """Reject empty/oversized/control-character text at the service boundary."""
    cleaned = str(value).strip()
    if not minimum <= len(cleaned) <= maximum:
        raise DomainError(f"{name}长度必须为 {minimum}～{maximum} 个字符")
    if any(ord(char) < 32 and char not in "\n\t" for char in cleaned):
        raise DomainError(f"{name}含有非法控制字符")
    return cleaned


def username_value(value):
    """Keep usernames stable and unambiguous for case-insensitive uniqueness."""
    cleaned = text(value, "用户名", 24, 3)
    if not re.fullmatch(r"[A-Za-z0-9_]+", cleaned):
        raise DomainError("用户名仅支持英文字母、数字及下划线")
    return cleaned


def password_value(value):
    """Validate passwords without stripping intentional spaces."""
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise DomainError("密码长度必须为 8～128 个字符")
    return value


def money_cents(value):
    """Convert exact decimal currency into integral cents, rejecting NaN/infinity."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise DomainError("价格必须是合法数字") from error
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999.99"):
        raise DomainError("价格必须在 0.01～999999.99 元之间")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise DomainError("价格最多保留两位小数")
    return int(cents)


def money(value):
    """Format an integer amount without floating-point roundoff."""
    return f"{value // 100}.{value % 100:02d}"


def require_state(record, allowed):
    """Reject stale UI actions before any related data is modified."""
    if record["status"] not in allowed:
        raise DomainError(f"状态冲突：当前为{label(record['status'])}，请刷新后重试")

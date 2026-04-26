"""ORM models for Mi Host bot.

All datetimes are stored in UTC; presentation is converted to MSK at render time.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ProductKind(str, PyEnum):
    CARDINAL = "cardinal"
    SCRIPT = "script"


class InstanceStatus(str, PyEnum):
    PENDING = "pending"  # awaiting deploy
    DEPLOYING = "deploying"
    LIVE = "live"
    SUSPENDED = "suspended"
    FAILED = "failed"
    DELETED = "deleted"


class PaymentStatus(str, PyEnum):
    CREATED = "created"
    PAID = "paid"
    EXPIRED = "expired"
    REFUNDED = "refunded"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # tg user id
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    language_code: Mapped[Optional[str]] = mapped_column(String(8))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    referrer_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"))
    bonus_days: Mapped[int] = mapped_column(Integer, default=0)  # banked bonus
    coins: Mapped[int] = mapped_column(Integer, default=0)  # internal economy
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    last_minigame_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )

    referrals: Mapped[list["User"]] = relationship(
        "User", backref="referrer", remote_side="User.id"
    )

    __table_args__ = (Index("ix_users_referrer", "referrer_id"),)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    product: Mapped[ProductKind] = mapped_column(Enum(ProductKind, name="product_kind"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "product", name="uq_user_product"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    product: Mapped[ProductKind] = mapped_column(Enum(ProductKind, name="product_kind"))
    invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_rub: Mapped[int] = mapped_column(Integer)
    amount_crypto: Mapped[Optional[str]] = mapped_column(String(32))
    asset: Mapped[Optional[str]] = mapped_column(String(16))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.CREATED
    )
    pay_url: Mapped[Optional[str]] = mapped_column(Text)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    product: Mapped[ProductKind] = mapped_column(Enum(ProductKind, name="product_kind"))
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[InstanceStatus] = mapped_column(
        Enum(InstanceStatus, name="instance_status"),
        default=InstanceStatus.PENDING,
    )
    render_service_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    render_url: Mapped[Optional[str]] = mapped_column(Text)
    last_deploy_id: Mapped[Optional[str]] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # golden_key, etc.
    code_hash: Mapped[Optional[str]] = mapped_column(String(64))
    risk_score: Mapped[int] = mapped_column(Integer, default=0)  # 0..100
    risk_report: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )


class ContentPost(Base):
    __tablename__ = "content_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # post|review|case|update|trigger
    title: Mapped[Optional[str]] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    image_path: Mapped[Optional[str]] = mapped_column(String(255))
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    tg_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class ReferralEvent(Base):
    __tablename__ = "referral_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    referee_id: Mapped[int] = mapped_column(BigInteger, index=True, unique=True)
    rewarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )


class Setting(Base):
    """Mutable runtime settings (e.g. dynamic admin list, channel id)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )

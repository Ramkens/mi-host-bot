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


class ShardStatus(str, PyEnum):
    ACTIVE = "active"
    PAUSED = "paused"  # don't schedule new tenants here; existing keep running
    DRAINING = "draining"  # actively migrate tenants out
    DEAD = "dead"  # not responding


class Shard(Base):
    """A Render account (worker host) that runs tenant subprocesses.

 The master service writes desired_state on Instance rows, and the worker
 on this shard reconciles. Workers report `last_seen_at` so master can
 detect dead shards.
 """

    __tablename__ = "shards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # Fernet-encrypted Render API key (cyphertext is base64 urlsafe).
    api_key_enc: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[Optional[str]] = mapped_column(String(64))
    service_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    service_url: Mapped[Optional[str]] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(32), default="frankfurt")
    capacity: Mapped[int] = mapped_column(Integer, default=4)  # max tenants per shard
    status: Mapped[ShardStatus] = mapped_column(
        Enum(ShardStatus, name="shard_status"), default=ShardStatus.ACTIVE
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
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
    # Which shard runs the subprocess for this tenant. NULL = run on master.
    shard_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("shards.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[InstanceStatus] = mapped_column(
        Enum(InstanceStatus, name="instance_status"),
        default=InstanceStatus.PENDING,
    )
    # The desired vs actual lifecycle state — set by master, observed by worker.
    desired_state: Mapped[str] = mapped_column(String(16), default="live")  # live|stopped
    actual_state: Mapped[str] = mapped_column(String(16), default="stopped")
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


class Coupon(Base):
    """Multi-use code that grants a free subscription period.

 Created by an admin via /create_coupon or the admin panel, redeemed by
 a user during the buy flow as an alternative to paying. A single coupon
 can be activated up to ``max_uses`` times; the duration granted per
 activation is stored in ``duration_hours`` (fractional-day precision).
 ``tier`` lets Script coupons distinguish STD vs PRO.
 """

    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    product: Mapped[ProductKind] = mapped_column(Enum(ProductKind, name="product_kind"))
    tier: Mapped[str] = mapped_column(String(16), default="std")
    days: Mapped[int] = mapped_column(Integer, default=30)  # legacy
    duration_hours: Mapped[int] = mapped_column(Integer, default=30 * 24)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses_count: Mapped[int] = mapped_column(Integer, default=0)
    issued_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    # Kept for backward compatibility / reporting: last redeemer info.
    used_by: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    note: Mapped[Optional[str]] = mapped_column(Text)
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

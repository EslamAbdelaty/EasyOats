"""SQLAlchemy schema. Orders retain the prices/costs agreed at creation."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    phone_original: Mapped[str] = mapped_column(String(64))
    phone_normalized: Mapped[str] = mapped_column(String(11), index=True)
    order_datetime: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(64), default="واتساب")
    area: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    location_url: Mapped[str] = mapped_column(Text, default="")
    order_type: Mapped[str] = mapped_column(String(64), default="سعر عادي")
    honey_qty: Mapped[int] = mapped_column(Integer, default=0)
    date_qty: Mapped[int] = mapped_column(Integer, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    delivery_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    payment_method: Mapped[str] = mapped_column(String(64), default="كاش عند الاستلام")
    amount_collected: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(64), default="جديد", index=True)
    courier: Mapped[str] = mapped_column(String(200), default="")
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)
    actual_delivery_date: Mapped[date | None] = mapped_column(Date)
    tracking_number: Mapped[str] = mapped_column(String(200), default="")
    delivery_result: Mapped[str] = mapped_column(String(200), default="")
    feedback_consent: Mapped[str] = mapped_column(String(64), default="اسأله لاحقًا")
    feedback_request_date: Mapped[date | None] = mapped_column(Date)
    feedback_status: Mapped[str] = mapped_column(String(64), default="لم يُطلب")
    customer_rating: Mapped[int | None] = mapped_column(Integer)
    buy_again: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    returned_sellable: Mapped[bool] = mapped_column(Boolean, default=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    honey_unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    date_unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Inventory(Base):
    __tablename__ = "inventory"

    sku: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    opening_stock: Mapped[int] = mapped_column(Integer)
    added_stock: Mapped[int] = mapped_column(Integer, default=0)
    physical_count: Mapped[int | None] = mapped_column(Integer)
    reorder_point: Mapped[int] = mapped_column(Integer, default=50)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class OrderItem(Base):
    """A product quantity and frozen unit cost attached to an order."""

    __tablename__ = "order_items"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id", ondelete="CASCADE"), primary_key=True,
    )
    sku: Mapped[str] = mapped_column(
        ForeignKey("inventory.sku"), primary_key=True, index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), index=True)
    response_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64), default="واتساب")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    overall_rating: Mapped[int | None] = mapped_column(Integer)
    taste_rating: Mapped[int | None] = mapped_column(Integer)
    portion_rating: Mapped[int | None] = mapped_column(Integer)
    preparation_rating: Mapped[int | None] = mapped_column(Integer)
    value_rating: Mapped[int | None] = mapped_column(Integer)
    buy_again: Mapped[str] = mapped_column(String(32), default="")
    recommendation_score: Mapped[int | None] = mapped_column(Integer)
    preferred_flavor: Mapped[str] = mapped_column(String(100), default="")
    comments: Mapped[str] = mapped_column(Text, default="")
    issue_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_owner: Mapped[str] = mapped_column(String(200), default="")
    followup_status: Mapped[str] = mapped_column(String(64), default="لا تحتاج")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AppSetting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class SyncState(Base):
    __tablename__ = "sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    synced_revision: Mapped[int] = mapped_column(Integer, default=-1)
    pending: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime)
    message: Mapped[str] = mapped_column(Text, default="لم تتم مزامنة Excel بعد.")


class AuditHistory(Base):
    __tablename__ = "audit_history"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    order_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(100))
    field: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    user_name: Mapped[str] = mapped_column(String(200))


class AppUser(Base):
    """An authenticated OIDC identity authorized to use the hosted app."""

    __tablename__ = "app_users"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="staff", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

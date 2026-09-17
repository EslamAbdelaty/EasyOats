"""Transactional application API with a recoverable Excel reporting boundary."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hmac
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from constants import (
    ACTIVE_STATUSES, BUY_AGAIN_OPTIONS, DEFAULT_SETTINGS, FEEDBACK_CONSENTS,
    FEEDBACK_SOURCES, FEEDBACK_STATUSES, FOLLOWUP_STATUSES, ORDER_STATUSES,
    ORDER_TYPES, PAYMENT_METHODS, PRODUCTS, SOURCES,
)
from database import Database, PROJECT_ROOT
from models import AppSetting, AppUser, AuditHistory, Feedback, Inventory, Order, SyncState, utc_now
from services.inventory_service import inventory_rows
from utils.phone import PhoneValidationError, normalize_partial_phone, normalize_phone

logger = logging.getLogger(__name__)
CENT = Decimal("0.01")
USER_ROLES = ("staff", "admin")


class ValidationError(ValueError):
    """A business validation failure with text safe to display to the user."""


def local_now() -> datetime:
    return datetime.now(ZoneInfo("Africa/Cairo")).replace(tzinfo=None)


def money(value, label="المبلغ") -> Decimal:
    if value is None or value == "":
        value = 0
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal("9999999999.99"):
            raise InvalidOperation
        return result.quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{label}: أدخل مبلغاً صحيحاً أكبر من أو يساوي صفر.") from None


def integer(value, label, minimum=0, maximum=1_000_000, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value() or not minimum <= number <= maximum:
            raise ValueError
        return int(number)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{label}: أدخل عدداً صحيحاً من {minimum} إلى {maximum}.") from None


def date_value(value, label, required=False) -> date | None:
    if value is None or value == "":
        if required:
            raise ValidationError(f"يرجى إدخال {label}.")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValidationError(f"{label} غير صحيح. استخدم تاريخاً بصيغة YYYY-MM-DD.") from None


def datetime_value(value) -> datetime:
    if value in (None, ""):
        return local_now()
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if result.tzinfo:
            result = result.astimezone(ZoneInfo("Africa/Cairo")).replace(tzinfo=None)
        return result
    except (ValueError, TypeError):
        raise ValidationError("تاريخ ووقت الطلب غير صحيحين.") from None


def choice(value, choices, label):
    if value not in choices:
        raise ValidationError(f"يرجى اختيار قيمة صحيحة لحقل {label}.")
    return value


def boolean(value, label):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if value in ("نعم", "موافق", "true", "True"):
        return True
    if value in (None, "", "لا", "غير موافق", "false", "False"):
        return False
    raise ValidationError(f"يرجى تحديد {label} بنعم أو لا.")


def string_value(value, label, max_length=5000, required=False):
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise ValidationError(f"يرجى إدخال {label}.")
    if len(text) > max_length:
        raise ValidationError(f"{label} أطول من المسموح ({max_length} حرف).")
    return text


def email_value(value) -> str:
    email = string_value(value, "البريد الإلكتروني", 320, required=True).casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValidationError("أدخل عنوان بريد إلكتروني صحيحاً.")
    return email


def url_value(value, label):
    value = string_value(value, label, 2000)
    if value:
        url = urlparse(value)
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValidationError(f"{label} يجب أن يبدأ بـ https:// أو http://.")
    return value


def payment_status(total_due, amount_collected, refund_amount=0) -> str:
    total, collected, refund = (Decimal(str(x)) for x in (total_due, amount_collected, refund_amount))
    if refund > 0 and refund >= collected:
        return "مسترد"
    if collected >= total:
        return "مدفوع"
    if collected > 0:
        return "مدفوع جزئيًا"
    return "غير مدفوع"


def calculate_order(data: dict, settings: dict | None = None) -> dict:
    """Pure Decimal arithmetic; explicit snapshot prices always take precedence."""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    honey = int(data.get("honey_qty", 0))
    dates = int(data.get("date_qty", 0))
    kind = data.get("order_type", "سعر عادي")
    price = data.get("unit_price")
    if price is None:
        price = (Decimal(str(settings["offer_price"])) / 2 if kind == "عرض 2 بـ120"
                 else Decimal("0") if kind == "عينة" else Decimal(str(settings["retail_price"])))
    price = Decimal(str(price)).quantize(CENT, rounding=ROUND_HALF_UP)
    honey_cost = Decimal(str(data.get("honey_unit_cost", settings["honey_unit_cost"])))
    date_cost = Decimal(str(data.get("date_unit_cost", settings["date_unit_cost"])))
    subtotal = price * (honey + dates)
    due = subtotal - Decimal(str(data.get("discount", 0))) + Decimal(str(data.get("delivery_fee", 0)))
    cost = honey_cost * honey + date_cost * dates
    collected = Decimal(str(data.get("amount_collected", 0)))
    refund = Decimal(str(data.get("refund_amount", 0)))
    result = {
        "total_units": honey + dates,
        "unit_price": price,
        "product_subtotal": subtotal,
        "total_due": due,
        "product_cost": cost,
        "contribution_margin": due - cost - Decimal(str(data.get("delivery_cost", 0))),
        "outstanding_balance": due - collected,
        "payment_status": payment_status(due, collected, refund),
    }
    return {key: float(value.quantize(CENT, rounding=ROUND_HALF_UP)) if isinstance(value, Decimal) else value
            for key, value in result.items()}


def next_action(data: dict) -> str:
    actions = []
    status = data["status"]
    if status in ("ملغي", "مرتجع"):
        if data["amount_collected"] > data.get("refund_amount", 0):
            actions.append("مراجعة المبلغ المطلوب رده للعميل")
        if status == "مرتجع" and not data.get("returned_sellable"):
            actions.append("فحص المرتجع؛ لم يُعد إلى المخزون القابل للبيع")
        return " · ".join(actions) or "لا يوجد إجراء مطلوب"
    if status == "تم التوصيل":
        if data["outstanding_balance"] > 0:
            actions.append("تحصيل المبلغ المتبقي")
        if data["feedback_consent"] == "موافق" and data["feedback_status"] == "لم يُطلب":
            actions.append("إرسال رابط الفيدباك")
        elif data["feedback_status"] in ("تم إرسال الرابط", "يحتاج متابعة"):
            actions.append("متابعة رد الفيدباك")
        return " · ".join(actions) or "اكتمل الطلب"
    expected = data.get("expected_delivery_date")
    if expected and expected < local_now().date():
        actions.append("متابعة تأخر التوصيل")
    actions.append({"جديد": "تأكيد الطلب", "مؤكد": "بدء التجهيز", "جاري التجهيز": "إنهاء التجهيز",
                    "جاهز": "تسليم الطلب للمندوب", "خرج للتوصيل": "تأكيد التسليم والتحصيل"}[status])
    return " · ".join(actions)


class AppService:
    def __init__(self, database_url=None, workbook_path=None, backup_dir=None, auto_sync=True):
        self.db = Database(database_url)
        self.engine = self.db.engine
        self.Session = self.db.Session
        self.workbook_path = Path(workbook_path or os.getenv("EXCEL_PATH") or PROJECT_ROOT / "EasyOats_Order_Tracker.xlsx").resolve()
        self.backup_dir = Path(backup_dir or os.getenv("BACKUP_DIR") or PROJECT_ROOT / "backups").resolve()
        self.auto_sync = auto_sync
        self._excel = None
        with self._transaction() as session:
            for key, value in DEFAULT_SETTINGS.items():
                if session.get(AppSetting, key) is None:
                    session.add(AppSetting(key=key, value=value))
            for sku, opening in (("honey", 238), ("date", 250)):
                if session.get(Inventory, sku) is None:
                    session.add(Inventory(sku=sku, name=PRODUCTS[sku], opening_stock=opening,
                                          added_stock=0, reorder_point=50))
            if session.get(SyncState, 1) is None:
                session.add(SyncState(id=1, revision=0, synced_revision=-1, pending=True,
                                      message="لم تتم مزامنة Excel بعد."))
            # These variables provide a recoverable first administrator and may
            # also keep a small deployment allowlist authoritative.
            bootstrap = {
                "admin": os.getenv("BOOTSTRAP_ADMIN_EMAILS", ""),
                "staff": os.getenv("BOOTSTRAP_STAFF_EMAILS", ""),
            }
            for role, raw_emails in bootstrap.items():
                for raw_email in raw_emails.split(","):
                    if not raw_email.strip():
                        continue
                    email = email_value(raw_email)
                    account = session.get(AppUser, email)
                    if account is None:
                        session.add(AppUser(email=email, display_name=email.split("@", 1)[0],
                                            role=role, active=True))
                    elif role == "admin":
                        account.role = "admin"
                        account.active = True

    @property
    def excel_service(self):
        if self._excel is None:
            from services.excel_service import ExcelService
            self._excel = ExcelService(self.workbook_path, self.backup_dir)
        return self._excel

    @excel_service.setter
    def excel_service(self, value):
        self._excel = value

    @staticmethod
    def authenticate_admin(pin) -> bool:
        expected = os.getenv("ADMIN_PIN", "")
        return bool(expected and hmac.compare_digest(str(pin), expected))

    @staticmethod
    def _user_dict(user: AppUser) -> dict:
        return {
            "email": user.email, "display_name": user.display_name,
            "role": user.role, "active": user.active,
            "last_login": user.last_login, "created_at": user.created_at,
            "updated_at": user.updated_at,
        }

    def authenticate_user(self, email, display_name=None) -> dict | None:
        """Resolve a verified OIDC identity against the database allowlist."""
        normalized = email_value(email)
        with self._transaction() as session:
            user = session.get(AppUser, normalized)
            if user is None or not user.active:
                return None
            supplied_name = string_value(display_name or "", "اسم المستخدم", 200)
            if supplied_name:
                user.display_name = supplied_name
            user.last_login = utc_now()
            user.updated_at = utc_now()
            session.flush()
            return self._user_dict(user)

    def _require_admin(self, session, actor_email) -> AppUser:
        actor = session.get(AppUser, email_value(actor_email))
        if actor is None or not actor.active or actor.role != "admin":
            raise ValidationError("هذه العملية متاحة لمسؤول النظام فقط.")
        return actor

    def list_users(self, actor_email) -> list[dict]:
        with self.Session() as session:
            self._require_admin(session, actor_email)
            query = select(AppUser).order_by(AppUser.active.desc(), AppUser.role.desc(), AppUser.email)
            return [self._user_dict(user) for user in session.scalars(query)]

    def save_user(self, data, actor_email) -> dict:
        """Add or edit an authorized account while retaining one active admin."""
        with self._transaction() as session:
            actor = self._require_admin(session, actor_email)
            email = email_value(data.get("email"))
            role = data.get("role", "staff")
            if role not in USER_ROLES:
                raise ValidationError("صلاحية المستخدم يجب أن تكون موظفاً أو مسؤولاً.")
            active = boolean(data.get("active", True), "تفعيل المستخدم")
            name = string_value(data.get("display_name") or email.split("@", 1)[0],
                                "اسم المستخدم", 200, required=True)
            user = session.get(AppUser, email)
            if user is None:
                user = AppUser(email=email, display_name=name, role=role, active=active)
                session.add(user)
                old = None
            else:
                old = self._user_dict(user)
                if user.email == actor.email and (not active or role != "admin"):
                    raise ValidationError("لا يمكنك إلغاء حسابك الإداري أو خفض صلاحيته أثناء استخدامه.")
                user.display_name, user.role, user.active = name, role, active
                user.updated_at = utc_now()
            session.flush()
            active_admins = session.scalar(select(func.count()).select_from(AppUser).where(
                AppUser.active.is_(True), AppUser.role == "admin"))
            if not active_admins:
                raise ValidationError("يجب أن يبقى مسؤول نشط واحد على الأقل.")
            new = self._user_dict(user)
            for field in ("display_name", "role", "active"):
                previous = None if old is None else old[field]
                if previous != new[field]:
                    self._audit(session, None, "user_access_update", f"{email}.{field}",
                                previous, new[field], f"{actor.display_name} ({actor.email})")
            return new

    @contextmanager
    def _transaction(self):
        try:
            with self.db.transaction() as session:
                yield session
        except SQLAlchemyError:
            logger.exception("Database write failed")
            raise ValidationError("تعذر حفظ التغيير الآن. يرجى المحاولة مرة أخرى.") from None

    @staticmethod
    def _settings(session) -> dict:
        return {item.key: item.value for item in session.scalars(select(AppSetting))}

    @staticmethod
    def _raw(model) -> dict:
        return {column.name: (float(value) if isinstance(value := getattr(model, column.name), Decimal) else value)
                for column in model.__table__.columns}

    def _order_dict(self, order, feedback=None):
        result = self._raw(order)
        result.update(calculate_order(result))
        result["next_action"] = next_action(result)
        if feedback is not None:
            result["feedback"] = feedback
        return result

    @staticmethod
    def _audit_value(value):
        if value is None:
            return None
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    def _audit(self, session, order_id, action, field, old, new, user):
        session.add(AuditHistory(order_id=order_id, action=action, field=field,
                                 old_value=self._audit_value(old), new_value=self._audit_value(new), user_name=user))

    def _actor(self, session, user):
        return string_value(user or self._settings(session)["current_user"], "اسم المستخدم", 200, required=True)

    @staticmethod
    def _mark_pending(session):
        state = session.get(SyncState, 1)
        state.revision += 1
        state.pending = True
        state.message = "توجد تغييرات محفوظة في قاعدة البيانات بانتظار مزامنة Excel."

    def _after_commit(self):
        if self.auto_sync:
            # Excel must never turn a committed order into a reported save failure.
            try:
                self.sync_excel()
            except Exception:
                logger.exception("Post-commit Excel sync failed; database change is safe")

    def _validate_order(self, payload, settings, existing=None):
        base = self._raw(existing) if existing else {
            "customer_name": "", "phone_original": "", "order_datetime": local_now(),
            "source": "واتساب", "area": "", "address": "", "location_url": "",
            "order_type": "سعر عادي", "honey_qty": 0, "date_qty": 0,
            "discount": 0, "delivery_fee": 0, "delivery_cost": 0,
            "payment_method": "كاش عند الاستلام", "amount_collected": 0, "status": "جديد",
            "courier": "", "expected_delivery_date": None, "actual_delivery_date": None,
            "tracking_number": "", "delivery_result": "", "feedback_consent": "اسأله لاحقًا",
            "feedback_request_date": None, "feedback_status": "لم يُطلب", "customer_rating": None,
            "buy_again": "", "notes": "", "returned_sellable": False, "refund_amount": 0,
        }
        editable = {
            "customer_name", "phone_original", "order_datetime", "source", "area", "address", "location_url",
            "order_type", "honey_qty", "date_qty", "discount", "delivery_fee", "delivery_cost", "payment_method",
            "amount_collected", "status", "courier", "expected_delivery_date", "actual_delivery_date",
            "tracking_number", "delivery_result", "feedback_consent", "feedback_request_date", "feedback_status",
            "customer_rating", "buy_again", "notes", "returned_sellable", "refund_amount",
        }
        payload = dict(payload)
        if "phone" in payload and "phone_original" not in payload:
            payload["phone_original"] = payload["phone"]
        if "order_id" in payload and payload["order_id"] != (existing.order_id if existing else None):
            raise ValidationError("رقم الطلب يُنشأ تلقائياً ولا يمكن تغييره.")
        for key in editable:
            if key in payload:
                base[key] = payload[key]
        result = {key: base[key] for key in editable}
        for key, label, limit, required in (
            ("customer_name", "اسم العميل", 200, True), ("phone_original", "رقم الموبايل", 64, True),
            ("area", "المنطقة", 200, False), ("address", "العنوان", 2000, False),
            ("courier", "المندوب أو المسؤول", 200, False), ("tracking_number", "رقم التتبع", 200, False),
            ("delivery_result", "نتيجة التوصيل", 200, False), ("notes", "الملاحظات", 10000, False),
        ):
            result[key] = string_value(result[key], label, limit, required)
        try:
            result["phone_normalized"] = normalize_phone(result["phone_original"])
        except PhoneValidationError as exc:
            raise ValidationError(str(exc)) from None
        result["location_url"] = url_value(result["location_url"], "رابط الموقع")
        for key, options, label in (
            ("source", SOURCES, "مصدر الطلب"), ("order_type", ORDER_TYPES, "نوع الطلب"),
            ("status", ORDER_STATUSES, "حالة الطلب"), ("payment_method", PAYMENT_METHODS, "طريقة الدفع"),
            ("feedback_consent", FEEDBACK_CONSENTS, "الموافقة على الفيدباك"),
            ("feedback_status", FEEDBACK_STATUSES, "حالة الفيدباك"), ("buy_again", BUY_AGAIN_OPTIONS, "الشراء مجدداً"),
        ):
            result[key] = choice(result[key], options, label)
        for key, label in (("honey_qty", "كمية عسل ولبن"), ("date_qty", "كمية دبس تمر ولبن")):
            result[key] = integer(result[key], label)
        if result["honey_qty"] + result["date_qty"] == 0 and result["order_type"] not in ("طلب شركة", "أخرى"):
            raise ValidationError("أضف كوباً واحداً على الأقل، أو اختر طلب شركة / أخرى للطلبات الخاصة.")
        for key, label in (("discount", "الخصم"), ("delivery_fee", "رسوم التوصيل"), ("delivery_cost", "تكلفة التوصيل"),
                           ("amount_collected", "المبلغ المحصل"), ("refund_amount", "المبلغ المسترد")):
            result[key] = money(result[key], label)
        if result["refund_amount"] > result["amount_collected"]:
            raise ValidationError("المبلغ المسترد لا يمكن أن يتجاوز المبلغ المحصل.")
        result["order_datetime"] = datetime_value(result["order_datetime"])
        for key, label in (("expected_delivery_date", "تاريخ التوصيل المتوقع"),
                           ("actual_delivery_date", "تاريخ التوصيل الفعلي"),
                           ("feedback_request_date", "تاريخ طلب الفيدباك")):
            result[key] = date_value(result[key], label)
            if result[key] and result[key] < result["order_datetime"].date():
                raise ValidationError(f"{label} لا يمكن أن يسبق تاريخ الطلب.")
        if result["status"] == "تم التوصيل" and result["actual_delivery_date"] is None:
            raise ValidationError("يرجى إدخال تاريخ التوصيل الفعلي عند اختيار تم التوصيل.")
        if result["actual_delivery_date"] and result["actual_delivery_date"] > local_now().date():
            raise ValidationError("تاريخ التوصيل الفعلي لا يمكن أن يكون في المستقبل.")
        result["customer_rating"] = integer(result["customer_rating"], "تقييم العميل", 1, 5, optional=True)
        result["returned_sellable"] = boolean(result["returned_sellable"], "صلاحية المرتجع للبيع")
        if result["returned_sellable"] and result["status"] != "مرتجع":
            # When a returned order is reopened its previous confirmation no longer applies.
            result["returned_sellable"] = False
        if existing:
            result["unit_price"] = existing.unit_price
            result["honey_unit_cost"] = existing.honey_unit_cost
            result["date_unit_cost"] = existing.date_unit_cost
        else:
            result["honey_unit_cost"] = money(settings["honey_unit_cost"])
            result["date_unit_cost"] = money(settings["date_unit_cost"])
        if not existing or existing.order_type != result["order_type"]:
            result["unit_price"] = money(
                Decimal(str(settings["offer_price"])) / 2 if result["order_type"] == "عرض 2 بـ120"
                else 0 if result["order_type"] == "عينة" else settings["retail_price"])
        if calculate_order(result)["total_due"] < 0:
            raise ValidationError("الخصم لا يمكن أن يتجاوز قيمة المنتجات ورسوم التوصيل.")
        return result

    def _check_stock(self, session, before, order_id, user, is_admin, override_reason):
        after = inventory_rows(session, self._settings(session))
        previous = {row["sku"]: row["available"] for row in before}
        shortages = [row for row in after if row["available"] < 0 and row["available"] < previous.get(row["sku"], 0)]
        if shortages:
            if not is_admin or not str(override_reason or "").strip():
                names = "، ".join(f"{row['name']} (عجز {abs(row['available'])} كوب)" for row in shortages)
                raise ValidationError(f"المخزون غير كافٍ: {names}. يلزم تجاوز مسؤول مع تسجيل السبب.")
            self._audit(session, order_id, "inventory_override", "override_reason", None,
                        string_value(override_reason, "سبب التجاوز", 2000, True), user)

    def create_order(self, data, user=None, override_reason=None, is_admin=False) -> dict:
        with self._transaction() as session:
            settings = self._settings(session)
            actor = self._actor(session, user)
            before = inventory_rows(session, settings)
            values = self._validate_order(data, settings)
            order = Order(**values)
            session.add(order)
            session.flush()
            order.order_id = f"EO-{order.id:06d}"
            session.flush()
            self._check_stock(session, before, order.order_id, actor, is_admin, override_reason)
            for field, value in values.items():
                self._audit(session, order.order_id, "create", field, None, value, actor)
            self._audit(session, order.order_id, "create", "order_id", None, order.order_id, actor)
            self._mark_pending(session)
            result = self._order_dict(order, feedback=[])
        self._after_commit()
        return result

    def update_order(self, order_id, data, user=None, override_reason=None, is_admin=False) -> dict:
        with self._transaction() as session:
            order = session.scalar(select(Order).where(Order.order_id == order_id))
            if order is None:
                raise ValidationError("الطلب غير موجود.")
            actor = self._actor(session, user)
            settings = self._settings(session)
            before = inventory_rows(session, settings)
            values = self._validate_order(data, settings, existing=order)
            if order.status == "تم التوصيل" and values["status"] not in ("تم التوصيل", "مرتجع"):
                if not is_admin or not str(override_reason or "").strip():
                    raise ValidationError("لإلغاء طلب تم تسليمه اختر مرتجع. تصحيح حالة التسليم يحتاج تجاوز مسؤول مع السبب.")
                self._audit(session, order_id, "status_override", "override_reason", None, override_reason, actor)
            if order.status == "مرتجع" and values["status"] != "مرتجع":
                if not is_admin or not str(override_reason or "").strip():
                    raise ValidationError("إعادة فتح طلب مرتجع تحتاج تجاوز مسؤول مع تسجيل سبب التصحيح.")
                self._audit(session, order_id, "status_override", "override_reason", None, override_reason, actor)
            if order.status in ("تم التوصيل", "مرتجع") and any(
                    getattr(order, key) != values[key] for key in ("honey_qty", "date_qty", "order_type")):
                if not is_admin or not str(override_reason or "").strip():
                    raise ValidationError("تعديل منتجات طلب تم تسليمه أو إرجاعه يحتاج تجاوز مسؤول مع تسجيل السبب.")
                self._audit(session, order_id, "inventory_override", "override_reason", None, override_reason, actor)
            for field, value in values.items():
                previous = getattr(order, field)
                if previous != value:
                    self._audit(session, order_id, "update", field, previous, value, actor)
                    setattr(order, field, value)
            order.updated_at = utc_now()
            session.flush()
            self._check_stock(session, before, order_id, actor, is_admin, override_reason)
            self._mark_pending(session)
            feedback = [self._raw(item) for item in session.scalars(
                select(Feedback).where(Feedback.order_id == order_id).order_by(Feedback.response_date.desc(), Feedback.id.desc()))]
            result = self._order_dict(order, feedback)
        self._after_commit()
        return result

    def _orders(self, session, query):
        feedback_by_order = {}
        for item in session.scalars(select(Feedback).order_by(Feedback.response_date.desc(), Feedback.id.desc())):
            feedback_by_order.setdefault(item.order_id, []).append(self._raw(item))
        return [self._order_dict(order, feedback_by_order.get(order.order_id, []))
                for order in session.scalars(query.order_by(Order.order_datetime.desc(), Order.id.desc()))]

    def list_orders(self) -> list[dict]:
        with self.Session() as session:
            return self._orders(session, select(Order))

    def get_order(self, order_id) -> dict:
        with self.Session() as session:
            result = self._orders(session, select(Order).where(Order.order_id == order_id))
            if not result:
                raise ValidationError("الطلب غير موجود.")
            return result[0]

    def search_orders(self, phone, partial=False) -> list[dict]:
        try:
            normalized = normalize_partial_phone(phone) if partial else normalize_phone(phone)
        except PhoneValidationError as exc:
            raise ValidationError(str(exc)) from None
        with self.Session() as session:
            query = select(Order).where(Order.phone_normalized.endswith(normalized) if partial
                                       else Order.phone_normalized == normalized)
            return self._orders(session, query)

    def get_settings(self) -> dict:
        with self.Session() as session:
            return self._settings(session)

    def update_settings(self, data, user=None) -> None:
        with self._transaction() as session:
            actor = self._actor(session, user)
            values = dict(data)
            for key in values:
                if key not in DEFAULT_SETTINGS:
                    raise ValidationError("الإعداد المطلوب غير معروف.")
            for key in ("retail_price", "offer_price", "honey_unit_cost", "date_unit_cost"):
                if key in values:
                    values[key] = float(money(values[key], "السعر أو التكلفة"))
            if "low_stock_threshold" in values:
                values["low_stock_threshold"] = integer(values["low_stock_threshold"], "حد تنبيه المخزون")
            if "google_form_url" in values:
                values["google_form_url"] = url_value(values["google_form_url"], "رابط Google Form")
            if "user_names" in values:
                if not isinstance(values["user_names"], (tuple, list)):
                    raise ValidationError("أدخل أسماء المستخدمين كقائمة.")
                values["user_names"] = list(dict.fromkeys(string_value(name, "اسم المستخدم", 200, True)
                                                         for name in values["user_names"]))
                if not values["user_names"]:
                    raise ValidationError("أضف اسم مستخدم واحداً على الأقل.")
            if "current_user" in values:
                values["current_user"] = string_value(values["current_user"], "اسم المستخدم الحالي", 200, True)
            combined = {**self._settings(session), **values}
            if combined["current_user"] not in combined["user_names"]:
                raise ValidationError("يجب أن يكون المستخدم الحالي ضمن قائمة أسماء المستخدمين.")
            for key, value in values.items():
                setting = session.get(AppSetting, key)
                if setting.value != value:
                    self._audit(session, None, "settings_update", key, setting.value, value, actor)
                    setting.value = value
            if "low_stock_threshold" in values:
                for item in session.scalars(select(Inventory)):
                    item.reorder_point = values["low_stock_threshold"]
            self._mark_pending(session)
        self._after_commit()

    def inventory(self) -> list[dict]:
        with self.Session() as session:
            return inventory_rows(session, self._settings(session))

    def update_inventory(self, sku, data, user=None, override_reason=None, is_admin=False) -> None:
        with self._transaction() as session:
            item = session.get(Inventory, sku)
            if item is None:
                raise ValidationError("المنتج غير موجود.")
            actor = self._actor(session, user)
            before = inventory_rows(session, self._settings(session))
            for key, label in (("opening_stock", "رصيد البداية"), ("added_stock", "المخزون المضاف"),
                               ("physical_count", "الجرد الفعلي"), ("reorder_point", "نقطة إعادة الطلب")):
                if key in data:
                    value = integer(data[key], label, optional=key == "physical_count")
                    if getattr(item, key) != value:
                        self._audit(session, None, "inventory_update", f"{sku}.{key}", getattr(item, key), value, actor)
                        setattr(item, key, value)
            if "unit_cost" in data:
                value = float(money(data["unit_cost"], "تكلفة الوحدة"))
                setting = session.get(AppSetting, f"{sku}_unit_cost")
                self._audit(session, None, "inventory_update", f"{sku}.unit_cost", setting.value, value, actor)
                setting.value = value
            session.flush()
            self._check_stock(session, before, None, actor, is_admin, override_reason)
            self._mark_pending(session)
        self._after_commit()

    def save_feedback(self, data, user=None) -> dict:
        with self._transaction() as session:
            actor = self._actor(session, user)
            feedback_id = integer(data["id"], "رقم الفيدباك", 1) if data.get("id") else None
            item = session.get(Feedback, feedback_id) if feedback_id else None
            if feedback_id and item is None:
                raise ValidationError("الفيدباك المطلوب غير موجود.")
            values = self._raw(item) if item else {
                "order_id": "", "response_date": local_now().date(), "source": "واتساب",
                "consent_confirmed": False, "overall_rating": None, "taste_rating": None,
                "portion_rating": None, "preparation_rating": None, "value_rating": None,
                "buy_again": "", "recommendation_score": None, "preferred_flavor": "",
                "comments": "", "issue_reported": False, "followup_owner": "",
                "followup_status": "لا تحتاج", "resolution_notes": "",
            }
            editable = set(values) - {"id", "created_at", "updated_at"}
            values = {key: data.get(key, values[key]) for key in editable}
            values["order_id"] = string_value(values["order_id"], "رقم الطلب", 32, True)
            if item and item.order_id != values["order_id"]:
                raise ValidationError("لا يمكن نقل الفيدباك إلى طلب آخر.")
            order = session.scalar(select(Order).where(Order.order_id == values["order_id"]))
            if order is None:
                raise ValidationError("رقم الطلب غير موجود. اختر طلباً مسجلاً لإضافة الفيدباك.")
            values["response_date"] = date_value(values["response_date"], "تاريخ الرد", required=True)
            if not order.order_datetime.date() <= values["response_date"] <= local_now().date():
                raise ValidationError("تاريخ رد الفيدباك يجب أن يكون بين تاريخ الطلب واليوم.")
            values["source"] = choice(values["source"], FEEDBACK_SOURCES, "مصدر الفيدباك")
            values["consent_confirmed"] = boolean(values["consent_confirmed"], "تأكيد الموافقة")
            if not values["consent_confirmed"]:
                raise ValidationError("يرجى تأكيد موافقة العميل قبل تسجيل الفيدباك.")
            values["issue_reported"] = boolean(values["issue_reported"], "وجود مشكلة")
            for key in ("overall_rating", "taste_rating", "portion_rating", "preparation_rating", "value_rating"):
                values[key] = integer(values[key], "التقييم", 1, 5, optional=True)
            values["recommendation_score"] = integer(values["recommendation_score"], "احتمالية الترشيح", 0, 10, optional=True)
            values["buy_again"] = choice(values["buy_again"], BUY_AGAIN_OPTIONS, "الشراء مجدداً")
            values["followup_status"] = choice(values["followup_status"], FOLLOWUP_STATUSES, "حالة المتابعة")
            for key, label, length in (("preferred_flavor", "النكهة المفضلة", 100), ("comments", "تعليق العميل", 10000),
                                       ("followup_owner", "مسؤول المتابعة", 200),
                                       ("resolution_notes", "ملاحظات الحل", 10000)):
                values[key] = string_value(values[key], label, length)
            action = "feedback_update" if item else "feedback_create"
            if item is None:
                item = Feedback()
                session.add(item)
            for key, value in values.items():
                previous = getattr(item, key, None)
                if previous != value:
                    self._audit(session, order.order_id, action, f"feedback.{key}", previous, value, actor)
                    setattr(item, key, value)
            item.updated_at = utc_now()
            session.flush()
            # The latest response is the order summary; editing an older response
            # does not replace a newer customer's answer in the order history.
            latest = session.scalar(select(Feedback).where(Feedback.order_id == order.order_id)
                                    .order_by(Feedback.response_date.desc(), Feedback.id.desc()).limit(1))
            for key, value in {"feedback_status": "تم الاستلام", "customer_rating": latest.overall_rating,
                               "buy_again": latest.buy_again}.items():
                if getattr(order, key) != value:
                    self._audit(session, order.order_id, action, key, getattr(order, key), value, actor)
                    setattr(order, key, value)
            self._mark_pending(session)
            result = self._raw(item)
        self._after_commit()
        return result

    def list_feedback(self, order_id=None) -> list[dict]:
        with self.Session() as session:
            query = select(Feedback)
            if order_id:
                query = query.where(Feedback.order_id == order_id)
            return [self._raw(item) for item in session.scalars(query.order_by(Feedback.response_date.desc(), Feedback.id.desc()))]

    def audit_history(self, order_id=None) -> list[dict]:
        with self.Session() as session:
            query = select(AuditHistory)
            if order_id:
                query = query.where(AuditHistory.order_id == order_id)
            rows = [self._raw(item) for item in session.scalars(query.order_by(AuditHistory.timestamp.desc(), AuditHistory.id.desc()))]
            for row in rows:
                row["changed_field"] = row["field"]
            return rows

    def snapshot(self) -> dict:
        # A single read transaction gives the exporter a coherent set of rows.
        with self.Session() as session:
            if self.db.is_sqlite:
                session.connection().exec_driver_sql("BEGIN")
            else:
                session.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            settings = self._settings(session)
            return {
                "orders": self._orders(session, select(Order)),
                "feedback": [self._raw(row) for row in session.scalars(select(Feedback).order_by(Feedback.id))],
                "inventory": inventory_rows(session, settings), "settings": settings,
                "_revision": session.get(SyncState, 1).revision,
            }

    def sync_excel(self) -> bool:
        captured = {"revision": None}
        def fresh_snapshot():
            snapshot = self.snapshot()
            captured["revision"] = snapshot["_revision"]
            return snapshot
        try:
            result = self.excel_service.sync(fresh_snapshot)
            success = bool(result.success)
            message = str(result.message)
            timestamp = result.timestamp
        except Exception:
            logger.exception("Excel synchronization failed")
            success = False
            message = "تم حفظ بياناتك بأمان. تعذرت مزامنة Excel؛ أغلق الملف إذا كان مفتوحةاً ثم أعد المحاولة."
            timestamp = None
        with self._transaction() as session:
            state = session.get(SyncState, 1)
            if success and captured["revision"] is not None:
                state.synced_revision = max(state.synced_revision, captured["revision"])
                if timestamp is None:
                    timestamp = utc_now()
                elif isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp)
                if timestamp.tzinfo:
                    timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
                if state.last_success is None or timestamp > state.last_success:
                    state.last_success = timestamp
                state.pending = state.synced_revision < state.revision
                state.message = ("تمت مزامنة Excel، وتوجد تغييرات أحدث بانتظار المزامنة."
                                 if state.pending else "تمت مزامنة Excel بنجاح.")
            else:
                state.pending = True
                state.message = message
        return success

    def sync_status(self) -> dict:
        with self.Session() as session:
            state = session.get(SyncState, 1)
            return {"pending": state.pending, "last_success": state.last_success,
                    "message": state.message, "revision": state.revision,
                    "synced_revision": state.synced_revision}

    def dashboard(self) -> dict:
        snapshot = self.snapshot()
        orders, feedback, inventory = snapshot["orders"], snapshot["feedback"], snapshot["inventory"]
        sales = [row for row in orders if row["status"] not in ("ملغي", "مرتجع")]
        delivered = [row for row in orders if row["status"] == "تم التوصيل"]
        ratings = [row["customer_rating"] for row in orders if row["customer_rating"] is not None]
        today = local_now().date()
        alerts = []
        def alert(kind, message, row=None):
            alerts.append({"type": kind, "message": message, "order_id": row["order_id"] if row else None})
        for row in orders:
            if row["status"] in ACTIVE_STATUSES and row["expected_delivery_date"] and row["expected_delivery_date"] < today:
                alert("overdue", "تأخر عن موعد التوصيل المتوقع", row)
            if row["status"] == "تم التوصيل" and row["outstanding_balance"] > 0:
                alert("unpaid", "تم التوصيل ويوجد مبلغ لم يُحصل", row)
            if row["status"] == "تم التوصيل" and row["feedback_consent"] == "موافق" and row["feedback_status"] == "لم يُطلب":
                alert("feedback_link", "إرسال رابط الفيدباك للعميل", row)
            request_date = row["feedback_request_date"]
            if row["feedback_status"] == "يحتاج متابعة" or (row["feedback_status"] == "تم إرسال الرابط" and request_date and request_date <= today - timedelta(days=3)):
                alert("feedback_followup", "متابعة الرد على طلب الفيدباك", row)
        for item in inventory:
            if item["low_stock"]:
                alert("low_stock", f"مخزون {item['name']} منخفض: {item['available']} كوب متاح")
        for item in feedback:
            if item["followup_status"] in ("مفتوحة", "جاري الحل"):
                alert("feedback_issue", "مشكلة في الفيدباك تحتاج متابعة", item)
        responded = {row["order_id"] for row in feedback}
        delivered_responded = sum(row["order_id"] in responded or row["feedback_status"] == "تم الاستلام" for row in delivered)
        total_value = sum(Decimal(str(row["total_due"])) for row in sales)
        return {
            "total_orders": len(orders),
            "active_orders": sum(row["status"] in ACTIVE_STATUSES for row in orders),
            "delivered_orders": len(delivered), "cancelled_orders": sum(row["status"] == "ملغي" for row in orders),
            "returned_orders": sum(row["status"] == "مرتجع" for row in orders),
            "total_units": sum(row["total_units"] for row in sales),
            "total_order_value": float(total_value),
            "amount_collected": round(sum(row["amount_collected"] - row["refund_amount"] for row in orders), 2),
            "outstanding_balance": round(sum(max(0, row["outstanding_balance"]) for row in sales), 2),
            "contribution_margin": round(sum(row["contribution_margin"] for row in sales), 2),
            "average_order_value": round(float(total_value) / len(sales), 2) if sales else 0.0,
            "average_customer_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "feedback_response_rate": round(100 * delivered_responded / len(delivered), 1) if delivered else 0.0,
            "inventory": inventory,
            "orders_by_status": {status: sum(row["status"] == status for row in orders) for status in ORDER_STATUSES},
            "alerts": alerts,
        }

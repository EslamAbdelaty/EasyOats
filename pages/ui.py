"""Small presentation helpers; all displayed operational text is Arabic."""
from __future__ import annotations

import html
import logging
from datetime import date, datetime
from typing import Any

import streamlit as st

LOGGER = logging.getLogger("easyoats.ui")


def safe(value: Any) -> str:
    return html.escape(str(value if value is not None else "—"))


def money(value: Any) -> str:
    return f"{float(value or 0):,.2f}"


def number(value: Any) -> str:
    return f"{float(value or 0):,.0f}"


def as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def date_label(value: Any, include_time: bool = False) -> str:
    if not value:
        return "لم يُحدد"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return str(value)
    if include_time and isinstance(value, datetime):
        return value.strftime("%Y/%m/%d · %H:%M")
    return value.strftime("%Y/%m/%d") if hasattr(value, "strftime") else str(value)


def page_heading(title: str, subtitle: str) -> None:
    st.markdown('<div class="page-kicker">EASYOATS · إدارة الطلبات</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="page-subtitle">{safe(subtitle)}</div>', unsafe_allow_html=True)


def empty(title: str, description: str, symbol: str = "◌") -> None:
    st.markdown(
        f'<div class="empty-state"><div class="empty-symbol">{safe(symbol)}</div>'
        f'<strong>{safe(title)}</strong><p>{safe(description)}</p></div>',
        unsafe_allow_html=True,
    )


def details(rows: list[tuple[str, Any]]) -> None:
    st.markdown("".join(
        f'<div class="detail-row"><span>{safe(label)}</span><span>{safe(value)}</span></div>'
        for label, value in rows
    ), unsafe_allow_html=True)


def pill(status: str) -> str:
    extra = ""
    if status in ("ملغي", "مرتجع"):
        extra = " pill-muted"
    elif status in ("غير مدفوع", "مدفوع جزئيًا", "خرج للتوصيل"):
        extra = " pill-warn"
    return f'<span class="pill{extra}">{safe(status)}</span>'


def kpi(label: str, value: Any, note: str, unit: str = "") -> None:
    st.markdown(
        f'<div class="kpi"><div class="kpi-label">{safe(label)}</div>'
        f'<div class="kpi-value">{safe(value)}<span class="kpi-unit">{safe(unit)}</span></div>'
        f'<div class="kpi-note">{safe(note)}</div></div>', unsafe_allow_html=True,
    )


def select(label: str, options: list | tuple, value: Any = None, **kwargs: Any) -> Any:
    choices = list(options)
    if value is not None and value not in choices:
        choices.append(value)
    return st.selectbox(label, choices, index=choices.index(value) if value in choices else 0, **kwargs)


def optional_date(label: str, value: Any, key: str) -> date | None:
    return st.date_input(label, value=as_date(value), format="YYYY/MM/DD", key=key)


def go(page: str, order_id: str | None = None) -> None:
    # Navigation's widget has already been rendered for this run; consume the
    # target before constructing it again on the next run.
    st.session_state["next_page"] = page
    if order_id:
        st.session_state["edit_order_id"] = order_id
    st.rerun()


def flash() -> None:
    message = st.session_state.pop("flash_message", None)
    if message:
        st.success(message)


def show_error(exc: Exception) -> None:
    from services.order_service import ValidationError

    if isinstance(exc, ValidationError):
        st.error(str(exc))
    else:
        LOGGER.exception("Application operation failed")
        st.error("تعذّر إتمام العملية الآن. حاول مرة أخرى، أو اطلب مساعدة المسؤول إذا استمرت المشكلة.")


def user_name() -> str:
    return st.session_state.get("current_user", "فريق EasyOats")


def after_save(message: str, service: Any) -> None:
    state = service.sync_status()
    st.session_state["flash_message"] = message + (
        " بياناتك محفوظة، ومزامنة Excel قيد الانتظار. أغلق الملف ثم أعد المحاولة من القائمة الجانبية."
        if state.get("pending") else " تمت مزامنة ملف Excel."
    )


FIELD_LABELS = {
    "customer_name": "اسم العميل", "phone_original": "رقم الهاتف", "phone_normalized": "رقم البحث",
    "order_datetime": "تاريخ الطلب", "source": "مصدر الطلب", "area": "المنطقة", "address": "العنوان",
    "location_url": "رابط الموقع", "order_type": "نوع الطلب", "honey_qty": "كمية العسل واللبن",
    "date_qty": "كمية دبس التمر واللبن", "discount": "الخصم", "delivery_fee": "رسوم التوصيل",
    "delivery_cost": "تكلفة التوصيل", "payment_method": "طريقة الدفع", "amount_collected": "المبلغ المحصل",
    "status": "حالة الطلب", "courier": "المسؤول / المندوب", "expected_delivery_date": "موعد التوصيل المتوقع",
    "actual_delivery_date": "تاريخ التوصيل الفعلي", "tracking_number": "رقم التتبع", "delivery_result": "نتيجة التوصيل",
    "feedback_consent": "موافقة الفيدباك", "feedback_request_date": "تاريخ إرسال الفيدباك",
    "feedback_status": "حالة الفيدباك", "customer_rating": "تقييم العميل", "buy_again": "الشراء مجددًا",
    "notes": "ملاحظات", "returned_sellable": "صلاحية المرتجع", "refund_amount": "المبلغ المسترد",
    "override_reason": "سبب تجاوز المخزون", "overall_rating": "التقييم العام", "response_date": "تاريخ الرد",
    "followup_status": "حالة المتابعة", "followup_owner": "مسؤول المتابعة", "resolution_notes": "الحل",
    "opening_stock": "الرصيد الافتتاحي", "added_stock": "المخزون المضاف", "physical_count": "الجرد الفعلي",
    "reorder_point": "حد إعادة الطلب", "unit_cost": "تكلفة الوحدة", "retail_price": "سعر البيع",
    "offer_price": "سعر عرض كوبين", "honey_unit_cost": "تكلفة العسل واللبن", "date_unit_cost": "تكلفة دبس التمر واللبن",
    "current_user": "المستخدم الحالي", "user_names": "أسماء الفريق", "google_form_url": "رابط استبيان الفيدباك",
    "low_stock_threshold": "حد التنبيه", "consent_confirmed": "تأكيد الموافقة", "taste_rating": "تقييم الطعم",
    "portion_rating": "تقييم حجم العبوة", "preparation_rating": "تقييم سهولة التحضير", "value_rating": "تقييم القيمة",
    "recommendation_score": "الترشيح للآخرين", "preferred_flavor": "النكهة المفضلة", "comments": "تعليق العميل",
    "issue_reported": "المشكلة", "order_id": "رقم الطلب", "inventory_override": "تجاوز المخزون",
}


def audit_rows(entries: list[dict]) -> list[dict]:
    actions = {"create": "إنشاء", "created": "إنشاء", "update": "تحديث", "updated": "تحديث", "create_order": "إنشاء طلب",
               "update_order": "تحديث طلب", "feedback": "حفظ فيدباك", "inventory": "تحديث المخزون", "settings": "تحديث الإعدادات",
               "inventory_override": "تجاوز المخزون", "admin_override": "تجاوز بصلاحية مسؤول", "create_feedback": "إضافة فيدباك",
               "update_feedback": "تحديث فيدباك", "update_inventory": "تحديث المخزون", "update_settings": "تحديث الإعدادات"}
    return [{
        "التاريخ": date_label(row.get("timestamp") or row.get("created_at"), True),
        "رقم الطلب": row.get("order_id") or "—",
        "الإجراء": actions.get(row.get("action"), "تعديل"),
        "الحقل": FIELD_LABELS.get(row.get("field") or row.get("changed_field"), "بيانات السجل"),
        "قبل التعديل": str(row.get("old_value") or "—"),
        "بعد التعديل": str(row.get("new_value") or "—"),
        "المستخدم": row.get("user_name") or row.get("user") or "—",
    } for row in entries]

"""Seven complete Arabic operational pages for EasyOats."""
from __future__ import annotations

import hashlib
import os
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from constants import (
    ACTIVE_STATUSES, BUY_AGAIN_OPTIONS, FEEDBACK_CONSENTS, FEEDBACK_SOURCES,
    FEEDBACK_STATUSES, FOLLOWUP_STATUSES, ORDER_STATUSES, ORDER_TYPES,
    PAYMENT_METHODS, PRODUCTS, SOURCES,
)
from pages.ui import (
    after_save, as_date, audit_rows, date_label, details, empty, flash, go,
    kpi, money, number, optional_date, page_heading, pill, safe, select,
    show_error, user_name,
)


def _orders_table(orders: list[dict]) -> None:
    if not orders:
        empty("لا توجد طلبات هنا بعد", "ستظهر الطلبات المسجّلة وحالتها في هذا المكان.")
        return
    st.dataframe(pd.DataFrame([{
        "رقم الطلب": o["order_id"], "العميل": o["customer_name"],
        "التاريخ": date_label(o.get("order_datetime")), "الأكواب": o.get("total_units", 0),
        "الإجمالي · ج.م": float(o.get("total_due", 0)), "الحالة": o["status"],
        "المتبقي · ج.م": float(o.get("outstanding_balance", 0)),
        "الخطوة التالية": o.get("next_action", "—"),
    } for o in orders]), hide_index=True, width="stretch")


def _inventory_card(item: dict, compact: bool = False) -> None:
    flavor = "date" if item["sku"] == "date" else ""
    available = int(item.get("available", 0))
    total = max(int(item.get("opening_stock", 0)) + int(item.get("added_stock", 0)), 1)
    percentage = max(0, min(100, available / total * 100))
    st.markdown(
        f'<div class="inventory-flavor"><span class="flavor-dot {flavor}"></span>{safe(PRODUCTS.get(item["sku"], item["name"]))}</div>'
        f'<div class="inventory-number">{number(available)} <span class="hint">كوب متاح للبيع</span></div>'
        f'<div class="stock-track"><div class="stock-fill" style="width:{percentage:.1f}%"></div></div>',
        unsafe_allow_html=True,
    )
    if item.get("low_stock"):
        st.warning("المخزون منخفض · حان وقت إعادة الطلب.")
    if compact:
        details([("محجوز للطلبات", f'{number(item.get("reserved"))} كوب'), ("حد إعادة الطلب", f'{number(item.get("reorder_point"))} كوب')])
    else:
        details([
            ("رصيد افتتاحي", number(item.get("opening_stock"))), ("مخزون مضاف", number(item.get("added_stock"))),
            ("محجوز للطلبات النشطة", number(item.get("reserved"))), ("تم توصيله", number(item.get("delivered"))),
            ("مرتجعات غير صالحة", number(item.get("returned_unsellable"))),
            ("الجرد الفعلي", number(item["physical_count"]) if item.get("physical_count") is not None else "لم يُسجل"),
            ("فرق الجرد", number(item["variance"]) if item.get("variance") is not None else "—"),
            ("حد إعادة الطلب", number(item.get("reorder_point"))),
            ("قيمة المخزون المتاح", f'{money(item.get("value"))} ج.م'),
        ])


def dashboard(service: Any) -> None:
    page_heading("لوحة المتابعة", "نظرة واضحة على الطلبات والمخزون وما يحتاج اهتمامك اليوم.")
    flash()
    orders, inventory, feedback = service.list_orders(), service.inventory(), service.list_feedback()
    today = date.today()
    active = [o for o in orders if o["status"] in ACTIVE_STATUSES]
    delivered = [o for o in orders if o["status"] == "تم التوصيل"]
    revenue_orders = [o for o in orders if o["status"] not in ("ملغي", "مرتجع")]
    total_value = sum(float(o.get("total_due", 0)) for o in revenue_orders)
    collected = sum(float(o.get("amount_collected", 0)) - float(o.get("refund_amount", 0)) for o in orders)
    outstanding = sum(max(0, float(o.get("outstanding_balance", 0))) for o in revenue_orders)
    ratings = [float(f["overall_rating"]) for f in feedback if f.get("overall_rating") is not None]
    responded = {f["order_id"] for f in feedback}
    eligible = [o for o in delivered if o.get("feedback_consent") == "موافق"]
    feedback_rate = sum(o["order_id"] in responded for o in eligible) / len(eligible) * 100 if eligible else 0
    st.markdown(
        '<div class="hero"><div><h2>طلبات مرتبة، يوم أخف.</h2>'
        f'<p>لديك {number(len(active))} طلب نشط. كل تفاصيل فريقك في مكان واحد.</p></div>'
        f'<span class="hero-tag">{today.strftime("%Y / %m / %d")} · معًا لكل طلب</span></div>', unsafe_allow_html=True,
    )
    columns = st.columns(4)
    for column, values in zip(columns, [
        ("إجمالي الطلبات", number(len(orders)), "كل الطلبات المسجّلة", "طلب"),
        ("الطلبات النشطة", number(len(active)), "من الطلب الجديد حتى التوصيل", "طلب"),
        ("إجمالي قيمة الطلبات", money(total_value), "باستثناء الملغي والمرتجع", "ج.م"),
        ("المبالغ المحصّلة", money(collected), "بعد خصم المبالغ المستردة", "ج.م"),
    ]):
        with column:
            kpi(*values)
    st.write("")
    quick = st.columns([1, 1, 2])
    with quick[0]:
        if st.button("＋ إضافة طلب جديد", type="primary", width="stretch", key="dashboard_new"):
            go("طلب جديد")
    with quick[1]:
        if st.button("البحث عن عميل", width="stretch", key="dashboard_search"):
            go("البحث عن عميل")

    late = [o for o in active if as_date(o.get("expected_delivery_date")) and as_date(o["expected_delivery_date"]) < today]
    unpaid = [o for o in delivered if float(o.get("outstanding_balance", 0)) > 0]
    need_link = [o for o in eligible if o.get("feedback_status") == "لم يُرسل"]
    followup = [o for o in orders if o.get("feedback_status") == "يحتاج متابعة" or (
        o.get("feedback_status") == "تم الإرسال" and as_date(o.get("feedback_request_date"))
        and as_date(o["feedback_request_date"]) <= today - timedelta(days=3)
    )]
    low = [i for i in inventory if i.get("low_stock")]
    left, right = st.columns([1.2, 1])
    with left:
        with st.container(border=True):
            st.subheader("ما يحتاج انتباهك")
            for label, count in [
                ("طلبات تجاوزت موعد التوصيل", len(late)), ("طلبات موصّلة بها رصيد مستحق", len(unpaid)),
                ("عملاء ينتظرون رابط الفيدباك", len(need_link)), ("طلبات فيدباك تحتاج متابعة", len(followup)),
                ("منتجات وصلت لحد إعادة الطلب", len(low)),
            ]:
                st.markdown(f'<div class="alert-item"><span class="alert-text">{safe(label)}</span><span class="alert-count">{count}</span></div>', unsafe_allow_html=True)
            if not any((late, unpaid, need_link, followup, low)):
                st.caption("كل شيء هادئ حاليًا. لا توجد إجراءات متأخرة.")
            alert_orders = {o["order_id"]: o for o in late + unpaid + need_link + followup}
            if alert_orders:
                selected = st.selectbox("فتح طلب يحتاج متابعة", list(alert_orders), format_func=lambda oid: f'{oid} · {alert_orders[oid]["customer_name"]}', key="alert_order")
                if st.button("متابعة الطلب", key="alert_open", width="stretch"):
                    go("تحديث الطلب", selected)
    with right:
        with st.container(border=True):
            st.subheader("الطلبات حسب الحالة")
            counts = Counter(o["status"] for o in orders)
            if orders:
                chart = pd.DataFrame({"الحالة": ORDER_STATUSES, "الطلبات": [counts[s] for s in ORDER_STATUSES]})
                st.bar_chart(chart, x="الحالة", y="الطلبات", color="#718b5e", horizontal=True, height=260)
            else:
                empty("البداية من أول طلب", "أضف طلبًا لعرض توزيع الحالات.", "↗")

    st.markdown('<div class="section-title">المخزون المتاح</div>', unsafe_allow_html=True)
    stock_cols = st.columns(2)
    for column, item in zip(stock_cols, inventory):
        with column, st.container(border=True):
            _inventory_card(item, compact=True)
    st.markdown('<div class="section-title">أرقام تساعدك على اتخاذ القرار</div>', unsafe_allow_html=True)
    metrics = [
        ("طلبات تم توصيلها", number(len(delivered))), ("طلبات ملغية", number(sum(o["status"] == "ملغي" for o in orders))),
        ("إجمالي الأكواب المطلوبة", number(sum(o.get("total_units", 0) for o in orders))),
        ("الرصيد المستحق · ج.م", money(outstanding)),
        ("هامش المساهمة التقديري · ج.م", money(sum(float(o.get("contribution_margin", 0)) for o in revenue_orders))),
        ("متوسط الطلب · ج.م", money(total_value / len(revenue_orders) if revenue_orders else 0)),
        ("متوسط تقييم العملاء", f"{sum(ratings)/len(ratings):.1f} / 5" if ratings else "—"),
        ("معدل استجابة الفيدباك", f"{feedback_rate:.0f}%"),
    ]
    for offset in range(0, len(metrics), 4):
        for column, (label, value) in zip(st.columns(4), metrics[offset:offset + 4]):
            column.metric(label, value)
    st.caption("معدل الاستجابة محسوب بين الطلبات الموصّلة التي وافق أصحابها على الفيدباك. هامش المساهمة تقديري ولا يشمل المصروفات الثابتة.")
    st.markdown('<div class="section-title">آخر الطلبات</div>', unsafe_allow_html=True)
    _orders_table(orders[:8])


def _override_controls(prefix: str) -> tuple[bool, str, str]:
    with st.expander("تجاوز المخزون بصلاحية مسؤول"):
        if st.session_state.get("auth_mode"):
            if st.session_state.get("auth_role") != "admin":
                st.caption("تجاوز المخزون متاح للمسؤولين فقط.")
                return False, "", ""
            enabled = st.checkbox("السماح بتجاوز الكمية المتاحة لهذا التعديل", key=f"{prefix}_override")
            reason = st.text_input("سبب التجاوز (إلزامي)", key=f"{prefix}_override_reason", disabled=not enabled)
            st.caption("سيُسجل حساب المسؤول وسبب التجاوز في سجل التعديلات.")
            return enabled, reason, ""
        if not os.getenv("ADMIN_PIN"):
            st.caption("تجاوز المخزون غير مفعّل. يمكن للمسؤول تفعيله من إعدادات تشغيل التطبيق.")
            return False, "", ""
        enabled = st.checkbox("السماح بتجاوز الكمية المتاحة لهذا التعديل", key=f"{prefix}_override")
        reason = st.text_input("سبب التجاوز (إلزامي)", key=f"{prefix}_override_reason", disabled=not enabled)
        pin = st.text_input("رمز المسؤول", type="password", key=f"{prefix}_admin_pin", disabled=not enabled)
        st.caption("يُسجل اسم المستخدم وسبب التجاوز في سجل التعديلات.")
        return enabled, reason, pin


def _authorize(service: Any, enabled: bool, reason: str, pin: str) -> bool:
    if not enabled:
        return True
    if not reason.strip():
        st.error("اكتب سبب تجاوز المخزون قبل الحفظ.")
        return False
    if st.session_state.get("auth_mode"):
        if st.session_state.get("auth_role") != "admin":
            st.error("هذه العملية متاحة لمسؤول النظام فقط.")
            return False
        return True
    if not service.authenticate_admin(pin):
        st.error("رمز المسؤول غير صحيح. راجع المسؤول ثم حاول مرة أخرى.")
        return False
    return True


def new_order(service: Any) -> None:
    page_heading("طلب جديد", "سجّل تفاصيل العميل والطلب، وسنحسب الإجماليات ونحدّث المخزون تلقائيًا.")
    flash()
    settings = service.get_settings()
    generation = st.session_state.get("new_order_generation", 0)
    prefix = f"new_{generation}"
    main, summary = st.columns([2.2, 1])
    with main:
        with st.container(border=True):
            st.subheader("١ · بيانات العميل")
            a, b = st.columns(2)
            name = a.text_input("اسم العميل *", key=f"{prefix}_customer_name", placeholder="الاسم بالكامل")
            phone = b.text_input("رقم الموبايل *", key=f"{prefix}_phone", placeholder="01012345678", help="نقبل الصيغ المصرية المحلية والدولية مثل +201012345678.")
            a, b = st.columns(2)
            source = select("مصدر الطلب", SOURCES, key=f"{prefix}_source") if False else None
            with a:
                source = select("مصدر الطلب", SOURCES, key=f"{prefix}_source")
            area = b.text_input("المنطقة", key=f"{prefix}_area", placeholder="مثال: مدينة نصر")
            address = st.text_area("العنوان بالكامل", key=f"{prefix}_address", height=85, placeholder="الشارع، العمارة، الدور، وأقرب علامة مميزة")
            location = st.text_input("رابط الموقع / خرائط جوجل", key=f"{prefix}_location", placeholder="https://maps.google.com/…")
        with st.container(border=True):
            st.subheader("٢ · المنتجات والسعر")
            a, b, c = st.columns([1.2, 1, 1])
            with a:
                order_type = select("نوع الطلب", ORDER_TYPES, key=f"{prefix}_type")
            honey = b.number_input("عسل ولبن · أكواب", min_value=0, step=1, value=1, key=f"{prefix}_honey")
            dates = c.number_input("دبس تمر ولبن · أكواب", min_value=0, step=1, value=0, key=f"{prefix}_date")
            a, b, c = st.columns(3)
            discount = a.number_input("الخصم · ج.م", min_value=0.0, step=5.0, key=f"{prefix}_discount")
            delivery_fee = b.number_input("رسوم التوصيل على العميل · ج.م", min_value=0.0, step=5.0, key=f"{prefix}_delivery_fee")
            delivery_cost = c.number_input("تكلفة التوصيل الفعلية · ج.م", min_value=0.0, step=5.0, key=f"{prefix}_delivery_cost")
            st.caption(f'السعر العادي: {money(settings["retail_price"])} ج.م للكوب · سعر عرض كوبين الحالي: {money(settings["offer_price"])} ج.م · العينة مجانية للمنتج.')
        with st.container(border=True):
            st.subheader("٣ · الدفع والتوصيل")
            a, b = st.columns(2)
            with a:
                payment_method = select("طريقة الدفع", PAYMENT_METHODS, key=f"{prefix}_payment")
            collected = b.number_input("المبلغ المحصّل · ج.م", min_value=0.0, step=5.0, key=f"{prefix}_collected")
            a, b = st.columns(2)
            with a:
                status = select("حالة الطلب", ORDER_STATUSES, key=f"{prefix}_status")
            courier = b.text_input("المسؤول / مندوب التوصيل", key=f"{prefix}_courier")
            a, b = st.columns(2)
            with a:
                expected = optional_date("موعد التوصيل المتوقع", None, f"{prefix}_expected")
            tracking = b.text_input("رقم التتبع / المرجع", key=f"{prefix}_tracking")
            actual = None
            if status == "تم التوصيل":
                actual = optional_date("تاريخ التوصيل الفعلي *", None, f"{prefix}_actual")
            a, b = st.columns(2)
            ordered_date = a.date_input("تاريخ الطلب", value=date.today(), format="YYYY/MM/DD", key=f"{prefix}_order_date")
            ordered_time = b.time_input("وقت الطلب", value=datetime.now().time().replace(second=0, microsecond=0), key=f"{prefix}_order_time")
            with st.expander("الفيدباك والملاحظات", expanded=True):
                consent = select("موافقة العميل على الفيدباك", FEEDBACK_CONSENTS, key=f"{prefix}_consent")
                notes = st.text_area("ملاحظات الطلب", key=f"{prefix}_notes", height=85)
                sellable = st.checkbox("المرتجع صالح للبيع وإعادته للمخزون", key=f"{prefix}_sellable") if status == "مرتجع" else False
            override, reason, pin = _override_controls(prefix)

    units = honey + dates
    unit_price = 0 if order_type == "عينة" else float(settings["offer_price"]) / 2 if order_type == "عرض 2 بـ120" else float(settings["retail_price"])
    subtotal = units * unit_price
    total_due = subtotal - discount + delivery_fee
    product_cost = honey * float(settings["honey_unit_cost"]) + dates * float(settings["date_unit_cost"])
    with summary:
        st.markdown('<div class="section-title">ملخص الطلب</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="summary-box"><div class="hint">إجمالي المطلوب من العميل</div><div class="summary-total">{money(total_due)} <span class="hint">ج.م</span></div></div>', unsafe_allow_html=True)
        details([
            ("عدد الأكواب", number(units)), ("سعر الكوب", f"{money(unit_price)} ج.م"),
            ("قيمة المنتجات", f"{money(subtotal)} ج.م"), ("الخصم", f"{money(discount)} ج.م"),
            ("التوصيل على العميل", f"{money(delivery_fee)} ج.م"), ("المبلغ المحصّل", f"{money(collected)} ج.م"),
            ("الرصيد المتبقي", f"{money(total_due - collected)} ج.م"),
            ("تكلفة المنتجات التقديرية", f"{money(product_cost)} ج.م"),
            ("هامش المساهمة التقديري", f"{money(total_due - product_cost - delivery_cost)} ج.م"),
        ])
        st.write("")
        for item in service.inventory():
            st.caption(f'{PRODUCTS.get(item["sku"], item["name"])}: {number(item["available"])} كوب متاح')
        st.caption("يُنشأ رقم طلب ثابت تلقائيًا عند الحفظ، وتُحدّث نسخة Excel مباشرة.")
        if st.button("حفظ الطلب", type="primary", width="stretch", key="save_new_order"):
            if not _authorize(service, override, reason, pin):
                return
            data = {
                "customer_name": name, "phone_original": phone, "order_datetime": datetime.combine(ordered_date, ordered_time),
                "source": source, "area": area, "address": address, "location_url": location,
                "order_type": order_type, "honey_qty": honey, "date_qty": dates, "discount": discount,
                "delivery_fee": delivery_fee, "delivery_cost": delivery_cost, "payment_method": payment_method,
                "amount_collected": collected, "status": status, "courier": courier,
                "expected_delivery_date": expected, "actual_delivery_date": actual, "tracking_number": tracking,
                "feedback_consent": consent, "notes": notes, "returned_sellable": sellable,
            }
            try:
                order = service.create_order(data, user=user_name(), override_reason=reason if override else None, is_admin=override)
                st.session_state["last_created_order_id"] = order["order_id"]
                st.session_state["new_order_generation"] = generation + 1
                after_save(f'تم حفظ الطلب {order["order_id"]} بنجاح.', service)
                st.rerun()
            except Exception as exc:
                show_error(exc)
        last_id = st.session_state.get("last_created_order_id")
        if last_id and st.button(f"فتح آخر طلب · {last_id}", width="stretch", key="open_last_order"):
            go("تحديث الطلب", last_id)


def _feedback_table(entries: list[dict]) -> None:
    if not entries:
        st.caption("لم يُسجَّل فيدباك لهذا الطلب بعد.")
        return
    st.dataframe(pd.DataFrame([{
        "رقم الطلب": f["order_id"], "تاريخ الرد": date_label(f.get("response_date")),
        "المصدر": "استبيان جوجل" if f.get("source") == "Google Form" else f.get("source", "—"),
        "التقييم العام": f.get("overall_rating"), "الطعم": f.get("taste_rating"),
        "حجم العبوة": f.get("portion_rating"), "سهولة التحضير": f.get("preparation_rating"),
        "القيمة مقابل السعر": f.get("value_rating"), "الشراء مجددًا": f.get("buy_again"),
        "الترشيح للآخرين": f.get("recommendation_score"), "النكهة المفضلة": f.get("preferred_flavor"),
        "تعليق العميل": f.get("comments"), "المشكلة": f.get("issue_reported"),
        "المسؤول": f.get("followup_owner"), "حالة المتابعة": f.get("followup_status"), "الحل": f.get("resolution_notes"),
    } for f in entries]), hide_index=True, width="stretch")


def _order_card(service: Any, order: dict, allow_open: bool = True) -> None:
    oid = order["order_id"]
    with st.container(border=True):
        st.markdown(f'<div class="order-head"><div><span class="order-id">{safe(oid)}</span> · {safe(order["customer_name"])}</div>{pill(order["status"])}</div>', unsafe_allow_html=True)
        a, b, c = st.columns(3)
        with a:
            details([
                ("تاريخ الطلب", date_label(order.get("order_datetime"), True)),
                ("رقم الموبايل", order.get("phone_original")), ("رقم البحث", order.get("phone_normalized")),
                ("عسل ولبن", f'{number(order.get("honey_qty"))} كوب'),
                ("دبس تمر ولبن", f'{number(order.get("date_qty"))} كوب'),
                ("نوع الطلب", order.get("order_type")), ("مصدر الطلب", order.get("source")),
            ])
        with b:
            details([
                ("الإجمالي", f'{money(order.get("total_due"))} ج.م'),
                ("المحصّل", f'{money(order.get("amount_collected"))} ج.م'),
                ("المتبقي", f'{money(order.get("outstanding_balance"))} ج.م'),
                ("حالة الدفع", order.get("payment_status")), ("طريقة الدفع", order.get("payment_method")),
                ("المبلغ المسترد", f'{money(order.get("refund_amount"))} ج.م'),
                ("حالة الفيدباك", order.get("feedback_status")),
            ])
        with c:
            details([
                ("التوصيل المتوقع", date_label(order.get("expected_delivery_date"))),
                ("التوصيل الفعلي", date_label(order.get("actual_delivery_date"))),
                ("المسؤول / المندوب", order.get("courier") or "لم يُحدد"),
                ("التتبع / المرجع", order.get("tracking_number") or "—"),
                ("نتيجة التوصيل", order.get("delivery_result") or "—"),
                ("المنطقة", order.get("area") or "—"),
                ("الخطوة التالية", order.get("next_action") or "—"),
            ])
        if order.get("address"):
            st.caption(f'العنوان: {order["address"]}')
        if order.get("location_url"):
            st.link_button("فتح موقع العميل", order["location_url"])
        if order.get("notes"):
            st.write(f'ملاحظات: {order["notes"]}')
        with st.expander("الفيدباك وسجل التعديلات"):
            st.markdown("**فيدباك العميل**")
            _feedback_table(service.list_feedback(order_id=oid))
            st.markdown("**سجل التعديلات**")
            history = audit_rows(service.audit_history(order_id=oid))
            if history:
                st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
            else:
                st.caption("لا توجد تعديلات إضافية مسجلة.")
        if allow_open and st.button("فتح وتحديث الطلب", key=f"edit_{oid}", width="stretch"):
            go("تحديث الطلب", oid)


def customer_search(service: Any) -> None:
    page_heading("البحث عن عميل", "رقم واحد يعرض لك كل الطلبات والمدفوعات ومتابعات العميل.")
    flash()
    with st.container(border=True):
        with st.form("customer_search_form"):
            phone = st.text_input("رقم موبايل العميل", placeholder="01012345678 أو +201012345678", key="search_phone")
            partial = st.checkbox("بحث جزئي بآخر ٤ أرقام على الأقل", key="search_partial")
            submitted = st.form_submit_button("بحث عن الطلبات", type="primary", width="stretch")
            st.caption("البحث المطابق هو الوضع الافتراضي. الأرقام المحلية والدولية والمسافات والشرطات تُعامل كرقم واحد.")
        if submitted:
            try:
                results = service.search_orders(phone, partial=partial)
                st.session_state["search_criteria"] = (phone, partial)
                st.session_state["search_result_ids"] = [o["order_id"] for o in results]
            except Exception as exc:
                st.session_state.pop("search_criteria", None)
                show_error(exc)
    criteria = st.session_state.get("search_criteria")
    if not criteria:
        empty("قصة كل عميل تبدأ برقمه", "أدخل رقم الموبايل للاطلاع على سجل الطلبات الكامل.", "⌕")
        return
    orders = service.search_orders(criteria[0], partial=criteria[1])
    if not orders:
        empty("لم نعثر على طلبات لهذا الرقم", "راجع الرقم أو جرّب البحث الجزئي بآخر أربعة أرقام.")
        return
    st.caption(f"تم العثور على {len(orders)} طلب · الأحدث أولًا")
    for order in orders:
        _order_card(service, order)


def update_order(service: Any) -> None:
    page_heading("تحديث الطلب", "تابع التوصيل والتحصيل والمرتجعات والفيدباك مع الاحتفاظ بكل التعديلات.")
    flash()
    orders = service.list_orders()
    if not orders:
        empty("لا توجد طلبات لتحديثها", "أضف أول طلب ثم تابع حالته من هنا.")
        if st.button("إضافة طلب جديد", type="primary", key="update_create"):
            go("طلب جديد")
        return
    options = {o["order_id"]: o for o in orders}
    target = st.session_state.pop("edit_order_id", None)
    if target in options:
        st.session_state["update_selected_order"] = target
    oid = st.selectbox("اختر الطلب", list(options), format_func=lambda value: f'{value} · {options[value]["customer_name"]} · {options[value]["phone_normalized"]}', key="update_selected_order")
    order = service.get_order(oid)
    generation = st.session_state.get("update_generation", 0)
    prefix = f"update_{oid}_{generation}"
    a, b, c, d = st.columns(4)
    a.metric("إجمالي الطلب · ج.م", money(order["total_due"]))
    b.metric("المبلغ المحصل · ج.م", money(order["amount_collected"]))
    c.metric("المبلغ المتبقي · ج.م", money(order["outstanding_balance"]))
    d.metric("حالة الطلب الحالية", order["status"])
    left, right = st.columns([1.5, 1])
    with left, st.container(border=True):
        st.subheader("حالة الطلب والتوصيل")
        a, b = st.columns(2)
        with a:
            status = select("حالة الطلب", ORDER_STATUSES, order["status"], key=f"{prefix}_status")
        courier = b.text_input("المسؤول / مندوب التوصيل", value=order.get("courier") or "", key=f"{prefix}_courier")
        a, b = st.columns(2)
        with a:
            expected = optional_date("موعد التوصيل المتوقع", order.get("expected_delivery_date"), f"{prefix}_expected")
        with b:
            actual = optional_date("تاريخ التوصيل الفعلي" + (" *" if status == "تم التوصيل" else ""), order.get("actual_delivery_date"), f"{prefix}_actual")
        tracking = st.text_input("رقم التتبع / المرجع", value=order.get("tracking_number") or "", key=f"{prefix}_tracking")
        delivery_result = st.text_input("نتيجة التوصيل", value=order.get("delivery_result") or "", placeholder="مثال: تم الاستلام من العميل", key=f"{prefix}_delivery_result")
        notes = st.text_area("ملاحظات الطلب", value=order.get("notes") or "", height=110, key=f"{prefix}_notes")
        sellable = bool(order.get("returned_sellable", False))
        if status == "مرتجع":
            sellable = st.checkbox("أؤكد أن الوحدات المرتجعة صالحة للبيع", value=sellable, key=f"{prefix}_sellable")
            st.caption("تُعاد الوحدات للمخزون فقط عند تأكيد صلاحيتها للبيع.")
        if status == "ملغي":
            st.info("سيُلغى حجز مخزون هذا الطلب عند الحفظ، ويظل الطلب محفوظًا في سجل العميل.")
    with right, st.container(border=True):
        st.subheader("الدفع والتحصيل")
        payment_method = select("طريقة الدفع", PAYMENT_METHODS, order.get("payment_method"), key=f"{prefix}_payment")
        collected = st.number_input("إجمالي المبلغ المحصّل · ج.م", min_value=0.0, value=float(order.get("amount_collected", 0)), step=5.0, key=f"{prefix}_collected", help="أدخل الإجمالي المحصّل حتى الآن، وليس مبلغ الدفعة الجديدة فقط.")
        refund = st.number_input("إجمالي المبلغ المسترد للعميل · ج.م", min_value=0.0, value=float(order.get("refund_amount", 0)), step=5.0, key=f"{prefix}_refund")
        current_balance = float(order["total_due"]) - collected
        details([("المتبقي بعد هذا التعديل", f"{money(current_balance)} ج.م")])
        if status == "تم التوصيل" and current_balance > 0:
            st.warning(f"هذا الطلب تم توصيله وما زال عليه {money(current_balance)} ج.م للتحصيل.")
        if status == "تم التوصيل" and actual is None:
            st.warning("أدخل تاريخ التوصيل الفعلي لإتمام الحفظ.")

    with st.container(border=True):
        st.subheader("الفيدباك والمتابعة")
        a, b, c = st.columns(3)
        with a:
            consent = select("موافقة العميل", FEEDBACK_CONSENTS, order.get("feedback_consent"), key=f"{prefix}_consent")
        with b:
            feedback_status = select("حالة الفيدباك", FEEDBACK_STATUSES, order.get("feedback_status"), key=f"{prefix}_feedback_status")
        with c:
            requested = optional_date("تاريخ إرسال رابط الفيدباك", order.get("feedback_request_date"), f"{prefix}_requested")
        a, b = st.columns(2)
        with a:
            rating = select("تقييم العميل", [None, 1, 2, 3, 4, 5], order.get("customer_rating"), format_func=lambda value: "لم يُسجّل" if value is None else f"{value} / 5", key=f"{prefix}_rating")
        with b:
            buy_again = select("هل سيشتري مرة أخرى؟", BUY_AGAIN_OPTIONS, order.get("buy_again") or "", format_func=lambda value: value or "لم يُسجّل", key=f"{prefix}_buy_again")
        if status == "تم التوصيل" and consent == "موافق" and feedback_status == "لم يُرسل":
            st.info("العميل وافق على الفيدباك. الخطوة التالية: أرسل رابط الاستبيان ثم سجّل تاريخ الإرسال.")
            url = service.get_settings().get("google_form_url")
            if url:
                st.link_button("فتح استبيان الفيدباك", url)
            else:
                st.caption("يمكن حفظ رابط الاستبيان من صفحة الإعدادات والتصدير.")
    override, reason, pin = _override_controls(prefix)
    if st.button("حفظ التحديثات", type="primary", width="stretch", key="save_order_update"):
        if not _authorize(service, override, reason, pin):
            return
        try:
            service.update_order(oid, {
                "status": status, "courier": courier, "expected_delivery_date": expected, "actual_delivery_date": actual,
                "tracking_number": tracking, "delivery_result": delivery_result, "notes": notes,
                "returned_sellable": sellable, "payment_method": payment_method, "amount_collected": collected,
                "refund_amount": refund, "feedback_consent": consent, "feedback_status": feedback_status,
                "feedback_request_date": requested, "customer_rating": rating, "buy_again": buy_again,
            }, user=user_name(), override_reason=reason if override else None, is_admin=override)
            st.session_state["update_generation"] = generation + 1
            after_save(f"تم تحديث الطلب {oid} وحفظ سجل التعديلات.", service)
            st.rerun()
        except Exception as exc:
            show_error(exc)
    with st.expander("عرض بيانات الطلب وسجل العميل"):
        _order_card(service, order, allow_open=False)


def inventory_page(service: Any) -> None:
    page_heading("المخزون", "راقب الكميات المتاحة والمحجوزة، وسجّل الإضافات ونتيجة الجرد.")
    flash()
    items = service.inventory()
    columns = st.columns(2)
    for column, item in zip(columns, items):
        sku = item["sku"]
        with column, st.container(border=True):
            _inventory_card(item)
            with st.expander("تحديث الرصيد والجرد"):
                with st.form(f"inventory_form_{sku}"):
                    opening = st.number_input("الرصيد الافتتاحي · كوب", min_value=0, value=int(item.get("opening_stock", 0)), step=1, key=f"stock_{sku}_opening")
                    added = st.number_input("إجمالي المخزون المضاف · كوب", min_value=0, value=int(item.get("added_stock", 0)), step=1, key=f"stock_{sku}_added", help="أدخل مجموع كل الإضافات منذ بداية التشغيل.")
                    count_enabled = st.checkbox("تسجيل جرد فعلي", value=item.get("physical_count") is not None, key=f"stock_{sku}_count_enabled")
                    physical = st.number_input("عدد الأكواب في الجرد الفعلي", min_value=0, value=int(item.get("physical_count") or 0), step=1, key=f"stock_{sku}_physical")
                    reorder = st.number_input("حد إعادة الطلب · كوب", min_value=0, value=int(item.get("reorder_point", 50)), step=1, key=f"stock_{sku}_reorder")
                    saved = st.form_submit_button("حفظ بيانات المخزون", width="stretch", type="primary")
                if saved:
                    try:
                        service.update_inventory(sku, {"opening_stock": opening, "added_stock": added, "physical_count": physical if count_enabled else None, "reorder_point": reorder}, user=user_name())
                        after_save(f'تم تحديث مخزون {PRODUCTS.get(sku, item["name"])}.', service)
                        st.rerun()
                    except Exception as exc:
                        show_error(exc)
    st.info("الطلبات النشطة تحجز الكمية تلقائيًا. الإلغاء يحرّر الحجز، والمرتجع يعود للمخزون عند تأكيد صلاحيته للبيع.")
    st.caption("الجرد الفعلي يقارن رصيد المستودع الفعلي بالرصيد المتوقع، بما فيه الكميات المحجوزة التي لم تُسلَّم بعد. لا يغيّر تسجيل الجرد الرصيد تلقائيًا.")
    st.markdown('<div class="section-title">الطلبات التي تحجز مخزونًا</div>', unsafe_allow_html=True)
    _orders_table([o for o in service.list_orders() if o["status"] in ACTIVE_STATUSES])


def feedback_page(service: Any) -> None:
    page_heading("الفيدباك", "كل رأي يساعدنا على تحسين الكوب القادم وتجربة العميل.")
    flash()
    orders = service.list_orders()
    entries = service.list_feedback()
    a, b, c = st.columns(3)
    ratings = [float(f["overall_rating"]) for f in entries if f.get("overall_rating") is not None]
    a.metric("ردود العملاء", number(len(entries)))
    b.metric("متوسط التقييم", f"{sum(ratings)/len(ratings):.1f} / 5" if ratings else "—")
    c.metric("متابعات مفتوحة", number(sum(f.get("followup_status") in ("مفتوح", "قيد المتابعة") for f in entries)))
    if not orders:
        empty("الفيدباك يبدأ بطلب", "أضف طلبًا أولًا لربط رأي العميل بتجربته.")
        return
    options = {o["order_id"]: o for o in orders}
    form_tab, history_tab = st.tabs(["تسجيل أو تحديث فيدباك", "كل الردود والمتابعات"])
    with form_tab:
        mode = st.radio("الإجراء", ["إضافة رد جديد", "تحديث رد سابق"], horizontal=True, key="feedback_mode")
        record: dict = {}
        if mode == "تحديث رد سابق":
            if not entries:
                st.info("لم تُسجّل ردود سابقة بعد.")
                return
            lookup = {f["id"]: f for f in entries}
            feedback_id = st.selectbox("اختر الرد", list(lookup), format_func=lambda value: f'{lookup[value]["order_id"]} · {date_label(lookup[value].get("response_date"))} · {lookup[value].get("overall_rating", "—")} / 5', key="feedback_edit_id")
            record = lookup[feedback_id]
        prefix = f'feedback_{record.get("id", "new")}_{st.session_state.get("feedback_generation", 0)}'
        with st.form(f"{prefix}_form"):
            st.subheader("الطلب وموافقة العميل")
            a, b = st.columns(2)
            with a:
                order_id = select("رقم الطلب", list(options), record.get("order_id"), format_func=lambda value: f'{value} · {options[value]["customer_name"]}', key=f"{prefix}_order", disabled=bool(record))
            response_date = b.date_input("تاريخ الرد", value=as_date(record.get("response_date")) or date.today(), format="YYYY/MM/DD", key=f"{prefix}_date")
            a, b = st.columns(2)
            with a:
                source = select("مصدر الفيدباك", FEEDBACK_SOURCES, record.get("source"), format_func=lambda value: "استبيان جوجل" if value == "Google Form" else value, key=f"{prefix}_source")
            with b:
                consent = st.checkbox("تم تأكيد موافقة العميل على تسجيل الفيدباك", value=bool(record.get("consent_confirmed", False)), key=f"{prefix}_consent")
            st.subheader("تقييم التجربة")
            rating_fields = [("overall_rating", "التقييم العام"), ("taste_rating", "الطعم"), ("portion_rating", "حجم العبوة"), ("preparation_rating", "سهولة التحضير"), ("value_rating", "القيمة مقابل السعر")]
            ratings_data = {}
            columns = st.columns(5)
            for column, (field, label) in zip(columns, rating_fields):
                with column:
                    ratings_data[field] = select(label, [1, 2, 3, 4, 5], record.get(field, 5), key=f"{prefix}_{field}")
            a, b, c = st.columns(3)
            with a:
                buy_again = select("هل سيشتري مجددًا؟", ["نعم", "ممكن", "لا"], record.get("buy_again", "نعم"), key=f"{prefix}_again")
            recommendation = b.number_input("الترشيح للآخرين · من ١٠", min_value=0, max_value=10, value=int(record.get("recommendation_score", 10) or 0), step=1, key=f"{prefix}_recommendation")
            with c:
                flavor = select("النكهة المفضلة", ["عسل ولبن", "دبس تمر ولبن", "كلاهما", "لا تفضيل"], record.get("preferred_flavor", "لا تفضيل"), key=f"{prefix}_flavor")
            comments = st.text_area("تعليقات العميل", value=record.get("comments") or "", height=90, key=f"{prefix}_comments")
            st.subheader("المشكلة والمتابعة")
            issue = st.text_area("مشكلة أبلغ عنها العميل", value=record.get("issue_reported") or "", height=75, key=f"{prefix}_issue")
            a, b = st.columns(2)
            owner = a.text_input("مسؤول المتابعة", value=record.get("followup_owner") or "", key=f"{prefix}_owner")
            with b:
                followup_status = select("حالة المتابعة", FOLLOWUP_STATUSES, record.get("followup_status"), key=f"{prefix}_followup")
            resolution = st.text_area("ملاحظات الحل", value=record.get("resolution_notes") or "", height=75, key=f"{prefix}_resolution")
            submitted = st.form_submit_button("حفظ الفيدباك", type="primary", width="stretch")
        if submitted:
            data = dict(record)
            data.update({"order_id": order_id, "response_date": response_date, "source": source, "consent_confirmed": consent,
                         **ratings_data, "buy_again": buy_again, "recommendation_score": recommendation, "preferred_flavor": flavor,
                         "comments": comments, "issue_reported": issue, "followup_owner": owner, "followup_status": followup_status, "resolution_notes": resolution})
            try:
                service.save_feedback(data, user=user_name())
                st.session_state["feedback_generation"] = st.session_state.get("feedback_generation", 0) + 1
                after_save("تم حفظ الفيدباك وربطه بسجل الطلب.", service)
                st.rerun()
            except Exception as exc:
                show_error(exc)
    with history_tab:
        filter_status = st.selectbox("حالة المتابعة المعروضة", ["كل الردود"] + FOLLOWUP_STATUSES, key="feedback_filter")
        shown = entries if filter_status == "كل الردود" else [f for f in entries if f.get("followup_status") == filter_status]
        _feedback_table(shown)


def settings_page(service: Any) -> None:
    page_heading("الإعدادات والتصدير", "أسعارك وتكاليفك وأسماء فريقك، مع نسخة Excel جاهزة للعمل.")
    flash()
    settings = service.get_settings()
    hosted = bool(st.session_state.get("auth_mode"))
    is_admin = not hosted or st.session_state.get("auth_role") == "admin"
    settings_tab, export_tab, audit_tab = st.tabs(["إعدادات العمل", "Excel والتصدير", "سجل التعديلات"])
    with settings_tab:
        if not is_admin:
            st.info("يمكن للمسؤول فقط تغيير الأسعار والتكاليف وصلاحيات الفريق.")
        else:
            with st.form("settings_form"):
                st.subheader("الأسعار والتكاليف")
                a, b = st.columns(2)
                retail = a.number_input("سعر الكوب العادي · ج.م", min_value=0.0, value=float(settings["retail_price"]), step=5.0, key="settings_retail")
                offer = b.number_input("سعر عرض كوبين · ج.م", min_value=0.0, value=float(settings["offer_price"]), step=5.0, key="settings_offer")
                a, b = st.columns(2)
                honey_cost = a.number_input("تكلفة كوب عسل ولبن · ج.م", min_value=0.0, value=float(settings["honey_unit_cost"]), step=1.0, key="settings_honey_cost")
                date_cost = b.number_input("تكلفة كوب دبس تمر ولبن · ج.م", min_value=0.0, value=float(settings["date_unit_cost"]), step=1.0, key="settings_date_cost")
                threshold = st.number_input("حد تنبيه انخفاض المخزون الافتراضي · كوب", min_value=0, value=int(settings.get("low_stock_threshold", 50)), step=1, key="settings_threshold")
                st.caption("تُطبق الأسعار والتكاليف الجديدة على الطلبات الجديدة؛ يحتفظ الطلب السابق بأسعاره وتكاليفه عند الإنشاء. تحديث حد التنبيه الافتراضي يطبّقه على المنتجين.")
                st.subheader("استبيان الفيدباك")
                form_url = st.text_input("رابط استبيان جوجل", value=settings.get("google_form_url") or "", placeholder="https://forms.gle/…", key="settings_form_url", help="يمكن نسخ الرابط ومشاركته مع العملاء الموافقين على الفيدباك.")
                names = settings.get("user_names") or ["فريق EasyOats"]
                if isinstance(names, str):
                    names = [names]
                if hosted:
                    user_names = "\n".join(names)
                    current_name = settings.get("current_user") or "فريق EasyOats"
                else:
                    st.subheader("فريق العمل المحلي")
                    user_names = st.text_area("أسماء أعضاء الفريق · اسم واحد في كل سطر", value="\n".join(names), height=100, key="settings_users")
                    current_name = st.text_input("اسم المستخدم الافتراضي", value=settings.get("current_user") or user_name(), key="settings_default_user")
                saved = st.form_submit_button("حفظ الإعدادات", type="primary", width="stretch")
            if saved:
                try:
                    names_list = list(dict.fromkeys(n.strip() for n in user_names.splitlines() if n.strip()))
                    service.update_settings({"retail_price": retail, "offer_price": offer, "honey_unit_cost": honey_cost, "date_unit_cost": date_cost,
                                             "low_stock_threshold": threshold, "google_form_url": form_url,
                                             "user_names": names_list, "current_user": current_name}, user=user_name())
                    after_save("تم حفظ إعدادات العمل.", service)
                    st.rerun()
                except Exception as exc:
                    show_error(exc)
            if hosted:
                st.divider()
                st.subheader("حسابات الفريق وصلاحياته")
                actor_email = st.session_state.get("auth_email", "")
                try:
                    accounts = service.list_users(actor_email)
                    st.dataframe(pd.DataFrame([{
                        "الاسم": account["display_name"], "البريد الإلكتروني": account["email"],
                        "الصلاحية": "مسؤول" if account["role"] == "admin" else "موظف",
                        "الحالة": "نشط" if account["active"] else "موقوف",
                        "آخر دخول": date_label(account.get("last_login"), True),
                    } for account in accounts]), hide_index=True, width="stretch")
                    with st.form("user_access_form", clear_on_submit=True):
                        st.caption("أدخل بريداً جديداً للإضافة، أو بريداً موجوداً لتعديل حسابه.")
                        a, b = st.columns(2)
                        account_email = a.text_input("البريد الإلكتروني", key="access_email")
                        account_name = b.text_input("الاسم", key="access_name")
                        a, b = st.columns(2)
                        account_role = a.selectbox("الصلاحية", ["staff", "admin"],
                                                   format_func=lambda value: "موظف" if value == "staff" else "مسؤول",
                                                   key="access_role")
                        account_active = b.checkbox("الحساب نشط", value=True, key="access_active")
                        save_account = st.form_submit_button("حفظ حساب الفريق", type="primary", width="stretch")
                    if save_account:
                        service.save_user({"email": account_email, "display_name": account_name,
                                           "role": account_role, "active": account_active}, actor_email)
                        st.session_state["flash_message"] = "تم حفظ حساب الفريق وصلاحيته."
                        st.rerun()
                except Exception as exc:
                    show_error(exc)
        if settings.get("google_form_url"):
            st.caption("رابط الفيدباك المحفوظ · استخدم زر النسخ داخل المربع")
            st.code(settings["google_form_url"], language=None)
            st.link_button("فتح الاستبيان", settings["google_form_url"])
    with export_tab:
        st.subheader("نسخة Excel التشغيلية")
        st.write("يشمل التصدير كل الطلبات والمخزون والفيدباك والإعدادات. تُحفظ نسخة احتياطية عند كل مزامنة ناجحة.")
        sync = service.sync_status()
        if sync.get("pending"):
            st.warning("المزامنة قيد الانتظار. أغلق ملف Excel ثم اضغط على تصدير الآن. بياناتك محفوظة داخل التطبيق.")
        elif sync.get("last_success"):
            st.success("ملف Excel متزامن مع آخر التعديلات.")
        st.caption(f'آخر مزامنة ناجحة: {date_label(sync.get("last_success"), True)}')
        if st.button("تصدير Excel الآن", type="primary", width="stretch", key="export_excel_now"):
            with st.spinner("جارٍ تجهيز ملف Excel…"):
                success = service.sync_excel()
            if success:
                st.session_state["flash_message"] = "تم تصدير كل البيانات ومزامنة Excel بنجاح."
                st.rerun()
            else:
                st.warning("تعذّر تحديث ملف Excel. أغلق الملف إذا كان مفتوحًا، ثم أعد المحاولة. بياناتك محفوظة.")
        workbook_path = Path(getattr(service, "workbook_path", None) or os.getenv("EXCEL_WORKBOOK_PATH", "EasyOats_Order_Tracker.xlsx"))
        if workbook_path.is_file():
            st.download_button("تنزيل آخر نسخة Excel", data=workbook_path.read_bytes(), file_name="EasyOats_Order_Tracker.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_workbook", width="stretch")
            if sync.get("pending"):
                st.caption("النسخة المتاحة للتنزيل هي آخر نسخة ناجحة؛ قد لا تشمل التعديلات التي تنتظر المزامنة.")
        else:
            st.info("اضغط تصدير Excel الآن لإنشاء النسخة الأولى.")

        st.divider()
        st.subheader("استيراد تعديلات الطلبات من Excel")
        st.write(
            "نزّل أحدث نسخة، عدّل الطلبات الموجودة داخل ورقة «الطلبات»، ثم ارفع الملف هنا. "
            "سيعرض التطبيق كل تغيير قبل حفظه."
        )
        st.caption(
            "لا تغيّر أرقام الطلبات أو أسماء الأوراق والأعمدة. الحقول المحسوبة مثل الإجمالي والمتبقي "
            "وحالة الدفع يعيد التطبيق حسابها تلقائياً."
        )
        if not is_admin:
            st.info("استيراد تعديلات Excel متاح لمسؤول النظام فقط.")
        else:
            generation = st.session_state.get("excel_import_generation", 0)
            uploaded = st.file_uploader(
                "ارفع ملف EasyOats بصيغة xlsx",
                type="xlsx",
                max_upload_size=10,
                key=f"excel_import_file_{generation}",
                help="استخدم نسخة نُزّلت حديثاً من هذه الصفحة.",
            )
            if uploaded is not None:
                workbook_bytes = uploaded.getvalue()
                digest = hashlib.sha256(workbook_bytes).hexdigest()
                if st.button("فحص التعديلات", key="preview_excel_import", width="stretch"):
                    try:
                        with st.spinner("جارٍ فحص الملف ومقارنته بقاعدة البيانات…"):
                            plan = service.preview_excel_order_import(workbook_bytes)
                        st.session_state["excel_import_preview"] = {"digest": digest, "plan": plan}
                    except Exception as exc:
                        st.session_state.pop("excel_import_preview", None)
                        show_error(exc)
                preview = st.session_state.get("excel_import_preview")
                if preview and preview.get("digest") == digest:
                    plan = preview["plan"]
                    if plan["change_count"]:
                        st.success(
                            f'تم العثور على {plan["change_count"]} تعديل في '
                            f'{plan["changed_orders"]} طلب.'
                        )
                        st.dataframe(pd.DataFrame([{
                            "رقم الطلب": item["order_id"],
                            "الحقل": item["label"],
                            "القيمة الحالية": item["old_value"],
                            "القيمة الجديدة": item["new_value"],
                        } for item in plan["changes"]]), hide_index=True, width="stretch")
                        reason = st.text_area(
                            "سبب الاستيراد *",
                            placeholder="مثال: تحديث حالات التوصيل والتحصيل من ملف متابعة الفريق",
                            key="excel_import_reason",
                        )
                        confirmed = st.checkbox(
                            "راجعت التعديلات وأؤكد تطبيقها على قاعدة البيانات",
                            key="excel_import_confirmed",
                        )
                        if st.button(
                            "تطبيق التعديلات على التطبيق",
                            type="primary",
                            width="stretch",
                            disabled=not confirmed,
                            key="apply_excel_import",
                        ):
                            if not reason.strip():
                                st.error("اكتب سبب الاستيراد قبل تطبيق التعديلات.")
                            else:
                                try:
                                    with st.spinner("جارٍ حفظ التعديلات وإعادة إنشاء نسخة Excel…"):
                                        result = service.import_excel_order_updates(
                                            workbook_bytes,
                                            plan["revision"],
                                            user=user_name(),
                                            actor_email=st.session_state.get("auth_email") if hosted else None,
                                            is_admin=is_admin,
                                            import_reason=reason,
                                        )
                                    st.session_state.pop("excel_import_preview", None)
                                    st.session_state["excel_import_generation"] = generation + 1
                                    after_save(
                                        f'تم استيراد {result["change_count"]} تعديل في '
                                        f'{result["changed_orders"]} طلب.',
                                        service,
                                    )
                                    st.rerun()
                                except Exception as exc:
                                    show_error(exc)
                    else:
                        st.info("الملف مطابق لبيانات التطبيق ولا يحتوي تعديلات قابلة للاستيراد.")
    with audit_tab:
        st.subheader("كل تعديل له سجل")
        history = audit_rows(service.audit_history())
        if history:
            st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
        else:
            empty("لا توجد تعديلات حتى الآن", "تظهر هنا عمليات الحفظ والتحديث واسم المستخدم المسؤول عنها.")


PAGE_RENDERERS = {
    "لوحة المتابعة": dashboard,
    "طلب جديد": new_order,
    "البحث عن عميل": customer_search,
    "تحديث الطلب": update_order,
    "المخزون": inventory_page,
    "الفيدباك": feedback_page,
    "الإعدادات والتصدير": settings_page,
}

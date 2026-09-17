"""EasyOats Order Manager — local Arabic team workspace."""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

st.set_page_config(
    page_title="EasyOats | إدارة الطلبات", page_icon="🌾", layout="wide",
    initial_sidebar_state="expanded", menu_items={"About": "EasyOats · إدارة الطلبات والمخزون وخدمة العملاء"},
)

from services.order_service import AppService
from pages.ui import date_label, safe, show_error
from pages.views import PAGE_RENDERERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@st.cache_resource
def get_service() -> AppService:
    return AppService()


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _claim(name: str, default: str = "") -> str:
    value = getattr(st.user, name, None)
    if value is None:
        try:
            value = st.user.get(name, default)
        except (AttributeError, TypeError):
            value = default
    return str(value or default)


def require_identity(service: AppService) -> dict | None:
    """Gate hosted mode with OIDC and resolve the verified account's role."""
    if not _enabled("AUTH_REQUIRED"):
        st.session_state["auth_mode"] = False
        st.session_state.setdefault("auth_role", "local")
        return None
    st.session_state["auth_mode"] = True
    if not st.user.is_logged_in:
        st.title("تسجيل الدخول إلى EasyOats")
        st.write("استخدم حساب العمل المصرح به للوصول إلى الطلبات وبيانات العملاء.")
        if st.button("تسجيل الدخول", type="primary", key="oidc_login"):
            st.login()
        st.stop()
    email = _claim("email").casefold()
    name = _claim("name", email)
    try:
        access = service.authenticate_user(email, name)
    except Exception as exc:
        show_error(exc)
        st.stop()
    if access is None:
        st.error("هذا الحساب غير مصرح له باستخدام التطبيق. اطلب من مسؤول EasyOats إضافته.")
        st.caption(email)
        if st.button("تسجيل الخروج", key="unauthorized_logout"):
            st.logout()
        st.stop()
    st.session_state["auth_email"] = access["email"]
    st.session_state["auth_role"] = access["role"]
    st.session_state["current_user"] = f'{access["display_name"]} ({access["email"]})'
    return access


def render() -> None:
    st.markdown(f"<style>{(ROOT / 'assets' / 'style.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    try:
        service = get_service()
        access = require_identity(service)
        settings = service.get_settings()
    except Exception as exc:
        show_error(exc)
        st.stop()

    if "next_page" in st.session_state:
        st.session_state["navigation"] = st.session_state.pop("next_page")

    with st.sidebar:
        logo = ROOT / "logo.png"
        if logo.exists():
            encoded = base64.b64encode(logo.read_bytes()).decode("ascii")
            st.markdown(f'<img class="brand-image" alt="EasyOats" src="data:image/png;base64,{encoded}">', unsafe_allow_html=True)
        else:
            st.markdown("## EasyOats 🌾")
        st.markdown('<div class="brand-caption">كل طلب، خطوة أقرب لعميل سعيد</div>', unsafe_allow_html=True)
        st.markdown('<div class="side-label">مساحة العمل</div>', unsafe_allow_html=True)
        page = st.radio("انتقل إلى", list(PAGE_RENDERERS), key="navigation", label_visibility="collapsed")
        st.divider()
        if access:
            role_label = "مسؤول" if access["role"] == "admin" else "موظف"
            st.markdown(f'**{safe(access["display_name"])}**')
            st.caption(f'{safe(access["email"])} · {role_label}')
            if st.button("تسجيل الخروج", key="oidc_logout", width="stretch"):
                st.logout()
        else:
            names = settings.get("user_names", ["فريق EasyOats"])
            if isinstance(names, str):
                names = [v.strip() for v in names.split(",") if v.strip()]
            names = list(names) or ["فريق EasyOats"]
            current = st.session_state.get("current_user", settings.get("current_user", names[0]))
            if current not in names:
                names.append(current)
            st.selectbox("المستخدم الحالي", names, index=names.index(current), key="current_user")
        try:
            sync = service.sync_status()
            pending = sync.get("pending", False)
            state_class = "sync-state pending" if pending else "sync-state"
            state_text = "◷ مزامنة Excel قيد الانتظار" if pending else "✓ ملف Excel متزامن"
            st.markdown(f'<div class="{state_class}">{state_text}</div>', unsafe_allow_html=True)
            if sync.get("last_success"):
                st.markdown(f'<div class="sync-time">آخر مزامنة: {safe(date_label(sync["last_success"], True))}</div>', unsafe_allow_html=True)
            else:
                st.caption("يُحدَّث ملف Excel تلقائيًا بعد حفظ البيانات.")
            if pending:
                st.caption("بياناتك محفوظة. أغلق ملف Excel ثم أعد المزامنة.")
                if st.button("إعادة مزامنة Excel", key="retry_excel_sync", width="stretch"):
                    with st.spinner("جارٍ تحديث الملف…"):
                        succeeded = service.sync_excel()
                    if succeeded:
                        st.session_state["flash_message"] = "اكتملت مزامنة Excel بنجاح."
                        st.rerun()
                    else:
                        st.warning("لم تكتمل المزامنة. تأكد من إغلاق الملف ومن توفر مساحة للحفظ ثم حاول مجددًا.")
        except Exception as exc:
            show_error(exc)
        st.markdown('<div class="side-footer">صُنع لأيام عمل أبسط.<br>EasyOats · إدارة الطلبات</div>', unsafe_allow_html=True)

    try:
        PAGE_RENDERERS[page](service)
    except Exception as exc:
        show_error(exc)


# Explicit navigation keeps helper modules in pages/ from becoming public pages.
st.navigation([st.Page(render, title="إدارة طلبات EasyOats", default=True)], position="hidden").run()

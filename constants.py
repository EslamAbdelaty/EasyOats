"""Shared Arabic labels and stable domain values."""

SOURCES = ["واتساب", "الاستبيان", "إنستجرام", "فيسبوك", "ترشيح", "شركة", "بازار/فعالية", "أخرى"]
ORDER_TYPES = ["سعر عادي", "عرض 2 بـ120", "عينة", "طلب شركة", "أخرى"]
ORDER_STATUSES = ["جديد", "مؤكد", "جاري التجهيز", "جاهز", "خرج للتوصيل", "تم التوصيل", "ملغي", "مرتجع"]
ACTIVE_STATUSES = tuple(ORDER_STATUSES[:5])
PAYMENT_METHODS = ["كاش عند الاستلام", "إنستاباي", "تحويل بنكي", "كاش", "مجاني/عينة"]
FEEDBACK_CONSENTS = ["موافق", "غير موافق", "اسأله لاحقًا"]
FEEDBACK_STATUSES = ["لم يُطلب", "تم إرسال الرابط", "تم الاستلام", "رفض", "يحتاج متابعة"]
BUY_AGAIN_OPTIONS = ["", "نعم", "ممكن", "لا"]
FEEDBACK_SOURCES = ["واتساب", "Google Form"]
FOLLOWUP_STATUSES = ["لا تحتاج", "مفتوحة", "جاري الحل", "تم الحل"]
PRODUCTS = {"honey": "عسل ولبن", "date": "دبس تمر ولبن"}
DEFAULT_SETTINGS = {
    "retail_price": 75.0,
    "offer_price": 120.0,
    "honey_unit_cost": 44.0,
    "date_unit_cost": 44.0,
    "low_stock_threshold": 50,
    "google_form_url": "",
    "current_user": "فريق EasyOats",
    "user_names": ["فريق EasyOats"],
}

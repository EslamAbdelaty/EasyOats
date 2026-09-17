"""Atomic, template-preserving Excel report synchronization.

SQLite owns the records. The workbook retains its original dashboard, layout,
tables and charts; calculated columns remain formulas with fresh OOXML caches.
The snapshot callable is deliberately evaluated *after* acquiring the process
lock so an older concurrent export can never replace a newer database state.
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import os
import logging
import re
import shutil
import tempfile
from typing import Callable
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from filelock import FileLock, Timeout
from openpyxl import load_workbook
from openpyxl.chart.data_source import NumData, NumVal, StrData, StrVal
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from openpyxl.utils.datetime import to_excel
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange
from openpyxl.worksheet.table import TableColumn
from openpyxl.workbook.properties import CalcProperties


ORDER_HEADERS = {
    "order_id": "رقم الطلب", "order_datetime": "تاريخ الطلب", "source": "مصدر الطلب",
    "customer_name": "اسم العميل", "phone_original": "رقم الموبايل", "area": "المنطقة",
    "address": "العنوان / لينك اللوكيشن", "order_type": "نوع الطلب",
    "honey_qty": "كمية عسل ولبن", "date_qty": "كمية دبس تمر ولبن",
    "total_units": "إجمالي الوحدات", "unit_price": "سعر الوحدة المطبق",
    "product_subtotal": "إجمالي المنتجات", "discount": "الخصم",
    "delivery_fee": "مصاريف الشحن المحصلة", "delivery_cost": "تكلفة الشحن الفعلية",
    "total_due": "إجمالي المطلوب", "product_cost": "تكلفة المنتجات التقديرية",
    "contribution_margin": "هامش المساهمة المقدر", "payment_method": "طريقة الدفع",
    "payment_status": "حالة الدفع", "amount_collected": "المبلغ المحصل",
    "outstanding_balance": "المتبقي", "status": "حالة الطلب",
    "courier": "مسؤول الطلب / الكابتن", "expected_delivery_date": "تاريخ التوصيل المتوقع",
    "actual_delivery_date": "تاريخ التوصيل الفعلي", "tracking_number": "رقم الشحنة / التتبع",
    "delivery_result": "نتيجة التوصيل", "feedback_consent": "موافقة الفيدباك",
    "feedback_request_date": "تاريخ طلب الفيدباك", "feedback_status": "حالة الفيدباك",
    "customer_rating": "التقييم العام", "buy_again": "هل سيشتري مرة أخرى؟",
    "notes": "ملاحظات", "next_action": "الإجراء التالي",
    "location_url": "رابط الموقع", "phone_normalized": "رقم الموبايل الموحد",
    "returned_sellable": "المرتجع صالح للبيع", "refund_amount": "المبلغ المسترد",
    "frozen_unit_price": "سعر الوحدة وقت الطلب", "honey_unit_cost": "تكلفة العسل وقت الطلب",
    "date_unit_cost": "تكلفة الدبس وقت الطلب", "stored_customer_rating": "التقييم المسجل بالطلب",
    "stored_buy_again": "إعادة الشراء المسجلة بالطلب",
}
FEEDBACK_HEADERS = {
    "id": "رقم الفيدباك", "response_date": "تاريخ الرد", "order_id": "رقم الطلب",
    "customer_name": "اسم العميل", "source": "المصدر", "consent_confirmed": "الموافقة مؤكدة؟",
    "overall_rating": "التقييم العام 1–5", "taste_rating": "الطعم 1–5",
    "portion_rating": "حجم الوجبة 1–5", "preparation_rating": "سهولة التحضير 1–5",
    "value_rating": "القيمة مقابل السعر 1–5", "buy_again": "هل سيشتري مرة أخرى؟",
    "recommendation_score": "احتمال الترشيح 0–10", "preferred_flavor": "النكهة المفضلة",
    "comments": "التعليق", "issue_reported": "توجد مشكلة؟", "followup_owner": "مسؤول المتابعة",
    "followup_status": "حالة المتابعة", "resolution_notes": "ملاحظات الحل",
}
INVENTORY_HEADERS = {
    "sku": "SKU", "name": "المنتج", "opening_stock": "رصيد أول المدة",
    "added_stock": "إضافات المخزون", "reserved": "محجوز لطلبات نشطة",
    "delivered": "تم توصيله", "available": "المتاح للبيع", "physical_count": "الجرد الفعلي",
    "variance": "فرق الجرد", "reorder_point": "حد إعادة الطلب", "low_stock": "حالة المخزون",
    "unit_cost": "تكلفة الوحدة التقديرية", "value": "قيمة المخزون المتاح",
    "returned_unsellable": "مرتجع غير صالح للبيع",
}
CALCULATED = {
    "total_units", "unit_price", "product_subtotal", "total_due", "product_cost",
    "contribution_margin", "payment_status", "outstanding_balance", "customer_rating",
    "buy_again", "next_action",
}
STATUSES = ("جديد", "مؤكد", "جاري التجهيز", "جاهز", "خرج للتوصيل", "تم التوصيل", "ملغي", "مرتجع")
DEMO_IDS = {"مثال-احذفه", "FB-EXAMPLE"}


@dataclass(frozen=True)
class SyncResult:
    success: bool
    message: str
    timestamp: datetime | None = None


def _normalize(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())


def _set(cell, value):
    """All externally supplied strings are literal text, never Excel formulas."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, datetime) and value.tzinfo:
        value = value.replace(tzinfo=None)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
        if value.lstrip().startswith(("=", "+", "-", "@")):
            cell.quotePrefix = True


def _date(value):
    if not value or isinstance(value, (date, datetime)):
        return value or None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return value


def _headers(ws, mapping, required):
    for row in ws.iter_rows(min_row=1, max_row=min(25, ws.max_row)):
        names = {_normalize(c.value): c.column for c in row if c.value is not None}
        if all(_normalize(mapping[k]) in names for k in required):
            header_row = row[0].row
            break
    else:
        raise ValueError("Missing template headers")
    columns = {}
    for key, title in mapping.items():
        normalized = _normalize(title)
        if normalized in names:
            columns[key] = names[normalized]
        else:
            col = ws.max_column + 1
            columns[key] = col
            names[normalized] = col
            target = ws.cell(header_row, col)
            source = ws.cell(header_row, col - 1)
            target._style = copy(source._style)
            _set(target, title)
            ws.column_dimensions[get_column_letter(col)].width = max(20, len(title) + 2)
            for r in range(header_row + 1, ws.max_row + 1):
                ws.cell(r, col)._style = copy(ws.cell(r, col - 1)._style)
    return header_row, columns


def _expand_sheet(ws, header, end):
    old_end = ws.max_row
    template_row = header + 2 if old_end > header + 1 else header + 1
    for r in range(old_end + 1, end + 1):
        ws.row_dimensions[r] = copy(ws.row_dimensions[template_row])
        ws.row_dimensions[r].index = r
        for col in range(1, ws.max_column + 1):
            src, dst = ws.cell(template_row, col), ws.cell(r, col)
            dst._style = copy(src._style)
            dst.alignment = copy(src.alignment)
            dst.protection = copy(src.protection)
    for dv in ws.data_validations.dataValidation:
        ranges = []
        for rng in dv.sqref.ranges:
            extended = copy(rng)
            if extended.max_row >= old_end and extended.min_row > header:
                extended.max_row = end
            ranges.append(extended)
        dv.sqref = MultiCellRange(ranges)
    rules = list(ws.conditional_formatting._cf_rules.items())
    ws.conditional_formatting._cf_rules.clear()
    for conditional, items in rules:
        ranges = []
        for rng in conditional.sqref.ranges:
            extended = copy(rng)
            if extended.max_row >= old_end and extended.min_row > header:
                extended.max_row = end
            ranges.append(extended)
        conditional.sqref = MultiCellRange(ranges)
        ws.conditional_formatting._cf_rules[conditional] = items
    for table in ws.tables.values():
        min_col, min_row, _, _ = range_boundaries(table.ref)
        table.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(ws.max_column)}{end}"
        if table.autoFilter:
            table.autoFilter.ref = table.ref
        existing = {t.name: t for t in table.tableColumns}
        table.tableColumns = []
        for index, col in enumerate(range(min_col, ws.max_column + 1), 1):
            name = str(ws.cell(header, col).value)
            entry = copy(existing.get(name)) if name in existing else TableColumn(id=index, name=name)
            entry.id, entry.name = index, name
            table.tableColumns.append(entry)


def _literal_formula(value):
    return '"' + str(value or "").replace('"', '""') + '"'


def _next_action_expression(c):
    """Mirror the application's simultaneous lifecycle actions in Excel."""
    refund = f'{c("amount_collected")}>{c("refund_amount")}'
    unsellable = f'AND({c("status")}="مرتجع",{c("returned_sellable")}<>"نعم")'
    returned = (f'IF(OR({refund},{unsellable}),'
        f'IF({refund},"مراجعة المبلغ المطلوب رده للعميل","")&'
        f'IF(AND({refund},{unsellable})," · ","")&'
        f'IF({unsellable},"فحص المرتجع؛ لم يُعد إلى المخزون القابل للبيع",""),"لا يوجد إجراء مطلوب")')
    unpaid = f'{c("outstanding_balance")}>0'
    send = f'AND({c("feedback_consent")}="موافق",{c("feedback_status")}="لم يُطلب")'
    followup = f'OR({c("feedback_status")}="تم إرسال الرابط",{c("feedback_status")}="يحتاج متابعة")'
    delivered = (f'IF(OR({unpaid},{send},{followup}),'
        f'IF({unpaid},"تحصيل المبلغ المتبقي","")&'
        f'IF(AND({unpaid},OR({send},{followup}))," · ","")&'
        f'IF({send},"إرسال رابط الفيدباك",IF({followup},"متابعة رد الفيدباك","")),"اكتمل الطلب")')
    expected = c("expected_delivery_date")
    active = (f'IF(AND({expected}<>"",{expected}<TODAY()),"متابعة تأخر التوصيل · ","")&'
        f'IF({c("status")}="جديد","تأكيد الطلب",IF({c("status")}="مؤكد","بدء التجهيز",'
        f'IF({c("status")}="جاري التجهيز","إنهاء التجهيز",IF({c("status")}="جاهز","تسليم الطلب للمندوب","تأكيد التسليم والتحصيل"))))')
    return f'IF(OR({c("status")}="ملغي",{c("status")}="مرتجع"),{returned},IF({c("status")}="تم التوصيل",{delivered},{active}))'


def _patch_caches(path, workbook, caches):
    """openpyxl writes formulas but cannot write their results; populate both.

    This makes downloads truthful in data_only readers and file previews even
    before Excel opens/recalculates the workbook. No Excel installation required.
    """
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    tag = "{" + ns["m"] + "}"
    stream = BytesIO()
    with ZipFile(path, "r") as incoming, ZipFile(stream, "w") as outgoing:
        for item in incoming.infolist():
            data = incoming.read(item.filename)
            match = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", item.filename)
            if match:
                index = int(match.group(1)) - 1
                cache = caches.get(workbook.worksheets[index].title, {})
                root = ET.fromstring(data)
                for cell in root.findall(".//m:sheetData/m:row/m:c", ns):
                    coordinate = cell.attrib.get("r")
                    if coordinate not in cache or cell.find("m:f", ns) is None:
                        continue
                    value = cache[coordinate]
                    old = cell.find("m:v", ns)
                    if old is None:
                        old = ET.SubElement(cell, tag + "v")
                    if value is None or value == "":
                        cell.set("t", "str")
                        old.text = ""
                    elif isinstance(value, bool):
                        cell.set("t", "b")
                        old.text = "1" if value else "0"
                    elif isinstance(value, (int, float, Decimal, date, datetime)):
                        cell.attrib.pop("t", None)
                        old.text = str(to_excel(value, workbook.epoch) if isinstance(value, (date, datetime)) else value)
                    else:
                        cell.set("t", "str")
                        old.text = str(value)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            outgoing.writestr(item, data)
    with open(path, "wb") as handle:
        handle.write(stream.getvalue())
        handle.flush()
        os.fsync(handle.fileno())


class ExcelService:
    def __init__(self, workbook_path, backup_dir):
        self.workbook_path = Path(workbook_path).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        default_template = Path(__file__).resolve().parents[1] / "templates" / "EasyOats_Order_Tracker.xlsx"
        self.template_path = Path(os.getenv("TEMPLATE_PATH") or default_template).resolve()
        self.lock_path = str(self.workbook_path) + ".sync.lock"

    def _ensure_workbook(self):
        if self.workbook_path.exists():
            return
        if not self.template_path.exists():
            raise FileNotFoundError(self.template_path)
        fd, name = tempfile.mkstemp(prefix=".EasyOats_template_", suffix=".tmp.xlsx",
                                    dir=self.workbook_path.parent)
        os.close(fd)
        staging = Path(name)
        try:
            with self.template_path.open("rb") as source, staging.open("wb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(staging, self.workbook_path)
        finally:
            staging.unlink(missing_ok=True)

    def sync(self, snapshot: dict | Callable[[], dict]) -> SyncResult:
        temporary = None
        workbook = None
        try:
            self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(self.lock_path, timeout=5):
                self._ensure_workbook()
                data = snapshot() if callable(snapshot) else snapshot
                workbook = load_workbook(self.workbook_path)
                if not self._safe_to_replace(workbook, data):
                    return SyncResult(False, "ملف Excel يحتوي طلبات غير موجودة بقاعدة البيانات. تم الاحتفاظ بالملف؛ راجع نسخة قاعدة البيانات قبل المزامنة.")
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                original = self.backup_dir / "original_template.xlsx"
                if not original.exists():
                    fd, original_name = tempfile.mkstemp(prefix=".original_template_", suffix=".tmp.xlsx",
                                                         dir=self.backup_dir)
                    os.close(fd)
                    original_staging = Path(original_name)
                    try:
                        with original_staging.open("wb") as output, self.workbook_path.open("rb") as source:
                            shutil.copyfileobj(source, output)
                            output.flush()
                            os.fsync(output.fileno())
                        # The export lock serializes this existence check and replace.
                        if not original.exists():
                            os.rename(original_staging, original)
                    finally:
                        original_staging.unlink(missing_ok=True)
                stamp = datetime.now(timezone.utc)
                backup = self.backup_dir / f"EasyOats_{stamp:%Y%m%d_%H%M%S_%f}.xlsx"
                shutil.copy2(self.workbook_path, backup)
                caches = self._render(workbook, data)
                workbook.calculation = CalcProperties(calcId=191029, fullCalcOnLoad=True, forceFullCalc=True, calcMode="auto")
                fd, name = tempfile.mkstemp(prefix=".EasyOats_", suffix=".tmp.xlsx", dir=self.workbook_path.parent)
                os.close(fd)
                temporary = Path(name)
                workbook.save(temporary)
                _patch_caches(temporary, workbook, caches)
                os.replace(temporary, self.workbook_path)
                return SyncResult(True, "تمت مزامنة Excel بنجاح.", datetime.now(timezone.utc))
        except (PermissionError, Timeout):
            return SyncResult(False, "تم حفظ البيانات. أغلق ملف Excel ثم اضغط إعادة محاولة المزامنة؛ مزامنة Excel معلّقة.")
        except FileNotFoundError:
            return SyncResult(False, "تعذرت المزامنة: ضع ملف EasyOats_Order_Tracker.xlsx في مجلد التطبيق ثم أعد المحاولة.")
        except Exception:
            logging.getLogger(__name__).exception("Excel synchronization failed")
            return SyncResult(False, "تم حفظ البيانات بقاعدة البيانات. تعذرت مزامنة Excel؛ أغلق الملف وأعد المحاولة.")
        finally:
            if workbook is not None:
                workbook.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _safe_to_replace(workbook, data):
        for title, headers, key in (("الطلبات", ORDER_HEADERS, "orders"), ("الفيدباك", FEEDBACK_HEADERS, "feedback")):
            ws = workbook[title]
            row, cols = _headers(ws, headers, ("order_id", "customer_name"))
            incoming = {str(item.get("order_id", "")) for item in data.get(key, [])}
            for r in range(row + 1, ws.max_row + 1):
                value = ws.cell(r, cols["order_id"]).value
                if value and str(value) not in DEMO_IDS and str(value) not in incoming:
                    return False
        return True

    def _render(self, workbook, data):
        orders = data.get("orders", [])
        # LOOKUP returns the last matching record, so chronological export makes
        # the displayed order rating agree with its latest response on recalc.
        feedback = sorted(data.get("feedback", []), key=lambda item: (
            str(item.get("response_date") or ""),
            int(item.get("id") or 0) if str(item.get("id") or "0").isdigit() else 0,
        ))
        inventory, settings = data.get("inventory", []), data.get("settings", {})
        ws, fs, ins = workbook["الطلبات"], workbook["الفيدباك"], workbook["المخزون"]
        oh, oc = _headers(ws, ORDER_HEADERS, ("order_id", "customer_name"))
        fh, fc = _headers(fs, FEEDBACK_HEADERS, ("order_id", "customer_name"))
        ih, ic = _headers(ins, INVENTORY_HEADERS, ("sku", "name"))
        old_ends = {ws.title: ws.max_row, fs.title: fs.max_row}
        oe, fe = max(ws.max_row, oh + len(orders)), max(fs.max_row, fh + len(feedback))
        _expand_sheet(ws, oh, oe)
        _expand_sheet(fs, fh, fe)
        _expand_sheet(ins, ih, max(ins.max_row, ih + len(inventory)))
        caches = {s.title: {} for s in workbook}
        def formula(sheet, row, col, text, value):
            cell = sheet.cell(row, col, text)
            caches[sheet.title][cell.coordinate] = value
        def order_range(key):
            letter = get_column_letter(oc[key])
            return f"'الطلبات'!${letter}${oh + 1}:${letter}${oe}"
        def feedback_range(key):
            letter = get_column_letter(fc[key])
            return f"'الفيدباك'!${letter}${fh + 1}:${letter}${fe}"
        for row in range(oh + 1, oe + 1):
            record = orders[row - oh - 1] if row - oh - 1 < len(orders) else {}
            c = lambda key: f"{get_column_letter(oc[key])}{row}"
            for key, col in oc.items():
                if key in CALCULATED:
                    continue
                value = record.get(key)
                if key == "frozen_unit_price":
                    value = record.get("unit_price")
                elif key == "stored_customer_rating":
                    value = record.get("customer_rating")
                elif key == "stored_buy_again":
                    value = record.get("buy_again")
                elif key == "returned_sellable" and record:
                    value = "نعم" if value else "لا"
                if key.endswith("_date") or key == "order_datetime":
                    value = _date(value)
                cell = ws.cell(row, col)
                _set(cell, value)
                if key.startswith("phone"):
                    cell.number_format = "@"
                elif isinstance(value, (date, datetime)):
                    cell.number_format = "yyyy-mm-dd hh:mm" if key == "order_datetime" else "yyyy-mm-dd"
                elif isinstance(value, (int, float, Decimal)):
                    cell.number_format = "0" if key.endswith("qty") or "rating" in key else "0.00"
            expressions = {
                "total_units": f'{c("honey_qty")}+{c("date_qty")}',
                "unit_price": c("frozen_unit_price"),
                "product_subtotal": f'{c("total_units")}*{c("unit_price")}',
                "total_due": f'{c("product_subtotal")}-{c("discount")}+{c("delivery_fee")}',
                "product_cost": f'{c("honey_qty")}*{c("honey_unit_cost")}+{c("date_qty")}*{c("date_unit_cost")}',
                "contribution_margin": f'{c("total_due")}-{c("product_cost")}-{c("delivery_cost")}',
                "outstanding_balance": f'{c("total_due")}-{c("amount_collected")}',
                "payment_status": f'IF(AND({c("refund_amount")}>0,{c("refund_amount")}>={c("amount_collected")}),"مسترد",IF({c("outstanding_balance")}<=0,"مدفوع",IF({c("amount_collected")}>0,"مدفوع جزئيًا","غير مدفوع")))',
                "customer_rating": f'IF({c("stored_customer_rating")}="","",{c("stored_customer_rating")})',
                "buy_again": f'IF({c("stored_buy_again")}="","",{c("stored_buy_again")})',
                "next_action": _next_action_expression(c),
            }
            for key, expression in expressions.items():
                value = record.get(key, "")
                formula(ws, row, oc[key], f'=IF({c("order_id")}="","",{expression})', value)
        by_id = {record["order_id"]: record for record in orders}
        for row in range(fh + 1, fe + 1):
            record = feedback[row - fh - 1] if row - fh - 1 < len(feedback) else {}
            for key, col in fc.items():
                if key == "customer_name":
                    ref = f'{get_column_letter(fc["order_id"])}{row}'
                    expression = f'=IF({ref}="","",IFERROR(INDEX({order_range("customer_name")},MATCH({ref},{order_range("order_id")},0)),""))'
                    formula(fs, row, col, expression, by_id.get(record.get("order_id"), {}).get("customer_name", ""))
                else:
                    value = record.get(key)
                    if key == "response_date":
                        value = _date(value)
                    elif key in {"consent_confirmed", "issue_reported"} and isinstance(value, bool):
                        value = "نعم" if value else "لا"
                    _set(fs.cell(row, col), value)
        for index, record in enumerate(inventory):
            row = ih + index + 1
            c = lambda key: f"{get_column_letter(ic[key])}{row}"
            quantity = "honey_qty" if "HONEY" in str(record.get("sku", "")).upper() else "date_qty"
            base = f'{order_range(quantity)},{order_range("order_id")},"<>",{order_range("order_id")},"<>مثال-احذفه"'
            expressions = {
                "reserved": f'SUMIFS({base},{order_range("status")},"<>تم التوصيل",{order_range("status")},"<>ملغي",{order_range("status")},"<>مرتجع")',
                "delivered": f'SUMIFS({base},{order_range("status")},"تم التوصيل")',
                "returned_unsellable": f'SUMIFS({base},{order_range("status")},"مرتجع",{order_range("returned_sellable")},"<>نعم")',
                "available": f'{c("opening_stock")}+{c("added_stock")}-{c("reserved")}-{c("delivered")}-{c("returned_unsellable")}',
                "variance": f'IF({c("physical_count")}="","",{c("physical_count")}-({c("opening_stock")}+{c("added_stock")}-{c("delivered")}-{c("returned_unsellable")}))',
                "low_stock": f'IF({c("available")}<={c("reorder_point")},"اطلب تصنيع جديد","مطمئن")',
                "value": f'{c("available")}*{c("unit_cost")}',
            }
            for key, col in ic.items():
                value = record.get(key)
                if key in expressions:
                    if key == "low_stock":
                        value = "اطلب تصنيع جديد" if record.get("low_stock") else "مطمئن"
                    formula(ins, row, col, "=" + expressions[key], value)
                else:
                    _set(ins.cell(row, col), value)
        self._settings(workbook["القوائم والإعدادات"], settings)
        self._dashboard(workbook["لوحة المتابعة"], caches, orders, feedback, inventory, settings, order_range, feedback_range, ih, ic)
        # Preserve any other template formulas while extending bounded ranges.
        for sheet in workbook:
            for row in sheet:
                for cell in row:
                    if cell.data_type != "f":
                        continue
                    for title, old_end in old_ends.items():
                        new_end = oe if title == ws.title else fe
                        if new_end != old_end:
                            cell.value = re.sub(r"('" + re.escape(title) + r"'!\$?[A-Z]+\$?\d+:\$?[A-Z]+\$?)" + str(old_end) + r"(?!\d)", lambda m: m.group(1) + str(new_end), cell.value)
        return caches

    @staticmethod
    def _settings(ws, settings):
        labels = {
            "سعر البيع القياسي للوحدة": "retail_price", "سعر عرض وحدتين": "offer_price",
            "رابط Google Form للفيدباك": "google_form_url", "حد تنبيه المخزون الافتراضي": "low_stock_threshold",
        }
        for row in ws:
            for cell in row:
                key = labels.get(str(cell.value))
                if key is not None and key in settings:
                    _set(ws.cell(cell.row, cell.column + 1), settings[key])
        additional = {"تكلفة وحدة العسل واللبن": "honey_unit_cost", "تكلفة وحدة الدبس واللبن": "date_unit_cost", "المستخدم الحالي": "current_user", "أسماء المستخدمين": "user_names"}
        for title, key in additional.items():
            existing = next((r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == title), None)
            row = existing or ws.max_row + 1
            ws.cell(row, 1)._style = copy(ws.cell(5, 1)._style)
            ws.cell(row, 2)._style = copy(ws.cell(5, 2)._style)
            _set(ws.cell(row, 1), title)
            value = settings.get(key)
            if isinstance(value, list):
                value = "، ".join(map(str, value))
            _set(ws.cell(row, 2), value)
        # Replace the old manual-entry instructions while retaining their layout.
        _set(ws["A14"], "1) سجّل الطلبات وحدّثها من تطبيق EasyOats؛ قاعدة البيانات هي المصدر الأساسي.")
        _set(ws["A18"], "5) احفظ رابط Google Form في إعدادات التطبيق؛ يظهر هنا تلقائيًا ولا يحتاج ربطًا برمجيًا.")
        _set(ws["A21"], "8) التقرير يشمل كل الطلبات. أسعار وتكاليف الطلب محفوظة بتاريخ إنشائه ولا تتغير بتعديل الإعدادات.")

    @staticmethod
    def _dashboard(ws, caches, orders, feedback, inventory, settings, rng, frng, ih, ic):
        excluded = {"ملغي", "مرتجع"}
        live = [o for o in orders if o.get("status") not in excluded]
        active = [o for o in live if o.get("status") != "تم التوصيل"]
        delivered = [o for o in orders if o.get("status") == "تم التوصيل"]
        def total(records, key):
            return sum(float(o.get(key) or 0) for o in records)
        criteria = f'{rng("order_id")},"<>",{rng("order_id")},"<>مثال-احذفه"'
        eligible = criteria + f',{rng("status")},"<>ملغي",{rng("status")},"<>مرتجع"'
        count = lambda extra="": "=COUNTIFS(" + criteria + extra + ")"
        sum_live = lambda key: f'=SUMIFS({rng(key)},{eligible})'
        count_feedback = f'COUNTIFS({frng("order_id")},"<>",{frng("order_id")},"<>مثال-احذفه")'
        values = {
            "A5": (count(), len(orders)),
            "C5": (count(f',{rng("status")},"<>تم التوصيل",{rng("status")},"<>ملغي",{rng("status")},"<>مرتجع"'), len(active)),
            "E5": (count(f',{rng("status")},"تم التوصيل"'), len(delivered)),
            "G5": (count(f',{rng("status")},"ملغي"'), sum(o.get("status") == "ملغي" for o in orders)),
            "A8": (f'=SUMIFS({rng("total_units")},{criteria})', total(orders, "total_units")),
            "C8": (sum_live("total_due"), total(live, "total_due")),
            "E8": (f'=SUMIFS({rng("amount_collected")},{criteria})-SUMIFS({rng("refund_amount")},{criteria})', total(orders, "amount_collected") - total(orders, "refund_amount")),
            "G8": (f'=SUMIFS({rng("outstanding_balance")},{eligible},{rng("outstanding_balance")},">0")', sum(max(0, float(o.get("outstanding_balance") or 0)) for o in live)),
            "A11": (sum_live("contribution_margin"), total(live, "contribution_margin")),
            "C11": (f'=IFERROR(C8/COUNTIFS({eligible}),0)', total(live, "total_due") / len(live) if live else 0),
            "E11": (f'=IFERROR(AVERAGE({rng("customer_rating")}),0)', total([o for o in orders if o.get("customer_rating") is not None], "customer_rating") / len([o for o in orders if o.get("customer_rating") is not None]) if any(o.get("customer_rating") is not None for o in orders) else 0),
            "G11": (f'=IF(E5=0,0,SUMPRODUCT(--({rng("status")}= "تم التوصيل"),--((COUNTIF({frng("order_id")},{rng("order_id")})+({rng("feedback_status")}="تم الاستلام"))>0))/E5)', sum(o["order_id"] in {f["order_id"] for f in feedback} or o.get("feedback_status") == "تم الاستلام" for o in delivered) / len(delivered) if delivered else 0),
            "A14": (f"='المخزون'!{get_column_letter(ic['available'])}{ih + 1}", inventory[0].get("available", 0) if inventory else 0),
            "C14": (f"='المخزون'!{get_column_letter(ic['available'])}{ih + 2}", inventory[1].get("available", 0) if len(inventory) > 1 else 0),
            "E14": ("=A14+C14", total(inventory, "available")),
            "G14": ("=TODAY()", date.today()),
            "A37": ('=IF(\'القوائم والإعدادات\'!B8="","الصق رابط Google Form في القوائم والإعدادات!B8",\'القوائم والإعدادات\'!B8)', settings.get("google_form_url") or "الصق رابط Google Form في القوائم والإعدادات!B8"),
        }
        for coordinate, action in (("A19", "متابعة تأخر التوصيل"), ("C19", "تحصيل المبلغ المتبقي"), ("E19", "إرسال رابط الفيدباك")):
            values[coordinate] = (f'=COUNTIF({rng("next_action")},"*{action}*")', sum(action in str(o.get("next_action") or "") for o in orders))
        followup_count = 0
        for order in orders:
            requested = _date(order.get("feedback_request_date"))
            requested = requested.date() if isinstance(requested, datetime) else requested
            if order.get("feedback_status") == "يحتاج متابعة" or (order.get("feedback_status") == "تم إرسال الرابط" and isinstance(requested, date) and (date.today() - requested).days >= 3):
                followup_count += 1
        values["G19"] = (f'=COUNTIFS({rng("feedback_status")},"يحتاج متابعة")+COUNTIFS({rng("feedback_status")},"تم إرسال الرابط",{rng("feedback_request_date")},">0",{rng("feedback_request_date")},"<="&TODAY()-3)', followup_count)
        for row, status in enumerate(STATUSES, 23):
            values[f"B{row}"] = (count(f',{rng("status")},A{row}'), sum(o.get("status") == status for o in orders))
        for coordinate, (expression, value) in values.items():
            ws[coordinate] = expression
            caches[ws.title][coordinate] = value
        for chart in ws._charts:
            for series in chart.series:
                if series.val and series.val.numRef and "B$23:$B$30" in series.val.numRef.f:
                    series.val.numRef.numCache = NumData(formatCode="0", ptCount=8,
                        pt=[NumVal(idx=index, v=values[f"B{23 + index}"][1]) for index in range(8)])
                if series.cat and series.cat.strRef and "A$23:$A$30" in series.cat.strRef.f:
                    series.cat.strRef.strCache = StrData(ptCount=8,
                        pt=[StrVal(idx=index, v=status) for index, status in enumerate(STATUSES)])

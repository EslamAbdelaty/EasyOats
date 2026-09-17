"""Validate and normalize Egyptian mobile numbers, including Arabic numerals."""

import re


class PhoneValidationError(ValueError):
    """A friendly validation error that is safe to show in the Arabic UI."""


_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_FORMAT = re.compile(r"[\s()\-\.\u200e\u200f\u202a-\u202e]")


def phone_digits(value: str) -> str:
    """Remove common presentation characters without accepting arbitrary text."""
    if value is None:
        raise PhoneValidationError("يرجى إدخال رقم الموبايل.")
    text = str(value).translate(_DIGIT_MAP).strip()
    text = _FORMAT.sub("", text)
    if not re.fullmatch(r"\+?[0-9]+", text):
        raise PhoneValidationError("رقم الموبايل غير صحيح. استخدم أرقاماً مثل 01012345678 أو +201012345678.")
    return text


def normalize_phone(value: str) -> str:
    number = phone_digits(value)
    if number.startswith("+20"):
        number = "0" + number[3:]
    elif number.startswith("0020"):
        number = "0" + number[4:]
    elif number.startswith("20") and len(number) == 12:
        number = "0" + number[2:]
    if not re.fullmatch(r"01[0125][0-9]{8}", number):
        raise PhoneValidationError("أدخل رقم موبايل مصري صحيحاً من 11 رقماً يبدأ بـ 010 أو 011 أو 012 أو 015.")
    return number


def normalize_partial_phone(value: str) -> str:
    number = phone_digits(value)
    if number.startswith("+20"):
        number = "0" + number[3:]
    elif number.startswith("0020"):
        number = "0" + number[4:]
    elif number.startswith("20") and len(number) == 12:
        number = "0" + number[2:]
    if not number.isascii() or not number.isdigit() or not 4 <= len(number) <= 11:
        raise PhoneValidationError("للبحث الجزئي، أدخل أربعة أرقام على الأقل وحتى 11 رقماً.")
    return number


normalize_egyptian_phone = normalize_phone

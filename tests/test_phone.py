import pytest

from utils.phone import normalize_phone, normalize_partial_phone


@pytest.mark.parametrize("raw", [
    "01012345678", "+201012345678", "00201012345678", "201012345678",
    "010 1234 5678", "+20 (10) 1234-5678", "٠١٠١٢٣٤٥٦٧٨", "۰۱۰۱۲۳۴۵۶۷۸",
])
def test_normalization(raw):
    assert normalize_phone(raw) == "01012345678"


@pytest.mark.parametrize("raw", ["", "1234", "02012345678", "0101234567", "010123456789", "abc01012345678", "+101012345678"])
def test_invalid_phones(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


@pytest.mark.parametrize("raw", ["01112345678", "01212345678", "01512345678"])
def test_other_mobile_prefixes(raw):
    assert normalize_phone(raw) == raw


def test_partial_normalization():
    assert normalize_partial_phone("٥٦٧٨") == "5678"
    with pytest.raises(ValueError):
        normalize_partial_phone("678")

from datetime import date

import pytest

from services.order_service import ValidationError


def feedback_payload(order_id):
    return {
        "order_id": order_id, "response_date": date(2026, 9, 3),
        "source": "واتساب", "consent_confirmed": True,
        "overall_rating": 5, "taste_rating": 4, "portion_rating": 4,
        "preparation_rating": 5, "value_rating": 4, "buy_again": "نعم",
        "recommendation_score": 9, "preferred_flavor": "عسل ولبن",
        "comments": "سهل التحضير", "issue_reported": False,
        "followup_owner": "فريق المتابعة", "followup_status": "لا تحتاج",
        "resolution_notes": "",
    }


def test_feedback_link_and_edit(service, order_data):
    order = service.create_order(order_data)
    response = service.save_feedback(feedback_payload(order["order_id"]))
    assert service.list_feedback(order["order_id"])[0]["overall_rating"] == 5
    updated = service.get_order(order["order_id"])
    assert updated["feedback_status"] == "تم الاستلام"
    assert updated["customer_rating"] == 5
    response.update(overall_rating=4, issue_reported=True, followup_status="مفتوحة")
    service.save_feedback(response)
    assert len(service.list_feedback(order["order_id"])) == 1
    assert service.get_order(order["order_id"])["customer_rating"] == 4


@pytest.mark.parametrize("field,value", [("overall_rating", 6), ("taste_rating", 0), ("recommendation_score", 11), ("consent_confirmed", False)])
def test_feedback_validation(service, order_data, field, value):
    order = service.create_order(order_data)
    payload = feedback_payload(order["order_id"])
    payload[field] = value
    with pytest.raises(ValidationError):
        service.save_feedback(payload)
    assert service.list_feedback() == []


def test_unknown_order_feedback_rejected(service):
    with pytest.raises(ValidationError):
        service.save_feedback(feedback_payload("EO-999999"))

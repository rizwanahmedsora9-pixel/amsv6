from app.blueprints.ledgers._client_financial_ledger import _financial_bill_outstanding


def test_opening_credit_reduces_full_booking_pending_amount():
    history = [
        {"bill_no": "OPENING", "type": None, "id": None, "debit": 0, "credit": 750245},
        {"bill_no": "SB-BK-1017", "type": "Booking", "id": 18, "debit": 801200, "credit": 0},
    ]

    outstanding = _financial_bill_outstanding(history)

    assert outstanding["by_source"][("Booking", 18)] == 50955
    assert outstanding["by_reference"]["sb-bk-1017"] == 50955


def test_later_receipt_reduces_booking_balance_fifo():
    history = [
        {"bill_no": "OPENING", "type": None, "id": None, "debit": 0, "credit": 750245},
        {"bill_no": "SB-BK-1017", "type": "Booking", "id": 18, "debit": 801200, "credit": 0},
        {"bill_no": "SB-CP-0001", "type": "Payment", "id": 5, "debit": 0, "credit": 10000},
    ]

    outstanding = _financial_bill_outstanding(history)

    assert outstanding["by_source"][("Booking", 18)] == 40955

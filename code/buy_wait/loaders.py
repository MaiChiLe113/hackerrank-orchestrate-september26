"""Read only participant files, validate relationships, and preserve unknown values."""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .schemas import Dataset, FinancialEvent, FinancialProfile, ImageMetadata, Message, PaymentOption, Request, money

class DatasetError(ValueError):
    pass

def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise DatasetError(f"Missing input: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise DatasetError(f"Missing or duplicate columns: {path.name}")
        result = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in result):
        raise DatasetError(f"Malformed CSV row: {path.name}")
    return result

def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key, "").strip()
    if not value:
        raise DatasetError(f"Missing required {key}")
    return value

def _date(row: dict[str, str], key: str) -> date:
    value = _required(row, key)
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise DatasetError(f"Invalid YYYY-MM-DD date in {key}: {value}")
    return parsed

def _set(row: dict[str, str], key: str) -> frozenset[str]:
    return frozenset(v.strip() for v in row.get(key, "").split("|") if v.strip())

def _optional(row: dict[str, str], key: str) -> str | None:
    return row.get(key, "").strip() or None

def _unique(items: list[Any], attr: str, table: str) -> dict[str, Any]:
    result = {}
    for item in items:
        key = getattr(item, attr)
        if key in result:
            raise DatasetError(f"Duplicate {attr} {key} in {table}")
        result[key] = item
    return result

def load_dataset(dataset_dir: Path, requests_file: str = "requests.csv") -> Dataset:
    """The caller may choose the public sample file for diagnostics, never hidden labels."""
    if requests_file not in {"requests.csv", "sample_requests.csv"}:
        raise DatasetError("requests_file must be requests.csv or sample_requests.csv")
    requests = []
    for r in _rows(dataset_dir / requests_file):
        flag = _required(r, "allows_partial_payment").lower()
        if flag not in {"true", "false"}:
            raise DatasetError("allows_partial_payment must be true or false")
        requests.append(Request(_required(r, "request_id"), _required(r, "user_id"), _date(r, "request_date"), _required(r, "request_type"), money(_required(r, "requested_amount")), _date(r, "desired_completion_date"), flag == "true", r.get("request_text", "")))
    request_map = _unique(requests, "request_id", requests_file)
    profiles = []
    for r in _rows(dataset_dir / "financial_profiles.csv"):
        profiles.append(FinancialProfile(_required(r, "user_id"), _required(r, "home_currency"), money(_required(r, "current_available_balance")), money(_required(r, "minimum_balance_to_keep")), _set(r, "financial_priorities"), _set(r, "expense_categories_to_protect"), _set(r, "expense_categories_user_is_willing_to_reduce"), _set(r, "expense_categories_user_is_willing_to_stop"), _set(r, "payment_methods_user_will_consider"), int(r["max_installment_months"]) if _optional(r, "max_installment_months") else None))
    profile_map = _unique(profiles, "user_id", "financial_profiles")
    events = []
    for r in _rows(dataset_dir / "financial_events.csv"):
        events.append(FinancialEvent(_required(r, "event_id"), _required(r, "user_id"), _required(r, "event_type"), r.get("description", ""), _required(r, "category"), _required(r, "direction"), money(r["amount"]) if _optional(r, "amount") else None, _required(r, "currency"), _date(r, "event_date"), _date(r, "settlement_date"), _required(r, "status"), _optional(r, "linked_event_id"), _required(r, "flexibility"), money(r["minimum_allowed_amount"]) if _optional(r, "minimum_allowed_amount") else None))
    event_map = _unique(events, "event_id", "financial_events")
    options = []
    for r in _rows(dataset_dir / "request_payment_options.csv"):
        options.append(PaymentOption(_required(r, "payment_option_id"), _required(r, "request_id"), _required(r, "payment_method"), money(_required(r, "payment_amount")), int(_required(r, "number_of_payments")), _date(r, "first_payment_date"), int(r["payment_frequency_days"]) if _optional(r, "payment_frequency_days") else None, money(_required(r, "financing_fee")), money(_required(r, "total_payable_amount"))))
    _unique(options, "payment_option_id", "request_payment_options")
    messages = []
    for r in _rows(dataset_dir / "messages.csv"):
        timestamp = datetime.fromisoformat(_required(r, "sent_at").replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise DatasetError("Message sent_at requires timezone")
        messages.append(Message(_required(r, "message_id"), _required(r, "user_id"), _optional(r, "request_id"), _optional(r, "related_event_id"), timestamp, _required(r, "source_type"), _required(r, "message_text")))
    _unique(messages, "message_id", "messages")
    images = []
    for r in _rows(dataset_dir / "images.csv"):
        image_id = _required(r, "image_id")
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in image_id):
            raise DatasetError("Unsafe image_id")
        images.append(ImageMetadata(image_id, _required(r, "user_id"), _optional(r, "request_id"), _optional(r, "related_event_id"), dataset_dir / "media" / "images" / f"{image_id}.png"))
    _unique(images, "image_id", "images")
    rates = {}
    for r in _rows(dataset_dir / "exchange_rates.csv"):
        key = (_date(r, "rate_date"), _required(r, "from_currency"), _required(r, "to_currency"))
        if key in rates:
            raise DatasetError(f"Duplicate FX key {key}")
        rate = money(_required(r, "rate"))
        if rate <= 0:
            raise DatasetError(f"Nonpositive FX rate {key}")
        rates[key] = rate
    # Supporting tables include both sample and evaluation populations. All links
    # are checked against the union, while only the selected request file is run.
    all_requests = {}
    for filename in ("requests.csv", "sample_requests.csv"):
        for r in _rows(dataset_dir / filename):
            key, user = _required(r, "request_id"), _required(r, "user_id")
            if key in all_requests and all_requests[key] != user:
                raise DatasetError(f"Conflicting request ownership {key}")
            all_requests[key] = user
    for r in requests:
        if r.user_id not in profile_map or r.requested_amount <= 0 or r.desired_completion_date < r.request_date:
            raise DatasetError(f"Invalid request {r.request_id}")
        if r.request_type not in {"purchase", "travel", "education", "family_transfer", "debt_repayment", "investment", "housing", "emergency_expense", "other"}:
            raise DatasetError(f"Unknown request_type {r.request_type}")
    for p in profiles:
        if p.minimum_balance_to_keep < 0 or p.max_installment_months is not None and p.max_installment_months <= 0:
            raise DatasetError(f"Invalid profile limits {p.user_id}")
        if not p.payment_methods_user_will_consider <= {"full_payment", "partial_payment", "installments"}:
            raise DatasetError(f"Unknown payment preference {p.user_id}")
    for e in events:
        if e.user_id not in profile_map or e.direction not in {"credit", "debit"} or e.status not in {"settled", "pending", "scheduled", "failed", "cancelled", "unrealized"}:
            raise DatasetError(f"Invalid event {e.event_id}")
        if e.amount is not None and e.amount < 0 or e.minimum_allowed_amount is not None and e.minimum_allowed_amount < 0:
            raise DatasetError(f"Negative event amount {e.event_id}")
        if e.linked_event_id:
            parent = event_map.get(e.linked_event_id)
            if parent is None or parent.user_id != e.user_id or parent.event_id == e.event_id:
                raise DatasetError(f"Invalid linked event {e.event_id}")
    # Detect lifecycle cycles independently of row order.
    checked = set()
    for e in events:
        chain = set()
        current = e
        while current.event_id not in checked:
            if current.event_id in chain:
                raise DatasetError(f"Lifecycle cycle at {current.event_id}")
            chain.add(current.event_id)
            if not current.linked_event_id:
                break
            current = event_map[current.linked_event_id]
        checked.update(chain)
    for record in [*messages, *images]:
        if record.user_id not in profile_map:
            raise DatasetError("Unknown evidence user")
        if record.request_id and all_requests.get(record.request_id) != record.user_id:
            raise DatasetError("Evidence request/user mismatch")
        if record.related_event_id and (record.related_event_id not in event_map or event_map[record.related_event_id].user_id != record.user_id):
            raise DatasetError("Evidence event/user mismatch")
    for o in options:
        if o.request_id not in all_requests or o.payment_method not in {"full_payment", "installments", "partial_payment"}:
            raise DatasetError(f"Invalid payment option {o.payment_option_id}")
        if o.number_of_payments < 1 or o.payment_amount <= 0 or o.financing_fee < 0 or o.total_payable_amount <= 0:
            raise DatasetError(f"Invalid payment arithmetic {o.payment_option_id}")
        if o.number_of_payments > 1 and (o.payment_frequency_days is None or o.payment_frequency_days <= 0):
            raise DatasetError(f"Missing positive payment interval {o.payment_option_id}")
        if abs(o.payment_amount * o.number_of_payments - o.total_payable_amount) > money("0.01"):
            raise DatasetError(f"Inconsistent option total {o.payment_option_id}")
        if o.request_id in request_map and abs(o.total_payable_amount - request_map[o.request_id].requested_amount - o.financing_fee) > money("0.01"):
            raise DatasetError(f"Inconsistent principal plus fee {o.payment_option_id}")
    return Dataset(requests, profile_map, events, options, messages, images, rates)

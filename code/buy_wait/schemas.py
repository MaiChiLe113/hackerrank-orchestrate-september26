"""Shared typed contracts. Monetary values never pass through binary floats."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ZERO = Decimal("0")
CENT = Decimal("0.01")
OUTPUT_COLUMNS = ("request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation")

def money(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Money must be finite")
    return result

def floor_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_FLOOR)

def format_money(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")

def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    return value

@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str = ""

@dataclass(frozen=True)
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: frozenset[str] = frozenset()
    expense_categories_to_protect: frozenset[str] = frozenset()
    expense_categories_user_is_willing_to_reduce: frozenset[str] = frozenset()
    expense_categories_user_is_willing_to_stop: frozenset[str] = frozenset()
    payment_methods_user_will_consider: frozenset[str] = frozenset()
    max_installment_months: int | None = None

@dataclass(frozen=True)
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Decimal | None
    currency: str
    event_date: date
    settlement_date: date
    status: str
    linked_event_id: str | None = None
    flexibility: str = "fixed"
    minimum_allowed_amount: Decimal | None = None

@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal

@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    message_text: str

@dataclass(frozen=True)
class ImageMetadata:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    path: Path

@dataclass(frozen=True)
class FinancialFact:
    user_id: str
    source_id: str
    fact_type: str
    source_type: str = "message"
    request_id: str | None = None
    related_event_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    effective_date: date | None = None
    status: str | None = None
    category: str | None = None
    description: str | None = None
    scope: str = "single"
    source_timestamp: datetime | None = None
    evidence_reference: str = ""
    extraction_method: str = "rules"
    needs_review: bool = False
    percentage: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class EvidenceResult:
    facts: list[FinancialFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)

@dataclass(frozen=True)
class LedgerEntry:
    date: date
    amount: Decimal  # positive credit, negative debit; home currency
    event_id: str
    category: str
    description: str = ""
    kind: str = "forecast"
    series_id: str | None = None
    provenance: tuple[str, ...] = ()

@dataclass(frozen=True)
class AdjustmentTarget:
    event_id: str
    series_id: str
    category: str
    current_amount: Decimal
    minimum_amount: Decimal = ZERO
    can_stop: bool = False
    can_reduce: bool = False

@dataclass
class FinancialState:
    request: Request
    profile: FinancialProfile
    history: list[FinancialEvent]
    future_entries: list[LedgerEntry]
    facts: list[FinancialFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class Forecast:
    entries: list[LedgerEntry]
    adjustment_targets: list[AdjustmentTarget] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Payment:
    date: date
    amount: Decimal

@dataclass(frozen=True)
class SpendingChange:
    event_id: str
    action: str  # stop or reduce_to
    new_amount: Decimal | None = None

@dataclass
class CandidatePlan:
    method: str
    payments: list[Payment]
    changes: list[SpendingChange] = field(default_factory=list)
    payment_option_id: str = ""

@dataclass
class SimulationResult:
    safe: bool
    minimum_balance: Decimal
    minimum_date: date
    ending_balance: Decimal
    first_violation_date: date | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class Decision:
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None
    affordability_status: str
    plan: CandidatePlan
    simulation: SimulationResult
    diagnostics: dict[str, Any] = field(default_factory=dict)

@dataclass
class Dataset:
    requests: list[Request]
    profiles: dict[str, FinancialProfile]
    events: list[FinancialEvent]
    options: list[PaymentOption]
    messages: list[Message]
    images: list[ImageMetadata]
    exchange_rates: dict[tuple[date, str, str], Decimal]

@dataclass
class EngineConfig:
    horizon_days: int = 90
    same_day_order: str = "credits_first"
    recurrence_method: str = "adaptive"
    expense_quantile: Decimal = Decimal("0.75")
    history_months: int = 6
    installment_limit_mode: str = "payment_count"
    max_changes: int = 3
    evidence_mode: str = "offline"
    provider: str = "openai"
    model: str = ""
    fallback_model: str = ""
    cache_dir: Path = Path("artifacts/evidence_cache")
    strict_evidence: bool = True

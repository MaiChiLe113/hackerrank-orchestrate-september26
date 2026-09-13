"""Exact cash-flow simulation with an explicitly defined within-day order."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from .schemas import CandidatePlan, EngineConfig, FinancialProfile, Forecast, LedgerEntry, Payment, Request, SimulationResult, SpendingChange, ZERO, floor_money

def apply_changes(forecast: Forecast, changes: Sequence[SpendingChange]) -> list[LedgerEntry]:
    targets = {t.event_id: t for t in forecast.adjustment_targets}
    updates = {}
    for change in changes:
        if change.event_id in updates:
            raise ValueError("A spending event cannot be changed twice")
        target = targets.get(change.event_id)
        if target is None:
            raise ValueError(f"Unknown adjustment target {change.event_id}")
        if change.action == "stop":
            if not target.can_stop:
                raise ValueError("Stopping this event is not allowed")
            amount = ZERO
        elif change.action == "reduce_to":
            amount = change.new_amount
            if not target.can_reduce or amount is None or amount < target.minimum_amount or amount >= target.current_amount:
                raise ValueError("Invalid reduction amount")
        else:
            raise ValueError("Unknown spending change")
        updates[change.event_id] = (target, amount)
    by_series = {t.series_id: (t, amount) for t, amount in updates.values()}
    if len(by_series) != len(updates):
        raise ValueError("Multiple changes target the same recurring series")
    result = []
    for entry in forecast.entries:
        replacement = by_series.get(entry.series_id)
        # Settled/pending/scheduled obligations cannot be cancelled by an optional
        # change to future discretionary recurrence.
        if replacement and entry.amount < 0 and entry.kind in {"forecast", "recurring", "variable"}:
            _, amount = replacement
            result.append(replace(entry, amount=-min(-entry.amount, amount)))
        else:
            result.append(entry)
    return result

def ordered_entries(request: Request, entries: Sequence[LedgerEntry], payments: Sequence[Payment], config: EngineConfig) -> list[LedgerEntry]:
    end = request.request_date + timedelta(days=config.horizon_days)
    if config.horizon_days < 1 or config.same_day_order not in {"credits_first", "debits_first"}:
        raise ValueError("Invalid horizon or within-day ordering")
    combined = [e for e in entries if request.request_date <= e.date <= end]
    for index, payment in enumerate(payments):
        if payment.amount <= 0 or not request.request_date <= payment.date <= end:
            raise ValueError("Payment is nonpositive or outside the forecast horizon")
        combined.append(LedgerEntry(payment.date, -payment.amount, f"request-payment-{index}", "requested_payment", kind="payment"))
    def key(entry: LedgerEntry):
        # Pending holds are already unavailable at the opening instant. This is
        # separate from the documented ordering of dated cash events.
        if entry.kind == "reserve" and entry.date == request.request_date:
            phase = -1
        elif config.same_day_order == "credits_first":
            phase = 0 if entry.amount >= 0 else 2 if entry.kind == "payment" else 1
        else:
            phase = 2 if entry.amount >= 0 else 1 if entry.kind == "payment" else 0
        return entry.date, phase, entry.event_id
    return sorted(combined, key=key)

def simulate(request: Request, profile: FinancialProfile, entries: Sequence[LedgerEntry], payments: Sequence[Payment] = (), config: EngineConfig | None = None, trace: bool = False) -> SimulationResult:
    config = config or EngineConfig()
    balance = profile.current_available_balance
    minimum = balance
    minimum_date = request.request_date
    violation = request.request_date if balance < profile.minimum_balance_to_keep else None
    timeline = []
    if trace:
        timeline.append({"date": request.request_date, "event_id": "opening", "amount": ZERO, "balance": balance})
    for entry in ordered_entries(request, entries, payments, config):
        balance += entry.amount
        if balance < minimum:
            minimum, minimum_date = balance, entry.date
        if violation is None and balance < profile.minimum_balance_to_keep:
            violation = entry.date
        if trace:
            timeline.append({"date": entry.date, "event_id": entry.event_id, "category": entry.category, "kind": entry.kind, "amount": entry.amount, "balance": balance, "provenance": entry.provenance})
    return SimulationResult(violation is None, minimum, minimum_date, balance, violation, timeline)

def safe_amount(request: Request, profile: FinancialProfile, entries: Sequence[LedgerEntry], config: EngineConfig | None = None) -> Decimal:
    """Find the exact cent-denominated maximum; feasibility is monotone in payment."""
    config = config or EngineConfig()
    if not simulate(request, profile, entries, config=config).safe:
        return ZERO
    maximum = floor_money(request.requested_amount)
    # Analytical suffix bound: insert a zero-value marker at the request-payment
    # phase, then reserve the smallest balance from that instant onward.
    marker = LedgerEntry(request.request_date, ZERO, "request-payment-marker", "requested_payment", kind="payment")
    # ordered_entries treats zero as credit, so use a infinitesimal negative
    # marker and remove its amount while replaying. No rounding participates.
    marker = replace(marker, amount=Decimal("-0.0000000001"))
    balance = profile.current_available_balance
    after_payment = False
    headroom = None
    for entry in ordered_entries(request, [*entries, marker], (), config):
        if entry.event_id == marker.event_id:
            after_payment = True
        else:
            balance += entry.amount
        if after_payment:
            available = balance - profile.minimum_balance_to_keep
            headroom = available if headroom is None else min(headroom, available)
    return max(ZERO, min(maximum, floor_money(headroom if headroom is not None else ZERO)))

def earliest_full_date(request: Request, profile: FinancialProfile, entries: Sequence[LedgerEntry], config: EngineConfig | None = None) -> date | None:
    config = config or EngineConfig()
    # Search independently of payment preferences and completion deadline. The
    # candidate plan's separate eligibility check applies those constraints.
    for day in range(config.horizon_days + 1):
        candidate = request.request_date + timedelta(days=day)
        if simulate(request, profile, entries, [Payment(candidate, request.requested_amount)], config).safe:
            return candidate
    return None

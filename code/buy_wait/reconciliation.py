"""Reconstruct cash state without replaying settled history into the opening balance.

Only supplied transaction links and explicit evidence can resolve lifecycle records.
The returned history and ledger are in home currency; every conversion uses the
exact settlement-date, directed rate in the participant dataset.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Iterable

from .schemas import (EngineConfig, FinancialEvent, FinancialFact, FinancialProfile,
                      FinancialState, LedgerEntry, Request, ZERO)


class ReconciliationError(ValueError):
    """A necessary financial input could not be resolved safely."""


def normalized_description(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value.casefold())).strip()


def series_key(event: FinancialEvent) -> str:
    return "|".join((event.user_id, event.direction, event.category,
                     normalized_description(event.description)))


def fact_order(fact: FinancialFact) -> tuple:
    stamp = fact.source_timestamp or datetime.min.replace(tzinfo=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    # Same-time conflicts are financially conservative, independent of row order.
    amount = fact.amount or ZERO
    signed = -amount if fact.category == "salary" else amount
    return (stamp, signed, fact.source_id)


def _convert(amount: Decimal, currency: str, home: str, when: date,
             rates: dict[tuple[date, str, str], Decimal], identifier: str) -> Decimal:
    if currency == home:
        return amount
    rate = rates.get((when, currency, home))
    if rate is None or rate <= ZERO:
        raise ReconciliationError(
            f"{identifier}: missing exact FX rate {when}:{currency}->{home}")
    return amount * rate


def _cash_event(event: FinancialEvent) -> bool:
    return event.direction in {"credit", "debit"} and event.status != "unrealized"


def _same_movement(parent: FinancialEvent, child: FinancialEvent) -> bool:
    """A capture replaces its authorization, but a refund is another movement."""
    if (parent.direction != child.direction or parent.currency != child.currency
            or parent.amount != child.amount or not _cash_event(child)):
        return False
    if parent.status not in {"pending", "scheduled", "failed", "cancelled"}:
        return False
    return child.status in {"settled", "scheduled", "cancelled"}


def reconcile(request: Request, profile: FinancialProfile,
              events: Iterable[FinancialEvent], facts: Iterable[FinancialFact],
              exchange_rates: dict[tuple[date, str, str], Decimal],
              config: EngineConfig) -> FinancialState:
    if request.user_id != profile.user_id:
        raise ReconciliationError("Request/profile user mismatch")
    supplied = {e.event_id: e for e in events if e.user_id == request.user_id}
    if len(supplied) == 0:
        # A cash-only profile is valid; do not invent a minimum history requirement.
        supplied = {}
    today = request.request_date
    horizon = today + timedelta(days=config.horizon_days)
    audit: list[dict] = []
    warnings: list[str] = []
    applicable: list[FinancialFact] = []
    for fact in facts:
        if (fact.user_id != request.user_id
                or fact.request_id not in {None, "", request.request_id}):
            continue
        if fact.source_timestamp and fact.source_timestamp.date() > today:
            audit.append({"source_id": fact.source_id, "action": "excluded_future_evidence"})
            continue
        if fact.needs_review:
            warnings.append(f"{fact.source_id}: evidence requires review; not applied")
            continue
        if fact.related_event_id and fact.related_event_id not in supplied:
            raise ReconciliationError(f"{fact.source_id}: unknown related event {fact.related_event_id}")
        applicable.append(fact)
    applicable.sort(key=fact_order)
    provenance: dict[str, list[str]] = {key: [key] for key in supplied}
    excluded: set[str] = set()

    # First apply direct amendments in native currency, before converting records.
    for fact in applicable:
        if not fact.related_event_id:
            continue
        event = supplied[fact.related_event_id]
        changes: dict = {}
        if fact.fact_type in {"amount", "amount_amendment", "event_amendment"}:
            if fact.amount is not None:
                changes["amount"] = fact.amount
                changes["currency"] = fact.currency or event.currency
        if fact.fact_type == "cancellation":
            changes["status"] = "cancelled"
        if fact.fact_type in {"payment_status_change", "event_amendment"} and fact.status:
            if fact.status == "outstanding":
                changes["status"] = "scheduled"
                changes["settlement_date"] = max(today, fact.effective_date or today)
            elif fact.status == "disputed":
                # A dispute is not a cancellation and does not release the hold.
                if event.status not in {"settled", "cancelled"}:
                    changes["status"] = "pending"
            elif fact.status in {"settled", "pending", "scheduled", "cancelled", "failed"}:
                changes["status"] = fact.status
                if fact.effective_date:
                    changes["settlement_date"] = fact.effective_date
        if fact.fact_type == "transaction_clarification":
            clarification = fact.metadata.get("clarification", "")
            if clarification in {"internal_transfer", "non_cash", "confirmed_duplicate"}:
                excluded.add(event.event_id)
            if clarification == "unsettled_credit" and event.direction == "credit":
                changes["status"] = "pending"
        # Single linked salary amendments affect one future cash record only.
        if (fact.fact_type in {"salary_change", "income_confirmed", "salary_delay"}
                and event.direction == "credit" and event.settlement_date > today):
            if fact.amount is not None:
                changes.update(amount=fact.amount, currency=fact.currency or event.currency)
            if fact.effective_date:
                changes["settlement_date"] = fact.effective_date
        if changes:
            supplied[event.event_id] = replace(event, **changes)
            provenance[event.event_id].append(fact.source_id)
            audit.append({"event_id": event.event_id, "source_id": fact.source_id,
                          "action": "amended", "fields": sorted(changes)})

    # Links express lifecycles, not a blanket instruction to drop linked rows.
    superseded: set[str] = set()
    for event in supplied.values():
        if event.event_id in excluded or not event.linked_event_id:
            continue
        parent = supplied.get(event.linked_event_id)
        if parent and _same_movement(parent, event):
            superseded.add(parent.event_id)
            audit.append({"event_id": parent.event_id, "action": "superseded_cash_state",
                          "by_event_id": event.event_id})

    history: list[FinancialEvent] = []
    future: list[LedgerEntry] = []
    for event in sorted(supplied.values(), key=lambda e: (e.settlement_date, e.event_id)):
        reason = None
        if event.event_id in excluded:
            reason = "noncash_transfer_or_explicit_duplicate"
        elif event.event_id in superseded:
            reason = "superseded"
        elif not _cash_event(event):
            reason = "non_cash"
        elif event.status in {"failed", "cancelled"}:
            reason = event.status
        elif event.direction == "credit" and event.status == "pending":
            reason = "unsettled_credit"
        elif (event.direction == "credit" and event.status != "settled"
              and not (event.status in {"scheduled", "confirmed"} and event.category == "salary"
                       and event.event_type == "income")):
            reason = "unconfirmed_credit"
        if reason:
            audit.append({"event_id": event.event_id, "action": "excluded", "reason": reason})
            continue
        if event.amount is None:
            raise ReconciliationError(f"{event.event_id}: necessary amount unresolved; supply linked image evidence")
        if event.amount < ZERO:
            raise ReconciliationError(f"{event.event_id}: negative event amount")
        amount = _convert(event.amount, event.currency, profile.home_currency,
                          event.settlement_date, exchange_rates, event.event_id)
        minimum = event.minimum_allowed_amount
        if minimum is not None:
            minimum = _convert(minimum, event.currency, profile.home_currency,
                               event.settlement_date, exchange_rates, event.event_id)
        converted = replace(event, amount=amount, currency=profile.home_currency,
                            minimum_allowed_amount=minimum)
        if event.status == "settled" and event.settlement_date <= today:
            history.append(converted)
            audit.append({"event_id": event.event_id, "action": "historical_only",
                          "reason": "opening_balance_already_reflects_settlement"})
            continue
        # Pending debits are reserved now, even when their posting date is later.
        when = today if event.direction == "debit" and event.status == "pending" else max(today, event.settlement_date)
        if when <= horizon:
            future.append(LedgerEntry(
                date=when, amount=amount if event.direction == "credit" else -amount,
                event_id=event.event_id, category=event.category, description=event.description,
                kind="pending_reserve" if event.status == "pending" else "confirmed",
                series_id=series_key(converted), provenance=tuple(provenance[event.event_id])))
            audit.append({"event_id": event.event_id, "action": "future_cash",
                          "date": when.isoformat(), "amount": str(future[-1].amount)})

    normalized_facts: list[FinancialFact] = []
    for fact in applicable:
        currency = fact.currency or profile.home_currency
        if fact.amount is not None and currency != profile.home_currency:
            related = supplied.get(fact.related_event_id or "")
            when = fact.effective_date or (related.settlement_date if related else None)
            if when is None:
                raise ReconciliationError(f"{fact.source_id}: foreign evidence amount has no dated FX anchor")
            fact = replace(fact, amount=_convert(fact.amount, currency, profile.home_currency,
                                                when, exchange_rates, fact.source_id),
                           currency=profile.home_currency)
        normalized_facts.append(fact)
        if (not fact.related_event_id and fact.fact_type in {"expense_confirmed", "new_expense", "payment_status_change"}
                and fact.status != "cancelled" and fact.amount is not None):
            when = max(today, fact.effective_date or today)
            if when <= horizon:
                future.append(LedgerEntry(when, -fact.amount, f"evidence:{fact.source_id}",
                                          fact.category or "other", fact.description or "Confirmed obligation",
                                          "confirmed", f"evidence:{fact.source_id}", (fact.source_id,)))
    return FinancialState(request, profile, history, sorted(future, key=lambda e: (e.date, e.event_id)),
                          normalized_facts, warnings, audit)

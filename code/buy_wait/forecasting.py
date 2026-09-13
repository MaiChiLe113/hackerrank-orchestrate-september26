"""Auditable recurrence inference and conservative spending forecasts.

Calendar and fixed-day models are selected by walk-forward date errors. Essential
variable spending uses an empirical upper quantile, not a median mislabeled as a
safety margin. No stochastic sampling or outside financial data is involved.
"""
from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from statistics import median
from typing import Iterable
import re

from .reconciliation import ReconciliationError, fact_order, normalized_description, series_key
from .schemas import (AdjustmentTarget, EngineConfig, FinancialEvent, FinancialFact,
                      FinancialState, Forecast, LedgerEntry, ZERO)


VARIABLE_CATEGORIES = frozenset({"groceries", "transport", "dining"})
ESSENTIAL_CATEGORIES = frozenset({"groceries", "transport", "rent", "housing", "utilities",
                                "healthcare", "insurance", "education", "debt_repayment", "family_support"})
UNCERTAIN_INCOME = re.compile(
    r"bonus|commission|arrears|prize|lottery|windfall|reimburse|refund|investment|"
    r"freelanc|project|invoice|contract|consulting|independent work|retainer|"
    r"platform|app earnings|marketplace|seasonal|peak.season|temporary assignment|final employer",
    re.I)


@dataclass(frozen=True)
class Cadence:
    method: str
    interval_days: int
    day_of_month: int | None = None
    month_end: bool = False
    backtest_mae_days: Decimal = ZERO
    folds: int = 0


def empirical_quantile(values: Iterable[Decimal], q: Decimal) -> Decimal:
    values = sorted(values)
    if not values:
        raise ValueError("Cannot estimate an amount without observations")
    if not ZERO <= q <= Decimal(1):
        raise ValueError("expense_quantile must be between zero and one")
    # Linear interpolation is deterministic in Decimal, including fractional cents.
    index = q * (len(values) - 1)
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (index - lower) * (values[upper] - values[lower])


def _add_month(when: date, day: int, end: bool = False) -> date:
    year = when.year + (when.month == 12)
    month = 1 if when.month == 12 else when.month + 1
    last = monthrange(year, month)[1]
    return date(year, month, last if end else min(day, last))


def _fit(dates: list[date], method: str) -> Cadence:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    interval = max(1, int(median(gaps)))
    if method == "calendar_month":
        day = int(median([d.day for d in dates]))
        end = all(d.day == monthrange(d.year, d.month)[1] for d in dates)
        return Cadence(method, 30, day, end)
    if method == "fixed_30":
        interval = 30
    elif method == "last_interval":
        interval = gaps[-1]
    elif method == "mode_interval":
        counts = Counter(gaps)
        interval = min(counts, key=lambda gap: (-counts[gap], abs(gap - interval), gap))
    return Cadence(method, interval)


def _next_date(when: date, cadence: Cadence) -> date:
    if cadence.method == "calendar_month":
        return _add_month(when, cadence.day_of_month or when.day, cadence.month_end)
    return when + timedelta(days=cadence.interval_days)


def infer_cadence(dates: Iterable[date], method: str = "adaptive") -> tuple[Cadence | None, dict]:
    """Select a model using only prefixes preceding each historical target."""
    dates = sorted(set(dates))
    if len(dates) < 2:
        return None, {"reason": "insufficient_distinct_dates", "observations": len(dates)}
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    typical = int(median(gaps))
    if typical <= 0 or typical > 120:
        return None, {"reason": "unsupported_interval", "median_interval_days": typical}
    aliases = {"historical_median": "median_interval", "calendar_monthly": "calendar_month"}
    method = aliases.get(method, method)
    if method != "adaptive":
        if method not in {"calendar_month", "fixed_30", "median_interval", "last_interval", "mode_interval"}:
            raise ValueError(f"Unknown recurrence method {method}")
        candidates = [method]
    else:
        candidates = (["calendar_month", "fixed_30", "median_interval", "mode_interval"]
                      if 27 <= typical <= 33 else ["median_interval", "mode_interval"])
    scores: dict[str, Decimal] = {}
    folds = max(0, len(dates) - 2)
    for candidate in candidates:
        errors = [abs((_next_date(dates[n - 1], _fit(dates[:n], candidate)) - dates[n]).days)
                  for n in range(2, len(dates))]
        scores[candidate] = Decimal(sum(errors)) / len(errors) if errors else ZERO
    selected = min(candidates, key=lambda name: (scores[name], candidates.index(name)))
    cadence = replace(_fit(dates, selected), backtest_mae_days=scores[selected], folds=folds)
    diagnostics = {"method": cadence.method, "interval_days": cadence.interval_days,
                   "backtest_mae_days": str(cadence.backtest_mae_days), "folds": folds,
                   "candidate_mae_days": {k: str(v) for k, v in scores.items()},
                   "observations": len(dates)}
    # Extremely irregular timestamps are evidence of spending, not a reliable due date.
    if folds and scores[selected] > Decimal(max(3, typical * 0.35)):
        diagnostics["reason"] = "irregular_timing"
        return None, diagnostics
    return cadence, diagnostics


def _salary_event(event: FinancialEvent) -> bool:
    return (event.direction == "credit" and event.category == "salary"
            and event.event_type == "income" and not UNCERTAIN_INCOME.search(event.description))


def _group_key(event: FinancialEvent) -> str:
    if event.direction == "debit" and event.category in VARIABLE_CATEGORIES:
        return f"{event.user_id}|debit|{event.category}|variable"
    desc = normalized_description(event.description)
    if _salary_event(event) and ("payroll before leave" in desc or "payroll after returning" in desc):
        return f"{event.user_id}|credit|salary|employment_payroll"
    return series_key(event)


def _is_target(fact: FinancialFact, events: list[FinancialEvent]) -> bool:
    if fact.related_event_id:
        return any(event.event_id == fact.related_event_id for event in events)
    target = fact.metadata.get("target_description") or fact.metadata.get("series_description")
    if target:
        target = normalized_description(str(target))
        return any(target in normalized_description(event.description) for event in events)
    return fact.category in {None, "", events[-1].category}


def _fact_start(fact: FinancialFact, today: date) -> date:
    return max(today, fact.effective_date or today)


def _adjustment_target(state: FinancialState, series: str, events: list[FinancialEvent],
                       forecast_amount: Decimal) -> AdjustmentTarget | None:
    latest = max(events, key=lambda e: (e.settlement_date, e.event_id))
    category = latest.category
    profile = state.profile
    if category in profile.expense_categories_to_protect or latest.direction != "debit":
        return None
    stop = (latest.flexibility in {"stoppable", "reducible_or_stoppable"}
            and category in profile.expense_categories_user_is_willing_to_stop)
    reduce = (latest.flexibility in {"reducible", "reducible_or_stoppable"}
              and category in profile.expense_categories_user_is_willing_to_reduce)
    floor = latest.minimum_allowed_amount or ZERO
    if not stop and not (reduce and floor < forecast_amount):
        return None
    return AdjustmentTarget(latest.event_id, series, category, forecast_amount, floor, stop, reduce)


def _amount_for(events: list[FinancialEvent], essential: bool, config: EngineConfig) -> Decimal:
    values = [event.amount for event in events if event.amount is not None]
    quantile = config.expense_quantile if essential else Decimal("0.5")
    return empirical_quantile(values, quantile)


def _future_dates(last: date, cadence: Cadence, today: date, horizon: date) -> list[date]:
    result = []
    current = _next_date(last, cadence)
    while current <= horizon:
        if current >= today:
            result.append(current)
        current = _next_date(current, cadence)
    return result


def _spending_entries(state: FinancialState, groups: dict[str, list[FinancialEvent]], config: EngineConfig,
                      diagnostics: dict) -> tuple[list[LedgerEntry], list[AdjustmentTarget]]:
    today = state.request.request_date
    horizon = today + timedelta(days=config.horizon_days)
    entries: list[LedgerEntry] = []
    targets: list[AdjustmentTarget] = []
    for series, records in sorted(groups.items()):
        if records[-1].direction != "debit":
            continue
        records.sort(key=lambda e: (e.settlement_date, e.event_id))
        last = records[-1]
        # One transaction cannot establish a recurring commitment.
        if len({e.settlement_date for e in records}) < 2:
            diagnostics[series] = {"reason": "insufficient_distinct_dates"}
            continue
        cadence, details = infer_cadence([e.settlement_date for e in records], config.recurrence_method)
        diagnostics[series] = details
        essential = last.category in ESSENTIAL_CATEGORIES or last.category in state.profile.expense_categories_to_protect
        variable = last.category in VARIABLE_CATEGORIES
        amount = _amount_for(records, essential and variable, config)
        details["amount_estimator"] = f"quantile_{config.expense_quantile}" if essential and variable else "historical_median"
        details["amount"] = str(amount)
        facts = [f for f in state.facts if _is_target(f, records)
                 and f.fact_type in {"recurring_expense_change", "cancellation"}]
        if cadence is None:
            # Irregular protected/essential expenses still require a reserve. Build
            # disjoint complete 28-day buckets ending at the request boundary.
            if not essential or len(records) < 4:
                continue
            span = (today - records[0].settlement_date).days
            bucket_count = min(6, span // 28)
            if bucket_count < 2:
                continue
            buckets = [sum((e.amount or ZERO for e in records
                            if today - timedelta(days=28 * (n + 1)) <= e.settlement_date
                            < today - timedelta(days=28 * n)), ZERO) for n in range(bucket_count)]
            budget = empirical_quantile(buckets, config.expense_quantile)
            daily = (budget / 28).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
            if daily <= ZERO:
                continue
            for n in range(config.horizon_days + 1):
                entries.append(LedgerEntry(today + timedelta(days=n), -daily, last.event_id,
                                          last.category, "Essential variable spending reserve", "variable_reserve",
                                          series, tuple(e.event_id for e in records)))
            details.update(reason="irregular_essential_budget", daily_reserve=str(daily), buckets=len(buckets))
            continue
        if (today - last.settlement_date).days > max(45, cadence.interval_days * 2):
            details["reason"] = "stale_series"
            continue
        target = _adjustment_target(state, series, records, amount)
        generated: list[LedgerEntry] = []
        for when in _future_dates(last.settlement_date, cadence, today, horizon):
            payment_amount = amount
            sources = [e.event_id for e in records]
            cancelled = False
            for fact in sorted(facts, key=fact_order):
                if when < _fact_start(fact, today):
                    continue
                if fact.fact_type == "cancellation" and fact.scope == "ongoing":
                    cancelled = True
                elif fact.fact_type == "recurring_expense_change":
                    if fact.amount is not None:
                        payment_amount = fact.amount
                    elif fact.percentage is not None:
                        payment_amount = amount * (Decimal(1) + fact.percentage / 100)
                    else:
                        raise ReconciliationError(f"{fact.source_id}: recurring expense amendment has no amount or percentage")
                sources.append(fact.source_id)
            if not cancelled:
                generated.append(LedgerEntry(when, -payment_amount, last.event_id, last.category,
                                             last.description, "recurring_expense", series, tuple(sources)))
        if target and generated:
            # A known amendment changes the baseline cost, but never lowers the
            # supplied reduction floor. One action remains tied to the latest row.
            target = replace(target, current_amount=max(-e.amount for e in generated))
            targets.append(target)
        entries.extend(generated)
    return entries, targets


def _salary_entries(state: FinancialState, groups: dict[str, list[FinancialEvent]], config: EngineConfig,
                    diagnostics: dict) -> list[LedgerEntry]:
    today = state.request.request_date
    horizon = today + timedelta(days=config.horizon_days)
    facts = sorted([f for f in state.facts if f.category in {None, "", "salary"}
                    and f.fact_type in {"salary_change", "salary_delay", "income_stop", "income_confirmed"}],
                   key=fact_order)
    salary_groups = {key: value for key, value in groups.items() if _salary_event(value[-1])}
    # Consolidated remaining household salary supersedes both old household streams.
    household = [f for f in facts if f.metadata.get("household_scope") and f.amount is not None]
    if household and salary_groups:
        all_records = [e for records in salary_groups.values() for e in records]
        primary = [e for e in all_records if "primary" in e.description.casefold()]
        chosen = primary or max(salary_groups.values(), key=lambda records: len(records))
        salary_groups = {_group_key(chosen[-1]): chosen}
    generated: list[LedgerEntry] = []
    for series, records in sorted(salary_groups.items()):
        records = sorted(records, key=lambda e: (e.settlement_date, e.event_id))
        last = records[-1]
        matching = [f for f in facts if _is_target(f, records)]
        cadence, details = infer_cadence([e.settlement_date for e in records], config.recurrence_method)
        diagnostics[series] = details
        recurring = [f for f in matching if f.scope == "ongoing" and f.fact_type in {"salary_change", "income_confirmed"}
                     and f.amount is not None]
        # Explicit monthly ongoing salary can bridge a leave gap or a first cycle.
        if cadence is None and recurring:
            anchor = recurring[-1].effective_date or last.settlement_date
            cadence = Cadence("calendar_month", 30, anchor.day)
            details.update(method="calendar_month", reason="explicit_ongoing_salary", interval_days=30)
        if cadence is None:
            continue
        if (today - last.settlement_date).days > max(40, cadence.interval_days * 1.6) and not recurring:
            details["reason"] = "stale_income_series"
            continue
        # Fixed contractual salary uses median; recent stable regimes can replace
        # the old median only with at least two repeated new observations.
        amount = empirical_quantile([e.amount for e in records if e.amount is not None], Decimal("0.5"))
        if len(records) >= 3 and records[-1].amount == records[-2].amount and records[-1].amount != amount:
            amount = records[-1].amount or amount
            details["amount_estimator"] = "confirmed_recent_regime"
        else:
            details["amount_estimator"] = "historical_median"
        dates = _future_dates(last.settlement_date, cadence, today, horizon)
        entries = [LedgerEntry(when, amount, last.event_id, "salary", last.description,
                               "recurring_salary", series, tuple(e.event_id for e in records)) for when in dates]
        for fact in matching:
            if fact.metadata.get("one_time_arrears"):
                continue
            start = _fact_start(fact, today)
            if fact.fact_type == "income_stop":
                entries = [entry for entry in entries if entry.date < start]
                details["income_stop_source"] = fact.source_id
                continue
            if fact.fact_type == "salary_delay":
                if not fact.effective_date:
                    raise ReconciliationError(f"{fact.source_id}: salary delay lacks a date")
                if entries:
                    first = entries[0]
                    entries[0] = replace(first, date=fact.effective_date,
                                         provenance=first.provenance + (fact.source_id,))
                continue
            if fact.fact_type in {"salary_change", "income_confirmed"} and fact.amount is not None:
                if fact.scope == "ongoing":
                    affected = [index for index, entry in enumerate(entries) if entry.date >= start]
                else:
                    # The next payroll may be advanced or delayed by evidence.
                    affected = [0] if entries else []
                for index in affected:
                    entry = entries[index]
                    new_date = fact.effective_date if fact.scope == "single" and fact.effective_date else entry.date
                    entries[index] = replace(entry, date=new_date, amount=fact.amount,
                                             provenance=entry.provenance + (fact.source_id,))
                if fact.effective_date and today <= fact.effective_date <= horizon:
                    # An explicit resumption date is the first revised payment;
                    # suppress only the forecast for that corresponding pay cycle.
                    near = [(abs((entry.date - fact.effective_date).days), index)
                            for index, entry in enumerate(entries)]
                    if near and min(near)[0] <= cadence.interval_days // 2:
                        index = min(near)[1]
                        entries[index] = replace(entries[index], date=fact.effective_date, amount=fact.amount,
                                                 provenance=entries[index].provenance + (fact.source_id,))
                    elif not entries or fact.effective_date < entries[0].date:
                        entries.append(LedgerEntry(fact.effective_date, fact.amount, f"evidence:{fact.source_id}",
                                                   "salary", "Confirmed salary", "confirmed", series, (fact.source_id,)))
        generated.extend(entry for entry in entries if today <= entry.date <= horizon)

    # Confirmed first salary / regular resumption may have no usable historical
    # series. A single fact confirms one credit; only explicit ongoing scope repeats.
    for fact in facts:
        if fact.fact_type not in {"salary_change", "income_confirmed"} or fact.amount is None or not fact.effective_date:
            continue
        if fact.metadata.get("one_time_arrears") or fact.status in {"pending", "unconfirmed", "cancelled"}:
            continue
        if any(fact.source_id in entry.provenance for entry in generated):
            continue
        if any(f.fact_type == "income_stop" and fact_order(f) >= fact_order(fact) for f in facts):
            continue
        when = fact.effective_date
        series = f"{state.request.user_id}|credit|salary|evidence:{fact.source_id}"
        while when <= horizon:
            if when >= today:
                generated.append(LedgerEntry(when, fact.amount, f"evidence:{fact.source_id}", "salary",
                                             "Confirmed salary", "confirmed", series, (fact.source_id,)))
            if fact.scope != "ongoing":
                break
            when = _add_month(when, fact.effective_date.day)
    return generated


def _merge_confirmed(state: FinancialState, projected: list[LedgerEntry], config: EngineConfig,
                     diagnostics: dict) -> list[LedgerEntry]:
    """Replace a forecast occurrence with a matched supplied cash obligation."""
    today = state.request.request_date
    horizon = today + timedelta(days=config.horizon_days)
    confirmed = list(state.future_entries)
    salary_facts = sorted([f for f in state.facts if f.fact_type in {"salary_change", "salary_delay", "income_stop", "income_confirmed"}
                           and f.category in {None, "", "salary"}], key=fact_order)
    # Amend supplied salary records before matching them to inferred occurrences.
    for fact in salary_facts:
        candidates = [i for i, entry in enumerate(confirmed) if entry.category == "salary" and entry.amount > ZERO
                      and (not fact.related_event_id or fact.related_event_id == entry.event_id)]
        candidates.sort(key=lambda i: (confirmed[i].date, confirmed[i].event_id))
        if not candidates:
            continue
        if fact.fact_type == "income_stop":
            confirmed = [entry for entry in confirmed if not (entry.category == "salary" and entry.amount > ZERO
                         and entry.date >= _fact_start(fact, today))]
        elif fact.fact_type == "salary_delay" and fact.effective_date:
            i = candidates[0]
            confirmed[i] = replace(confirmed[i], date=fact.effective_date,
                                   provenance=confirmed[i].provenance + (fact.source_id,))
        elif fact.fact_type in {"salary_change", "income_confirmed"} and fact.amount is not None and not fact.metadata.get("one_time_arrears"):
            i = candidates[0]
            if fact.scope == "single" or confirmed[i].date >= _fact_start(fact, today):
                confirmed[i] = replace(confirmed[i], amount=fact.amount,
                                       date=fact.effective_date or confirmed[i].date,
                                       provenance=confirmed[i].provenance + (fact.source_id,))
    remaining = list(projected)
    for actual in sorted(confirmed, key=lambda e: (e.date, e.event_id)):
        if actual.category == "salary" and UNCERTAIN_INCOME.search(actual.description):
            continue
        candidates = []
        for i, entry in enumerate(remaining):
            if entry.category != actual.category or (entry.amount > ZERO) != (actual.amount > ZERO):
                continue
            same_source = (actual.event_id == entry.event_id or actual.series_id == entry.series_id
                           or normalized_description(actual.description) == normalized_description(entry.description)
                           or bool(set(actual.provenance) & set(entry.provenance)))
            # "Next confirmed salary" is intentionally description-independent;
            # amount proximity identifies the likely household stream first.
            if actual.category == "salary":
                same_source = True
            if not same_source or abs((entry.date - actual.date).days) > 20:
                continue
            candidates.append((abs(entry.amount - actual.amount), abs((entry.date - actual.date).days), entry.series_id or "", i))
        if candidates:
            i = min(candidates)[-1]
            matched = remaining.pop(i)
            actual = replace(actual, series_id=matched.series_id,
                             provenance=tuple(dict.fromkeys(actual.provenance + matched.provenance)))
            diagnostics.setdefault("superseded_forecast", []).append(
                {"event_id": actual.event_id, "forecast_date": matched.date.isoformat(), "actual_date": actual.date.isoformat()})
        remaining.append(actual)
    # Each source can yield multiple payments; dedupe only an identical occurrence.
    unique: dict[tuple, LedgerEntry] = {}
    for entry in remaining:
        if not today <= entry.date <= horizon:
            continue
        key = (entry.series_id, entry.date, entry.amount)
        if key not in unique or entry.kind in {"confirmed", "pending_reserve"}:
            unique[key] = entry
    return sorted(unique.values(), key=lambda e: (e.date, e.event_id, e.series_id or ""))


def build_forecast(state: FinancialState, config: EngineConfig) -> Forecast:
    today = state.request.request_date
    cutoff = today - timedelta(days=round(config.history_months * 365.2425 / 12))
    groups: dict[str, list[FinancialEvent]] = defaultdict(list)
    diagnostics: dict = {"horizon_start": today.isoformat(),
                         "horizon_end": (today + timedelta(days=config.horizon_days)).isoformat(),
                         "horizon_inclusive": True, "series": {}, "excluded_income": []}
    for event in state.history:
        if not cutoff <= event.settlement_date <= today:
            continue
        if event.direction == "credit" and not _salary_event(event):
            diagnostics["excluded_income"].append({"event_id": event.event_id, "reason": "not_supported_recurring_salary"})
            continue
        if event.direction == "debit" and event.linked_event_id:
            # Refund/capture lifecycle records do not establish recurring spending.
            continue
        groups[_group_key(event)].append(event)
    for records in groups.values():
        records.sort(key=lambda e: (e.settlement_date, e.event_id))
    expenses, targets = _spending_entries(state, groups, config, diagnostics["series"])
    income = _salary_entries(state, groups, config, diagnostics["series"])
    entries = _merge_confirmed(state, expenses + income, config, diagnostics)
    used_series = {entry.series_id for entry in entries}
    return Forecast(entries, [target for target in targets if target.series_id in used_series],
                    list(state.warnings), diagnostics)

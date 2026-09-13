"""Enumerate supported payment schedules, verify safety, and rank deterministically.

The search is exhaustive over the permitted sets of up to three spending changes.
For each set, maximal reductions establish feasibility; after selecting a schedule,
cent-level search restores as much discretionary spending as safety allows.  This
keeps the financial-capacity fields independent from optional spending changes.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from itertools import combinations, product
from typing import Iterable

from .schemas import (
    CENT, ZERO, AdjustmentTarget, CandidatePlan, Decision, EngineConfig,
    FinancialProfile, Forecast, Payment, PaymentOption, Request, SpendingChange,
)
from .simulation import apply_changes, earliest_full_date, safe_amount, simulate


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(index, 12)
    return date(year, month0 + 1, min(day.day, monthrange(year, month0 + 1)[1]))


def installment_schedule(option: PaymentOption, profile: FinancialProfile,
                         config: EngineConfig) -> list[Payment] | None:
    """Return an exact offer schedule, or None for a malformed/ineligible offer."""
    count = option.number_of_payments
    if option.payment_method != "installments" or count < 2:
        return None
    if profile.max_installment_months is None or profile.max_installment_months <= 0:
        return None
    if option.payment_frequency_days is None or option.payment_frequency_days <= 0:
        return None
    if option.payment_amount <= ZERO or option.financing_fee < ZERO:
        return None
    if option.payment_amount * count != option.total_payable_amount:
        return None
    if config.installment_limit_mode == "payment_count":
        if count > profile.max_installment_months:
            return None
    elif config.installment_limit_mode != "calendar_duration":
        raise ValueError("installment_limit_mode must be payment_count or calendar_duration")
    final = option.first_payment_date + timedelta(days=(count - 1) * option.payment_frequency_days)
    if config.installment_limit_mode == "calendar_duration":
        if final > _add_months(option.first_payment_date, profile.max_installment_months):
            return None
    return [Payment(option.first_payment_date + timedelta(days=i * option.payment_frequency_days),
                    option.payment_amount) for i in range(count)]


def _rank(plan: CandidatePlan) -> tuple:
    """Deadline/safety eligibility is enforced before this ordered comparison."""
    return (bool(plan.changes), sum((p.amount for p in plan.payments), ZERO),
            plan.payments[0].date, len(plan.payments), plan.payment_option_id)


def _change_key(changes: list[SpendingChange]) -> tuple:
    return tuple((c.event_id, c.action, c.new_amount or ZERO) for c in changes)


def _eligible_targets(profile: FinancialProfile, forecast: Forecast) -> list[AdjustmentTarget]:
    targets = []
    seen: set[str] = set()
    for target in sorted(forecast.adjustment_targets, key=lambda t: (t.event_id, t.series_id)):
        # Adjustment targets are constructed from flexible recurring records by
        # forecasting; apply both the target permission and profile permission.
        if target.event_id in seen or target.category in profile.expense_categories_to_protect:
            continue
        if not ZERO <= target.minimum_amount <= target.current_amount:
            continue
        stop = target.can_stop and target.category in profile.expense_categories_user_is_willing_to_stop
        reduce = (target.can_reduce and target.category in profile.expense_categories_user_is_willing_to_reduce
                  and target.minimum_amount < target.current_amount)
        if target.current_amount <= ZERO or not (stop or reduce):
            continue
        if not any(e.amount < ZERO and e.series_id == target.series_id
                   and e.kind in {"forecast", "recurring", "variable"} for e in forecast.entries):
            continue
        targets.append(replace(target, can_stop=stop, can_reduce=reduce))
        seen.add(target.event_id)
    return targets


def _change_sets(targets: list[AdjustmentTarget], limit: int) -> Iterable[list[SpendingChange]]:
    for count in range(1, min(3, limit, len(targets)) + 1):
        for selected in combinations(targets, count):
            # Two event IDs describing the same series cannot supply two actions.
            if len({t.series_id for t in selected}) != count:
                continue
            alternatives = []
            for target in selected:
                actions = []
                if target.can_reduce:
                    actions.append(SpendingChange(target.event_id, "reduce_to", target.minimum_amount))
                if target.can_stop:
                    actions.append(SpendingChange(target.event_id, "stop"))
                alternatives.append(actions)
            for actions in product(*alternatives):
                yield list(actions)


def _schedules(request: Request, profile: FinancialProfile, forecast: Forecast,
               options: list[PaymentOption], config: EngineConfig, capacity: Decimal,
               baseline_earliest: date | None, changes: list[SpendingChange]) -> Iterable[CandidatePlan]:
    entries = apply_changes(forecast, changes) if changes else forecast.entries
    methods = profile.payment_methods_user_will_consider
    full_ids = sorted(o.payment_option_id for o in options if o.request_id == request.request_id
                      and o.payment_method == "full_payment")
    full_id = full_ids[0] if full_ids else ""
    if "full_payment" in methods:
        yield CandidatePlan("full_payment", [Payment(request.request_date, request.requested_amount)],
                            list(changes), full_id)
        earliest = earliest_full_date(request, profile, entries, config) if changes else baseline_earliest
        if earliest is not None and earliest > request.request_date:
            yield CandidatePlan("wait", [Payment(earliest, request.requested_amount)], list(changes), full_id)
    if (request.allows_partial_payment and "partial_payment" in methods
            and ZERO < capacity < request.requested_amount and baseline_earliest is not None
            and request.request_date < baseline_earliest <= request.desired_completion_date):
        yield CandidatePlan("partial_payment", [Payment(request.request_date, capacity),
                            Payment(baseline_earliest, request.requested_amount - capacity)], list(changes))
    if "installments" in methods:
        for option in sorted(options, key=lambda o: o.payment_option_id):
            if option.request_id != request.request_id:
                continue
            payments = installment_schedule(option, profile, config)
            if payments is None or option.total_payable_amount != request.requested_amount + option.financing_fee:
                continue
            yield CandidatePlan("installments", payments, list(changes), option.payment_option_id)


def _safe_candidates(request: Request, profile: FinancialProfile, forecast: Forecast,
                     options: list[PaymentOption], config: EngineConfig, capacity: Decimal,
                     baseline_earliest: date | None, changes: list[SpendingChange]) -> Iterable[CandidatePlan]:
    horizon = request.request_date + timedelta(days=config.horizon_days)
    entries = apply_changes(forecast, changes) if changes else forecast.entries
    for plan in _schedules(request, profile, forecast, options, config, capacity, baseline_earliest, changes):
        if (not plan.payments or plan.payments[0].date < request.request_date
                or plan.payments[-1].date > min(request.desired_completion_date, horizon)):
            continue
        if simulate(request, profile, entries, plan.payments, config).safe:
            yield plan


def _restore_spending(request: Request, profile: FinancialProfile, forecast: Forecast,
                      plan: CandidatePlan, config: EngineConfig) -> CandidatePlan:
    """Remove redundant actions and minimize reductions for the chosen schedule.

Safety is monotone in each nonnegative expense reduction.  Bisection therefore
finds the largest cent-denominated new amount in O(log monetary range) replays.
The deterministic coordinate order is a tie policy, not a claim of globally
optimal distribution of reductions between categories.
"""
    changes = list(plan.changes)
    targets = {target.event_id: target for target in forecast.adjustment_targets}
    for original in list(changes):
        if original not in changes:
            continue
        removed = [c for c in changes if c.event_id != original.event_id]
        if simulate(request, profile, apply_changes(forecast, removed), plan.payments, config).safe:
            changes = removed
            continue
        if original.action != "reduce_to":
            continue
        target = targets[original.event_id]
        low = int(((original.new_amount or ZERO) / CENT).to_integral_value(rounding=ROUND_CEILING))
        high = int((target.current_amount / CENT).to_integral_value(rounding=ROUND_CEILING)) - 1
        while low < high:
            mid = (low + high + 1) // 2
            trial = [replace(c, new_amount=Decimal(mid) * CENT) if c.event_id == original.event_id else c
                     for c in changes]
            if simulate(request, profile, apply_changes(forecast, trial), plan.payments, config).safe:
                low = mid
            else:
                high = mid - 1
        changes = [replace(c, new_amount=Decimal(low) * CENT) if c.event_id == original.event_id else c
                   for c in changes]
    return replace(plan, changes=changes)


def decide(request: Request, profile: FinancialProfile, forecast: Forecast,
           options: list[PaymentOption], config: EngineConfig | None = None) -> Decision:
    config = config or EngineConfig()
    if request.requested_amount <= ZERO:
        raise ValueError("Requested amount must be positive")
    if not 0 <= config.max_changes <= 3:
        raise ValueError("max_changes must be between zero and three")
    capacity = safe_amount(request, profile, forecast.entries, config)
    baseline_earliest = earliest_full_date(request, profile, forecast.entries, config)
    candidates = list(_safe_candidates(request, profile, forecast, options, config,
                                      capacity, baseline_earliest, []))
    scenarios_examined = 1
    # Every safe no-change candidate outranks every changed-spending candidate.
    if not candidates and config.max_changes:
        best_rank = None
        best_plans: list[CandidatePlan] = []
        for changes in _change_sets(_eligible_targets(profile, forecast), config.max_changes):
            scenarios_examined += 1
            for candidate in _safe_candidates(request, profile, forecast, options, config,
                                               capacity, baseline_earliest, changes):
                rank = _rank(candidate)
                if best_rank is None or rank < best_rank:
                    best_rank, best_plans = rank, [candidate]
                elif rank == best_rank:
                    best_plans.append(candidate)
        candidates = [_restore_spending(request, profile, forecast, plan, config) for plan in best_plans]
    diagnostics = {"change_scenarios_examined": scenarios_examined, "safe_candidate_count": len(candidates),
                   "capacity_without_spending_changes": str(capacity)}
    if candidates:
        # After the six required ranking rules, prefer fewer interventions and
        # stable event/action order to make equally ranked results reproducible.
        plan = min(candidates, key=lambda p: (_rank(p), len(p.changes), _change_key(p.changes)))
        if plan.changes or plan.method in {"partial_payment", "installments"}:
            status = "affordable_with_plan"
        elif plan.method == "full_payment":
            status = "affordable_now"
        else:
            status = "affordable_later"
    else:
        plan = CandidatePlan("not_recommended", [])
        status = "affordable_later" if baseline_earliest and baseline_earliest > request.request_date else "not_affordable"
        diagnostics["rejection_reason"] = (
            "Full-payment capacity exists, but no safe plan satisfies preferences, offer terms, and deadline."
            if baseline_earliest else "No safe eligible schedule completes the request within the forecast and deadline."
        )
        if baseline_earliest == request.request_date:
            diagnostics["status_interpretation"] = "capacity_now_but_no_eligible_payment_method"
    entries = apply_changes(forecast, plan.changes) if plan.changes else forecast.entries
    result = simulate(request, profile, entries, plan.payments, config, trace=True)
    return Decision(capacity, baseline_earliest, status, plan, result, diagnostics)

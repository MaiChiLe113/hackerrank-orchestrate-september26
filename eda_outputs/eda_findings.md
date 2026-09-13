# Executed EDA findings

Generated from `plan/eda.ipynb`. See the notebook for analysis code and charts.

## Integrity

### Extract Insights

- The supplied tables contain 250 evaluation requests and 25 samples; evaluation users contribute 23,054 events.
- Primary-key duplicates total 0; unresolved populated foreign keys total 0.
- All-data missing amounts: 16; 16 have existing linked PNGs. Their numeric values are still unextracted.

### Implications for the Financial Agent

- **DETERMINISTIC:** Validate identifiers, typed fields and joins at ingestion; preserve blank amounts until evidence resolves them.
- **LLM_REQUIRED:** Image-backed amounts need visual interpretation, including selecting the correct monetary field; EDA does not prove an LLM is the only possible extractor.

## Request Types

### Extract Insights

- Type counts range from 27 to 28 across 9 types.
- Largest type share is 11.2%; no single type dominates the evaluation population.

### Implications for the Financial Agent

- **DETERMINISTIC:** Apply the same safety constraints across all request types; do not infer affordability from type frequency.

## Amount Headroom

### Extract Insights

- 250 requests have positive headroom; 0 have zero or negative headroom and are excluded from ratios.
- 111/250 (44.4%) requests exceed raw headroom; median ratio is 0.88, 95th percentile 5.57.
- Within-currency max/median request ratios range from 2.5 to 4.8.

### Implications for the Financial Agent

- **DETERMINISTIC:** Raw headroom is balance minus reserve, not 90-day safe spending. Do not mix native currency amounts.
- **STATISTICAL:** Forecast commitments before interpreting a low request/headroom ratio as affordable.

## Deadlines

### Extract Insights

- Deadlines span 6–86 days; median 65 days.
- 55/250 (22.0%) must complete within 30 days.

### Implications for the Financial Agent

- **DETERMINISTIC:** Compute completion eligibility using each request date and deadline, independently of the forecast horizon.

## Preferences

### Extract Insights

- 227/250 (90.8%) users exclude at least one immediate payment method.
- 108 users (43.2%) accept exactly one method; request-level partial permission still needs a separate check.

### Implications for the Financial Agent

- **DETERMINISTIC:** Filter payment candidates by user acceptance before ranking; financial capacity and method eligibility are distinct.

## Event Status

### Extract Insights

- Settled rows account for 22883/23054 (99.3%) evaluation-user events.
- There are 62 pending and 8 unrealized rows; these require different cash treatment despite their small frequency.

### Implications for the Financial Agent

- **DETERMINISTIC:** Reserve pending debits; exclude pending credits and unrealized value from available cash. Failed collections may leave liabilities.

## Event Types

### Extract Insights

- expense contributes 18,677 events (81.0%).
- The three most frequent categories (groceries, transport, dining) account for 59.0% of events.

### Implications for the Financial Agent

- **STATISTICAL:** Recurring obligations and essential variable spending need separate estimation; an expense type does not establish recurrence.

## Event Counts

### Extract Insights

- Users have 56–129 rows; median 98, IQR 75–104.
- 60 users exceed the upper-quartile event count; row volume alone is not evidence complexity.

### Implications for the Financial Agent

- **DETERMINISTIC:** Retrieve and process per-user histories; avoid sending the entire event table to an interpreter.

## Recurrence Intervals

### Extract Insights

- 3022 candidates across 250 users have at least four distinct historical settlement dates.
- 64.7% have median intervals of 28–32 days; 1.4% fall in 6–8 days.
- 1195 (39.5%) have interval IQR above seven days; description-based groups are candidates, not confirmed obligations.

### Implications for the Financial Agent

- **STATISTICAL:** Infer cadence from history rather than assuming 30 days. Different descriptions may fragment one obligation; one description may merge multiple obligations.

## Recurrence Amounts

### Extract Insights

- Median amount CV is 0.064; 95th percentile is 0.189.
- 7/3022 measurable series exceed CV 0.25; 0 have undefined CV.

### Implications for the Financial Agent

- **STATISTICAL:** Compare robust amount estimators; point-error accuracy alone does not guarantee conservative expense reserves.

## Recurrence Backtest

### Extract Insights

- Lowest date error: fixed_30, 8.710 days across 3022 series and 6122 folds.
- Lowest amount error: historical_median, 0.0897 normalized absolute error across 3022 series.
- The benchmark contains 42,854 method/fold rows; missing target or unscalable amount folds are excluded. These are one-step historical results, not a 90-day safety evaluation.

### Implications for the Financial Agent

- **STATISTICAL:** Use the observed winners as baselines and inspect direction/cadence breakdowns; validate conservative tails and regime changes separately.
- **EDGE_CASE:** Calendar months clip at month end; fixed-day and calendar-month recurrence are distinct. Ties are reported, not hidden.

## Payment Options

### Extract Insights

- Evaluation requests have 719 offers, including 469 installment offers using ['28', '30', '31']-day intervals.
- Arithmetic discrepancies above 0.01 native units: 0 payment-total and 0 principal-plus-fee mismatches.
- 394 offers finish after the deadline; 310 use rejected methods; 381 fail the declared installment-count screen. These flags can overlap.

### Implications for the Financial Agent

- **DETERMINISTIC:** Calculate schedules with exact day intervals and validate fees, deadlines and preferences before cash simulation.
- **EDGE_CASE:** The month-limit screen uses payment count; resolve duration interpretation explicitly before implementing final eligibility.

## Message Triage

### Extract Insights

- 198 evaluation-user messages cover 198/250 users; 162 lack a direct event link.
- 31 messages are unmatched by the provisional multilingual patterns; 58 match multiple topics, which does not itself prove conflict.
- 0 messages are dated after their request day; same-day availability cannot be resolved from a date-only request.
- Three representative supplied images were visually inspected: a payslip, retail receipt, and utility receipt. EDA leaves all 11 evaluation-user missing amounts unresolved.

### Implications for the Financial Agent

- **LLM_REQUIRED:** Interpret amendments, effective dates and document-specific amounts with provenance; keyword triage is not fact extraction or proof of model necessity.
- **EDGE_CASE:** Filter evidence by availability; request dates lack time-of-day precision. Never export raw personal document details.

## Lifecycle

### Extract Insights

- 52 linked evaluation-user rows occur across 48 users (19.2%).
- The links exhibit 5 status-transition combinations; transitions describe row relationships, not resolved duplicates or cash obligations.

### Implications for the Financial Agent

- **DETERMINISTIC:** Traverse lifecycle links and retain cash status rather than dropping all linked rows.
- **LLM_REQUIRED:** Use evidence to distinguish cancellation, settlement, transfers and still-outstanding liabilities when structured rows are insufficient.

## Fx Coverage

### Extract Insights

- 134 evaluation-user cash rows need conversion; 0 lack the exact dated directed rate.
- The 134 supplied rates contain 0 duplicate date/pair keys; no inverse, nearest-date, or live-rate fallback is applied.

### Implications for the Financial Agent

- **DETERMINISTIC:** Use settlement-date directed rates; fail explicitly on unresolved required conversions rather than inventing rates.

## Pending Debits

### Extract Insights

- 55 pending debit rows affect 55 users; 1 users have incomplete amount/conversion evidence.
- Among 54 complete positive-headroom users, median pending debit/headroom is 5.1%; 0 exceed 25%.

### Implications for the Financial Agent

- **DETERMINISTIC:** Reserve pending debits per user; keep unknown reserves explicit rather than treating missing amounts as zero.

## Complexity

### Extract Insights

- Under the declared rule: 4 simple, 35 structured-complex, and 211 evidence-heavy evaluation requests.
- Most prevalent score components: irregular_recurrence (237/250) and restricted_methods (227/250). These are score contributions, not independently validated difficulty predictors.
- Across three irregularity thresholds, simple counts range 4–4; evidence-heavy counts remain fixed by definition.

### Implications for the Financial Agent

- **DETERMINISTIC:** Use explicit flags for routing and audit priorities, not an opaque model-based complexity label.
- **EDGE_CASE:** Evidence-heavy denotes evidence presence, not proven ambiguity; score rankings need validation against actual implementation effort.

## Complex Timeline

### Extract Insights

- request_64 has score 6/7 and 123 supplied events, selected by score, then event count, then request ID.
- 1 amounts cannot be plotted after exact-rate conversion; markers show cash-event records, not a reconstructed balance or recommended plan.

### Implications for the Financial Agent

- **DETERMINISTIC:** Build an auditable dated ledger before simulating balances; historical event plots must not be mistaken for current available cash.

# EDA Conclusions for Architecture Design

### 1. What can be handled completely deterministically?

Identifier joins, typed parsing, preference filters, exact FX lookups, cash-state rules, supplied option arithmetic/schedules, output validation and plan simulation once financial facts are normalized.

### 2. What requires statistical inference?

Cadence, variable essential spending, and recurrence amount estimation: 39.5% of candidate series have interval IQR above seven days.

### 3. What needs LLM or vision interpretation?

198 evaluation-user messages and image-backed amounts require semantic triage/interpretation. The three reviewed document types have different monetary fields. This EDA does not establish that an LLM outperforms rules/OCR; test that in later ablations.

### 4. What financial-event patterns are most common?

expense is the largest event type (18,677 rows); settled rows dominate. 64.7% of recurrence candidates have a monthly-range median gap.

### 5. What are the major outliers and edge cases?

Nonpositive headroom: 0 requests; missing exact FX: 0 foreign cash rows; linked lifecycle records: 52. Missing image amounts, failed-but-outstanding bills and conflicting dates remain explicit review cases.

### 6. Which recurrence heuristic performed best?

Date winner(s): fixed_30, MAE 8.710 days. Amount winner(s): historical_median, normalized error 0.0897. Equal-series one-step backtesting does not establish a universally safe forecast.

### 7. Which variables most strongly affect complexity?

The largest declared score components are irregular_recurrence (237 requests), restricted_methods (227 requests), messages (198 requests). This is definitional contribution, not causal importance or a validated difficulty model.

### 8. How many requests are simple versus evidence-heavy?

Simple: 4; evidence-heavy: 211; structured-complex: 35. Tiers are exhaustive and mutually exclusive under the published triage rule.

### 9. Which assumptions are dangerous to hardcode?

Every month is 30 days; all salary continues indefinitely; latest amount repeats; linked means duplicate; failed means no remaining liability; pending credit is available; blank equals zero; all users accept full payment; headroom equals safe capacity; every message is available at request time.

### 10. What should the final architecture contain?

Validated ingestion and provenance → evidence interpretation with uncertainty → lifecycle and cash-state reconciliation → statistical recurrence/essential-spending forecasts → deterministic 90-day simulator → eligible plan generation/ranking → independent invariant checks → grounded explanations, final output and usage reporting. Route evidence by explicit flags, and introduce an LLM only after measured rule/OCR failures.

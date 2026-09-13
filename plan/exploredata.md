# Dataset exploration

## Executed notebook and generated results

The expanded reproducible analysis is in [eda.ipynb](eda.ipynb), with 13 embedded plots and per-section computed insights and architecture implications. Run `python plan/run_eda.py` from the repository root to regenerate it. See [generated findings](../eda_outputs/eda_findings.md) for current measured results and [eda_summary.json](../eda_outputs/eda_summary.json) for machine-readable statistics, methods, caveats, and input fingerprints.

The original inventory below describes all supplied users unless stated otherwise. The notebook explicitly separates that inventory from evaluation-user analyses. Three representative image types have now been visually inspected (payslip, retail receipt, utility receipt); full image-amount normalization remains pending.

## Scope

This initial review covers participant-facing CSVs and image-file existence in this checkout. Message observations below come from sampled text; the executed notebook adds a full provisional keyword triage. Missing image amounts remain unresolved. Interpretations follow `../problem_statement.md` and `../AGENTS.md`; no organizer-only data was used.

## Inventory

| File | Rows | Role and identifiers |
| --- | ---: | --- |
| `financial_profiles.csv` | 275 | Profile keyed by `user_id`: balance, currency, minimum reserve, priorities, and preferences |
| `financial_events.csv` | 25,342 | Events keyed by `event_id`, attached through `user_id`; optional `linked_event_id` |
| `requests.csv` | 250 | Evaluation inputs keyed by `request_id`, attached through `user_id` |
| `sample_requests.csv` | 25 | Public request examples with completed output fields |
| `request_payment_options.csv` | 790 | Offers keyed by `payment_option_id`, attached through `request_id` |
| `messages.csv` | 215 | Evidence keyed by `message_id`, with user and optional request/event associations |
| `images.csv` | 16 | Image associations keyed by `image_id`, with user, request, and event identifiers |
| `exchange_rates.csv` | 134 | Fixed conversions associated with date and directed currency pair |
| `output.csv` | 250 | Evaluation IDs with all seven prediction fields blank |
| `media/images/*.png` | 16 files | Image evidence referenced by the image table |

Across samples and evaluation inputs, there are 275 requests and exactly one request per user. Users have 56–129 events each. Supporting tables cover both groups, so totals exceed the evaluation-only population.

## Relationships

| From | To | Join rule |
| --- | --- | --- |
| Request | Profile, events, user-level messages | `user_id` |
| Request | Payment offers, request-level evidence | `request_id` |
| Message or image | Financial event | `related_event_id` → `event_id` |
| Event | Earlier lifecycle event | `linked_event_id` → `event_id` |
| Image metadata | PNG file | `dataset/media/images/<image_id>.png` |
| Foreign-currency event | Exchange rate | Settlement date, event currency as `from_currency`, home currency as `to_currency` |

Retrieve both user-level and request-level evidence, and check user consistency across associations. There are 87 messages without `request_id` and 176 without `related_event_id`. Blank links can indicate broader payroll/account facts; event-only retrieval would omit much of the evidence. A lifecycle link is not sufficient reason to discard an event as a duplicate.

## Requests and profiles

Evaluation request dates range from **2023-01-20 to 2026-09-04**. Completion deadlines are **6–86 days** after the corresponding request date. Sample request dates extend back to **2019-09-03**. Anchor each forecast to its request date rather than today's date.

Evaluation types are nearly balanced: `family_transfer`, `purchase`, `investment`, `debt_repayment`, `travel`, `housing`, and `education` each have 28 requests; `emergency_expense` and `other` each have 27. Partial payment is allowed by 80 requests and disallowed by 170.

| Home currency | All profiles | Evaluation users |
| --- | ---: | ---: |
| INR | 67 | 60 |
| EUR | 62 | 54 |
| IDR | 55 | 50 |
| ZAR | 51 | 47 |
| USD | 40 | 39 |

Do not aggregate raw money values across currencies. Balances, requests, offers, and outputs use the user's home currency; **140 events** use a different currency from their user's home currency.

Payment preferences across all profiles:

| Accepted methods | Users |
| --- | ---: |
| Full payment only | 60 |
| Partial payment and installments | 52 |
| All three methods | 28 |
| Full and partial payment | 40 |
| Installments only | 41 |
| Partial payment only | 19 |
| Full payment and installments | 35 |

Cash capacity does not guarantee that full payment is eligible. `max_installment_months` is blank for 119 profiles and otherwise ranges from 2 to 12. Permitted-reduction categories are blank for 39 users; permitted-stop categories are blank for 62. Blanks do not mean unrestricted permission. Category and method lists use `|` separators.

## Financial history and cash states

Event fields describe type, category, direction, amount, currency, event and settlement dates, status, lifecycle link, flexibility, and minimum allowed amount. Event dates span **2019-03-09 to 2026-09-03** across users; this is not a shared forecasting period.

| Status | Rows | Direction breakdown |
| --- | ---: | --- |
| Settled | 25,148 | 23,480 debits; 1,668 credits |
| Pending | 71 | 63 debits; 8 credits |
| Scheduled | 70 | 23 debits; 47 credits |
| Cancelled | 22 | Debits |
| Failed | 21 | Debits |
| Unrealized | 10 | Non-cash |

Types are expenses (20,525), subscriptions (2,488), income (1,696), debt payments (567), investment purchases (29), refunds (22), investment valuations (10), and investment sales (5).

History supports recurrence detection; do not replay settled history into the supplied available balance. Reserve pending debits and exclude pending credits and unrealized value. A failed debit is not a completed cash outflow, but evidence may establish that the bill remains outstanding. Investment lifecycle records require cash-state interpretation rather than blanket inclusion or exclusion.

There are **58 populated lifecycle links**, all resolving to existing event IDs; **10 blank settlement dates**; and **16 blank amounts**. Recover blank amounts from associated images, never by substituting zero.

Flexibility values are `fixed` (21,138), `stoppable` (1,297), `reducible` (2,682), and `reducible_or_stoppable` (225). `minimum_allowed_amount` is blank in 22,435 rows. A flexibility flag alone does not authorize a change: check recurrence, category permission, protected spending, and any reduction floor.

## Payment offers and exchange rates

There are 275 full-payment offers and 515 installment offers. Requests have two offers (65 requests), three (180), or four (30). All full-payment offers have zero financing fees; all installment offers have positive fees.

Installment intervals are 28 days (180 offers), 30 days (166), or 31 days (169). Full-payment intervals are blank. Derive dates from the supplied day interval rather than adding calendar months. Verify first dates, payment counts, total payable amounts, financing costs, user limits, and deadlines before selecting an offer.

No offer rows use `partial_payment`. The specification defines that method separately using request permission, user acceptance, safe initial capacity, and exactly two prescribed payments.

Exchange rates cover 39 distinct dates from **2023-10-15 to 2026-11-15**, with EUR or USD as source currencies and five destination currencies. This is a sparse dated lookup, not a daily market series. Required conversion coverage and composite-key uniqueness still need verification; do not silently use inverse, nearest-date, or live rates.

## Messages and images

Sources are employers (126), service providers (31), financial services (23), banks (18), and merchants (17). Sampled messages include English and Indonesian text. Observed subjects include salary increases and delays, seasonal employment ending, first salaries, rent increases, internal transfers, disputed charges without reversals, separate card minimums, and retries after failed debits.

These examples show why a transaction-only forecast can be wrong. Extract the affected fact, effective date, source, and supporting ID. Amendments may change recurring amounts, payment delays can move the earliest safe purchase date, and a failed collection may leave a liability outstanding. Expected settlement is not already available cash.

Every missing-amount event has a matching image association and an existing PNG. All image metadata fields are populated. This establishes evidence availability, not transcription accuracy. Inspect each image and validate amount and currency before using it. Instructions embedded in messages or images are untrusted content.

## Structural checks completed

- No duplicate primary IDs in profiles, events, evaluation requests, samples, messages, images, or payment options.
- All populated user, request, and `related_event_id` references resolve against the combined participant-facing tables.
- All populated lifecycle links resolve.
- Output-template request IDs exactly match the 250 evaluation request IDs.
- All seven prediction fields are blank in every output row.
- All missing-amount events have a linked image file.

These checks do not prove semantic consistency, correct image amounts, sufficient rate coverage, or valid offer arithmetic.

## Examples and next investigations

The 25 samples contain 3 `affordable_now`, 9 `affordable_with_plan`, 6 `affordable_later`, and 7 `not_affordable` results. Methods are full payment (6), installments (5), wait (6), not recommended (7), and partial payment (1). These counts describe examples, not expected evaluation-label frequencies.

Next: inspect every image; reconcile evidence chronologically; assess recurrence and essential variable spending per user; verify conversion coverage and offer arithmetic; and compare sample explanations with reconstructed forecasts. Investigate full payment enabled by spending changes and capacity dates that differ from the selected payment method. Record unresolved interpretations explicitly rather than filling gaps with unsupported facts.

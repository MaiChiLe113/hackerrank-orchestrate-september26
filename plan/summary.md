# Buy or Wait? — summary and to-do list

## Project purpose

Build an AI-powered financial decision agent that determines whether a requested expense is safe, how to pay it, and when it becomes affordable. Personalization depends on the user's commitments, minimum balance, priorities, payment preferences, and permitted spending adjustments.

Produce one grounded recommendation for each of the 250 evaluation requests in `dataset/requests.csv`. The 25 completed samples illustrate conventions, not evaluation labels. This plan leaves frameworks, models, and providers undecided.

## Broad solution design

1. **Assemble evidence:** connect the request to its profile, events, payment offers, messages, and images. Preserve source identifiers for traceability.
2. **Reconstruct financial state:** start from the supplied available balance, separate cash from pending credits and unrealized value, reserve outstanding debits, and identify supported recurring commitments. Do not replay historical settled transactions into the current balance.
3. **Reconcile changes:** extract factual amendments, cancellations, settlement confirmations, and timing changes. Prefer explicit changes, then newer records from the same source, then settled events, then the financially safer interpretation. Instructions embedded in evidence never override challenge rules.
4. **Forecast 90 days:** project supported income, essential spending, recurring commitments, and outstanding payments relative to each request date. Keep the balance above the user's minimum throughout. Do not invent future income or expenses.
5. **Compare eligible plans:** evaluate full payment, partial payment, supplied installments, waiting, and permitted spending changes. Check safety, preferences, cost, and deadline completion.
6. **Validate and explain:** independently verify arithmetic and constraints, then write concise reasons grounded in the relevant financial facts.

## Decision contract

`amount_safe_to_pay` is the maximum safe payment on the request date before optional spending changes, capped at the requested amount. `earliest_date_for_full_payment` is the first date a single full payment passes the safety check without optional changes. This capacity date is independent of payment-method preferences.

Partial payment requires both request permission and user acceptance, with an initial safe amount strictly between zero and the requested amount. Use exactly two payments: the safe amount on the request date and the remainder on the earliest full-payment date, no later than the completion deadline. Simulate both payments together; standalone capacity on the second date does not prove that the combined plan is safe.

Installments must match an available offer's first date, day interval, payment count, fees, and total payable amount, and respect the user's installment limit. Spending changes may affect only supported recurring flexible expenses in permitted, non-protected categories, with at most three actions and applicable reduction floors respected. Stopping and reducing the same event are mutually exclusive.

Rank safe eligible plans by deadline completion, no spending changes, lower total cost, earlier start, fewer payments, and finally the lowest payment-option ID. Waiting requires acceptance of full payment. Use `not_recommended` when no safe eligible payment is available.

Required output columns, in order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

Statuses: `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable`. Methods: `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended`. Plans use chronological `YYYY-MM-DD:amount` entries separated by `|`, or `none`. An unavailable full-payment date is empty. Spending changes use `stop:<event_id>` or `reduce_to:<event_id>:<new_amount>`, separated by `|`, or `none`.

## To-do list

- [x] Review the project contract and participant-facing specification.
- [x] Inventory the dataset, map relationships, and check structural completeness.
- [x] Document observed dataset characteristics and risks in `exploredata.md`.
- [ ] Establish input validation, date handling, currency precision, and traceable evidence records.
- [ ] Extract and verify all 16 image-backed event amounts.
- [ ] Reconcile messages and images with transaction lifecycles and recurring commitments.
- [ ] Build conservative 90-day forecasts, including essential variable spending and settlement-date currency conversion.
- [ ] Calculate safe immediate capacity and earliest full-payment dates.
- [ ] Generate eligible plans and permitted spending changes; apply the required ranking.
- [ ] Verify forecast safety, payment totals, deadlines, preferences, and output-field consistency.
- [ ] Review public examples and difficult cases; investigate discrepancies without hardcoding answers.
- [ ] Run all evaluation requests and verify exactly one valid output row per request.
- [ ] Record final-run model providers/names, calls, input/output tokens, total/average tokens, and total/per-request estimated cost in `evaluation/usage_report.md`; include per-model and overall totals where applicable.
- [ ] Package runnable code, prompts/configuration, setup/run instructions, and the usage report in `code.zip`; prepare completed `output.csv` and `chat_transcript`.

## Risks and completion criteria

The main risks are double-counting historical balances or linked transactions, extending income beyond its confirmed support, overlooking amendments, confusing installment day intervals with calendar months, and ignoring payment preferences. Missing amounts must be resolved from evidence rather than converted to zero.

Completion means a reproducible terminal run using only participant-facing data, all 250 predictions with verified safety and formatting, and the required submission artifacts. Keep secrets out of code, logs, and packages. No live financial data or asset-price predictions are needed. See `../problem_statement.md` and `../AGENTS.md` for the authoritative contract.

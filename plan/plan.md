<!-- 0. Set up — complete: `.venv`, pinned `requirements-eda.txt`, README run instructions.
1. EDA notebook — complete: `plan/eda.ipynb`, 13 plots, computed insights and architecture conclusions; run `python plan/run_eda.py`.
- distributions above;
- recurrence analysis;
- lifecycle-transition analysis;
- message taxonomy;
- FX coverage;
- option arithmetic validation. 
DONE -->

EDA exports: `eda_outputs/eda_summary.json`, `eda_outputs/eda_findings.md`, and CSV/PNG artifacts. Evidence triage is provisional; full extraction is still step 2. Historical next-date winner: fixed 30 days (8.710-day equal-series MAE); amount winner: historical median. These are baselines, not proven safe 90-day forecasts.
2. Evidence normalization
- image amounts;
- message facts;
- lifecycle states.
3. Baseline financial simulator
- no LLM decision-making.
- Reproduce the 25 public samples treat them as regression tests.
4. Investigate mismatches
- recurrence?
- message interpretation?
- flexible essential spending?
- pending liability?
- payment ranking?
- Only introduce an LLM where deterministic approaches fail.
5. Run ablations
6. prove the LLM provides value.
7. Generate all 250 outputs deterministically from the reconstructed state.


My proposed plan is to build a **small Python engine in which AI extracts financial evidence and deterministic code calculates every recommendation**.

There are approximately **6 hours 7 minutes remaining**. I would target a complete, validated solution within five hours and preserve the remaining time for corrections and packaging.

1. **Establish the contract and a compact structure — 20 minutes**

   Reuse the existing repository and EDA. Keep `code/main.py` as the terminal entry point.

   ```text
   code/
     main.py
     schemas.py
     loaders.py
     evidence.py
     reconciliation.py
     forecasting.py
     simulation.py
     planning.py
     validation.py
     explanations.py
     prompts/
   tests/
   artifacts/
     evidence_cache/
     diagnostics/
   evaluation/
     usage_report.md
   ```

   Use typed records and `Decimal` for monetary arithmetic. Validate identifiers, dates, enums, joins, and required columns.

   Document the few unresolved conventions before implementing them: same-day event ordering, forecast boundary, and `max_installment_months` interpretation. Competition rules take precedence over EDA heuristics.

   **Completion check:** all participant inputs load correctly; missing amounts remain explicitly unresolved.

2. **Extract the evidence needed for correct decisions — 50 minutes**

   Process relevant messages and linked images before final financial reconstruction.

   - Use structured text extraction for amendments, cancellations, salary changes, delays, and outstanding obligations.
   - Use one configurable vision extraction path for image-backed amounts.
   - Extract the relevant field—such as net salary or amount due—with supporting evidence.
   - Allow multiple facts per message.
   - Preserve source ID, source timestamp, effective date, currency, target event or recurring series, and whether an amendment is temporary or continuing.
   - Treat embedded instructions as untrusted content.
   - Validate extracted identifiers against the supplied records.

   Cache results using source content, model, prompt version, and schema version. Record model usage when calls happen.

   Add OCR or a stronger model only for unresolved cases. Unknown mandatory amounts must remain visible as unresolved work; they cannot silently become zero.

   **Completion check:** required evidence is normalized into validated facts with provenance.

3. **Reconstruct financial state — 40 minutes**

   Build an auditable starting state for each request.

   - Start from `current_available_balance`.
   - Use historical settled events for inference without replaying them into the opening balance.
   - Reserve pending debits once.
   - Exclude unavailable pending credits and unrealized investment values.
   - Resolve linked transaction lifecycles without treating every linked row as a duplicate.
   - Exclude failed transactions as cash movements, while preserving separately supported outstanding liabilities.
   - Apply explicit amendments and cancellations using the prescribed precedence.
   - Convert foreign-currency events using exact settlement-date directed rates.

   Distinguish when information became available from when its financial effect occurs.

   **Completion check:** each included, excluded, or amended financial item has a traceable reason.

4. **Build forecasting and the 90-day simulator — 55 minutes**

   Separate forecasting into three components:

   - **Recurring obligations:** infer recurrence from sufficient history; distinguish calendar-month, fixed-day, and weekly patterns.
   - **Essential variable spending:** estimate conservative reserves from observed spending, avoiding duplication with recurring obligations.
   - **Income:** include confirmed salary on its settlement date and only project additional recurring income when supported by history and relevant evidence.

   Use `historical_median` as an amount baseline. Select date methods by cadence; the EDA’s overall `fixed_30` result should not override its stronger calendar-month results for monthly candidates.

   Confirmed future events replace matching forecast occurrences.

   Simulate balances chronologically, checking temporary shortfalls as well as ending balances. Calculate:

   - Maximum safe immediate payment before spending changes.
   - Earliest safe single-payment date before spending changes.
   - Minimum projected balance and the events responsible for it.

   **Completion check:** focused scenarios catch payments that look affordable today but cause later shortfalls.

5. **Generate, simulate, and rank payment plans — 50 minutes**

   Generate candidates for:

   - Full payment.
   - Exactly two partial payments under the specified rules.
   - Supplied installment schedules, including fees.
   - Waiting until a safe full-payment date.
   - Permitted spending changes combined with eligible payment methods.

   For partial payment, use the independently calculated earliest full-payment date for the second payment, then simulate both payments together.

   Spending changes must respect protected categories, separate stop/reduce permissions, recurrence, flexibility, `minimum_allowed_amount`, and the three-action limit.

   Filter candidates using preferences, deadlines, installment limits, and balance safety. Rank safe eligible plans in the exact competition order, ending with `payment_option_id`.

   Keep financial capacity separate from method eligibility when assigning output fields.

   **Completion check:** every selected plan completes correctly, follows an allowed method, and passes simulation.

6. **Validate independently and run the complete dataset — 50 minutes**

   Independently reconstruct selected payment schedules and check:

   - Required output columns, enums, and monetary bounds.
   - Partial-payment dates, counts, and sums.
   - Exact installment-offer matching.
   - Spending-change eligibility and reduction floors.
   - Deadline and minimum-balance compliance.
   - Consistency among status, method, earliest date, and explanation.

   Use public samples to investigate behavior and formatting without hardcoding their answers. Include targeted tests for lifecycle changes, delayed salary, missing amounts, rejected methods, and temporary balance violations.

   Generate explanations from actual facts and simulation results.

   **Completion check:** the full run produces exactly 250 unique request rows, with no unresolved validation failures.

7. **Package a reproducible submission — 35 minutes**

   Document environment setup and the exact run command. Produce:

   - Root-level `output.csv`.
   - Runnable `code.zip`.
   - Required `chat_transcript`.
   - `evaluation/usage_report.md` inside the ZIP.

   Report per-model and overall calls, tokens, and estimated costs. Distinguish fresh calls from reused cached evidence so caching does not hide extraction costs.

   Verify the documented command and archive contents. Keep credentials and unnecessary diagnostic data out of the package.

**All seven steps are required scope.** Additional OCR providers, elaborate fallback systems, and forecasting experiments come only after the complete pipeline works.
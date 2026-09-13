"""Build and execute the EDA notebook using this Python interpreter."""
from pathlib import Path
import sys
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager

ROOT = Path(__file__).resolve().parents[1]
sections = [
    ('Input inventory and structural integrity', 'integrity', 'Count supplied rows, missing cells, duplicate IDs, orphan references and image coverage. This section uses all supplied tables.'),
    ('Request distributions, money and deadlines', 'requests', 'Count evaluation request types, compare amounts within currency, divide by positive raw headroom, and calculate date differences. Headroom is not safe-to-pay capacity.'),
    ('Payment preferences', 'preferences', 'Count accepted method combinations and restrictive preferences among evaluation users.'),
    ('Financial-event distributions', 'events', 'Count status, type, category and per-user event volume for evaluation users, including rare cash states.'),
    ('Recurring candidates and chronological backtesting', 'recurrence', 'Group settled pre-request cash history by user, normalized description, category, direction and currency. Aggregate same-day amounts within groups (all-missing stays missing); require four distinct dates. Use expanding windows with three prior observations minimum. Compare four date and three amount heuristics, average folds within series then weight series equally. No sample answers are used. Cadence breakdowns are descriptive full-history strata, not predictors supplied to folds.'),
    ('Payment-option schedules and arithmetic', 'options', 'Check native-unit arithmetic to 0.01 absolute tolerance, exact-day schedules, deadlines, accepted methods and a declared payment-count screen for installment limits.'),
    ('Message taxonomy, image triage and lifecycle transitions', 'evidence', 'Apply provisional multilingual regex topic flags to evaluation-user messages; allow multiple topics and retain unmatched text as unclassified. Count linked status transitions. Three supplied images were manually inspected for document structure only; full fact normalization is deferred.'),
    ('Exchange-rate coverage and pending reserves', 'fx_pending', 'Match directed settlement-date rates without fallback. Sum known pending debits per user; exclude incomplete totals and nonpositive headroom from ratio statistics.'),
    ('Request complexity and example timeline', 'complexity', 'Give one point each for messages, images/missing amounts, lifecycle links, foreign currency, pending debits, restricted methods and irregular candidates (gap IQR > 7 days or amount CV > 0.25). Evidence-heavy means any of the first three evidence flags; simple means no evidence flags and score <= 1; remaining requests are structured-complex. Compare alternate irregularity thresholds. Select timeline by descending score, event count, then ascending request ID.'),
]
nb = nbformat.v4.new_notebook()
nb.metadata['kernelspec'] = {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'}
nb.cells = [nbformat.v4.new_markdown_cell('# Buy or Wait? — Reproducible EDA\n\nThis executed notebook studies participant-facing data, not hidden labels. Request-level analyses use evaluation users; inventory and representative image triage explicitly include all supplied data. Each analysis produces quantified observations and implementation implications. There are 13 figures. Re-run from the repository root with `python plan/run_eda.py`.\n\nThe analysis implementation lives in `plan/eda_analysis.py`; each section below displays its exact source before execution. Outputs are exported to `eda_outputs/`. No financial recommendations are produced.'),
nbformat.v4.new_code_cell("from pathlib import Path\nimport sys, inspect\nroot = Path.cwd() if (Path.cwd() / 'dataset').is_dir() else Path.cwd().parent\nsys.path.insert(0, str(root / 'plan'))\nfrom eda_analysis import Analysis\nfrom IPython.display import Code, display\na = Analysis()")]
for title, method, description in sections:
    nb.cells.append(nbformat.v4.new_markdown_cell(f'## {title}\n\n### Query / Analysis\n\n{description}\n\n### Visualization and computed results\n\nPlots are included where useful; detailed checks are exported as CSV tables.'))
    nb.cells.append(nbformat.v4.new_code_cell(f'display(Code(inspect.getsource(Analysis.{method}), language="python"))\na.{method}()'))
nb.cells.append(nbformat.v4.new_markdown_cell('# EDA Conclusions for Architecture Design\n\nThe following answers are generated from the computed section results, not preset conclusions.'))
nb.cells.append(nbformat.v4.new_code_cell('a.conclusions()'))
path = ROOT / 'plan/eda.ipynb'
nbformat.write(nb, path)
print(f'Created {path}', flush=True)
manager = KernelManager(kernel_name='python3')
manager.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}']
client = NotebookClient(nb, timeout=600, resources={'metadata': {'path': str(ROOT)}}, km=manager)
try:
    client.execute()
finally:
    nbformat.write(nb, path)
    manager.shutdown_kernel(now=True)
print('Executed notebook and exported artifacts successfully.', flush=True)

"""The golden test: the whole pipeline against the ground truth.

Every synthetic file is uploaded as one job, exactly as a user would, and the
resulting ledger is compared row by row and field by field with
``expected_transactions.csv``. The metrics the brief asks for are computed here
and printed, so a regression shows up as a number moving rather than as a wall
of diffs.

Every number below is earned by code. No model is called anywhere in this
pipeline: statements, the card PDF, the spreadsheet, the emails and the bills
are all parsed deterministically, so the same files always produce the same
ledger and the metrics mean what they say.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.pipeline import JobResult, export_job, run_job
from ingestion.schema import ALL_TRANSACTION_COLUMNS, OBLIGATION_COLUMNS, RESERVED_COLUMNS

COMPARED_COLUMNS = tuple(c for c in ALL_TRANSACTION_COLUMNS if c not in RESERVED_COLUMNS)


def _text(value) -> str:
    return "" if value is None else str(value)


@pytest.fixture(scope="module")
def job(synthetic_dir: Path) -> JobResult:
    files = sorted(
        p for p in synthetic_dir.iterdir()
        if p.is_file() and not p.name.startswith("expected")
    )
    files += sorted((synthetic_dir / "emails").glob("*.eml"))
    files += sorted((synthetic_dir / "bills").glob("*.pdf"))
    return run_job(files, job_id="golden", base_dir=synthetic_dir)


@pytest.fixture(scope="module")
def expected(synthetic_dir: Path) -> dict[str, dict]:
    with (synthetic_dir / "expected_transactions.csv").open(encoding="utf-8") as fh:
        return {r["txn_id"]: r for r in csv.DictReader(fh)}


@pytest.fixture(scope="module")
def expected_obligations(synthetic_dir: Path) -> dict[str, dict]:
    with (synthetic_dir / "expected_obligations.csv").open(encoding="utf-8") as fh:
        return {r["bill_id"]: r for r in csv.DictReader(fh)}


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


@dataclass
class PRF:
    """Precision / recall over a set comparison."""

    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        total = self.true_positive + self.false_positive
        return self.true_positive / total if total else 1.0

    @property
    def recall(self) -> float:
        total = self.true_positive + self.false_negative
        return self.true_positive / total if total else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 1.0

    def __str__(self) -> str:
        return (
            f"P {self.precision * 100:6.2f}%  R {self.recall * 100:6.2f}%  "
            f"F1 {self.f1 * 100:6.2f}%   (tp {self.true_positive}, "
            f"fp {self.false_positive}, fn {self.false_negative})"
        )


def compare_sets(produced: set, truth: set) -> PRF:
    return PRF(
        true_positive=len(produced & truth),
        false_positive=len(produced - truth),
        false_negative=len(truth - produced),
    )


def _flag_set(rows, flag: str) -> set[str]:
    return {t.txn_id for t in rows if getattr(t, flag)}


def _flag_set_truth(rows: dict[str, dict], flag: str) -> set[str]:
    return {k for k, r in rows.items() if r[flag] == "True"}


def _link_pairs(rows) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for t in rows:
        for ref in t.linked_sources.split("|"):
            if ref:
                out.add((t.txn_id, ref))
    return out


def _link_pairs_truth(rows: dict[str, dict]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for txn_id, r in rows.items():
        for ref in r["linked_sources"].split("|"):
            if ref:
                out.add((txn_id, ref))
    return out


@dataclass
class GoldenMetrics:
    row_recall: float
    row_precision: float
    field_accuracy: float
    field_errors: Counter
    reconciliation_pass_rate: float | None
    duplicates: PRF
    links: PRF
    transfers: PRF
    refunds: PRF
    obligations_matched: int
    obligations_expected: int


def compute_metrics(job: JobResult, expected: dict[str, dict]) -> GoldenMetrics:
    produced = {t.txn_id: t for t in job.transactions}
    matched = set(produced) & set(expected)

    field_errors: Counter = Counter()
    for txn_id in matched:
        dumped = produced[txn_id].model_dump(mode="json")
        want = expected[txn_id]
        for column in COMPARED_COLUMNS:
            got_value, want_value = _text(dumped.get(column)), _text(want.get(column))
            if column == "extraction_confidence":
                got_value = f"{float(got_value or 0):.2f}"
                want_value = f"{float(want_value or 0):.2f}"
            if got_value != want_value:
                field_errors[column] += 1

    compared = len(matched) * len(COMPARED_COLUMNS)
    totals = job.report["totals"]

    # a duplicate is a transaction described by more than one statement row
    def duplicate_pairs(link_pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
        by_txn: dict[str, list[str]] = {}
        for txn_id, ref in link_pairs:
            by_txn.setdefault(txn_id, []).append(ref)
        return {
            (txn_id, ref)
            for txn_id, refs in by_txn.items()
            if len(refs) > 1
            for ref in sorted(refs)
            if ref.endswith(tuple(f"#{n}" for n in range(10))) or ".csv#" in ref
        }

    produced_links = _link_pairs(job.transactions)
    truth_links = _link_pairs_truth(expected)

    return GoldenMetrics(
        row_recall=len(matched) / len(expected) if expected else 1.0,
        row_precision=len(matched) / len(produced) if produced else 1.0,
        field_accuracy=(compared - sum(field_errors.values())) / compared if compared else 1.0,
        field_errors=field_errors,
        reconciliation_pass_rate=totals["reconciliation_pass_rate"],
        duplicates=compare_sets(
            duplicate_pairs(produced_links), duplicate_pairs(truth_links)
        ),
        links=compare_sets(produced_links, truth_links),
        transfers=compare_sets(
            _flag_set(job.transactions, "is_transfer"), _flag_set_truth(expected, "is_transfer")
        ),
        refunds=compare_sets(
            _flag_set(job.transactions, "is_refund"), _flag_set_truth(expected, "is_refund")
        ),
        obligations_matched=0,
        obligations_expected=0,
    )


@pytest.fixture(scope="module")
def metrics(job: JobResult, expected: dict[str, dict]) -> GoldenMetrics:
    return compute_metrics(job, expected)


def test_report_metrics(job: JobResult, metrics: GoldenMetrics, capsys):
    """Not an assertion -- this prints the scorecard the brief asks for."""
    totals = job.report["totals"]
    lines = [
        "",
        "=" * 68,
        "  FinPilot ingestion -- golden run",
        "=" * 68,
        f"  files processed            {totals['files_processed']}",
        f"  rows produced              {totals['rows']}",
        "",
        f"  row recall                 {metrics.row_recall * 100:6.2f}%",
        f"  row precision              {metrics.row_precision * 100:6.2f}%",
        f"  field-level accuracy       {metrics.field_accuracy * 100:6.2f}%"
        f"   over {len(COMPARED_COLUMNS)} columns",
        f"  reconciliation pass rate   {(metrics.reconciliation_pass_rate or 0) * 100:6.2f}%",
        "",
        f"  duplicate detection        {metrics.duplicates}",
        f"  source linking             {metrics.links}",
        f"  transfer detection         {metrics.transfers}",
        f"  refund detection           {metrics.refunds}",
        "",
        f"  duplicates merged          {totals['duplicates_merged']}",
        f"  transfers / card payments  {totals['transfers_found']} / "
        f"{totals['card_payments_found']}",
        f"  emails linked / added      {totals['emails_linked']} / "
        f"{totals['emails_added_as_rows']}",
        f"  sheet rows linked / kept   {totals['spreadsheet_rows_linked']} / "
        f"{totals['spreadsheet_rows_kept']}",
        f"  bills found / paid         {totals['bills_found']} / {totals['bills_paid']}",
        f"  line items extracted       {totals['items_extracted']}",
        f"  rows needing review        {totals['rows_needing_review']}",
        "=" * 68,
    ]
    if metrics.field_errors:
        lines.append("  field errors:")
        for column, count in metrics.field_errors.most_common():
            lines.append(f"    {column:<28}{count:>5}")
        lines.append("=" * 68)
    with capsys.disabled():
        print("\n".join(lines))


# --------------------------------------------------------------------------
# the assertions
# --------------------------------------------------------------------------


def test_every_expected_row_is_produced(job: JobResult, expected: dict[str, dict]):
    produced = {t.txn_id for t in job.transactions}
    missing = set(expected) - produced
    assert not missing, "\n".join(
        f"missing {expected[k]['date']} {expected[k]['amount']} {expected[k]['source_ref']}"
        for k in sorted(missing)[:10]
    )


def test_no_rows_are_invented(job: JobResult, expected: dict[str, dict]):
    produced = {t.txn_id: t for t in job.transactions}
    extra = set(produced) - set(expected)
    assert not extra, "\n".join(
        f"extra {produced[k].date} {produced[k].amount} {produced[k].source_ref}"
        for k in sorted(extra)[:10]
    )


def test_field_level_accuracy_is_perfect(metrics: GoldenMetrics):
    assert not metrics.field_errors, dict(metrics.field_errors)
    assert metrics.field_accuracy == 1.0


def test_every_statement_reconciles(job: JobResult):
    assert metrics_pass_rate(job) == 1.0


def metrics_pass_rate(job: JobResult) -> float:
    return job.report["totals"]["reconciliation_pass_rate"]


def test_duplicate_detection(metrics: GoldenMetrics, job: JobResult):
    assert job.report["totals"]["duplicates_merged"] == 19
    assert metrics.duplicates.precision == 1.0
    assert metrics.duplicates.recall == 1.0


def test_transfer_detection(metrics: GoldenMetrics, job: JobResult):
    assert metrics.transfers.precision == 1.0
    assert metrics.transfers.recall == 1.0
    totals = job.report["totals"]
    assert totals["transfers_found"] == 1, "one self-transfer between the two banks"
    assert totals["card_payments_found"] == 1, "one credit-card bill payment"


def test_refund_detection(metrics: GoldenMetrics, job: JobResult):
    assert metrics.refunds.precision == 1.0
    assert metrics.refunds.recall == 1.0
    assert job.report["totals"]["refunds_found"] == 1


def test_a_repayment_from_a_friend_is_not_a_refund(job: JobResult):
    """Money coming back from a person is settling up, not a merchant refund."""
    for txn in job.transactions:
        if txn.is_refund:
            assert txn.counterparty_type != "person", txn.source_ref


def test_linking_accuracy(metrics: GoldenMetrics):
    assert metrics.links.precision == 1.0
    assert metrics.links.recall == 1.0


def test_emails_are_linked_not_duplicated(job: JobResult):
    totals = job.report["totals"]
    assert totals["emails_linked"] == 22
    assert totals["emails_added_as_rows"] == 1, "only the one dated after the statements"
    assert totals["emails_unmatched"] == 1, "the one inside the covered period is reported"


def test_an_unmatched_email_inside_the_covered_period_is_reported_not_invented(job: JobResult):
    unmatched = [i for i in job.issues if i.code == "email_unmatched"]
    assert len(unmatched) == 1
    assert "no row was invented" in unmatched[0].message


def test_spreadsheet_rows(job: JobResult):
    totals = job.report["totals"]
    assert totals["spreadsheet_rows_linked"] == 3, "three re-describe statement rows"
    assert totals["spreadsheet_rows_kept"] == 6, "six genuine cash spends survive"
    cash = [t for t in job.transactions if t.account_type == "cash"]
    assert len(cash) == 6


def test_obligations_match_ground_truth(job: JobResult, expected_obligations: dict[str, dict]):
    produced = {o.bill_id: o for o in job.obligations}
    assert set(produced) == set(expected_obligations)
    for bill_id, want in expected_obligations.items():
        got = produced[bill_id].model_dump(mode="json")
        for column in OBLIGATION_COLUMNS:
            assert _text(got.get(column)) == want[column], f"{want['biller']}.{column}"


def test_bill_payments_are_matched(job: JobResult):
    paid = [o for o in job.obligations if o.status == "paid"]
    assert len(paid) == 2
    ids = {t.txn_id for t in job.transactions}
    for obligation in paid:
        assert obligation.paid_txn_id in ids
    unpaid = [o for o in job.obligations if o.status != "paid"]
    assert len(unpaid) == 1 and unpaid[0].status == "overdue"


def test_nothing_needs_review(job: JobResult):
    flagged = [t for t in job.transactions if t.needs_review]
    assert not flagged, [t.source_ref for t in flagged]
    errors = [i for i in job.issues if i.severity == "error"]
    assert not errors, [i.message for i in errors]


# --------------------------------------------------------------------------
# idempotency and privacy
# --------------------------------------------------------------------------


def test_rerunning_the_job_is_identical(synthetic_dir: Path, job: JobResult):
    """Principle 5: re-running any stage produces the same result."""
    files = sorted(
        p for p in synthetic_dir.iterdir()
        if p.is_file() and not p.name.startswith("expected")
    )
    files += sorted((synthetic_dir / "emails").glob("*.eml"))
    files += sorted((synthetic_dir / "bills").glob("*.pdf"))
    again = run_job(files, job_id="golden-2", base_dir=synthetic_dir)
    assert [t.txn_id for t in again.transactions] == [t.txn_id for t in job.transactions]
    assert [t.linked_sources for t in again.transactions] == [
        t.linked_sources for t in job.transactions
    ]
    assert [o.bill_id for o in again.obligations] == [o.bill_id for o in job.obligations]


def test_uploading_the_same_file_twice_is_a_no_op(synthetic_dir: Path):
    one = synthetic_dir / "sbi_savings_jun_aug.csv"
    once = run_job([one], job_id="a", base_dir=synthetic_dir)
    twice = run_job([one, one], job_id="b", base_dir=synthetic_dir)
    assert len(twice.transactions) == len(once.transactions)
    assert twice.duplicates_skipped == [one.name]


SECRETS = ("50100234567890", "20012345678901", "4477123456784417",
           "ramesh.kumar@", "swiggy.payu@", "9820045612@", "8765432109@",
           "900012345678")


def test_no_unmasked_identifier_reaches_the_exports(job: JobResult, tmp_path: Path):
    paths = export_job(job, tmp_path)
    for name, path in paths.items():
        blob = path.read_text(encoding="utf-8")
        for secret in SECRETS:
            assert secret not in blob, f"{secret} leaked into {name}"


# --------------------------------------------------------------------------
# the exports
# --------------------------------------------------------------------------


def test_exports_are_written_in_the_contract_shape(job: JobResult, tmp_path: Path):
    paths = export_job(job, tmp_path)
    with paths["transactions"].open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == list(ALL_TRANSACTION_COLUMNS)
        rows = list(reader)
    assert len(rows) == len(job.transactions)
    for column in RESERVED_COLUMNS:
        assert all(r[column] == "" for r in rows), f"{column} is for a later stage"

    with paths["obligations"].open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == list(OBLIGATION_COLUMNS)
        assert len(list(reader)) == len(job.obligations)


def test_report_has_per_file_and_total_stats(job: JobResult, tmp_path: Path):
    import json

    paths = export_job(job, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert len(report["files"]) == job.report["totals"]["files_processed"]
    for entry in report["files"]:
        assert {"filename", "rows_extracted", "extraction_method", "status", "issues"} <= set(entry)
    # only documents that stated balances can actually be reconciled; the
    # spreadsheet carries an unchecked result rather than a missing one
    checked = [f for f in report["files"] if (f["reconciliation"] or {}).get("checked")]
    assert len(checked) == 4, "three bank statements and the card statement"
    for entry in checked:
        assert entry["reconciliation"]["ok"] is True
        assert entry["reconciliation"]["difference"] == "0.00"
    unchecked = [
        f for f in report["files"]
        if f["reconciliation"] and not f["reconciliation"]["checked"]
    ]
    assert [f["filename"] for f in unchecked] == ["personal_expenses.xlsx"]


def test_money_is_exported_at_two_decimals(job: JobResult, tmp_path: Path):
    paths = export_job(job, tmp_path)
    with paths["transactions"].open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            assert Decimal(row["amount"]) == Decimal(row["amount"]).quantize(Decimal("0.01"))
            assert row["amount"].count(".") == 1 and len(row["amount"].split(".")[1]) == 2

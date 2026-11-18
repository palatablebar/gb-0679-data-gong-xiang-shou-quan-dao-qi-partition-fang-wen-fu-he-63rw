import csv, io, json
import pytest
from app.modules.m8_shares import rules
from app.pipeline import calculate, load_sample


@pytest.fixture
def run(): return calculate(load_sample("dirty.csv"))


def policy(**overrides):
    base = {"resources": ["orders"], "partitions": ["2026-09"]}
    base.update(overrides)
    return rules.normalize_policy(base)


def test_published_summary_is_balanced_and_hides_notes(run):
    summary = rules.build_summary(run, "partner-a", policy())
    assert summary["status"] == "published"
    assert summary["reconciliation"]["balanced"] is True
    assert summary["reconciliation"]["published_records"] == len(summary["rows"]) > 0
    # 隐藏部分仍可核对：发布 + 隐藏 = 来源，且两侧合计等于来源合计
    assert summary["reconciliation"]["published_records"] + summary["reconciliation"]["withheld_records"] == 32
    assert (int(summary["reconciliation"]["in_scope_expected_total"])
            + int(summary["reconciliation"]["withheld_expected_total"])
            == int(summary["reconciliation"]["expected_total"]))
    assert summary["hidden"]["redacted_fields"]["note"] > 0
    assert all("note" not in row for row in summary["rows"])
    assert all(group["records"] for group in summary["hidden"]["breakdown"].values())


def test_hidden_breakdown_reconciles_by_reason(run):
    summary = rules.build_summary(run, "partner-a", policy())
    breakdown = summary["hidden"]["breakdown"]
    assert sum(group["records"] for group in breakdown.values()) == summary["hidden"]["records"]
    assert sum(group["violations"] for group in breakdown.values()) == summary["hidden"]["withheld_violations"]


def test_unexplained_violation_blocks_whole_summary(run):
    summary = rules.build_summary(run, "partner-b", rules.normalize_policy({"resources": ["orders", "customers"]}))
    assert summary["status"] == "blocked" and summary["publishable"] is False
    assert summary["rows"] == []
    assert summary["differences"]["counts"]["unexplained_violations"] == 16
    assert any(b["code"] == "BLOCK" for b in summary["basis"])
    with pytest.raises(Exception):
        rules.export_csv({**summary, "attempt_status": "blocked"})


def test_explanation_publishes_and_is_carried(run):
    p = policy(partitions=["2026-10"])
    unexplained = rules.build_summary(run, "partner-a", p)["differences"]["unexplained_violations"]
    assert len(unexplained) == 8
    p["explanations"] = {row["event_id"]: f"批准依据 {row['event_id']}" for row in unexplained}
    summary = rules.build_summary(run, "partner-a", p)
    assert summary["status"] == "published"
    explained = summary["differences"]["explained_violations"]
    assert len(explained) == 8
    assert all(row["explanation"] for row in summary["rows"] if row["event_id"] in {e["event_id"] for e in explained})


def test_explanation_for_out_of_scope_event_rejected(run):
    p = policy(partitions=["2026-09"], explanations={"00001": "越界解释"})
    with pytest.raises(Exception) as error:
        rules.build_summary(run, "partner-a", p)
    assert error.value.code == "BAD_EXPLANATION"


def test_fingerprint_is_order_independent(run):
    a = policy(resources=["customers", "orders"])
    b = rules.normalize_policy({"resources": ["orders", "customers"], "partitions": ["2026-09"]})
    assert rules.fingerprint(run["run_id"], "x", a) == rules.fingerprint(run["run_id"], "x", b)


def test_unknown_scope_resource_rejected():
    with pytest.raises(Exception) as error:
        rules.normalize_policy({"resources": ["salaries"]})
    assert error.value.code == "UNKNOWN_SCOPE"


def test_bad_partition_rejected():
    with pytest.raises(Exception) as error:
        rules.normalize_policy({"resources": ["orders"], "partitions": ["2026-13"]})
    assert error.value.code == "BAD_POLICY"


def test_export_contains_counts_on_every_row(run):
    p = policy(partitions=["2026-10"])
    unexplained = rules.build_summary(run, "partner-a", p)["differences"]["unexplained_violations"]
    p["explanations"] = {row["event_id"]: "已批准" for row in unexplained}
    summary = rules.build_summary(run, "partner-a", p)
    summary.update({"version": 1, "attempt_status": "published"})
    rows = list(csv.reader(io.StringIO(rules.export_csv(summary).lstrip("﻿"))))
    header = rows[0]
    assert header[:5] == ["share_version", "run_id", "input_sha256", "rule_version", "recipient"]
    assert "withheld_records" in header and "balanced" in header
    tail = dict(zip(header, rows[-1]))
    assert rows[-1][5] == "reconciliation"
    assert tail["kind"] == "reconciliation" and tail["balanced"] == "True"
    assert int(tail["published_records"]) + int(tail["withheld_records"]) == int(tail["total_records"])
    assert "note" not in header

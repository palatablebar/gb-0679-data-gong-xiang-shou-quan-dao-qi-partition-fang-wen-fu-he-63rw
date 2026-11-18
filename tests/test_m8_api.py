import csv, io
from app.pipeline import calculate, load_sample
from app import store


def _run(client):
    return client.post("/api/runs", json={"sample": "dirty.csv"}).get_json()["run_id"]


def _policy(**overrides):
    base = {"resources": ["orders"], "partitions": ["2026-09"]}
    base.update(overrides)
    return base


def test_share_retry_reuses_attempt_and_version(client):
    run_id = _run(client)
    first = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy()})
    second = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy()})
    assert first.status_code == second.status_code == 200
    assert first.get_json()["attempt_id"] == second.get_json()["attempt_id"]
    assert first.get_json()["version"] == second.get_json()["version"] == 1
    assert len(store.share_versions(run_id, "partner-a")) == 1


def test_blocked_attempt_stores_no_version_and_keeps_old(client):
    run_id = _run(client)
    good = _policy(resources=["customers"])
    assert client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": good}).status_code == 200
    bad = _policy(partitions=[])
    blocked = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": bad})
    assert blocked.status_code == 409
    assert blocked.get_json()["version"] is None
    assert blocked.get_json()["attempt_status"] == "blocked"
    # 旧版仍可访问并导出
    old = client.get(f"/api/runs/{run_id}/shares/partner-a/1")
    assert old.status_code == 200 and old.get_json()["attempt_status"] == "published"
    export = client.get(f"/api/runs/{run_id}/shares/partner-a/1/export")
    assert export.status_code == 200
    assert export.headers["Content-Disposition"].endswith('partner-a-v1.csv"')


def test_retry_blocked_attempt_is_idempotent(client):
    run_id = _run(client)
    bad = _policy(partitions=[])
    first = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-b", "policy": bad})
    second = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-b", "policy": bad})
    assert first.status_code == second.status_code == 409
    assert first.get_json()["attempt_id"] == second.get_json()["attempt_id"]
    assert store.share_overview(run_id)[0]["blocked_attempts"][0]["fingerprint"] == first.get_json()["fingerprint"][:12]


def test_published_versions_increment(client):
    run_id = _run(client)
    a = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy(resources=["customers"])})
    b = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy(resources=["orders"], partitions=["2026-09"])})
    assert (a.get_json()["version"], b.get_json()["version"]) == (1, 2)


def test_workbench_reports_hidden_counts_basis_and_unexplained(client):
    run_id = _run(client)
    client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy()})
    client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-b", "policy": _policy(partitions=[])})
    board = client.get(f"/api/runs/{run_id}/shares").get_json()
    assert {r["recipient"] for r in board["recipients"]} == {"partner-a", "partner-b"}
    a = next(r for r in board["recipients"] if r["recipient"] == "partner-a")
    assert a["latest_published"]["hidden"]["redacted_fields"]["note"] > 0
    assert a["latest_published"]["reconciliation"]["withheld_records"] > 0
    assert any(b["code"] == "SCOPE" for b in a["latest_published"]["basis"])
    b = next(r for r in board["recipients"] if r["recipient"] == "partner-b")
    assert b["latest_published"] is None
    assert b["blocked_attempts"][0]["counts"]["unexplained_violations"] == 16
    assert b["blocked_attempts"][0]["unexplained_violations"]


def test_workbench_page_renders(client):
    run_id = _run(client)
    client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy()})
    page = client.get("/shares")
    assert page.status_code == 200 and "partner-a".encode() in page.data


def test_export_rejects_unpublished_and_unknown_version(client):
    run_id = _run(client)
    client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy(partitions=[])})
    assert client.get(f"/api/runs/{run_id}/shares/partner-a/1/export").status_code == 404
    assert client.get(f"/api/runs/{run_id}/shares/nobody/1/export").status_code == 404


def test_note_exposure_requires_opt_in(client):
    run_id = _run(client)
    hidden = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy()}).get_json()
    shown = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-b", "policy": _policy(include_note=True)}).get_json()
    assert all("note" not in row for row in hidden["rows"])
    assert any(row.get("note") for row in shown["rows"])
    assert shown["hidden"]["redacted_fields"] == {}


def test_bad_recipient_and_scope_inputs(client):
    run_id = _run(client)
    assert client.post(f"/api/runs/{run_id}/shares", json={"recipient": "", "policy": _policy()}).status_code == 422
    assert client.post(f"/api/runs/{run_id}/shares", json={"recipient": "a/b", "policy": _policy()}).status_code == 422
    assert client.post(f"/api/runs/{run_id}/shares", json={"recipient": "x", "policy": {"resources": ["secret"]}}).status_code == 422


def test_share_export_reconciles(client):
    run_id = _run(client)
    summary = client.post(f"/api/runs/{run_id}/shares", json={"recipient": "partner-a", "policy": _policy()}).get_json()
    data = client.get(f"/api/runs/{run_id}/shares/partner-a/{summary['version']}/export").data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == summary["reconciliation"]["published_records"] + 2
    assert rows[-1][5] == "reconciliation"

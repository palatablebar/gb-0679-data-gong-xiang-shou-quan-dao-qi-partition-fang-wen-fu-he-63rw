"""对外共享摘要：按接收方策略做最小必要裁剪，并保证隐藏部分可核对。

设计裁决：
- 范围内记录按外部字段白名单输出原值，供接收方追查越权；
- 备注等受限字段默认隐藏，只输出隐藏行数与字段计数，不输出隐藏行的原值；
- 发布 + 隐藏 = 来源总数，且两侧的应/实合计必须对平，隐藏部分因此仍可核对；
- 范围内存在未解释的“越权放行”时，整个接收方摘要阻断发布；
- 同一请求指纹只在首次落版，重试复用已记录结果，不新增版本；
- 新版本发布失败时保留旧的已发布版本，旧版仍可按版本号导出。
"""
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone

from app.config import RULE_VERSION
from app.errors import DomainError
from app.modules.m2_resources.rules import RESOURCES

# 对外可发布的原值字段：接收方据此追查越权，不含备注与内部证据结构。
EXTERNAL_FIELDS = (
    "event_id", "principal", "resource", "partition", "access_time",
    "observed", "reported", "expected", "difference", "violation",
    "status", "effect", "rule_evidence",
)
# 默认按最小必要原则隐藏的字段。
RESTRICTED_FIELDS = ("note",)
VIOLATION = "越权放行"


def _text_list(raw, field):
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(x, str) or not x.strip() for x in raw):
        raise DomainError("BAD_POLICY", f"{field} 需要非空字符串列表")
    return sorted({x.strip() for x in raw})


def _partition(value):
    if (not isinstance(value, str) or len(value) != 7 or value[4] != "-"
            or not value[:4].isdigit() or not value[5:].isdigit()
            or not 1 <= int(value[5:]) <= 12):
        raise DomainError("BAD_POLICY", "分区必须为 YYYY-MM")
    return value


def normalize_policy(raw):
    """把接收方策略规范成稳定结构，供指纹与执行共用。"""
    if not isinstance(raw, dict):
        raise DomainError("BAD_POLICY", "共享策略需要对象")
    resources = []
    for item in _text_list(raw.get("resources"), "resources"):
        canonical = RESOURCES.get(item)
        if not canonical:
            raise DomainError("UNKNOWN_SCOPE", "策略资源未登记")
        resources.append(canonical)
    if not resources:
        raise DomainError("BAD_POLICY", "resources 至少需要一项")
    partitions = sorted({_partition(x.strip()) for x in _text_list(raw.get("partitions"), "partitions")})
    principals = _text_list(raw.get("principals"), "principals")
    include_note = raw.get("include_note", False)
    if not isinstance(include_note, bool):
        raise DomainError("BAD_POLICY", "include_note 必须为布尔值")
    explanations = raw.get("explanations") or {}
    if not isinstance(explanations, dict):
        raise DomainError("BAD_POLICY", "explanations 需要对象")
    clean = {}
    for key, value in explanations.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip():
            raise DomainError("BAD_POLICY", "裁决依据需要 event_id 到说明文本")
        clean[key.strip()] = value.strip()
    return {
        "resources": sorted(set(resources)),
        "partitions": partitions,
        "principals": principals,
        "include_note": include_note,
        "explanations": {k: clean[k] for k in sorted(clean)},
    }


def fingerprint(run_id, recipient, policy):
    canonical = json.dumps(
        {"run_id": run_id, "rule_version": RULE_VERSION, "recipient": recipient, "policy": policy},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def scope_reason(row, policy):
    if row.get("resource") not in policy["resources"]:
        return "OUT_OF_SCOPE_RESOURCE"
    if policy["partitions"] and row.get("partition") not in policy["partitions"]:
        return "OUT_OF_SCOPE_PARTITION"
    if policy["principals"] and row.get("principal") not in policy["principals"]:
        return "OUT_OF_SCOPE_PRINCIPAL"
    return None


def _as_int(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _sum(rows, key):
    return sum(_as_int(row.get(key, "0")) for row in rows)


def _scope_text(policy):
    return "资源 {}；分区 {}；主体 {}".format(
        "、".join(policy["resources"]),
        "、".join(policy["partitions"]) or "全部",
        "、".join(policy["principals"]) or "全部",
    )


def _difference_evidence(row):
    return {
        "event_id": row.get("event_id", ""),
        "principal": row.get("principal", ""),
        "resource": row.get("resource", ""),
        "partition": row.get("partition", ""),
        "rule_evidence": row.get("rule_evidence", ""),
        "difference": row.get("difference", ""),
        "violation": row.get("violation", ""),
    }


def build_summary(run, recipient, policy):
    """生成某接收方的受控摘要；不写入存储。"""
    rows = run["stages"][6]["rows"]
    total = len(rows)
    in_scope, withheld = [], []
    reason_counts = Counter()
    for row in rows:
        reason = scope_reason(row, policy)
        if reason is None:
            in_scope.append(row)
        else:
            withheld.append(row)
            reason_counts[reason] += 1

    violations = [row for row in in_scope if row.get("violation") == VIOLATION]
    explained, unexplained = {}, []
    for row in violations:
        event_id = str(row.get("event_id", ""))
        text = policy["explanations"].get(event_id)
        if text:
            explained[event_id] = text
        else:
            unexplained.append(row)
    stray = sorted(set(policy["explanations"]) - set(explained))
    if stray:
        raise DomainError("BAD_EXPLANATION", "裁决依据只能对应范围内的越权放行记录")

    breakdown = {}
    for reason in sorted(reason_counts):
        bucket = [row for row in withheld if scope_reason(row, policy) == reason]
        breakdown[reason] = {
            "records": len(bucket),
            "violations": sum(1 for row in bucket if row.get("violation") == VIOLATION),
            "expected_total": str(_sum(bucket, "expected")),
            "reported_total": str(_sum(bucket, "reported")),
        }

    published_rows = []
    for row in in_scope:
        item = {field: row.get(field, "") for field in EXTERNAL_FIELDS}
        if policy["include_note"]:
            item["note"] = row.get("note", "")
        event_id = str(row.get("event_id", ""))
        if event_id in explained:
            item["explanation"] = explained[event_id]
        published_rows.append(item)

    redacted_note = 0 if policy["include_note"] else sum(
        1 for row in in_scope if str(row.get("note", "")).strip()
    )
    source_expected = _sum(in_scope, "expected") + _sum(withheld, "expected")
    source_reported = _sum(in_scope, "reported") + _sum(withheld, "reported")
    metrics = run["stages"][5].get("metrics", {})
    source_totals_match = (
        (metrics.get("expected_total") is None or str(metrics["expected_total"]) == str(source_expected))
        and (metrics.get("reported_total") is None or str(metrics["reported_total"]) == str(source_reported))
    )
    balanced = (
        len(in_scope) + len(withheld) == total
        and _sum(in_scope, "expected") + _sum(withheld, "expected") == source_expected
        and source_totals_match
    )
    blocked = bool(unexplained) or not balanced

    basis = [
        {"code": "SCOPE", "detail": _scope_text(policy)},
        {"code": "MINIMIZATION", "detail": (
            f"备注按最小必要隐藏，涉及 {redacted_note} 行" if redacted_note
            else "无受限备注需要隐藏"
        )},
        {"code": "RECONCILIATION", "detail": (
            f"来源 {total} = 范围内 {len(in_scope)} + 范围外隐藏 {len(withheld)}；应/实合计已对平"
            if balanced else f"来源 {total} 与范围/隐藏行数或应实合计未对平"
        )},
        {"code": "LINEAGE", "detail": (
            f"批次 {run['run_id']} / 规则 {run['manifest']['rule_version']} / "
            f"输入摘要 {run['manifest']['input_sha256'][:12]}…"
        )},
    ]
    for event_id, text in explained.items():
        basis.append({"code": "JUSTIFICATION", "detail": f"{event_id}：{text}"})
    if blocked:
        reasons = []
        if unexplained:
            reasons.append(f"{len(unexplained)} 条范围内越权放行缺少裁决依据")
        if not balanced:
            reasons.append("行数或应实合计未对平，隐藏部分不可核对")
        basis.append({"code": "BLOCK", "detail": "；".join(reasons) + "，摘要不可发布"})

    differences = {
        "published": [_difference_evidence(row) for row in in_scope if row.get("status") == "差异"],
        "explained_violations": [
            {**_difference_evidence(row), "explanation": explained[str(row.get("event_id", ""))]}
            for row in violations if str(row.get("event_id", "")) in explained
        ],
        "unexplained_violations": [_difference_evidence(row) for row in unexplained],
        "counts": {
            "published_differences": sum(1 for row in in_scope if row.get("status") == "差异"),
            "explained_violations": len(explained),
            "unexplained_violations": len(unexplained),
            "withheld_violations": sum(1 for row in withheld if row.get("violation") == VIOLATION),
        },
    }

    return {
        "run_id": run["run_id"],
        "rule_version": run["manifest"]["rule_version"],
        "input_sha256": run["manifest"]["input_sha256"],
        "recipient": recipient,
        "status": "blocked" if blocked else "published",
        "publishable": not blocked,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": policy,
        "reconciliation": {
            "total_records": total,
            "in_scope_records": len(in_scope),
            "published_records": 0 if blocked else len(in_scope),
            "blocked_records": len(in_scope) if blocked else 0,
            "withheld_records": len(withheld),
            "balanced": balanced,
            "explained": (
                f"来源 {total} = 范围内 {len(in_scope)} + 范围外隐藏 {len(withheld)}"
                if not blocked else
                f"来源 {total} = 范围内 {len(in_scope)}（阻断未发布） + 范围外隐藏 {len(withheld)}"
            ),
            "expected_total": str(source_expected),
            "reported_total": str(source_reported),
            "in_scope_expected_total": str(_sum(in_scope, "expected")),
            "in_scope_reported_total": str(_sum(in_scope, "reported")),
            "withheld_expected_total": str(_sum(withheld, "expected")),
            "withheld_reported_total": str(_sum(withheld, "reported")),
        },
        "hidden": {
            "records": len(withheld),
            "reasons": dict(sorted(reason_counts.items())),
            "breakdown": breakdown,
            "withheld_violations": differences["counts"]["withheld_violations"],
            "redacted_fields": {field: redacted_note for field in RESTRICTED_FIELDS} if not policy["include_note"] else {},
            "hides_original_values": True,
        },
        "basis": basis,
        "differences": differences,
        "rows": [] if blocked else published_rows,
    }


def export_csv(summary):
    """把已发布摘要导出为 CSV；每一行都携带核对计数。"""
    if summary.get("attempt_status") != "published":
        raise DomainError("NOT_PUBLISHED", "该版本未发布，不能导出")
    recon = summary["reconciliation"]
    fields = list(EXTERNAL_FIELDS)
    if summary["policy"].get("include_note"):
        fields.append("note")
    fields.append("explanation")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    meta = [
        "share_version", "run_id", "input_sha256", "rule_version", "recipient", "kind",
    ]
    counts = [
        "total_records", "in_scope_records", "published_records", "withheld_records",
        "withheld_reasons", "withheld_violations", "redacted_fields", "balanced",
    ]
    writer.writerow(meta + fields + counts)
    counts_values = [
        recon["total_records"], recon["in_scope_records"], recon["published_records"],
        recon["withheld_records"], json.dumps(summary["hidden"]["reasons"], ensure_ascii=False),
        summary["hidden"]["withheld_violations"],
        json.dumps(summary["hidden"]["redacted_fields"], ensure_ascii=False),
        recon["balanced"],
    ]
    for row in summary["rows"]:
        writer.writerow([
            summary["version"], summary["run_id"], summary["input_sha256"],
            summary["rule_version"], summary["recipient"], "record",
        ] + [row.get(field, "") for field in fields] + counts_values)
    writer.writerow([
        summary["version"], summary["run_id"], summary["input_sha256"],
        summary["rule_version"], summary["recipient"], "reconciliation",
    ] + [""] * len(fields) + counts_values)
    return "﻿" + stream.getvalue()


def workbench(run, overview):
    """审计工作台数据：接收方版本、隐藏计数、裁决依据与未解释差异。"""
    rows = run["stages"][6]["rows"]
    violations = [_difference_evidence(row) for row in rows if row.get("violation") == VIOLATION]
    return {
        "run_id": run["run_id"],
        "run_version": run.get("version"),
        "resources": [
            {"value": "orders", "label": "订单 orders"},
            {"value": "customers", "label": "客户 customers"},
        ],
        "partitions": sorted({row.get("partition", "") for row in rows}),
        "principals": sorted({row.get("principal", "") for row in rows}),
        "violations": violations,
        "recipients": overview,
    }

from flask import Blueprint,request,jsonify,current_app
from app.errors import DomainError
from app import store
from app.modules.m8_shares import rules
bp=Blueprint("shares",__name__)

def _run(run_id):
    return store.get(run_id)

@bp.get("/api/shares")
def latest_overview():
    run=store.latest()
    if not run: raise DomainError("NOT_FOUND","尚无批次",404)
    return jsonify(rules.workbench(run,store.share_overview(run["run_id"])))

@bp.get("/api/runs/<run_id>/shares")
def overview(run_id):
    run=_run(run_id)
    return jsonify(rules.workbench(run,store.share_overview(run_id)))

@bp.post("/api/runs/<run_id>/shares")
def publish(run_id):
    run=_run(run_id)
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    recipient=payload.get("recipient")
    if not isinstance(recipient,str) or not recipient.strip(): raise DomainError("BAD_RECIPIENT","请提供接收方")
    recipient=recipient.strip()
    if len(recipient)>64 or "/" in recipient or "\\" in recipient: raise DomainError("BAD_RECIPIENT","接收方名称不能包含路径字符且不超过64字符")
    policy=rules.normalize_policy(payload.get("policy"))
    fingerprint=rules.fingerprint(run_id,recipient,policy)
    summary=rules.build_summary(run,recipient,policy)
    summary["fingerprint"]=fingerprint
    status="published" if summary["publishable"] else "blocked"
    code=None if summary["publishable"] else "UNEXPLAINED_VIOLATION"
    if not summary["publishable"] and not summary["reconciliation"]["balanced"]:
        code="UNBALANCED_SHARE"
    record=store.record_share(run_id,recipient,fingerprint,status,code,summary)
    body=dict(record["payload"])
    body.update({"attempt_id":record["attempt_id"],"attempt_status":record["status"],"attempt_code":record["code"],"created_at":record["created_at"]})
    response=jsonify(body)
    if record["status"]!="published": response.status_code=409
    return response

@bp.get("/api/runs/<run_id>/shares/<recipient>/<int:version>")
def detail(run_id,recipient,version):
    _run(run_id)
    record=store.get_share(run_id,recipient,version)
    body=dict(record["payload"])
    body.update({"attempt_id":record["attempt_id"],"attempt_status":record["status"],"attempt_code":record["code"],"created_at":record["created_at"]})
    return jsonify(body)

@bp.get("/api/runs/<run_id>/shares/<recipient>/<int:version>/export")
def export(run_id,recipient,version):
    _run(run_id)
    record=store.get_share(run_id,recipient,version)
    body=dict(record["payload"])
    body.update({"attempt_status":record["status"],"version":version})
    data=rules.export_csv(body)
    filename=f"share-{run_id}-{recipient}-v{version}.csv"
    return current_app.response_class(data,mimetype="text/csv",headers={"Content-Disposition":f'attachment; filename="{filename}"'})

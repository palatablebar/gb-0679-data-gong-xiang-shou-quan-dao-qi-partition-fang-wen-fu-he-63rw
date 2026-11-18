import sqlite3,json
from datetime import datetime,timezone
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS shares (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,recipient TEXT NOT NULL,version INTEGER,fingerprint TEXT NOT NULL,status TEXT NOT NULL,code TEXT,payload TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,recipient,fingerprint))")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS shares_published_version ON shares(run_id,recipient,version) WHERE version IS NOT NULL")
    return db
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)


def _share_row(row):
    return {"attempt_id":row[0],"run_id":row[1],"recipient":row[2],"version":row[3],"fingerprint":row[4],"status":row[5],"code":row[6],"payload":json.loads(row[7]),"created_at":row[8]}


def find_share(run_id,recipient,fingerprint):
    with connect() as db:
        row=db.execute("SELECT id,run_id,recipient,version,fingerprint,status,code,payload,created_at FROM shares WHERE run_id=? AND recipient=? AND fingerprint=?",(run_id,recipient,fingerprint)).fetchone()
    return _share_row(row) if row else None


def record_share(run_id,recipient,fingerprint,status,code,payload):
    """同一指纹只落一条记录；发布版本号按接收方递增，失败尝试不占版本号。

    BEGIN IMMEDIATE 把「查重 + 取号 + 写入」放在同一写事务内；并发下若撞上取号竞争，
    唯一索引会拒绝重复版本号，这里重读版本号后有限重试，保证重试不会新增版本。
    """
    for _ in range(5):
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing=db.execute("SELECT id,run_id,recipient,version,fingerprint,status,code,payload,created_at FROM shares WHERE run_id=? AND recipient=? AND fingerprint=?",(run_id,recipient,fingerprint)).fetchone()
            if existing: return _share_row(existing)
            version=None
            if status=="published":
                version=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM shares WHERE run_id=? AND recipient=? AND status='published'",(run_id,recipient)).fetchone()[0]
            now=datetime.now(timezone.utc).isoformat()
            body=json.dumps({**payload,"version":version},ensure_ascii=False)
            try:
                db.execute("INSERT INTO shares(run_id,recipient,version,fingerprint,status,code,payload,created_at) VALUES (?,?,?,?,?,?,?,?)",(run_id,recipient,version,fingerprint,status,code,body,now))
            except sqlite3.IntegrityError:
                continue
            row=db.execute("SELECT id,run_id,recipient,version,fingerprint,status,code,payload,created_at FROM shares WHERE run_id=? AND recipient=? AND fingerprint=?",(run_id,recipient,fingerprint)).fetchone()
            return _share_row(row)
    raise DomainError("SHARE_CONFLICT","共享摘要正在被并发写入，请重试",409)


def share_versions(run_id,recipient):
    with connect() as db:
        rows=db.execute("SELECT id,run_id,recipient,version,fingerprint,status,code,payload,created_at FROM shares WHERE run_id=? AND recipient=? ORDER BY id",(run_id,recipient)).fetchall()
    return [_share_row(row) for row in rows]


def get_share(run_id,recipient,version):
    with connect() as db:
        row=db.execute("SELECT id,run_id,recipient,version,fingerprint,status,code,payload,created_at FROM shares WHERE run_id=? AND recipient=? AND version=? AND status='published'",(run_id,recipient,version)).fetchone()
    if not row: raise DomainError("SHARE_NOT_FOUND","该接收方版本不存在或未发布",404)
    return _share_row(row)


def share_overview(run_id):
    """按接收方汇总：已发布版本、未发布尝试与隐藏计数，供审计工作台使用。"""
    with connect() as db:
        rows=db.execute("SELECT id,run_id,recipient,version,fingerprint,status,code,payload,created_at FROM shares WHERE run_id=? ORDER BY id",(run_id,)).fetchall()
    grouped={}
    for row in rows:
        item=_share_row(row);recipient=item["recipient"]
        summary=item["payload"];entry=grouped.setdefault(recipient,{"recipient":recipient,"published_versions":[],"blocked_attempts":[],"latest_published":None,"latest_attempt":None})
        differences=summary.get("differences",{})
        brief={"attempt_id":item["attempt_id"],"version":item["version"],"status":item["status"],"code":item["code"],"fingerprint":item["fingerprint"][:12],"created_at":item["created_at"],"filled_explanations":len(summary.get("policy",{}).get("explanations",{})),"reconciliation":summary.get("reconciliation"),"hidden":summary.get("hidden"),"counts":differences.get("counts",{}),"unexplained_violations":differences.get("unexplained_violations",[]),"explained_violations":differences.get("explained_violations",[]),"basis":summary.get("basis",[])}
        entry["latest_attempt"]=brief
        if item["status"]=="published":
            entry["published_versions"].append(brief);entry["latest_published"]=brief
        else:
            entry["blocked_attempts"].append(brief)
    return [grouped[key] for key in sorted(grouped)]


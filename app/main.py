import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from urllib.parse import quote
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from .db import connect, init_db
from .recordings import safe_recording_path
from .report_mailer import DB_PATH, JST, send_monthly_report, send_report, validate_email

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="仮想点呼システム", lifespan=lifespan)

@app.get("/api/openwebui/status")
def openwebui_status():
    from .openwebui_client import status
    return status()

@app.get("/api/metahuman/status")
def metahuman_status():
    from .metahuman_bridge import status
    return status()

@app.get("/metahuman", response_class=HTMLResponse)
def metahuman_control():
    return """<!doctype html><html lang="ja"><meta charset="utf-8"><title>MetaHuman接続確認</title>
    <style>body{font-family:sans-serif;max-width:850px;margin:40px auto;padding:20px;line-height:1.8}button{padding:12px}</style>
    <h1>MetaHuman接続確認（試験導入）</h1><p><a href="/">管理UIへ戻る</a></p>
    <p>現在の3D方式とは別の、Unreal Engineが描画する人物映像の接続モードです。</p>
    <p>前提：MetaHumanシーン、Live Link音声入力、OBS仮想カメラ、Windows音声ループバック、ホストブリッジ。</p>
    <p id="status">確認中…</p><button id="check">再確認</button>
    <p>接続済みは映像・音声の通信経路を示します。MetaHumanの顔・口の動作を保証する表示ではありません。
    初回はOBSとUnreal画面で映像・音声入力を確認してください。未接続ではモードを保存できません。</p>
    <p>接続手順：プロジェクト内 METAHUMAN_SETUP.md。設定後は管理UIでMetaHumanを選び、Meetを再接続してください。</p>
    <script>async function check(){try{const r=await fetch('/api/metahuman/status');const s=await r.json();
    document.getElementById('status').textContent=(s.ready?'接続済み：':'未接続：')+s.detail;
    }catch(e){document.getElementById('status').textContent='状態取得に失敗しました';}}
    document.getElementById('check').onclick=check;check();</script></html>"""

@app.get("/avatar-3d/asset/{name}")
def avatar_asset(name: str):
    if name not in ("three.min.js", "avatar3d.js", "avatar-portrait.png"):
        raise HTTPException(404)
    return FileResponse(os.path.join(os.path.dirname(__file__), name), media_type="image/png" if name.endswith(".png") else "application/javascript")

@app.get("/avatar-3d", response_class=HTMLResponse)
def avatar_preview():
    return """<!doctype html><html lang="ja"><meta charset="utf-8"><title>3D点呼スタッフ</title>
    <style>body{font-family:sans-serif;background:#eef3f6;text-align:center}canvas{width:min(960px,100%);border-radius:18px}button{padding:12px;margin:12px}</style>
    <h1>点呼スタッフ・自然な正面表示</h1><p>元画像を使った正面用の立体メッシュ。口のテストは無音デモです。</p>
    <canvas id="avatar"></canvas><p id="status">準備中</p><button id="test">口の動作テスト</button>
    <script src="/avatar-3d/asset/three.min.js"></script><script src="/avatar-3d/asset/avatar3d.js"></script>
    <script>(async()=>{try{const a=createTenkoAvatar3D(document.getElementById('avatar'));await a.ready;let until=0;
    document.getElementById('test').onclick=()=>until=performance.now()+5000;
    function draw(t){const talking=t<until;a.render(t/1000,talking?(1+Math.sin(t/90))*0.35:0,talking);requestAnimationFrame(draw);}
    requestAnimationFrame(draw);document.getElementById('status').textContent='3D描画中';
    }catch(e){document.getElementById('status').textContent='3D描画エラー: '+e.message;}})();</script></html>"""

templates = Jinja2Templates(directory="app/templates")
HEALTH_LOG_DIR = os.getenv("HEALTH_LOG_DIR", "/data/health-logs")

def _read_health_log():
    directory = os.path.abspath(HEALTH_LOG_DIR)
    current, history = {}, []
    try:
        with open(os.path.join(directory, "current.json"), encoding="utf-8") as stream:
            current = json.load(stream)
    except (OSError, ValueError):
        pass
    try:
        files = sorted((n for n in os.listdir(directory) if n.endswith(".jsonl")), reverse=True)[:3]
        for name in files:
            with open(os.path.join(directory, name), encoding="utf-8") as stream:
                rows = stream.readlines()
            for line in reversed(rows):
                if len(history) >= 100:
                    break
                try:
                    history.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return current, history

@app.get("/health-monitor", response_class=HTMLResponse)
def health_monitor_page(request: Request):
    current, history = _read_health_log()
    return templates.TemplateResponse(request, "health_monitor.html", {"current": current, "history": history})

@app.get("/api/health-monitor")
def health_monitor_api():
    current, _ = _read_health_log()
    return current or {"status": "starting", "issues": [], "components": {}}

class ScenarioIn(BaseModel):
    name: str
    questions: list[dict]
class StartIn(BaseModel):
    participant_id: str
    participant_name: str = ""
    scenario_id: int = 1
    meet_url: str = ""
class AnswerIn(BaseModel):
    question_key: str
    question_text: str
    answer_text: str
    is_ok: bool | None = None


CLUSTER_SHARED_DIR = os.getenv("CLUSTER_SHARED_DIR", "/cluster-shared")

def _cluster_view():
    with connect() as con:
        settings = dict(con.execute("SELECT key,value FROM settings WHERE key LIKE 'cluster_%'").fetchall())
    nodes = []
    node_dir = os.path.join(CLUSTER_SHARED_DIR, "nodes")
    try:
        for name in os.listdir(node_dir):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(node_dir, name), encoding="utf-8") as stream:
                    node = json.load(stream)
                stamp = datetime.fromisoformat(node["timestamp"].replace("Z", "+00:00"))
                age = max(0, round((datetime.now(stamp.tzinfo) - stamp).total_seconds()))
                node["age_seconds"] = age
                node["online"] = age <= 45
                try:
                    status_path = os.path.join(CLUSTER_SHARED_DIR, "status", f"{node['node_id']}.json")
                    with open(status_path, encoding="utf-8") as status_stream:
                        node_status = json.load(status_stream)
                    node["is_leader"] = bool(node_status.get("is_leader"))
                    node["witness_ok"] = bool(node_status.get("witness_ok"))
                    node["sync_audit"] = node_status.get("sync_audit", {})
                    node["identity_audit"] = node_status.get("identity_audit", {})
                    node["configuration_audit"] = node_status.get("configuration_audit", {})
                    node["status_timestamp"] = node_status.get("timestamp", "")
                    try:
                        recovery_path = os.path.join(CLUSTER_SHARED_DIR, "recovery", node["node_id"], "current.json")
                        with open(recovery_path, encoding="utf-8") as recovery_stream:
                            node["last_recovery"] = json.load(recovery_stream)
                    except (OSError, ValueError):
                        node["last_recovery"] = {}
                except (OSError, ValueError, KeyError):
                    node["sync_audit"] = {}
                    node["identity_audit"] = {}
                    node["configuration_audit"] = {}
                    node["last_recovery"] = {}
                nodes.append(node)
            except (OSError, ValueError, KeyError):
                continue
    except OSError:
        pass
    preferred = ""
    try:
        with open(os.path.join(CLUSTER_SHARED_DIR, "control.json"), encoding="utf-8") as stream:
            preferred = json.load(stream).get("preferred_node", "")
    except (OSError, ValueError):
        pass
    local = {
        "node_id": settings.get("cluster_node_id", os.getenv("CLUSTER_NODE_ID", "primary-pc")),
        "node_name": settings.get("cluster_node_name", os.getenv("CLUSTER_NODE_NAME", "Primary PC")),
        "leader_id": settings.get("cluster_leader_id", ""),
        "is_leader": settings.get("cluster_is_leader") == "1",
        "witness_ok": settings.get("cluster_witness_ok") == "1",
        "enabled": settings.get("cluster_node_enabled", "1") == "1",
        "last_cycle_at": settings.get("cluster_last_cycle_at", ""),
        "last_exported": settings.get("cluster_last_exported", "0"),
        "last_imported": settings.get("cluster_last_imported", "0"),
    }
    return local, sorted(nodes, key=lambda x: (-int(x.get("priority", 0)), x.get("node_id", ""))), preferred

@app.get("/cluster-control", response_class=HTMLResponse)
def cluster_control_page(request: Request):
    local, nodes, preferred = _cluster_view()
    return templates.TemplateResponse(request, "cluster_control.html", {"local": local, "nodes": nodes, "preferred": preferred})

@app.get("/api/cluster")
def cluster_api():
    local, nodes, preferred = _cluster_view()
    return {"local": local, "nodes": nodes, "preferred_node": preferred}

@app.post("/cluster/preferred")
def cluster_preferred(node_id: str = Form(...)):
    node_id = node_id.strip()
    if not node_id or len(node_id) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in node_id):
        raise HTTPException(400, "ノードIDが不正です")
    os.makedirs(CLUSTER_SHARED_DIR, exist_ok=True)
    target = os.path.join(CLUSTER_SHARED_DIR, "control.json")
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"preferred_node": node_id, "updated_at": datetime.now(JST).isoformat()}, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, target)
    return RedirectResponse("/cluster-control", 303)

@app.post("/cluster/local-enabled")
def cluster_local_enabled(enabled: str = Form("1")):
    value = "1" if enabled == "1" else "0"
    with connect() as con:
        con.execute("INSERT INTO settings(key,value) VALUES('cluster_node_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
        if value == "0":
            con.execute("INSERT INTO settings(key,value) VALUES('cluster_is_leader','0') ON CONFLICT(key) DO UPDATE SET value='0'")
    return RedirectResponse("/cluster-control", 303)
@app.get("/voice", response_class=HTMLResponse)
def voice_checkin(request: Request):
    with connect() as con:
        scenario_rows = con.execute("SELECT id,name FROM scenarios ORDER BY id").fetchall()
    return templates.TemplateResponse(request, "voice.html", {"scenarios": scenario_rows})
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with connect() as con:
        rows = con.execute("""SELECT c.*, s.name scenario_name,
          (SELECT COUNT(*) FROM answers a WHERE a.checkin_id=c.id) answer_count,
          (SELECT COUNT(*) FROM recordings r WHERE r.checkin_id=c.id) recording_count
          FROM checkins c JOIN scenarios s ON s.id=c.scenario_id ORDER BY c.id DESC LIMIT 100""").fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            if row["status"] == "cancelled":
                row["status"] = "\u9000\u51fa\uff08\u70b9\u547c\u4e2d\u65ad\uff09\uff0f\u8981\u78ba\u8a8d"
                row["alert"] = 0
        scenarios = con.execute("SELECT * FROM scenarios ORDER BY id").fetchall()
        settings = dict(con.execute("SELECT key,value FROM settings").fetchall())
    previous_date = datetime.now(JST).date() - timedelta(days=1)
    current_month = datetime.now(JST).date().replace(day=1)
    previous_month = (current_month - timedelta(days=1)).strftime("%Y-%m")
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rows, "scenarios": scenarios, "settings": settings,
            "mode": os.getenv("APP_MODE", "simulation"),
            "previous_report_date": previous_date.isoformat(),
            "report_message": request.query_params.get("report_message", ""),
            "previous_report_month": previous_month,
        },
    )

@app.get("/checkins/{checkin_id}", response_class=HTMLResponse)
def detail(request: Request, checkin_id: int):
    with connect() as con:
        item = con.execute("SELECT c.*,s.name scenario_name,s.questions_json FROM checkins c JOIN scenarios s ON s.id=c.scenario_id WHERE c.id=?", (checkin_id,)).fetchone()
        answers = con.execute("SELECT * FROM answers WHERE checkin_id=? ORDER BY id", (checkin_id,)).fetchall()
        recordings = con.execute("SELECT * FROM recordings WHERE checkin_id=? ORDER BY recorded_at,id", (checkin_id,)).fetchall()
        company_message = con.execute("SELECT * FROM company_messages WHERE checkin_id=?", (checkin_id,)).fetchone()
    if not item: raise HTTPException(404)
    questions = json.loads(item["questions_json"])
    answered_keys = {a["question_key"] for a in answers}
    next_question = next((q for q in questions if q["key"] not in answered_keys), None)
    return templates.TemplateResponse(request, "detail_v2.html", {"item": item, "answers": answers, "recordings": recordings, "company_message": company_message, "next_question": next_question, "question_count": len(questions)})

@app.get("/recordings/{recording_id}")
def recording_audio(recording_id: int):
    with connect() as con:
        row = con.execute("SELECT file_path FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if not row:
        raise HTTPException(404, "recording not found")
    path = safe_recording_path(row[0])
    if path is None or not path.is_file():
        raise HTTPException(404, "recording file not found")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})


def _normalize_identity(vehicle_number: str, driver_name: str) -> tuple[str, str]:
    vehicle_number = " ".join(vehicle_number.strip().split())
    driver_name = " ".join(driver_name.strip().split())
    if not vehicle_number or not driver_name:
        raise HTTPException(400, "車番と氏名を入力してください")
    if len(vehicle_number) > 50 or len(driver_name) > 100:
        raise HTTPException(400, "車番または氏名が長すぎます")
    return vehicle_number, driver_name


@app.get("/driver-identities", response_class=HTMLResponse)
def driver_identities(request: Request):
    with connect() as con:
        identities = con.execute(
            "SELECT vehicle_number,driver_name,meet_participant_id,first_seen_at,last_seen_at,use_count,verified "
            "FROM driver_identities ORDER BY vehicle_number COLLATE NOCASE"
        ).fetchall()
    return templates.TemplateResponse(request, "driver_identities.html", {
        "identities": identities, "message": request.query_params.get("message", "")
    })


@app.post("/driver-identities")
def create_driver_identity(vehicle_number: str = Form(...), driver_name: str = Form(...), meet_participant_id: str = Form("")):
    meet_participant_id = meet_participant_id.strip()[:200]
    vehicle_number, driver_name = _normalize_identity(vehicle_number, driver_name)
    with connect() as con:
        if con.execute("SELECT 1 FROM driver_identities WHERE vehicle_number=?", (vehicle_number,)).fetchone():
            message = "この車番は登録済みです。既存行から更新してください。"
            return RedirectResponse(f"/driver-identities?message={quote(message)}", 303)
        con.execute(
            "INSERT INTO driver_identities(vehicle_number,driver_name,meet_participant_id,verified) VALUES(?,?,?,1)",
            (vehicle_number, driver_name, meet_participant_id),
        )
    return RedirectResponse(f"/driver-identities?message={quote('車番と氏名を登録しました。')}", 303)


@app.post("/driver-identities/{vehicle_number}/update")
def update_driver_identity(vehicle_number: str, new_vehicle_number: str = Form(...), driver_name: str = Form(...), meet_participant_id: str = Form("")):
    meet_participant_id = meet_participant_id.strip()[:200]
    new_vehicle_number, driver_name = _normalize_identity(new_vehicle_number, driver_name)
    with connect() as con:
        if not con.execute("SELECT 1 FROM driver_identities WHERE vehicle_number=?", (vehicle_number,)).fetchone():
            raise HTTPException(404, "登録が見つかりません")
        duplicate = con.execute(
            "SELECT 1 FROM driver_identities WHERE vehicle_number=? AND vehicle_number<>?",
            (new_vehicle_number, vehicle_number),
        ).fetchone()
        if duplicate:
            message = "変更先の車番はすでに登録されています。"
            return RedirectResponse(f"/driver-identities?message={quote(message)}", 303)
        con.execute(
            "UPDATE driver_identities SET vehicle_number=?,driver_name=?,meet_participant_id=?,verified=1,last_seen_at=CURRENT_TIMESTAMP WHERE vehicle_number=?",
            (new_vehicle_number, driver_name, meet_participant_id, vehicle_number),
        )
    return RedirectResponse(f"/driver-identities?message={quote('登録内容を更新しました。')}", 303)


@app.post("/driver-identities/{vehicle_number}/delete")
def delete_driver_identity(vehicle_number: str):
    with connect() as con:
        node_row = con.execute("SELECT value FROM settings WHERE key='cluster_node_id'").fetchone()
        origin_node = node_row[0] if node_row else os.getenv("CLUSTER_NODE_ID", "local")
        con.execute(
            "CREATE TABLE IF NOT EXISTS identity_tombstones("
            "vehicle_number TEXT PRIMARY KEY,deleted_at TEXT NOT NULL,origin_node TEXT NOT NULL DEFAULT '')"
        )
        con.execute(
            "INSERT INTO identity_tombstones(vehicle_number,deleted_at,origin_node) VALUES(?,CURRENT_TIMESTAMP,?) "
            "ON CONFLICT(vehicle_number) DO UPDATE SET deleted_at=CURRENT_TIMESTAMP,origin_node=excluded.origin_node",
            (vehicle_number, origin_node),
        )
        con.execute("DELETE FROM driver_identities WHERE vehicle_number=?", (vehicle_number,))
    message = "登録を削除しました。点呼履歴は保持されています。"
    return RedirectResponse(f"/driver-identities?message={quote(message)}", 303)

@app.post("/settings")
def save_settings(
    meet_url: str = Form(""),
    bot_display_name: str = Form("仮想点呼"),
    meet_bot_account_name: str = Form(""),
    active_scenario_id: int = Form(1),
    operation_mode: str = Form("legacy"),
):
    if operation_mode not in ("legacy", "realtime", "realtime_gpu", "realtime_natural", "realtime_3d", "realtime_metahuman", "realtime_openwebui"):
        raise HTTPException(400, "動作モードが不正です")
    if operation_mode == "realtime_metahuman":
        from .metahuman_bridge import status
        if not status()["ready"]:
            raise HTTPException(409, "MetaHuman未接続です。/metahuman で接続状態を確認してください。現在の設定は保持されました。")
    with connect() as con:
        for k,v in {"meet_url":meet_url,"bot_display_name":bot_display_name,"meet_bot_account_name":meet_bot_account_name.strip(),"active_scenario_id":str(active_scenario_id),"operation_mode":operation_mode}.items():
            con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k,v))
    return RedirectResponse("/", 303)


@app.post("/report-settings")
def save_report_settings(
    smtp_host: str = Form(""),
    smtp_port: int = Form(465),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_sender: str = Form(""),
    report_recipient: str = Form(""),
    daily_report_enabled: str | None = Form(None),
    monthly_report_enabled: str | None = Form(None),
):
    smtp_host = smtp_host.strip()
    smtp_user = smtp_user.strip()
    smtp_sender = smtp_sender.strip()
    report_recipient = report_recipient.strip()
    if not smtp_host or any(char in smtp_host for char in "\r\n "):
        message = "SMTPサーバを正しく入力してください。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    if not 1 <= smtp_port <= 65535:
        message = "SMTPポート番号を正しく入力してください。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    if not smtp_user:
        message = "SMTPログインユーザーを入力してください。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    if not validate_email(smtp_sender):
        message = "送信元メールアドレスが不正です。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    if not validate_email(report_recipient):
        message = "送信先メールアドレスが不正です。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    with connect() as con:
        values = {
            "smtp_host": smtp_host,
            "smtp_port": str(smtp_port),
            "smtp_user": smtp_user,
            "smtp_sender": smtp_sender,
            "report_recipient": report_recipient,
            "daily_report_enabled": "1" if daily_report_enabled else "0",
            "monthly_report_enabled": "1" if monthly_report_enabled else "0",
        }
        if smtp_password:
            values["smtp_password"] = smtp_password
        for key, value in values.items():
            con.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    message = "メール設定を保存しました。"
    return RedirectResponse(f"/?report_message={quote(message)}", 303)


@app.post("/reports/send")
def send_report_manually(report_date: str = Form(...)):
    try:
        target_date = date.fromisoformat(report_date)
    except ValueError:
        message = "送信対象日が不正です。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    try:
        result = send_report(DB_PATH, target_date)
    except Exception as exc:
        message = f"メール送信に失敗しました: {type(exc).__name__}: {exc}"[:500]
        with connect() as con:
            con.execute(
                "INSERT INTO settings(key,value) VALUES('report_manual_last_error',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (message,),
            )
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    message = (
        f"{result['report_date']}の点呼記録"
        f"（{result['checkin_count']}件）を送信しました。"
    )
    with connect() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('report_manual_last_sent_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now(JST).isoformat(timespec="seconds"),),
        )
    return RedirectResponse(f"/?report_message={quote(message)}", 303)

@app.post("/reports/send-monthly")
def send_monthly_report_manually(report_month: str = Form(...)):
    try:
        target_month = date.fromisoformat(f"{report_month}-01")
    except ValueError:
        message = "送信対象月が不正です。"
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    try:
        result = send_monthly_report(DB_PATH, target_month)
    except Exception as exc:
        message = f"月次メール送信に失敗しました: {type(exc).__name__}: {exc}"[:500]
        with connect() as con:
            con.execute(
                "INSERT INTO settings(key,value) VALUES('report_monthly_manual_last_error',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (message,),
            )
        return RedirectResponse(f"/?report_message={quote(message)}", 303)
    message = (
        f"{result['report_month']}の月次点呼記録"
        f"（{result['checkin_count']}件）を送信しました。"
    )
    with connect() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('report_monthly_manual_last_sent_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now(JST).isoformat(timespec="seconds"),),
        )
        con.execute(
            "INSERT INTO settings(key,value) VALUES('report_monthly_manual_last_error','') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
    return RedirectResponse(f"/?report_message={quote(message)}", 303)

@app.post("/scenarios")
def create_scenario_form(name: str = Form(...), questions: str = Form(...)):
    parsed=[]
    for i,line in enumerate(questions.splitlines()):
        if line.strip(): parsed.append({"key":f"q{i+1}","text":line.strip(),"expected":"yes"})
    if not parsed: raise HTTPException(400, "質問を入力してください")
    with connect() as con: con.execute("INSERT INTO scenarios(name,questions_json) VALUES(?,?)", (name,json.dumps(parsed,ensure_ascii=False)))
    return RedirectResponse("/",303)

@app.post("/simulate")
def simulate(participant_id: str = Form(...), participant_name: str = Form(""), scenario_id: int = Form(1)):
    cid = start(StartIn(participant_id=participant_id, participant_name=participant_name, scenario_id=scenario_id)).get("id")
    return RedirectResponse(f"/checkins/{cid}",303)

@app.post("/checkins/{checkin_id}/form-answer")
def form_answer(checkin_id:int, question_key:str=Form(...), question_text:str=Form(...), answer_text:str=Form(...), is_ok:str=Form("yes")):
    answer(checkin_id, AnswerIn(question_key=question_key,question_text=question_text,answer_text=answer_text,is_ok=is_ok=="yes"))
    return RedirectResponse(f"/checkins/{checkin_id}",303)

@app.post("/checkins/{checkin_id}/form-complete")
def form_complete(checkin_id:int): complete(checkin_id); return RedirectResponse(f"/checkins/{checkin_id}",303)

@app.get("/api/health")
def health():
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key='operation_mode'").fetchone()
    return {
        "ok": True,
        "mode": os.getenv("APP_MODE", "simulation"),
        "operation_mode": row[0] if row else "legacy",
    }
@app.get("/api/scenarios")
def scenarios():
    with connect() as con: rows=con.execute("SELECT * FROM scenarios ORDER BY id").fetchall()
    return [{**dict(r),"questions":json.loads(r["questions_json"])} for r in rows]
@app.post("/api/scenarios")
def create_scenario(data: ScenarioIn):
    with connect() as con:
        cur=con.execute("INSERT INTO scenarios(name,questions_json) VALUES(?,?)",(data.name,json.dumps(data.questions,ensure_ascii=False)))
        return {"id":cur.lastrowid}
@app.post("/api/checkins/start")
def start(data: StartIn):
    with connect() as con:
        if not con.execute("SELECT 1 FROM scenarios WHERE id=?",(data.scenario_id,)).fetchone(): raise HTTPException(404,"scenario not found")
        cur=con.execute("INSERT INTO checkins(participant_id,participant_name,meet_url,scenario_id) VALUES(?,?,?,?)",(data.participant_id,data.participant_name,data.meet_url,data.scenario_id))
        return {"id":cur.lastrowid}
@app.post("/api/checkins/{checkin_id}/answer")
def answer(checkin_id:int,data:AnswerIn):
    with connect() as con:
        if not con.execute("SELECT 1 FROM checkins WHERE id=?",(checkin_id,)).fetchone(): raise HTTPException(404)
        con.execute("INSERT INTO answers(checkin_id,question_key,question_text,answer_text,is_ok) VALUES(?,?,?,?,?)",(checkin_id,data.question_key,data.question_text,data.answer_text,data.is_ok))
        if data.is_ok is False: con.execute("UPDATE checkins SET alert=1 WHERE id=?",(checkin_id,))
    return {"ok":True}
@app.post("/api/checkins/{checkin_id}/complete")
def complete(checkin_id:int):
    with connect() as con: con.execute("UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",(checkin_id,))
    return {"ok":True}
@app.get("/api/checkins")
def list_checkins():
    with connect() as con: return [dict(r) for r in con.execute("SELECT * FROM checkins ORDER BY id DESC LIMIT 500")]

@app.get("/api/worker-status")
def worker_status():
    with connect() as con:
        values = dict(con.execute(
            "SELECT key,value FROM settings WHERE key IN "
            "('operation_mode','worker_status','worker_detail','worker_updated_at',"
            "'participant_worker_status','participant_worker_detail','participant_worker_updated_at')"
        ).fetchall())
    prefix = "participant_worker" if values.get("operation_mode", "").startswith("realtime") else "worker"
    return {
        "status": values.get(f"{prefix}_status", "unknown"),
        "detail": values.get(f"{prefix}_detail", ""),
        "updated_at": values.get(f"{prefix}_updated_at", ""),
    }

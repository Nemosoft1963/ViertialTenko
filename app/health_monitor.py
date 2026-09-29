import json,logging,os,shutil,sqlite3,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import httpx
try:
 import docker
except ImportError:
 docker=None
DATABASE_PATH=Path(os.getenv("DATABASE_PATH","/data/tenko.db"))
APP_HEALTH_URL=os.getenv("APP_HEALTH_URL","http://app:8080/api/health")
MEET_OUTPUT_PATH=Path(os.getenv("MEET_OUTPUT_PATH","/meet-config/meet-output.raw"))
LOG_DIR=Path(os.getenv("HEALTH_LOG_DIR","/data/health-logs"))
INTERVAL=max(10,int(os.getenv("HEALTH_MONITOR_INTERVAL_SECONDS","30")))
RETENTION=max(1,int(os.getenv("HEALTH_LOG_RETENTION_DAYS","90")))
STALE=max(60,int(os.getenv("HEALTH_WORKER_STALE_SECONDS","180")))
AUTO_RECOVERY=os.getenv("HEALTH_AUTO_RECOVERY","0")=="1"
FAILURE_THRESHOLD=max(2,int(os.getenv("HEALTH_RECOVERY_FAILURE_THRESHOLD","3")))
RECOVERY_COOLDOWN=max(60,int(os.getenv("HEALTH_RECOVERY_COOLDOWN_SECONDS","600")))
CLUSTER_SHARED_DIR=Path(os.getenv("CLUSTER_SHARED_DIR","/cluster-shared"))
NODE_ID=os.getenv("CLUSTER_NODE_ID","primary-pc").strip()
RECOVERY_STATE=LOG_DIR/"recovery-state.json"
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
def parse_time(v):
 try:
  d=datetime.fromisoformat(v.replace("Z","+00:00"));return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
 except (AttributeError,ValueError):return None
def check_app():
 t=time.monotonic()
 try:
  r=httpx.get(APP_HEALTH_URL,timeout=5);r.raise_for_status();return {"ok":True,"status_code":r.status_code,"response_ms":round((time.monotonic()-t)*1000)}
 except Exception as e:return {"ok":False,"error":f"{type(e).__name__}: {e}"}
def check_db(now):
 out={"ok":False,"settings":{},"active_checkins":0,"queued_participants":0}
 try:
  with sqlite3.connect(f"file:{DATABASE_PATH.as_posix()}?mode=ro",uri=True,timeout=3) as c:
   out["settings"]=dict(c.execute("select key,value from settings"))
   out["active_checkins"]=c.execute("select count(*) from checkins where status in ('awaiting_id','awaiting_name','in_progress','awaiting_company_message','awaiting_company_message_confirmation')").fetchone()[0]
   out["queued_participants"]=c.execute("select count(*) from participant_queue where status='waiting'").fetchone()[0]
   out["oldest_active_minutes"]=c.execute("select coalesce((julianday(?)-julianday(min(joined_at)))*1440,0) from checkins where status='in_progress'",(now.isoformat(),)).fetchone()[0];out["ok"]=True
 except Exception as e:out["error"]=f"{type(e).__name__}: {e}"
 return out
def collect_snapshot():
 now=datetime.now(timezone.utc);app=check_app();db=check_db(now);s=db.get("settings",{});du=shutil.disk_usage(DATABASE_PATH.parent);free=round(du.free/du.total*100,1);audio=MEET_OUTPUT_PATH.is_file();audio_age=round(now.timestamp()-MEET_OUTPUT_PATH.stat().st_mtime) if audio else None;active=int(db.get("active_checkins",0));issues=[]
 def add(sev,code,msg):issues.append({"severity":sev,"code":code,"message":msg})
 if not app["ok"]:add("critical","app_unreachable","管理APIに接続できません")
 if not db["ok"]:add("critical","database_unreadable","データベースを読み取れません")
 if free<5:add("critical","disk_critical",f"ディスク空き容量が{free}%です")
 elif free<15:add("warning","disk_low",f"ディスク空き容量が{free}%です")
 ws=s.get("participant_worker_status","unknown");wt=parse_time(s.get("participant_worker_updated_at"));wa=round((now-wt).total_seconds()) if wt else None
 if active and ws in {"error","attention_required","configuration_required"}:add("warning","participant_worker_error",f"Meet監視状態: {ws}")
 if active and (wa is None or wa>STALE):add("warning","participant_worker_stale","Meet監視の更新が停止しています")
 if active and (not audio or audio_age is None or audio_age>STALE):add("warning","audio_stale","Meet音声入力が更新されていません")
 if float(db.get("oldest_active_minutes",0) or 0)>30:add("warning","checkin_stalled","30分以上継続している点呼があります")
 if s.get("realtime_prompt_state")=="error":add("warning","conversation_error","対話処理でエラーが発生しています")
 prompt_time=parse_time(s.get("realtime_prompt_queued_at"));prompt_age=round((now-prompt_time).total_seconds()) if prompt_time else None
 if active and s.get("realtime_prompt_state") in {"queued","synthesizing","generating","rendering","ready"} and prompt_age is not None and prompt_age>120:add("warning","conversation_stale","案内音声処理が120秒以上停止しています")
 status="critical" if any(x["severity"]=="critical" for x in issues) else ("warning" if issues else "healthy")
 return {"timestamp":now.isoformat(),"status":status,"issues":issues,"components":{"app":app,"database":{k:v for k,v in db.items() if k!="settings"},"participant_worker":{"status":ws,"detail":s.get("participant_worker_detail",""),"updated_at":s.get("participant_worker_updated_at",""),"age_seconds":wa},"response_worker":{"status":s.get("response_worker_status","unknown")},"conversation":{"state":s.get("realtime_prompt_state","unknown"),"error":s.get("realtime_prompt_error","")},"gpu_pipeline":{"state":s.get("gpu_pipeline_state","standby"),"error":s.get("gpu_pipeline_error","")},"audio_input":{"exists":audio,"age_seconds":audio_age},"disk":{"free_percent":free,"free_bytes":du.free,"total_bytes":du.total}}}
def write_snapshot(x):
 LOG_DIR.mkdir(parents=True,exist_ok=True)
 with (LOG_DIR/f"{x['timestamp'][:10]}.jsonl").open("a",encoding="utf-8") as f:f.write(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n")
 t=LOG_DIR/"current.json.tmp";t.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8");t.replace(LOG_DIR/"current.json")
def cleanup(now=None):
 cutoff=(now or datetime.now(timezone.utc)).date()-timedelta(days=RETENTION)
 for p in LOG_DIR.glob("????-??-??.jsonl"):
  try:
   if datetime.strptime(p.stem,"%Y-%m-%d").date()<cutoff:p.unlink()
  except ValueError:pass
def recovery_targets(snapshot):
 codes={x["code"] for x in snapshot.get("issues",[])};targets={}
 if "app_unreachable" in codes:targets["app"]="管理API応答停止"
 if codes & {"participant_worker_stale","audio_stale"}:targets["meet-browser"]="Meet監視または音声入力の更新停止"
 if "participant_worker_error" in codes:targets["meet-browser"]="Meet監視処理エラー"
 if codes & {"conversation_error","conversation_stale"}:targets["response-worker"]="対話処理エラーまたは案内音声停止"
 detail=str(snapshot.get("components",{}).get("participant_worker",{}).get("detail","")).lower()
 if "database is locked" in detail:targets["meet-bot"]="SQLiteロック"
 gpu_component=snapshot.get("components",{}).get("gpu_pipeline",{})
 gpu=str(gpu_component.get("error","")).lower()
 if gpu_component.get("state")=="error" and "database is locked" in gpu:targets["gpu-avatar-worker"]="SQLiteロック"
 return targets
def _read_recovery_state():
 try:return json.loads(RECOVERY_STATE.read_text(encoding="utf-8"))
 except (OSError,ValueError):return {"failures":{},"last_restart":{}}
def _write_recovery_state(state):
 LOG_DIR.mkdir(parents=True,exist_ok=True);tmp=RECOVERY_STATE.with_suffix(".tmp");tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(RECOVERY_STATE)
def publish_recovery(event):
 local=LOG_DIR/f"recovery-{event['timestamp'][:10]}.jsonl"
 with local.open("a",encoding="utf-8") as f:f.write(json.dumps(event,ensure_ascii=False,separators=(",",":"))+"\n")
 try:
  directory=CLUSTER_SHARED_DIR/"recovery"/NODE_ID;directory.mkdir(parents=True,exist_ok=True)
  atomic=directory/"current.json.tmp";atomic.write_text(json.dumps(event,ensure_ascii=False,indent=2),encoding="utf-8");atomic.replace(directory/"current.json")
  (directory/(event["timestamp"].replace(":","-")+".json")).write_text(json.dumps(event,ensure_ascii=False,indent=2),encoding="utf-8")
 except OSError as e:logging.warning("recovery event share failed: %s",e)
def restart_service(service):
 if docker is None:raise RuntimeError("Docker SDK is unavailable")
 client=docker.from_env();containers=client.containers.list(all=True,filters={"label":f"com.docker.compose.service={service}"})
 if len(containers)!=1:raise RuntimeError(f"expected one {service} container, found {len(containers)}")
 containers[0].restart(timeout=10)
def auto_recover(snapshot,now=None,restart=restart_service):
 now=now or datetime.now(timezone.utc);state=_read_recovery_state();targets=recovery_targets(snapshot);actions=[]
 failures=state.setdefault("failures",{});last=state.setdefault("last_restart",{})
 for service in set(failures)-set(targets):failures[service]=0
 for service,reason in targets.items():
  failures[service]=int(failures.get(service,0))+1;previous=parse_time(last.get(service));cooldown_ok=not previous or (now-previous).total_seconds()>=RECOVERY_COOLDOWN
  if failures[service]<FAILURE_THRESHOLD or not cooldown_ok:continue
  event={"schema":1,"timestamp":now.isoformat(),"node_id":NODE_ID,"service":service,"reason":reason,"action":"restart","result":"failed"}
  try:restart(service);event["result"]="success";last[service]=now.isoformat();failures[service]=0
  except Exception as e:event["error"]=f"{type(e).__name__}: {e}"[:500]
  publish_recovery(event);actions.append(event);logging.warning("auto recovery service=%s result=%s reason=%s",service,event["result"],reason)
 _write_recovery_state(state);return actions
def run():
 logging.info("monitor started interval=%s retention=%s",INTERVAL,RETENTION)
 while True:
  started=time.monotonic()
  try:
   x=collect_snapshot();x["recovery_actions"]=auto_recover(x) if AUTO_RECOVERY else [];write_snapshot(x);cleanup();logging.info("status=%s issues=%s recovery=%s",x["status"],len(x["issues"]),len(x["recovery_actions"]))
  except Exception:logging.exception("monitor cycle failed")
  time.sleep(max(1,INTERVAL-(time.monotonic()-started)))
if __name__=="__main__":run()

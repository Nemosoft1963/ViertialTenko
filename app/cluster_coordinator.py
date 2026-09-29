"""Two-node failover and result replication through a NAS witness directory."""
import hashlib,json,logging,os,shutil,sqlite3,threading,time
from datetime import datetime,timezone
from pathlib import Path
DB=Path(os.getenv("DATABASE_PATH","/data/tenko.db"))
SHARED=Path(os.getenv("CLUSTER_SHARED_DIR","/cluster-shared"))
NODE_ID=os.getenv("CLUSTER_NODE_ID","primary-pc").strip()
NODE_NAME=os.getenv("CLUSTER_NODE_NAME",NODE_ID).strip()
PRIORITY=int(os.getenv("CLUSTER_NODE_PRIORITY","100"))
INTERVAL=max(2,int(os.getenv("CLUSTER_INTERVAL_SECONDS","5")))
STALE=max(15,int(os.getenv("CLUSTER_STALE_SECONDS","45")))
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
def now():return datetime.now(timezone.utc)
def atomic_json(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+f".{NODE_ID}.{threading.get_ident()}.tmp");tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(path)
def setting(con,key,default=""):
 row=con.execute("select value from settings where key=?",(key,)).fetchone();return row[0] if row else default
def save(con,key,value):
 con.execute("insert into settings(key,value) values(?,?) on conflict(key) do update set value=excluded.value",(key,str(value)))
def ensure_schema(con):
 cols={r[1] for r in con.execute("pragma table_info(checkins)")}
 if "cluster_origin_node" not in cols:con.execute("alter table checkins add column cluster_origin_node text not null default ''")
 if "cluster_origin_id" not in cols:con.execute("alter table checkins add column cluster_origin_id text not null default ''")
 con.execute("create unique index if not exists idx_checkins_cluster_origin on checkins(cluster_origin_node,cluster_origin_id) where cluster_origin_node<>''")
 con.execute("create table if not exists identity_tombstones(vehicle_number text primary key,deleted_at text not null,origin_node text not null default '')")
def heartbeat(enabled):
 return {"node_id":NODE_ID,"name":NODE_NAME,"priority":PRIORITY,"enabled":enabled,"timestamp":now().isoformat(),"version":4}
def write_heartbeat():
 with sqlite3.connect(DB,timeout=10) as con:
  enabled=setting(con,"cluster_node_enabled","1")=="1"
 atomic_json(SHARED/"nodes"/f"{NODE_ID}.json",heartbeat(enabled))
def heartbeat_loop():
 while True:
  try:write_heartbeat()
  except Exception:logging.exception("cluster heartbeat failed")
  time.sleep(INTERVAL)
def read_nodes():
 out=[]
 for p in (SHARED/"nodes").glob("*.json"):
  try:
   x=json.loads(p.read_text(encoding="utf-8"));ts=datetime.fromisoformat(x["timestamp"].replace("Z","+00:00"));x["age_seconds"]=round((now()-ts).total_seconds());x["online"]=x["age_seconds"]<=STALE;out.append(x)
  except Exception:continue
 return out
def control():
 try:return json.loads((SHARED/"control.json").read_text(encoding="utf-8"))
 except Exception:return {}
def choose_leader(nodes):
 live=[x for x in nodes if x.get("online") and x.get("enabled")]
 preferred=control().get("preferred_node","")
 match=next((x for x in live if x["node_id"]==preferred),None)
 if match:return match["node_id"]
 return max(live,key=lambda x:(int(x.get("priority",0)),x["node_id"]))["node_id"] if live else ""
def bundle(con,row):
 cid=row["id"]
 answers=[dict(x) for x in con.execute("select question_key,question_text,answer_text,is_ok,answered_at from answers where checkin_id=? order by id",(cid,))]
 msg=con.execute("select original_text,summary_text,category,created_at from company_messages where checkin_id=?",(cid,)).fetchone()
 return {"schema":1,"origin_node":NODE_ID,"origin_id":str(cid),"checkin":{k:row[k] for k in row.keys() if k not in ("id","cluster_origin_node","cluster_origin_id")},"answers":answers,"company_message":dict(msg) if msg else None}
def export_results(con):
 directory=SHARED/"results"/NODE_ID;directory.mkdir(parents=True,exist_ok=True)
 rows=con.execute("select * from checkins where status='completed' and (cluster_origin_node='' or cluster_origin_node=?) order by id",(NODE_ID,)).fetchall()
 count=0
 for row in rows:
  origin=row["cluster_origin_id"] or str(row["id"])
  if not row["cluster_origin_node"]:con.execute("update checkins set cluster_origin_node=?,cluster_origin_id=? where id=?",(NODE_ID,origin,row["id"]))
  path=directory/f"{origin}.json"
  if not path.exists():atomic_json(path,bundle(con,row));count+=1
 return count
def import_one(con,data):
 node=str(data["origin_node"]);oid=str(data["origin_id"])
 if node==NODE_ID or con.execute("select 1 from checkins where cluster_origin_node=? and cluster_origin_id=?",(node,oid)).fetchone():return False
 c=data["checkin"];cols=["participant_id","participant_name","meet_participant_id","meet_url","scenario_id","joined_at","completed_at","status","alert","cluster_origin_node","cluster_origin_id"];vals=[c.get(k) for k in cols[:-2]]+[node,oid]
 cur=con.execute(f"insert into checkins({','.join(cols)}) values({','.join('?' for _ in cols)})",vals);cid=cur.lastrowid
 for a in data.get("answers",[]):con.execute("insert into answers(checkin_id,question_key,question_text,answer_text,is_ok,answered_at) values(?,?,?,?,?,?)",(cid,a.get("question_key",""),a.get("question_text",""),a.get("answer_text",""),a.get("is_ok"),a.get("answered_at")))
 m=data.get("company_message")
 if m:con.execute("insert into company_messages(checkin_id,original_text,summary_text,category,created_at) values(?,?,?,?,?)",(cid,m.get("original_text",""),m.get("summary_text",""),m.get("category","normal"),m.get("created_at")))
 return True
def import_results(con):
 count=0
 for p in (SHARED/"results").glob("*/*.json"):
  try:
   if import_one(con,json.loads(p.read_text(encoding="utf-8"))):count+=1
  except Exception as e:logging.warning("import failed %s: %s",p,e)
 return count
AUDIT_CHECKIN_KEYS=("participant_id","participant_name","meet_participant_id","meet_url","scenario_id","joined_at","completed_at","status","alert")
def audit_results(con):
 result={"timestamp":now().isoformat(),"source_total":0,"verified":0,"missing":0,"mismatch":0,"corrupt":0,"by_origin":{},"details":[]}
 for path in (SHARED/"results").glob("*/*.json"):
  result["source_total"]+=1
  try:
   data=json.loads(path.read_text(encoding="utf-8"));node=str(data["origin_node"]);oid=str(data["origin_id"])
   if data.get("schema")!=1 or not node or not oid or not isinstance(data.get("checkin"),dict):raise ValueError("invalid schema")
  except Exception as exc:
   result["corrupt"]+=1
   if len(result["details"])<20:result["details"].append({"file":str(path),"state":"corrupt","error":f"{type(exc).__name__}: {exc}"})
   continue
  origin=result["by_origin"].setdefault(node,{"source_total":0,"verified":0,"missing":0,"mismatch":0});origin["source_total"]+=1
  row=con.execute("select * from checkins where cluster_origin_node=? and cluster_origin_id=?",(node,oid)).fetchone()
  if not row:
   result["missing"]+=1;origin["missing"]+=1
   if len(result["details"])<20:result["details"].append({"origin_node":node,"origin_id":oid,"state":"missing"})
   continue
  actual_checkin={key:row[key] for key in AUDIT_CHECKIN_KEYS};expected_checkin={key:data["checkin"].get(key) for key in AUDIT_CHECKIN_KEYS}
  actual_answers=[dict(x) for x in con.execute("select question_key,question_text,answer_text,is_ok,answered_at from answers where checkin_id=? order by id",(row["id"],))]
  message=con.execute("select original_text,summary_text,category,created_at from company_messages where checkin_id=?",(row["id"],)).fetchone();actual_message=dict(message) if message else None
  if actual_checkin==expected_checkin and actual_answers==data.get("answers",[]) and actual_message==data.get("company_message"):
   result["verified"]+=1;origin["verified"]+=1
  else:
   result["mismatch"]+=1;origin["mismatch"]+=1
   if len(result["details"])<20:result["details"].append({"origin_node":node,"origin_id":oid,"state":"mismatch"})
 return result
def export_identity_snapshot(con):
 rows=[dict(x) for x in con.execute("select vehicle_number,driver_name,meet_participant_id,first_seen_at,last_seen_at,use_count,verified from driver_identities order by vehicle_number")]
 deleted=[dict(x) for x in con.execute("select vehicle_number,deleted_at,origin_node from identity_tombstones order by vehicle_number")]
 atomic_json(SHARED/"identities"/f"{NODE_ID}.json",{"schema":1,"node_id":NODE_ID,"timestamp":now().isoformat(),"identities":rows,"tombstones":deleted})
 return len(rows)
def expected_identity_state():
 identities={};tombstones={};corrupt=0
 for path in (SHARED/"identities").glob("*.json"):
  try:
   data=json.loads(path.read_text(encoding="utf-8"))
   if data.get("schema")!=1:raise ValueError("invalid schema")
   source=str(data.get("node_id",""))
   for item in data.get("identities",[]):
    vehicle=str(item["vehicle_number"]);candidate=dict(item);candidate["_source"]=source
    current=identities.get(vehicle)
    rank=(int(candidate.get("verified",0)),str(candidate.get("last_seen_at","")),source)
    if current is None or rank>(int(current.get("verified",0)),str(current.get("last_seen_at","")),str(current.get("_source",""))):identities[vehicle]=candidate
   for item in data.get("tombstones",[]):
    vehicle=str(item["vehicle_number"]);candidate=dict(item);candidate["_source"]=source
    current=tombstones.get(vehicle)
    if current is None or (str(candidate.get("deleted_at","")),source)>(str(current.get("deleted_at","")),str(current.get("_source",""))):tombstones[vehicle]=candidate
  except Exception as exc:
   corrupt+=1;logging.warning("identity snapshot failed %s: %s",path,exc)
 for vehicle,deleted in list(tombstones.items()):
  current=identities.get(vehicle)
  if current and str(deleted.get("deleted_at",""))>=str(current.get("last_seen_at","")):identities.pop(vehicle,None)
  elif current:tombstones.pop(vehicle,None)
 return identities,tombstones,corrupt
def sync_identities(con):
 expected,deleted,corrupt=expected_identity_state();changed=0
 for vehicle,item in expected.items():
  row=con.execute("select driver_name,meet_participant_id,first_seen_at,last_seen_at,use_count,verified from driver_identities where vehicle_number=?",(vehicle,)).fetchone()
  values=(item.get("driver_name",""),item.get("meet_participant_id",""),item.get("first_seen_at") or now().isoformat(),item.get("last_seen_at") or now().isoformat(),int(item.get("use_count",1)),int(item.get("verified",0)))
  if row is None:
   con.execute("insert into driver_identities(vehicle_number,driver_name,meet_participant_id,first_seen_at,last_seen_at,use_count,verified) values(?,?,?,?,?,?,?)",(vehicle,)+values);changed+=1
  elif tuple(row)!=values:
   con.execute("update driver_identities set driver_name=?,meet_participant_id=?,first_seen_at=?,last_seen_at=?,use_count=?,verified=? where vehicle_number=?",values+(vehicle,));changed+=1
  con.execute("delete from identity_tombstones where vehicle_number=?",(vehicle,))
 for vehicle,item in deleted.items():
  if con.execute("select 1 from driver_identities where vehicle_number=?",(vehicle,)).fetchone():
   con.execute("delete from driver_identities where vehicle_number=?",(vehicle,));changed+=1
  con.execute("insert into identity_tombstones(vehicle_number,deleted_at,origin_node) values(?,?,?) on conflict(vehicle_number) do update set deleted_at=excluded.deleted_at,origin_node=excluded.origin_node",(vehicle,item.get("deleted_at") or now().isoformat(),item.get("_source","")))
 local={r[0]:tuple(r[1:]) for r in con.execute("select vehicle_number,driver_name,meet_participant_id,first_seen_at,last_seen_at,use_count,verified from driver_identities")}
 expected_values={vehicle:(item.get("driver_name",""),item.get("meet_participant_id",""),item.get("first_seen_at") or "",item.get("last_seen_at") or "",int(item.get("use_count",1)),int(item.get("verified",0))) for vehicle,item in expected.items()}
 missing=sorted(set(expected_values)-set(local));extra=sorted(set(local)-set(expected_values));mismatch=sorted(vehicle for vehicle in set(local)&set(expected_values) if local[vehicle]!=expected_values[vehicle])
 return changed,{"timestamp":now().isoformat(),"expected":len(expected_values),"verified":len(expected_values)-len(missing)-len(mismatch),"missing":len(missing),"extra":len(extra),"mismatch":len(mismatch),"deleted":len(deleted),"corrupt":corrupt,"details":{"missing":missing[:20],"extra":extra[:20],"mismatch":mismatch[:20]}}
SAFE_CONFIGURATION_KEYS=("meet_url","bot_display_name","meet_bot_account_name","operation_mode")
def export_configuration(con):
 scenarios=[dict(x) for x in con.execute("select name,questions_json,created_at,updated_at from scenarios order by name")]
 settings={key:setting(con,key,"") for key in SAFE_CONFIGURATION_KEYS};active_id=setting(con,"active_scenario_id","")
 row=con.execute("select name from scenarios where id=?",(active_id,)).fetchone() if active_id else None
 atomic_json(SHARED/"configuration"/f"{NODE_ID}.json",{"schema":1,"node_id":NODE_ID,"timestamp":now().isoformat(),"scenarios":scenarios,"settings":settings,"active_scenario_name":row[0] if row else ""});return len(scenarios)
def sync_configuration(con,leader_id):
 result={"timestamp":now().isoformat(),"source_node":leader_id,"state":"awaiting_leader_snapshot","expected":0,"verified":0,"missing":0,"extra":0,"mismatch":0,"settings_verified":0,"settings_mismatch":0,"corrupt":0,"details":{"missing":[],"extra":[],"mismatch":[],"settings":[]}}
 if not leader_id:return 0,result
 path=SHARED/"configuration"/f"{leader_id}.json"
 if not path.exists():return 0,result
 try:
  data=json.loads(path.read_text(encoding="utf-8"))
  if data.get("schema")!=1 or data.get("node_id")!=leader_id or not isinstance(data.get("scenarios"),list) or not isinstance(data.get("settings"),dict):raise ValueError("invalid schema")
 except Exception as exc:
  result["state"]="corrupt";result["corrupt"]=1;result["details"]["error"]=f"{type(exc).__name__}: {exc}";return 0,result
 changed=0;expected={}
 for item in data["scenarios"]:
  name=str(item.get("name","")).strip()
  if not name:continue
  questions=str(item.get("questions_json","[]"));expected[name]=questions;row=con.execute("select id,questions_json from scenarios where name=? order by id limit 1",(name,)).fetchone()
  if row is None:con.execute("insert into scenarios(name,questions_json,created_at,updated_at) values(?,?,?,?)",(name,questions,item.get("created_at") or now().isoformat(),item.get("updated_at") or now().isoformat()));changed+=1
  elif row["questions_json"]!=questions:con.execute("update scenarios set questions_json=?,updated_at=? where id=?",(questions,item.get("updated_at") or now().isoformat(),row["id"]));changed+=1
 expected_settings={key:str(data["settings"].get(key,"")) for key in SAFE_CONFIGURATION_KEYS}
 for key,value in expected_settings.items():
  if setting(con,key,"")!=value:save(con,key,value);changed+=1
 active_name=str(data.get("active_scenario_name","")).strip()
 if active_name:
  row=con.execute("select id from scenarios where name=? order by id limit 1",(active_name,)).fetchone()
  if row and setting(con,"active_scenario_id","")!=str(row[0]):save(con,"active_scenario_id",row[0]);changed+=1
 local={row[0]:row[1] for row in con.execute("select name,questions_json from scenarios")};missing=sorted(set(expected)-set(local));extra=sorted(set(local)-set(expected));mismatch=sorted(name for name in set(local)&set(expected) if local[name]!=expected[name])
 setting_details=[];settings_verified=0
 for key,value in expected_settings.items():
  if setting(con,key,None)==value:settings_verified+=1
  else:setting_details.append(key)
 active_actual="";active_id=setting(con,"active_scenario_id","")
 if active_id:
  row=con.execute("select name from scenarios where id=?",(active_id,)).fetchone();active_actual=row[0] if row else ""
 if active_actual!=active_name:setting_details.append("active_scenario")
 else:settings_verified+=1
 result.update({"state":"ok" if not missing and not mismatch and not setting_details else "difference","expected":len(expected),"verified":len(expected)-len(missing)-len(mismatch),"missing":len(missing),"extra":len(extra),"mismatch":len(mismatch),"settings_verified":settings_verified,"settings_mismatch":len(setting_details),"details":{"missing":missing[:20],"extra":extra[:20],"mismatch":mismatch[:20],"settings":setting_details[:20]}});return changed,result
def cycle():
 with sqlite3.connect(DB,timeout=10,isolation_level=None) as con:
  con.row_factory=sqlite3.Row;ensure_schema(con);enabled=setting(con,"cluster_node_enabled","1")=="1"
 atomic_json(SHARED/"nodes"/f"{NODE_ID}.json",heartbeat(enabled))
 nodes=read_nodes();leader=choose_leader(nodes);witness_ok=any(x["node_id"]==NODE_ID and x.get("online") for x in nodes);is_leader=witness_ok and enabled and leader==NODE_ID
 # Synchronization performs NAS file I/O as well as SQL. Autocommit keeps the
 # SQLite write lock scoped to each statement instead of the whole sync cycle.
 with sqlite3.connect(DB,timeout=10,isolation_level=None) as con:
  con.row_factory=sqlite3.Row;ensure_schema(con);exported=export_results(con);imported=import_results(con);identity_exported=export_identity_snapshot(con);identity_changed,identity_audit=sync_identities(con);configuration_exported=export_configuration(con);configuration_changed,configuration_audit=sync_configuration(con,leader);audit=audit_results(con)
  save(con,"cluster_sync_audit",json.dumps(audit,ensure_ascii=False,separators=(",",":")));save(con,"cluster_node_id",NODE_ID);save(con,"cluster_node_name",NODE_NAME);save(con,"cluster_leader_id",leader);save(con,"cluster_is_leader","1" if is_leader else "0");save(con,"cluster_witness_ok","1" if witness_ok else "0");save(con,"cluster_last_cycle_at",now().isoformat());save(con,"cluster_last_error","");save(con,"cluster_last_exported",exported);save(con,"cluster_last_imported",imported);con.commit()
 atomic_json(SHARED/"status"/f"{NODE_ID}.json",{"node_id":NODE_ID,"leader_id":leader,"is_leader":is_leader,"witness_ok":witness_ok,"timestamp":now().isoformat(),"exported":exported,"imported":imported,"sync_audit":audit,"identity_audit":identity_audit,"identity_changed":identity_changed,"identity_exported":identity_exported,"configuration_audit":configuration_audit,"configuration_changed":configuration_changed,"configuration_exported":configuration_exported})
 return leader,is_leader,exported,imported
def run():
 threading.Thread(target=heartbeat_loop,name="cluster-heartbeat",daemon=True).start()
 while True:
  try:
   leader,active,outgoing,incoming=cycle();logging.info("leader=%s active=%s export=%s import=%s",leader,active,outgoing,incoming)
  except Exception as e:
   logging.exception("cluster cycle failed")
   try:
    with sqlite3.connect(DB,timeout=5) as con:save(con,"cluster_is_leader","0");save(con,"cluster_witness_ok","0");save(con,"cluster_last_error",f"{type(e).__name__}: {e}"[:500]);con.commit()
   except Exception:pass
  time.sleep(INTERVAL)
if __name__=="__main__":run()

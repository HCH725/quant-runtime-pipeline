#!/usr/bin/env python3
"""Instantiate immutable same-day open/close round/run specs (direct family, no Kanban)."""
import argparse, hashlib, json, os, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import parameter_contract as pc
from production_handoff import fingerprint
FAMILY_ID="same-day-open-to-close-directional-spy-walk-forward-2026-09-02"
DEFAULT_RESULTS="/Volumes/ExpansionDrive/qlib-results"
DEFAULT_SCRIPTS="/Users/hong/workspace/qlib-apple-container/scripts"
ROUND_TEMPLATE=HERE/"templates"/"spy_open_close_round_spec.template.json"
RUN_TEMPLATE=HERE/"templates"/"spy_open_close_run_spec.template.json"

def load(p): return json.loads(Path(p).read_text())
def sha256(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1<<20),b""): h.update(b)
 return "sha256:"+h.hexdigest()
def substitute(v,m):
 if isinstance(v,str):
  for k,x in m.items(): v=v.replace(k,x)
  return v
 if isinstance(v,list): return [substitute(x,m) for x in v]
 if isinstance(v,dict): return {k:substitute(x,m) for k,x in v.items()}
 return v
def atomic_new(p,v):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
 data=(json.dumps(v,indent=2,ensure_ascii=False)+"\n").encode()
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 try:
  with os.fdopen(fd,"wb") as f:
   f.write(data); f.flush(); os.fsync(f.fileno())
 except Exception:
  p.unlink(missing_ok=True); raise

def main(argv=None):
 ap=argparse.ArgumentParser()
 ap.add_argument("--results-root",default=DEFAULT_RESULTS)
 ap.add_argument("--round-id",default=FAMILY_ID+"-r1")
 ap.add_argument("--run-id",default=None)
 ap.add_argument("--host-scripts",default=DEFAULT_SCRIPTS)
 ap.add_argument("--dry-run",action="store_true")
 ap.add_argument("--json",action="store_true")
 a=ap.parse_args(argv); run_id=a.run_id or a.round_id+"-u1"
 runner=Path(a.host_scripts)/"260_spy_open_close_run.py"
 test=Path(a.host_scripts)/"tests"/"test_spy_open_close_engine.py"
 family_dir=Path(a.results_root)/FAMILY_ID
 problems=[]
 try: family=load(family_dir/"family.json")
 except Exception as e: problems.append("family.json unreadable: %s"%e); family={}
 if family.get("family_id")!=FAMILY_ID: problems.append("family_id mismatch")
 if (family.get("handoff") or {}).get("execution")!="direct_hermes": problems.append("not direct_hermes")
 if any(k in family for k in ("task_id","kanban_task_id","kanban_board")): problems.append("family carries Kanban keys")
 if family.get("semantic_fingerprint")!=fingerprint(family.get("fingerprint_input")): problems.append("fingerprint mismatch")
 if not runner.is_file(): problems.append("runner missing: %s"%runner)
 if not test.is_file(): problems.append("self-check missing: %s"%test)
 if problems:
  print(json.dumps({"ok":False,"problems":problems},indent=2)); return 1
 mapping={"{{round_id}}":a.round_id,"{{run_id}}":run_id,"{{script_sha256}}":sha256(runner),
          "{{engine_selfcheck_sha256}}":sha256(test),"{{created_at_utc}}":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
 rs=substitute(load(ROUND_TEMPLATE),mapping); ws=substitute(load(RUN_TEMPLATE),mapping)
 for label,obj in (("round",rs),("run",ws)):
  if obj.get("family_id")!=FAMILY_ID: problems.append(label+" family mismatch")
  if obj.get("semantic_fingerprint")!=family.get("semantic_fingerprint"): problems.append(label+" fingerprint mismatch")
  problems.extend(label+": "+x for x in pc.validate_contract(obj.get("parameter_contract")))
 problems.extend("round: "+x for x in pc.validate_round_spec_contract(rs))
 if problems: print(json.dumps({"ok":False,"problems":problems},indent=2)); return 1
 round_dir=family_dir/"rounds"/a.round_id; attempt=round_dir/"attempts"/run_id
 if not a.dry_run:
  rp=round_dir/"round-spec.json"
  if rp.exists() and load(rp)!=rs: raise SystemExit("refusing overwrite: "+str(rp))
  if not rp.exists(): atomic_new(rp,rs)
  if attempt.exists(): raise SystemExit("refusing overwrite: "+str(attempt))
  atomic_new(attempt/"run-spec.json",ws)
 out={"ok":True,"dry_run":a.dry_run,"family_id":FAMILY_ID,"round_id":a.round_id,"run_id":run_id,
      "round_dir":str(round_dir),"attempt_dir":str(attempt),"runner_sha256":sha256(runner),
      "selfcheck_sha256":sha256(test),"expected_case_evaluations":17280}
 print(json.dumps(out,indent=2) if a.json else out)
 return 0

if __name__=="__main__": raise SystemExit(main())

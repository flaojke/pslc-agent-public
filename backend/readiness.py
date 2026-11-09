"""Read-only diagnostics, never an authorization/promotion mechanism."""
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from typing import Literal,Annotated
import argparse,json,time
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .config import ROOT,load_settings
from .operations import engine,status
from .store import Problem
from scripts.run_evals import fingerprint

Reference=Annotated[str,Field(pattern=r'^[A-Za-z0-9_.:/-]{0,128}$')]
class Target(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
 os_family:Literal['Windows','Linux']
 environment:Literal['test','workstation','server']
class PilotPlan(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 schema_version:Literal[1]
 targets:list[Target]=Field(default_factory=list,max_length=10)
 it_approval_reference:Reference=''
 security_approval_reference:Reference=''
 external_acceptance_reference:Reference=''
 recovery_reference:Reference=''
 retention_policy_reference:Reference=''
 shadow_evidence_reference:Reference=''
 shadow_start_utc:str=Field(default='',max_length=40)
 shadow_end_utc:str=Field(default='',max_length=40)
 @model_validator(mode='after')
 def checked(self):
  if self.targets and (len(self.targets)<5 or len({t.id for t in self.targets})!=len(self.targets) or {t.os_family for t in self.targets}!={'Windows','Linux'}):raise ValueError('pilot_target_set_invalid')
  if bool(self.shadow_start_utc)!=bool(self.shadow_end_utc):raise ValueError('shadow_interval_invalid')
  if self.shadow_start_utc:
   a=datetime.fromisoformat(self.shadow_start_utc.replace('Z','+00:00'));b=datetime.fromisoformat(self.shadow_end_utc.replace('Z','+00:00'))
   if a.tzinfo is None or b.tzinfo is None or not a<b<=datetime.now(timezone.utc):raise ValueError('shadow_interval_invalid')
  return self

def bounded_file(path,limit):
 p=Path(path)
 if not p.is_file() or p.is_symlink() or p.stat().st_size>limit:raise ValueError('evidence_unavailable')
 with p.open('rb') as stream:payload=stream.read(limit+1)
 if len(payload)>limit:raise ValueError('evidence_too_large')
 return payload

def eval_check(path):
 try:
  value=json.loads(bounded_file(path,1048576))
  if value['format']!='pslc-evals-v1' or value['source_fingerprint']!=fingerprint() or value['source_unchanged'] is not True:raise ValueError()
  if type(value['created']) not in (int,float) or not 0<=time.time()-value['created']<=86400:raise ValueError()
  expected={'A0'+str(i) for i in range(1,9)}
  if set(value['scenarios'])!=expected:raise ValueError()
  count=0
  for row in value['scenarios'].values():
   counts=row['counts']
   if row['state']!='passed' or row['exit_code']!=0 or row.get('external_acceptance')!='pending' or set(counts)!={'passed','failed','skipped'} or any(type(v)!=int or v<0 for v in counts.values()) or counts['failed'] or counts['skipped'] or counts['passed']<1:raise ValueError()
   cases=row['cases']
   if not isinstance(cases,list) or len(cases)!=counts['passed'] or len({case['name'] for case in cases})!=len(cases) or any(case['status']!='passed' or not isinstance(case['name'],str) or not case['name'].startswith('tests/') or len(case['name'])>300 for case in cases):raise ValueError()
   count+=counts['passed']
  if value['external_acceptance'] is not False:raise ValueError()
  return {'state':'verified_local','scenarios':8,'cases':count,'reason':'fresh_automated_evidence'}
 except (OSError,ValueError,TypeError,KeyError):return {'state':'failed','scenarios':0,'cases':0,'reason':'rerun_current_evals'}

def declaration(path):
 if not path:return None,'missing'
 try:return PilotPlan.model_validate_json(bounded_file(path,32768),strict=True),'declared_unverified'
 except (OSError,ValueError):return None,'invalid_declaration'

def report(settings,store=None):
 own=store is None
 if own:
  db=engine(settings.database_url);store=SimpleNamespace(engine=db,plans=SimpleNamespace(data={}),outputs=SimpleNamespace(data={}))
 try:
  try:dbcheck=status(store);database={'state':'verified_local','engine':dbcheck['engine'],'schema':dbcheck['schema']}
  except Problem:database={'state':'failed','reason':'database_unavailable'}
  evaluation=eval_check(settings.readiness_eval_path or ROOT/'docs/qa/evals/report.json')
  plan,plan_state=declaration(settings.pilot_plan_path)
  days=0.0
  if plan and plan.shadow_start_utc:days=(datetime.fromisoformat(plan.shadow_end_utc.replace('Z','+00:00'))-datetime.fromisoformat(plan.shadow_start_utc.replace('Z','+00:00'))).total_seconds()/86400
  def configured(condition):return 'configured_unverified' if condition else 'missing'
  def declared(field):return 'declared_unverified' if plan and getattr(plan,field) else 'missing'
  gates=[
   {'id':'model','label':'Модель / endpoint / качество на реальном кейсе','state':configured(bool(settings.model_url and settings.model_id))},
   {'id':'targets','label':'Подключение Windows/Linux узлов через систему','state':'declared_unverified' if plan and plan.targets else 'awaiting_onboarding'},
   {'id':'security','label':'Допуск ИБ','state':declared('security_approval_reference')},
   {'id':'it','label':'Допуск ИТ','state':declared('it_approval_reference')},
   {'id':'delivery','label':'Корпоративные PKI / подпись / SCM и systemd','state':'pending_external'},
   {'id':'recovery','label':'Backup storage / restore в выбранной среде','state':declared('recovery_reference')},
   {'id':'retention','label':'Хранение metadata / OS dumps / upstream logs','state':declared('retention_policy_reference')},
   {'id':'acceptance','label':'A01–A08 в реальной пилотной среде','state':declared('external_acceptance_reference')},
   {'id':'shadow','label':'≥14 дней shadow/dry-run и review','state':'declared_unverified' if plan and days>=14 and plan.shadow_evidence_reference else 'missing'}]
  deferred=[]
  if settings.auth_mode=='oidc':gates.insert(0,{'id':'identity','label':'Корпоративный IdP и группы','state':'configured_unverified'})
  else:deferred.append('AD / SSO')
  if settings.knowledge_enabled:gates.insert(0,{'id':'confluence','label':'Confluence / пространства / персональный ACL','state':configured(bool(settings.confluence_url and settings.confluence_spaces))})
  else:deferred.append('Confluence')
  return {'auth_mode':settings.auth_mode,'deferred_capabilities':deferred,'format':'pslc-readiness-v1','created':time.time(),'code_ready':database['state']=='verified_local' and evaluation['state']=='verified_local','database':database,'evals':evaluation,'declaration':plan_state,'declared_target_count':len(plan.targets) if plan else 0,'reported_shadow_days':round(days,2),'shadow_duration_reported_sufficient':days>=14,'shadow_evidence_verified':False,'autonomy_promotion':False,'production_admitted':False,'pilot_readiness':'requires_external_acceptance','external_gates':gates,'instructions':['docs/PILOT_GUIDE.md','docs/SECURITY_APPROVALS.md','docs/OPERATIONS.md'],'rerun_command':'python scripts/run_evals.py --postgres-config <private QA configuration>'}
 finally:
  if own:db.dispose()

def main():
 p=argparse.ArgumentParser(description='Read-only pilot preflight, no grant or execution');p.add_argument('--output',default='');a=p.parse_args()
 try:
  settings=load_settings();settings.validate();from .model_connection import ModelConnection
  settings=ModelConnection(settings).snapshot();value=report(settings);payload=json.dumps(value,ensure_ascii=False,indent=2)
  if a.output:
   path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True)
   with path.open('x',encoding='utf-8') as f:f.write(payload)
  else:print(payload)
  return 0 if value['code_ready'] else 2
 except Exception:print('{"error":"readiness_unavailable"}');return 2
if __name__=='__main__':raise SystemExit(main())

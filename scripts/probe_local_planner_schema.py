"""Actual fixed synthetic health prompts, real pinned CPU, no database or execution."""
from pathlib import Path
from dataclasses import replace
import json,tempfile,time,os
import backend.planner as planner_module
from pydantic import ValidationError
from backend.config import ROOT,load_settings
from backend.model_connection import ModelConnection
from backend.integrations import Integrations,IntegrationError
from backend.planner import Planner,ActionPlan,PlanningError
base=Path(tempfile.mkdtemp(prefix='planner-schema-',dir=ROOT/'.local'))
count=int(os.getenv('PSLC_SCHEMA_CASES','3'));assert 1<=count<=10
original=planner_module.bounded_json
def diagnostic(*args,**kwargs):
 data=original(*args,**kwargs);choice=data.get('choices',[{}])[0];message=choice.get('message',{});content=message.get('content')
 meta={'phase':'response_metadata','finish_reason':choice.get('finish_reason'),'content_chars':len(content or ''),'reasoning_chars':len(message.get('reasoning_content') or ''),'completion_tokens':data.get('usage',{}).get('completion_tokens')}
 try:planner_module.Proposal.validate_json(content,strict=True);meta['typed_valid']=True
 except (ValidationError,TypeError) as e:
  meta['typed_valid']=False;meta['validation_types']=sorted({x['type'] for x in e.errors()}) if isinstance(e,ValidationError) else ['non_string']
 print(json.dumps(meta),flush=True);return data
planner_module.bounded_json=diagnostic
s=replace(load_settings(),model_url='',model_token='',model_id='',model_config_path=str(base/'connection.enc'),local_models_enabled=True)
m=ModelConnection(s);i=Integrations(s);i.model_connection=m;p=Planner(i);results=[]
try:
 for n in range(count):
  did='00000000-0000-4000-8000-'+str(n+1).zfill(12)
  inventory=[{'id':did,'name':'QA-CPU-PC-01','os_family':'Windows','agent_protocol':1,'commands_enabled':False,'installations_enabled':False,'diagnostics_enabled':False}]
  start=time.monotonic()
  try:
   proposal,source=p.propose('Проверь состояние устройства '+did+'. Нужен ровно один шаг health:CPU/RAM/диск. Только чтение, без команд, установки или сбора журналов.',inventory,[])
   ok=isinstance(proposal,ActionPlan) and len(proposal.steps)==1 and proposal.steps[0].kind=='health' and proposal.steps[0].device_id==did and source=='configured_model'
   row={'case':n+1,'seconds':round(time.monotonic()-start,3),'health_plan':ok,'schema_constrained':True,'execution_admitted':False}
  except (IntegrationError,PlanningError) as e:row={'case':n+1,'seconds':round(time.monotonic()-start,3),'health_plan':False,'fixed_error':str(e),'execution_admitted':False}
  results.append(row);print(json.dumps(row),flush=True)
finally:m.local.close()
value={'version':'0.18.1','scope':'real pinned CPU/runtime, three fixed synthetic inventory IDs, no database/enrollment/actions','results':results,'all_passed':len(results)==count and all(r['health_plan'] for r in results),'raw_context_saved':False}
(ROOT/'docs/qa/local-model/schema-planner.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
if not value['all_passed']:raise SystemExit(1)

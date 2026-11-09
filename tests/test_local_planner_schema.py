"""The wire format constrains local syntax; authoritative validation remains independent."""
from types import SimpleNamespace
from contextlib import contextmanager
import json,httpx,pytest
from backend.planner import Planner,Proposal,PlanningError,local_planning_schema

@pytest.mark.parametrize('backend',['local','external'])
def test_local_planner_schema_and_unknown_target_remain_rejected(backend):
 calls=[]
 def handler(r):
  body=json.loads(r.content);calls.append(body)
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'status':'plan','steps':[{'kind':'health','device_id':'foreign-node'}]})}}]})
 class Integration:
  s=SimpleNamespace(model_backend=backend,model_token='',model_url='http://127.0.0.1/v1',model_id='qa')
  def status(self):return {'model':'configured_unverified'}
  def budget(self,s):return 120
  @contextmanager
  def model_client(self,s):
   with httpx.Client(transport=httpx.MockTransport(handler)) as c:yield c
 with pytest.raises(PlanningError,match='unknown_plan_target'):
  Planner(Integration()).propose('Проверь состояние node-1',[{'id':'node-1','name':'node-1','os_family':'Windows'}],[])
 assert len(calls)==1
 if backend=='local':assert calls[0]['response_format']=={'type':'json_schema','json_schema':{'name':'pslc_action_plan','strict':True,'schema':local_planning_schema([{'id':'node-1','name':'node-1','os_family':'Windows'}],[])}}
 else:assert 'response_format' not in calls[0]

@pytest.mark.parametrize('mode',['empty','health','mixed'])
def test_local_grammar_has_only_eligible_targets_and_reachable_definitions(mode):
 nodes=[] if mode=='empty' else [{'id':'read-only','os_family':'Windows'},{'id':'mutable','os_family':'Windows','agent_protocol':1,'commands_enabled':mode=='mixed','installations_enabled':mode=='mixed','diagnostics_enabled':mode=='mixed'}]
 schema=local_planning_schema(nodes,[{'os_family':'Windows'}])
 defs=schema['$defs']
 if mode=='empty':assert 'ActionPlan' not in defs and 'HealthStep' not in defs
 else:
  assert defs['HealthStep']['properties']['device_id']['enum']==['mutable','read-only']
  if mode=='health':assert set(defs)=={'ActionPlan','HealthStep','Clarification','Unsupported'}
  else:
   for kind in ('CommandStep','InstallStep','InvestigationStep'):
    assert defs[kind]['properties']['device_id']['enum']==['mutable']
   assert 'RecoveryResources' in defs
 def check(node):
  if isinstance(node,dict):
   assert 'discriminator' not in node
   if '$ref' in node:assert node['$ref'].split('/')[-1] in defs
   for v in node.values():check(v)
  elif isinstance(node,list):
   for v in node:check(v)
 check(schema)

@pytest.mark.parametrize('fail',[False,True])
def test_local_request_failure_stops_owned_engine_before_admission_unlock(fail):
 from backend.integrations import Integrations,IntegrationError,bounded_json
 from backend.config import Settings
 state={'locked':False,'stopped':0}
 class Local:
  @contextmanager
  def generation(self,mid):
   assert not state['locked'];state['locked']=True
   try:yield
   finally:state['locked']=False
  def _stop(self):assert state['locked'];state['stopped']+=1
 def handler(request):
  if fail:raise httpx.ReadTimeout('public fixture',request=request)
  return httpx.Response(200,json={'ok':True})
 s=Settings('public-test-no-secret','sqlite://',model_backend='local',model_url='http://127.0.0.1/v1',model_id='qa')
 i=Integrations(s,httpx.MockTransport(handler));i.model_connection=SimpleNamespace(local=Local())
 def run():
  with i.model_client(s) as c:return bounded_json(c,'POST',s.model_url+'/chat/completions',json={'messages':[]})
 if fail:
  with pytest.raises(IntegrationError,match='upstream_unavailable'):run()
 else:assert run()=={'ok':True}
 assert state=={'locked':False,'stopped':int(fail)}

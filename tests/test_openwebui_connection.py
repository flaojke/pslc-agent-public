import json
import httpx,pytest
from scripts.check_openwebui import check
from backend.integrations import IntegrationError
KEY='PUBLIC-FIXTURE-OPENWEBUI-KEY-123'
@pytest.mark.parametrize('mode',['list','probe','bad-json','missing','redirect'])
def test_openwebui_paths_bearer_exact_ids_json_and_no_fallback(mode):
 calls=[]
 def handler(r):
  calls.append(r);assert r.headers['authorization']=='Bearer '+KEY
  if r.url.path.endswith('/models'):
   if mode=='redirect':return httpx.Response(302,headers={'location':'https://external.invalid/steal'})
   return httpx.Response(200,json={'data':[{'id':'qwen-fixture'},{'id':'gemma-fixture'},{'id':KEY}]})
  assert r.url.path=='/api/chat/completions'
  body=json.loads(r.content);assert body['model']=='qwen-fixture' and body['tools']==[] and body['tool_ids']==[] and KEY not in r.content.decode()
  return httpx.Response(200,json={'choices':[{'message':{'content':'raw-response-marker' if mode=='bad-json' else '{"status":"ok"}'}}]})
 transport=httpx.MockTransport(handler)
 if mode=='redirect':
  with pytest.raises(IntegrationError):check('https://openwebui.invalid',KEY,transport=transport)
  assert len(calls)==1;return
 r=check('https://openwebui.invalid',KEY,'' if mode=='list' else 'unavailable' if mode=='missing' else 'qwen-fixture',transport)
 assert KEY not in json.dumps(r) and 'raw-response-marker' not in json.dumps(r)
 assert r['status']=={'list':'model_list_received','probe':'json_probe_passed','bad-json':'json_contract_failed','missing':'model_not_available'}[mode]
 assert r['real_task_quality_verified'] is False

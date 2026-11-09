"""Bounded Open WebUI diagnostic: no stored key, conversation, or model free text."""
from pathlib import Path
import sys,argparse,getpass,os,re,ssl,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from backend.integrations import validate_endpoint,bounded_json,IntegrationError

def check(url,key,model='',transport=None,ca_file=''):
 validate_endpoint(url)
 if not key or len(key)>8192 or any(ord(c)<33 or ord(c)>126 for c in key):raise ValueError('invalid_key')
 base=url.rstrip('/');base=base if base.endswith('/api') else base+'/api'
 with httpx.Client(transport=transport,timeout=15,follow_redirects=False,trust_env=False,verify=ssl.create_default_context(cafile=ca_file or None)) as client:
  data=bounded_json(client,'GET',base+'/models',headers={'Authorization':'Bearer '+key},budget_seconds=20)
  if not isinstance(data,dict):raise ValueError('invalid_model_list')
  rows=data.get('data')
  if not isinstance(rows,list) or len(rows)>2000:raise ValueError('invalid_model_list')
  ids=sorted({row['id'] for row in rows if isinstance(row,dict) and isinstance(row.get('id'),str) and re.fullmatch(r'[A-Za-z0-9_./:@+~-]{1,128}',row['id']) and key not in row['id']})
  if not model:return {'status':'model_list_received','model_ids':ids,'real_task_quality_verified':False}
  if model not in ids:return {'status':'model_not_available','real_task_quality_verified':False}
  data=bounded_json(client,'POST',base+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'stream':False,'max_tokens':256,'response_format':{'type':'json_object'},'tools':[],'tool_ids':[],'messages':[{'role':'system','content':'Return ONLY the exact JSON object {"status":"ok"}. Do not call tools or execute actions.'},{'role':'user','content':'JSON contract connectivity probe; no operational context.'}]},budget_seconds=30)
  try:content=json.loads(data['choices'][0]['message']['content'])
  except (ValueError,KeyError,TypeError,IndexError):return {'status':'json_contract_failed','real_task_quality_verified':False}
  return {'status':'json_probe_passed' if content=={'status':'ok'} else 'json_contract_failed','model_id':model,'real_task_quality_verified':False}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);parser.add_argument('--model',default='');parser.add_argument('--ca-file',default='');a=parser.parse_args()
 try:
  key=os.getenv('PSLC_MODEL_TOKEN') or getpass.getpass('Open WebUI API key (hidden): ')
  result=check(a.url,key,a.model,ca_file=a.ca_file);print(json.dumps(result,ensure_ascii=False));return 0 if result['status'] in ('model_list_received','json_probe_passed') else 2
 except (ValueError,IntegrationError,httpx.HTTPError,OSError):print('{"status":"connection_rejected_or_unavailable","key_or_response_echoed":false}');return 2
if __name__=='__main__':raise SystemExit(main())

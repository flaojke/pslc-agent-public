"""Local real HTTP OIDC protocol fixture. No corporate IdP, no agent execution."""
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode,parse_qs
import base64,hashlib,json,secrets,tempfile,time
import jwt,uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.schemas import Enroll

base=Path(tempfile.mkdtemp(prefix='identity-qa-',dir=ROOT/'.local'))
issuer='http://127.0.0.1:8795/realm';app_url='http://127.0.0.1:8796';client_id='identity-browser-fixture'
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='fixture-key',alg='RS256',use='sig')
codes={};pending={};idp=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@idp.get('/realm/.well-known/openid-configuration')
def discovery():return {'issuer':issuer,'authorization_endpoint':issuer+'/authorize','token_endpoint':issuer+'/token','jwks_uri':issuer+'/jwks','code_challenge_methods_supported':['S256']}
@idp.get('/realm/jwks')
def keys():return {'keys':[jwk]}
@idp.get('/realm/authorize')
def authorize(request:Request):
    query=dict(request.query_params)
    if query.get('client_id')!=client_id or query.get('redirect_uri')!=app_url+'/auth/callback' or query.get('code_challenge_method')!='S256':raise HTTPException(400)
    ticket=secrets.token_urlsafe(24);pending[ticket]=(time.time()+300,query)
    return HTMLResponse('<html lang="ru"><meta charset="utf-8"><body style="background:#102130;color:#d5ecec;font:20px Arial;padding:60px"><h1>QA IdP — локальный fixture</h1><p>Проверка протокола OIDC/PKCE. Это не корпоративный вход.</p>'+''.join('<p><a style="color:#74dfcd" href="/realm/approve?'+urlencode({'ticket':ticket,'actor':actor})+'">Войти как '+actor+'</a></p>' for actor in ['Alice','Bob','Auditor'])+'</body></html>')
@idp.get('/realm/approve')
def approve(ticket:str,actor:str):
    item=pending.pop(ticket,None)
    if not item or item[0]<time.time() or actor not in ('Alice','Bob','Auditor'):raise HTTPException(400)
    code=secrets.token_urlsafe(32);codes[code]=(time.time()+60,item[1],actor)
    return RedirectResponse(item[1]['redirect_uri']+'?'+urlencode({'state':item[1]['state'],'code':code}),303)
@idp.post('/realm/token')
async def token(request:Request):
    form=parse_qs((await request.body()).decode());item=codes.pop(form.get('code',[''])[0],None)
    if not item or item[0]<time.time():raise HTTPException(400)
    query=item[1];actor=item[2]
    verifier=form.get('code_verifier',[''])[0];challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    if form.get('client_id')!=[client_id] or form.get('redirect_uri')!=[query['redirect_uri']] or challenge!=query['code_challenge']:raise HTTPException(400)
    value=jwt.encode({'iss':issuer,'aud':client_id,'sub':actor.lower(),'iat':int(time.time()),'exp':int(time.time())+600,'nonce':query['nonce'],'groups':['IB'] if actor=='Auditor' else ['IT']},key,algorithm='RS256',headers={'kid':'fixture-key'})
    return {'id_token':value,'token_type':'Bearer'}

policy=base/'policy.json';policy.write_text(json.dumps({'schema_version':1,'groups':{'IT':{'roles':['operator'],'device_ids':['*'],'knowledge_spaces':['IT']},'IB':{'roles':['auditor'],'device_ids':['*']}}}),encoding='utf-8')
app=create_app(Settings('unused-fixture-local-token-long-enough','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='oidc',auth_policy_path=str(policy),oidc_issuer=issuer,oidc_client_id=client_id,oidc_api_audience='pslc-api',oidc_redirect_uri=app_url+'/auth/callback'))
store=app.state.store
node=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='oidc-qa-node',os_family='Windows',commands_enabled=True))
(ROOT/'.local/identity-browser-qa.json').write_text(json.dumps({'url':app_url,'issuer':issuer,'directory':str(base),'device_id':node['device_id']}),encoding='utf-8')
Thread(target=lambda:uvicorn.run(idp,host='127.0.0.1',port=8795,access_log=False,proxy_headers=False,log_level='warning'),daemon=True).start()
uvicorn.run(app,host='127.0.0.1',port=8796,access_log=False,proxy_headers=False,workers=1,log_level='warning')

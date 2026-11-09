"""Explicit operator identity, trusted policy and bounded OIDC sessions."""
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from urllib.parse import urlsplit,urlencode
import base64,hashlib,json,secrets,time
from pathlib import Path
from typing import Literal
import httpx,jwt
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from fastapi.responses import JSONResponse,RedirectResponse
from .integrations import bounded_json,validate_endpoint,IntegrationError
from .store import Problem,Volatile

LOCAL_ID='local-development'
current_principal=ContextVar('pslc_principal',default=None)
from .rbac import ALL,role_permits
ROLES=set(ALL)

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def subject_id(issuer,subject):return digest(issuer+'\0'+subject)
def origin(value):
    parsed=urlsplit(value)
    return parsed.scheme.lower()+'://'+parsed.netloc.lower()

class Grant(BaseModel):
    model_config=ConfigDict(extra='forbid')
    roles:list[Literal['operator','auditor','reviewer','platform_admin']]=Field(default_factory=list,max_length=4)
    device_ids:list[str]=Field(default_factory=list,max_length=500)
    knowledge_spaces:list[str]=Field(default_factory=list,max_length=100)
    disabled:bool=False

class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    schema_version:Literal[1]
    subjects:dict[str,Grant]=Field(default_factory=dict,max_length=1000)
    groups:dict[str,Grant]=Field(default_factory=dict,max_length=100)

@dataclass(frozen=True)
class Identity:
    id:str
    groups:tuple[str,...]
    expires:float
    revision:int=0

@dataclass(frozen=True)
class Principal:
    id:str
    roles:frozenset[str]
    device_ids:frozenset[str]
    knowledge_spaces:frozenset[str]
    expires:float
    def device(self,did):return '*' in self.device_ids or did in self.device_ids
    def operator(self):return role_permits(self.roles,'operate')
    def audit(self):return role_permits(self.roles,'audit_metadata')

class OperatorAuth:
    def __init__(self,settings,transport=None,store=None):
        self.s=settings;self.transport=transport;self.lock=RLock()
        self.identities=Volatile(1024);self.sessions=Volatile(1024);self.states=Volatile(128)
        self.accounts=None;self.login_attempts=Volatile(1024)
        if settings.auth_mode=='accounts':
            if store is None:raise ValueError('account_store_required')
            from .accounts import LocalAccounts
            self.accounts=LocalAccounts(store)
        self.discovery=None;self.discovery_until=0;self.keys={};self.keys_until=0;self.keys_fetched=0

    def local(self):return Principal(LOCAL_ID,frozenset(ROLES),frozenset({'*'}),frozenset({'*'}),float('inf'))

    def policy(self):
        try:
            path=Path(self.s.auth_policy_path)
            if not path.is_file() or path.stat().st_size>131072:raise ValueError()
            result=Policy.model_validate_json(path.read_bytes(),strict=True)
            for key in result.subjects:
                if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError()
            for group in result.groups:
                if not 1<=len(group)<=128:raise ValueError()
            for grant in list(result.groups.values())+list(result.subjects.values()):
                if any(not isinstance(x,str) or not 1<=len(x)<=64 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-*' for c in x) for x in grant.device_ids+grant.knowledge_spaces):raise ValueError()
            return result
        except (OSError,ValueError,ValidationError):raise Problem('operator_policy_unavailable',403) from None

    def grant(self,identity):
        if self.s.auth_mode=="accounts":return self.accounts.principal(identity)
        if identity.expires<=time.time():raise Problem('operator_unauthorized',401)
        policy=self.policy();specific=policy.subjects.get(identity.id)
        grants=([specific] if specific else [])+[policy.groups[g] for g in identity.groups if g in policy.groups]
        if not grants or any(g.disabled for g in grants):raise Problem('operator_forbidden',403)
        roles=frozenset(x for g in grants for x in g.roles)
        if not roles:raise Problem('operator_forbidden',403)
        return Principal(identity.id,roles,frozenset(x for g in grants for x in g.device_ids),frozenset(x for g in grants for x in g.knowledge_spaces),identity.expires)

    def known(self,owner):
        if self.s.auth_mode=='local' and owner==LOCAL_ID:return self.local()
        with self.lock:identity=self.identities.get(owner)
        if identity is None:raise Problem('owner_access_revoked',403)
        return self.grant(identity)

    def authorize_owner(self,owner,did):
        principal=self.known(owner)
        if not principal.operator() or not principal.device(did):raise Problem('owner_access_revoked',403)
        return principal

    def remember(self,identity):
        principal=self.grant(identity)
        with self.lock:self.identities.put(identity.id,identity,identity.expires-time.time())
        return principal

    def remote(self,url,method='GET',**kwargs):
        try:
            with httpx.Client(transport=self.transport,timeout=10,follow_redirects=False,trust_env=False) as client:
                return bounded_json(client,method,url,budget_seconds=15,**kwargs)
        except IntegrationError:raise Problem('identity_provider_unavailable',503) from None

    def metadata(self):
        with self.lock:
            if self.discovery and self.discovery_until>time.time():return self.discovery
            data=self.remote(self.s.oidc_issuer.rstrip('/')+'/.well-known/openid-configuration')
            try:
                if data['issuer']!=self.s.oidc_issuer or 'S256' not in data.get('code_challenge_methods_supported',[]):raise ValueError()
                for field in ('authorization_endpoint','token_endpoint','jwks_uri'):
                    validate_endpoint(data[field])
                    if origin(data[field])!=origin(self.s.oidc_issuer):raise ValueError()
            except (KeyError,TypeError,ValueError,IntegrationError):raise Problem('invalid_identity_metadata',503) from None
            self.discovery=data;self.discovery_until=time.time()+300
            return data

    def key(self,kid):
        with self.lock:
            if self.keys_until>time.time() and kid in self.keys:return self.keys[kid]
            if self.keys_until>time.time() and time.time()-self.keys_fetched<5:raise Problem('operator_unauthorized',401)
            data=self.remote(self.metadata()['jwks_uri'])
            try:
                if not isinstance(data['keys'],list) or not 1<=len(data['keys'])<=32:raise ValueError()
                keys={}
                for row in data['keys']:
                    if not isinstance(row,dict):raise ValueError()
                    if row.get('kty')!='RSA' or row.get('use','sig')!='sig' or row.get('alg','RS256')!='RS256' or 'd' in row:continue
                    if 'key_ops' in row and 'verify' not in row['key_ops']:continue
                    name=row.get('kid','')
                    if not isinstance(name,str) or not 1<=len(name)<=128 or name in keys:raise ValueError()
                    key=jwt.PyJWK.from_dict(row,algorithm='RS256').key
                    if key.key_size<2048:raise ValueError()
                    keys[name]=key
                if not keys:raise ValueError()
            except (ValueError,TypeError,KeyError,jwt.PyJWTError):raise Problem('invalid_identity_keys',503) from None
            self.keys=keys;self.keys_until=time.time()+60;self.keys_fetched=time.time()
            if kid not in keys:raise Problem('operator_unauthorized',401)
            return keys[kid]

    def verify(self,token,audience,nonce=None):
        try:
            if not isinstance(token,str) or len(token)>16384:raise ValueError()
            header=jwt.get_unverified_header(token)
            kid=header.get('kid')
            if header.get('alg')!='RS256' or not isinstance(kid,str) or not 1<=len(kid)<=128 or 'jku' in header or 'x5u' in header:raise ValueError()
            claims=jwt.decode(token,self.key(kid),algorithms=['RS256'],audience=audience,issuer=self.s.oidc_issuer,
                options={'require':['exp','iat','sub','iss','aud']},leeway=30)
            if not isinstance(claims['sub'],str) or not 1<=len(claims['sub'])<=255:raise ValueError()
            if type(claims['iat']) not in (int,float) or type(claims['exp']) not in (int,float):raise ValueError()
            if claims['exp']<=claims['iat'] or time.time()-claims['iat']>900:raise ValueError()
            if nonce is not None and (not isinstance(claims.get('nonce'),str) or not secrets.compare_digest(nonce,claims['nonce'])):raise ValueError()
            if nonce is not None and ('azp' in claims or isinstance(claims['aud'],list) and len(claims['aud'])>1) and claims.get('azp')!=self.s.oidc_client_id:raise ValueError()
            groups=claims.get('groups',[])
            if not isinstance(groups,list) or len(groups)>64 or any(not isinstance(g,str) or not 1<=len(g)<=128 for g in groups):raise ValueError()
            return Identity(subject_id(self.s.oidc_issuer,claims['sub']),tuple(groups),min(claims['exp'],claims['iat']+900))
        except (jwt.PyJWTError,ValueError,TypeError,KeyError):raise Problem('operator_unauthorized',401) from None

    def authenticate(self,request):
        if self.s.auth_mode=='local':
            if not secrets.compare_digest(request.headers.get('authorization','').encode(),('Bearer '+self.s.operator_token).encode()):raise Problem('operator_unauthorized',401)
            return self.local()
        header=request.headers.get('authorization','')
        if self.s.auth_mode=='accounts' and header:raise Problem('operator_unauthorized',401)
        if header:
            if not header.startswith('Bearer '):raise Problem('operator_unauthorized',401)
            return self.remember(self.verify(header[7:],self.s.oidc_api_audience))
        cookie=request.cookies.get('pslc_session','')
        with self.lock:identity=self.sessions.get(digest(cookie)) if cookie else None
        if identity is None:raise Problem('operator_unauthorized',401)
        if request.method not in ('GET','HEAD','OPTIONS'):self.same_origin(request)
        return self.remember(identity)

    def same_origin(self,request):
        if request.headers.get('origin')!=origin(self.s.public_origin if self.s.auth_mode=='accounts' else self.s.oidc_redirect_uri):raise Problem('csrf_rejected',403)

    def account_login(self,request,data):
        if self.s.auth_mode!='accounts':raise Problem('not_found',404)
        self.same_origin(request)
        key=digest(str(request.client.host if request.client else '')+'\0'+data.username.strip().lower())
        with self.lock:
            failures=self.login_attempts.get(key) or 0
            if failures>=5:raise Problem('login_throttled',429)
            self.login_attempts.put(key,failures+1,30)
        try:identity=self.accounts.login(data.username,data.password)
        except Problem:raise
        self.remember(identity);sid=secrets.token_urlsafe(32)
        with self.lock:
            self.login_attempts.drop(key);self.sessions.put(digest(sid),identity,900)
        response=JSONResponse({'status':'signed_in'})
        response.set_cookie('pslc_session',sid,max_age=900,httponly=True,samesite='strict',secure=self.s.public_origin.startswith('https://'),path='/')
        return response

    def start(self):
        if self.s.auth_mode!='oidc':raise Problem('not_found',404)
        state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with self.lock:self.states.put(digest(state),{'nonce':nonce,'verifier':verifier},300)
        params={'client_id':self.s.oidc_client_id,'redirect_uri':self.s.oidc_redirect_uri,'response_type':'code','scope':'openid profile','state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256'}
        response=RedirectResponse(self.metadata()['authorization_endpoint']+'?'+urlencode(params),303)
        response.set_cookie('pslc_login',state,max_age=300,httponly=True,samesite='lax',secure=self.s.oidc_redirect_uri.startswith('https://'),path='/auth')
        return response

    def callback(self,request):
        if self.s.auth_mode!='oidc':raise Problem('not_found',404)
        state=request.query_params.get('state','');code=request.query_params.get('code','');cookie=request.cookies.get('pslc_login','')
        if not 1<=len(code)<=2048 or not state or len(state)>128 or not secrets.compare_digest(state.encode(),cookie.encode()):raise Problem('invalid_login_state',401)
        with self.lock:
            pending=self.states.get(digest(state));self.states.drop(digest(state))
        if pending is None:raise Problem('invalid_login_state',401)
        payload={'grant_type':'authorization_code','code':code,'redirect_uri':self.s.oidc_redirect_uri,'client_id':self.s.oidc_client_id,'code_verifier':pending['verifier']}
        options={'data':payload}
        if self.s.oidc_client_secret:options['auth']=httpx.BasicAuth(self.s.oidc_client_id,self.s.oidc_client_secret)
        data=self.remote(self.metadata()['token_endpoint'],'POST',**options)
        identity=self.verify(data.get('id_token'),self.s.oidc_client_id,pending['nonce']);self.remember(identity)
        session=secrets.token_urlsafe(32)
        with self.lock:self.sessions.put(digest(session),identity,identity.expires-time.time())
        response=RedirectResponse('/',303)
        response.set_cookie('pslc_session',session,max_age=max(1,int(identity.expires-time.time())),httponly=True,samesite='strict',secure=self.s.oidc_redirect_uri.startswith('https://'),path='/')
        response.delete_cookie('pslc_login',path='/auth')
        return response

    def logout(self,request):
        cookie=request.cookies.get('pslc_session','')
        if self.s.auth_mode in ('oidc','accounts') and cookie:
            self.same_origin(request)
            with self.lock:
                identity=self.sessions.get(digest(cookie));self.sessions.drop(digest(cookie))
                if identity:self.identities.drop(identity.id)
        response=JSONResponse({'status':'signed_out'});response.delete_cookie('pslc_session',path='/');response.delete_cookie('pslc_login',path='/auth')
        return response

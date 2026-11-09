"""Synthetic Confluence/model HTTP contract fixture, never a real integration."""
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from dataclasses import replace
import httpx,uvicorn
from fastapi import Header,HTTPException
from starlette.routing import Mount
from backend.app import create_app
from backend.config import Settings,ROOT
from test_knowledge_retrieval import Upstream
base=Path(tempfile.mkdtemp(prefix='knowledge-qa-',dir=ROOT/'.local'));upstream=Upstream()
token='knowledge-browser-public-fixture-token'
app=create_app(Settings(token,'sqlite:///'+(base/'qa.sqlite3').as_posix(),confluence_url='https://confluence.fixture.invalid/wiki',confluence_token='alice-personal-token',confluence_spaces=('IT',),model_url='https://model.fixture.invalid/v1',model_id='QA-FIXTURE-not-real-LLM'),httpx.MockTransport(upstream.handler))
@app.post('/qa/revoke')
def revoke(authorization:str=Header(default='')):
 if authorization!='Bearer '+token:raise HTTPException(403)
 upstream.allowed=False;return {'revoked':True}
mounts=[route for route in app.routes if isinstance(route,Mount)]
for route in mounts:app.routes.remove(route);app.routes.append(route)
(ROOT/'.local/knowledge-browser-qa.json').write_text(json.dumps({'url':'http://127.0.0.1:8794','database':str(base/'qa.sqlite3')}),encoding='utf-8')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8794,access_log=False,log_level='warning')

"""Isolated browser fixture. All device metrics/model responses are synthetic; no host commands."""
from pathlib import Path
from threading import Thread
import json,socket,tempfile,time
import httpx,uvicorn
from sqlalchemy import update
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.schemas import Enroll,Result
from backend.store import devices
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
url=f'http://127.0.0.1:{port}'
base=Path(tempfile.mkdtemp(prefix='case-browser-',dir=ROOT/'.local'))
meta={'model_calls':0,'health_jobs':0,'multi_turn_seen':False}
def handle(request):
    body=json.loads(request.content);messages=body['messages'];meta['model_calls']+=1
    users=[m['content'] for m in messages if m['role']=='user'];meta['multi_turn_seen']=meta['multi_turn_seen'] or len(users)>1
    if users and 'Отмена' in users[-1]:time.sleep(1.2);answer='LATE-FIXTURE-REPLY'
    else:answer=f'Учтено сообщений в этой беседе: {len(users)}. Гипотеза требует проверки. Фактические результаты узла ещё не получены; подготовьте отдельный план диагностики.'
    return httpx.Response(200,json={'choices':[{'message':{'content':answer}}]})
app=create_app(Settings('case-browser-public-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False,model_url='https://case-fixture.invalid/v1',model_id='fixture-dialogue'),httpx.MockTransport(handle))
store=app.state.store
nodes=[]
for name,os,kind in [('QA-PC-128','Windows','workstation'),('QA-PC-147','Windows','workstation'),('QA-LINUX-PC','Linux','workstation'),('QA-WIN-SERVER','Windows','server'),('QA-UNKNOWN','Linux','unknown')]:
    n=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name=name,os_family=os));nodes.append(n)
    with store.tx() as c:c.execute(update(devices).where(devices.c.id==n['device_id']).values(device_kind=kind))
@app.get('/qa/status')
def qa_status():return {**meta,'scope':'synthetic model/device browser fixture','real_host_actions':False}
def tick():
    while True:
        for n in nodes:
            try:
                job=store.poll(n['device_id'],n['credential'])
                if job:
                    if job['payload']['kind']!='health':raise RuntimeError('fixture rejects all non-health actions')
                    store.finish(n['device_id'],n['credential'],job['task_id'],Result(lease_id=job['lease_id'],outcome='ok',exit_code=0,cpu_percent=5,memory_percent=21,disk_free_gb=123));meta['health_jobs']+=1
            except Exception:pass
        time.sleep(.2)
Thread(target=tick,daemon=True).start()
(ROOT/'.local/case-browser-qa.json').write_text(json.dumps({'url':url,'directory':str(base),'scope':'synthetic browser fixture'}),encoding='utf-8')
qa_route=next(r for r in app.router.routes if getattr(r,'path',None)=='/qa/status')
app.router.routes.remove(qa_route);app.router.routes.insert(0,qa_route)
uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False)

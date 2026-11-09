import {RecoveryActions} from './RecoveryActions';
import {RecoveryStatus} from './RecoveryPreview';
import {systemDateOptions,displayTimezone} from './time';
import {MessageText} from './MessageText';
import React,{useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {PlanStep,states,errors,type Flow} from './AgentWorkspace';
import {InvestigationResult} from './InvestigationResult';

type Api=<T>(path:string,method?:string,body?:unknown)=>Promise<T>;
type ActiveModel={id:string;provider:string;status:string};
type Device={id:string;name:string;os_family:string;device_kind?:string;online:boolean;revoked:boolean;virtual?:boolean};
type Scope={kind:string;ids:string[]};
type Temp={question:string;answer:string|null;expires:number};
type Turn={id:string;state:string;source:string;created:number;temporary:Temp|null;temporary_status:string;workflow:Flow|null};
type Conversation={id:string;title:string;target_scope:Scope;updated:number;turns?:Turn[];turns_truncated?:boolean;turn_count?:number;context_expires_in?:number};
const scopeLabels:Record<string,string>={none:'Цели не выбраны',all:'Все машины',demo:'Виртуальное демо',workstations:'Пользовательские ПК',servers:'Серверы',manual:'Выбрать вручную'};
const caseErrors:Record<string,string>={...errors,conversation_busy:'В этой беседе уже готовится ответ. Дождитесь его или остановите.',conversation_capacity:'Все слоты диалога заняты. Повторите позже.',conversation_limit:'Достигнут лимит100 бесед. Удалите ненужную.',conversation_context_expired:'Полный контекст истёк. Кратко опишите задачу снова.',target_selection_required:'Выберите устройства или группу для плана.',target_unavailable:'Одно из устройств недоступно по текущим правам.',conversation_has_active_tasks:'В беседе есть активные задания. Для удаления сначала запросите их отмену.',volatile_capacity_reached:'Не хватает временной ёмкости для полного плана. Уменьшите область запроса.'};
const date=(t:number)=>new Date(t*1000).toLocaleString('ru-RU',systemDateOptions());
const title=(s:string)=>states[s]??s;
const busyStates=['queued','generating'];
const terminalSuccess=['verified','already_installed','restored'];
const phases=[['clarify','Уточнение'],['facts','Факты'],['plan','План'],['check','Проверка']] as const;

export function CaseWorkspace({api,devices,onMutate,operatorId,canOperate=false,selectedId,onSelect}:{api:Api;devices:Device[];modelStatus?:string;onMutate:()=>Promise<void>;operatorId?:string;canOperate?:boolean;selectedId:string;onSelect:(id:string)=>void}){
 const client=useRef(api);client.current=api;
 const [chats,setChats]=useState<Conversation[]>([]),[active,setActive]=useState<Conversation|null>(null);
 const [host,setHost]=useState<HTMLElement|null>(null),[question,setQuestion]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [rename,setRename]=useState(false),[name,setName]=useState(''),[clock,setClock]=useState(Date.now()),[model,setModel]=useState<ActiveModel|null>(null);
 const [targetOpen,setTargetOpen]=useState(false),[filter,setFilter]=useState(''),[page,setPage]=useState<Record<string,number>>({});
 const [phaseOpen,setPhaseOpen]=useState<Record<string,boolean>>({clarify:true,facts:false,plan:false,check:false});
 const selected=useRef(selectedId);selected.current=selectedId;
 const mounted=useRef(true),editor=useRef<HTMLTextAreaElement|null>(null);
 const actor=useRef(operatorId),actorEpoch=useRef(0);
 if(actor.current!==operatorId){actor.current=operatorId;actorEpoch.current++;}
 const currentActor=(epoch:number)=>mounted.current&&actorEpoch.current===epoch;
 const caseUnavailable=active?active.id!==selectedId:!!selectedId;
 const modelEpoch=useRef(0),modelFlight=useRef<{promise:Promise<void>;timeout:ReturnType<typeof setTimeout>}|null>(null);
 useEffect(()=>{mounted.current=true;setHost(document.getElementById('conversation-navigation'));return()=>{mounted.current=false;actorEpoch.current++;};},[]);
 async function refresh(){
  const epoch=actorEpoch.current;
  const list=await client.current<Conversation[]>('/conversations');
  if(!currentActor(epoch))return;
  setChats(list);const id=list.some(c=>c.id===selected.current)?selected.current:list[0]?.id;
  if(!id){setActive(null);return;}
  if(id!==selected.current)onSelect(id);
  const value=await client.current<Conversation>('/conversations/'+id);
  if(currentActor(epoch)&&(selected.current===id||!selected.current))setActive(value);
 }
 useEffect(()=>{
  let loading=false;
  const load=async()=>{if(loading)return;loading=true;try{await refresh();}catch(e){if(mounted.current)setError(caseErrors[(e as Error).message]??'Не удалось обновить беседу.');}finally{loading=false;}};
  void load();const poll=setInterval(()=>void load(),2000);const tick=setInterval(()=>setClock(Date.now()),1000);
  return()=>{clearInterval(poll);clearInterval(tick);};
 },[]);
 useEffect(()=>{const epoch=actorEpoch.current;if(selectedId){void client.current<Conversation>('/conversations/'+selectedId).then(value=>{if(currentActor(epoch)&&selected.current===selectedId)setActive(value);}).catch(e=>{if(currentActor(epoch)&&selected.current===selectedId)setError(caseErrors[(e as Error).message]??'Беседа недоступна.');});}setQuestion('');setRename(false);setPage({});},[selectedId,operatorId]);
 function refreshModel():Promise<void>{
  if(modelFlight.current)return modelFlight.current.promise;
  const epoch=++modelEpoch.current;
  const current=()=>mounted.current&&modelEpoch.current===epoch;
  let operation:Promise<ActiveModel>;
  try{operation=client.current<ActiveModel>('/model/active');}catch(e){operation=Promise.reject(e);}
  let expired=false;
  const timeout=setTimeout(()=>{expired=true;if(current())setModel(null);},5000);
  const promise=operation.then(value=>{
   if(!value||typeof value.id!=='string'||!['local','external'].includes(value.provider)||typeof value.status!=='string')throw new Error('invalid_active_model');
   if(current()&&!expired)setModel({id:value.id,provider:value.provider,status:value.status});
  }).catch(()=>{if(current())setModel(null);}).finally(()=>{clearTimeout(timeout);if(modelEpoch.current===epoch)modelFlight.current=null;});
  modelFlight.current={promise,timeout};return promise;
 }
 useEffect(()=>{
  setModel(null);void refreshModel();
  const poll=setInterval(()=>void refreshModel(),2000);
  const focused=()=>void refreshModel();
  const visible=()=>{if(document.visibilityState==='visible')void refreshModel();};
  window.addEventListener('focus',focused);document.addEventListener('visibilitychange',visible);
  return()=>{clearInterval(poll);window.removeEventListener('focus',focused);document.removeEventListener('visibilitychange',visible);modelEpoch.current++;if(modelFlight.current)clearTimeout(modelFlight.current.timeout);modelFlight.current=null;};
 },[operatorId]);
 const turns=active?.turns??[],pending=turns.find(t=>busyStates.includes(t.state));
 const dialogueTurns=turns.filter(t=>!t.workflow),lastTextTurn=dialogueTurns.at(-1);
 const flows=turns.filter(t=>t.workflow).map(t=>t.workflow!);
 const latest=flows.at(-1);
 const lastTransition=useRef('');
 useEffect(()=>{
  const key=(latest?.id??'')+':'+(latest?.state??'');
  if(key===lastTransition.current)return;lastTransition.current=key;
  if(latest?.state==='awaiting_confirmation')setPhaseOpen(p=>({...p,plan:true}));
  if(latest&&['verified','partial','outcome_unknown','stopped'].includes(latest.state))setPhaseOpen(p=>({...p,check:true}));
 },[latest?.id,latest?.state]);

 const scope=active?.target_scope??{kind:'none',ids:[]};
 const targets=devices.filter(d=>!d.revoked&&(scope.kind==='all'||scope.kind==='demo'&&d.virtual||scope.kind==='workstations'&&!d.virtual&&d.device_kind==='workstation'||scope.kind==='servers'&&!d.virtual&&d.device_kind==='server'||scope.kind==='manual'&&scope.ids.includes(d.id)));
 const readyFacts=flows.flatMap(f=>f.steps.filter(s=>terminalSuccess.includes(s.state)&&!s.recovery?.historical).map(s=>({...s,flow:f.id})));
 async function act(fn:()=>Promise<unknown>){const epoch=actorEpoch.current;setBusy(true);setError('');try{await fn();if(currentActor(epoch))await refresh();if(currentActor(epoch))await onMutate();}catch(e){if(currentActor(epoch))setError(caseErrors[(e as Error).message]??'Запрос не выполнен. Проверьте состояние и повторите.');}finally{if(currentActor(epoch))setBusy(false);}}
 async function create(){
  const epoch=actorEpoch.current,selection=selected.current;
  await act(async()=>{const c=await client.current<Conversation>('/conversations','POST',{title:'Беседа · '+new Date().toLocaleTimeString('ru-RU',systemDateOptions())});if(!currentActor(epoch)||selected.current!==selection)return;onSelect(c.id);selected.current=c.id;setActive(c);setQuestion('');});
 }
 async function chooseScope(kind:string,ids:string[]=[],id=active?.id){
  if(!id||!active)return;
  await act(async()=>{const value=await client.current<Conversation>('/conversations/'+id,'PUT',{title:active.title,scope:{kind,ids}});setActive(value);setPage({});});
 }
 async function send(){
  if(!question.trim()||busy||pending||!canOperate||(active?active.id!==selected.current:!!selected.current))return;
  void refreshModel();
  let cid=active?.id;const q=question,selection=selected.current,epoch=actorEpoch.current;
  await act(async()=>{if(!cid){const c=await client.current<Conversation>('/conversations','POST',{});if(!currentActor(epoch)||selected.current!==selection)return;cid=c.id;onSelect(cid);selected.current=cid;setActive(c);}
   if(!currentActor(epoch)||selected.current!==cid)return;
   await client.current('/conversations/'+cid+'/messages','POST',{request_id:crypto.randomUUID(),question:q});if(currentActor(epoch)&&selected.current===cid)setQuestion('');});
 }
 async function plan(){
  if(!active||active.id!==selected.current||busy||pending||!canOperate)return;
  void refreshModel();
  const cid=active.id,q=question.trim(),epoch=actorEpoch.current;
  await act(async()=>{await client.current('/conversations/'+cid+'/plans','POST',{request_id:crypto.randomUUID(),question:q});if(currentActor(epoch)&&selected.current===cid){setQuestion('');setPhaseOpen(p=>({...p,plan:true}));}});
 }
 async function remove(c:Conversation){
  if(!window.confirm('Удалить беседу «'+c.title+'»? Её полный временный контекст будет удалён. Активным заданиям будет запрошена отмена; это не откат. Метаданные аудита сохранятся.'))return;
  await act(async()=>{await client.current('/conversations/'+c.id+'/delete','POST',{cancel_tasks:true});if(selected.current===c.id){onSelect('');selected.current='';setActive(null);}setQuestion('');});
 }
 async function improvement(tid:string){
  await act(async()=>{const v=await client.current<{filename:string;markdown:string}>('/tasks/'+tid+'/improvement');const url=URL.createObjectURL(new Blob([v.markdown],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=v.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 }
 const navigation=<section className="conversation-nav" aria-label="Беседы"><div className="conversation-nav-title"><h2>Беседы</h2><button type="button" className="ghost" disabled={busy||!canOperate} onClick={()=>void create()} aria-label="Создать новую беседу">＋</button></div>
 <div className="conversation-list">{!chats.length&&<p className="muted">Создайте беседу для отдельной проблемы.</p>}{chats.map(c=><div key={c.id} className={'conversation-row '+(c.id===selectedId?'chosen':'')}><button className="conversation-name" onClick={()=>{selected.current=c.id;onSelect(c.id);setQuestion('');}}><span>{c.title}</span><small>{date(c.updated)}</small></button><button className="conversation-delete" aria-label={'Удалить беседу '+c.title} disabled={busy||!canOperate} onClick={()=>void remove(c)}>×</button></div>)}</div>
 <button className="primary new-conversation" disabled={busy||!canOperate} onClick={()=>void create()}>＋ Новая беседа</button></section>;
 return <section className="case-workspace">
 {host&&createPortal(navigation,host)}
 <div className="case-heading"><div><span className="eyebrow">ДИАЛОГ · ХОД РАЗБОРА</span><h2>{active?.title??'Новая проблема — новая беседа'}</h2></div>{active&&<button className="ghost" disabled={busy||!canOperate} onClick={()=>{setName(active.title);setRename(!rename);}}>Переименовать</button>}</div>
 {rename&&active&&<form className="rename-conversation" onSubmit={e=>{e.preventDefault();void act(async()=>{setActive(await client.current<Conversation>('/conversations/'+active.id,'PUT',{title:name}));setRename(false);});}}><label htmlFor="conversation-title">Короткое имя беседы <small>Сохраняется в истории. Не вставляйте журналы или секреты.</small></label><input id="conversation-title" value={name} onChange={e=>setName(e.target.value)} maxLength={80} required/><button className="primary" disabled={busy}>Сохранить имя</button></form>}
 <form className="case-composer" onSubmit={e=>{e.preventDefault();void send();}}>
 <div className="case-context"><label htmlFor="target-scope">Целевые машины<select id="target-scope" aria-label="Целевые машины" value={scope.kind} disabled={!active||busy||!!pending||!canOperate} onChange={e=>void chooseScope(e.target.value)}>{Object.entries(scopeLabels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label><button type="button" className="ghost target-count" disabled={!active} onClick={()=>setTargetOpen(!targetOpen)}>Цели: {targets.length} · показать список</button><span className="model-label" aria-live="polite">Активная модель: {model?model.id||'не подключена':'статус недоступен'}{model?.provider==='local'?' · CPU':''}{model?.status==='local_runtime_policy_blocked'?' · Windows блокирует запуск — Настройки → Модели':''}{model?.status==='local_unavailable'?' · требуется загрузка в «Настройки → Модели»':''}</span></div>
 {targetOpen&&<div className="target-picker"><label htmlFor="target-filter">Найти устройство<input id="target-filter" value={filter} onChange={e=>setFilter(e.target.value)} placeholder="Имя или ID"/></label><p className="muted">Тип ПК/сервер назначается в «Устройствах». Неизвестный тип не входит в эти группы.</p><ul>{devices.filter(d=>!d.revoked&&(d.name+' '+d.id).toLowerCase().includes(filter.toLowerCase())).map(d=><li key={d.id}><label><input type="checkbox" checked={targets.some(x=>x.id===d.id)} disabled={busy||!!pending||!canOperate} onChange={e=>{const ids=targets.map(x=>x.id).filter(id=>id!==d.id);if(e.target.checked)ids.push(d.id);void chooseScope('manual',ids);}}/><span>{d.name}<small>{d.virtual?'Демо · только состояние':(d.device_kind==='server'?'Сервер':d.device_kind==='workstation'?'Пользовательский ПК':'Тип не указан')+' · '+d.os_family} · {d.online?'На связи':'Нет свежих данных'}</small></span></label></li>)}</ul></div>}
 <label htmlFor="case-message">Сообщение агенту<textarea id="case-message" aria-label="Сообщение агенту" ref={editor} value={question} onChange={e=>setQuestion(e.target.value)} disabled={!canOperate} maxLength={8000} rows={3} placeholder="Опишите проблему. Уточнения продолжат текущую беседу." onKeyDown={e=>{if(e.key==='Enter'&&e.ctrlKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/></label>
 <div className="case-composer-actions"><small>Ctrl+Enter — отправить · Enter — новая строка</small><div><button type="button" className="ghost" disabled={!active||caseUnavailable||busy||!!pending||!targets.length||!canOperate} onClick={()=>void plan()}>Подготовить план</button><button type="submit" className="primary" disabled={caseUnavailable||busy||!!pending||!question.trim()||!canOperate}>{busy?'Обработка…':'Отправить →'}</button></div></div>
 {caseUnavailable&&<p role="status">Выбранная беседа загружается… Отправка и план будут доступны после загрузки.</p>}
 <p className="case-retention">Полный контекст временный, до30минут. В истории — краткое имя, метаданные и проверенные итоги. Сообщение не запускает команду.</p>
 </form>
 {error&&<div role="alert" className="error">{error}</div>}
 <div className="case-status" role="status" aria-live="polite"><span>Беседа {active?.id.slice(0,8)??'ещё не создана'}</span><span>{pending?(pending.state==='queued'?'Ответ в очереди':'Модель готовит разбор'):latest?title(latest.state):'Ожидает сообщения'}</span>{pending&&active&&<button className="ghost" onClick={()=>void act(()=>client.current('/conversations/'+active.id+'/messages/'+pending.id+'/cancel','POST'))}>Остановить ответ</button>}</div>
 {active?.turns_truncated&&<p className="notice">Показаны последние 60 из {active.turn_count} ходов. Метаданные более ранних задач сохраняются в системе.</p>}<div className="case-phases">{phases.map(([id,label])=><details key={id} open={phaseOpen[id]} className={'case-phase phase-'+id} onToggle={e=>{const open=e.currentTarget.open;setPhaseOpen(p=>p[id]===open?p:{...p,[id]:open});}}><summary><span className="phase-dot"/><h3>{label}</h3><small>{id==='clarify'?turns.filter(t=>!t.workflow).length+' сообщений':id==='facts'?readyFacts.length+' проверенных шагов':id==='plan'?flows.length+' планов':latest?title(latest.state):'Действия не выполнялись'}</small></summary>
 {id==='clarify'&&<div className="dialogue-thread">{!turns.length&&<div className="case-empty"><h3>Начните с проблемы</h3><p>Создайте отдельную беседу о доступности, конкретной машине или анализе журналов. Для действия выберите цели и подготовьте план.</p></div>}{dialogueTurns.map(t=>{const raw=t.temporary&&t.temporary.expires*1000>clock?t.temporary:null;return <article key={t.id} className="dialogue-turn"><small>{date(t.created)}</small>{raw?<><div className="user-message"><b>Вы</b><MessageText text={raw.question} latest={t.id===lastTextTurn?.id&&!raw.answer} author="пользователя"/></div>{raw.answer&&<div className="agent-message"><b>PSLC Agent · {t.source==='not_configured'?'модель не подключена':'разбор модели, требует проверки'}</b><MessageText text={raw.answer} latest={t.id===lastTextTurn?.id} author="агента"/></div>}</>:<p className="muted">Полный контекст истёк или утрачен при перезапуске. Сохранились метаданные хода.</p>}{t.state==='failed'&&<p className="error">Ответ не получен. Проверьте модель или попробуйте более короткий запрос.</p>}{t.state==='cancelled'&&<p className="muted">Ответ отменён. Вычисление может завершиться, но его результат не будет добавлен.</p>}{busyStates.includes(t.state)&&<p role="status">{t.state==='queued'?'В очереди':'Готовится ответ…'}</p>}</article>;})}</div>}
 {id==='facts'&&<div className="case-facts">{readyFacts.length>30&&<p className="muted">Последние30 наблюдений. Полный список шагов — в «Проверке».</p>}{!readyFacts.length?<p className="muted">Проверенных наблюдений пока нет. Ответ модели не является результатом диагностики.</p>:readyFacts.slice(-30).map(s=><article key={s.task_id}><b>✓ {devices.find(d=>d.id===s.device_id)?.name??s.device_id} · {s.kind==='health'?'Состояние ресурсов':s.kind==='investigate'?'Сбор журналов':'Постусловие'}</b><small>Задача {s.task_id} · {title(s.state)}</small><dl>{Object.entries(s.summary??{}).filter(([k])=>['cpu_percent','memory_percent','disk_free_gb','postcondition_matched','version_matched','source'].includes(k)).map(([k,v])=><div key={k}><dt>{{cpu_percent:'CPU, %',memory_percent:'RAM, %',disk_free_gb:'Свободно, ГБ',postcondition_matched:'Постусловие',version_matched:'Версия',source:'Источник'}[k]}</dt><dd>{v===true?'Подтверждено':v==='demo_simulation'?'Демо · синтетические данные':String(v)}</dd></div>)}</dl></article>)}</div>}
 {id==='plan'&&<div className="case-plans">{!flows.length&&<p className="muted">Выберите машины и нажмите «Подготовить план». Точные действия, цели и проверки появятся здесь.</p>}{flows.map(f=><article className="case-plan" key={f.id} data-workflow-id={f.id}><div className="plan-header"><h3>План {f.id.slice(0,8)}</h3><span className="status">{title(f.state)}</span></div><small>{f.source==='approved_template'?'Явный безопасный профиль состояния, без генерации команд моделью':f.source.includes('model')?'Предложен настроенной моделью':'Локальные правила · без LLM'} · {f.steps.length} шагов</small>
 {f.preview?.question&&<p className="notice">{f.preview.question}</p>}{f.preview?.reason&&<p className="notice">{f.preview.reason}</p>}
 {f.preview?.steps&&f.preview_expires*1000>clock?<><details className="plan-targets"><summary>Точные цели: {[...new Set(f.steps.map(s=>s.device_id))].length} · показать все</summary><ul>{[...new Set(f.steps.map(s=>s.device_id))].map(did=><li key={did}>{devices.find(d=>d.id===did)?.name??did} · <code>{did}</code></li>)}</ul></details><p className="muted">План действует до {date(f.expires)}. При изменении целей подготовьте новый.</p>{f.preview.steps.slice((page[f.id]??0)*20,((page[f.id]??0)+1)*20).map(step=><PlanStep key={step.task_id} step={step}/>)}{f.preview.steps.length>20&&<div className="plan-pagination"><button className="ghost" disabled={(page[f.id]??0)===0} onClick={()=>setPage(p=>({...p,[f.id]:Math.max(0,(p[f.id]??0)-1)}))}>← Назад</button><span>Страница {(page[f.id]??0)+1} из {Math.ceil(f.preview.steps.length/20)} · все шаги входят в подтверждение</span><button className="ghost" disabled={((page[f.id]??0)+1)*20>=f.preview.steps.length} onClick={()=>setPage(p=>({...p,[f.id]:(p[f.id]??0)+1}))}>Далее →</button></div>}</>:f.steps.length>0&&<p className="muted">Полный временный план больше недоступен. Состояния и краткие результаты сохранены.</p>}
 {f.state==='awaiting_confirmation'&&f.preview?.steps&&f.expires*1000>clock&&f.owner_id===operatorId&&<div className="actions"><button className="primary" disabled={busy||!canOperate} onClick={()=>void act(()=>client.current('/workflows/'+f.id+'/decision','POST',{approve:true,plan_hash:f.plan_hash}))}>Подтвердить на {[...new Set(f.steps.map(s=>s.device_id))].length} устройствах</button><button className="ghost" disabled={busy||!canOperate} onClick={()=>void act(()=>client.current('/workflows/'+f.id+'/decision','POST',{approve:false,plan_hash:f.plan_hash}))}>Отклонить</button></div>}
 {['queued','running','outcome_unknown','cancel_requested'].includes(f.state)&&f.owner_id===operatorId&&<button className="ghost" disabled={busy||!canOperate} onClick={()=>void act(()=>client.current('/workflows/'+f.id+'/cancel','POST'))}>Запросить отмену задания</button>}</article>)}</div>}
 {id==='check'&&<div className="case-results">{!flows.length&&<p className="muted">Результаты появятся после подтверждённого выполнения.</p>}{flows.map(f=><article key={f.id}><h3>{title(f.state)} · {f.steps.filter(s=>terminalSuccess.includes(s.state)).length}/{f.steps.length} шагов проверено</h3><small>Общий успех требует проверки всех шагов. Сбор данных не доказывает причину инцидента.</small><div className="table-wrap"><table><thead><tr><th>Устройство</th><th>Действие</th><th>Исход</th><th>Дальше</th></tr></thead><tbody>{f.steps.map(s=><tr key={s.task_id}><td>{devices.find(d=>d.id===s.device_id)?.name??s.device_id.slice(0,8)}</td><td>{s.kind}</td><td>{title(s.state)}</td><td>{s.state==='outcome_unknown'&&f.owner_id===operatorId?<button className="ghost" onClick={()=>{if(window.confirm('Вы проверили на узле, что процесс больше не работает? Отметка ошибки освободит слот, но не отменит внешний эффект.'))void act(()=>client.current('/tasks/'+s.task_id+'/reconcile','POST',{outcome:'failed',externally_checked_not_running:true}));}}>После сверки: отметить ошибку</button>:terminalSuccess.includes(s.state)&&f.owner_id===operatorId?<button className="ghost" onClick={()=>void improvement(s.task_id)}>Экспорт OpenSpec</button>:null}<RecoveryStatus value={s.recovery} kind={s.kind}/></td></tr>)}</tbody></table></div><div className="step-recoveries">{f.steps.map(s=><section key={'recovery-'+s.task_id} aria-label={'Восстановление '+(devices.find(d=>d.id===s.device_id)?.name??s.device_id.slice(0,8))}>{['command','install'].includes(s.kind)&&f.owner_id===operatorId&&<RecoveryActions key={'undo-'+s.task_id} api={api} id={s.task_id} existingFlowId={s.recovery?.latest_recovery_workflow_id} session={f.session_id} enabled={canOperate&&!['not_needed','not_required','expired','unavailable','not_recorded'].includes(s.recovery?.status??'not_recorded')&&!['awaiting_confirmation','queued','running','cancel_requested','outcome_unknown'].includes(s.state)} onMutate={async()=>{await refresh();await onMutate();}}/>}</section>)}</div>{f.source!=='manual_rollback'&&f.steps.some(s=>['command','install'].includes(s.kind))&&f.owner_id===operatorId&&<RecoveryActions api={api} id={f.id} scope="workflows" session={f.session_id} enabled={canOperate&&!['awaiting_confirmation','queued','running','cancel_requested','outcome_unknown'].includes(f.state)} onMutate={async()=>{await refresh();await onMutate();}}/>}{f.steps.filter(s=>s.kind==='investigate'&&s.investigation).map(s=><InvestigationResult key={s.task_id} taskId={s.task_id} api={api} summary={s.summary} meta={s.investigation!} temporaryAllowed={canOperate&&f.owner_id===operatorId}/>)}</article>)}</div>}
 </details>)}</div>
 </section>;
}

import {systemDateOptions,displayTimezone} from './time';
import React,{useEffect,useRef,useState} from 'react';
export type DiagnosticScope={source:string;start_utc:string;end_utc:string;max_events:number;channels?:string[];unit?:string};
type Hypothesis={category:string;confidence:string;evidence_ids:string[];explanation?:string;next_checks?:string[]};
export type InvestigationMeta={scope:DiagnosticScope;scope_hash:string;analysis_state:string;analysis_meta:{model_id?:string;hypotheses?:Hypothesis[];conclusion?:string;cause_status:string}};
type Report={meta:InvestigationMeta;assessment:{conclusion:string;hypotheses:Hypothesis[]}|null;assessment_expires:number|null;temporary_status:string};
type Reference={ref:string;at:string;channel:string;severity:string;event_id:number|null;record_id:number|null};
type Api=<T>(path:string,method?:string,body?:unknown)=>Promise<T>;
const analysisLabels:Record<string,string>={awaiting_collection:'Ожидаем сбор',pending:'Анализ в очереди',analyzing:'Модель анализирует данные',ready:'Гипотезы подготовлены',not_configured:'Модель не подключена',insufficient_evidence:'Недостаточно наблюдений для гипотезы',not_requested:'Анализ не запускался',invalid_response:'Ответ модели не прошёл проверку',upstream_error:'Модель недоступна',expired:'Временный контекст истёк',context_lost:'Контекст утрачен при перезапуске',cancelled:'Анализ остановлен',capacity_limited:'Лимит анализа: сохранены наблюдения'};
const coverageLabels:Record<string,string>={complete:'Чтение завершено в заданных границах',truncated:'Достигнут лимит событий',timeout:'Не хватило времени',output_limit:'Достигнут лимит объёма',access_denied:'Нет доступа',collector_failed:'Ошибка коллектора',prerequisite_missing:'Коллектор недоступен',parse_error:'Данные прочитаны не полностью'};
const categories:Record<string,string>={service_failure:'Сбой службы',authentication:'Аутентификация',resource_pressure:'Недостаток ресурсов',application_error:'Ошибка приложения',configuration:'Конфигурация',network:'Сеть',unknown:'Причина не определена'};
const severity:Record<string,string>={critical:'Критическое',error:'Ошибка',warning:'Предупреждение'};
const time=(value:string)=>new Date(value).toLocaleString('ru-RU',systemDateOptions())+' '+displayTimezone();

export function ScopeReview({scope}:{scope:DiagnosticScope}){
 return <div className="verification-box diagnostic-scope"><b>Область расследования</b><p>{scope.source==='windows_events'?'Windows Event Log · '+scope.channels?.join(', '):'systemd journal · '+scope.unit}</p><p>{time(scope.start_utc)} → {time(scope.end_utc)}</p><p>До {scope.max_events} событий · 64 КиБ · 30 с · Critical / Error / Warning</p><small>Чтение в пределах прав процесса агента. Затем возможен анализ в настроенной модели. Причина и исправление требуют отдельной проверки.</small></div>;
}

export function InvestigationResult({taskId,summary,meta,api,temporaryAllowed}:{taskId:string;summary:Record<string,unknown>|null;meta:InvestigationMeta;api:Api;temporaryAllowed:boolean}){
 const client=useRef(api);client.current=api;
 const [report,setReport]=useState<Report|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[clock,setClock]=useState(Date.now());
 useEffect(()=>{
  if(!temporaryAllowed)return;
  let disposed=false,loading=false;
  async function load(){if(loading)return;loading=true;try{const data=await client.current<Report>('/tasks/'+taskId+'/investigation');if(data.assessment_expires&&data.assessment_expires*1000<=Date.now())data.assessment=null;if(!disposed)setReport(data);}catch{if(!disposed)setError('Не удалось получить состояние анализа.');}finally{loading=false;}}
  void load();const poll=setInterval(()=>void load(),2000);const ttl=setInterval(()=>{setClock(Date.now());setReport(value=>value?.assessment_expires&&value.assessment_expires*1000<=Date.now()?{...value,assessment:null}:value);},1000);
  return()=>{disposed=true;clearInterval(poll);clearInterval(ttl);};
 },[taskId,temporaryAllowed]);
 const info=report?.meta??meta;
 const references=Array.isArray(summary?.references)?summary.references as Reference[]:[];
 const coverage=Array.isArray(summary?.coverage)?summary.coverage as {target:string;status:string}[]:[];
 const hypotheses=report?.assessment&&report.assessment_expires&&report.assessment_expires*1000>clock?report.assessment.hypotheses:info.analysis_meta.hypotheses??[];
 const hasText=!!(report?.assessment&&report.assessment_expires&&report.assessment_expires*1000>clock);
 return <section className="investigation-result"><div className="investigation-heading"><h3>Наблюдения и гипотезы</h3><span className="status" data-testid="analysis-status">{analysisLabels[info.analysis_state]??info.analysis_state}</span></div>
 <p className="cause-unconfirmed">Причина пока не установлена</p><p className="muted">Проверка сбора подтверждает метаданные выбранного интервала. Совпадение времени событий не доказывает причинную связь.</p>
 <div className="coverage-grid">{coverage.map(item=><div key={item.target}><b>{item.target==='journal'?meta.scope.unit:item.target}</b><span>{coverageLabels[item.status]??item.status}</span></div>)}</div>
 {summary&&<><h4>Факты из метаданных</h4><p>Получено событий: {String(summary.collected_count??0)} · critical: {String(summary.critical_count??0)} · error: {String(summary.error_count??0)} · warning: {String(summary.warning_count??0)}</p>{Number(summary.messages_truncated)>0&&<p className="notice">Текст {String(summary.messages_truncated)} событий ограничен по размеру. Контекст неполный.</p>}</>}
 {references.length>0&&<details className="evidence-list"><summary>Ссылки на наблюдения · {references.length}</summary>{references.map(ref=><article key={ref.ref}><div><b>{ref.channel} · {severity[ref.severity]}</b><small>{time(ref.at)}</small></div><p>{ref.event_id!==null?'Event ID '+ref.event_id+' · Record ID '+ref.record_id:'systemd · opaque event reference'}</p><code>{ref.ref}</code></article>)}</details>}
 <div className="hypothesis-panel"><h4>Гипотезы модели · не подтверждены</h4>{info.analysis_meta.model_id&&<small>Модель: {info.analysis_meta.model_id}</small>}
 {info.analysis_state==='not_configured'&&<p>Для интерпретации журналов подключите модель. Сейчас доступны только наблюдения.</p>}
 {info.analysis_state==='ready'&&!hasText&&<p className="muted">Сохранены категории и ссылки. Свободный текст анализа истёк или не восстанавливается после обновления страницы.</p>}
 {hypotheses.map((h,i)=><article key={i} className="hypothesis"><b>{categories[h.category]??h.category} · уверенность {h.confidence==='low'?'низкая':'средняя'}</b>{hasText&&h.explanation&&<p>{h.explanation}</p>}<small>Evidence: {h.evidence_ids.map(id=>id.slice(0,12)).join(', ')}</small>{hasText&&h.next_checks?.length!==0&&<ul>{h.next_checks?.map((check,j)=><li key={j}>{check}</li>)}</ul>}</article>)}
 {hasText&&<small>Подробный текст временный до {new Date(report!.assessment_expires!*1000).toLocaleTimeString('ru-RU',systemDateOptions())} {displayTimezone()}. Предложения проверки не исполняются автоматически.</small>}
 {['pending','analyzing'].includes(info.analysis_state)&&<button className="ghost" disabled={busy} onClick={()=>{setBusy(true);void client.current<Report>('/tasks/'+taskId+'/investigation/cancel','POST').then(setReport).catch(()=>setError('Не удалось остановить анализ.')).finally(()=>setBusy(false));}}>Остановить анализ</button>}
 </div>{error&&<p role="alert" className="error">{error}</p>}
 </section>;
}

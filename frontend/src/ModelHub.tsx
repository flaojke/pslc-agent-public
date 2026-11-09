import {systemDateOptions,displayTimezone} from './time';
import {useEffect,useRef,useState} from 'react';
import {ModelSettings} from './ModelSettings';
type Api=<T>(path:string,method?:string,body?:unknown)=>Promise<T>;
type LocalModel={id:string;title:string;bytes:number;sha256:string;license:string;state:string;downloaded_bytes:number;error:string|null};
type Profile={id:string;title:string;server_url:string;model_id:string;has_key:boolean};
type Hub={revision:string;active:{provider:string;model_id:string;profile_id:string};profiles:Profile[];configuration_error:boolean;local:{enabled:boolean;default:string;models:LocalModel[];runtime:{state:string;model_id:string|null;error:string|null;context_tokens:number;parallel_generations:number;queued:number;cpu_only:boolean}}};
const states:Record<string,string>={missing:'Не загружена',interrupted:'Загрузка прервана',downloading:'Скачивается',verifying:'Проверяем SHA-256',ready:'Файл проверен',failed:'Ошибка',cancelled:'Отменено',cancelling:'Отмена…',loading:'Запуск модели на CPU',stopped:'Остановлена'};
const errors:Record<string,string>={model_disk_space:'Недостаточно места для модели и временных данных.',model_download_busy:'Уже выполняется загрузка. Дождитесь её или отмените.',model_integrity_failed:'Размер или SHA-256 не совпал. Файл не активирован; повторите загрузку.',model_download_failed:'Не удалось скачать модель. Проверьте доступ сервера к Hugging Face и повторите.',model_redirect_rejected:'Источник вернул недопустимое перенаправление.',model_configuration_changed:'Выбор уже изменён. Обновите страницу настроек.',local_model_missing:'Сначала скачайте и проверьте файл выбранной модели.',local_runtime_policy_blocked:'Windows запретила запуск CPU runtime по политике контроля приложений или проверки подписи. Согласуйте доверенный runtime с ИБ либо разрешённую Linux/OpenWebUI-среду; подробности — в инструкции развёртывания.',local_runtime_dependencies:'Не выполнены системные требования CPU runtime. В Linux проверьте libgomp1, libstdc++6, OpenSSL3 и ldd по инструкции развёртывания; в Windows — совместимость x64/runtime.',local_runtime_integrity:'Файлы CPU runtime отсутствуют или изменены. Восстановите их из проверенного дистрибутива.',local_runtime_start_failed:'Не удалось запустить CPU runtime. Проверьте совместимость сервера с дистрибутивом.',local_runtime_timeout:'Модель не успела запуститься.',model_profile_limit:'Достигнут лимит восьми профилей. Удалите ненужное подключение.',local_model_busy:'Очередь CPU-модели заполнена.',local_model_queue_timeout:'Истекло ожидание CPU-модели.'};
const bytes=(n:number)=>(n/1e9).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ГБ';
export function ModelHub({api,onMutate}:{api:Api;onMutate:()=>Promise<void>}){
 const [hub,setHub]=useState<Hub|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[formEpoch,setFormEpoch]=useState(0);
 const alive=useRef(true);
 async function load(){const next=await api<Hub>('/settings/providers');if(alive.current)setHub(next);}
 useEffect(()=>{alive.current=true;void load().catch(()=>setError('Не удалось загрузить список моделей.'));const timer=setInterval(()=>void load().catch(()=>{}),2000);return()=>{alive.current=false;clearInterval(timer);};},[]);
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn();await load();await onMutate();}catch(e){if(alive.current)setError(errors[(e as Error).message]??'Не удалось выполнить действие. Проверьте настройки и повторите.');}finally{if(alive.current)setBusy(false);}}
 async function choose(provider:string,model_id='',profile_id=''){
  await api('/settings/providers/active','POST',{provider,model_id,profile_id,revision:hub!.revision});setFormEpoch(x=>x+1);
  setNotice(provider==='local'?'Выбрана локальная модель для новых запросов. Проверьте запуск и JSON-контракт перед использованием.':provider==='disabled'?'ИИ отключён. Сохранённые профили остались доступны.':'Выбран профиль Open WebUI. Локальные веса и остальные подключения сохранены.');
 }
 const active=hub?.active,local=hub?.local;
 return <div className="model-hub">
 {local?.enabled&&<section className="panel local-models"><div className="panel-title"><div><span className="eyebrow">ЛОКАЛЬНЫЙ ИИ · CPU</span><h2>Модели на вашем сервере</h2><p>Qwen3.5-4B по умолчанию · работа без видеокарты · 32 ГБ RAM</p></div><span className="status">{active?.provider==='local'?'Активно: '+active.model_id:active?.provider==='openwebui'?'Активно: Open WebUI':'ИИ отключён'}</span></div>
 <div className="notice"><b>Одна генерация на CPU</b><p>Контекст {local.runtime.context_tokens.toLocaleString('ru-RU',systemDateOptions())} токенов. В очереди: {local.runtime.queued}. Фоновые задания на устройствах исполняются отдельно с прежними лимитами.</p><small>Локальный режим обрабатывает рабочие запросы на сервере. Интернет нужен для загрузки весов по вашему нажатию.</small></div>
 <div className="local-model-grid">{local.models.map(m=>{const downloading=['downloading','verifying','cancelling'].includes(m.state),selected=active?.provider==='local'&&active.model_id===m.id;return <article key={m.id} className={'local-model-card'+(selected?' selected-model':'')}>
 <div className="panel-title"><h3>{m.title}</h3>{selected&&<span className="status good">Выбрана</span>}</div>
 <p>{bytes(m.bytes)} · {m.license}</p><span className="status">{states[m.state]??m.state}</span>
 {downloading&&<><progress aria-label={'Загрузка '+m.title} value={m.downloaded_bytes} max={m.bytes}/><small>{bytes(m.downloaded_bytes)} из {bytes(m.bytes)}</small></>}
 <small>SHA-256: <code>{m.sha256.slice(0,20)}…</code></small>
 {m.error&&<div className="error" role="alert">{errors[m.error]??'Загрузка не завершена. Повторите попытку.'}</div>}
 <div className="actions">{m.state!=='ready'&&!downloading&&<button className="primary" disabled={busy} onClick={()=>void run(async()=>{await api('/settings/local-models/'+m.id+'/download','POST');})}>{['failed','cancelled','interrupted'].includes(m.state)?'Повторить загрузку ':'Скачать '}{m.title}</button>}
 {downloading&&<button className="ghost" disabled={busy||m.state==='cancelling'} onClick={()=>void run(async()=>{await api('/settings/local-models/'+m.id+'/cancel','POST');})}>Отменить загрузку {m.title}</button>}
 {m.state==='ready'&&<button className="primary" disabled={busy||selected} onClick={()=>void run(()=>choose('local',m.id))}>Использовать {m.title}</button>}</div>
 </article>;})}</div>
 <div className="model-runtime"><b>CPU runtime: {local.runtime.state==='ready'?'Работает':states[local.runtime.state]??local.runtime.state}</b>{local.runtime.state==='ready'&&<small>{local.runtime.model_id} · GPU не используется</small>}{local.runtime.error&&<div className="error" role="alert">{errors[local.runtime.error]??'Локальная модель пока недоступна. Проверьте файлы и повторите.'}</div>}
 <div className="actions"><button className="ghost" disabled={busy||active?.provider!=='local'||local.models.find(m=>m.id===active.model_id)?.state!=='ready'} onClick={()=>void run(async()=>{const r=await api<{status:string;duration_ms:number}>('/settings/local-models/check','POST');setNotice(r.status==='json_probe_passed'?'Локальная модель проверена: JSON-контракт выполнен. Время '+(r.duration_ms/1000).toFixed(1)+' с.':'Ответ модели не прошёл JSON-проверку.');})}>Проверить локальную модель</button>
 <button className="ghost" disabled={busy||active?.provider==='disabled'} onClick={()=>void run(()=>choose('disabled'))}>Отключить ИИ</button></div></div>
 </section>}
 {!!hub?.profiles.length&&<section className="panel"><div className="panel-title"><div><h2>Сохранённые подключения</h2><p>Переключайтесь между моделями без повторного ввода ключа.</p></div></div><div className="saved-models">{hub.profiles.map(p=><article className="local-model-card" key={p.id}><h3>{p.title}</h3><p>{p.server_url}</p><small>Ключ сохранён на сервере</small><button className="ghost" disabled={busy||active?.profile_id===p.id} onClick={()=>void run(()=>choose('openwebui','',p.id))}>{active?.profile_id===p.id?'Активный профиль':'Использовать профиль '+p.title}</button></article>)}</div></section>}
 {notice&&<div role="status" className="notice">{notice}</div>}{error&&error!==(local?.runtime.error?errors[local.runtime.error]:undefined)&&<div role="alert" className="error">{error}</div>}
 <ModelSettings api={api} refreshKey={formEpoch} onMutate={async()=>{await load();await onMutate();}}/>
 </div>;
}

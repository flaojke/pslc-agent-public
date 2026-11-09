import {systemDateOptions,displayTimezone} from './time';
import {useEffect,useRef,useState} from 'react';
type Api=<T>(path:string,method?:string,body?:unknown)=>Promise<T>;
type Config={server_url:string;model_id:string;has_key:boolean;revision:string;source:string;status:string};
type Check={status:string;model_ids:string[];model_id:string;revision:string;checked_at:number;real_task_quality_verified:boolean};
const messages:Record<string,string>={
 invalid_model_endpoint:'Укажите HTTPS-адрес сервера Open WebUI. HTTP разрешён только для localhost. Не используйте /v1, параметры или логин в URL.',
 invalid_model_key:'Проверьте API key: без пробелов и переносов строки.',
 new_key_required:'Для нового адреса введите API key заново.',
 model_configuration_changed:'Настройки уже изменены. Нажмите «Обновить настройки» и повторите.',
 openwebui_auth_failed:'Open WebUI отклонил ключ или доступ. Проверьте API key, разрешения API и модели.',
 openwebui_redirect_rejected:'Сервер вернул перенаправление. Укажите конечный адрес Open WebUI.',
 openwebui_unavailable:'Сервер недоступен. Проверьте адрес, сеть и доверие TLS-сертификату.',
 openwebui_timeout:'Время ожидания истекло. Проверьте доступность сервера и загрузку модели.',
 openwebui_invalid_response:'Сервер вернул неожиданный ответ. Нужен API Open WebUI.',
 openwebui_response_too_large:'Слишком большой ответ сервера.',
 model_not_available:'Эта модель недоступна для данного ключа. Обновите список.',
 reload_model_list:'Загрузите доступные модели ещё раз: список действует 5 минут.',
 model_secret_unavailable:'Не удалось защитить ключ на сервере. Настройки не изменены.',
 model_check_busy:'Уже выполняются проверки подключения. Повторите немного позже.',
 operator_forbidden:'Настройки доступны администратору с доступом ко всем узлам.'
};
export function ModelSettings({api,onMutate,refreshKey=0}:{api:Api;onMutate:()=>Promise<void>;refreshKey?:number}){
 const [config,setConfig]=useState<Config|null>(null),[url,setUrl]=useState(''),[key,setKey]=useState(''),[model,setModel]=useState(''),[ids,setIds]=useState<string[]>([]);
 const [result,setResult]=useState<Check|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const secret=useRef(''),alive=useRef(true);
 useEffect(()=>{alive.current=true;setNotice('');void load();return()=>{alive.current=false;secret.current='';};},[refreshKey]);
 async function load(){setError('');setResult(null);setIds([]);setKey('');secret.current='';try{const c=await api<Config>('/settings/model');if(!alive.current)return;setConfig(c);setUrl(c.server_url);setModel(c.model_id);}catch(e){if(alive.current)setError(messages[(e as Error).message]??'Не удалось загрузить настройки.');}}
 function invalidate(){setResult(null);setNotice('');setError('');}
 function body(){if(key)secret.current=key;setKey('');return {server_url:url,api_key:secret.current,model_id:model,revision:config?.revision};}
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn();}catch(e){if(alive.current)setError(messages[(e as Error).message]??'Не удалось выполнить действие. Настройки можно обновить и повторить.');}finally{if(alive.current)setBusy(false);}}
 return <section className="panel model-settings"><div className="panel-title"><div><span className="eyebrow">ПОДКЛЮЧЕНИЕ ИИ</span><h2>Подключение Open WebUI</h2><p>Настройте модель для основного окна агента и расследований.</p></div><button className="ghost" disabled={busy} onClick={()=>void load()}>Обновить настройки</button></div>
 <div className="model-status"><span className="status">{config?.status==='configured_unverified'?'Подключение настроено':config?.status==='configuration_unavailable'?'Ключ недоступен':'Модель не настроена'}</span><p>{config?.has_key?'Ключ сохранён на сервере. Поле ниже оставьте пустым, чтобы использовать его для того же адреса.':'Введите API key пользователя Open WebUI с доступом к нужной модели.'}</p>{config?.source==='environment'&&<small>Сейчас используется окружение сервера. Сохранение в интерфейсе заменит эту настройку.</small>}{config?.status==='configuration_unavailable'&&<p role="alert">Защищённый ключ не удалось прочитать. Введите подключение заново.</p>}</div>
 <div className="model-form"><label>Адрес сервера Open WebUI<input aria-label="Адрес сервера Open WebUI" type="url" autoComplete="off" placeholder="https://openwebui.company.ru" maxLength={2048} value={url} disabled={busy||!config} onChange={e=>{setUrl(e.target.value);secret.current='';setKey('');setIds([]);setModel('');invalidate();}}/></label>
 <label>API key<input aria-label="API key Open WebUI" type="password" autoComplete="new-password" placeholder={config?.has_key?'Сохранён · оставьте пустым или введите новый':'Вставьте API key'} maxLength={8192} value={key} disabled={busy||!config} onChange={e=>{setKey(e.target.value);secret.current='';setIds([]);invalidate();}}/></label>
 <div><button className="ghost" disabled={busy||!config||!url.trim()} onClick={()=>void run(async()=>{const r=await api<Check>('/settings/model/models','POST',body());if(!alive.current)return;setIds(r.model_ids);setResult(null);if(!r.model_ids.includes(model))setModel('');setNotice(r.model_ids.length?'Список моделей получен. Выберите модель и проверьте подключение.':'Для этого ключа нет доступных моделей. Проверьте права в Open WebUI.');})}>{busy?'Подождите…':'Загрузить модели'}</button></div>
 <label>Доступная модель<select aria-label="Доступная модель" value={model} disabled={busy||!ids.length} onChange={e=>{setModel(e.target.value);invalidate();}}><option value="">Выберите модель из Open WebUI</option>{model&&!ids.includes(model)&&<option value={model}>{model} · сохранённая</option>}{ids.map(id=><option value={id} key={id}>{id}</option>)}</select></label>
 <p className="muted">Названия Qwen и Gemma могут отличаться от model ID. Список поступает от вашего сервера с правами владельца ключа.</p>
 <div className="actions"><button className="ghost" disabled={busy||!model||!ids.includes(model)} onClick={()=>void run(async()=>{setResult(null);const r=await api<Check>('/settings/model/check','POST',body());if(alive.current)setResult(r);})}>Проверить подключение</button>
 <button className="primary" disabled={busy||!config||!model||!ids.includes(model)} onClick={()=>void run(async()=>{await api('/settings/model','PUT',body());secret.current='';await load();await onMutate();if(alive.current)setNotice('Подключение сохранено. Новые запросы используют эту модель без перезапуска.');})}>Сохранить подключение</button>
 <button className="danger-text" disabled={busy||!config||(!config.has_key&&!config.server_url)} onClick={()=>{if(confirm('Отключить модель для новых запросов и удалить сохранённый ключ?'))void run(async()=>{await api('/settings/model','DELETE',{revision:config!.revision});await load();await onMutate();if(alive.current)setNotice('Модель отключена, сохранённый ключ удалён.');});}}>Отключить модель</button></div>
 </div>
 {result&&<div className={result.status==='json_probe_passed'?'notice model-success':'notice'} role="status"><b>{result.status==='json_probe_passed'?'Подключение проверено · JSON-контракт выполнен':'Модель доступна, но JSON-контракт не выполнен'}</b><p>{result.model_id} · {new Date(result.checked_at*1000).toLocaleTimeString('ru-RU',systemDateOptions())}</p><small>Это проверка API и формата ответа. Качество планов и расследований проверяется отдельно на реальных кейсах.</small></div>}
 {notice&&<div className="notice" role="status">{notice}</div>}{error&&<div className="error" role="alert">{error}</div>}
 <div className="notice"><b>Как хранится ключ</b><p>Ключ защищён на сервере и не возвращается в браузер. Он не попадает в историю, базу знаний, резервную копию метаданных и релиз. Для другого адреса потребуется повторный ввод.</p><small>Выберите обычную базовую модель без привязанных серверных инструментов. Выполнение действий PSLC Agent требует отдельного подтверждённого плана.</small></div>
 </section>;
}

"""Compact release gallery from real captured UI, never mockups."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[1]
def build():
 screenshots=ROOT/'docs/qa/model-release/screenshots'
 required=[('11-live-main.png','Основной раздел · реальный Windows health'),('12-live-devices.png','Устройства · реальный узел и виртуальное демо'),('13-live-node-settings.png','Настройки виртуального узла'),('14-live-model.png','Настройка Open WebUI · реальный endpoint ещё не введён'),('15-live-events.png','События и готовность'),('16-live-accounts.png','Локальные учётные записи'),('17-live-knowledge.png','Знания · Confluence отложен'),('10-live-virtual-health.png','Виртуальное демо · синтетические метрики'),('02-model-probe.png','Проверка модели · synthetic transport fixture'),('07-mobile-model.png','Мобильный интерфейс · fixture')]
 version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version'];stamp=datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y-%m-%d_%H-%M-%S')
 target=ROOT/'release-dashboards'/(stamp+'_MSK_v'+version);target.mkdir(parents=True,exist_ok=False);entries=[]
 for name,label in required:
  src=screenshots/name
  if not src.is_file():raise ValueError('missing_actual_screenshot')
  shutil.copyfile(src,target/name);entries.append({'file':name,'label':label,'sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
 (target/'manifest.json').write_text(json.dumps({'version':version,'timezone':'Europe/Moscow','screenshots':entries,'scope':'real captured UI; fixture entries explicitly labeled','mockups':False},ensure_ascii=False,indent=2),encoding='utf-8')
 cards=''.join('<article><h2>'+x['label']+'</h2><a href="'+x['file']+'"><img src="'+x['file']+'" loading="lazy" alt="'+x['label']+'"></a></article>' for x in entries)
 (target/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PSLC Agent '+version+'</title><style>body{margin:0;padding:32px;background:#09121f;color:#e3edf5;font:16px Segoe UI,Arial}header{max-width:1000px;margin:auto auto 30px}h1{color:#53d7d0}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px}article{background:#142332;padding:20px;border-radius:12px}h2{font-size:16px;min-height:38px}img{width:100%;height:380px;object-fit:cover;object-position:top;border-radius:6px}p{line-height:1.6;color:#a6bfce}</style><header><h1>PSLC Agent '+version+' · релизный дашборд</h1><p>Все снимки получены из работающего интерфейса. Реальный Windows health и виртуальное демо разделены. Проверка Open WebUI на fixture не означает подключения вашего сервера. Нажмите снимок для полного размера.</p></header><main>'+cards+'</main></html>',encoding='utf-8')
 (ROOT/'docs/qa/model-release/dashboard.json').write_text(json.dumps({'path':str(target/'index.html'),'screenshots':len(entries),'complete':True},indent=2),encoding='utf-8')
 print(json.dumps({'dashboard':str(target/'index.html'),'screenshots':len(entries)}))
if __name__=='__main__':build()

"""Current 1C gallery from captured running UI, with fixture sources labeled."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json,hashlib,shutil,html
ROOT=Path(__file__).resolve().parents[1]
def build():
    source=ROOT/'docs/qa/dialogue-case-workspace/screenshots'
    required=[('10-live-main-verified','Основной раздел: подтверждённый health реальной Windows и отдельного демо'),('11-live-devices','Устройства'),('12-live-node-settings','Настройки демо-узла'),('18-live-onboarding','Подключение: ОС, явный тип и независимые разрешения'),('13-live-model','Локальный Qwen на CPU и настройка OpenWebUI'),('14-live-events','События'),('15-live-accounts','Учётные записи'),('16-live-knowledge','Знания: Confluence отложен'),('17-live-mobile','Основной раздел:390px'),('19-live-text-200','Основной раздел:320px и200% текста, тестовое увеличение'),('01-multi-turn-dialogue','Продолжаемый диалог:synthetic model fixture'),('02-exact-group-plan','Точный групповой план:synthetic fixture')]
    version=json.loads((ROOT/'package.json').read_text())['version']
    target=ROOT/'release-dashboards'/(datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y-%m-%d_%H-%M-%S')+'_MSK_v'+version)
    assert all((source/(name+'.png')).is_file() for name,_ in required)
    target.mkdir(parents=True,exist_ok=False);rows=[]
    for name,label in required:
        p=source/(name+'.png');shutil.copyfile(p,target/p.name)
        rows.append({'file':p.name,'label':label,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source':'fixture' if 'fixture' in label else 'actual local UI'})
    data={'version':version,'timezone':'Europe/Moscow','mockups':False,'screenshots':rows,'realHostMutation':False,'externalAcceptance':False}
    (target/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    cards=''.join('<article><h2>'+html.escape(x['label'])+'</h2><a href="'+x['file']+'"><img src="'+x['file']+'" loading="lazy" alt="'+html.escape(x['label'])+'"></a></article>' for x in rows)
    (target/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PSLC Agent '+version+'</title><style>body{margin:0;padding:30px;background:#f5f7fb;color:#17233d;font:16px Segoe UI,Arial}header{max-width:1100px;margin:auto auto 30px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}article{background:white;border:1px solid #c6d2e3;border-radius:12px;padding:18px}h1{color:#3158d8}h2{font-size:16px;min-height:44px}img{width:100%;height:400px;object-fit:cover;object-position:top}p{line-height:1.6}</style><header><h1>PSLC Agent '+version+' · Диалог / Ход разбора</h1><p>Снимки работающего интерфейса. Реальный Windows health и виртуальные данные разделены; диалог и групповой план fixture обозначены. Нажмите снимок для полного размера. Обнаружение, откат и планировщик ещё в следующих срезах.</p></header><main>'+cards+'</main></html>',encoding='utf-8')
    (ROOT/'docs/qa/dialogue-case-workspace/dashboard.json').write_text(json.dumps({'path':str(target/'index.html'),'screenshots':len(rows),'complete':True}),encoding='utf-8')
    print(json.dumps({'dashboard':str(target/'index.html'),'screenshots':len(rows)}))
if __name__=='__main__':build()

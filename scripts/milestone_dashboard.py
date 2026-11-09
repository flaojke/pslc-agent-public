"""Build a versioned gallery only from explicitly listed captured screenshots."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse,hashlib,html,json,shutil
ROOT=Path(__file__).resolve().parents[1]
def build(config):
 spec=json.loads(Path(config).read_text(encoding='utf-8'));version=json.loads((ROOT/'package.json').read_text())['version']
 entries=[]
 for row in spec['screenshots']:
  src=(ROOT/row['path']).resolve()
  if not src.is_relative_to(ROOT.resolve()) or not src.is_file() or src.suffix!='.png':raise ValueError('missing_actual_screenshot')
  entries.append((src,row))
 target=ROOT/'release-dashboards'/(datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y-%m-%d_%H-%M-%S')+'_MSK_v'+version)
 target.mkdir(parents=True,exist_ok=False);rows=[]
 for i,(src,row) in enumerate(entries):
  name=str(i+1).zfill(2)+'-'+src.name;shutil.copyfile(src,target/name)
  rows.append({'file':name,'label':row['label'],'source':row['source'],'sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
 (target/'manifest.json').write_text(json.dumps({'version':version,'timezone':'Europe/Moscow','mockups':False,'screenshots':rows,'scope':spec['scope']},ensure_ascii=False,indent=2),encoding='utf-8')
 cards=''.join('<article><h2>'+html.escape(x['label'])+'</h2><small>'+html.escape(x['source'])+'</small><a href="'+x['file']+'"><img src="'+x['file']+'" loading="lazy" alt="'+html.escape(x['label'])+'"></a></article>' for x in rows)
 (target/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PSLC Agent '+version+'</title><style>body{margin:0;padding:30px;background:#f5f7fb;color:#17233d;font:16px Segoe UI,Arial}header{max-width:1100px;margin:auto auto 30px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}article{background:#fff;border:1px solid #c6d2e3;border-radius:12px;padding:18px}h1{color:#3158d8}h2{font-size:16px;min-height:40px}small{display:block;color:#52627b;margin:8px 0 12px}img{width:100%;height:400px;object-fit:cover;object-position:top}p{line-height:1.6}</style><header><h1>PSLC Agent '+version+' · '+html.escape(spec['title'])+'</h1><p>'+html.escape(spec['scope'])+' Нажмите снимок для полного размера.</p></header><main>'+cards+'</main></html>',encoding='utf-8')
 result={'path':str(target/'index.html'),'screenshots':len(rows),'complete':True}
 Path(config).with_name('dashboard.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('config');a=p.parse_args();build(a.config)

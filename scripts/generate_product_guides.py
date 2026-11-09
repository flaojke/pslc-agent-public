"""Generate the four product Markdown guides from the validated source."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.product_guides import FILES,render,markdown
def main():
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
 stale=[]
 for gid,name in FILES.items():
  target=ROOT/'docs'/name;text=markdown(render(gid))
  if args.check:
   if not target.is_file() or target.read_text(encoding='utf-8')!=text:stale.append(name)
  else:target.write_text(text,encoding='utf-8')
 if stale:raise SystemExit('Guides out of date: '+', '.join(stale))
 print('Product guides: 4 validated and '+('synchronized' if args.check else 'generated'))
if __name__=='__main__':main()

"""Join verified GitHub release parts using Python standard library only."""
from pathlib import Path
import argparse,hashlib,json,os,re,sys,tempfile

def assemble(manifest_path):
 manifest_path=Path(manifest_path).resolve();base=manifest_path.parent
 data=json.loads(manifest_path.read_text(encoding='utf-8'))
 if data.get('format')!='pslc-full-parts-v1' or not 1<=len(data.get('parts',[]))<=16:raise ValueError('invalid_parts_manifest')
 name=data['archive']
 if Path(name).name!=name or not name.endswith('.zip') or not re.fullmatch('[a-f0-9]{64}',data['sha256']):raise ValueError('invalid_archive')
 target=base/name
 if target.exists():
  if target.is_symlink() or target.stat().st_size!=data['bytes'] or hashlib.file_digest(target.open('rb'),'sha256').hexdigest()!=data['sha256']:raise ValueError('existing_output_differs')
  return {'status':'verified_existing','archive':name,'bytes':data['bytes']}
 paths=[]
 for row in data['parts']:
  if Path(row['name']).name!=row['name'] or not re.fullmatch(re.escape(name)+r'\.part\d{2}',row['name']) or not re.fullmatch('[a-f0-9]{64}',row['sha256']) or not 0<row['bytes']<2*1024**3:raise ValueError('invalid_part')
  p=base/row['name']
  if p.is_symlink() or not p.is_file() or p.stat().st_size!=row['bytes']:raise ValueError('part_missing_or_size')
  paths.append((p,row))
 if sum(r['bytes'] for _,r in paths)!=data['bytes']:raise ValueError('invalid_total_size')
 fd,tmp=tempfile.mkstemp(prefix=name+'.assembling-',dir=base);digest=hashlib.sha256();total=0
 try:
  with os.fdopen(fd,'wb') as out:
   for path,row in paths:
    h=hashlib.sha256()
    with path.open('rb') as f:
     for b in iter(lambda:f.read(4*1024*1024),b''):
      h.update(b);digest.update(b);out.write(b);total+=len(b)
    if h.hexdigest()!=row['sha256']:raise ValueError('part_integrity_failed')
   out.flush();os.fsync(out.fileno())
  if total!=data['bytes'] or digest.hexdigest()!=data['sha256']:raise ValueError('archive_integrity_failed')
  if target.exists():raise ValueError('output_created_concurrently')
  os.replace(tmp,target);tmp=None
  return {'status':'assembled_verified','archive':name,'bytes':total}
 finally:
  if tmp:
   try:Path(tmp).unlink()
   except OSError:pass
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('manifest');a=p.parse_args()
 try:print(json.dumps(assemble(a.manifest)))
 except Exception:print('{"error":"assembly_rejected","action":"check all downloaded parts and manifest; existing files retained"}');raise SystemExit(2)

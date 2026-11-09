"""Verify a clean extracted release without importing application dependencies."""
from pathlib import Path
import hashlib,json,sys
def verify(root):
 root=Path(root).resolve();manifest=json.loads((root/'release-manifest.json').read_text(encoding='utf-8'))
 if manifest.get('format')!='pslc-source-release-v1':raise ValueError('manifest_format')
 expected={row['path']:row for row in manifest['files']}
 actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix()!='release-manifest.json'}
 if set(expected)!=actual:raise ValueError('missing_or_extra_files')
 for name,row in expected.items():
  p=root/name
  if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('invalid_path')
  if p.stat().st_size!=row['size']:raise ValueError('integrity_mismatch')
  with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  if digest!=row['sha256']:raise ValueError('integrity_mismatch')
 return {'status':'verified','version':manifest['version'],'files':len(expected),'scope':'byte integrity, not corporate signature'}
if __name__=='__main__':
 try:print(json.dumps(verify(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parents[1])))
 except Exception:print('{"status":"verification_failed"}');raise SystemExit(2)

"""Immutable protected schedule template revisions; no portable clear-text backup."""
from pathlib import Path
import re,hashlib,json
from .secret_store import SecretStore,SecretUnavailable
from .schedule_types import Template
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def template_hash(template):return hashlib.sha256(canonical(template.model_dump()).encode()).hexdigest()
def definition_fingerprint(row,ids):
 value={k:row[k] for k in ('id','owner_id','revision','title','mode','calendar','target_scope','template_hash')}
 return hashlib.sha256(canonical({**value,'consented_ids':sorted(ids)}).encode()).hexdigest()
class ScheduleVault:
 def __init__(self,root):self.root=Path(root)
 def path(self,sid,revision,sha):
  if not re.fullmatch(r'[a-f0-9]{32}',sid) or type(revision)!=int or not 1<=revision<=1000000 or not re.fullmatch('[a-f0-9]{64}',sha):raise SecretUnavailable()
  return self.root/(sid+'-'+str(revision)+'-'+sha+'.enc')
 def save(self,sid,revision,owner,template):
  sha=template_hash(template);p=self.path(sid,revision,sha);SecretStore(p,max_bytes=131072).write({'version':1,'schedule_id':sid,'revision':revision,'owner_id':owner,'template':template.model_dump()})
  return sha
 def load(self,row):
  try:
   value=SecretStore(self.path(row['id'],row['revision'],row['template_hash']),max_bytes=131072).read()
   if set(value)!={'version','schedule_id','revision','owner_id','template'} or value['version']!=1 or value['schedule_id']!=row['id'] or value['revision']!=row['revision'] or value['owner_id']!=row['owner_id']:raise ValueError()
   template=Template.model_validate(value['template'],strict=True)
   if template.mode!=row['mode'] or template_hash(template)!=row['template_hash']:raise ValueError()
   return template
  except Exception:raise SecretUnavailable() from None
 def cleanup(self,sid,keep=None):
  if not re.fullmatch(r'[a-f0-9]{32}',sid):raise SecretUnavailable()
  if self.root.is_symlink() or (hasattr(self.root,'is_junction') and self.root.is_junction()):raise SecretUnavailable()
  for p in self.root.glob(sid+'-*.enc'):
   if p!=keep and not p.is_symlink() and p.parent.resolve()==self.root.resolve():p.unlink(missing_ok=True)

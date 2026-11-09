"""Validated bundled product guides; safe structured text and reproducible Markdown."""
from pathlib import Path
from typing import Literal,Annotated
import json,re
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .config import ROOT
from .rbac import catalogue
class Strict(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
class Text(Strict):
 type:Literal['paragraph','note','heading']
 text:str=Field(min_length=1,max_length=12000)
class Items(Strict):
 type:Literal['list','steps']
 items:list[str]=Field(min_length=1,max_length=50)
class Code(Strict):
 type:Literal['code']
 language:Literal['powershell','sh','json','python','text']='text'
 code:str=Field(min_length=1,max_length=16000)
class Table(Strict):
 type:Literal['table']
 columns:list[str]=Field(min_length=1,max_length=8)
 rows:list[list[str]]=Field(max_length=60)
 @model_validator(mode='after')
 def shape(self):
  if any(len(row)!=len(self.columns) for row in self.rows):raise ValueError('table_shape')
  return self
class Link(Strict):
 label:str=Field(min_length=1,max_length=200)
 url:str|None=None
 section:Literal['models','system','about','user','administrator','deployment','access']|None=None
 @model_validator(mode='after')
 def safe(self):
  if bool(self.url)==bool(self.section):raise ValueError('invalid_link')
  if self.url and (not self.url.startswith('https://') or any(ord(c)<33 for c in self.url)):raise ValueError('unsafe_link')
  return self
class Links(Strict):
 type:Literal['links']
 items:list[Link]=Field(min_length=1,max_length=12)
class RoleMatrix(Strict):
 type:Literal['role_matrix']
Block=Annotated[Text|Items|Code|Table|Links|RoleMatrix,Field(discriminator='type')]
class Topic(Strict):
 title:str=Field(min_length=1,max_length=120)
 blocks:list[Block]=Field(min_length=1,max_length=80)
class Section(Strict):
 id:str=Field(pattern=r'^[a-z0-9-]{1,64}$')
 title:str|None=None
 topic:str|None=None
 blocks:list[Block]|None=None
 @model_validator(mode='after')
 def one_source(self):
  if bool(self.topic)==bool(self.blocks):raise ValueError('section_source')
  if self.blocks and not self.title:raise ValueError('section_title')
  return self
class Guide(Strict):
 id:Literal['user','administrator','deployment','models']
 title:str=Field(min_length=1,max_length=120)
 audience:str=Field(min_length=1,max_length=160)
 sections:list[Section]=Field(min_length=1,max_length=30)
class Source(Strict):
 schema_version:Literal[1]
 topics:dict[str,Topic]
 guides:list[Guide]=Field(min_length=4,max_length=4)
 @model_validator(mode='after')
 def references(self):
  if {g.id for g in self.guides}!={'user','administrator','deployment','models'}:raise ValueError('guide_set')
  for g in self.guides:
   if len({s.id for s in g.sections})!=len(g.sections):raise ValueError('duplicate_section')
   if any(s.topic and s.topic not in self.topics for s in g.sections):raise ValueError('missing_topic')
  return self
def variables():
 from .store import SCHEMA_VERSION
 from agent import __version__ as agent_version
 version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
 models=json.loads((ROOT/'config/local-models.json').read_text(encoding='utf-8'))
 default=next(m for m in models['models'] if m['id']==models['default'])
 return {'version':version,'schema':str(SCHEMA_VERSION),'agent_version':agent_version,'default_model':default['title']}
def source(path=None):
 p=Path(path or ROOT/'docs/product-guides.json')
 if p.is_symlink() or p.stat().st_size>262144:raise ValueError('invalid_guides')
 return Source.model_validate_json(p.read_bytes(),strict=True)
def render(guide_id,path=None):
 data=source(path);g=next((g for g in data.guides if g.id==guide_id),None)
 if g is None:raise KeyError('guide_not_found')
 values=variables()
 def expand(s):
  for key,value in values.items():s=s.replace('{{'+key+'}}',value)
  if re.search(r'{{[^}]+}}',s):raise ValueError('unknown_guide_variable')
  return s
 sections=[]
 roles=catalogue()
 for section in g.sections:
  topic=data.topics[section.topic] if section.topic else None
  blocks=[]
  for block in (topic.blocks if topic else section.blocks):
   value=block.model_dump(exclude_none=True)
   if value['type']=='role_matrix':
    value={'type':'table','columns':['Действие']+[r['name'] for r in roles['roles']],
     'rows':[[c['label']]+[c['grants'][r['id']] for r in roles['roles']] for c in roles['capabilities']]}
   # All authored strings, including code, are rendered as text. No expression evaluation.
   def strings(obj):
    if isinstance(obj,str):return expand(obj)
    if isinstance(obj,list):return [strings(x) for x in obj]
    if isinstance(obj,dict):return {k:strings(v) for k,v in obj.items()}
    return obj
   blocks.append(strings(value))
  sections.append({'id':section.id,'title':expand(section.title or topic.title),'blocks':blocks})
 return {'id':g.id,'title':g.title,'audience':g.audience,'version':values['version'],'schema_version':1,'sections':sections}
FILES={'user':'USER_GUIDE.md','administrator':'ADMIN_GUIDE.md','deployment':'INSTALL_RELEASE.md','models':'OPENWEBUI_CONNECTION_GUIDE.md'}
def markdown(guide):
 lines=['# '+guide['title'],'','Версия приложения: '+guide['version']+'. '+guide['audience'],'','Источник: docs/product-guides.json. Файл генерируется; изменяйте исходник и запускайте scripts/generate_product_guides.py.','']
 fence=chr(96)*3
 for section in guide['sections']:
  lines.extend(['## '+section['title'],''])
  for b in section['blocks']:
   kind=b['type']
   if kind=='heading':lines.extend(['### '+b['text'],''])
   elif kind in ('paragraph','note'):lines.extend([('> ' if kind=='note' else '')+b['text'],''])
   elif kind in ('list','steps'):
    lines.extend([(str(i+1)+'. ' if kind=='steps' else '- ')+v for i,v in enumerate(b['items'])]);lines.append('')
   elif kind=='code':lines.extend([fence+b['language'],b['code'],fence,''])
   elif kind=='table':
    escape=lambda s:s.replace('|',r'\|').replace('\n',' ')
    lines.extend(['| '+' | '.join(escape(s) for s in b['columns'])+' |','| '+' | '.join('---' for _ in b['columns'])+' |'])
    lines.extend('| '+' | '.join(escape(s) for s in row)+' |' for row in b['rows']);lines.append('')
   elif kind=='links':
    for link in b['items']:
     target=link.get('url') or FILES.get(link.get('section'),'USER_GUIDE.md')
     lines.append('- ['+link['label']+']('+target+')')
    lines.append('')
 return '\n'.join(lines).rstrip()+'\n'

"""Strict reviewed calendars/templates. No executable text belongs in SQL."""
import json,re
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import Field,model_validator,field_validator
from .schemas import Strict,CommandCheck
from .recovery_types import RecoveryResources
from .targets import TargetScope

class Calendar(Strict):
 frequency:Literal['once','daily','weekly']='daily'
 local_time:str=Field(default='07:00',pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
 timezone:str=Field(default='Europe/Moscow',min_length=1,max_length=80)
 date:str=Field(default='',max_length=10)
 weekdays:list[int]=Field(default_factory=list,max_length=7)
 @model_validator(mode='after')
 def valid(self):
  try:ZoneInfo(self.timezone)
  except (KeyError,ValueError):raise ValueError("invalid_timezone") from None
  if len(set(self.weekdays))!=len(self.weekdays) or any(type(d)!=int or not 0<=d<=6 for d in self.weekdays):raise ValueError('invalid_weekdays')
  if self.frequency=='weekly' and not self.weekdays:raise ValueError('weekdays_required')
  if self.frequency!='weekly' and self.weekdays:raise ValueError('weekdays_only_weekly')
  if self.frequency=='once':
   if not re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}',self.date):raise ValueError('date_required')
   date.fromisoformat(self.date)
  elif self.date:raise ValueError('date_only_once')
  self.weekdays.sort()
  return self

class Script(Strict):
 runtime:Literal['powershell','cmd','python','node']='python'
 code:str=Field(min_length=1,max_length=16000)
 timeout_seconds:int=Field(default=30,ge=1,le=120)
 verification:CommandCheck
 recovery:RecoveryResources
 @field_validator('code')
 @classmethod
 def nonblank(cls,v):
  if not v.strip():raise ValueError('script_required')
  return v

class Template(Strict):
 mode:Literal['prompt','health','errors','script']
 prompt:str=Field(default='',max_length=8000)
 script:Script|None=None
 linux_unit:str=Field(default='',max_length=80)
 @model_validator(mode='after')
 def valid(self):
  if (self.mode=='prompt')!=bool(self.prompt.strip()):raise ValueError('prompt_only_prompt_mode')
  if (self.mode=='script')!=(self.script is not None):raise ValueError('script_only_script_mode')
  if self.linux_unit and (self.mode!='errors' or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,79}\.service',self.linux_unit)):raise ValueError('invalid_journal_unit')
  if len(self.model_dump_json().encode('utf-8'))>65536:raise ValueError('template_size_limit')
  return self

class RuleInput(Strict):
 title:str=Field(min_length=1,max_length=80)
 calendar:Calendar
 targets:TargetScope
 template:Template
 @model_validator(mode='after')
 def valid(self):
  self.title=self.title.strip()
  if not self.title or any(ord(c)<32 for c in self.title):raise ValueError('invalid_schedule_title')
  if self.targets.kind=='none':raise ValueError('select_schedule_targets')
  if self.template.mode=='script' and (self.targets.kind!='manual' or not self.targets.ids):raise ValueError('script_requires_explicit_ids')
  return self
class RuleUpdate(RuleInput):
 expected_revision:int=Field(ge=1,strict=True)
class RuleDecision(Strict):
 expected_revision:int=Field(ge=1,strict=True)
 definition_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
 consent:bool=Field(strict=True)
class RuleRevision(Strict):
 expected_revision:int=Field(ge=1,strict=True)
class ManualRun(Strict):
 request_id:str=Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')

class Skip(Strict):
 device_id:str=Field(min_length=1,max_length=64)
 reason:Literal['outside_consent','access_revoked','device_unavailable','commands_disabled','diagnostics_disabled','agent_upgrade_required','unit_required','unsupported_platform']
class RunBrief(Strict):
 target_count:int=Field(default=0,ge=0,le=500)
 eligible_count:int=Field(default=0,ge=0,le=500)
 skipped:list[Skip]=Field(default_factory=list,max_length=500)
 code:Literal['','schedule_missed','interrupted','worker_busy','overlap','no_targets','template_unavailable','access_revoked','plan_failed','stopped','completed']=''

"""Shared bounded schemas; no raw diagnostic text is a durable contract."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

class Typed(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


def utc(value):
    if not isinstance(value,str) or len(value)>40:raise ValueError('invalid_utc')
    try:parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('invalid_utc') from None
    if parsed.tzinfo is None:raise ValueError('timezone_required')
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def timestamp(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def scope_hash(scope):return hashlib.sha256(canonical(scope.model_dump()).encode()).hexdigest()

class Window(Typed):
    start_utc:str=Field(max_length=40)
    end_utc:str=Field(max_length=40)
    max_events:int=Field(default=50,ge=1,le=100)
    @field_validator('start_utc','end_utc')
    @classmethod
    def dates(cls,value):return utc(value)
    @model_validator(mode='after')
    def interval(self):
        duration=(timestamp(self.end_utc)-timestamp(self.start_utc)).total_seconds()
        if not 0<duration<=86400:raise ValueError('invalid_window')
        return self

class WindowsScope(Window):
    source:Literal['windows_events']
    channels:list[Literal['System','Application']]=Field(default_factory=lambda:['Application','System'],min_length=1,max_length=2)
    @field_validator('channels')
    @classmethod
    def unique(cls,value):
        if len(set(value))!=len(value):raise ValueError('duplicate_channel')
        return sorted(value)

class JournalScope(Window):
    source:Literal['systemd']
    unit:str=Field(pattern=r'^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,79}\.service$')

Scope=TypeAdapter(Annotated[WindowsScope|JournalScope,Field(discriminator='source')])

def admit_scope(value,os_family):
    if os_family not in ('Windows','Linux'):raise ValueError('unsupported_platform')
    scope=Scope.validate_python(value,strict=True)
    if (scope.source=='windows_events')!=(os_family=='Windows'):raise ValueError('source_os_mismatch')
    now=datetime.now(timezone.utc)
    if timestamp(scope.end_utc)>now+timedelta(minutes=5) or timestamp(scope.start_utc)<now-timedelta(days=7):raise ValueError('scope_outside_retention_window')
    return scope

class EvidenceRecord(Typed):
    ref:str=Field(pattern=r'^[a-f0-9]{64}$')
    at:str=Field(max_length=40)
    channel:Literal['System','Application','journal']
    severity:Literal['critical','error','warning']
    event_id:int|None=Field(default=None,ge=0,le=4294967295)
    record_id:int|None=Field(default=None,ge=0,le=9223372036854775807)
    provider:str=Field(default='',max_length=128)
    message:str=Field(default='',max_length=2000)
    text_truncated:bool=False
    @field_validator('at')
    @classmethod
    def dates(cls,value):return utc(value)

class Coverage(Typed):
    target:Literal['System','Application','journal']
    status:Literal['complete','truncated','timeout','output_limit','access_denied','collector_failed','prerequisite_missing','parse_error']

class EvidenceEnvelope(Typed):
    scope_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    records:list[EvidenceRecord]=Field(max_length=100)
    coverage:list[Coverage]=Field(min_length=1,max_length=2)
    @model_validator(mode='after')
    def bounded(self):
        if len(self.model_dump_json().encode())>65536:raise ValueError('evidence_too_large')
        if len({r.ref for r in self.records})!=len(self.records):raise ValueError('duplicate_evidence')
        if len({c.target for c in self.coverage})!=len(self.coverage):raise ValueError('duplicate_coverage')
        return self

class Hypothesis(Typed):
    category:Literal['service_failure','authentication','resource_pressure','application_error','configuration','network','unknown']
    confidence:Literal['low','medium']
    evidence_ids:list[str]=Field(min_length=1,max_length=10)
    explanation:str=Field(min_length=1,max_length=1000)
    next_checks:list[Annotated[str,Field(min_length=1,max_length=500)]]=Field(default_factory=list,max_length=3)
    @field_validator('evidence_ids')
    @classmethod
    def unique(cls,value):
        if len(set(value))!=len(value):raise ValueError('duplicate_reference')
        return value

class Assessment(Typed):
    conclusion:Literal['hypothesis_only','insufficient_evidence']
    hypotheses:list[Hypothesis]=Field(max_length=5)
    @model_validator(mode='after')
    def consistency(self):
        if (self.conclusion=='hypothesis_only')!=bool(self.hypotheses):raise ValueError('inconsistent_assessment')
        return self

import json
from pathlib import Path
from datetime import datetime,timezone,date
import pytest
from backend.schedule_types import Calendar,Template,RuleInput
from backend.schedule_clock import instant,next_slot,slot_key
from backend.schedule_vault import ScheduleVault
from backend.secret_store import SecretUnavailable

def epoch(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def test_calendar_moscow_monday_daily_and_dst_once():
 c=Calendar(local_time='07:00',timezone='Europe/Moscow')
 at,key=next_slot(c,epoch('2026-11-08T21:01:00Z'));assert at==epoch('2026-11-09T04:00:00Z') and key=='2026-11-09T07:00'
 assert next_slot(c,at)[0]==at+86400
 weekly=Calendar(frequency='weekly',weekdays=[0],local_time='07:00')
 assert next_slot(weekly,at)[1]=='2026-11-16T07:00'
 # US spring gap does not exist; autumn repeated slot chooses only the first UTC instant.
 assert instant(date(2026,3,8),'02:30','America/New_York') is None
 assert instant(date(2026,11,1),'01:30','America/New_York')==epoch('2026-11-01T05:30:00Z')
 assert next_slot(Calendar(frequency='once',date='2026-03-08',local_time='02:30',timezone='America/New_York'),epoch('2026-03-07T00:00:00Z')) is None
 assert next_slot(Calendar(frequency='once',date='2026-01-01'),epoch('2026-02-01T00:00:00Z')) is None
 for data in ({'timezone':'No/SuchZone'},{'local_time':'25:00'},{'frequency':'weekly'},{'frequency':'weekly','weekdays':[0,0]},{'frequency':'once','date':'2026-02-30'},{'weekdays':[0]}):
  with pytest.raises(ValueError):Calendar.model_validate(data)

def test_template_vault_privacy_binding_corruption_and_cleanup(tmp_path):
 template=Template(mode='prompt',prompt='PRIVATE-SCHEDULE-PROMPT-MARKER');vault=ScheduleVault(tmp_path/'schedule')
 row={'id':'a'*32,'revision':1,'owner_id':'qa-owner','mode':'prompt'};row['template_hash']=vault.save(row['id'],1,'qa-owner',template)
 p=vault.path(row['id'],1,row['template_hash']);assert b'PRIVATE-SCHEDULE-PROMPT-MARKER' not in p.read_bytes()
 assert vault.load(row)==template
 with pytest.raises(SecretUnavailable):vault.load({**row,'owner_id':'foreign'})
 p.write_bytes(p.read_bytes()[:-4]+b'FAIL')
 with pytest.raises(SecretUnavailable):vault.load(row)
 for sid in ('../outside','a'*31,'g'*32):
  with pytest.raises(SecretUnavailable):vault.path(sid,1,'b'*64)
 vault.cleanup(row['id']);assert not p.exists()
 with pytest.raises(ValueError):Template(mode='health',prompt='unexpected')
 with pytest.raises(ValueError):Template(mode='prompt',prompt=' ')
 with pytest.raises(ValueError):Template(mode='errors',linux_unit='../../tmp')

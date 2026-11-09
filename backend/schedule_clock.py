"""Local slot identity -> UTC instant; no ambiguous double fold or catch-up."""
from datetime import datetime,date,time,timedelta,timezone
from zoneinfo import ZoneInfo
from .schedule_types import Calendar
def instant(day,clock,zone):
 naive=datetime.combine(day,time.fromisoformat(clock));z=ZoneInfo(zone);values=[]
 for fold in (0,1):
  aware=naive.replace(tzinfo=z,fold=fold);utc=aware.astimezone(timezone.utc)
  if utc.astimezone(z).replace(tzinfo=None)==naive:values.append(utc.timestamp())
 return min(values) if values else None

def next_slot(calendar,after):
 c=calendar if isinstance(calendar,Calendar) else Calendar.model_validate(calendar,strict=True)
 start=datetime.fromtimestamp(after,ZoneInfo(c.timezone)).date()
 days=[date.fromisoformat(c.date)] if c.frequency=='once' else [start+timedelta(days=n) for n in range(370)]
 for day in days:
  if c.frequency=='weekly' and day.weekday() not in c.weekdays:continue
  at=instant(day,c.local_time,c.timezone)
  if at is not None and at>after:return at,day.isoformat()+'T'+c.local_time
 return None

def slot_key(calendar,at):
 c=calendar if isinstance(calendar,Calendar) else Calendar.model_validate(calendar,strict=True)
 return datetime.fromtimestamp(at,ZoneInfo(c.timezone)).strftime('%Y-%m-%dT%H:%M')

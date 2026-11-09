"""Metadata-only pytest report: no captured output, tracebacks or locals."""
import json,os,xml.etree.ElementTree as ET
from pathlib import Path
results={}
def pytest_runtest_logreport(report):
 row=results.setdefault(report.nodeid,{'name':report.nodeid,'status':'passed','duration':0.0})
 row['duration']+=report.duration
 if report.failed:row['status']='failed'
 elif report.skipped and row['status']!='failed':row['status']='skipped'
def pytest_sessionfinish(session,exitstatus):
 path=Path(os.environ['PSLC_EVAL_REPORT']);path.parent.mkdir(parents=True,exist_ok=True)
 rows=list(results.values());counts={s:sum(r['status']==s for r in rows) for s in ('passed','failed','skipped')}
 path.write_text(json.dumps({'exit_code':int(exitstatus),'counts':counts,'cases':rows},indent=2),encoding='utf-8')
 suite=ET.Element('testsuite',tests=str(len(rows)),failures=str(counts['failed']),skipped=str(counts['skipped']))
 for row in rows:
  test=ET.SubElement(suite,'testcase',name=row['name'],time=str(round(row['duration'],4)))
  if row['status']!='passed':ET.SubElement(test,'failure' if row['status']=='failed' else 'skipped',message='scenario_'+row['status'])
 ET.ElementTree(suite).write(path.with_suffix('.xml'),encoding='utf-8',xml_declaration=True)

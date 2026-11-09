"""Actual full reconstruction plus missing/changed-part failures in a disposable directory."""
from pathlib import Path
import hashlib,json,os,tempfile
from scripts.assemble_full import assemble
ROOT=Path(__file__).resolve().parents[1];version=json.loads((ROOT/'package.json').read_text())['version'];source=ROOT/'artifacts/releases'/version
original=source/('PSLC-Agent-'+version+'-Full.parts.json');manifest=json.loads(original.read_text());base=Path(tempfile.mkdtemp(prefix='parts-proof-',dir=ROOT/'.local'));target_manifest=base/original.name;target_manifest.write_text(json.dumps(manifest),encoding='utf-8')
try:assemble(target_manifest);raise AssertionError('Missing part accepted')
except ValueError:pass
bad=base/manifest['parts'][0]['name']
with bad.open('wb') as f:f.truncate(manifest['parts'][0]['bytes'])
for row in manifest['parts'][1:]:os.link(source/row['name'],base/row['name'])
try:assemble(target_manifest);raise AssertionError('Changed part accepted')
except ValueError as e:assert str(e)=='part_integrity_failed'
assert not (base/manifest['archive']).exists() and not list(base.glob('*.assembling-*'))
assert bad.parent.resolve()==base.resolve();bad.unlink();os.link(source/manifest['parts'][0]['name'],bad)
result=assemble(target_manifest);assert result['status']=='assembled_verified';assert assemble(target_manifest)['status']=='verified_existing'
with (base/manifest['archive']).open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==manifest['sha256']
for row in manifest['parts']:
 with (source/row['name']).open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==row['sha256']
value={'version':version,'actual_full_bytes':manifest['bytes'],'full_sha256':manifest['sha256'],'parts':len(manifest['parts']),'missing_rejected':True,'changed_rejected':True,'partial_output_removed':True,'actual_full_reassembled':True,'idempotent_existing_verified':True,'source_parts_unchanged':True}
(ROOT/'docs/qa/local-model/parts-proof.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps(value))

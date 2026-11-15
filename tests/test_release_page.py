"""Release guidance from a self-contained synthetic inventory; no local QA files."""
import hashlib,re
import pytest
from scripts.release_page import render,choices

REPO='example/pslc-agent'
VERSION='0.18.2'

@pytest.fixture
def assets():
 prefix='PSLC-Agent-'+VERSION
 names=[prefix+'-Lite.zip',prefix+'-Lite.zip.sha256',
        prefix+'-source.zip',prefix+'-source.zip.sha256',
        prefix+'-Full.zip.part01',prefix+'-Full.zip.part02',
        prefix+'-Full.parts.json',prefix+'-Full.zip.sha256',
        'assemble_full.py','DOWNLOADS.md','distribution-manifest.json']
 base='https://github.com/'+REPO+'/releases/download/v'+VERSION+'/'
 return [{'name':name,'bytes':100+index,
          'sha256':hashlib.sha256(('public-fixture:'+name).encode()).hexdigest(),
          'url':base+name} for index,name in enumerate(names)]

def test_historical_release_links_match_declared_fixture_inventory(assets):
 page=render(REPO,VERSION,assets,'public fixture notes',historical=True)
 links=set(re.findall(r'\]\((https://github.com/example/pslc-agent/releases/download/v0\.18\.2/[^)]+)\)',page))
 assert links=={row['url'] for row in assets}
 primary=page.split('<details>')[0]
 assert 'Lite.zip' in primary and 'Source.zip' in primary
 for name in ('Full.zip.part01','Full.zip.part02','Full.parts.json','assemble_full.py'):
  assert name in primary
 assert primary.index('Python3.12')<primary.index('py -3.12')
 assert 'SHA256' not in primary
 assert '<summary>Контрольные суммы' in page

@pytest.mark.parametrize('missing',['PSLC-Agent-0.18.2-Full.zip.part01','PSLC-Agent-0.18.2-Full.zip.part02','assemble_full.py'])
def test_missing_historical_piece_blocks_description(assets,missing):
 with pytest.raises(ValueError):
  render(REPO,VERSION,[row for row in assets if row['name']!=missing],historical=True)

def test_bad_asset_cannot_become_link(assets):
 with pytest.raises(ValueError,match='invalid_release_asset'):
  choices(REPO,VERSION,[*assets,{'name':'../../private','bytes':1,'sha256':'a'*64}])

def test_current_default_refuses_historical_full_inventory(assets):
 with pytest.raises(ValueError,match='historical_full_requires_explicit_mode'):
  render(REPO,VERSION,assets)

def test_current_model_free_page_requires_no_full_assembly(assets):
 rows=[row for row in assets if '-Full.' not in row['name'] and row['name']!='assemble_full.py']
 page=render(REPO,VERSION,rows)
 primary=page.split('<details>')[0]
 assert 'без весов' in primary and '«Скачать»' in primary
 assert 'Full' not in primary and 'assemble_full' not in page and 'part01' not in page
 assert 'автоматически' in primary and 'SHA256' not in primary

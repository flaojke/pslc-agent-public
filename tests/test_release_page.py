import json,re
from pathlib import Path
import pytest
from scripts.release_page import render,choices

@pytest.fixture
def assets():
 names=['assemble_full.py','distribution-manifest.json','DOWNLOADS.md','PSLC-Agent-0.18.2-Full.parts.json','PSLC-Agent-0.18.2-Full.zip.part01','PSLC-Agent-0.18.2-Full.zip.part02','PSLC-Agent-0.18.2-Full.zip.sha256','PSLC-Agent-0.18.2-Lite.zip','PSLC-Agent-0.18.2-Lite.zip.sha256','PSLC-Agent-0.18.2-source.zip','PSLC-Agent-0.18.2-source.zip.sha256']
 # Synthetic inventory: no recorded environment or release metadata.
 return [{'name':n,'bytes':100+i,'sha256':format(i+1,'064x'),'url':'https://github.com/flaojke/pslc-agent/releases/download/v0.18.2/'+n} for i,n in enumerate(names)]

def test_release_links_match_synthetic_inventory_and_primary_full_is_complete(assets):
 page=render('flaojke/pslc-agent','0.18.2',assets,'release notes')
 links=set(re.findall(r'\]\((https://github.com/flaojke/pslc-agent/releases/download/v0\.18\.2/[^)]+)\)',page))
 assert links=={a['url'] for a in assets}
 primary=page.split('<details>')[0]
 assert 'Lite.zip' in primary and 'Source.zip' in primary
 for name in ('Full.zip.part01','Full.zip.part02','Full.parts.json','assemble_full.py'):assert name in primary
 assert primary.index('Python3.12')<primary.index('py -3.12')
 assert 'SHA256' not in primary
 assert '<summary>Контрольные суммы' in page

@pytest.mark.parametrize('missing',['PSLC-Agent-0.18.2-Full.zip.part01','PSLC-Agent-0.18.2-Full.zip.part02','assemble_full.py'])
def test_missing_full_piece_blocks_description_before_publication(assets,missing):
 with pytest.raises(ValueError):render('flaojke/pslc-agent','0.18.2',[a for a in assets if a['name']!=missing])

def test_bad_asset_cannot_become_link(assets):
 with pytest.raises(ValueError,match='invalid_release_asset'):choices('flaojke/pslc-agent','0.18.2',[*assets,{'name':'../../private','bytes':1,'sha256':'a'*64}])

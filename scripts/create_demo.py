"""Explicit local administrative bootstrap; no credential output."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.config import load_settings
from backend.store import Store,Problem
from backend.demo import DemoNode
if __name__=='__main__':
 store=None
 try:
  s=load_settings();s.validate();store=Store(s);print(json.dumps(DemoNode(store).create()))
 except Exception:print('{"error":"demo_creation_rejected","credentials_echoed":false}');raise SystemExit(2)
 finally:
  if store:store.engine.dispose()

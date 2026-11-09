from pathlib import Path
import json
import secrets
import sys

ROOT=Path(__file__).resolve().parents[1]
d=ROOT/".local"
if not d.exists():
    sys.path.insert(0,str(ROOT))
    from backend.secret_store import SecretStore
    SecretStore(d/"settings.json")._directory()
else:
    d.mkdir(exist_ok=True)
p=d/"settings.json"
if not p.exists():
    p.write_text(json.dumps({"operator_token":secrets.token_urlsafe(32),"database_url":"sqlite:///"+(d/"pslc.sqlite3").as_posix()},indent=2),encoding="utf-8")
    print("Created .local/settings.json (secret not printed). Local development only.")
else:
    print("Existing local settings retained.")

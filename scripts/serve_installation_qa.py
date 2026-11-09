"""Isolated browser fixture. No real installer or agent process is started."""
from pathlib import Path
import hashlib
import json
import tempfile
import uvicorn
from backend.app import create_app
from backend.config import Settings, ROOT
from backend.schemas import Enroll

base=Path(tempfile.mkdtemp(prefix="install-qa-",dir=ROOT/".local"))
package={"id":"fixture-tool","name":"QA — тестовый пакет (не настоящий дистрибутив)","version":"1.2.3","os_family":"Windows","installer":"msi","source_url":"https://fixtures.invalid/qa-package.msi","sha256":hashlib.sha256(b"qa-placeholder-only").hexdigest(),"timeout_seconds":600,"verification_argv":["fixture-version-check","--version"],"expected_output":"1.2.3","uninstall_instructions":"QA: только просмотр плана. Реальный пакет не устанавливается и удаление не запускается."}
catalog=base/"catalog.json";catalog.write_text(json.dumps({"schema_version":1,"packages":[package]},ensure_ascii=False),encoding="utf-8")
token="browser-installation-fixture-public-token"
app=create_app(Settings(token,"sqlite:///"+(base/"qa.sqlite3").as_posix(),package_catalog_path=str(catalog)))
ticket=app.state.store.ticket()["enrollment_token"]
app.state.store.enroll(Enroll(enrollment_token=ticket,name="installation-qa-fixture",os_family="Windows",installations_enabled=True))
(ROOT/".local/installation-browser-qa.json").write_text(json.dumps({"url":"http://127.0.0.1:8799","token":token,"catalog":str(catalog),"directory":str(base)}),encoding="utf-8")
uvicorn.run(app,host="127.0.0.1",port=8799,access_log=False,proxy_headers=False,workers=1,log_level="warning")

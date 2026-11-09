"""Run the extracted artifact with existing pinned interpreter; no source-root imports."""
import sys,json,os
from pathlib import Path
stage=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(stage));os.chdir(stage)
import uvicorn,backend.app
from backend.config import Settings
assert Path(backend.app.__file__).resolve().is_relative_to(stage)
app=backend.app.create_app(Settings('unused-public-release-smoke-token','sqlite:///'+(stage/'.local/qa.sqlite3').as_posix(),auth_mode='accounts',public_origin='http://127.0.0.1:8794',knowledge_enabled=False,model_config_path=str(stage/'.local/model/connection.enc')))
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8794,access_log=False,log_level='warning')

from pathlib import Path
import sys,tempfile,uvicorn
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app import create_app
from backend.config import Settings
base=Path(tempfile.mkdtemp(prefix='operations-qa-',dir=ROOT/'.local'))
app=create_app(Settings('operations-browser-public-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix()))
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8793,access_log=False,log_level='warning')

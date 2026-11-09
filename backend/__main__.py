import logging,os
import uvicorn
from .app import create_app
from .config import load_settings
logging.getLogger("sqlalchemy.engine").setLevel(logging.CRITICAL)
if __name__ == "__main__":
    settings=load_settings();settings.validate()
    cert=settings.tls_cert_file;key=settings.tls_key_file
    if bool(cert)!=bool(key):raise SystemExit('Both PSLC_TLS_CERT and PSLC_TLS_KEY are required for TLS.')
    app=create_app(settings)
    uvicorn.run(app,host=settings.api_host,port=8787,access_log=False,proxy_headers=False,workers=1,limit_concurrency=1024,backlog=128,timeout_keep_alive=5,log_level="warning",ssl_certfile=cert or None,ssl_keyfile=key or None)

import hashlib
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time
import httpx
from pydantic import ValidationError
from backend.catalog import PackageManifest, manifest_hash, installer_argv

class DownloadError(Exception): pass

def download(package,destination,deadline,client):
    digest=hashlib.sha256();size=0
    try:
        with client.stream("GET",package.source_url) as response:
            if response.status_code!=200: raise DownloadError()
            declared=response.headers.get("content-length")
            if declared and int(declared)>package.max_bytes: raise DownloadError()
            with open(destination,"xb") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    if time.monotonic()>=deadline or size+len(chunk)>package.max_bytes: raise DownloadError()
                    f.write(chunk);digest.update(chunk);size+=len(chunk)
    except (httpx.HTTPError,OSError,ValueError): raise DownloadError() from None
    return digest.hexdigest()


def execute_installation(payload,enabled=False,workdir=None,runner=None,transport=None,os_family=None,which=None):
    from .executor import run_bounded
    started=time.monotonic();runner=runner or run_bounded;which=which or shutil.which
    result={"outcome":"failed","exit_code":None,"duration_ms":0,"output":""}
    try:
        package=PackageManifest.model_validate(payload.get("manifest"))
        if manifest_hash(package)!=payload.get("manifest_hash") or payload.get("timeout_seconds")!=package.timeout_seconds:
            return result
    except (ValidationError,ValueError,TypeError): return result
    proof={"package_id":package.id,"version":package.version,"manifest_hash":manifest_hash(package),"artifact_sha256":None,
        "version_matched":False,"already_installed":False,"reboot_required":False}
    result["installation"]=proof
    def done(outcome,**values):
        result.update(outcome=outcome,duration_ms=int((time.monotonic()-started)*1000),**values)
        return result
    if not enabled: return done("installations_disabled")
    if package.os_family!=(os_family or platform.system()): return done("unsupported_platform")
    deadline=started+package.timeout_seconds
    def probe():
        remaining=deadline-time.monotonic()
        if remaining<=0:return {"outcome":"timeout","exit_code":None,"output":""},False
        r=runner(package.verification_argv,timeout_seconds=min(15,max(1,int(remaining))),cwd=workdir)
        matched=r["outcome"]=="process_succeeded" and r.get("exit_code")==0 and r.get("output","").strip()==package.expected_output
        return r,matched
    pre,matched=probe()
    if matched:
        proof.update(version_matched=True,already_installed=True)
        return done("already_installed",exit_code=0)
    if pre["outcome"] in ("timeout","output_limit"): return done("verification_failed")
    driver_path=which({"msi":"msiexec","deb":"dpkg","rpm":"rpm"}[package.installer]) if package.installer!="exe" else None
    if package.installer!="exe" and not driver_path:
        return done("prerequisite_missing")
    root=Path(workdir or tempfile.gettempdir()).resolve();root.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="pslc-install-",dir=root) as temp:
            folder=Path(temp).resolve()
            if root not in folder.parents: return done("failed")
            artifact=folder/("package."+package.installer)
            with httpx.Client(transport=transport,timeout=httpx.Timeout(15,connect=10),follow_redirects=False,trust_env=False) as client:
                try: actual=download(package,artifact,deadline,client)
                except DownloadError: return done("download_failed")
            proof["artifact_sha256"]=actual
            if actual!=package.sha256: return done("integrity_failed")
            remaining=deadline-time.monotonic()
            if remaining<=0:return done("download_failed")
            argv=installer_argv(package,artifact)
            if package.installer!="exe":argv[0]=driver_path
            install=runner(argv,timeout_seconds=max(1,int(remaining)),cwd=workdir)
            result["output"]=install.get("output","")[:65536]
            code=install.get("exit_code");result["exit_code"]=code
            if install["outcome"] in ("timeout","output_limit"):return done("outcome_unknown")
            if install["outcome"]=="prerequisite_missing":return done("prerequisite_missing")
            if code not in (0,3010) or (code==3010 and package.os_family!="Windows"):return done("failed")
            proof["reboot_required"]=code==3010
            post,matched=probe();proof["version_matched"]=matched
            if not matched:
                result["output"]=post.get("output","")[:65536]
                return done("verification_failed")
            return done("reboot_required" if proof["reboot_required"] else "installed")
    except OSError:
        return done("outcome_unknown" if result["exit_code"] is not None else "failed")

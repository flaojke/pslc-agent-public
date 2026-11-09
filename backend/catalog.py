import hashlib
import json
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, model_validator

class CatalogError(Exception): pass

class PackageRecovery(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    uninstall_argv:list[str]=Field(min_length=1,max_length=30)
    absence_argv:list[str]=Field(min_length=1,max_length=20)
    absence_expected_output:str=Field(min_length=1,max_length=256)
    timeout_seconds:int=Field(default=600,ge=1,le=1800)
    verification_timeout_seconds:int=Field(default=15,ge=1,le=60)
    @model_validator(mode="after")
    def arguments(self):
        if any(not x or len(x)>1024 or any(c in x for c in ("\0","\n","\r")) for x in self.uninstall_argv+self.absence_argv):raise ValueError("invalid_recovery_arguments")
        if any(x.lower() in ("/forcerestart","/promptrestart","--reboot","/restart","reboot=force","reboot=reallyforce") for x in self.uninstall_argv):raise ValueError("reboot_forbidden")
        return self

class PackageManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    id:str=Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name:str=Field(min_length=1,max_length=120)
    version:str=Field(pattern=r"^[a-zA-Z0-9_.+~-]{1,64}$")
    os_family:Literal["Windows","Linux"]
    installer:Literal["msi","exe","deb","rpm"]
    source_url:str=Field(max_length=2048)
    sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    max_bytes:int=Field(default=268435456,ge=1,le=2147483648)
    timeout_seconds:int=Field(default=600,ge=30,le=1800)
    silent_args:list[str]=Field(default_factory=list,max_length=30)
    no_restart_args:list[str]=Field(default_factory=list,max_length=10)
    verification_argv:list[str]=Field(min_length=1,max_length=20)
    expected_output:str=Field(min_length=1,max_length=256)
    uninstall_instructions:str=Field(min_length=1,max_length=2000)
    recovery:PackageRecovery|None=None

    @model_validator(mode="after")
    def check(self):
        u=urlparse(self.source_url)
        if not u.hostname or u.username or u.password or u.query or u.fragment: raise ValueError("invalid_source")
        if u.scheme!="https" and not (u.scheme=="http" and u.hostname in ("localhost","127.0.0.1","::1")): raise ValueError("https_required")
        if (self.installer in ("msi","exe")) != (self.os_family=="Windows"): raise ValueError("installer_os_mismatch")
        args=self.silent_args+self.no_restart_args+self.verification_argv
        if any(not x or len(x)>1024 or '\x00' in x or '\n' in x or '\r' in x for x in args): raise ValueError("invalid_arguments")
        if self.version not in self.expected_output: raise ValueError("verification_must_identify_version")
        if any(x.lower() in ('/forcerestart','/promptrestart','reboot=force','reboot=reallyforce','--reboot','/restart') for x in self.silent_args+self.no_restart_args): raise ValueError("reboot_forbidden")
        if self.installer=="msi" and (self.no_restart_args or any(not re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\r\n]+",x) or x.split('=')[0] in ("REBOOT","REBOOTPROMPT") for x in self.silent_args)):
            raise ValueError("msi_properties_only")
        if self.installer=="exe" and (not self.silent_args or not self.no_restart_args): raise ValueError("exe_requires_silent_and_no_restart_args")
        if self.installer in ("deb","rpm") and (self.silent_args or self.no_restart_args): raise ValueError("linux_arguments_fixed")
        return self

class CatalogDocument(BaseModel):
    model_config=ConfigDict(extra="forbid")
    schema_version:Literal[1]
    packages:list[PackageManifest]=Field(max_length=1000)


def manifest_hash(package):
    return hashlib.sha256(json.dumps(package.model_dump(),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def installer_argv(package,artifact):
    if package.installer=="msi": return ["msiexec","/i",str(artifact),"/qn","/norestart","REBOOT=ReallySuppress",*package.silent_args]
    if package.installer=="exe": return [str(artifact),*package.silent_args,*package.no_restart_args]
    if package.installer=="deb": return ["dpkg","--install",str(artifact)]
    return ["rpm","--upgrade",str(artifact)]

class Catalog:
    def __init__(self,path=""): self.path=path
    def read(self):
        if not self.path: return []
        try:
            path=Path(self.path)
            if path.stat().st_size>1048576: raise ValueError()
            doc=CatalogDocument.model_validate_json(path.read_text(encoding="utf-8"))
            keys=[(p.id,p.version,p.os_family) for p in doc.packages]
            if len(keys)!=len(set(keys)): raise ValueError()
            return doc.packages
        except (OSError,ValueError): raise CatalogError("catalog_unavailable") from None
    def get(self,pid,version,os_family):
        for p in self.read():
            if (p.id,p.version,p.os_family)==(pid,version,os_family): return p
        raise CatalogError("package_not_approved")
    def listing(self):
        packages=self.read()
        return {"status":"configured" if packages else "empty","packages":[{**p.model_dump(),"manifest_hash":manifest_hash(p),"install_argv":installer_argv(p,"{installer}")} for p in packages]}

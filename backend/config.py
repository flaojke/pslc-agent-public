from dataclasses import dataclass
from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    operator_token: str
    database_url: str
    local_mode: bool = True
    plan_ttl: float = 300
    output_ttl: float = 120
    lease_seconds: float = 180
    api_host: str = "127.0.0.1"
    pilot_network_admitted: bool = False
    pilot_allowed_cidrs: tuple[str, ...] = ()
    pilot_it_reference: str = ""
    pilot_security_reference: str = ""
    tls_cert_file: str = ""
    tls_key_file: str = ""
    public_origin: str = "http://127.0.0.1:8787"
    knowledge_enabled: bool = True
    auth_mode: str = "local"
    auth_policy_path: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_api_audience: str = ""
    oidc_redirect_uri: str = "http://127.0.0.1:8787/auth/callback"
    oidc_client_secret: str = ""
    package_catalog_path: str = ""
    model_url: str = ""
    model_id: str = ""
    model_token: str = ""
    model_config_path: str = ""
    model_ca_file: str = ""
    model_backend: str = "external"
    local_models_enabled: bool = False
    local_models_dir: str = ""
    local_runtime_dir: str = ""
    local_catalog_path: str = ""
    local_runtime_manifest_path: str = ""
    local_model_threads: int = 6
    schedule_vault_path: str = ""
    system_preferences_path: str = ""
    demo_config_path: str = ""
    readiness_eval_path: str = ""
    pilot_plan_path: str = ""
    confluence_kind: str = "dc"
    confluence_bindings_path: str = ""
    confluence_url: str = ""
    confluence_token: str = ""
    confluence_spaces: tuple[str, ...] = ()

    def validate(self):
        if not self.local_mode:
            raise ValueError("production_mode_not_admitted")
        from .transport import validate_transport
        validate_transport(self)
        if not 1<=self.local_model_threads<=32:raise ValueError("invalid_local_threads")
        if len(self.model_id)>128:
            raise ValueError("invalid_model_id")
        if self.auth_mode not in ("accounts","local","oidc"):raise ValueError("invalid_auth_mode")
        if self.auth_mode=="accounts":
            from .integrations import validate_endpoint
            from urllib.parse import urlsplit
            validate_endpoint(self.public_origin)
            if urlsplit(self.public_origin).path not in ("","/"):raise ValueError("invalid_public_origin")
        if self.auth_mode=="oidc":
            from .integrations import validate_endpoint
            from urllib.parse import urlsplit
            if not self.oidc_issuer or not self.oidc_client_id or not self.oidc_api_audience or not self.auth_policy_path:raise ValueError("oidc_not_configured")
            validate_endpoint(self.oidc_issuer);validate_endpoint(self.oidc_redirect_uri)
            if urlsplit(self.oidc_redirect_uri).path!="/auth/callback":raise ValueError("invalid_redirect_uri")
        if self.auth_mode=="local" and len(self.operator_token) < 24:
            raise ValueError("operator_token_too_short")
        if not self.database_url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("unsupported_database")
        if min(self.plan_ttl, self.output_ttl, self.lease_seconds) <= 0:
            raise ValueError("invalid_ttl")


def network_flag():
    value=os.getenv('PSLC_PILOT_NETWORK_ADMITTED','0')
    if value not in ('0','1'):raise ValueError('invalid_pilot_network_flag')
    return value=='1'

def load_settings():
    path = ROOT / ".local/settings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Settings(
        operator_token=os.getenv("PSLC_OPERATOR_TOKEN", data.get("operator_token", "")),
        database_url=os.getenv("PSLC_DATABASE_URL", data.get("database_url", "")),
        api_host=os.getenv("PSLC_API_HOST","127.0.0.1"),
        pilot_network_admitted=network_flag(),
        pilot_allowed_cidrs=tuple(x.strip() for x in os.getenv("PSLC_PILOT_ALLOWED_CIDRS","").split(',') if x.strip()),
        pilot_it_reference=os.getenv("PSLC_PILOT_IT_REFERENCE",""),
        pilot_security_reference=os.getenv("PSLC_PILOT_SECURITY_REFERENCE",""),
        tls_cert_file=os.getenv("PSLC_TLS_CERT",""),
        tls_key_file=os.getenv("PSLC_TLS_KEY",""),
        auth_mode=os.getenv("PSLC_AUTH_MODE",data.get("auth_mode","accounts")),
        public_origin=os.getenv("PSLC_PUBLIC_ORIGIN","http://127.0.0.1:8787"),
        knowledge_enabled=os.getenv("PSLC_CONFLUENCE_ENABLED","0")=="1",
        auth_policy_path=os.getenv("PSLC_AUTH_POLICY",""),
        oidc_issuer=os.getenv("PSLC_OIDC_ISSUER",""),
        oidc_client_id=os.getenv("PSLC_OIDC_CLIENT_ID",""),
        oidc_api_audience=os.getenv("PSLC_OIDC_API_AUDIENCE",""),
        oidc_redirect_uri=os.getenv("PSLC_OIDC_REDIRECT_URI","http://127.0.0.1:8787/auth/callback"),
        oidc_client_secret=os.getenv("PSLC_OIDC_CLIENT_SECRET",""),
        package_catalog_path=os.getenv("PSLC_PACKAGE_CATALOG", ""),
        model_url=os.getenv("PSLC_MODEL_URL", ""),
        model_id=os.getenv("PSLC_MODEL_ID", ""),
        model_token=os.getenv("PSLC_MODEL_TOKEN", ""),
        model_config_path=os.getenv("PSLC_MODEL_CONFIG", str(ROOT/".local/model/connection.enc")),
        model_ca_file=os.getenv("PSLC_MODEL_CA_FILE", ""),
        local_models_enabled=os.getenv("PSLC_LOCAL_MODELS_ENABLED","1")=="1",
        local_models_dir=os.getenv("PSLC_LOCAL_MODELS_DIR",str(ROOT/"models")),
        local_runtime_dir=os.getenv("PSLC_LOCAL_RUNTIME_DIR",str(ROOT/"runtime")),
        local_model_threads=int(os.getenv("PSLC_LOCAL_MODEL_THREADS","6")),
        schedule_vault_path=os.getenv("PSLC_SCHEDULE_VAULT",""),
        system_preferences_path=os.getenv("PSLC_SYSTEM_PREFERENCES",str(ROOT/".local/system/preferences.json")),
        demo_config_path=os.getenv("PSLC_DEMO_CONFIG", str(ROOT/".local/demo/identity.enc")),
        readiness_eval_path=os.getenv("PSLC_READINESS_EVAL_PATH",""),
        pilot_plan_path=os.getenv("PSLC_PILOT_PLAN",""),
        confluence_kind=os.getenv("PSLC_CONFLUENCE_KIND","dc"),
        confluence_bindings_path=os.getenv("PSLC_CONFLUENCE_BINDINGS",""),
        confluence_url=os.getenv("PSLC_CONFLUENCE_URL", ""),
        confluence_token=os.getenv("PSLC_CONFLUENCE_TOKEN", ""),
        confluence_spaces=tuple(x.strip() for x in os.getenv("PSLC_CONFLUENCE_SPACES", "").split(",") if x.strip()),
    )

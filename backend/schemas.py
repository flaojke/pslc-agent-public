from typing import Literal
from .diagnostic_types import EvidenceEnvelope
from pydantic import BaseModel, ConfigDict, Field,model_validator
from .recovery_types import RecoveryResources,RecoveryProof

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Enroll(Strict):
    enrollment_token: str = Field(min_length=24, max_length=128)
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    os_family: Literal["Windows", "Linux"]
    commands_enabled: bool = False
    installations_enabled: bool = False
    diagnostics_enabled: bool = False
    agent_protocol: int = Field(default=0,ge=0,le=1,strict=True)

class CommandCheck(Strict):
    runtime: Literal["powershell","cmd","python","node"]
    code: str = Field(min_length=1,max_length=8000)
    timeout_seconds: int = Field(default=15,ge=1,le=60)
    expected_output: str = Field(min_length=1,max_length=512)
    @model_validator(mode='after')
    def nonblank(self):
        if not self.code.strip() or not self.expected_output.strip():raise ValueError('invalid_command_verification')
        return self

class TaskInput(Strict):
    device_id: str = Field(max_length=64)
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: Literal["health", "command"] = "health"
    runtime: Literal["powershell", "cmd", "python", "node"] = "python"
    code: str = Field(default="", max_length=16000)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    verification:CommandCheck|None=None
    recovery:RecoveryResources|None=None
    @model_validator(mode='after')
    def paired_command(self):
        if self.kind=='command' and (self.verification is None or self.recovery is None):raise ValueError('paired_recovery_and_verification_required')
        if self.kind=='health' and (self.verification is not None or self.recovery is not None):raise ValueError('health_is_read_only')
        return self

class InstallTaskInput(Strict):
    device_id: str = Field(max_length=64)
    session_id: str = Field(min_length=1,max_length=64,pattern=r"^[a-zA-Z0-9_-]+$")
    package_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    package_version: str = Field(pattern=r"^[a-zA-Z0-9_.+~-]{1,64}$")

class InstallationProof(Strict):
    package_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9_.+~-]{1,64}$")
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")
    version_matched: bool = False
    already_installed: bool = False
    reboot_required: bool = False

class Decision(Strict):
    plan_hash: str = Field(min_length=64, max_length=64)
    approve: bool

class CommandProof(Strict):
    verification_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    matched: bool
    exit_code: int | None = None

class Result(Strict):
    lease_id: str = Field(min_length=16, max_length=128)
    outcome: Literal["ok", "process_succeeded", "failed", "timeout", "output_limit", "prerequisite_missing", "commands_disabled", "installed", "already_installed", "reboot_required", "verification_failed", "integrity_failed", "download_failed", "installations_disabled", "unsupported_platform", "outcome_unknown", "collected", "partial", "diagnostics_disabled", "recovery_required", "recovery_unavailable", "recovery_conflict", "recovery_partial", "restored"]
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0, le=3600000)
    installation: InstallationProof | None = None
    command_verification: CommandProof | None = None
    recovery:RecoveryProof|None=None
    diagnostics: EvidenceEnvelope | None = None
    output: str = Field(default="", max_length=65536)
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    memory_percent: float | None = Field(default=None, ge=0, le=100)
    disk_free_gb: float | None = Field(default=None, ge=0)

class Reconcile(Strict):
    outcome: Literal["failed", "cancelled"]
    externally_checked_not_running: Literal[True]

class Chat(Strict):
    question: str = Field(min_length=1, max_length=16000)
class WorkflowRequest(Strict):
    target_ids:list[str]|None=Field(default=None,max_length=500)
    conversation_id:str|None=Field(default=None,max_length=64)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,64}$')
    session_id: str = Field(min_length=1,max_length=64,pattern=r'^[a-zA-Z0-9_-]+$')
    question: str = Field(min_length=1,max_length=8000)


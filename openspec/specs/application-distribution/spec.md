# application-distribution Specification

## Purpose
Проверяемая поставка одного PSLC Agent с заранее загруженными весами или загрузкой после развёртывания без переноса рабочих данных.

## Requirements

### Requirement: DS-001 Equivalent Full and Lite
Релиз SHALL содержать Full с verified Qwen4B и Lite без весов с кнопкой загрузки, с одинаковым кодом/UI/CPU runtime и версиями. Оба SHALL включать manifest/digests/licenses/install/recovery инструкции, без keys/DB/accounts/local identity. Full SHALL запускаться без повторного скачивания весов.
#### Scenario: Clean installations
- **WHEN** распакован Full либо Lite
- **THEN** Full обнаруживает verified модель, Lite показывает download; после загрузки функциональный контракт одинаков

### Requirement: DS-002 Verifiable segmented downloads
Файл больше GitHub asset limit SHALL поставляться частями с digest каждой части и полной поставки, стандартным assemble/verifier и понятной инструкцией. Сборка MUST отклонять missing/изменённую часть до активации. Веса MUST не попадать в git history.
#### Scenario: Damaged part
- **WHEN** часть Full отсутствует либо изменена
- **THEN** сборка отклоняется, готовая установка не заменяется

### Requirement: DS-003 Authorized GitHub publication
Публикация SHALL следовать выбранной пользователем структуре GitHub и содержать ссылки на оба варианта, source tag и SHA256. Недоступные permissions/auth/upload MUST отмечаться честно; локальный artifact MUST не выдаваться за опубликованный. Публикация требует разрешения владельца репозитория.
#### Scenario: Publication unavailable
- **WHEN** отсутствует доступ GitHub
- **THEN** готовые проверенные artifacts доступны локально, конкретный внешний блокер сообщён без потери реализации

### Requirement: DS-004 Recoverable owned installation
Installer SHALL validate Python3.12+ x64 before creating .venv. Explicit -Resume SHALL require matching installer ownership, root and exact manifest binding, and recheck declared file integrity. Changed, foreign or reparse installations MUST be refused without deleting environments or resetting user .local/config. Interrupted installation SHALL be recoverable; completed resume SHALL stay idempotent with pinned model downloads.
#### Scenario: Dependency download interrupted
- **WHEN** pip fails after an owned .venv was created and operator retries with -Resume and unchanged release
- **THEN** ownership/integrity are rechecked and installation continues; existing configuration is preserved
#### Scenario: Changed or unrelated installation
- **WHEN** receipt/marker/manifest/root do not match, a release file changed or .venv is unrelated/reparse
- **THEN** installer refuses before environment mutation and retains existing files
#### Scenario: Unsupported interpreter
- **WHEN** selected interpreter is below3.12 or is not x64
- **THEN** installer refuses before creating .venv
#### Scenario: Completed resume
- **WHEN** the verified owned installation is already complete and -Resume is repeated
- **THEN** no dependency/setup steps are repeated and user configuration remains unchanged

#### Scenario: Supported model downloads after completion
- **WHEN** completed Lite installation contains regular GGUF files matching the manifest-bound pinned model catalog, or its bounded .gguf.partial downloads, and -Resume is repeated
- **THEN** no dependency/setup steps are repeated; arbitrary extra files, reparse paths and changed manifest-declared files including Full weights are still refused

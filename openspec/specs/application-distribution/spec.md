# application-distribution Specification

## Purpose

Проверяемая поставка PSLC Agent без весов моделей. Локальные модели загружаются только по явной кнопке после установки; существующие модели и настройки сохраняются.

## Requirements

### Requirement: DS-001 Download-only application package
Текущая поставка SHALL содержать один install-вариант без весов моделей, с готовым web/CPU runtime/native bundles, manifest/digests/licenses и инструкцией. SourceZIP SHALL также исключать веса. Builder MUST не требовать/читать локальный model cache и MUST отвергать попытку включить GGUF/другие веса в inputs/archive/manifest. Full/parts с весами SHALL не создаваться.
#### Scenario: Clean installations
- **WHEN** установлено приложение без весов
- **THEN** локальная модель показана отсутствующей, доступна явная кнопка загрузки; нет автоматического скачивания и обещания готового inference
#### Scenario: Populated developer cache
- **WHEN** на машине сборки уже есть downloaded models или model cache отсутствует
- **THEN** model-free install/source ZIP формируются без чтения cache, содержат ноль весов; cache не меняется
#### Scenario: Weight injection
- **WHEN** модельные пути/файлы или переименованный GGUF попали во входы/manifest
- **THEN** сборка/проверка отказывает; архив с весами не объявляется current install package

### Requirement: DS-002 Verifiable downloads
Текущие model-free ZIP SHALL иметь manifest и SHA256 для проверки без Full assembler. Повреждённый архив MUST отклоняться до активации. Исторические Full/parts и их проверка SHALL сохраняться как история; новое правило MUST не удалять или перезаписывать прежние поставки/скачанные пользователем модели. Веса MUST не попадать в git history.
#### Scenario: Damaged part
- **WHEN** current ZIP/hash повреждён либо отсутствует/изменена часть исторического Full
- **THEN** проверка отказывает до активации, готовая установка не заменяется; этот legacy сценарий не требует создания нового Full
#### Scenario: Current download
- **WHEN** пользователь выбирает действующий install ZIP
- **THEN** ему предлагается один архив без весов и инструкция загрузки моделей из программы, без обязательной сборки Full
#### Scenario: Historical release
- **WHEN** рассматривается ранее созданная Full поставка
- **THEN** она явно историческая; существующие файлы/хеши/страницы не переписываются текущей сборкой

### Requirement: DS-003 Authorized GitHub publication
Публикация SHALL требовать актуального разрешения владельца и сохранять выбранную приватность репозитория. Current inventory SHALL описывать model-free install/source ZIP и SHA256 без Full. Рабочие данные, ключи и приватные записи разработки MUST не попадать в source/releases.
#### Scenario: Publication unavailable
- **WHEN** доступа или разрешения нет
- **THEN** artifacts остаются локальными; нельзя объявлять успешную публикацию
#### Scenario: Verified release
- **WHEN** владелец разрешил публикацию и все uploaded assets прошли size/SHA256 readback
- **THEN** выдаются реальные ссылки на source tag и install/source ZIP без весов; прежние релизы сохраняются

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
- **THEN** no dependency/setup steps are repeated; arbitrary extra files, reparse paths and changed manifest-declared files including model catalog/runtime files are still refused

### Requirement: DS-005 Native launch without virtual machines
Windows release launch SHALL start the native application without Docker/VM or state migration. Withdrawn VM entrypoints SHALL refuse before Docker/state operations. Provider selection, accounts and node identities SHALL remain unchanged; Windows runtime blocks SHALL retain LI-006 status without security changes or automatic fallback.
#### Scenario: Ordinary Windows start
- **WHEN** operator invokes start-release.ps1 with no arguments or the compatible -Native switch
- **THEN** the installed native application is started with its existing configuration; no VM command or source import is invoked
#### Scenario: Withdrawn VM command
- **WHEN** a withdrawn VM CLI/wrapper or stale VM-only option is invoked
- **THEN** it refuses before any Docker/resource/state action and explains native launch; no alternate model is selected
#### Scenario: Current help
- **WHEN** operator opens installation, administration, model help or About
- **THEN** VM is not a prerequisite or next approved deployment; existing OpenWebUI configuration is documented without claiming a verified external connection

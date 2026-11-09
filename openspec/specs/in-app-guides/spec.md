# in-app-guides Specification

## Purpose
Даёт сотрудникам и администраторам полные практические руководства внутри системы и в GitHub-поставке из одного проверяемого источника.

## Requirements

### Requirement: IG-001 Complete audience guides
Встроенные руководства SHALL раскрывать полный путь установки сервера, native агентов, локальнойLLM/OpenWebUI, обычной работы и администрирования. SHALL включаться prerequisites, Windows/Linux команды, roles/scopes, TLS/CA, backup/update, проблемы и границы приёмки без реальных секретов.
#### Scenario: First deployment
- **WHEN** ИТ-специалист читает инструкцию развёртывания
- **THEN** может выбрать Full/Lite, проверить архив, установить зависимости/сервер, войти и подготовить подключение агента/LLM по конкретным шагам
#### Scenario: Remote enrollment
- **WHEN** агент устанавливается на другой машине
- **THEN** инструкция объясняет доступный HTTPS server, отдельный code/identity/local opt-ins, checksum, foreground/отдельную службу и проверку появления узла

### Requirement: IG-002 Single safe documentation source
Руководства SHALL иметь единый versioned structured source с общими темами и генерацией Markdown для GitHub. UI MUST безопасно отображать текст/таблицы/код без выполнения HTML или команд; ссылки/версия/состав SHALL проверяться. Нет автоматической загрузки/установки по чтению руководства.
#### Scenario: Guide update
- **WHEN** меняется описанный путь/возможность
- **THEN** обновляется источник и regenerated Markdown; check отказывает на рассогласовании
#### Scenario: Offline read
- **WHEN** приложение развёрнуто без интернета
- **THEN** встроенный текст и code steps доступны локально, внешние ссылки не требуются для чтения

### Requirement: IG-003 Truthful installation boundaries
Инструкции SHALL различать source/dev и готовые Full/Lite архивы, необходимость Python dependencies/wheelhouse, CPU model weights и download; реальные сборки/fixtures/services/production acceptance MUST не смешиваться. Bootstrap admin/admin SHALL описываться без сброса существующих УЗ или принудительного нового шага.
#### Scenario: Full without package index
- **WHEN** выбран Full на изолированном сервере
- **THEN** читатель понимает, что веса включены, а Python зависимости требуют подготовленного wheelhouse либо package index; автономный server exe не обещан

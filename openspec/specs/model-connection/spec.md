# model-connection Specification

## Purpose
Даёт ИИ-специалистам воспроизводимый способ подключить доступные модели Open WebUI к PSLC Agent без раскрытия API key, предположений о model IDs и неявного выполнения инструментов.

## Requirements

### Requirement: MC-001 Open WebUI handoff
Инструкция SHALL описывать Bearer auth, GET /api/models, POST /api/chat/completions, точный model ID, PSLC_MODEL_URL с /api и PSLC_MODEL_TOKEN/ID. Недостаток URL/доступа/structured JSON MUST обозначаться непроверенным, без подстановки выдуманной модели.

#### Scenario: Only API key available
- **WHEN** известен только key
- **THEN** специалист получает точный перечень недостающих параметров и проверки Qwen/Gemma

### Requirement: MC-002 Safe model probe
Probe SHALL запрашивать ключ скрыто/из приватного окружения, проверять TLS/endpoint и bounded responses без redirects, не сохранять key или свободный ответ. Проверка SHALL использовать только фиксированный безвредный JSON prompt и не вызывать node/Confluence/tools API.

#### Scenario: Bad key or non JSON response
- **WHEN** провайдер отказал или модель не соблюла контракт
- **THEN** фиксированный диагностический статус без секрета/вывода; интеграция не объявляется готовой

### Requirement: MC-003 Administrative connection workspace
Администратор SHALL настраивать адрес Open WebUI, API key и точный доступный model ID через UI, загружать список моделей, проверять выбранную модель, сохранять или отключать подключение. Остальные роли MUST не получать конфигурацию и не вызывать сетевые проверки. Неизменённый ключ SHALL повторно использоваться только для того же адреса.
#### Scenario: Configure from the interface
- **WHEN** admin вводит адрес и ключ
- **THEN** получает доступные IDs, выбирает модель, видит проверку JSON-контракта и сохраняет подключение
#### Scenario: Different host with retained key
- **WHEN** адрес изменён без ввода нового ключа
- **THEN** сетевой запрос отклоняется до передачи сохранённого ключа
#### Scenario: Non admin access
- **WHEN** обычный сотрудник обращается к настройкам или probe
- **THEN** возвращается403 и upstream не вызывается

### Requirement: MC-004 Protected connection secret
API key SHALL сохраняться только в защищённом server-local secret store, переживать рестарт и заменяться/удаляться администратором. API MUST не возвращать сохранённый ключ; браузер MUST не хранить его в local/session storage. БД, metadata backup, release и отчёты MUST не содержать ключ. Ошибка расшифровки SHALL отключать модель до явной перенастройки.
#### Scenario: Restart and secret replacement
- **WHEN** процесс перезапущен либо admin заменил ключ
- **THEN** используется соответствующий защищённый ключ без его выдачи API; новая запись атомарна
#### Scenario: Unreadable secret
- **WHEN** secret повреждён либо запущен под другой identity
- **THEN** UI показывает фиксированный отказ, модель не использует скрытый fallback

### Requirement: MC-005 Bounded honest connection checks
Список моделей и probe SHALL вызываться server-side по TLS (HTTP только loopback), без redirects, с лимитами времени/размера и фиксированными ошибками. Probe SHALL использовать безвредный JSON prompt и пустые tools/tool_ids. Список/ответ провайдера MUST не становиться HTML, исполняемыми действиями или долговременным свободным текстом.
#### Scenario: Provider rejection or malformed response
- **WHEN** upstream вернул401/403, redirect, timeout либо некорректный JSON
- **THEN** показан фиксированный результат без ключа/сырого ответа; прежнее подключение сохранено
#### Scenario: Probe passes
- **WHEN** выбранная доступная модель вернула точный JSON
- **THEN** проверка отмечена успешной для текущего draft, но real_task_quality_verified=false

### Requirement: MC-006 Atomic current model configuration
Сохранённое подключение SHALL применяться без рестарта для новых chat/planning/investigation/RAG обращений. Один запрос SHALL использовать целостный snapshot адреса/ключа/модели. Конкурирующее сохранение или устаревшая проверка MUST не перезаписывать новую ревизию. Изменение draft SHALL сбрасывать его видимую проверку; restart MUST не выдавать старый probe за свежую проверку.
#### Scenario: Concurrent edits
- **WHEN** два администратора сохраняют одну исходную ревизию
- **THEN** только первое сохранение успешно, второе требует обновить настройки
#### Scenario: Disable
- **WHEN** admin отключил подключение
- **THEN** новые обращения не вызывают прежнюю модель и не возвращаются к env fallback

### Requirement: MC-007 Selectable model profiles
Admin SHALL переключать активную модель между проверяемыми local catalog моделями и сохранёнными OpenWebUI profiles. Переключение SHALL сохранять остальные profiles/ключи и применять целостный snapshot для новых запросов. UI MUST показывать текущий provider/model и отсутствие готовности без скрытой подмены.
#### Scenario: Switch back to local
- **WHEN** выбран local после OpenWebUI
- **THEN** ключ OpenWebUI сохранён, новые запросы используют выбранную local модель после проверки файлов

### Requirement: MC-008 Compatible provider migration
Новая установка SHALL выбирать local Qwen4B. Upgrade SHALL сохранять прежнюю явно настроенную OpenWebUI модель либо disabled состояние. Невалидная encrypted конфигурация MUST не вызывать автоматический fallback. Существующие metadata schema/УЗ/узловые identities SHALL сохраняться; M6 не понижает текущую schema9.
#### Scenario: Legacy external configuration
- **WHEN** обновлена установка0.11 с сохранённым ключом
- **THEN** прежний выбор и credential сохранены; local становится доступной опцией

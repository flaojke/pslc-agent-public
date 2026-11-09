# operations-recovery Specification

## Purpose
Поддерживает сопровождение PSLC Agent одним владельцем через проверяемую БД, резервирование только метаданных, консервативное восстановление и доказательные сценарные проверки без исходных журналов.

## Requirements

### Requirement: OP-001 Supported database evidence
SQLite development и PostgreSQL SHALL сохранять одинаковый metadata контракт. Миграции/конкурентный dispatch SHALL соблюдать5/20 и500 узлов. Отчёт SHALL фиксировать реально проверенные engine/version/среды отдельно от корпоративной приёмки.

#### Scenario: Concurrent PostgreSQL polling
- **WHEN** несколько соединений одновременно допускают задания
- **THEN** фактический active count не превышает5/owner-session и20/fleet

### Requirement: OP-002 Consistent bounded metadata backup
Backup SHALL читать согласованный snapshot только известных typed metadata, включая account password verifiers/grants и enrollment profiles текущей схемы, без паролей/volatile payload. Неизвестный столбец/сырой JSON/unsupported schema MUST отклоняться. Файл SHALL создаваться без перезаписи с ограниченным доступом и digest.

#### Scenario: Raw marker or corrupt schema
- **WHEN** вывод содержит marker либо БД содержит неподдерживаемое поле
- **THEN** marker не сохраняется, неподдерживаемый snapshot не экспортируется как допустимый

### Requirement: OP-003 Empty target recovery
Restore SHALL требовать ожидаемый digest, поддерживаемую схему и пустую БД. Узловые credential hashes, account verifiers/grants/history SHALL сохраняться; pending планы MUST не становиться исполнимыми, running/cancel_requested SHALL стать outcome_unknown. Сессии не восстанавливаются, узловые операции не вызываются.

#### Scenario: Wrong target or interrupted task
- **WHEN** target уже содержит таблицы либо в backup есть активная задача
- **THEN** существующий target не изменяется; в пустом target активный эффект требует ручной сверки

### Requirement: OP-004 Payload free operational health
ИТ/ИБ с административным допуском SHALL получать bounded metadata-only статус БД/schema/очередей/cache counts и configured-unverified integrations. Ошибка соединения SHALL возвращать фиксированный отказ без DSN/secret/payload. Health MUST не объявлять внешние подключения проверенными по наличию конфигурации.

#### Scenario: Database outage
- **WHEN** БД недоступна
- **THEN** health показывает degraded/503 без чувствительных деталей, узловая команда не запускается

### Requirement: OP-005 Repeatable scenario evidence
Eval runner SHALL связывать A01–A08 с реальными командами/exit codes/counts/source fingerprint и отмечать synthetic/local/external separately. Backup/restore drill SHALL измерять время и проверять историю/retention. Начальные целиRPO24h/RTO4h MUST не выдаваться за доказанное SLA или проведённый пилот.

#### Scenario: Fixture only acceptance
- **WHEN** сценарии прошли только на fixtures
- **THEN** отчёт сохраняет эту область и оставляет корпоративную приёмку pending

### Requirement: OP-006 Compatible conversation metadata
Schema7 SHALL аддитивно сохранять учётные записи, узлы, credentials, допуски и историю; добавлять тип узла/metadata бесед и групп. Backup/restore SHALL поддерживать известные typed поля без полного диалога; незавершённые ответы/планы после потериRAM MUST не возобновляться.
#### Scenario: Upgrade existing installation
- **WHEN** schema6 обновляется до7 либо восстанавливается совместимый metadata backup
- **THEN** identity и допуски прежние, типunknown, контекст утрачен явно, узловые действия автоматически не выполняются

### Requirement: OP-008 Protected preference recovery
Системные предпочтения SHALL храниться отдельным bounded versioned config с атомарной записью/правами владельца, без изменения schema7/УЗ/узлов/model secret. Backup guide SHALL явно включать эту отдельную конфигурацию. Corruption MUST не приводить к молчаливому сбросу или отражению содержимого в ошибке.
#### Scenario: Invalid preference file
- **WHEN** config повреждён/имеет лишние поля
- **THEN** настройки сообщают фиксированную ошибку, файл не перезаписывается default значениями, существующие данные/права сохранены

### Requirement: OP-007 Compatible recovery metadata
Schema8 SHALL аддитивно сохранять credentials/УЗ/права/историю и добавлять observed protocol/typed recovery lineage/программу/receipt без baseline bytes/forward кода. Backup SHALL принимать известные schema6/7/8. Отсутствующий capsule/ключ после restore MUST давать unavailable, без автоматического forward/undo.
#### Scenario: Upgrade and restore
- **WHEN** schema7 обновляется либо metadata snapshot восстанавливается
- **THEN** старые identities и health доступны, незавершённый forward не повторяется, rollback требует действительного capsule и нового решения

### Requirement: OP-008 Schedule schema and conservative restore
Schema9 SHALL сохранять identities/grants/M4 receipts, совместимые metadata backups6/7/8/9 и schedule lineage без template content. Restore SHALL паузить правила/сбрасывать consent, не повторять claimed/unknown forward. Missing vault MUST обозначаться unavailable.

#### Scenario: Upgrade and restore
- **WHEN** schema8 обновляется либо snapshot восстановлен
- **THEN** данные M4/идентификаторы сохранены, запуск расписаний требует действующего отдельно проверенного vault и нового согласия

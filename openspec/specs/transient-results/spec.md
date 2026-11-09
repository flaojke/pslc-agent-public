# transient-results Specification

## Purpose
Сохраняет полезную историю исполнения PSLC Agent без превращения системы в хранилище исходных журналов и полного вывода команд.

## Requirements

### Requirement: TR-001 Volatile payload
Одноразовые вывод, диалог/ответ модели, forward-код и строки postcheck SHALL быть bounded RAM, без копий в SQL/очереди/telemetry. Исключения: явные имена бесед, typed anti-program metadata, encrypted node baseline/package recovery и явно сохранённый schedule template по TR-004. Raw logs MUST не сохраняться; после рестарта forward не возобновляется.

#### Scenario: Recovery retention exceptions
- **WHEN** подготовлен откат
- **THEN** metadata содержит только opaque IDs/hash/type counts; configuration baseline и approved uninstall/absence/version-probe contract остаются только в зашифрованном узловом capsule, без SQL/телеметрии

#### Scenario: Retention marker
- **WHEN** вывод/вопрос/ответ содержит уникальный маркер
- **THEN** маркер отсутствует в БД, журнале событий и статистике после завершения и повторного открытия сервера

#### Scenario: Volatile expiry
- **WHEN** истёк TTL временного результата
- **THEN** API больше не возвращает полный вывод, но метаданные и вычисленный краткий итог остаются

#### Scenario: Conversation metadata exception
- **WHEN** оператор явно задаёт короткое имя беседы и отправляет содержащий marker запрос
- **THEN** имя/ID/тип/время сохраняются, а marker вопроса/ответа отсутствует в БД и backup

### Requirement: TR-002 Typed durable outcomes
Система SHALL сохранять только разрешённые metadata/краткие typed итоги. Raw text/forward-код/промпты/ожидаемые строки MUST не попадать в SQL/историю. Для command успех процесса и postcheck различаются; install требует version proof; investigation — refs/typed категории. Approved anti-program SHALL иметь строгую схему IDs/hash/type counts без свободного кода, путей/содержимого ресурсов или вывода.

#### Scenario: Malicious summary
- **WHEN** агент прислал сырой текст в непредусмотренном поле
- **THEN** этот текст не записывается в долговечный итог или ошибку

#### Scenario: Installation retention
- **WHEN** вывод установщика или проверки содержит уникальную исходную строку
- **THEN** она не сохраняется в истории/БД/событиях, а результат содержит только разрешённые ID/версию/хеш/коды/флаги проверки

#### Scenario: Command proof retention
- **WHEN** команда проверена по точной ожидаемой строке
- **THEN** сохраняются только хеш проверки и typed matched/exit code, а исходная/ожидаемая строки не сохраняются

#### Scenario: Diagnostic retention
- **WHEN** журнал или анализ модели содержит marker
- **THEN** marker отсутствует в durable истории/backup; ID, время, счётчики и typed категории остаются

### Requirement: TR-003 Confirmation statistics
Система SHALL учитывать запрошенные/подтверждённые/отклонённые/истёкшие решения и ожидание, связывая их с исходом без сырых данных. Повторный запрос/результат MUST не удваивать счётчики.

#### Scenario: Replayed confirmation
- **WHEN** уже подтверждённый план подтверждают повторно
- **THEN** число подтверждений не увеличивается второй раз

### Requirement: TR-004 Explicit schedule template exception
Явно сохранённый prompt/script/postcheck/resources расписания MAY сохраняться только в отдельном encrypted bounded server-local vault. SQL SHALL хранить typed metadata/hash/consent/исходы; raw logs/output остаются RAM. Metadata restore MUST не активировать правила без повторного review.

#### Scenario: Encrypted exception
- **WHEN** оператор сохранил шаблон расписания
- **THEN** читаемый текст доступен владельцу после входа, отсутствует в SQL/backup/events/releases

# task-scheduling Specification

## Purpose
Автоматизирует заранее согласованные повторяющиеся задания PSLC Agent с проверяемыми границами, временем и фактическими результатами.

## Requirements

### Requirement: TS-001 Owned rule lifecycle
Оператор SHALL создавать/редактировать/удалять собственные правила, включать их только по точному review hash и явно приостанавливать. Изменение definition SHALL сбрасывать согласие. Чужой prompt/script MUST не раскрываться.

#### Scenario: Review required
- **WHEN** правило создано/изменено
- **THEN** оно paused; изменение hash/revision требует нового согласия

#### Scenario: Delete and pause
- **WHEN** владелец выключил правило
- **THEN** новые выдачи запрещены; уже активное действие требует завершения/сверки

### Requirement: TS-002 Calendar and unique occurrence
Правило SHALL поддерживать once/daily/weekly с локальным HH:MM и сохранённой IANA zone, Monday0. Один slot SHALL допускаться один раз. DST повтор выбирает первый fold; отсутствующее время/пропуск после downtime MUST не вызывать burst/replay.

#### Scenario: DST and double tick
- **WHEN** локальное время повторилось либо два tick увидели один slot
- **THEN** допускается не более одного run

#### Scenario: Restart
- **WHEN** срок прошёл во время остановки
- **THEN** история указывает missed/interrupted, forward не повторяется

### Requirement: TS-003 Safe scheduled modes
Планировщик SHALL поддерживать prompt→план с новым ручным решением, fixed health/errors и утверждённый static script с postcheck/recovery. Автоматическое исполнение SHALL быть ограничено неизменным consent template. Новая LLM-команда MUST не наследовать согласие расписания.

#### Scenario: Dynamic plan
- **WHEN** промпт предложил мутацию
- **THEN** план и анти-программа ожидают новое exact решение

#### Scenario: Static script
- **WHEN** наступил согласованный slot
- **THEN** исполняются только frozen code/check/resource contract и новые bound anti-pointers

### Requirement: TS-004 Explicit target consent
Каждый run SHALL замораживать разрешённые current IDs в пределах consent IDs и fresh scopes. Workstations MUST исключать server/unknown/demo. Script SHALL использовать manual frozen IDs одной ОС. Не допущенные узлы SHALL отражаться в typed skips.

#### Scenario: Morning workstations
- **WHEN** правило07:00 выбрало пользовательские ПК
- **THEN** серверы/unknown/demo не получают задачи

#### Scenario: New device
- **WHEN** в парк добавлен новый ПК
- **THEN** он требует review consent, не наследует автоматическую мутацию

### Requirement: TS-005 Durable protected templates
Только явно сохранённые schedule prompt/script/check/resources SHALL храниться в отдельном bounded encrypted server-local vault с проверкой hash. SQL/telemetry/metadata backup MUST не содержать этот текст или raw logs/output. Потеря ключа/повреждение SHALL закрывать запуск.

#### Scenario: Tampered template
- **WHEN** файл/ключ/hash не соответствует definition
- **THEN** run blocked, выполнение и сброс отсутствуют

#### Scenario: Retention marker
- **WHEN** prompt/скрипт/вывод содержит marker
- **THEN** marker отсутствует в SQL/backup/events, template зашифрован отдельно

### Requirement: TS-006 Bounded run execution
Планировщик SHALL соблюдать5workers/5session/20fleet/one-active-or-unknown-node, запрещать overlap одного правила и автоматический retry/rollback. Статистика автоматического допуска MUST отличаться от человеческих решений.

#### Scenario: Overlap
- **WHEN** прежний run pending/active/unknown
- **THEN** новый slot skipped_overlap без новой выдачи

#### Scenario: Outcome loss
- **WHEN** процесс потерял определённость
- **THEN** unknown требует сверки, новый запуск не выдаётся слепо

### Requirement: TS-007 Reviewable workspace and history
Раздел «Планировщик» SHALL показывать календарь/zone/следующий срок, private template, frozen targets/consent, bounded историю и typed исходы/скрытые причины. Владелец SHALL видеть volatile план/решение/результаты/ручной откат. Обучающие материалы SHALL объяснять каждый режим и границы.

#### Scenario: Review run
- **WHEN** оператор открыл запуск
- **THEN** видит plan/confirmation или typed результат, недоступный контекст не выдумывается

#### Scenario: Mobile
- **WHEN** пользователь управляет с390px/клавиатуры
- **THEN** основные действия доступны без горизонтального overflow документа

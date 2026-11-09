# task-rollback Specification

## Purpose
Обеспечивает подготовленный до мутации анти-скрипт и ручной проверяемый откат объявленных ресурсов без вымышленной гарантии восстановления произвольного состояния всей ОС.

## Requirements

### Requirement: RR-001 Prepared paired action
Изменяющее действие SHALL иметь forward и исполняемую typed anti-program до подтверждения/выдачи, с общим binding цели/объектов/hash. Неподдерживаемый или необратимый охват MUST отклоняться до выдачи. Health/investigate SHALL явно обозначаться без изменений/без отката.
#### Scenario: No reversible contract
- **WHEN** command/install не имеет проверяемого recovery contract
- **THEN** payload не выдаётся, оператор видит требуемое уточнение/отказ
#### Scenario: Review pair
- **WHEN** показан план
- **THEN** доступны forward, anti-script, границы восстановления и проверка; изменение любого элемента требует нового решения

### Requirement: RR-002 Baseline before mutation
Узел SHALL до forward проверить local opt-in/provider/runtime/quota и сохранить защищённый baseline объявленных конфигурационных ресурсов. Ошибка snapshot MUST запрещать мутацию. Snapshot SHALL оставаться только на узле, без raw logs/поднятия привилегий.
#### Scenario: Snapshot failure
- **WHEN** файл/ACL/служба/registry/provider/место недоступны
- **THEN** основной скрипт не запускается и исход не объявляется успешным
#### Scenario: Failed forward
- **WHEN** snapshot готов, но основной скрипт завершился ошибкой либо частично
- **THEN** capsule остаётся доступным для ручного восстановления после сверки фактического исхода

### Requirement: RR-003 Explicit rollback plan
«Откатить задачу» SHALL готовить новый owner-bound/idempotent точный план для шага либо группы с исходными ID/hash/anti-program/сроком/покрытием. Подготовка MUST не исполнять откат. Требуется отдельное подтверждение и повторная проверка прав.
#### Scenario: Reject rollback
- **WHEN** оператор отклонил план отката
- **THEN** состояние устройств не меняется и решение учитывается один раз
#### Scenario: Foreign request
- **WHEN** другой сотрудник запрашивает или подтверждает чужой rollback
- **THEN** действие и приватная программа не раскрываются/не исполняются

### Requirement: RR-004 Drift and restored state
Anti-script SHALL сверять capsule/original hash/program hash и текущее состояние с сохранённым post-state либо уже baseline. Drift MUST давать conflict без перезаписи чужих изменений. Итог восстановлено SHALL требовать проверки всех объявленных ресурсов, независимо от exit code forward/undo.
#### Scenario: External change
- **WHEN** после forward ресурс изменил другой процесс
- **THEN** восстановление конфликтующего ресурса не выдаётся за безопасный успех
#### Scenario: Partial undo
- **WHEN** восстановилась только часть ресурсов
- **THEN** виден partial/ошибка и повтор требует нового ручного плана; уже восстановленные ресурсы проверяются идемпотентно

### Requirement: RR-005 Existing execution boundaries
Rollback SHALL использовать обычные lease,5/сеанс,20/парк и сериализацию узла. В группе порядок SHALL быть обратным отдельно на узле. Unknown/running/cancel_requested исходного действия MUST требовать завершения/сверки перед откатом. Автоматический forward retry/rollback запрещён.
#### Scenario: Unknown outcome
- **WHEN** forward потерял определённость
- **THEN** кнопка объясняет необходимость сверки отсутствия процесса; никакой конкурентный undo не запускается
#### Scenario: Reverse group
- **WHEN** подтверждён откат нескольких шагов/узлов
- **THEN** внутри узла предыдущий шаг восстанавливается после следующего; независимые узлы продвигаются в пределах5/20

### Requirement: RR-006 Bounded protected recovery
Утверждённая anti-program SHALL сохраняться как строгие typed metadata; baseline только в зашифрованном локальном vault с правами владельца. Лимиты/срок SHALL показываться до решения. Потеря/истечение/corruption capsule MUST давать unavailable, не повтор forward и не фиктивный откат.
#### Scenario: Restart
- **WHEN** сервер/агент перезапущен
- **THEN** raw forward не возобновляется; отдельный rollback возможен только с действительной metadata/capsule и новым решением
#### Scenario: Retention
- **WHEN** вывод или журнал содержит marker
- **THEN** marker не переносится в metadata/anti-program/backup; capsule хранит только явно объявленный configuration baseline

### Requirement: RR-007 Honest coverage and lineage
UI SHALL различать snapshot готов/не нужен/недоступен, запрошенный откат, conflict/partial/восстановлено и связывать исходную/восстановительную задачи. Проверка declared файлов/службы/registry/package presence MUST не называться полным восстановлением ОС, памяти служб или внешних эффектов.
#### Scenario: Historical result
- **WHEN** исходное действие откатили
- **THEN** его история сохраняется, прежнее постусловие помечено историческим, а восстановление имеет отдельный проверенный итог

#### Scenario: Historical facts in AI context
- **WHEN** восстановление объявленных ресурсов подтверждено
- **THEN** UI фактов и дальнейший контекст модели не представляют прежнее постусловие как текущее; история сохраняется

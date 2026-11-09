# node-management Specification

## Purpose
Позволяет администратору подключать неизвестные заранее Windows/Linux машины и изменять их серверные разрешения через проверяемые настройки, сохраняя независимую локальную политику агента.

## Requirements

### Requirement: NM-001 Bound onboarding
Администратор SHALL готовить одноразовый10min код с ожидаемой ОС, отображаемым именем и потолком capabilities, получать agent config и инструкции Windows/Linux. Enrollment MUST проверять ОС/потолок; в БД только hash/profile. Неизвестный парк MUST не требовать фиктивного предзаполнения IDs.

#### Scenario: Overprivileged enrollment
- **WHEN** агент просит capabilities выше профиля либо другую ОС
- **THEN** подключение отклоняется и токен не расходуется; credential никогда не записывается открытым

### Requirement: NM-002 Exact node settings
Изменение имени/capabilities узла SHALL готовить owner-bound план с hash/TTL/current-state digest и требовать admin confirmation. Revoked/изменённый/чужой/истёкший план MUST не применяться. Включение серверного допуска MUST не менять локальный config агента или исполнять команду.

#### Scenario: Concurrent update
- **WHEN** состояние узла изменилось между показом и подтверждением
- **THEN** нужен новый план, identity/credential/локальные opt-ins сохраняются

### Requirement: NM-003 Disable before dispatch
Отключение capability SHALL запрещать соответствующие pending/direct/workflow jobs до выдачи. Активный процесс MUST не объявляться отменённым/откаченным; дальнейший анализ при снятом diagnostics допуске прекращается. UI SHALL показывать freshness/revoke и необходимость отдельного локального допуска.

#### Scenario: Revoked command capability
- **WHEN** command был подтверждён, затем server capability отключён
- **THEN** агент не получает эту команду, статистика/краткий отказ сохранены

### Requirement: NM-004 Honest virtual device
Виртуальный узел SHALL иметь постоянную явную DEMO-VIRTUAL маркировку в списке, настройках и результатах. Он SHALL симулировать только health; команды, установки и журналы MUST не исполняться на хосте. Демо SHALL проходить обычные scope/план/подтверждение/lease/отзыв проверки и MUST не считаться реальной приёмкой ОС.
#### Scenario: Demonstrate administration
- **WHEN** оператор подтверждает health-план для демо-узла
- **THEN** получает проверенный результат с источником demo_simulation, CPU/RAM/disk синтетические
#### Scenario: Change demo permissions or label
- **WHEN** администратор пытается включить исполняемые capabilities либо убрать DEMO-VIRTUAL prefix
- **THEN** настройка отклоняется; реальный executor не вызывается

### Requirement: NM-005 Single persistent demo identity
Создание демо SHALL быть явным локальным административным действием, с защищённой credential и сохранением ID. Повторное создание/рестарт MUST не создавать дубликат или отменять отзыв. Новая установка без команды создания SHALL иметь пустой inventory.
#### Scenario: Restart or revoked demo
- **WHEN** API/демо перезапущены либо узел отозван
- **THEN** ID сохранён, второй узел не создан, отозванный не обрабатывает задания
#### Scenario: Backup
- **WHEN** экспортируются метаданные демо-задачи
- **THEN** источник симуляции сохраняется, credential и сырые outputs отсутствуют

### Requirement: NM-006 Explicit device role
Узел SHALL иметь явный тип workstation/server/unknown, независимый отОС и имени. Администратор SHALL задавать его в подключении или точном подтверждаемом изменении настроек. Старые узлы SHALL мигрировать вunknown, без изменения identity/credentials/capabilities.
#### Scenario: Linux workstation and Windows server
- **WHEN** типы назначены администратором
- **THEN** выбор пользовательскихПК включает Linuxworkstation и исключает Windowsserver; пропущенное поле старого API сохраняет текущий тип

### Requirement: NM-007 Explicit discovered onboarding
Кандидат обнаружения SHALL открывать обычную подготовку подключения с явным выбором ОС/типа/разрешений и экспортом полного агента. IP обнаружения MUST не создавать managed device, credential или разрешение исполнения. Одноразовый код SHALL оставаться owner-bound, ограниченным по времени и передаваться отдельно от config/ZIP.
#### Scenario: Discovery is insufficient
- **WHEN** компьютер найден и выбран для подключения
- **THEN** он ещё не входит в цели заданий; enrollment проверяет профиль/ОС/capabilities и только затем создаёт устройство

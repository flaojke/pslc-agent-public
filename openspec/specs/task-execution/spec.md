# task-execution Specification

## Purpose
Предоставляет оператору проверяемое исполнение диагностических и подтверждённых командных заданий с ограниченной параллельностью и явным неизвестным исходом.

## Requirements

### Requirement: TE-001 Exact plan confirmation
Команда/установка SHALL требовать действующее owner-bound решение по хешу нормализованных цели/runtime/кода/timeout/проверок. Изменённый/просроченный план MUST отклоняться. Workflow SHALL подтверждаться целиком, включая health. Единственное исключение нового решения для каждого запуска — неизменное заранее согласованное расписание по TE-007; dynamic LLM план требует нового exact решения.

#### Scenario: Wrong hash
- **WHEN** оператор подтверждает другой хеш либо истёкший план
- **THEN** командное задание не становится готовым к выдаче

#### Scenario: Rejection
- **WHEN** оператор отклоняет план
- **THEN** команда не выдаётся агенту и решение учитывается в статистике

#### Scenario: Installation plan binding
- **WHEN** подтверждается установка ПО
- **THEN** хеш связывает ID узла, snapshot записи каталога, проверку версии и timeout; изменённая запись требует нового плана

#### Scenario: Whole workflow confirmation
- **WHEN** из основного раздела подготовлена цепочка
- **THEN** одно решение связывает весь точный план и не допускает отдельный неподтверждённый шаг

#### Scenario: Owner binding
- **WHEN** другой сотрудник пытается подтвердить точный хеш
- **THEN** действие не допускается, dispatch повторно проверяет полномочия владельца

### Requirement: TE-002 Fleet and session concurrency
Диспетчер SHALL выдавать не более 5 активных шагов одного сеанса и 20 активных удалённых заданий на парк. Ожидающие подтверждения и очередные задачи MUST не занимать активный слот; конфликтующие задания одного узла MUST сериализоваться.

#### Scenario: Session limit
- **WHEN** уже активны 5 заданий одного сеанса
- **THEN** шестое остаётся в очереди даже при наличии свободного глобального слота

#### Scenario: Fleet limit
- **WHEN** уже активны 20 удалённых заданий разных сеансов
- **THEN** следующее не выдаётся до освобождения слота

### Requirement: TE-003 Leases and uncertain outcome
Каждая выдача SHALL иметь одноразовый lease ID; повторный poll MUST не выдавать то же активное действие снова. Потеря ответа, истечение lease или перезапуск сервера при активном действии MUST приводить к outcome_unknown и запрету слепого повтора. Поздний результат SHALL приниматься только от исходного узла и lease.

#### Scenario: Server restart
- **WHEN** сервер перезапущен во время выполнения команды
- **THEN** задача отмечена outcome_unknown и повторная команда не выдаётся автоматически

#### Scenario: Duplicate completion
- **WHEN** корректный результат доставлен повторно
- **THEN** состояние и счётчики не меняются повторно

#### Scenario: Long installation
- **WHEN** подтверждённая установка допускает выполнение дольше обычного lease
- **THEN** lease покрывает ограниченный timeout с запасом, а unknown-результат/его повторная доставка не запускают новую установку и не дублируют событие

### Requirement: TE-004 Bounded explicit executor
Узловой исполнитель SHALL поддерживать health и явно включаемый command mode для PowerShell/cmd/Python/Node.js, определять runtime и ограничивать время/вывод. Отсутствующий runtime MUST не устанавливаться скрыто. Код 0 MUST означать только успех процесса. Если план включает постпроверку, она SHALL исполняться отдельно и bounded; verified требует её совпадения и связанного типизированного proof.

#### Scenario: Commands disabled
- **WHEN** агент запущен без разрешения команд
- **THEN** командный payload не выполняется

#### Scenario: Missing runtime
- **WHEN** выбранный runtime отсутствует
- **THEN** возвращается prerequisite_missing без попытки его установки

#### Scenario: Timeout
- **WHEN** процесс превысил timeout или лимит вывода
- **THEN** исполнитель завершает дерево процесса доступным механизмом и возвращает ограниченный типизированный исход

#### Scenario: Explicit command verification
- **WHEN** подтверждённый план команды содержит отдельную проверку и ожидаемый результат
- **THEN** агент после успеха команды исполняет только эту проверку; несовпадение или отсутствие proof не считается verified

### Requirement: TE-005 Cancellation and reconciliation
Отмена SHALL запрещать новые шаги, не объявляя уже запущенный процесс отменённым задним числом. Неизвестный исход SHALL требовать позднего проверенного результата либо явного решения оператора после внешней сверки.

#### Scenario: Cancel running action
- **WHEN** оператор отменяет уже выданное действие
- **THEN** интерфейс показывает запрос отмены/необходимость сверки, а не вымышленный откат

### Requirement: TE-006 Recovery bound mutation
Command/install SHALL требовать проверяемый recovery contract и связывать forward, anti-program, target/resource hashes и проверки одним подтверждением. Невозможность подготовки baseline/provider/protocol MUST запрещать выдачу/запуск. Произвольный read_only флаг MUST не обходить это правило.
#### Scenario: Legacy command
- **WHEN** старый клиент отправил команду без recovery contract
- **THEN** запрос отклоняется до dispatch; health/investigate остаются доступными
#### Scenario: Anti program changed
- **WHEN** anti-program/ресурсы изменились после показа
- **THEN** старое подтверждение не подходит и никакая часть нового действия не выполняется

### Requirement: TE-007 Scheduled exact template consent
Для scheduled fixed profile/static script SHALL допускаться заранее подтверждённый immutable rule hash вместо нового человеческого решения на каждый slot. Binding SHALL включать calendar/scope/IDs/template/recovery/check. Prompt-generated план MUST требовать обычного exact решения;5/20/lease/baseline неизменны.

#### Scenario: Template changed
- **WHEN** код/проверка/ресурс/цель/календарь изменились
- **THEN** прежнее согласие не допускает выдачу

#### Scenario: Auto admission
- **WHEN** допущен неизменный static run
- **THEN** фиксируется schedule admission, не человеческое confirmed событие

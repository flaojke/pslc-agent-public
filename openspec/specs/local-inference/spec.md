# local-inference Specification

## Purpose
Управляемое локальное выполнение проверенных GGUF моделей на CPU без передачи рабочих данных внешнему провайдеру.

## Requirements

### Requirement: LI-001 Pinned local model integrity
Каталог SHALL закреплять revision/имя/размер/SHA256/лицензию каждой GGUF. Default SHALL быть Qwen3.5-4B Q4_K_M; Qwen2B SHALL оставаться опцией. Только полностью проверенные файл и runtime MUST допускаться к запуску; partial/изменённый файл MUST не считаться готовым.
#### Scenario: Corrupt or incomplete weights
- **WHEN** размер/hash не совпал либо файл partial
- **THEN** модель не запускается; UI показывает отсутствие/ошибку и повторную загрузку

### Requirement: LI-002 Explicit bounded download
Global admin SHALL запускать/отменять/повторять загрузку из UI с прогрессом. Загрузка SHALL принимать только pinned catalog URLs и проверенные HTTPS redirects, ограничивать размер/время/диск и атомарно активировать verified файл. Restart MUST не считать незавершённую загрузку завершённой.
#### Scenario: Interrupted download
- **WHEN** соединение оборвалось или admin отменил
- **THEN** прежняя готовая модель сохраняется, partial не используется, повтор доступен
#### Scenario: Non admin
- **WHEN** другой сотрудник вызывает download/cancel
- **THEN**403 без загрузки или изменения файлов

### Requirement: LI-003 Owned CPU runtime
Система SHALL управлять собственным CPU runtime, bind только loopback с приватным ключом, bounded context8192 и явным статусом загрузки/готовности/ошибки. Stop/restart/crash владельца SHALL завершать его child runtime; чужие процессы MUST не останавливаться. Raw prompts/ответы/runtime stdout MUST не сохраняться в файлы.
#### Scenario: API shutdown or occupied resources
- **WHEN** API завершён либо запуск не удался
- **THEN** только собственный runtime прекращается или отмечается failed; скрытого GPU/cloud fallback нет

### Requirement: LI-004 CPU inference capacity
Локальная генерация SHALL выполняться по одной с bounded queue и timeout, не изменяя5active steps/session и20remote jobs/fleet. Локальный planner SHALL запрашивать schema-constrained JSON, ограниченный доступными kinds/IDs по текущим opt-ins/protocol/catalog, сохраняя независимую серверную валидацию. Ошибка/перегрузка/невалидный JSON MUST не создавать исполнимый неподтверждённый план. Очередь/вывод SHALL оставаться в RAM.
#### Scenario: Parallel requests
- **WHEN** несколько пользователей запросили модель
- **THEN** одна генерация активна, очередь ограничена, лишний/просроченный запрос получает фиксированный отказ

### Requirement: LI-005 Real local evidence
Проверка SHALL различать verified weights/runtime, реальный CPU inference/JSON/план/подтверждение и fixture evidence. Скорость/качество SHALL сообщаться только для фактически проверенного CPU/контекста. Успешный probe MUST не считаться приёмкой произвольных команд/инцидентов.
#### Scenario: Actual health plan
- **WHEN** реальная локальная модель предложила допустимую health-задачу
- **THEN** исполнение проходит обычные scope/confirmation/postcondition, отчёт содержит реально измеренную длительность без raw output

### Requirement: LI-006 Noninteractive Windows authorization failure
Windows runtime version/start checks SHALL suppress inherited loader error dialogs in an isolated owned process. Recognized policy/signature blocks SHALL return local_runtime_policy_blocked without fallback or security changes. Known blocks SHALL appear in model status/UI without changing provider/model identity or misreporting missing weights. Existing ownership, timeouts and RAM-only output SHALL remain enforced.
#### Scenario: Recognized Windows block
- **WHEN** child creation raises WinError4551/577, or the native child exits with NTSTATUS0xC0E90002/0xC0000428 in signed or unsigned32 form
- **THEN** the bounded check fails with local_runtime_policy_blocked (existing HTTP502); no loader modal acknowledgement, security change or external fallback is required
#### Scenario: Unknown failure
- **WHEN** the native child returns another code, including77, or a different launch error occurs
- **THEN** the existing generic dependency/start failure remains; it is not falsely classified as a policy block
#### Scenario: Worker cancellation and owner death
- **WHEN** probe deadline, API close or owning process death occurs
- **THEN** only the owned worker/runtime tree stops; unrelated processes continue and no raw output/keys are saved
#### Scenario: Known block visible to operator
- **WHEN** local runtime has a known local_runtime_policy_blocked error and an authorized operator reads active model metadata
- **THEN** model status carries that fixed code while provider/model IDs remain unchanged; admin keys, OS messages and policy internals are absent
#### Scenario: Truthful interface after model choice
- **WHEN** administrator selects a local model or a recognized block is returned
- **THEN** UI does not promise working inference before probe success; ModelHub and main section identify Windows authorization block instead of asking to download intact weights

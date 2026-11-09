# knowledge-retrieval Specification

## Purpose
Даёт сотруднику ответы по разрешённым источникам Confluence с актуальными правами и версиями и позволяет специалистам ИТ проверять применимость знаний без накопления исходных журналов.

## Requirements

### Requirement: KR-001 User delegated read through
Поиск/чтение SHALL обращаться только к настроенному Cloud/DC Confluence и пересечению server spaces и scope пользователя. В OIDC персональный credential SHALL проверяться по доверенной связи principal→upstream account; общий PAT MUST не использоваться. Секрет живёт в bounded RAM до logout/TTL.

#### Scenario: Different user or missing binding
- **WHEN** credential принадлежит другому account либо связь не настроена
- **THEN** подключение запрещено и общий PAT не подставляется

### Requirement: KR-002 Fresh source access
Каждая выдача фрагментов/ответа SHALL перепроверять live ACL и версию всех источников. Отзыв, удаление, смена версии или scope MUST закрывать прежний ответ без stale fallback. Перенаправления/внешние ссылки/неразрешённые spaces не дают нового сетевого доступа.

#### Scenario: Revocation after retrieval
- **WHEN** страница стала недоступна или изменена до повторной выдачи ответа
- **THEN** старый ответ удаляется и не возвращается

### Requirement: KR-003 Bounded cited retrieval
Фрагменты, query и свободный ответ SHALL оставаться bounded RAM≤120с. Настроенная модель SHALL возвращать строгий ответ с ID только полученных источников; неизвестные citations запрещены. Без модели SHALL возвращаться найденные источники с честным статусом. Контент MUST не порождать команды/планы.

#### Scenario: Prompt injection or invented citation
- **WHEN** источник требует выполнить команду или модель ссылается на неизвестный ID
- **THEN** нет исполняемого действия; невалидный ответ не выдаётся как доказанный

### Requirement: KR-004 Reviewed metadata lifecycle
Черновик SHALL связывать source ID/version, применимость OS/service/version и при наличии проверенную задачу. Состояния draft→in_review→published→deprecated→archived SHALL требовать текущих ролей/scope и optimistic revision. Публикация SHALL требовать reviewer и перепроверку версии; в OIDC автор не публикует собственный draft.

#### Scenario: Unreviewed or changed source
- **WHEN** автор пытается опубликовать без независимого review либо source version сменилась
- **THEN** переход запрещён, исходная страница не изменяется

### Requirement: KR-005 Metadata only history
Хранилище знаний SHALL сохранять только типизированные ссылки/версии/применимость/авторов/состояния и события; страницы, excerpts, credentials, query и ответы MUST не попадать в БД/логи/backup. Добавление таблицы SHALL сохранять существующие данные/права.

#### Scenario: Raw marker and restart
- **WHEN** страница/ответ содержит исходный marker, затем API перезапущен
- **THEN** marker отсутствует в persistence, volatile ответ недоступен, metadata review сохранён

### Requirement: KR-006 Unified knowledge prompt
Основной раздел SHALL принимать явно обозначенный запрос «Найди инструкцию …» в единственном промпте и показывать источники/временный ответ без подтверждения исполнения. Раздел «Знания» SHALL давать подключение, поиск, применимость и review. Поток действий SHALL сохранять точное подтверждение.

#### Scenario: Knowledge versus execution
- **WHEN** пользователь ищет инструкцию из основного окна
- **THEN** показаны citations, ни одна задача на узле не создаётся

### Requirement: KR-007 Explicit deferred knowledge source
Отключённый Confluence SHALL показываться как deferred в UI/readiness; search/retrieve/connect/review MUST не выполнять upstream запросы даже при сохранённой конфигурации. Возврат коннектора SHALL требовать явного включения. Отложенный источник MUST не блокировать основной поток без знаний.

#### Scenario: Deferred with old credentials configured
- **WHEN** credentials/URL остались, но feature выключен
- **THEN** нет обращения в Confluence/model через knowledge route, UI сообщает об отложенном подключении

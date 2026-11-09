# operator-identity Specification

## Purpose
Аутентифицирует сотрудника через явно выбранный локальный или корпоративный режим и применяет доверенные роли, область устройств и владение задачами без сохранения секретов и обхода точного подтверждения.

## Requirements

### Requirement: OI-001 Explicit verified identity
Система SHALL явно выбирать accounts, legacy-local либо OIDC без fallback. Accounts SHALL проверять локальную УЗ/verifier; OIDC SHALL проверять RS256/issuer/audience/exp/iat/sub/JWKS и связывать code/state/nonce/PKCE. Непроверенные claims/URL/алгоритмы MUST не давать полномочий.

#### Scenario: Forged or mismatched token
- **WHEN** токен имеет неверные подпись/issuer/audience/срок/алгоритм либо callback state/nonce не совпали
- **THEN** доступ закрыт, данные и секреты не отражаются в ошибке

### Requirement: OI-002 Trusted deny by default policy
Роли/scopes SHALL задаваться доверенными DB grants в accounts либо административной policy по подписанному субъекту/группам OIDC, не ролью клиента. Неизвестная/disabled УЗ, повреждённые grants/policy и пустой scope MUST закрывать доступ. Изменения SHALL повторно применяться на API/confirmation/dispatch.

#### Scenario: Revoked grant
- **WHEN** policy отключила сотрудника или убрала устройство
- **THEN** последующие API и выдача его pending задания запрещены

### Requirement: OI-003 Resource and owner isolation
Устройства, задачи, workflow, события и статистика SHALL фильтроваться по роли/scope. Исполнение/решение SHALL требовать operator/admin и владельца. Auditors/admins MUST видеть чужие разрешённые метаданные без чужого volatile кода/вывода/гипотез. Существование недоступного ID не раскрывается.

#### Scenario: Cross operator request
- **WHEN** сотрудник запрашивает чужой request ID, решение или сырой результат
- **THEN** доступ закрыт и чужой payload не возвращается

### Requirement: OI-004 Bound admission and execution
Точный план/request digest SHALL связывать owner ID с целью и действием. Подтверждение и dispatch SHALL повторно проверять действующие роль/scope/срок доступа; потеря доступа останавливает оставшуюся цепочку. Лимит 5 SHALL относиться к сеансу владельца, глобальные 20 сохраняются.

#### Scenario: Permission lost after confirmation
- **WHEN** оператор подтвердил план, затем его scope истёк/отозван
- **THEN** задание не выдаётся агенту и не перехватывается другим оператором

### Requirement: OI-005 Safe audit and compatible migration
История SHALL хранить только opaque owner/actor IDs и разрешённые метаданные, без JWT/session/client secrets/claims payload. Миграция v3→v4 SHALL сохранять узлы/credentials/задачи и явную legacy local ownership; неизвестные схемы закрываются. Старый local owner MUST не получать корпоративные полномочия автоматически.

#### Scenario: Existing local data
- **WHEN** локальный стенд обновлён
- **THEN** прежний токен работает только в local режиме, данные сохранены, в OIDC fallback отсутствует

### Requirement: OI-006 Bounded browser session
UI SHALL показывать выбранный вход и роли, поддерживать выход; accounts/OIDC session SHALL использовать bounded RAM и HttpOnly/SameSite cookie, legacy bearer только RAM. Cookie mutations/login SHALL проверять same-origin. Logout/restart/revocation MUST удалять доступ без восстановления секретов из истории.

#### Scenario: Session expiry or CSRF
- **WHEN** session истекла или cookie mutation пришла с чужого Origin
- **THEN** запрос не исполняется; новый вход не расширяет прежний план

### Requirement: OI-007 Enforceable role catalogue
Система SHALL предоставлять единый каталог/матрицу существующих operator/auditor/reviewer/platform_admin и вычисленные trusted capabilities. Глобальные модели/настройки/УЗ SHALL требовать platform_admin и global device scope; scoped admin сохраняет только разрешённые scoped действия. UI MUST не заменять API enforcement.
#### Scenario: Scoped admin escalation
- **WHEN** администратор с одним device ID запрашивает управление УЗ/глобальными настройками/моделями
- **THEN** API отказывает независимо от клиентского меню/roles поля; scopes/owner других задач не меняются
#### Scenario: Combined roles
- **WHEN** сотруднику назначены несколько разрешённых ролей
- **THEN** права вычисляются из trusted grants с прежними scopes/owner границами, а не из произвольной роли клиента

### Requirement: OI-006 Persisted schedule delegation
Accounts scheduler SHALL брать fresh DB grants для владельца при run/dispatch и проверять серверную lineage consent→run→task. Выход из browser SHALL не отменять согласованное правило; disable/revoke/pause MUST запрещать новые выдачи. Обычная API isolation не ослабляется.

#### Scenario: Logged out owner
- **WHEN** browser session истекла при действительном accounts consent
- **THEN** проверенный static/profile run доступен в пределах fresh grants

#### Scenario: Role revoked
- **WHEN** operator/scope отозваны либо правило выключено
- **THEN** pending dispatch запрещён; cached browser identity не даёт обход

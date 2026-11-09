# local-accounts Specification

## Purpose
Предоставляет сотрудникам внутренние учётные записи PSLC Agent без AD и сохраняет независимые роли, области доступа и проверяемый отзыв сессий.

## Requirements

### Requirement: LA-001 Local bootstrap and password storage
При первом запуске accounts режима система SHALL создать системную УЗ admin/admin только при пустом реестре. В БД SHALL храниться salted scrypt verifier, не пароль. Рестарт MUST не сбрасывать существующие пароли/disable/roles; accounts MUST не принимать прежний operator token или OIDC fallback.

#### Scenario: Restart and wrong credential
- **WHEN** пароль администратора изменён либо введён неверный пароль
- **THEN** рестарт не возвращает default, отказ не раскрывает наличие УЗ/пароль

### Requirement: LA-002 Bounded account session
Локальный вход SHALL выдавать случайную HttpOnly/SameSite cookie session в bounded RAM≤15min; mutations/login/logout SHALL проверять configured same-origin. Пароль/сессия MUST не попадать в события/ошибки/историю. Неудачные попытки SHALL ограничиваться; logout/restart/смена auth revision отзывают сессии.

#### Scenario: Stale session or CSRF
- **WHEN** сессия устарела, пароль/роль изменены либо Origin чужой
- **THEN** доступ закрыт без обхода owner/confirmation gate

### Requirement: LA-003 Admin managed employees
Platform_admin с действующим global device scope SHALL управлять УЗ/ролями/scopes/сбросом пароля. Каждый пользователь SHALL менять собственный пароль с проверкой текущего. Последний enabled global admin MUST не терять роль,global scope или доступ. Password verifier MUST не возвращаться в списке.

#### Scenario: Permission loss
- **WHEN** администратор отключил сотрудника или убрал scope
- **THEN** его API/confirmation/dispatch отозваны; чужие задачи не становятся доступными

#### Scenario: Last global scope
- **WHEN** у последнего global admin пытаются убрать'*' либо отключить/снять роль при наличии только scoped admin
- **THEN** изменение отклоняется без потери управления платформой

### Requirement: LA-004 Compatible local ownership
Миграция5→6 SHALL сохранить узлы/credentials/tasks/history/capabilities. Bootstrap admin SHALL соответствовать прежнему local owner, остальные сотрудники получают отдельные opaque IDs. Корпоративные идентичности MUST не наследоваться автоматически. Backup SHALL содержать только verifiers/typed grants, не исходные пароли.

#### Scenario: Existing local stand
- **WHEN** стенд переходит с token mode на accounts
- **THEN** прежние данные доступны системному admin, старый bearer не работает в accounts

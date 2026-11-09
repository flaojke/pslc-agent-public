# pilot-readiness Specification

## Purpose
Отделяет готовность проверенного кода PSLC Agent от конфигурации, разрешений и фактической приёмки пилота, чтобы запуск сопровождался проверяемыми свидетельствами и не повышал автономность автоматически.

## Requirements

### Requirement: PR-001 Fresh read only preflight
Preflight SHALL проверять DB/schema и A01–A08 с текущим source fingerprint, возрастом≤24h, полным набором успешных сценариев без skipped. Неполный/повреждённый/устаревший отчёт MUST не обозначать code_ready. Preflight MUST не мигрировать БД или запускать задания.

#### Scenario: Changed code or old evidence
- **WHEN** код изменён после eval либо отчёт неполный/старый
- **THEN** готовность кода false с фиксированной причиной и командой повторной проверки

### Requirement: PR-002 External gates stay explicit
CLI/API/UI SHALL различать missing/configured-unverified/declared-unverified external gates и отложенные пользователем возможности. Отключённый Confluence и будущий AD/SSO MUST показываться deferred, не обязательным подключением. Unknown targets SHALL ожидать onboarding. Fixtures/declaration MUST не давать production-допуск.

#### Scenario: All local checks passed
- **WHEN** автоматические проверки прошли, но внешние свидетельства не приняты
- **THEN** code_ready true допускается, pilot_readiness остаётся requires_external_acceptance и production_admitted false

### Requirement: PR-003 Shadow run and target declarations
Pilot declaration SHALL ограничивать5–10 уникальных целей с Windows/Linux и содержать только метаданные ссылок согласований. Shadow интервал SHALL проверяться по timezone/time и минимум14days; декларация MUST оставаться непроверенной и не изменять opt-ins/полномочия/автономность.

#### Scenario: Premature or fabricated duration
- **WHEN** указано менее14days, будущее время либо один лишь интервал
- **THEN** повышение автономности не разрешено, фактическое наблюдение и решение ответственного остаются внешней приёмкой

### Requirement: PR-004 Scoped safe readiness
Readiness API SHALL требовать platform_admin; CLI SHALL читать секреты только из приватной конфигурации и выдавать bounded metadata без URL/JWT/credential/payload. Ошибки SHALL быть фиксированными; отчёт SHALL ссылаться на инструкции оператора/ИБ/восстановления.

#### Scenario: Non admin or bad declaration
- **WHEN** запрос сделал обычный оператор либо declaration повреждён
- **THEN** API отказывает роли, а preflight не принимает повреждённый declaration как допуск

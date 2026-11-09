# pilot-transport Specification

## Purpose
Позволяет администратору явно настроить HTTPS-доступ выбранных пилотных машин к PSLC Agent после внешнего допуска, сохраняя закрытый сетевой режим по умолчанию, персональные права и ограниченные запросы.

## Requirements

### Requirement: PT-001 Explicit admitted pilot configuration
Сетевой пилот SHALL требовать явный trusted flag, ссылки ИТ/ИБ, accounts с HTTPS public origin либо OIDC с HTTPS issuer/callback, TLS cert/key и private CIDR allowlist. Узлы SHALL добавляться через enrollment без обязательного предзаполнения5–10IDs. Неполная конфигурация MUST не запускать non-loopback listener; preflight/model не дают допуска.

#### Scenario: Incomplete network configuration
- **WHEN** выбран non-loopback bind без всех обязательных параметров
- **THEN** приложение отвергает конфигурацию до приёма соединений

### Requirement: PT-002 Transport boundary
Non-loopback запрос SHALL приниматься только при admitted pilot config, HTTPS и source IP allowlist. Forwarded headers MUST не подменять peer/scheme. Accounts/OIDC роли/scopes/ownership/confirmation и node credentials SHALL применяться неизменно. Несогласованный source или plain HTTP MUST получать отказ.

#### Scenario: Spoofed forwarding or public source
- **WHEN** клиент вне allowlist подставил X-Forwarded-For/Proto
- **THEN** доступ закрыт по реальному transport peer и scheme

### Requirement: PT-003 Bounded request admission
HTTP body SHALL ограничиваться256KiB до разбора JSON, включая chunked без Content-Length. Превышение SHALL возвращать фиксированный413 без echo/persistence; listener SHALL иметь ограниченную concurrency. Разрешение сети MUST не расширять capabilities/автономность узлов.

#### Scenario: Oversized streamed body
- **WHEN** поток превысил лимит даже с ложным Content-Length
- **THEN** JSON/модель/узловая операция не запускаются и содержимое не отражается в ошибке

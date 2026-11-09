# agent-delivery Specification

## Purpose
Обеспечивает проверяемую поставку узлового агента, явное доверие транспорту и восстановимый запуск службы с сохранением идентичности и разрешений на выбранных пилотных машинах.

## Requirements

### Requirement: AD-001 Verified transport
Агент SHALL проверять HTTPS chain/hostname с системным либо явно настроенным CA; HTTP допустим только loopback. Ошибка TLS MUST не включать insecure fallback. Уникальный credential узла и отзыв SHALL действовать поверх транспорта. TLS endpoint API SHALL оставаться явно локальным до отдельного сетевого допуска.

#### Scenario: Untrusted or wrong hostname
- **WHEN** сертификат не доверен, просрочен либо имя не совпало
- **THEN** enrollment/poll/result не передаются по небезопасному каналу

### Requirement: AD-002 Signed bounded distribution
Поставка SHALL иметь подписанный manifest с версией/платформой/SHA256/размером каждого файла и явным доверенным Ed25519 ключом. Неверная подпись, extra/missing/изменённый файл, traversal/symlink и превышение bounds MUST блокировать staging до активации. QA ключ MUST отличаться от корпоративного доверия.

#### Scenario: Tampered release
- **WHEN** бинарный файл или manifest изменён либо ключ не доверен
- **THEN** поставка отвергнута, текущая версия сохранена

### Requirement: AD-003 Stopped explicit activation
Обновление SHALL требовать остановленного/неисполняющего агента и точного ожидаемого digest поставки. Версия SHALL переключаться без записи state/config/credentials и без расширения capabilities. Downgrade MUST отвергаться. Прежняя версия сохраняется для явного восстановления; неизвестный эффект задачи не повторяется автоматически.

#### Scenario: Running agent or wrong digest
- **WHEN** идёт исполнение либо ожидаемый digest не совпал
- **THEN** обновление не активируется и identity не меняется

### Requirement: AD-004 Service lifecycle and configuration
Агент SHALL иметь нативный service entrypoint Windows и systemd unit Linux, отдельные state/config и opt-ins по умолчанию false. Stop SHALL прекратить новые poll, дождаться текущего ограниченного исполнения и не сохранять raw pending result. Одновременный запуск с одной identity MUST блокироваться.

#### Scenario: Restart with existing state
- **WHEN** служба перезапущена с тем же config/state
- **THEN** ID/права сохранены, raw output не восстанавливается, неизвестный исход сверяется отдельно

### Requirement: AD-005 Honest native builds
Windows/Linux SHALL собираться на соответствующей ОС с фиксированными зависимостями и inventory лицензий. Отчёт SHALL различать сборку/контейнер/проверку службы и реальную пилотную ОС. Отсутствующая PKI/подпись организации MUST не обозначаться пройденной приёмкой.

#### Scenario: Container build
- **WHEN** Linux binary проверен в контейнере без systemd PID1
- **THEN** отчёт указывает эту среду и не объявляет установленную службу Linux проверенной

### Requirement: AD-006 Complete installation export
Администратор SHALL скачивать полный установочный ZIP выбранной Windows/Linux платформы: проверенный native runtime, config, инструкции, лицензии и SHA256 состава. Credential существующего узла и открытый enrollment code MUST не включаться. Истёкший/чужой профиль или повреждённая native-поставка SHALL блокировать экспорт. QA доверие SHALL обозначаться явно.
#### Scenario: Export and enroll
- **WHEN** создан профиль и скачан ZIP
- **THEN** его можно распаковать на соответствующей ОС, проверить целостность и зарегистрировать отдельным одноразовым кодом; Python на узле не требуется
#### Scenario: Tampered native package
- **WHEN** изменён файл native-поставки до экспорта
- **THEN** ZIP не выдаётся и чужой либо текущий рабочий агент не изменяется

### Requirement: AD-007 Recovery capable native protocol
Native0.8/protocol1 SHALL исполнять проверяемую anti-program со своим protected snapshot. Credential SHALL проверяться до приёма observed protocol. Protocol0 MUST не получать recovery-dependent мутацию/rollback, сохраняя health/поддержанную read-only диагностику. Экспорт/обновление SHALL сохранять identity/локальные opt-ins.
#### Scenario: Old agent polls
- **WHEN** прежний агент без protocol1 делает poll
- **THEN** мутация/rollback не выдаётся, UI обозначает необходимость обновления
#### Scenario: Native package upgrade
- **WHEN** проверенная поставка0.8 распакована/активирована на выбранном узле
- **THEN** identity/допуски не расширяются, Windows/Linux proof различает standalone/container/service acceptance

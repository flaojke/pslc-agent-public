# application-distribution Specification

## Purpose
Проверяемая поставка одного PSLC Agent с заранее загруженными весами или загрузкой после развёртывания без переноса рабочих данных.

## Requirements

### Requirement: DS-001 Equivalent Full and Lite
Релиз SHALL содержать Full с verified Qwen4B и Lite без весов с кнопкой загрузки, с одинаковым кодом/UI/CPU runtime и версиями. Оба SHALL включать manifest/digests/licenses/install/recovery инструкции, без keys/DB/accounts/local identity. Full SHALL запускаться без повторного скачивания весов.
#### Scenario: Clean installations
- **WHEN** распакован Full либо Lite
- **THEN** Full обнаруживает verified модель, Lite показывает download; после загрузки функциональный контракт одинаков

### Requirement: DS-002 Verifiable segmented downloads
Файл больше GitHub asset limit SHALL поставляться частями с digest каждой части и полной поставки, стандартным assemble/verifier и понятной инструкцией. Сборка MUST отклонять missing/изменённую часть до активации. Веса MUST не попадать в git history.
#### Scenario: Damaged part
- **WHEN** часть Full отсутствует либо изменена
- **THEN** сборка отклоняется, готовая установка не заменяется

### Requirement: DS-003 Authorized GitHub publication
Публикация SHALL следовать выбранной пользователем структуре GitHub и содержать ссылки на оба варианта, source tag и SHA256. Недоступные permissions/auth/upload MUST отмечаться честно; локальный artifact MUST не выдаваться за опубликованный. Публикация требует разрешения владельца репозитория.
#### Scenario: Publication unavailable
- **WHEN** отсутствует доступ GitHub
- **THEN** готовые проверенные artifacts доступны локально, конкретный внешний блокер сообщён без потери реализации

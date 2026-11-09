# improvement-feedback Specification

## Purpose
Связывает результат эксплуатации с последующим AI PDLC/SDD изменением через проверяемую заготовку OpenSpec, сохраняя ручную проверку специалистом и запрет хранения исходного контекста расследования.

## Requirements

### Requirement: IF-001 Metadata only improvement handoff
Владелец-оператор SHALL получать заготовку предложения OpenSpec по своему завершённому заданию с ID/типом/состоянием/хешем плана и проверяемыми следующими шагами. Сырой вывод/код/гипотеза/model context MUST не входить в заготовку. Неизвестный исход и проверенный сбор MUST не объявляться доказанной причиной.

#### Scenario: Export after investigation or unknown effect
- **WHEN** специалист выгружает заготовку
- **THEN** она сохраняет явную неопределённость, метаданные результата и необходимость ручной проверки

### Requirement: IF-002 Human reviewed lifecycle boundary
Экспорт SHALL быть read-only по отношению к системе/Confluence/repository и доступен в основном окне. Он MUST не создавать автоматически изменение/PR, не публиковать знание и не выполнять remediation. Новое поведение SHALL проходить обычный proposal/spec/design/tasks/verification цикл.

#### Scenario: Foreign or active result
- **WHEN** запросили чужую либо ещё активную задачу
- **THEN** экспорт закрыт; никакое действие не выполняется и не подтверждается автоматически

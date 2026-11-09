"""A reviewed SDD handoff, never autonomous self-modification."""
from sqlalchemy import select
from .store import TERMINAL,Problem,tasks

def export(ops,tid):
 ops.execute()
 with ops.store.tx() as c:task=c.execute(select(tasks).where(tasks.c.id==tid)).mappings().first()
 if task is None or task['owner_id']!=ops.p.id or not ops.p.device(task['device_id']):raise Problem('not_found',404)
 if task['state'] not in TERMINAL and task['state']!='outcome_unknown':raise Problem('terminal_result_required',409)
 # Only fixed metadata, never a plan payload/summary/model field.
 text=f"""# Proposal: review operational result

## Why
Проверить результат задачи `{task['id']}` перед предложением изменения поведения.
Тип: `{task['kind']}`. Статус: `{task['state']}`. Хеш плана: `{task['plan_hash']}`.
Статус verified у расследования означает завершённый сбор, а не доказанную причину.
Неизвестный исход требует внешней сверки; успешный процесс сам не доказывает устранение инцидента.

## What Changes
- Специалист должен описать воспроизводимый случай и отделить наблюдения от гипотез.
- Уточнить ожидаемое поведение, критерий успеха и допустимый стенд.
- Не переносить исходные журналы, вывод, секреты или код из оперативного контекста.

## Capabilities
### New Capabilities
- Определить после ручного review действующих specs.
### Modified Capabilities
- Указать реальные requirement IDs; не считать эту заготовку готовым изменением.

## Impact
Требуется обычный цикл OpenSpec proposal → delta specs → design → tasks → implementation → verification.
Нужны регрессия/сценарный eval, проверка retention и независимая проверка гипотез.
Эта выгрузка не создала change/PR, не изменила Confluence и не выполнила remediation.
"""
 return {'filename':'pslc-improvement-'+task['id']+'.md','markdown':text,'review_required':True,'repository_changed':False,'remote_execution':False}

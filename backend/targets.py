"""Explicit, authorization-filtered fleet scope; no OS-to-role inference."""
import re
from typing import Literal
from pydantic import Field
from .schemas import Strict
from .store import Problem
from .planner import ActionPlan,HealthStep,Clarification,PlanningError,contains_reference,Step

class TargetScope(Strict):
    kind:Literal['none','all','demo','workstations','servers','manual']='none'
    ids:list[str]=Field(default_factory=list,max_length=500)

class ExpandedPlan(ActionPlan):
    steps:list[Step]=Field(min_length=1,max_length=2500)

def resolve_scope(scope,inventory):
    by_id={d['id']:d for d in inventory if not d['revoked']}
    if scope.kind!='manual' and scope.ids:raise Problem('scope_ids_require_manual',422)
    if len(scope.ids)!=len(set(scope.ids)):raise Problem('duplicate_scope_target',422)
    if scope.kind=='manual':
        if not scope.ids:return []
        if any(did not in by_id for did in scope.ids):raise Problem('target_unavailable',404)
        return [by_id[did] for did in scope.ids]
    if scope.kind=='none':return []
    selected=list(by_id.values())
    if scope.kind=='demo':selected=[d for d in selected if d.get('virtual')]
    elif scope.kind in ('workstations','servers'):
        kind='workstation' if scope.kind=='workstations' else 'server'
        selected=[d for d in selected if not d.get('virtual') and d.get('device_kind','unknown')==kind]
    return sorted(selected,key=lambda d:(d['name'],d['id']))

class FleetPlanner:
    def __init__(self,planner,known_inventory):
        self.planner=planner;self.known_inventory=known_inventory
    def propose(self,question,inventory,packages):
        if not inventory:return Clarification(status='clarification',question='Выберите устройства или группу машин для плана.'),'group_rules'
        selected={d['id'] for d in inventory}
        for d in self.known_inventory:
            if d['id'] not in selected and (contains_reference(question,d['id']) or contains_reference(question,d['name'])):
                return Clarification(status='clarification',question='В запросе назван узел вне выбранных целей. Измените выбор машин или уточните запрос.'),'group_rules'
        # This is a declared deterministic health profile, never a fabricated LLM answer.
        q=question.strip()
        for d in inventory:q=re.sub(re.escape(d['id']), '', q,flags=re.I);q=re.sub(re.escape(d['name']),'',q,flags=re.I)
        health=re.fullmatch(r'(?:проверь|проверить|покажи|показать)\s+(?:состояние|статус)(?:\s+(?:выбранных|всех|узла|узлов|устройства|устройств|машин|агентов|пк|парка|на|в))*[\s.!?]*',q,re.I)
        if health:return ExpandedPlan(status='plan',steps=[HealthStep(kind='health',device_id=d['id']) for d in inventory]),'approved_template'
        steps=[];sources=[]
        for os_family in ('Windows','Linux'):
            peers=[d for d in inventory if d['os_family']==os_family]
            if not peers:continue
            rep=peers[0]
            request=question+'\nЦель выбрана оператором в интерфейсе: '+rep['id']+'. Подготовь шаги для этой ОС; сервер применит точный шаблон к выбранной группе.'
            result,source=self.planner.propose(request,[rep],packages)
            if not isinstance(result,ActionPlan):return result,source
            if any(s.device_id!=rep['id'] for s in result.steps):raise PlanningError('unknown_plan_target')
            for peer in peers:
                steps.extend(s.model_copy(update={'device_id':peer['id']}) for s in result.steps)
            sources.append(source)
        if not steps or len(steps)>2500:raise PlanningError('fleet_plan_too_large')
        return ExpandedPlan(status='plan',steps=steps),'group_model' if 'configured_model' in sources else 'group_rules'

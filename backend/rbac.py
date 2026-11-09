"""Existing role IDs and one effective-capability catalogue. No client-assigned privileges."""
ROLES=(
 {'id':'operator','name':'Оператор ИТ','description':'Ведёт свои разборы и подтверждает действия на разрешённых устройствах.'},
 {'id':'auditor','name':'Аудитор ИБ','description':'Читает разрешённые метаданные и события без чужого кода, вывода и диалога.'},
 {'id':'reviewer','name':'Ревью знаний','description':'Проверяет знания при доступном коннекторе и ACL пространства; Confluence пока отложен.'},
 {'id':'platform_admin','name':'Администратор платформы','description':'Управляет разрешёнными узлами; глобальные настройки и УЗ доступны только с областью всех устройств.'},
)
ALL=tuple(r['id'] for r in ROLES)
CAPS=(
 ('read_inventory','Просмотр парка',ALL,False,False,'Только назначенные устройства.'),
 ('operate','Диалоги и собственные планы',('operator','platform_admin'),False,False,'Свои задачи, текущие device scopes и точное подтверждение.'),
 ('audit_metadata','Аудит чужих задач',('auditor','platform_admin'),False,False,'Метаданные назначенных устройств; чужой временный контекст закрыт.'),
 ('manage_devices','Настройки отдельных устройств',('platform_admin',),False,False,'Только назначенные устройства.'),
 ('enroll_devices','Подключение новых агентов',('platform_admin',),True,False,'Нужна глобальная область устройств.'),
 ('discover_network','Сканирование сети',('platform_admin',),True,False,'Глобальная область, явный private CIDR, без автоустановки.'),
 ('manage_models','Подключение и переключение LLM',('platform_admin',),True,False,'Глобальная область; ключи остаются на сервере.'),
 ('configure_system','Изменение системных настроек',('platform_admin',),True,False,'Глобальная область; лимиты и транспорт не редактируются этой формой.'),
 ('manage_users','Управление учётными записями',('platform_admin',),True,True,'Accounts режим; защищён последний глобальный администратор.'),
 ('create_knowledge_draft','Черновик знаний',('operator','platform_admin'),False,False,'Нужны доступный коннектор и разрешённое пространство.'),
 ('review_knowledge','Ревью и публикация знаний',('reviewer','platform_admin'),False,False,'Space ACL и workflow review; Confluence по текущему решению отложен.'),
 ('read_guides','Сведения и руководства',ALL,False,False,'Аутентифицированный сотрудник; чтение не запускает действия.'),
 ('change_own_password','Смена своего пароля',ALL,False,True,'Accounts режим, проверка текущего пароля.'),
 ('manage_platform','Глобальное администрирование',('platform_admin',),True,False,'Роль администратора и область всех устройств.'),
)
def role_permits(roles,capability):
 return any(cap[0]==capability and set(roles).intersection(cap[2]) for cap in CAPS)
def capabilities(principal,auth_mode='accounts'):
 global_scope='*' in principal.device_ids
 return {key:bool(set(principal.roles).intersection(roles)) and (not global_required or global_scope) and (not accounts_only or auth_mode=='accounts')
  for key,_,roles,global_required,accounts_only,_ in CAPS}
def catalogue(principal=None,auth_mode='accounts'):
 rows=[]
 for key,title,roles,global_required,accounts_only,note in CAPS:
  rows.append({'id':key,'label':title,'note':note,'grants':{r['id']:('Глобальная область' if global_required else 'Accounts режим' if accounts_only else 'Да') if r['id'] in roles else 'Нет' for r in ROLES}})
 result={'schema_version':1,'roles':[dict(r) for r in ROLES],'capabilities':rows,'presets':[{'id':r['id'],'label':r['name'],'roles':[r['id']],'requires_global_scope':r['id']=='platform_admin'} for r in ROLES]}
 if principal:result['effective']=capabilities(principal,auth_mode)
 return result
def require_global(ops,capability='manage_platform'):
 from .store import Problem
 p=ops.auth.known(ops.p.id)
 if not capabilities(p,ops.auth.s.auth_mode).get(capability,False):raise Problem('operator_forbidden',403)
 return p

"""Truthful local runtime facts; no probes, URLs, DSNs or environment dump."""
from .product_guides import variables
from .store import SCHEMA_VERSION
from .rbac import capabilities

def info(settings,store,principal,api_version=None):
 v=variables()
 result={'name':'PSLC Agent','version':api_version or v['version'],'native_agent_version':v['agent_version'],'schema_version':SCHEMA_VERSION,
  'mode':'pilot-network' if settings.pilot_network_admitted else 'local-development',
  'purpose':'Поддержка и администрирование Windows/Linux с подтверждением действий и проверкой результата.',
  'architecture':['Web-интерфейс и диалоги','API, политики и очередь','Локальный CPU LLM или OpenWebUI','Узловой Windows/Linux native агент','SQLite/PostgreSQL: только метаданные'],
  'limits':{'session':5,'fleet':20,'devices':500,'api_processes':1},
  'features':[
   {'name':'Беседы и планы с подтверждением','status':'implemented','detail':'Чужой временный контекст закрыт; ответ модели требует проверки.'},
   {'name':'Управление Windows/Linux','status':'implemented','detail':'Возможности узла отдельно включаются локально и на сервере.'},
   {'name':'Поиск сети и ZIP агента','status':'implemented','detail':'Явный private IPv4 CIDR; кандидат требует enrollment.'},
   {'name':'CPU Qwen и OpenWebUI','status':'implemented','detail':'Qwen4B CPU и чистые Full/Lite проверены: JSON, план, подтверждение, native health. Корпоративный OpenWebUI и качество произвольных задач требуют внешней приёмки.'},
   {'name':'Откат задачи','status':'implemented','detail':'Пара действий, локальный зашифрованный baseline и ручное восстановление объявленных ресурсов. Windows standalone / Linux container проверены; службы требуют внешней приёмки.'},
   {'name':'Планировщик','status':'implemented','detail':'Accounts-only календари, согласие на точный шаблон, fresh scopes, история без catch-up. Промпты требуют нового решения по плану.'},
   {'name':'AD/SSO и Confluence','status':'deferred','detail':'Отложены пользователем, не блокируют локальные УЗ.'},
   {'name':'Production и корпоративная сеть','status':'pending_external','detail':'PKI/ИТ/ИБ, выбранные узлы и реальный shadow-пилот не заменяются unit-тестами.'}],
  'data_policy':'Исходные журналы, полный вывод и диалог — ограниченная RAM. Явно сохранённые шаблоны расписаний зашифрованы отдельно; в БД метаданные и краткие исходы.'}
 if capabilities(principal,settings.auth_mode)['manage_platform']:
  result['administration']={'database_engine':store.engine.dialect.name,'auth_mode':settings.auth_mode,
   'tls_configured':bool(settings.tls_cert_file and settings.tls_key_file),'network_admitted':settings.pilot_network_admitted,
   'local_models_enabled':settings.local_models_enabled,'knowledge_enabled':settings.knowledge_enabled}
 return result

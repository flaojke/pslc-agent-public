# Настройка модели в системе
Доступ: platform_admin с правами на все узлы. В новой пустой базе создаётся admin/admin; пароль не сбрасывается при обновлении. Основная страница остаётся одним промптом.
Откройте «Настройка модели». Введите адрес сервера и API key, загрузите список моделей, выберите точный ID, выполните проверку, сохраните. Допустим адрес сайта или base с /api; /api добавляется автоматически. HTTP разрешён только loopback, иначе HTTPS с доверенной цепочкой. Внутренний CA задаётся на сервере PSLC_MODEL_CA_FILE; отключения TLS-проверки нет.
Сохранение разрешено после получения актуального списка (5минут), даже если JSON probe не прошёл: статус остаётся configured_unverified. Для планирования нужна модель, соблюдающая JSON-контракт; проверяйте её качество отдельно. Результат probe сбрасывается при правке draft. Проверка не запускает node/actions/tools.
Ключ после ввода/запроса скрывается из поля, остаётся только в RAM формы до сохранения/ухода. Сохранённый ключ никогда не возвращается API. Пустое поле повторно использует server key только для неизменённого нормализованного адреса. Новый адрес требует новый ввод key.
Защищённый файл по умолчанию .local/model/connection.enc, путь можно задать PSLC_MODEL_CONFIG. Windows DPAPI CurrentUser, directory ACL owner/SYSTEM; POSIX AES-GCM,0700directory/0600master.key. Он исключён из release/metadata backup. При смене identity/машины потребуется key заново. Ошибка чтения выключает модель без env fallback.
Настройка UI имеет явный приоритет над PSLC_MODEL_URL/ID/TOKEN. «Отключить» удаляет key из активной конфигурации, сохраняет зашифрованный disabled marker, не возвращаясь к env fallback. Старый уже начатый запрос может завершиться со своим snapshot. Новые запросы chat/planner/investigation/RAG используют новую конфигурацию без рестарта.
## API
Все /api/settings/model endpoints требуют текущий global admin; mutation — exact Origin.
- GET /api/settings/model — URL,ID,has_key,revision,status/source; без key.
- POST /api/settings/model/models — candidate server_url/api_key/model_id/revision, список безопасных IDs.
- POST /api/settings/model/check — тот же candidate, выбранная модель обязательна, JSON probe и real_task_quality_verified=false.
- PUT /api/settings/model — candidate; актуальная revision и полученный список обязательны.
- DELETE /api/settings/model — revision; отключение.
Ключ/сырой upstream output не сохраняются в DB/отчётах/телеметрии. Ошибки фиксированные:401/403upstream→openwebui_auth_failed; redirects/timeout/response bounds отдельно. Сохранение атомарно, параллельный stale save→409. Неудачный probe/secret write не меняет прежний snapshot.
## Параметры для ИИ-команды
См. OPENWEBUI_CONNECTION_GUIDE.md: укажите URL, разрешённые model IDs и доступы своей установки. Сетевые обращения выполняет сервер PSLC Agent. Dedicated Open WebUI key пользователя, доступ к /api/models и /api/chat/completions, plain model без привязанных Tools/Functions; retention провайдера согласуется отдельно.

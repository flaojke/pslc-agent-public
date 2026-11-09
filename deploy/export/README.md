# Установка PSLC Agent на выбранную машину

Архив подготовлен для одного профиля. Одноразовый код на 10 минут показан отдельно в интерфейсе: его нет в архиве. После истечения подготовьте новый профиль. Credentials и identity существующих машин не включены.

Скачайте по доверенному каналу, сверяйте SHA256 архива из ответа сервера. Распакуйте в отдельную постоянную папку без дополнительных файлов. SHA256SUMS проверяет состав; это не корпоративная подпись. Native manifest и Ed25519 подпись проверены сервером; публичный ключ включён для независимой сверки.

Windows: выполните .\verify.ps1, затем .\start.ps1 -Enroll; код вводится скрыто. Дальше .\start.ps1 без -Enroll. Не обходите корпоративную ExecutionPolicy. После проверки можно отдельно запустить .\native\pslc-agent.exe --config .\config.json --enroll.

Linux: chmod +x start.sh native/pslc-agent; ./start.sh --enroll, затем ./start.sh. Python не нужен. Подготовьте права на state-директорию и CA для выбранной УЗ. Identity хранится только в указанном state, с правами владельца.

Удалённым узлам нужен доступный HTTPS server в config.json. 127.0.0.1 работает только на машине приложения. CA предоставьте отдельно в точном ca_file; insecure fallback нет. Проверьте server и разрешения в config перед запуском.

Распаковка и экспорт ничего не устанавливают и не добавляют компьютер в парк; managed статус появляется после enrollment.

Служба — отдельное действие администратора после enrollment и проверки прав. Windows: разместите config в C:/ProgramData/PSLC-Agent/agent-config.json; install-windows-service.ps1 -AgentExe <absolute native exe> создаёт остановленную службу LocalService. Доступ к config/state/CA выдайте этой УЗ отдельно. Linux: pslc-agent.service — шаблон с выделенной УЗ; проверьте абсолютные ExecStart/config/state и ReadWritePaths перед явным systemctl enable/start. Реальная SCM/systemd приёмка и корпоративная PKI не заявляются.

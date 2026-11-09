# Идентичность и права — 0.5.0

Актуальный scope0.10: этот коннектор отложен. Локальные УЗ описаны в LOCAL_ACCOUNTS.md; Confluence/AD не требуются для текущей работы. Содержимое ниже — сохранённая техническая инструкция опционального режима, не факт текущего подключения.


Режим выбирается явно: PSLC_AUTH_MODE=local или oidc. Local допускает только прежний локальный токен; OIDC его не принимает. API остаётся loopback development; корпоративная публикация пока не разрешена.

OIDC: PSLC_OIDC_ISSUER, PSLC_OIDC_CLIENT_ID, PSLC_OIDC_API_AUDIENCE, PSLC_OIDC_REDIRECT_URI (полный /auth/callback), PSLC_AUTH_POLICY (абсолютный путь). Необязательный PSLC_OIDC_CLIENT_SECRET передаётся только локальной конфигурацией. HTTPS обязателен, кроме loopback. Discovery/JWKS/token/authorize должны иметь origin настроенного issuer. Поддержаны RS256 RSA≥2048, Code + PKCE S256, state/nonce, API JWT с отдельной audience. Срок доступа ограничен 15 минутами; introspection не реализована. Настоящий IdP требует отдельной приёмки.

Policy schema 1: subjects индексируются SHA256(issuer + NUL + sub); groups — точными подписанными группами. Grant содержит roles, device_ids, knowledge_spaces, disabled. Роли: operator исполняет свои задачи, auditor видит разрешённые метаданные, reviewer нужен для review знаний, platform_admin управляет enrollment/отзывом. '*' — все устройства/пространства. Разрешения subject и groups складываются; любой disabled запрещает доступ. Пустая, неизвестная или повреждённая policy закрывает доступ. Ограничивающая subject-запись не сужает широкий group grant — удаляйте широкий grant. Политика перечитывается на API/confirmation/dispatch/background обработке.

Сессии, JWT и CSRF state находятся только в ограниченной RAM. Cookie HttpOnly/SameSite, Secure на HTTPS, mutations требуют точный Origin. Logout/перезапуск уничтожают сессии. UI очищает пользовательские данные при выходе. История хранит opaque owner/actor IDs, а не claims/секреты. Чужой код, вывод и гипотезы недоступны даже auditor/admin. Администратор не подтверждает чужие планы.

Schema v4 добавляет owner_id/actor_id, сохраняя credentials/историю. Прежние записи принадлежат local-development; корпоративный вход не наследует их полномочия. Pending задания потерявшего доступ владельца блокируются. 5 шагов считаются по owner+session, 20 — по парку. Один процесс API: RAM-сессии не рассчитаны на горизонтальное масштабирование.


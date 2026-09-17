# Бэклог profi-worker

> Обновлён 16.09. Текущий основной flow: fast-path — свежий заказ
> открывается один раз, full gates + LLM/fallback + отправка идут в той же
> вкладке. На Windows один supervisor на аккаунт держит живыми Chrome + worker.

## Сделано 16.09 (инциденты дня: 3 ребута без автостарта, чаты молчали)

- [x] **Автостарт после входа в Windows**: Task Scheduler `ProfiWorkerAutostart`
  → `scripts/ops/autostart-win.ps1` → start-win по всем `accounts/*.env`
  (16 ч простоя из-за отсутствия автостарта).
- [x] **Системные строки больше не глушат чаты**: после клиентского вопроса
  площадка вставляет «Робот: Сообщите…» — вопрос навсегда выпадал из
  таргетинга (кейсы «Надежда» 25,5 ч, «Николай»). Теперь `_classify_chat_dialog`
  отдаёт системные диалоги в инспекцию: открытый диалог парсится
  (`parse_dialog_texts`), придавленный вопрос клиента логируется и получает
  ответ; окно для придавленных — 48 ч (`PROFI_CHAT_BURIED_WINDOW_H`),
  перепроверка «уже ответили» — раз в 30 мин (`PROFI_CHAT_INSPECT_INTERVAL_MIN`).
- [x] **Наблюдаемость чатов**: таблица `chat_seen` — состояние каждого видимого
  диалога на каждом чат-чеке (было: живых 19, в БД 6); сводка
  «диалогов/целей/inspect/system» в worker-лог.
- [x] **LLM-ретрай при битом JSON**: чаты теперь повторяют той же моделью с
  большим `max_tokens`, затем фолбэк (инциденты 15–16.09 «Unterminated
  string»); окончательный провал пишет `LLM_JSON_ERROR` в chat_log и
  ретраится, а не тихо скипается.

## Сделано 03–04.09

- [x] **Fast-path**: `feed → order → details → full gates → LLM/fallback → send`
  в одной открытой карточке; старый autopilot при `PROFI_FAST_PATH=1` no-op.
- [x] **Profile fallback при проблеме LLM**: acquisition не останавливается из-за
  LLM cooldown/лимита; безопасный шаблон выбирается из business-profile после
  детерминированных гейтов. Нормальный LLM verdict `skip` fallback не переопределяет.
- [x] **Защита от дублей**: atomic `draft=generating`, `send=sending`; terminal
  `sent/unknown/skipped/failed`, фонового reopen/retry свежего заказа нет.
- [x] **Два ключа z.ai**: свой ключ на аккаунт + взаимный fallback провайдера.
- [x] **`PROFI_CHROME_NO_LAUNCH=1` у worker**: Python business-process сам Chrome
  не стартует. Владение браузером вынесено наружу, чтобы не повторять инцидент
  с захватом чужого/занятого профиля.
- [x] **referer при прямой навигации** на заказ (`orders.py`): устранил 45-с
  tarpit/timeout на `n.php?o=…`.
- [x] **Windows Chrome supervisor**: `scripts/account/supervise-win.ps1` проверяет
  `/json/version`, поднимает exact profile+CDP, перезапускает managed Chrome при
  устойчивом dead-CDP и fail-closed ждёт, если профиль занят foreign Chrome.
- [x] **Windows worker supervisor**: тот же процесс перезапускает worker при падении;
  `start-win.ps1` идемпотентен, `stop-win.ps1 -Account <acc>` останавливает owner
  перед children, чтобы не было respawn-race. Chrome при stop намеренно остаётся.
- [x] **Windows rollback compatibility**: отдельный 120-с autopilot-loop запускается
  только при явном `PROFI_FAST_PATH=0`.
- [x] **Безопасный hardening чатов без DOM-переделки**: бот не дописывает поверх
  ручного draft, ошибки чтения поля после Enter теперь fail-closed; chat-style не
  придумывает свободные временные окна и не использует `needs_human`; legacy
  `chat_cron.sh` поднимает Chrome только при явном `cdp_dead`.

## Бэклог

### 1. Наблюдаемость fast-path на реальном трафике
После выката сравнить с до-fast-path периодом:
- time-to-send;
- `OPEN_FAIL`, `tab_hygiene`, `BROWSER_OFFLINE`;
- `sent / unknown / failed`;
- `draft_source=llm|fallback`;
- конверсию LLM vs fallback и по профилям.

### 2. Windows supervisor: эксплуатационная проверка
Unit/static tests закрывают policy и PowerShell parsing, но после merge нужен
реальный smoke на Windows:
- запустить `start-win -Account <acc>` дважды → один supervisor;
- закрыть managed Chrome → тот же profile+port поднимается автоматически;
- убить worker → worker возвращается;
- вручную открыть тот же profile без ожидаемого CDP → supervisor не должен его убить,
  только `PROFILE_IN_USE_NO_CDP` в `logs/supervisor-<acc>.log`.

### 3. thinking-токены glm-5.3-flash
Модель по anthropic-протоколу отдаёт `thinking` + `text`; размышление ест
`max_tokens`. При росте `LLM/JSON` ошибок проверить обрезание ответа/лимиты.

### 4. Ротация ключей и лимиты
При упоре обоих аккаунтов в лимит добавить третий ключ или перераспределить
модели/квоты. Ключи держать только в локальных `.env`/`accounts/*.env`, не в Git.

### 5. Детектор скрытых заказов
Если карточка загрузилась, но нет ни тарифов, ни CTA, возможно заказ уже скрыт,
даже когда в body нет явного текста «Заказ скрыт». Нужна структурная эвристика,
чтобы считать это clean skip, а не send failure.

### 6. Гигиена репо/процессов
- следить, чтобы в публичный Git не попадали реальные account env, профили браузера,
  cookies, ключи, сырые чаты/заказы;
- старые Mac/VPS legacy-autopilot entrypoints со временем можно удалить после
  подтверждения, что fast-path стабилен в production.

### 7. Чаты: reliability / корректная модель сообщений

Сейчас chat-auto рабочий на happy path, но источником истины остаются sidebar/ARIA
и `unread`, а в LLM передаётся `body.inner_text()[-4000:]`. До полностью
автономного режима нужно заменить это на устойчивую message-state модель.

- [ ] **Stable dialog identity вместо имени.** `list_dialogs()` сейчас оставляет
  только первое слово имени, а `open_dialog_by_name(...).first` может открыть не
  тот диалог при двух Аннах/Александрах. Нужно до клика получать href/order-id или
  другой стабильный идентификатор и после открытия проверять expected id.
- [ ] **Durable incoming-message state.** Открытие unread-диалога может само снять
  unread. Если после этого упали LLM/JSON/postcheck/send, клиентское сообщение
  способно навсегда исчезнуть из `targets`. Нужны `conversation_id +
  last_client_message_id/fingerprint` и состояния `pending/generating/sending/
  sent/unknown`, не UI-бейдж как очередь.
- [ ] **Отмечать reply независимо от успеха автоответа.** `first_client_reply_at`
  должен фиксироваться в момент надёжного наблюдения нового клиентского сообщения,
  а не как побочный эффект успешного `log_chat(tutor/system)`. Иначе A/B/C reply
  rate смещён в сторону сообщений, которые агент сумел обработать.
- [ ] **Передавать в LLM только активный диалог.** Сейчас `read_dialog_text()` берёт
  весь `body`, куда могут попасть превью соседних клиентов и служебный UI. Нужен
  контейнер текущей переписки или структурированный список последних сообщений
  `{sender,text}`. Перед внедрением нужен живой DOM/ARIA smoke на текущем Profi.
- [ ] **Повторно проверять turn ownership перед send.** Между sidebar-проверкой
  `last_is_ours=false` и Enter владелец может вручную ответить. Перед необратимой
  отправкой нужно сверять, что последнее клиентское message-id/fingerprint не
  изменилось и нового tutor message нет.
- [ ] **Настоящий delivery proof.** Текущее опустевшее поле — только слабый сигнал.
  После отправки нужно дождаться нового outgoing bubble/message-id с нашим текстом;
  невозможность доказать исход → `unknown`, а не `sent` и не автоматический retry.
- [ ] **Retry после transient failure.** LLM timeout/invalid JSON/send failure не
  должны терять уже прочитанное сообщение. Retry должен идти из durable queue,
  даже если sidebar `unread=0` или legacy `changed=false`.
- [ ] **Не связывать SLA чатов только с успешным feed cycle.** Сейчас встроенный
  `run_chat_auto()` вызывается каждый N-й `state == OK`; длинный/ошибочный feed
  откладывает ответ уже заинтересованному клиенту. При переходе к единому browser
  owner нужен независимый `chat_due` внутри того же runtime.
- [ ] **Убрать legacy chat cron после smoke integrated path.** Пока он нужен для
  rollback, но отдельный `changed` trigger имеет другие retry semantics и может
  расходиться с worker. После подтверждения Windows/Mac integrated chat оставить
  один authoritative scheduler.
- [ ] **Набор интеграционных regression-тестов:** одинаковые имена; client message
  стал read до LLM-error; ручной draft; owner ответил во время LLM; поле очистилось,
  но outgoing bubble не появился; opened order-id mismatch; два/три unread и
  ошибки первых диалогов; message-fingerprint/idempotency.

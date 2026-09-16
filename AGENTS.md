# AGENTS.md — profi-worker (Контур A: автоотклики Профи.ру)

Перед любой работой с браузером/откликами читать `RULES.md` — он обязателен
и главнее этого файла. Спека: `docs/SPEC.md`, журнал: `docs/JOURNAL.md`.

## Что это

Автономный воркер откликов на Профи.ру: лента заказов → hard-фильтры →
LLM-триаж и кастомный текст отклика → отправка с денежными гейтами.
Мультиаккаунтно: **один аккаунт = одна персона = свой env, профиль Chrome, CDP-порт и БД**.

## Запуск (VPS, здесь)

```bash
cd /root/profi-worker
uv sync
cp .env.example .env        # ZAI_API_KEY, GLM_BASE_URL, LLM_MODEL
uv run python -m profi llm-check   # проверка LLM

# поднять аккаунт (браузер + воркер, идемпотентно) — generic, по accounts/<acc>.env:
bash scripts/account/run_account.sh lang    # акк lang (Валерия, англ+исп, CDP :9224, data/lang.db)
bash scripts/account/run_account.sh info    # акк info (информатика, CDP :9223, data/profi.db)
bash scripts/browser/fix_browser.sh lang    # починить зависший браузер lang
bash scripts/account/fix_worker.sh info     # перезапустить info-воркер
```

## Запуск (локально на Windows)

Профили Chrome НЕ в репо (публичный GitHub, в профилях живые куки сессий).
Каждый `accounts/<acc>.env` должен задавать свой `PROFI_CHROME_PROFILE` и
`PROFI_CDP_PORT`.

Теперь вручную поднимать Chrome перед воркером не нужно. На аккаунт запускается
один supervisor: он держит живыми **Chrome этого профиля + worker**, а worker
подключается к Chrome по CDP и сам переподключается после его рестарта.

```powershell
# Запустить/гарантировать один supervisor на аккаунт (идемпотентно):
powershell scripts\start-win.ps1 -Account info
powershell scripts\start-win.ps1 -Account lang

# Остановить только один аккаунт; Chrome намеренно остаётся жить:
powershell scripts\stop-win.ps1 -Account info

# Остановить все Windows supervisor/worker/autopilot процессы:
powershell scripts\stop-win.ps1
```

Жизненный цикл:

```text
start-win.ps1
  ↓
supervise-win.ps1 <account>
  ├─ CDP /json/version жив? ─ yes ─┐
  │                                │
  ├─ no, Chrome профиля нет        │
  │    → старт exact profile+port  │
  │                                │
  ├─ no, managed Chrome завис      │
  │    → несколько health-fail     │
  │    → recycle только его        │
  │                                │
  ├─ foreign Chrome держит profile │
  │    → FAIL CLOSED, не убиваем   │
  │                                │
  └─ CDP READY ────────────────────┘
           ↓
      worker жив?
       ├─ yes → monitor
       └─ no  → restart worker
```

- `start-win.ps1` **идемпотентен**: если supervisor этого аккаунта уже жив,
  повторный запуск завершается без создания дубля.
- При первом переходе со старого Windows launcher новый `start-win` fail-closed,
  если видит старые `profi-worker-*.ps1` / `profi-loop-*.ps1` или untagged worker.
  Один раз выполнить `scripts\stop-win.ps1`, затем снова `start-win -Account ...`.
- `PROFI_CHROME_NO_LAUNCH=1` остаётся правильным: **worker сам Chrome не стартует**.
  Владение Chrome вынесено во внешний `supervise-win.ps1`, поэтому не повторяется
  старый инцидент с попыткой BrowserManager захватить чужой профиль.
- Если Chrome полностью закрылся, supervisor поднимает тот же профиль/порт. Живой
  worker увидит восстановившийся CDP через `BrowserManager.reconnect()` и продолжит.
- Если процесс Chrome ещё существует, но CDP кратко моргнул, supervisor не убивает
  его сразу: по умолчанию recycle только после 3 последовательных health-fail.
- Если тот же профиль уже открыт другим/ручным Chrome без ожидаемого CDP-порта,
  supervisor пишет `PROFILE_IN_USE_NO_CDP` и ждёт — чужой браузер не убивает.
- При `PROFI_FAST_PATH=1` отдельного autopilot-loop нет. При явном rollback
  `PROFI_FAST_PATH=0` supervisor поднимает legacy `run-autopilot-win.ps1` раз в 120 с.
- Изменения account-env, влияющие на порт/профиль/fast-path, применять через
  `stop-win.ps1 -Account <acc>` → `start-win.ps1 -Account <acc>`.
- Логи: `logs\supervisor-<акк>.log`, `logs\console-<акк>.log`, при rollback
  `logs\autopilot-<акк>.log`, плюс `logs\respond\*.png`; БД `data\<акк>.db`.
- Опционально: `PROFI_BROWSER_WATCH_INTERVAL` (дефолт 10 с, минимум 3) и
  `PROFI_BROWSER_RESTART_FAILURES` (дефолт 3, минимум 2).
- Грабли Windows: `uv sync` на этой машине может падать с DNS-ошибкой — runtime
  использует готовую `.venv\Scripts\python.exe` с `PYTHONPATH=src`, `PYTHONUTF8=1`.

## Запуск (Mac — legacy до 03.09)

```bash
bash scripts/browser/start-chrome.sh        # Chrome с CDP
bash scripts/account/run_account.sh <acc>   # браузер + воркер, идемпотентно
# автопилот и чаты — launchd: autopilot_cron.sh, chat_cron.sh (см. scripts/)
```

## Крон (пользователь root)

- `*/15 * * * *` `scripts/rhythm_keeper.sh` — живость всех `accounts/*.env`,
  «человеческий ритм» (паузы 01–14 МСК), лог `logs/rhythm.log`
- `18,48 * * * *` `/root/profi-autopilot-accounts.sh` — legacy-autopilot по всем
  `accounts/*.ready`; при fast-path `run_autopilot()` завершается no-op

**Отключить аккаунт**: переименовать `accounts/<acc>.env` и `<acc>.ready`
в `*.disabled` — кроны его перестанут трогать.

## Структура

```
src/profi/          — пакет (ставится через uv sync, editable):
  main.py           — CLI и оркестрация: цикл воркера (лента + fast-path + чат-чек),
                      legacy autopilot для rollback, chats/chat-auto
  config.py         — все настройки + env-переопределения (якорь: PROJECT_DIR = корень репо)
  fastpath.py       — full gates → LLM/fallback → same-page send, terminal states
  filters.py        — hard-фильтры (предметы, дистанционка)
  browser/          — Chrome over CDP: manager (connect/reconnect), логин-стена
  integration/      — Профи.ру: feed (перехват BoSearchBoardItems), orders (карточки),
                      respond (форма ЧЕЛОВЕЧЕСКИМИ инпутами, RULES §1), chat (чаты)
  storage/          — SQLite: feed_seen, candidates, chat_log, v_responses
  llm/              — мульти-провайдерный LLM (glm/openai/anthropic), ключи в .env
  models/           — OrderSnippet, FeedSnapshot, FilterVerdict
  utils/            — human_pause (человеческий темп), textguard (анти-инъекция)
personas/           — промпты персон (info.md, lang.md)
profiles/           — бизнес-профили: subjects, stop-слова, persona, fallback templates
accounts/           — <acc>.env (профиль/порт/настройки) + <acc>.ready
scripts/            — точки входа планировщиков
  account/          — run_account.sh/fix_worker.sh (VPS), supervise-win.ps1,
                      run-worker-win.ps1, run-autopilot-win.ps1 (Windows)
  browser/          — start-chrome.sh (Mac), chrome-vps.sh, launch_account_browser.sh,
                      fix_browser.sh <acc>
  diag/             — read-only пробники: diag_feed2, probe_order/form/chat, chats_unread
data/               — <acc>.db (SQLite), browser-profiles/<acc>, rhythm_state.json
logs/               — worker/console/supervisor/autopilot logs, respond/ screenshots
```

## Ключевые env (в accounts/<acc>.env и .env)

- `PROFI_PROFILE`, `PROFI_PERSONA`, `PROFI_SUBJECTS`, `PROFI_CDP_PORT`,
  `PROFI_CHROME_PROFILE`, `PROFI_DB`
- `PROFI_FAST_PATH` — `1` основной same-page flow; `0` rollback к legacy-autopilot
- `PROFI_CHAT_EVERY` — чат-чек каждый N-й цикл воркера (дефолт 3)
- `PROFI_WORK_HOURS` — рабочее окно `[начало, конец)`, дефолт `8,23`
- `PROFI_CHROME_PATH` — путь к Chrome; Windows supervisor также пробует стандартные
  Program Files/LocalAppData пути, если env не задан
- `PROFI_CHROME_NO_LAUNCH` — для worker production `1`: Chrome запускает supervisor
- `PROFI_BROWSER_WATCH_INTERVAL` — Windows health-check interval, default 10 s
- `PROFI_BROWSER_RESTART_FAILURES` — сколько подряд dead-CDP checks до recycle, default 3
- `PROFI_RESPOND_MODE` — `pay` (платный) | `commission`
- LLM: `ZAI_API_KEY`/`GLM_API_KEY`, `GLM_BASE_URL`, `LLM_MODEL`

## Гейты безопасности (RULES.md §2)

- дневной лимит отправок `PROFI_DAILY_SEND_LIMIT` (дефолт 0 = без лимита),
  потолок цены отклика 500 ₽ (`MAX_RESPONSE_PRICE_RUB`)
- fast-path перед необратимым send повторно проверяет work-hours/лимиты;
  `unknown` после потенциального клика terminal и автоматически не ретраится
- ставка в форме: `config.RATE`

## Известные грабли (VPS)

- 1.6 ГБ RAM: два акка могут выжрать память → Chrome крашится, CDP-рукопожатие
  виснет. Внешний VPS supervisor/rhythm keeper должен вернуть браузер; при залипшем
  профиле иногда всё ещё нужен `fix_browser.sh`.
- `pkill -f` с паттерном из собственной команды убивает сам exec — используй
  class-трюк: `pkill -f "922[3]"`.
- Лимит z.ai месячный — при 429 (code 1310) смотреть, на какой модели: у
  `glm-5.3-flash` квота отдельная и живёт дольше.

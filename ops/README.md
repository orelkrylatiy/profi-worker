# Daily ops snapshots

`ops/` содержит санитизированные агрегированные отчёты, которые можно хранить в публичном репозитории и разбирать через GitHub/ChatGPT.

## Быстрый запуск

Collector использует только стандартную библиотеку Python. `uv run` не нужен.

Windows / PowerShell:

```powershell
python scripts\ops\daily_report.py --date today
python scripts\ops\daily_report.py --date yesterday
```

Linux / VPS:

```bash
python3 scripts/ops/daily_report.py --date today
python3 scripts/ops/daily_report.py --date yesterday
```

Для конкретной даты:

```bash
python3 scripts/ops/daily_report.py --date 2026-09-03
```

Collector создаёт/обновляет:

```text
ops/daily/YYYY-MM-DD.json
ops/latest.json
```

Часовой пояс отчёта по умолчанию — `Asia/Yekaterinburg`. На Windows без системной IANA timezone database есть встроенный fallback UTC+05:00, поэтому `tzdata` для Екатеринбурга не требуется.

Runtime использует тот же business timezone через `PROFI_TIMEZONE` (default `Asia/Yekaterinburg`) для рабочих часов и дневного лимита отправок. Это не зависит от timezone ОС/VPS.

## Schema v2: как читать отчёт

Начиная со schema version 2 намеренно разделены три разных типа показателей.

### `events_today`

События, которые реально произошли в выбранный календарный день:

- новые заказы и увиденные заказы feed;
- созданные candidates;
- завершённые details/drafts;
- начатые отправки (`sends_started`);
- `sent` / `unknown`;
- recorded upfront spend и response modes;
- `draft_sources` (`llm`, `fallback`);
- chat replies / `needs_human` / chat failures.

Это поток событий, а не cohort-конверсия. Например кандидат мог быть создан вчера, а отправлен сегодня.

### `inventory_now`

Текущий stock всей БД на момент генерации отчёта:

- распределение `send_status`;
- текущие `details_errors`;
- текущие `draft_errors`.

Эти числа нельзя делить на `events_today.candidates_created`: у них разные временные семантики.

### `cohort_first_seen_today`

Настоящая cohort-воронка: берём только кандидатов, впервые увиденных в выбранный день, и смотрим, куда они в итоге дошли:

- candidates;
- details ready;
- drafts generated;
- sent / unknown / failed / skipped;
- client replied;
- `reply_yield_pct = client_replied / candidates`.

Для сравнений конверсии между днями/профилями используй именно этот блок.

### `latency_sec`

Privacy-safe latency без order ids:

- `first_seen_to_details`;
- `first_seen_to_draft`;
- `first_seen_to_sent`;
- для каждого: `count`, `p50`, `p90`.

Это позволяет измерять эффект fast-path без чтения сырых заказов.

## Runtime / supervisor telemetry

Для каждого аккаунта `runtime` показывает, что было **увидено в canonical logs за выбранный день**:

- `worker_seen_today`;
- `supervisor_seen_today`;
- `sources_seen_today`;
- `last_seen_at`;
- supervisor/browser/worker events;
- event groups (`errors`, `recovery`, `operational`, `external_limits`);
- `availability_incidents`.

`worker_seen_today=true` не является гарантией, что процесс жив прямо сейчас. Это означает только наличие canonical worker-log activity за выбранный день. Для live process health нужен process/CDP probe на самой машине.

### Occurrences vs incidents

`logs.events` — raw occurrences разрешённых сигнатур. Повторяющаяся проблема может дать десятки строк.

`availability_incidents` объединяет последовательные одинаковые availability/process errors одного аккаунта, если между ними не более 5 минут. Поэтому один outage с 47 `BROWSER_OFFLINE` не обязан считаться 47 отдельными инцидентами.

Старого `technical_error_events` в schema v2 нет: он смешивал ошибки, recovery и нормальные operational events.

## Какие логи считаются

Для metrics используются только canonical sources:

```text
worker-<account>.log
browser-<account>.log
autopilot-<account>.log
supervisor-<account>.log
```

`console-<account>.log` — diagnostic mirror stdout/stderr Windows wrapper и **не участвует в counters**, иначе одна Python warning/error могла бы считаться дважды: из `worker-*` и `console-*`.

Supervisor-сигнатуры включают, например:

- `SUPERVISOR_START` / `SUPERVISOR_ERROR`;
- `WORKER_START` / `WORKER_FLAP_BACKOFF`;
- `CDP_PORT_CONFLICT` / `PROFILE_IN_USE_NO_CDP` / `CDP_UNHEALTHY`;
- `BROWSER_START` / `BROWSER_READY` / `BROWSER_RECYCLE` / startup failures.

Worker/browser сигнатуры включают `FEED_CAPTURE_ERROR`, `BROWSER_OFFLINE`, `AUTH_REQUIRED`, `OPEN_FAIL`, LLM limit, `send_status=failed|unknown`, DB lock, traceback, tab hygiene и CDP reconnect.

Сырые совпавшие строки в JSON никогда не сохраняются.

## Crash safety отправки

Перед необратимой отправкой candidate атомарно переходит:

```text
not_sent -> sending
```

и сохраняется `send_started_at`.

Если worker умер и `sending` остался старше 5 минут, следующий `Store` startup переводит его в terminal `unknown`. Повторной автоматической отправки нет. `sent_at` для такого reconciliation привязывается к исходному `send_started_at`, а не ко времени позднего рестарта — это сохраняет корректный business-day attribution.

Legacy `send_status=fail` нормализован в active flow до `failed`; collector всё ещё понимает старые строки/строки БД для обратной совместимости.

## A/B/C outreach metrics

`scripts/ops/experiment_report.py` показывает одновременно:

- `assigned` — сколько кандидатов получили variant;
- `evaluated` — сколько реально прошло LLM-decision этим variant;
- `send% = sent / evaluated`;
- `reply% = replied / sent`;
- `yield% = replied / evaluated` — основной acquisition KPI;
- fallback отдельно и не размывает LLM denominator.

Запуск:

```bash
uv run python scripts/ops/experiment_report.py --db data/lang.db
```

## Автоматический daily publish

Для чистого VPS-клона на `main`:

```bash
bash scripts/ops/daily_publish.sh yesterday
```

Publisher:

1. проверяет нужную branch;
2. отказывается работать при tracked/staged изменениях вне `ops/`;
3. делает `git pull --ff-only` **до генерации**, поэтому `code_revision` относится к актуальному коду;
4. запускает collector обычным Python без PyPI;
5. stage/commit делает только для дневного отчёта и `ops/latest.json`;
6. делает push.

Настройки:

```text
OPS_TIMEZONE=Asia/Yekaterinburg
OPS_PUBLISH_BRANCH=main
OPS_PYTHON=python3
```

Пример cron за вчера в 02:30:

```cron
30 2 * * * cd /root/profi-worker && OPS_TIMEZONE=Asia/Yekaterinburg bash scripts/ops/daily_publish.sh yesterday >> logs/ops-daily.log 2>&1
```

## Pre-commit snapshot

Для локального клона можно активировать существующий hook:

```bash
git config core.hooksPath githooks
```

Он запускает `daily_report.py --date today` и добавляет отчёт в commit; ошибка collector не блокирует commit. Merge commits не snapshot-ятся.

## Privacy contract

Collector работает по allow-list. В generated JSON не должны попадать:

- client names / PII;
- order ids;
- тексты чатов, заказов или откликов;
- raw URLs;
- cookies, headers, API keys, tokens;
- raw traceback/exception/log fragments.

Хранятся только агрегаты, timestamps уровня runtime-health и code revision. `logs/` и `data/` остаются в `.gitignore`; это не мешает collector читать их локально.

После push можно попросить ChatGPT:

```text
Посмотри ops/latest.json в profi-worker. Разбери events_today, cohort, inventory, runtime incidents и latency по аккаунтам.
```

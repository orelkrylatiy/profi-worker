#!/bin/bash
# shellcheck disable=SC1090
# rhythm_keeper.sh — человеческий ритм (generic: все accounts/*.env).
export PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Гарантированно онлайн с 18:00 МСК; паузы 60–120 мин, макс 2/день, только 01–14 МСК.
set -u
BASE=/root/profi-worker
MSK_H=$(TZ=Europe/Moscow date +%H)
STATE=$BASE/data/rhythm_state.json
TODAY=$(date +%F)
PAUSED_UNTIL=0; PAUSES_TODAY=0; LAST_DAY=""
[ -f "$STATE" ] && eval "$(jq -r '"PAUSED_UNTIL=\(.paused_until // 0) PAUSES_TODAY=\(.pauses_today // 0) LAST_DAY=\"\(.day // \"\")\""' "$STATE" 2>/dev/null)"
[ "$LAST_DAY" != "$TODAY" ] && PAUSES_TODAY=0
NOW=$(date +%s)

stop_all() {
  for ENVF in "$BASE"/accounts/*.env; do
    ACC=$(basename "$ENVF" .env)
    . "$ENVF"
    pkill -f "profi.main --rhythm-tag $ACC\$" 2>/dev/null
    pkill -f "user-data-dir=${PROFI_CHROME_PROFILE}" 2>/dev/null
  done
}

if [ "$NOW" -lt "$PAUSED_UNTIL" ]; then
  stop_all
  jq -n --arg d "$TODAY" --argjson p "$PAUSED_UNTIL" --argjson n "$PAUSES_TODAY" '{day:$d,paused_until:$p,pauses_today:$n}' > "$STATE"
  exit 0
fi

# вне паузы: гарантируем живость всех акков
for ENVF in "$BASE"/accounts/*.env; do
  bash "$BASE/scripts/account/run_account.sh" "$(basename "$ENVF" .env)"
done
[ "$PAUSED_UNTIL" -gt 0 ] && PAUSED_UNTIL=0

# новая пауза: 01–14 МСК, ~8% на проверку, не больше 2/день
if [ "$MSK_H" -ge 1 ] && [ "$MSK_H" -lt 14 ] && [ "$PAUSES_TODAY" -lt 2 ]; then
  if [ $((RANDOM % 100)) -lt 8 ]; then
    DUR=$((60 + RANDOM % 61))
    PAUSED_UNTIL=$((NOW + DUR * 60))
    PAUSES_TODAY=$((PAUSES_TODAY + 1))
    stop_all
    echo "$(date '+%F %T') пауза ${DUR}м (№$PAUSES_TODAY)" >> "$BASE/logs/rhythm.log"
  fi
fi

jq -n --arg d "$TODAY" --argjson p "$PAUSED_UNTIL" --argjson n "$PAUSES_TODAY" '{day:$d,paused_until:$p,pauses_today:$n}' > "$STATE"

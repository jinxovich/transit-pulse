#!/usr/bin/env bash
# Chaos-проверка деградации: обрыв потока (стоп replayer) и падение ML-сервиса.
# Ожидания: backend не падает; при обрыве — DEGRADED, при падении ML — model_mode=fallback;
# после восстановления — снова LIVE / ml.
set -euo pipefail
cd "$(dirname "$0")/.."
API=${API:-http://localhost:8000/api/v1}
STREAM_OUTAGE_S=${STREAM_OUTAGE_S:-60}
ML_OUTAGE_S=${ML_OUTAGE_S:-30}

status() {
  curl -sf "$API/metrics/summary" >/dev/null || { echo "  backend НЕ отвечает"; return; }
  curl -sf "$API/health" | python3 -c 'import sys,json; h=json.load(sys.stdin); print("  health:", h["status"], h["checks"])'
  curl -sf "$API/vehicles" | python3 -c '
import sys, collections, json
vs = json.load(sys.stdin)
modes = collections.Counter(v["prediction"]["model_mode"] for v in vs if v.get("prediction"))
print("  ТС:", len(vs), "stale:", sum(v["stale"] for v in vs), "прогнозы:", dict(modes))'
}

watch_for() {  # watch_for <секунд> <метка>
  for ((i = 0; i < $1; i += 10)); do sleep 10; echo "[$2 +$((i + 10))s]"; status; done
}

echo "== исходное состояние"; status

echo "== обрыв потока: stop replayer на ${STREAM_OUTAGE_S} c"
docker compose stop replayer >/dev/null
watch_for "$STREAM_OUTAGE_S" "поток оборван"
docker compose start replayer >/dev/null
watch_for 20 "поток восстановлен"

echo "== падение ML: stop ml на ${ML_OUTAGE_S} c"
docker compose stop ml >/dev/null
watch_for "$ML_OUTAGE_S" "ml недоступен"
docker compose start ml >/dev/null
watch_for 30 "ml восстановлен"
echo "== готово"

#!/usr/bin/env bash
# Замер холодного старта: время от `docker compose up` до healthy всех сервисов
# и до первого прогноза на потоке. Результат — строки для docs/PERFORMANCE.md.
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICES=(ml backend replayer dashboard)
API=${API:-http://localhost:8000/api/v1}

docker compose down --remove-orphans >/dev/null 2>&1 || true
start=$(date +%s.%N)
docker compose up -d >/dev/null

for svc in "${SERVICES[@]}"; do
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null)" = "healthy" ]; do
    sleep 0.5
  done
  printf '%-10s healthy через %6.1f c\n' "$svc" "$(echo "$(date +%s.%N) - $start" | bc)"
done

until curl -sf "$API/vehicles" | python3 -c 'import sys,json; sys.exit(0 if any(v.get("prediction") for v in json.load(sys.stdin)) else 1)'; do
  sleep 1
done
printf '%-10s через %6.1f c (включая прогрев %s сим-мин)\n' "1-й прогноз" \
  "$(echo "$(date +%s.%N) - $start" | bc)" "${REPLAY_WARMUP_MIN:-30}"

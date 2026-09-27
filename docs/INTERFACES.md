# Межмодульные интерфейсы (договор между ветками)

Документ фиксирует границы модулей, чтобы `feat/ndtp`, `feat/ml` и `feat/backend` сошлись без
переделок. Контракт с дашбордом — `packages/transit_core/transit_core/schemas.py` (не меняем без
крайней нужды; если меняем — перегенерировать `contracts/` через `contract_export`).

Общие соглашения:

* Время — naive `datetime` во «времени датасета» (день 2026-01-06). В NDTP `timestamp` =
  `calendar.timegm(dt.timetuple())`, обратно — `datetime.utcfromtimestamp(ts)` без tz.
* Задержка в секундах, `+` — опоздание.
* Порты: backend HTTP `8000`, backend NDTP TCP `9201`, ml `8001`, replayer `8090`, dashboard `8080`,
  эмулятор `18080`.
* Данные: `DATA_DIR` (по умолчанию `data/raw`, в Docker — `/data/raw`, том `./data/raw:ro`).
* Модели: `MODELS_DIR` (по умолчанию `models/`, в образе ml — `/app/models`).

## 1. `transit_core.ndtp` (ветка `feat/ndtp`)

```python
@dataclass(frozen=True)
class NavCell:            # G6CellNav00, 26 байт
    timestamp: int        # unix-секунды
    lon: float            # со знаком, 1e-7 град
    lat: float
    valid: bool           # extraDopBit7
    speed_kmh: int        # speedAvg
    speed_max_kmh: int
    course: int
    track_m: int
    altitude_m: int
    nsat: int
    pdop: int
    bat_voltage: int
    flags: int            # сырой байт extraDop

@dataclass(frozen=True)
class Cell:
    type: int
    number: int
    name: str             # "G6CellNav00", "G6CellCan10", ...
    fields: dict[str, int | float | bool]

@dataclass(frozen=True)
class Frame:
    unit_id: int          # NPL peerAddress
    service_id: int
    nph_type: int         # 100 CONN_REQUEST, 101 REALTIME
    request_id: int       # NPH requestId
    crc_ok: bool
    nav: NavCell | None
    cells: tuple[Cell, ...]
    handshake: dict | None
    raw: bytes

class FrameDecoder:       # потоковый: частичные/склеенные кадры, ресинк по 0x7E7E
    def feed(self, data: bytes) -> list[Frame]: ...
    crc_errors: int; parse_errors: int; resyncs: int

def encode_handshake(unit_id: int, request_id: int) -> bytes: ...
def encode_realtime(unit_id: int, request_id: int, nav: NavCell, extra: Sequence[Cell] = ()) -> bytes: ...
def encode_result(frame: Frame, code: int = 0) -> bytes: ...   # ответ сервера (опционален)
def frame_fields(frame: Frame) -> dict[str, float | int | bool | str]: ...  # для LastPacket.fields
```

## 2. Replayer ↔ backend (ветки `feat/ndtp` и `feat/backend`)

Replayer (`services/replayer`, порт 8090) читает `validate/traffic.csv` по `event_time`, на каждый
`unit_id` держит своё TCP-соединение к `BACKEND_NDTP` (handshake → realtime Nav00). Невалидные строки
(`location_valid=False`) шлёт с `valid=0` и нулевыми координатами — как настоящий терминал.

Env: `BACKEND_NDTP=backend:9201`, `BACKEND_HTTP=http://backend:8000`, `REPLAY_START=07:00`,
`REPLAY_SPEED=30`, `REPLAY_LOOP=1`, `REPLAY_WARMUP_MIN=30`, `REPLAY_AUTOSTART=1`, `FLEET_MULTIPLIER=1`.

Сим-время replayer: `sim = sim_anchor + (wall − wall_anchor) × speed`. Прогрев: с `start − warmup`
до `start` играет со скоростью `WARMUP_SPEED=600`, затем `REPLAY_SPEED`.

О каждом изменении сессии replayer сообщает backend:

```
POST {BACKEND_HTTP}/internal/sim/session
{"session_id": "s-20260927-0001", "sim_time": "2026-01-06T06:30:00", "speed": 600.0,
 "state": "running" | "paused" | "stopped", "warmup_until": "2026-01-06T07:00:00" | null}
```

Новый `session_id` (старт, seek, новый круг loop) → backend очищает state/инциденты и шлёт WS
`snapshot`. Тот же `session_id` с другими `speed/state` → только обновление часов.

HTTP replayer: `GET /status` → `{session_id, sim_time, speed, state, units, packets_sent}`;
`POST /control` с телом `ReplayControl` из контракта. Backend `POST /api/v1/replay/control`
проксирует сюда. `GET /health`.

`FLEET_MULTIPLIER=k` (loadtest): каждый реальный юнит клонируется k раз с `unit_id + 10_000_000·i`
и сдвигом координат; такие борта backend видит как `unknown`.

## 3. `transit_core` — расписание, признаки, стоп-детектор (ветка `feat/ml`)

```python
# transit_core/plan.py
def load_plan(path: Path) -> pd.DataFrame
# колонки: visit_id (int, tt_action_item_id), tr_id (int), tb (datetime64), lon, lat,
#          stop_key (str, как contracts.mockgen.common.stop_key), name (адрес или NO_ADDRESS),
#          manual_fill (bool), gap_min (float, NaN у первой), new_trip (0/1), trip (int)
# отсортировано по (tr_id, tb). Факт (time_fact_begin) не читается никогда.

# transit_core/features.py — ЕДИНЫЙ as-of путь для офлайна и потока
FEATURES: list[str]                       # порядок колонок модели
def point_features(plan_tr: pd.DataFrame, track: pd.DataFrame, t: datetime,
                   visit_id: int, cur_dev_s: float | None) -> dict[str, float]
# track: колонки et (datetime64), lon, lat, speed, heading, valid (bool) — ВСЯ история ТС;
# функция сама берёт только et <= t. Возвращает словарь с ключами FEATURES (NaN допустим).

# transit_core/stops_detector.py — виртуальный факт прибытия по GPS
def detect_arrivals(plan_tr: pd.DataFrame, track: pd.DataFrame, until: datetime) -> pd.DataFrame
# колонки: visit_id, actual_at (datetime64), delay_s (float); только визиты с tb <= until
def online_cur_dev(plan_tr: pd.DataFrame, track: pd.DataFrame, t: datetime) -> float | None
# онлайн-аналог cur_dev_s: задержка на последней остановке с плановым временем <= t
```

## 4. ML-сервис (ветка `feat/ml`, `services/ml`, порт 8001)

```
POST /predict
{"model": "stream" | "submission",           # по умолчанию stream
 "items": [{"id": "130072:53700172828", "features": {"cur_dev": 42.0, "lead": 12.0, ...}}]}
→ 200
{"model_version": "catboost-v2-stream", "latency_ms": 3.1,
 "items": [{"id": "...", "delay_s": 95.0,          # абсолютный прогноз = cur_dev + остаток (q50)
            "q10": 20.0, "q90": 180.0, "p_late": 0.41, "expected_abs_error_s": 48.0,
            "contributions": [{"feature": "cur_dev", "contribution_s": 30.2}, ...]}]}  # топ-5 по |вкладу|
GET /health → {"status": "ok", "model_version": ..., "models": [...]}
GET /model/info → {"features": [...], "metrics": {...из models/metrics.json},
                   "calibration": {"stream": {...} | null, "submission": {...} | null}}
```

`p_late` — вероятность `delay > 120 c` по интерполяции квантилей. Недостающие признаки → NaN.
Если рядом с моделью лежит `models/calibration_{mode}.json` (`services.ml.app.calibration`,
пишется `train`): отступы q10/q90 от медианы растягиваются масштабом корзины признака `lead`
(split-conformal на OOF, покрытие 80% в каждой корзине), `p_late` проходит изотоническую
калибровку, `expected_abs_error_s = err_k · (q90 − q10)/2` по уже калиброванной ширине. Без файла —
общий `interval_scale` из `metrics.json` и сырой `p_late`. Brier/ECE/reliability до→после —
в `metrics.json → {mode}.calibration` и в сводке `calibration` у `/model/info`.

## 5. Backend (ветка `feat/backend`, `services/backend`, порт 8000 + NDTP 9201)

Реализует все REST из `schemas.REST_MODELS` под `/api/v1` и WS `/ws/v1/stream` ровно по контракту
(примеры ответов — `contracts/fixtures/*.json`, сценарий WS — `contracts/fixtures/ws_session.jsonl`).
Плюс `POST /internal/sim/session`, `GET /metrics` (Prometheus), `/docs`, CORS `*`.

`ML_URL=http://ml:8001`; при недоступности ml — circuit breaker и эвристика `0.7·cur_dev`,
`model_mode="fallback"`.

Проход прогнозов зовёт `/predict` дважды: `explain=false` для всего батча и `explain=true` только
для визитов, причину которых видит диспетчер (текущий прогноз red/yellow, первый красный визит,
цель открытого инцидента). Упал второй вызов — прогнозы остаются, причина по правилам.
`ml_batch` в `/metrics/summary` — сумма двух вызовов; раздельно — `tp_ml_predict_ms` / `tp_ml_explain_ms`.

`POST /api/v1/whatif` (`WhatIfRequest` → `WhatIfResult`): прогноз ТС без меры и с мерой по
остановкам `(T, T+60]` (с пометкой окна `(T+10, T+15]`). Меры: `hold_at_stop` (+N мин к отклонению),
`shorten_dwell` / `skip_layover` (−N / −весь отстой после конечной, не раньше графика),
`add_reserve` (рейс после конечной по графику). Те же признаки и ML-клиент; 409 — у ТС нет прогноза.

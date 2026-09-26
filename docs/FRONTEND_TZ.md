# ТЗ: дашборд диспетчера Transit Pulse

Документ для фронтендера. Прочитай его целиком один раз (минут 20): здесь всё, чтобы
начать сегодня и не ждать бэкенд. Вопросы по контракту — сразу в чат, не костыль молча.

---

## 0. Коротко

- **Что делаем.** Веб-дашборд диспетчера наземного транспорта. На карте ТС, раскрашенные
  по риску опоздания, лента инцидентов «через 10–15 минут ТС опоздает» и карточка инцидента
  с причиной и рекомендациями. Данные приходят в реальном времени по WebSocket.
- **Стек.** React 18 + TypeScript + Vite, MapLibre GL, Zustand, TanStack Query, ECharts.
- **Бэкенд тебе не нужен.** Mock-сервер отдаёт тот же REST/WS, что и настоящий бэкенд, на
  реальных траекториях ТС: `node contracts/mock-server/server.mjs`.
- **Типы готовы.** `contracts/ts/contract.ts` генерируется из бэкенда, руками не править.
- **Где код.** Всё твоё — в папке `dashboard/`. Остальное в репозитории не трогаешь.
- **Вехи.**

  | Когда | Что |
  |---|---|
  | 26.09 12:00 | P0 на моках |
  | 26.09 20:00 | Переключение на живой бэкенд |
  | 27.09 14:00 | P1 |
  | 27.09 18:00 | Фриз |

---

## 1. Зачем и как оценивают

Хакатон Московского транспорта. Система прогнозирует задержку ТС на остановке **за 10–15
минут до события**, чтобы диспетчер действовал заранее, а не констатировал опоздание.
Дашборд — это то, что жюри увидит глазами. Критерий 4 «Диспетчерский BI-дашборд» (6 баллов)
дословно:

1. **Понятность за ~5 секунд:** диспетчер видит проблемные ТС и участки без инструкции.
2. **Карта маршрутной сети** с текущим положением ТС и цветовой индикацией риска
   (зелёный / жёлтый / красный).
3. **Карточка инцидента:** ТС с высокой вероятностью задержки, прогнозируемое опоздание,
   предполагаемая причина, участок маршрута.
4. **Данные обновляются в реальном времени** вслед за потоком.

Ещё два критерия жюри тоже проверяет через дашборд:

- **горизонт 10–15 минут** (4 балла): в карточке видно «алерт создан за N мин до события»;
- **надёжность** (4 балла): при обрыве потока дашборд не падает, показывает баннер DEGRADED и
  восстанавливается сам.

Всё, что ниже помечено **P0**, закрывает эти баллы. P1 — усиление, P2 — если останется время.

---

## 2. Быстрый старт

```bash
git clone https://github.com/jinxovich/transit-pulse.git && cd transit-pulse
node contracts/mock-server/server.mjs            # Node 20+, без npm install
# → http://localhost:8000/mock   (справка по эндпоинтам)
# → ws://localhost:8000/ws/v1/stream
```

Каркас фронта создаёшь сам в `dashboard/`:

```bash
npm create vite@latest dashboard -- --template react-ts
cd dashboard
npm i maplibre-gl react-map-gl zustand @tanstack/react-query echarts echarts-for-react \
      @fontsource/ibm-plex-sans @fontsource/ibm-plex-mono
```

Добавь в `dashboard/package.json` скрипт `"mock": "node ../contracts/mock-server/server.mjs"`.

Типы контракта подключи алиасом, без копирования.

`vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const backend = process.env.VITE_BACKEND ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@contract": fileURLToPath(new URL("../contracts/ts/contract.ts", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
});
```

`tsconfig.app.json` → `compilerOptions.paths`: `{ "@contract": ["../contracts/ts/contract.ts"] }`.

Использование: `import type { VehicleState, Incident, WsMessage } from "@contract";`.

**Во фронте только относительные URL** (`/api/v1/...`, `/ws/v1/stream`). Адрес бэкенда задаёт
прокси: Vite в разработке, nginx в Docker. Живой бэкенд после 26.09 20:00 подключается так:
`VITE_BACKEND=https://<ссылка, которую дадим> npm run dev`.

### Возможности mock-сервера

- **Сессия.** 30 сим-минут дня (07:00–07:30) проигрываются по 10 сим-секунд за реальную
  секунду, то есть примерно за 3 минуты. Потом сессия начинается заново: приходит новый
  `session_id` и новый `snapshot`. Так же ведёт себя настоящий бэкенд при перемотке.
- **Режимы потока внутри сессии.** Прогрев (`WARMING_UP`, первые кадры), затем `LIVE`, с кадра
  ~120 обрыв потока (`DEGRADED`, ~20 кадров), потом снова `LIVE`.
- **Что есть в данных.** 5 инцидентов с полным жизненным циклом (`open → ack → resolved`),
  все уровни риска, ТС без расписания, неопознанные борта и ТС с потерей GPS.
- **Управление.**
  - Режим вручную: `POST /mock/status {"mode":"OFFLINE"}`, возврат — `{"mode":"AUTO"}`.
  - Пауза и скорость: `POST /api/v1/replay/control {"action":"pause"}`,
    `{"action":"speed","speed":30}`.
  - Скорость при запуске: `MOCK_SPEED=3 node contracts/mock-server/server.mjs`.

---

## 3. Стек и правила

| Что | Чем | Почему |
|---|---|---|
| Сборка | Vite + React 18 + TS (strict) | Быстро, статичный билд под nginx |
| Карта | `maplibre-gl` + `react-map-gl/maplibre` | Бесплатно, без ключей, WebGL |
| Подложка | Carto Dark Matter: `https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json` | Без ключа. URL в `VITE_MAP_STYLE` |
| Запасная подложка | Пустой стиль: фон `--bg-map` и только наши слои | Если на защите не будет интернета |
| Состояние WS | Zustand (один store) | Мелкие подписки, без ре-рендера всего дерева |
| REST | TanStack Query | Кэш, polling для метрик |
| Графики | ECharts (`echarts-for-react`) | Интервалы, markArea, тёмная тема |
| Шрифты | `@fontsource/ibm-plex-sans`, `@fontsource/ibm-plex-mono` | Кириллица, табличные цифры, работает офлайн |
| Стили | CSS Modules или обычный CSS + CSS-переменные | Без UI-китов: нужен свой вид, не шаблон |

Предлагаемая структура:

```
dashboard/src/
  app/            App.tsx, layout, провайдеры
  api/            rest.ts (fetch-обёртки), ws.ts (клиент + reconnect)
  store/          stream.ts (zustand: vehicles, incidents, kpis, status, session)
  lib/            time.ts (formatSimTime, minutesUntil), format.ts (formatDelay), risk.ts
  features/
    topbar/       TopBar, KpiCounters, StreamBadge, ReplayControls
    map/          MapView, layers/*.ts, icons.ts, Legend
    incidents/    IncidentFeed, IncidentCard, EvidenceBars, Recommendations
    vehicle/      VehicleDrawer, DeviationChart, StopTimeline
    system/       SystemPanel, LatencyTiles, LeadHistogram, LastPacket
    banners/      StatusBanner, ConnectionBanner
  styles/         tokens.css, global.css
```

---

## 4. Модель данных: что означают поля

Все типы — в `contracts/ts/contract.ts`, с комментариями к полям. Главное:

### Сущности

- **`VehicleState`** — ТС на карте. Ключ — `vehicle_id` (строка).
- **`Prediction`** (`vehicle.prediction`) — прогноз задержки на **целевой остановке**.
  Цель — первая остановка ТС с плановым временем в окне `(T+10, T+15]` минут от момента
  прогноза. Обновляется каждую сим-минуту.
- **`Incident`** — алерт на ТС с красным риском. У ТС не больше одного открытого инцидента,
  ссылка на него — в `vehicle.open_incident_id`.
- **`Network`** — статическая сеть дня: 847 остановок, 13 маршрутов (у каждого ТС свой),
  около 900 перегонов. Грузится один раз.

### Семантика полей (важно!)

| Поле | Значение | Как показывать |
|---|---|---|
| `predicted_delay_s` | Прогноз задержки на целевой остановке, секунды. **`+` — опоздание, `−` — опережение** | `+2 мин 30 с` / `−45 с`, функция `formatDelay` (§9) |
| `interval_s` | `[q10, q90]` — 80%-интервал прогноза | «от +1:44 до +3:53» или усы на графике |
| `expected_abs_error_s` | Ожидаемая абсолютная ошибка | `±58 с` рядом с прогнозом |
| `p_late` | Вероятность опоздания > 120 с | `78%` |
| `lead_min` | Минут от момента прогноза до планового прибытия | «за 11 мин до события» |
| `horizon_ok` | `false` — в окне 10–15 мин нет остановок (перерыв, конечная), прогноз на более позднюю | Приглушённо, подпись «вне окна 10–15 мин»; инцидентов по таким прогнозам не бывает |
| `risk_level` | `green` / `yellow` / `red` / `early` (опережение) / `none` (прогноза нет) | Цвет + форма маркера (§6). **Не пересчитывать на фронте**, брать как есть |
| `kind` | `scheduled` — есть расписание и прогнозы; `no_schedule` — ТС без расписания (только позиция); `unknown` — борт не из справочника, например от эмулятора, `vehicle_id = "u:<unit_id>"` | `no_schedule` и `unknown` — нейтральный серый маркер без прогноза; `unknown` ещё и с пометкой «неопознанный борт» |
| `stale` | Давно нет данных от ТС | Маркер полупрозрачный / пунктирный, подпись «нет данных N мин» по `last_seen` |
| `warming_up` | Мало истории, прогноз предварительный, инциденты не создаются | Мелкая пометка «прогрев» в тултипе |
| `model_mode` | `ml` — модель, `fallback` — эвристика при деградации | В карточке и тултипе бейдж «упрощённый прогноз» при `fallback` |
| `current_dev_s` | Отклонение от графика **сейчас** | В тултипе и drawer: «сейчас +2:19» |
| `cause.evidence[].contribution_s` | Вклад факта в прогноз, секунды | Горизонтальные бары |
| `outcome` | `pending` — событие ещё не наступило; `hit` — опоздание подтвердилось; `false_alarm` — не подтвердилось; `miss` — пропуск (в моках нет) | Для `resolved`: «Факт +3:55 — подтвердилось ✓» / «не подтвердилось» |
| `status` | `open` → `ack` (диспетчер отреагировал) → `resolved` (событие прошло) | Чип статуса; resolved уходят в конец ленты, серые |

> **Остановки без адреса.** У многих остановок в данных организаторов нет адреса, `name`
> приходит как `"Остановка без адреса"`. Не прячь такие строки. В карточке показывай
> соседнюю именованную остановку участка или координаты мелким шрифтом.

### Время — naive, «время датасета»

Все времена — строки `"2026-01-06T07:35:00"` **без часового пояса**. Это время из CSV
организаторов, показывать его нужно **как есть**. Никогда не делай `new Date(str)` с
локальной интерпретацией: браузер сдвинет время на свой пояс. Используй хелперы из §9.

«Сейчас» в системе — это **сим-время** (`sim_time` из любого WS-сообщения), а не часы
компьютера. Поток воспроизводится ускоренно, поэтому «через 9 мин» считай от `sim_time`.

---

## 5. API

### REST (`/api/v1`)

| Метод | Путь | Ответ | Когда вызывать |
|---|---|---|---|
| GET | `/config` | `AppConfig` | При старте. Пороги риска, цвета, горизонт — **только отсюда** |
| GET | `/network` | `Network` | При старте, один раз (≈550 КБ, отформатированный JSON) |
| GET | `/vehicles` | `VehicleState[]` | Не нужен, если есть WS. Запасной путь |
| GET | `/vehicles/{vehicle_id}` | `VehicleDetail` | При открытии drawer ТС, потом раз в 10 с, пока открыт |
| GET | `/incidents?status=open,ack` | `Incident[]` | Не нужен при WS. Для истории — `?status=resolved` |
| GET | `/incidents/{id}` | `Incident` | Прямая ссылка на инцидент |
| POST | `/incidents/{id}/ack` | `Incident` | Кнопка рекомендации. Тело `{action_code, comment}` |
| GET | `/segments/risk` | `SegmentRisk[]` | Раз в 10 с: окраска перегонов на карте |
| GET | `/metrics/summary` | `MetricsSummary` | Панель «Система», раз в 5 с |
| GET | `/metrics/quality` | `QualityMetrics` | Панель «Система», раз в 10 с |
| GET | `/ingest/stats` | `IngestStats` | Панель «Система», раз в 5 с |
| GET | `/sim/clock` | `SimClock` | Не нужен при WS |
| POST | `/replay/control` | `SimClock` | Кнопки пауза/скорость. `{action:"pause"\|"resume"\|"speed", speed?}` |
| GET | `/health` | `Health` | Опционально |

Ошибки приходят как HTTP 4xx/5xx с телом `{"detail": "..."}`. Показывай toast и не падай.

### WebSocket `/ws/v1/stream`

Каждое сообщение — это конверт:

```json
{ "type": "vehicles.delta", "schema_version": "1.0", "session_id": "mock-0001",
  "sim_time": "2026-01-06T07:15:00", "data": { "vehicles": [...], "removed": [] } }
```

| `type` | `data` | Что делать |
|---|---|---|
| `snapshot` | `{vehicles, incidents, kpis, status}` | **Заменить** всё состояние. Запомнить `session_id` |
| `vehicles.delta` | `{vehicles: VehicleState[], removed: string[]}` | Upsert ТС целиком по `vehicle_id`, удалить `removed` |
| `incident.opened` | `Incident` | Upsert. Toast «Новый инцидент: …», подсветить в ленте |
| `incident.updated` | `Incident` | Upsert (новый прогноз или ack) |
| `incident.resolved` | `Incident` | Upsert. Уводим в конец ленты, серым |
| `kpis` | `Kpis` | Заменить (~раз в 2 с) |
| `system.status` | `SystemStatus` | Заменить. Баннер по `mode` |
| `ping` | `{}` | Только обновить «последнее сообщение» (~раз в 10 с) |

Алгоритм клиента (`api/ws.ts` + `store/stream.ts`):

```ts
// Упрощённо. Типы из "@contract".
function onMessage(msg: WsMessage) {
  const s = useStream.getState();
  s.touch(msg.sim_time);                       // simTime и lastMessageAt = performance.now()
  if (msg.type === "snapshot") return s.replaceAll(msg);   // + sessionId = msg.session_id
  if (msg.session_id !== s.sessionId) return;  // хвост старой сессии: ждём её snapshot
  switch (msg.type) {
    case "vehicles.delta": return s.upsertVehicles(msg.data.vehicles, msg.data.removed);
    case "incident.opened": case "incident.updated": case "incident.resolved":
      return s.upsertIncident(msg.data, msg.type);
    case "kpis": return s.setKpis(msg.data);
    case "system.status": return s.setStatus(msg.data);
  }
}
```

- **Переподключение.** При `close` или `error` — backoff 0.5 → 1 → 2 → 5 → 10 с, потом каждые
  10 с. После подключения сервер сам пришлёт `snapshot`.
- **Watchdog.** Если сообщений нет 15 с — закрыть сокет и переподключиться.
- **Два разных баннера.** «Нет связи с сервером» — это проблема браузер ↔ бэкенд (WS
  отвалился). `DEGRADED` приходит **от бэкенда** и означает, что пропал поток телеметрии,
  хотя сам сервер жив. Не путать.

---

## 6. Дизайн

**Направление: ночной диспетчерский пульт.** Тёмный графитовый интерфейс, яркие только
сигналы риска. Это не «ещё один дашборд с карточками»: карта — героиня экрана, всё
остальное служит ей. Никаких UI-китов по умолчанию, градиентов-блобов и одинаковых
карточек в сетку.

### Токены (`styles/tokens.css`)

```css
:root {
  --bg: #0E1116;          /* фон приложения */
  --bg-map: #0B0E13;      /* фон карты без подложки */
  --surface: #151A22;     /* панели */
  --surface-2: #1C2330;   /* карточки, hover */
  --line: #2A3342;        /* разделители */
  --text: #E6EAF0;
  --text-dim: #8B93A1;
  --text-faint: #5B6472;

  /* риск — брать из /config.colors, это значения по умолчанию */
  --risk-green: #2FBF71;
  --risk-yellow: #F2B134;
  --risk-red: #E5484D;
  --risk-early: #3E9BFF;
  --risk-none: #8B93A1;
  --risk-stale: #6B7280;

  --font-sans: "IBM Plex Sans", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --radius-s: 4px; --radius-m: 8px;
  --gap-1: 4px; --gap-2: 8px; --gap-3: 12px; --gap-4: 16px; --gap-6: 24px;
  --dur-fast: 150ms; --dur: 250ms; --ease: cubic-bezier(.16,1,.3,1);
}
```

- Цифры (задержки, время, KPI) — `--font-mono` с `font-variant-numeric: tabular-nums`,
  чтобы значения не прыгали при обновлении.
- Цвета риска на старте **перезаписать из `/config.colors`** (`document.documentElement.style.setProperty`).
- Контраст текста ≥ 4.5:1. Фокус-стили видимые (кольцо 2px `--risk-early`).

### Кодирование риска цветом **и формой**

Так риск различим и для дальтоников, и на проекторе:

| risk | Цвет | Форма маркера ТС | Дополнительно |
|---|---|---|---|
| `red` | `--risk-red` | Треугольник | Крупнее, поверх остальных, P2: пульсирующий ореол |
| `yellow` | `--risk-yellow` | Ромб | — |
| `green` | `--risk-green` | Круг | — |
| `early` | `--risk-early` | Круг с полым центром | — |
| `none` (`no_schedule`/`unknown`) | `--risk-none` | Маленький круг | `unknown` — с обводкой пунктиром |
| любой + `stale` | Цвет риска, 40% прозрачности | Та же форма | Пунктирная обводка |

Стрелка курса у всех: маленький «клюв», повёрнутый по `heading`. Иконки рисуй один раз в
canvas и регистрируй через `map.addImage(name, imageData)`. Слой — `symbol` с
`icon-image: ["concat", risk, "-", shape]` и `icon-rotate: ["get","heading"]`,
`icon-rotation-alignment: "map"`.

### Карта

- **Подложка.** Carto Dark Matter, подписи улиц приглушены. Если стиль не загрузился —
  фон `--bg-map` (пустой стиль `{version:8, sources:{}, layers:[{id:"bg",type:"background",paint:{"background-color":"#0B0E13"}}]}`).
- **Слои снизу вверх:**
  1. `routes-base` — все перегоны из `/network.segments`, линия 2px `#2A3342`.
  2. `segments-risk` — перегоны из `/segments/risk` с `risk_level` yellow/red: 4px, цвет риска,
     красные поверх. Это и есть «проблемные участки».
  3. `stops` — остановки, кружки 3px, видны с zoom ≥ 13.
  4. `selection` — выбранный инцидент: его `segment.geometry` (6px, белая обводка) и целевая
     остановка (кольцо).
  5. `vehicles` — маркеры ТС. `symbol-sort-key` по риску: красные сверху.
- Стартовый вид — `fitBounds(network.bbox)` с отступами под панели.
- Легенда в левом нижнем углу: 5 форм с подписями и строка «серый пунктир — нет данных».

---

## 7. Экраны и компоненты

Один экран «Пульт» + выезжающие панели. Раскладка на 1440–1920 px:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ TopBar: 07:15:40 · ● LIVE ×10 · ТС 17/32 · 🔺1 ◆2 ●6 ○1 · Инцидентов 1 · ⏸ ×10 ×30 │
├──────────────────────────────────────────────────────────┬───────────────────┤
│ [StatusBanner — только если не LIVE]                     │ IncidentFeed      │
│                                                          │ ┌───────────────┐ │
│                  MapView (≈70% ширины)                   │ │🔺 +4:20 через 9м│ │
│                                                          │ │ Новоясеневский…│ │
│                                                          │ └───────────────┘ │
│  Legend                                                  │  …                │
└──────────────────────────────────────────────────────────┴───────────────────┘
 IncidentCard — поверх правой колонки;  VehicleDrawer — снизу или справа;  SystemPanel — по кнопке
```

### P0 — к 26.09 12:00 на моках

**TopBar**
- Сим-время крупно, моноширинно: `HH:MM:SS`, тикает от `sim_time`.
- Бейдж режима (`status.mode`): LIVE зелёная точка, WARMING_UP жёлтая, DEGRADED красная
  мигающая, OFFLINE серая, PAUSED синяя.
- Счётчики по `kpis.by_risk` с формами маркеров: 🔺red ◆yellow ●green ○early.
  Клик по счётчику фильтрует карту (P1).
- `ТС онлайн: vehicles_online / vehicles_total`, `Инцидентов: incidents_open`.

**StatusBanner** — полоса над картой, только при `mode !== "LIVE"`:
- **DEGRADED** (красный): «Поток телеметрии прерван. Прогноз — по расписанию и последнему
  известному состоянию» (текст берётся из `status.reason`) + «нет пакетов N с».
- **WARMING_UP** (жёлтый): «Прогрев: копится история, инциденты появятся через несколько минут».
- **OFFLINE**: «Нет данных от источника». **PAUSED**: «Воспроизведение на паузе».
- Отдельный **ConnectionBanner**: «Нет связи с сервером, переподключаемся…», когда отвалился WS.

**MapView** — слои и маркеры по §6.
- Hover ТС → тултип: маршрут, `vehicle_id`, «сейчас +2:19», «прогноз +2:43 на ост. X в 07:26 (через 11 мин)», скорость.
- Клик по ТС → VehicleDrawer (P1; в P0 достаточно тултипа, закреплённого по клику).
- Клик по перегону `segments-risk` → инцидент этого ТС, если он есть.

**IncidentFeed** — правая колонка:
- **Сортировка:**
  1. открытые (`open`, `ack`), затем `resolved`;
  2. внутри — `red` раньше остальных;
  3. затем по «минут до события» (`target_stop.planned_at − sim_time`) по возрастанию.
- **Элемент ленты:**
  - первая строка: форма и цвет риска, `+4 мин 20 с` крупно, «через 9 мин» — таймер от сим-времени;
  - вторая строка: маршрут и ТС;
  - третья строка: `cause.title`;
  - справа: чип статуса.
- Новый инцидент появляется с короткой анимацией (slide + вспышка фона 600 мс).
- Пусто → «Инцидентов нет — все ТС в графике» и маленький зелёный круг.
- Клик по элементу → IncidentCard, карта `flyTo` к участку, подсветка `selection`.

**IncidentCard** — главный объект критерия 4. Все четыре поля ТЗ обязательны:

1. **ТС:** маршрут (`route_name`), номер `vehicle_id`, бейдж риска, чип статуса.
2. **Прогноз опоздания:**
   - главная цифра `+4 мин 20 с` (`predicted_delay_s`), рядом `±69 с` (`expected_abs_error_s`);
   - строка «вероятность опоздания 98%» (`p_late`), интервал «от +3:06 до +5:45»;
   - «Остановка: <target_stop.name>, план 07:35 → прогноз 07:39:20»;
   - «Алерт создан за 11.8 мин до события» (`lead_min`) — это наш козырь по критерию горизонта, выдели его.
3. **Причина:**
   - `cause.title` жирным, `cause.details` обычным текстом;
   - «Почему так считаем»: список `evidence` с горизонтальными барами по `contribution_s`
     (подпись `label`, значение `value`, бар длиной ∝ вклад).
4. **Участок:** «<segment.from_stop.name> → <segment.to_stop.name>» + мини-подсветка на карте.
   Если `segment == null` — «Участок перед остановкой <target_stop.name>».
5. **Рекомендации:** список `recommendations` (title + description), у каждой кнопка «Принять».
   По нажатию — `POST /incidents/{id}/ack {action_code: rec.code}`, после успеха — статус
   «Принято: <title>» (из `ack`).
6. **Исход** (для `resolved`): «Факт: +3 мин 55 с — опоздание подтвердилось» (`hit`) /
   «не подтвердилось» (`false_alarm`).
7. Бейдж «упрощённый прогноз», если у ТС `prediction.model_mode === "fallback"`.

### P1 — к 27.09 14:00

**VehicleDrawer** (по клику на ТС). Данные — `GET /vehicles/{id}`, обновление раз в 10 с.
- **Шапка.** Маршрут, ТС, риск, «сейчас +2:19», «следующая: <next_stop.name> в 07:17».
- **DeviationChart** (ECharts):
  - по X — время, по Y — отклонение в секундах (`+` вверх);
  - линия `deviation_series` за последние 60 мин;
  - в `forecast.t` — точка прогноза `delay_s` с усами `[lo_s, hi_s]`;
  - горизонтальные зоны: зелёная до 60, жёлтая 60–120, красная > 120 (пороги из `/config`);
  - вертикальная линия «сейчас» = `sim_time`.
- **StopTimeline.** Список остановок `timeline`:
  - пройденные: план → факт, задержка цветом;
  - целевая (`is_target`) выделена рамкой;
  - будущие: план + прогноз серым.

**SystemPanel** (кнопка «Система» в TopBar) — доказательства для критериев 3 и 5:
- **Качество** (`/metrics/quality`):
  - плитки: онлайн-MAE; доля алертов с упреждением ≥ 10 мин (цель 100%); precision и recall;
  - гистограмма `lead_hist` (10…15 мин);
  - блок «Офлайн-валидация»: baseline MAE → модель MAE, снижение ошибки в %.
- **Производительность** (`/metrics/summary`): p50/p95 для `ingest_to_state`, `pass_to_ws`,
  `ml_batch`; `queue_lag`, `dropped_packets`.
- **Приём NDTP** (`/ingest/stats`): пакетов/с, соединений, реконнекты, CRC-ошибки. Последний
  пакет — hex моноширинно по 2 символа с разбивкой на NPL (15 байт) | NPH (10 байт) | ячейки,
  рядом таблица `fields`. Жюри так видит, что мы реально парсим NDTP.
- **Ссылки:** Swagger (`/docs`), документация кода (`/code-docs`).

**ReplayControls** в TopBar: ⏸/▶ и скорость ×10 / ×30 / ×60 через `POST /replay/control`.

Ещё в P1: плавная интерполяция позиций ТС между апдейтами (lerp за 1 с) и фильтр карты по
клику на счётчик риска.

### P2 — если останется время

- **«Нитка графика» (Marey-диаграмма)** для ТС: X — время, Y — остановки по порядку, линии
  «план», «факт по GPS», «прогноз». Классика диспетчерской — сильный вау-эффект.
- Пульсирующий ореол красных маркеров, звук при новом красном инциденте (с выключателем).
- What-if панель: контракт появится позже, **не начинать без него**.

---

## 8. Производительность

- На моках 17 ТС, в нагрузочном тесте бэкенда до ~300. Обновления идут раз в секунду.
- **Не ре-рендерить React на каждый тик.** ТС держать в Zustand, GeoJSON для карты
  собирать вне React и обновлять источник напрямую:
  `map.getSource("vehicles").setData(featureCollection)`. Делать это в `subscribe` store,
  батчить через `requestAnimationFrame`.
- Ленту инцидентов рендерить из селектора, который меняется только при событиях `incident.*`.
- Сеть (`/network`) — один статичный источник, данные не менять.
- Анимировать только `transform` / `opacity`.
- Цель: 60 fps на карте при 300 ТС, первый экран < 2 с.

---

## 9. Хелперы: время и форматирование

```ts
// lib/time.ts — время датасета без часового пояса, показываем как есть
const asUtc = (naive: string) => new Date(naive + "Z");
export const formatSimTime = (naive: string, withSeconds = false) =>
  asUtc(naive).toLocaleTimeString("ru-RU", {
    timeZone: "UTC", hour: "2-digit", minute: "2-digit", ...(withSeconds && { second: "2-digit" }),
  });
export const minutesBetween = (fromNaive: string, toNaive: string) =>
  (asUtc(toNaive).getTime() - asUtc(fromNaive).getTime()) / 60000;

// lib/format.ts — как на бэкенде: "+2 мин 30 с", "−45 с", "+2 мин"
export function formatDelay(sec: number): string {
  const sign = sec >= 0 ? "+" : "−";
  const total = Math.round(Math.abs(sec));
  const m = Math.floor(total / 60), s = total % 60;
  if (m === 0) return `${sign}${s} с`;
  return s ? `${sign}${m} мин ${s} с` : `${sign}${m} мин`;
}
// «через 9 мин» — minutesBetween(simTime, target.planned_at), округлять вниз; ≤ 0 → «сейчас»
```

---

## 10. Ошибки и пустые состояния

| Ситуация | Поведение |
|---|---|
| WS не подключился или отвалился | ConnectionBanner, данные остаются последними известными, backoff-переподключение |
| `status.mode = DEGRADED` | Красный баннер с `reason`. Маркеры `stale` полупрозрачные. Инциденты остаются |
| `OFFLINE` | Серый баннер «Нет данных от источника» |
| `/network` не загрузился | Карта без линий и toast «Сеть маршрутов недоступна», ТС всё равно рисуются |
| REST 4xx/5xx | Toast с `detail`, компонент показывает «Не удалось загрузить» и кнопку «Повторить» |
| ТС без координат (`lon == null`) | Не рисовать на карте, но учитывать в счётчиках |
| Пустая лента | «Инцидентов нет — все ТС в графике» |

---

## 11. Docker

Сборка из **корня репозитория**: фронту нужен `contracts/ts/contract.ts`.

`dashboard/Dockerfile`:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /repo/dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY contracts/ts /repo/contracts/ts
COPY dashboard/ ./
RUN npm run build

FROM nginx:1.27-alpine
ENV BACKEND_UPSTREAM=backend:8000
COPY dashboard/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/dashboard/dist /usr/share/nginx/html
EXPOSE 8080
```

`dashboard/nginx.conf.template` (nginx подставит `${BACKEND_UPSTREAM}` сам):

```nginx
server {
  listen 8080;
  root /usr/share/nginx/html;

  location / { try_files $uri /index.html; }

  location /api/ { proxy_pass http://${BACKEND_UPSTREAM}; }

  location /ws/ {
    proxy_pass http://${BACKEND_UPSTREAM};
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
  }

  location ~ ^/(docs|redoc|openapi\.json|code-docs) { proxy_pass http://${BACKEND_UPSTREAM}; }
}
```

В `docker-compose.yml` сервис назовём `dashboard`, порт `8080:8080`. Это сделаем мы, тебе
достаточно, чтобы `docker build -f dashboard/Dockerfile .` собирался. Если Docker у тебя
не стоит — проверим на нашей машине.

---

## 12. Приёмка

Перед каждой вехой пройди чек-лист и приложи скриншоты 1440×900 и 1920×1080 в MR.

- [ ] Открыл страницу, ничего не читая, за 5 секунд понятно: сколько ТС в красной зоне,
      где они на карте, какой инцидент самый срочный.
- [ ] Карта: маршруты, ТС с курсом, цвет **и** форма по риску, проблемные перегоны выделены.
- [ ] Карточка инцидента: ТС, прогноз опоздания (± ошибка, вероятность), причина с
      доказательствами, участок на карте, рекомендации с «Принять», «алерт за N мин».
- [ ] Данные обновляются сами. На смене сессии (конец записи моков) нет дублей и мусора.
- [ ] `POST /mock/status {"mode":"DEGRADED"}` → баннер. `{"mode":"AUTO"}` → баннер ушёл.
- [ ] Выключил mock-сервер → «Нет связи с сервером». Включил → восстановилось само.
- [ ] Время везде совпадает с `sim_time` (07:xx, а не 10:xx или 04:xx).
- [ ] Нет хардкода порогов и цветов риска — всё из `/config`.
- [ ] Консоль браузера без ошибок. `npm run build` проходит, `tsc` без ошибок.

---

## 13. Как работаем

- **Код.** Только в `dashboard/`. `contracts/` не редактируешь. Если в контракте чего-то не
  хватает, пиши нам: добавим в pydantic, перегенерируем типы и моки.
- **Git.**
  - Ветка на задачу (`feat/dashboard-map`, `feat/dashboard-incident-card`, …), MR в `main`.
  - Коммиты маленькие, по одному изменению: `feat(dashboard): карточка инцидента`.
    Заголовок в conventional-формате, тело — зачем правка.
- **Моки перегенерируются** из живого пайплайна 26.09 ночью. Формат тот же (контракт
  неизменен), но значения станут реальными.
- **Живой бэкенд.** С 26.09 ~20:00 дадим URL. Запуск: `VITE_BACKEND=<url> npm run dev`.

## 14. Демо для жюри (под это и приоритизируем)

1. Открываем пульт: сверху «🔺 1», на карте красный треугольник, в ленте первый инцидент
   «+4 мин 20 с, через 11 мин».
2. Клик по инциденту: карточка — затор на перегоне, доказательства, участок подсвечен на
   карте, «алерт создан за 11.8 мин до события». Жмём «Принять: Приоритет на светофорах».
3. Клик по ТС: drawer с графиком отклонения и прогнозом с интервалом.
4. Обрываем поток: красный баннер DEGRADED, маркеры тускнеют, система жива. Возвращаем
   поток — баннер уходит сам.
5. «Система»: латентность, упреждение 100% ≥ 10 мин, последний NDTP-пакет в hex.

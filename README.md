# Transit Pulse

Система раннего предупреждения о задержках городского транспорта. Она принимает поток телеметрии
NDTP, накладывает его на нитку графика, за **10–15 минут** до прибытия прогнозирует задержку ТС на
остановке и выводит алерт с причиной и рекомендациями на дашборд диспетчера.

```
 replayer / эмулятор ──NDTP TCP──► backend :8000 (+ NDTP :9201) ──HTTP──► ml :8001
                                        │  REST /api/v1 + WebSocket
                                        ▼
                                  dashboard :8080
```

## Запуск (Docker)

Нужны Docker с Compose v2 и архив датасета организаторов.

1. Положите архив датасета (`Предиктор задержек транспорта.zip` или `dataset.zip`) в каталог `./data`.
2. Поднимите систему:

   ```bash
   docker compose up -d --build
   ```

   Сервис `data-init` сам распакует архив в `./data/raw`, затем по healthcheck поднимутся `ml`,
   `backend`, `replayer` и `dashboard`. Replayer стартует автоматически: прогревает 30 сим-минут и
   проигрывает день 06.01.2026 с 07:00 со скоростью ×30.
3. Откройте дашборд: **http://localhost:8080**. Через минуту на карте двигаются ТС, а в ленте
   появляются инциденты с упреждением 10–15 минут.

Если порт занят, задайте другой в `.env` (пример — `.env.example`), например `DASHBOARD_PORT=8088`.

**Живой поток с официального эмулятора NDTP** (дополнительно):

```bash
docker load -i data/raw/ndtp-telemetry-emulator.tar
docker compose --profile emulator up -d
```

Борта эмулятора появятся на карте серыми как «неопознанные» (их нет в расписании), а на
`/api/v1/ingest/stats` будет виден последний раскодированный пакет (hex и поля).

## Что где смотреть

| Что | Где |
|---|---|
| Дашборд диспетчера | http://localhost:8080 |
| Метрики, качество на потоке, последний NDTP-пакет | кнопка «Система» на дашборде |
| Swagger backend (REST API) | http://localhost:8000/docs |
| Swagger ML-сервиса | http://localhost:8001/docs |
| Документация по коду (Sphinx) | http://localhost:8000/code-docs/ |
| Метрики Prometheus | http://localhost:8000/metrics, http://localhost:8001/metrics |
| Управление потоком (пауза, скорость, перемотка) | `POST /api/v1/replay/control`, статус — http://localhost:8090/status |

Подробный сценарий проверки — [docs/JURY_GUIDE.md](docs/JURY_GUIDE.md).

## Модули

| Модуль | Роль | Код |
|---|---|---|
| **backend** | Приём и парсинг NDTP (TCP :9201), состояние флота, сим-часы, сопоставление с расписанием, производные признаки (отклонение, скорость на перегоне, простой), оркестрация прогнозов, инциденты, REST и WebSocket | `services/backend` |
| **ml** | ML-ядро: обучение (CatBoost, CV) и stateless-инференс `/predict` с квантилями, вероятностью опоздания и вкладами признаков | `services/ml` |
| **dashboard** | BI-дашборд диспетчера (React, MapLibre) | `dashboard` |
| replayer | Источник потока: проигрывает исторический день как NDTP-трафик | `services/replayer` |
| transit_core | Общее ядро: NDTP-кодек, расписание, признаки на момент T, стоп-детектор, причины, контракт API | `packages/transit_core` |

## Документы

- [JURY_GUIDE](docs/JURY_GUIDE.md) — как подать поток, где увидеть прогнозы, алерты и метрики.
- [PERFORMANCE](docs/PERFORMANCE.md) — латентность, пропускная способность, деградация, холодный старт.
- [FEATURES](docs/FEATURES.md) — реализованные дополнительные возможности.
- [DATA_AUDIT](docs/DATA_AUDIT.md) — аудит данных: найденная утечка и почему мы её не используем.
- [SUBMISSIONS](docs/SUBMISSIONS.md) — журнал сабмитов.
- [INTERFACES](docs/INTERFACES.md) — договор между модулями.

## Разработка без Docker

```bash
uv sync --all-groups                               # окружение Python 3.12+
uv run python -m scripts.data_prep                 # распаковать ./data/*.zip в ./data/raw
uv run pytest -q                                   # тесты
uv run ruff check                                  # линтер
uv run python -m services.ml.app.train             # CV и обучение моделей в ./models
uv run python -m scripts.make_submission           # сабмит по validate
node contracts/mock-server/server.mjs              # mock-backend для фронта на :8000
```

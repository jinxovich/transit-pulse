"""Примеры тела ``POST /predict`` для Swagger «Try it out».

Признаки — реальные точки validate (``data/cache/validate.pkl``, округлены до сотых):
ТС 122048 в T=2026-01-06 04:30, цель через 11 мин, текущее отклонение ≈ +2:48;
ТС 130238 в T=07:50, цель через 12 мин. Порядок ключей совпадает с ``FEATURES``.
"""

from __future__ import annotations

from transit_core.features import FEATURES

VEHICLE_122048 = {
    "cur_dev": 167.56, "lead": 11.0, "hour_sin": 0.92, "hour_cos": 0.38, "tgt_manual": 0.0,
    "tgt_newtrip": 0.0, "tgt_gap": 2.0, "n_between": 7.0, "trip_break_between": 0.0,
    "max_gap_between": 3.0, "plan_run": 13.0, "since_last_plan": 2.0, "trip_progress": 0.35,
    "stale": 5.0, "stale_valid": 10.0, "invalid15": 0.09, "spd_last": 31.0, "spd1": 14.2,
    "spd5": 11.74, "spd15": 6.15, "stop5": 0.26, "dwell": 0.0, "gps_dev": 179.02,
    "gps_dist": 2.24, "gps_dev_trend": 32.59, "dist_tgt": 3135.95, "remain_m": 3675.64,
    "eta_dev": 1492.62,
}  # fmt: skip

VEHICLE_130238 = {
    "cur_dev": 127.0, "lead": 12.0, "hour_sin": 0.89, "hour_cos": -0.46, "tgt_manual": 0.0,
    "tgt_newtrip": 0.0, "tgt_gap": 3.0, "n_between": 9.0, "trip_break_between": 0.0,
    "max_gap_between": 3.0, "plan_run": 12.0, "since_last_plan": 0.0, "trip_progress": 0.55,
    "stale": 3.0, "stale_valid": 3.0, "invalid15": 0.09, "spd_last": 0.0, "spd1": 26.0,
    "spd5": 15.94, "spd15": 10.1, "stop5": 0.28, "dwell": 0.0, "gps_dev": 92.05,
    "gps_dist": 18.61, "gps_dev_trend": 67.95, "dist_tgt": 3242.46, "remain_m": 3285.62,
    "eta_dev": 451.49,
}  # fmt: skip

# Без GPS-признаков: сервис подставит NaN, модель отработает на расписании и cur_dev.
_NO_GPS = {k: v for k, v in VEHICLE_130238.items() if k in FEATURES[:13]}

PREDICT_EXAMPLES = {
    "stream": {
        "summary": "Один визит, поток (с вкладами признаков)",
        "description": "Как запрос бэкенда в проходе прогнозов: `cur_dev` посчитан по GPS.",
        "value": {
            "model": "stream",
            "explain": True,
            "items": [{"id": "122048:53699018729", "features": VEHICLE_122048}],
        },
    },
    "batch": {
        "summary": "Батч из двух точек, подсказка организаторов, без SHAP",
        "description": "`submission` — `cur_dev` из `points.csv`. У второй точки нет "
        "GPS-признаков: отсутствующие ключи и `null` превращаются в NaN.",
        "value": {
            "model": "submission",
            "explain": False,
            "items": [
                {"id": "122048:53699018729", "features": {**VEHICLE_122048, "cur_dev": 163.0}},
                {"id": "130238:53698370378", "features": _NO_GPS},
            ],
        },
    },
}

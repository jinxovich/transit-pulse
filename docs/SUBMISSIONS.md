# Журнал сабмитов

Платформа засчитывает **лучший** результат за всё время; лимит — 24 успешных и 36 всего
загрузок в сутки. Score на validate: `(mae0 − MAE) / (mae0 − MAE_T)`; по нашей оценке
mae0 ≈ 108.1, baseline `cur_dev_s` ≈ 88.7, MAE_T ≈ 59.6, то есть score 0.7 ↔ MAE ≈ 74.

Модель для питча выбираем по CV, а не по лидерборду: на 151 точке шум ≈ ±0.1 score.

| Версия | Файл | Модель | Локально (test MAE) | Score на платформе | Дата |
|---|---|---|---|---|---|
| v0 | `submissions/sub_v0_baseline.csv` | `cur_dev_s` (baseline организаторов) | 93.36 | — | 25.09 |
| v1 | `submissions/sub_v1_lgbm.csv` | LightGBM L1 на остатке, 17 признаков, синтетика с весом 0.5, 5 seed | 63.62 (−31.9%) | — | 25.09 |
| v2 | `submissions/sub_v2_catboost.csv` | CatBoost MultiQuantile + LightGBM L1 (бленд 0.5/0.5 по OOF) на остатке, 28 признаков `transit_core.features`, синтетика 0.5 | CV 3×5: 66.42 честно (clone guard), CatBoost 52.10 наивно; v1 на тех же фолдах 69.44 / 62.37; baseline 88.38 | — | 27.09 |

Score на платформе вписываем после загрузки.

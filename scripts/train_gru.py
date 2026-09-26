"""CV, финальное обучение и экспорт GRU-модели задержки.

CV: повторяемый GroupKFold (3×5) по группе ``(tr_id, floor(T, 30 мин))`` на реальных
размеченных точках train+test. Синтетика (вес ``--synth-weight``) добавляется только в
обучающие фолды; оценка — только на реальных ТС. Повторы отличаются перестановкой групп.

Артефакты: ``models/oof_gru.csv``, ``models/gru.onnx``, ``models/gru_metrics.json``,
``models/pred_validate_gru.csv``.

Запуск::

    uv run python -m scripts.train_gru
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.ensemble_weights import ensemble_section
from services.ml.app import seq_onnx
from services.ml.app.seq_data import N_FOLDS, cached, fold_ids, load_labeled, load_validate
from services.ml.app.seq_model import TrainConfig, device, fit, fit_predict, predict
from transit_core.sequence import SEQ_FEATURES, STATIC_FEATURES

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW = REPO_ROOT / "data" / "raw"
CACHE = REPO_ROOT / "data" / "cache"
MODELS = REPO_ROOT / "models"
PARITY_TOL = 1e-3


def _targets(pts: pd.DataFrame, synth_weight: float):
    y = pts["target_delay_s"].to_numpy(float)
    cur = pts["cur_dev_s"].to_numpy(float)
    w = np.where(pts["is_real"], 1.0, synth_weight)
    return y, cur, w


def run_cv(ds, args, cfg: TrainConfig) -> pd.DataFrame:
    """OOF-прогнозы на реальных точках для всех повторов CV."""
    pts = ds.points
    real = pts["is_real"].to_numpy(bool)
    y, cur, w = _targets(pts, args.synth_weight)
    use_synth = args.synth_weight > 0
    rows = []
    for rep in range(args.repeats):
        folds = np.full(len(pts), -1)
        folds[real] = fold_ids(pts.loc[real, "group"], rep)
        for f in range(N_FOLDS):
            va = real & (folds == f)
            tr = (real & (folds != f)) | (~real if use_synth else np.zeros_like(real))
            seeds = [100 * rep + 10 * f + s for s in range(args.seeds)]
            resid = fit_predict(
                (ds.seq[tr], ds.static[tr], y[tr] - cur[tr], w[tr]),
                (ds.seq[va], ds.static[va]),
                seeds,
                cfg,
            )
            rows.append(pd.DataFrame({
                "sample_id": pts.loc[va, "sample_id"].to_numpy(),
                "fold_repeat": rep,
                "fold": f,
                "oof_pred": cur[va] + resid,
                "y": y[va],
                "cur_dev_s": cur[va],
            }))
        done = pd.concat(rows)
        done = done[done["fold_repeat"] == rep]
        mae = (done["y"] - done["oof_pred"]).abs().mean()
        print(f"repeat {rep}: OOF-MAE {mae:.2f}", flush=True)
    return pd.concat(rows, ignore_index=True)


def cv_summary(oof: pd.DataFrame) -> dict:
    """MAE по повторам, среднее и baseline ``cur_dev_s`` (только реальные ТС)."""
    per_rep = oof.groupby("fold_repeat").apply(
        lambda d: float((d["y"] - d["oof_pred"]).abs().mean()), include_groups=False
    )
    base = float((oof["y"] - oof["cur_dev_s"]).abs().mean())
    mean = float(per_rep.mean())
    return {
        "n_real_points": int(oof["sample_id"].nunique()),
        "oof_mae_baseline_cur_dev": round(base, 2),
        "oof_mae_gru": round(mean, 2),
        "oof_mae_gru_per_repeat": [round(v, 2) for v in per_rep],
        "oof_mae_gru_std": round(float(per_rep.std(ddof=0)), 2),
        "improvement_vs_baseline": round(1 - mean / base, 4),
    }


def final_and_export(ds, val, args, cfg: TrainConfig) -> dict:
    """Финальный ансамбль на всех данных → ONNX, паритет, латентность, прогноз validate."""
    y, cur, w = _targets(ds.points, args.synth_weight)
    keep = ds.points["is_real"].to_numpy(bool) | (args.synth_weight > 0)
    train = (ds.seq[keep], ds.static[keep], (y - cur)[keep], w[keep])
    models = [fit(*train, seed=9000 + s, cfg=cfg) for s in range(args.final_seeds)]
    resid_torch = np.mean([predict(m, val.seq, val.static) for m in models], axis=0)

    onnx_path = MODELS / "gru.onnx"
    ens = seq_onnx.export(models, (val.seq, val.static), onnx_path)
    sess = seq_onnx.session(onnx_path)
    diff = seq_onnx.parity(ens, sess, val.seq, val.static)
    if diff >= PARITY_TOL:
        raise RuntimeError(f"ONNX расходится с torch: max|diff| = {diff}")
    pred_onnx = seq_onnx.onnx_predict(sess, val.seq, val.static)
    pred_torch = val.points["cur_dev_s"].to_numpy(float) + resid_torch
    pred = pd.DataFrame({"sample_id": val.points["sample_id"], "pred": np.round(pred_onnx, 3)})
    pred.to_csv(MODELS / "pred_validate_gru.csv", index=False)
    return {
        "onnx": {
            "path": "models/gru.onnx",
            "opset": seq_onnx.OPSET,
            "inputs": {
                "seq": ["batch", *ds.seq.shape[1:]],
                "static": ["batch", ds.static.shape[1]],
            },
            "output": "delay_s (абсолютная задержка, сек)",
            "n_models_averaged": args.final_seeds,
            "parity_max_abs_diff_s": diff,
            "torch_vs_onnx_validate_max_abs_diff_s": float(np.max(np.abs(pred_torch - pred_onnx))),
        },
        "latency_onnxruntime": seq_onnx.bench(onnx_path, val.seq, val.static),
        "validate": {"n": int(len(pred)), "mean_pred_s": round(float(pred_onnx.mean()), 2)},
    }


def _merge_metrics(key: str, value: dict) -> None:
    """Дописывает раздел в существующий ``models/gru_metrics.json``."""
    path = MODELS / "gru_metrics.json"
    metrics = json.loads(path.read_text("utf-8")) if path.exists() else {}
    path.write_text(json.dumps({**metrics, key: value}, ensure_ascii=False, indent=2), "utf-8")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--seeds", type=int, default=3, help="seed'ов на фолд")
    p.add_argument("--final-seeds", type=int, default=5)
    p.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    p.add_argument("--synth-weight", type=float, default=0.5)
    p.add_argument("--skip-final", action="store_true")
    p.add_argument(
        "--report-only", action="store_true",
        help="без обучения: пересчитать латентность gru.onnx и ансамбль в gru_metrics.json",
    )
    p.add_argument("--oof-name", default="oof_gru.csv")
    p.add_argument("--metrics-key", help="с --skip-final: дописать сводку CV в gru_metrics.json")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = TrainConfig(epochs=args.epochs)
    t0 = time.time()
    ds = cached(CACHE / "seq_labeled", lambda: load_labeled(RAW))
    val = cached(CACHE / "seq_validate", lambda: load_validate(RAW))
    print(f"данные: {len(ds.points)} точек ({ds.points['is_real'].sum()} реальных), "
          f"validate {len(val.points)}; {time.time() - t0:.0f} c; device={device()}", flush=True)

    if args.report_only:
        lat = seq_onnx.bench(MODELS / "gru.onnx", val.seq, val.static)
        _merge_metrics("latency_onnxruntime", lat)
        for key, value in ensemble_section(MODELS / args.oof_name, MODELS).items():
            _merge_metrics(key, value)
        return 0
    oof = run_cv(ds, args, cfg)
    MODELS.mkdir(exist_ok=True)
    cols = ["sample_id", "fold_repeat", "oof_pred", "y"]
    oof[cols].round(3).to_csv(MODELS / args.oof_name, index=False)
    summary = cv_summary(oof)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if args.skip_final:
        if args.metrics_key:
            _merge_metrics(args.metrics_key, {"synth_weight": args.synth_weight, **summary})
        return 0

    metrics = {
        "model": "gru_seq",
        "config": {**cfg.__dict__, "seeds_per_fold": args.seeds, "synth_weight": args.synth_weight},
        "features": {"sequence": list(SEQ_FEATURES), "static": list(STATIC_FEATURES)},
        "cv": {"scheme": f"GroupKFold({N_FOLDS}) x {args.repeats} по (tr_id, floor(T,30мин))",
               **summary},
        **final_and_export(ds, val, args, cfg),
        **ensemble_section(MODELS / args.oof_name, MODELS),
    }
    path = MODELS / "gru_metrics.json"
    old = json.loads(path.read_text("utf-8")) if path.exists() else {}
    metrics = {**{k: v for k, v in old.items() if k.startswith("cv_")}, **metrics}
    (MODELS / "gru_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps({k: metrics[k] for k in ("onnx", "latency_onnxruntime",
                                                "ensemble", "gain")}, ensure_ascii=False))
    print(f"готово за {time.time() - t0:.0f} c")
    return 0


if __name__ == "__main__":
    sys.exit(main())

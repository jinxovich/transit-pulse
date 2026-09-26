"""Генератор цели воспроизводит разметку организаторов.

Если на одну плановую минуту приходится несколько визитов (ничья), организаторы выбирают
визит без устойчивого правила: min/max id, порядок строк и факт дают по ~50%. Поэтому для
ничьих проверяется, что метка — один из визитов этой минуты, а для остальных точек — точное
совпадение.
"""

import numpy as np
import pandas as pd
import pytest

from tests.conftest import require_raw
from transit_core.labels import load_schedule_with_fact, make_labels, target_class

CUR_DEV_MIN_EXACT = 0.95


def _tied(sched: pd.DataFrame, labels: pd.DataFrame) -> pd.Series:
    """Флаг: у целевой плановой минуты больше одного визита."""
    counts = sched.groupby(["tr_id", "tb"]).size().rename("n").reset_index()
    ttb = pd.to_datetime(labels["target_time_begin"], format="ISO8601")
    key = pd.DataFrame({"tr_id": labels["tr_id"].to_numpy(), "tb": ttb.to_numpy()})
    return pd.Series(key.merge(counts, how="left")["n"].to_numpy() > 1, index=labels.index)


@pytest.mark.parametrize("split,file", [("train", "labels_train.csv"),
                                        ("test", "labels_test.csv")])
def test_reproduces_organizer_labels(split, file):
    labels = pd.read_csv(require_raw("labels", file))
    sched = load_schedule_with_fact(require_raw(split, "schedule.csv"))

    got = make_labels(sched, labels)

    ttb = pd.to_datetime(labels["target_time_begin"], format="ISO8601")
    assert (got["target_time_begin"] == ttb).all()
    tied = _tied(sched, labels)
    plain = ~tied
    assert (got.loc[plain, "target_stop_id"] == labels.loc[plain, "target_stop_id"]).all()
    assert (got.loc[plain, "target_delay_s"] == labels.loc[plain, "target_delay_s"]).all()
    by_visit = sched.set_index("visit_id")
    lab_tb = by_visit.loc[labels.loc[tied, "target_stop_id"], "tb"].to_numpy()
    assert (lab_tb == ttb[tied].to_numpy()).all()
    exact_cd = np.isclose(got["cur_dev_s"], labels["cur_dev_s"]).mean()
    assert exact_cd >= CUR_DEV_MIN_EXACT


def test_target_class_thresholds():
    assert target_class(-61) == "early"
    assert target_class(-60) == "ontime"
    assert target_class(120) == "ontime"
    assert target_class(121) == "late"

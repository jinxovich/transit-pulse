from datetime import timedelta

import numpy as np

from tests.conftest import START, TRUE_DELAY_S
from transit_core.stops_detector import detect_arrivals, online_cur_dev

TOL_S = 12.0


def test_detects_every_stop_with_known_delay(synth_plan, synth_track):
    until = START + timedelta(minutes=30)

    arr = detect_arrivals(synth_plan, synth_track, until)

    assert arr["visit_id"].tolist() == synth_plan["visit_id"].tolist()
    assert np.abs(arr["delay_s"] - TRUE_DELAY_S).max() < TOL_S
    assert arr["actual_at"].is_monotonic_increasing


def test_only_visits_planned_before_until(synth_plan, synth_track):
    until = START + timedelta(minutes=4, seconds=45)

    arr = detect_arrivals(synth_plan, synth_track, until)

    assert set(arr["visit_id"]) <= {1000, 1001, 1002, 1003, 1004}
    assert (arr["actual_at"] <= np.datetime64(until)).all()


def test_ignores_future_track(synth_plan, synth_track):
    until = START + timedelta(minutes=5, seconds=10)
    future = synth_track["et"] > np.datetime64(until)
    broken = synth_track.copy()
    broken.loc[future, ["lon", "lat"]] = 0.0

    a = detect_arrivals(synth_plan, synth_track, until)
    b = detect_arrivals(synth_plan, broken, until)

    assert a.equals(b)


def test_online_cur_dev_matches_true_delay(synth_plan, synth_track):
    t = START + timedelta(minutes=6, seconds=50)

    dev = online_cur_dev(synth_plan, synth_track, t)

    assert dev is not None and abs(dev - TRUE_DELAY_S) < TOL_S


def test_online_cur_dev_falls_back_to_last_detected_stop(synth_plan, synth_track):
    t = START + timedelta(minutes=6, seconds=20)  # план 08:06 прошёл, ТС приедет в 08:06:30

    dev = online_cur_dev(synth_plan, synth_track, t)

    assert dev is not None and abs(dev - TRUE_DELAY_S) < TOL_S


def test_online_cur_dev_none_before_plan(synth_plan, synth_track):
    assert online_cur_dev(synth_plan, synth_track, START - timedelta(minutes=1)) is None


def test_online_cur_dev_none_without_track(synth_plan, synth_track):
    assert online_cur_dev(synth_plan, synth_track.iloc[:0], START + timedelta(minutes=5)) is None

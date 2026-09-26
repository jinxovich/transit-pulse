"""Сим-часы replayer: скорость, прогрев, пауза, смена скорости."""

import pytest
from replayer.clock import SimClock
from replayer.config import Settings


class FakeWall:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def wall():
    return FakeWall()


def test_sim_runs_at_speed(wall):
    clock = SimClock(speed=30, warmup_speed=600, wall=wall)
    clock.start(0, None)
    wall.t += 2
    assert clock.now() == 60


def test_warmup_switches_to_main_speed_at_boundary(wall):
    clock = SimClock(speed=30, warmup_speed=600, wall=wall)
    clock.start(0, 1800)  # 30 мин прогрева на ×600 = 3 с wall
    wall.t += 3
    assert clock.now() == pytest.approx(1800)
    wall.t += 1
    assert clock.now() == pytest.approx(1830)
    assert clock.effective_speed == 600
    assert clock.advance() is True
    assert clock.effective_speed == 30 and clock.warmup_until is None
    wall.t += 1
    assert clock.now() == pytest.approx(1860)


def test_pause_freezes_and_resume_continues(wall):
    clock = SimClock(speed=10, warmup_speed=600, wall=wall)
    clock.start(100, None)
    wall.t += 1
    clock.pause()
    wall.t += 50
    assert clock.now() == 110 and clock.state == "paused"
    clock.resume()
    wall.t += 1
    assert clock.now() == 120


def test_set_speed_applies_immediately_and_cancels_warmup(wall):
    clock = SimClock(speed=30, warmup_speed=600, wall=wall)
    clock.start(0, 1800)
    wall.t += 1
    clock.set_speed(60)
    wall.t += 1
    assert clock.now() == 660 and clock.warmup_until is None


def test_settings_from_env():
    s = Settings.from_env(
        {
            "BACKEND_NDTP": "10.0.0.5:19201",
            "BACKEND_HTTP": "http://x:8000/",
            "REPLAY_START": "08:30",
            "REPLAY_SPEED": "60",
            "REPLAY_LOOP": "0",
            "FLEET_MULTIPLIER": "3",
        }
    )
    assert (s.ndtp_host, s.ndtp_port, s.backend_http) == ("10.0.0.5", 19201, "http://x:8000")
    assert (s.start.hour, s.start.minute, s.speed, s.loop) == (8, 30, 60.0, False)
    assert s.fleet_multiplier == 3 and s.warmup_speed == 600 and s.autostart


def test_settings_reject_bad_hostport():
    with pytest.raises(ValueError):
        Settings.from_env({"BACKEND_NDTP": "backend"})

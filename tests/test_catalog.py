import typing

from transit_core.catalog import CAUSES, format_delay, make_cause, recommendations_for
from transit_core.schemas import CauseCode


def test_every_cause_code_has_text_and_recommendations():
    for code in typing.get_args(CauseCode):
        assert code in CAUSES
        assert recommendations_for(code)


def test_make_cause_fills_title_from_catalog():
    cause = make_cause("CONGESTION", [])

    assert cause.title == "Затор на перегоне"
    assert cause.evidence == []


def test_format_delay_is_human_readable():
    assert format_delay(150) == "+2 мин 30 с"
    assert format_delay(-45) == "−45 с"
    assert format_delay(120) == "+2 мин"
    assert format_delay(0) == "+0 с"

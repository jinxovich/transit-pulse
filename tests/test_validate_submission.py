from pathlib import Path

from scripts.validate_submission import validate_submission

POINTS = (
    "sample_id,tr_id,T,target_stop_id,target_time_begin,cur_dev_s\na_1,1,t,1,t,0\nb_2,2,t,2,t,0\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_submission_has_no_errors(tmp_path):
    points = _write(tmp_path, "points.csv", POINTS)
    sub = _write(tmp_path, "sub.csv", "sample_id;prediction\na_1;12.5\nb_2;-30\n")

    assert validate_submission(sub, points) == []


def test_reports_wrong_separator(tmp_path):
    points = _write(tmp_path, "points.csv", POINTS)
    sub = _write(tmp_path, "sub.csv", "sample_id,prediction\na_1,1\nb_2,2\n")

    errors = validate_submission(sub, points)

    assert any("sample_id;prediction" in e for e in errors)


def test_reports_missing_duplicate_and_nan(tmp_path):
    points = _write(tmp_path, "points.csv", POINTS)
    sub = _write(tmp_path, "sub.csv", "sample_id;prediction\na_1;1\na_1;2\nx_9;nan\n")

    errors = " | ".join(validate_submission(sub, points))

    assert "дубли" in errors
    assert "b_2" in errors
    assert "x_9" in errors
    assert "NaN" in errors

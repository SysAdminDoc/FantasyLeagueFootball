"""CLI smoke tests — the one place cli.py is actually imported and executed."""

from __future__ import annotations

import pytest

from fantasyleague import __version__, cli


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert f"FantasyLeagueFootball v{__version__}" in capsys.readouterr().out


def test_list_filters_by_position(capsys):
    code, out, _ = run(capsys, "list", "--pos", "K", "--limit", "3")
    assert code == 0
    assert "Brandon Aubrey" in out
    assert "Jahmyr Gibbs" not in out


def test_values_lists_do_not_draft(capsys):
    code, out, _ = run(capsys, "values")
    assert code == 0
    assert "Do not draft" in out and "Justin Herbert" in out


def test_next_by_names_and_ranks(capsys):
    code, out, _ = run(capsys, "next", "--drafted", "gibbs", "bijan", "3", "--limit", "2")
    assert code == 0
    assert "Puka Nacua" in out and "Jahmyr Gibbs" not in out


def test_next_ambiguous_name_fails_cleanly(capsys):
    code, _, err = run(capsys, "next", "--drafted", "brown")
    assert code == 1
    assert "ambiguous" in err and "Brown" in err


def test_next_with_slot_prints_odds(capsys):
    code, out, _ = run(capsys, "next", "--teams", "12", "--slot", "5", "--limit", "3")
    assert code == 0
    assert "Pick 1 · yours in 4 · your next picks: 5, 20, 29" in out
    assert "odds of surviving to pick 5 / 20" in out
    assert "%" in out and "CALL" in out


def test_next_with_slot_on_your_pick(capsys):
    code, out, _ = run(capsys, "next", "--slot", "5", "--pick", "5", "--limit", "1")
    assert code == 0
    assert "Pick 5 · your pick now" in out


def test_build_writes_file_with_league_and_slot(capsys, tmp_path):
    out_path = tmp_path / "b.html"
    argv = ["build", "-o", str(out_path), "--league", "Test", "--teams", "10", "--slot", "3"]
    code, out, _ = run(capsys, *argv)
    assert code == 0 and out_path.exists()
    assert "Built" in out
    html = out_path.read_text("utf-8")
    assert '"draft": {"teams": 10, "slot": 3}' in html


def test_export_writes_csv_to_stdout(capsys):
    code, out, _ = run(capsys, "export", "--pos", "TE", "--limit", "2")
    assert code == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0] == "rank,name,pos,team,tier,flag,adp,adp_sd,bye,note"
    assert lines[1].startswith("17,Brock Bowers,TE,LV,3,")
    assert len(lines) == 3


def test_export_writes_a_file_and_quotes_commas(capsys, tmp_path):
    out_path = tmp_path / "board.csv"
    code, out, _ = run(capsys, "export", "-o", str(out_path))
    assert code == 0 and "Wrote" in out
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("rank,name,pos,team,tier,flag,adp,adp_sd,bye,note")
    # A note containing a comma must be quoted, not split into extra columns.
    import csv as _csv

    rows = list(_csv.reader(text.splitlines()))
    assert all(len(r) == 10 for r in rows), "every row must have exactly 10 columns"
    assert len(rows) == 201  # header + 200 players


def test_export_filters_compose(capsys):
    code, out, _ = run(capsys, "export", "--flag", "value", "--pos", "RB")
    assert code == 0
    import csv as _csv

    rows = list(_csv.DictReader(out.splitlines()))
    assert rows and all(r["flag"] == "value" and r["pos"] == "RB" for r in rows)

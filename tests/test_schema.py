"""Dataset checking: every problem at once, each with a JSON pointer."""

from __future__ import annotations

import json

import pytest

from fantasyleague import board, cli, schema
from fantasyleague.models import SCHEMA_VERSION, Dataset


def base() -> dict:
    return {
        "schema_version": 1,
        "season": 2026,
        "scoring": "half_ppr",
        "format": "test",
        "updated": "2026-08-16",
        "tiers": [{"n": 1, "name": "T1", "range": "", "note": ""}],
        "players": [
            {"rank": 1, "name": "A", "pos": "RB", "team": "DET", "tier": 1},
            {"rank": 2, "name": "B", "pos": "WR", "team": "LAR", "tier": 1},
        ],
    }


def test_packaged_board_has_no_problems():
    assert schema.check(json.loads(cli._packaged_data_path().read_text(encoding="utf-8"))) == []


def test_minimal_dataset_passes():
    assert schema.check(base()) == []


def test_reports_missing_required_fields():
    raw = base()
    del raw["season"]
    del raw["players"][0]["team"]
    problems = schema.check(raw)
    assert "/season: required field is missing" in problems
    assert "/players/0/team: required field is missing" in problems


def test_reports_every_problem_not_just_the_first():
    raw = base()
    raw["players"][0]["pos"] = "P"
    raw["players"][1]["flag"] = "hot"
    raw["players"][1]["tier"] = 9
    problems = schema.check(raw)
    assert len(problems) == 3
    assert any("/players/0/pos" in p for p in problems)
    assert any("/players/1/flag" in p for p in problems)
    assert any("/players/1/tier" in p for p in problems)


def test_rank_order_and_duplicates():
    raw = base()
    raw["players"][1]["rank"] = 5
    assert any("expected 2 (ranks must run 1..N in order), got 5" in p for p in schema.check(raw))

    raw = base()
    raw["players"][1]["rank"] = 1
    assert any("duplicate rank 1" in p for p in schema.check(raw))


def test_duplicate_names_and_ids():
    raw = base()
    raw["players"][1]["name"] = "A"
    assert any("/players/1/name: duplicate player" in p for p in schema.check(raw))

    raw = base()
    raw["players"][0]["ids"] = {"sleeper": "7"}
    raw["players"][1]["ids"] = {"sleeper": 7}
    assert any("/players/1/ids/sleeper: duplicate id" in p for p in schema.check(raw))


def test_unknown_field_is_reported_not_raised(tmp_path):
    """`notes` for `note` used to be a TypeError traceback from both validate and build."""
    raw = base()
    raw["players"][0]["notes"] = "typo"
    problems = schema.check(raw)
    assert any("/players/0/notes: unknown field (did you mean 'note'?)" in p for p in problems)
    raw["tiers"][0]["nmae"] = "typo"
    assert any("/tiers/0/nmae: unknown field" in p for p in schema.check(raw))
    raw2 = base()
    raw2["extras"] = 1
    assert any("/extras: unknown field" in p for p in schema.check(raw2))


def test_null_where_an_object_or_text_belongs():
    raw = base()
    raw["players"][0]["ids"] = None
    assert any("/players/0/ids: must be an object, got NoneType" in p for p in schema.check(raw))

    raw = base()
    raw["players"][0]["name"] = 12
    assert any("/players/0/name: must be text" in p for p in schema.check(raw))

    raw = base()
    raw["season"] = "2026"
    assert any("/season: must be a year, got str" in p for p in schema.check(raw))


def test_null_injury_status_is_caught_before_it_blanks_the_page():
    raw = base()
    raw["injuries"] = [{"name": "X", "team": "SF", "severity": "out", "status": None}]
    assert any("/injuries/0/status: must be text, got NoneType" in p for p in schema.check(raw))


def test_non_http_source_url_is_rejected():
    raw = base()
    raw["sources"] = [{"label": "x", "url": "javascript:alert(1)"}]
    assert any("/sources/0/url: must be an http(s) URL" in p for p in schema.check(raw))
    raw["sources"] = [{"label": "x", "url": "https://example.com"}]
    assert not [p for p in schema.check(raw) if "/sources" in p]


def test_malformed_dataset_loads_as_a_readable_error(tmp_path):
    """`board.load` must not leak KeyError/TypeError tracebacks to the CLI."""
    import json as _json

    from fantasyleague import board as board_mod

    raw = base()
    del raw["season"]
    path = tmp_path / "no-season.json"
    path.write_text(_json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="missing the required field 'season'"):
        board_mod.load(path)

    raw = base()
    raw["players"][0]["notes"] = "typo"
    path = tmp_path / "unknown-key.json"
    path.write_text(_json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the expected shape"):
        board_mod.load(path)


def test_cli_reports_shape_errors_without_a_traceback(tmp_path, capsys):
    import json as _json

    raw = base()
    raw["players"][0]["notes"] = "typo"
    path = tmp_path / "bad.json"
    path.write_text(_json.dumps(raw), encoding="utf-8")
    assert cli.main(["validate", str(path)]) == 1
    assert cli.main(["--data", str(path), "build", "-o", str(tmp_path / "b.html")]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "unknown field" in err


def test_unknown_id_source_and_bad_numbers():
    raw = base()
    raw["players"][0]["ids"] = {"nfl": "x"}
    raw["players"][0]["adp"] = 0
    raw["players"][1]["bye"] = 30
    problems = schema.check(raw)
    assert any("/players/0/ids/nfl: unknown id source" in p for p in problems)
    assert any("/players/0/adp" in p for p in problems)
    assert any("/players/1/bye" in p for p in problems)


def test_rail_rows_are_checked():
    raw = base()
    raw["injuries"] = [{"name": "A", "team": "DET", "severity": "bad", "status": "x"}, {"name": "B"}]
    problems = schema.check(raw)
    assert any("/injuries/0/severity" in p for p in problems)
    assert any("/injuries/1/team: required field is missing" in p for p in problems)


def test_future_schema_version_is_refused():
    raw = base()
    raw["schema_version"] = SCHEMA_VERSION + 1
    assert any("newer than this build understands" in p for p in schema.check(raw))
    with pytest.raises(ValueError, match="newer than this build"):
        Dataset.from_dict(raw)


def test_missing_schema_version_loads_as_v0():
    raw = base()
    del raw["schema_version"]
    assert schema.check(raw) == []
    assert Dataset.from_dict(raw).schema_version == SCHEMA_VERSION


def test_check_file_reports_parse_and_io_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert schema.check_file(bad)[0].startswith("/: not valid JSON")
    assert schema.check_file(tmp_path / "missing.json")[0].startswith("/: cannot read file")


def test_validate_command_exit_codes(capsys, tmp_path):
    assert cli.main(["validate"]) == 0
    assert "valid" in capsys.readouterr().out

    broken = tmp_path / "broken.json"
    raw = base()
    raw["players"][0]["pos"] = "P"
    broken.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(["validate", str(broken)]) == 1
    err = capsys.readouterr().err
    assert "1 problem" in err and "/players/0/pos" in err


def test_packaged_dataset_declares_its_version():
    assert board.load().schema_version == SCHEMA_VERSION
    raw = json.loads(cli._packaged_data_path().read_text(encoding="utf-8"))
    assert raw["schema_version"] == SCHEMA_VERSION


def test_minimal_dataset_actually_loads(tmp_path):
    """Whatever `validate` accepts, `load` must be able to open — rails are optional."""
    path = tmp_path / "minimal.json"
    path.write_text(json.dumps(base()), encoding="utf-8")
    data = board.load(path)
    assert len(data.players) == 2
    assert data.plan == [] and data.injuries == [] and data.sources == []
    assert cli.main(["--data", str(path), "validate"]) == 0


def test_bom_prefixed_dataset_is_accepted(tmp_path):
    """Notepad and PowerShell's Out-File write a BOM; that must not be an error."""
    path = tmp_path / "bom.json"
    path.write_text(json.dumps(base()), encoding="utf-8-sig")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert schema.check_file(path) == []
    assert len(board.load(path).players) == 2

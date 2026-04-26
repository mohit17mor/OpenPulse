import sys

from openpulse.scripts import (
    ScriptOutputError,
    extract_items,
    extract_scalar,
    parse_script_output,
    run_script,
    run_script_preview,
)


def test_parse_plain_text_output_as_whole_stdout():
    preview = parse_script_output("42\n")

    assert preview["outputType"] == "text"
    assert preview["nodes"] == [
        {"kind": "scalar", "path": "$stdout", "value": "42", "valueType": "number"}
    ]


def test_parse_json_discovers_scalar_and_array_paths():
    preview = parse_script_output(
        """
        {
          "btc": {"price": 7299207.91, "symbol": "BTC"},
          "items": [{"guid": "a", "title": "A"}, {"guid": "b", "title": "B"}]
        }
        """
    )

    nodes = preview["nodes"]
    assert {"kind": "scalar", "path": "btc.price", "value": 7299207.91, "valueType": "number"} in nodes
    assert {"kind": "scalar", "path": "btc.symbol", "value": "BTC", "valueType": "string"} in nodes
    assert {
        "kind": "array",
        "path": "items",
        "length": 2,
        "idFieldOptions": ["guid", "title"],
        "sample": {"guid": "a", "title": "A"},
    } in nodes


def test_extract_scalar_from_json_path():
    preview = parse_script_output('{"btc": {"price": 7299207.91}}')

    assert extract_scalar(preview, {"outputType": "json", "path": "btc.price"}) == "7299207.91"


def test_extract_scalar_reports_invalid_json_for_json_selection():
    preview = parse_script_output("not json")

    try:
        extract_scalar(preview, {"outputType": "json", "path": "btc.price"})
    except ScriptOutputError as exc:
        assert exc.reason == "script_invalid_json"
    else:
        raise AssertionError("Expected script_invalid_json")


def test_extract_items_requires_id_field():
    preview = parse_script_output('{"issues": [{"key": "PROJ-1", "summary": "Bug"}]}')

    items = extract_items(preview, {"arrayPath": "issues", "idField": "key"})

    assert items == [{"id": "PROJ-1", "item": {"key": "PROJ-1", "summary": "Bug"}}]


async def test_run_script_captures_stdout_and_stderr(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("import sys\nprint('ok')\nprint('warn', file=sys.stderr)\n")

    result = await run_script(sys.executable, [str(script)], str(tmp_path), 5)

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"
    assert result.stderr.strip() == "warn"
    assert result.timed_out is False


async def test_run_script_preview_reports_nonzero_exit(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("import sys\nprint('bad', file=sys.stderr)\nsys.exit(7)\n")

    preview = await run_script_preview(
        {"command": sys.executable, "args": [str(script)], "cwd": str(tmp_path), "timeoutSeconds": 5}
    )

    assert preview["ok"] is False
    assert preview["error"] == "script_failed"
    assert preview["execution"]["exitCode"] == 7
    assert preview["execution"]["stderr"].strip() == "bad"


async def test_run_script_preview_reports_launch_failure(tmp_path):
    missing_command = tmp_path / "missing-command"

    preview = await run_script_preview(
        {"command": str(missing_command), "args": [], "cwd": str(tmp_path), "timeoutSeconds": 5}
    )

    assert preview["ok"] is False
    assert preview["error"] == "script_failed"
    assert preview["execution"]["exitCode"] is None
    assert preview["execution"]["stderr"]


async def test_run_script_times_out(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(2)\n")

    result = await run_script(sys.executable, [str(script)], str(tmp_path), 1)

    assert result.timed_out is True
    assert result.exit_code is None

import json

from cost_per_task.cli import main

SAMPLE_LINE = (
    '{{"model": "{model}", "task_id": "t1", "success": {success}, '
    '"input_tokens": 1000000, "output_tokens": 0, "latency_s": 1.0}}\n'
)


def _write_log(tmp_path):
    log = tmp_path / "runs.jsonl"
    log.write_text(
        SAMPLE_LINE.format(model="gpt-5", success="true")
        + SAMPLE_LINE.format(model="gpt-5-mini", success="false")
        + SAMPLE_LINE.format(model="gpt-5-mini", success="true"),
        encoding="utf-8",
    )
    return log


def test_analyze_command_markdown(tmp_path, capsys):
    log = _write_log(tmp_path)
    assert main(["analyze", str(log)]) == 0
    out = capsys.readouterr().out
    assert "Cost / successful task" in out
    # gpt-5: $1.25 for one success. gpt-5-mini: 2 x $0.25 = $0.50 -> ranked first
    assert out.index("gpt-5-mini") < out.index("| 2 | gpt-5 |")


def test_analyze_command_json_output_file(tmp_path):
    log = _write_log(tmp_path)
    dest = tmp_path / "report.json"
    assert main(["analyze", str(log), "--format", "json", "--output", str(dest)]) == 0
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["models"][0]["model"] == "gpt-5-mini"
    assert payload["models"][0]["cost_per_successful_task_usd"] == 0.5


def test_prices_command(capsys):
    assert main(["prices"]) == 0
    out = capsys.readouterr().out
    assert "gpt-5-mini" in out and "claude-haiku-4-5" in out
    assert "as of" in out


def test_demo_command(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "gpt-5-mini" in out and "claude-haiku-4-5" in out and "gpt-5" in out


def test_missing_log_file_is_a_clean_error(tmp_path, capsys):
    assert main(["analyze", str(tmp_path / "nope.jsonl")]) == 2
    assert "error:" in capsys.readouterr().err

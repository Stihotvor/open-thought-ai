"""The latency-run harness (T5): fake providers only, WAV fixtures made in tmp_path."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_endpoint import make_client
from fakes import ENV, SPEECH, Endpoint

from ota_server import latency_run
from ota_server.audio import pcm_to_wav
from ota_server.latency_report import parse_records
from ota_server.latency_run import RunConfig, read_records, run
from ota_server.pipeline import SELF_TEST, VoicePipeline

IDS = ("p01", "p02", "p03")


def en_harmful(text: str) -> bool:
    return SELF_TEST["en"][0] in text


class ChatFails(Endpoint):
    """A fake endpoint whose chat fails (HTTP 400) on the given call numbers."""

    def __init__(self, failing: set[int], **kw: Any) -> None:
        super().__init__(**kw)
        self.failing = failing

    def __call__(self, req: httpx.Request) -> httpx.Response:
        chat = req.url.path.endswith("/chat/completions")
        if chat and len(self.chats) + 1 in self.failing:
            self.chats.append([])
            self.calls.append("completions")
            return httpx.Response(400)
        return super().__call__(req)


def endpoint(failing: set[int] | None = None) -> ChatFails:
    return ChatFails(
        failing or set(), transcript="Hello there.", reply="Hi. Nice day.", flag=en_harmful
    )


def pipeline(ep: Endpoint) -> VoicePipeline:
    return VoicePipeline(make_client(ep, ENV), "en", "voice-1")


def audio_dir(tmp_path: Path, ids: tuple[str, ...] = IDS) -> Path:
    root = tmp_path / "audio"
    (root / "en").mkdir(parents=True)
    for phrase_id in reversed(ids):  # creation order differs from id order
        (root / "en" / f"{phrase_id}.wav").write_bytes(pcm_to_wav(SPEECH))
    return root


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def config(tmp_path: Path, pause: float = 0, resume: bool = False) -> RunConfig:
    return RunConfig(
        audio_dir=audio_dir(tmp_path),
        lang="en",
        out=tmp_path / "out",
        pause=pause,
        resume=resume,
        run_id="run-test",
    )


def harness(cfg: RunConfig, ep: Endpoint, sleep: FakeSleep | None = None) -> FakeSleep:
    sleep = sleep or FakeSleep()
    asyncio.run(run(pipeline(ep), cfg, sleep))
    return sleep


def jsonl(cfg: RunConfig) -> list[dict[str, Any]]:
    text = (cfg.out / "run.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def write_jsonl(cfg: RunConfig, records: list[dict[str, Any]]) -> None:
    cfg.out.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(r) + "\n" for r in records)
    (cfg.out / "run.jsonl").write_text(lines, encoding="utf-8")


def done(phrase_id: str, code: str = "ok") -> dict[str, Any]:
    return {
        "run": "old",
        "lang": "en",
        "phrase_id": phrase_id,
        "turn_id": 1,
        "code": code,
        "latency_ms": None,
        "stages": {},
        "stt_text": "x",
    }


def test_order_and_pause(tmp_path: Path) -> None:
    cfg = config(tmp_path, pause=2)
    sleep = harness(cfg, endpoint())
    assert [r["phrase_id"] for r in jsonl(cfg)] == list(IDS)
    assert sleep.calls == [2, 2]


def test_lines_and_jsonl(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    harness(cfg, endpoint())
    log_lines = (cfg.out / "en.log").read_text(encoding="utf-8").splitlines()
    assert len([line for line in log_lines if "turn.latency" in line]) == 3
    assert all("Hello" not in line for line in log_lines)  # no text in the log (F5)
    records = jsonl(cfg)
    assert len(records) == 3
    for index, rec in enumerate(records, start=1):
        assert rec["run"] == "run-test"
        assert rec["lang"] == "en"
        assert rec["phrase_id"] == IDS[index - 1]
        assert rec["turn_id"] == index
        assert rec["code"] == "ok"
        assert rec["stt_text"] == "Hello there."
        assert isinstance(rec["latency_ms"], int)
        assert {"vad", "stt", "chat_full", "tts1"} <= set(rec["stages"])
    assert len(parse_records((cfg.out / "run.jsonl").read_text(encoding="utf-8"))) == 3


def test_failed_turn_is_recorded(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    harness(cfg, endpoint(failing={2}))
    records = jsonl(cfg)
    assert [r["code"] for r in records] == ["ok", "llm_failed", "ok"]
    assert records[1]["latency_ms"] is None
    assert records[1]["stt_text"] == "Hello there."


def test_stt_failure_has_no_text(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    ep = endpoint()
    ep.fail = {"/v1/audio/transcriptions": 400}
    harness(cfg, ep)
    records = jsonl(cfg)
    assert [r["code"] for r in records] == ["stt_failed"] * 3
    assert [r["stt_text"] for r in records] == [None] * 3


def test_resume(tmp_path: Path) -> None:
    cfg = config(tmp_path, pause=2, resume=True)
    earlier = [done("p01"), done("p02")]
    write_jsonl(cfg, earlier)
    ep = endpoint()
    sleep = harness(cfg, ep)
    records = jsonl(cfg)
    assert records[:2] == earlier
    assert [r["phrase_id"] for r in records[2:]] == ["p03"]
    assert ep.calls.count("transcriptions") == 1
    assert sleep.calls == []


def test_resume_ignores_other_language(tmp_path: Path) -> None:
    cfg = config(tmp_path, resume=True)
    write_jsonl(cfg, [{**done("p01"), "lang": "pl"}])
    harness(cfg, endpoint())
    assert [r["phrase_id"] for r in jsonl(cfg)[1:]] == list(IDS)


def test_rate_limited_turn_is_not_done(tmp_path: Path) -> None:
    cfg = config(tmp_path, resume=True)
    write_jsonl(cfg, [done("p01"), done("p02", "rate_limited"), done("p03")])
    harness(cfg, endpoint())
    assert [r["phrase_id"] for r in jsonl(cfg)[3:]] == ["p02"]


def test_without_resume_all_are_played_and_appended(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    write_jsonl(cfg, [done("p01")])
    harness(cfg, endpoint())
    assert [r["phrase_id"] for r in jsonl(cfg)] == ["p01", *IDS]


def test_damaged_last_line_is_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg = config(tmp_path, resume=True)
    write_jsonl(cfg, [done("p01")])
    with (cfg.out / "run.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"run": "old", "lang": "en", "phrase_id": "p0')
    with caplog.at_level(logging.WARNING, logger="ota_server.latency_run"):
        harness(cfg, endpoint())
    assert "damaged" in caplog.text
    records = jsonl(cfg)
    assert [r["phrase_id"] for r in records] == ["p01", "p02", "p03"]


def test_read_records_missing_file(tmp_path: Path) -> None:
    assert read_records(tmp_path / "none.jsonl") == []


def test_pipeline_logger_level_is_restored(tmp_path: Path) -> None:
    logger = logging.getLogger("ota_server.pipeline")
    before = logger.level
    harness(config(tmp_path), endpoint())
    assert logger.level == before


def test_main_parses_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_run_from_env(cfg: RunConfig) -> int:
        seen["cfg"] = cfg
        return 0

    monkeypatch.setattr(latency_run, "run_from_env", fake_run_from_env)
    argv = ["--audio", str(tmp_path), "--lang", "pl", "--pause", "5", "--resume"]
    assert latency_run.main([*argv, "--out", str(tmp_path / "o")]) == 0
    cfg = seen["cfg"]
    assert (cfg.audio_dir, cfg.lang, cfg.pause, cfg.resume) == (tmp_path, "pl", 5.0, True)
    assert cfg.out == tmp_path / "o"
    assert cfg.run_id


def test_no_audio_files(tmp_path: Path) -> None:
    cfg = RunConfig(tmp_path / "empty", "en", tmp_path / "out", 0, False, "r")
    with pytest.raises(SystemExit):
        asyncio.run(run(pipeline(endpoint()), cfg, FakeSleep()))


def test_run_from_env_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(latency_run, "model_env", lambda: {})
    assert asyncio.run(latency_run.run_from_env(config(tmp_path))) == 2
    assert not (tmp_path / "out").exists()


def test_run_from_env_builds_the_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ep = endpoint()
    monkeypatch.setattr(latency_run, "model_env", lambda: {**ENV, "OTA_TTS_VOICE": "v"})
    monkeypatch.setattr(
        latency_run,
        "make_http_client",
        lambda: make_client(ep, ENV).http,
    )
    cfg = config(tmp_path)
    assert asyncio.run(latency_run.run_from_env(cfg)) == 0
    assert [r["code"] for r in jsonl(cfg)] == ["ok"] * 3


def test_missing_newline_is_added(tmp_path: Path) -> None:
    cfg = config(tmp_path, resume=True)
    cfg.out.mkdir()
    (cfg.out / "run.jsonl").write_text(json.dumps(done("p01")), encoding="utf-8")
    harness(cfg, endpoint())
    assert [r["phrase_id"] for r in jsonl(cfg)] == list(IDS)


def test_damaged_middle_line_stops_the_run(tmp_path: Path) -> None:
    cfg = config(tmp_path, resume=True)
    cfg.out.mkdir()
    (cfg.out / "run.jsonl").write_text("{bad\n" + json.dumps(done("p01")) + "\n", "utf-8")
    with pytest.raises(json.JSONDecodeError):
        harness(cfg, endpoint())

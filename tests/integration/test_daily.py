"""Daily orchestration exercises real fixture replays and an in-memory generation store."""

import importlib
import json
import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from aitrader.cli import daily
from aitrader.cli.main import app, last_closed_session
from aitrader.clock import SimClock, TradingCalendar
from aitrader.cloud import CloudError, NotFound, PreconditionFailed, pack, unpack
from aitrader.config import ConfigError
from aitrader.data import DataError, DataSourceError
from aitrader.storage import Database, InstanceLockedError

REPO = Path(__file__).resolve().parents[2]
CLOCK = SimClock(datetime(2025, 1, 2, 20, tzinfo=UTC))
ORIGINAL_FETCH = daily.fetch


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, int]] = {}
        self.writes: list[str] = []
        self.conflict = False

    def download(self, name: str) -> tuple[bytes, int]:
        if name not in self.objects:
            raise NotFound("missing")
        return self.objects[name]

    def upload(self, name: str, data: bytes, if_generation_match: int) -> int:
        generation = self.objects.get(name, (b"", 0))[1]
        if generation != if_generation_match or (self.conflict and name == "state/a"):
            raise PreconditionFailed("conflict")
        self.objects[name] = data, generation + 1
        self.writes.append(name)
        return generation + 1


def fixture_fetch(config_dir: Path, work_dir: Path, clock: Any) -> dict[str, object]:
    shutil.copytree(REPO / "tests/fixtures/bars", work_dir / "data/bars", dirs_exist_ok=True)
    return {"session": "2024-12-31", "symbols": ["SPY"], "snapshot_hashes": {"raw": "fixture"}}


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Store, Path]:
    config = tmp_path / "config"
    config.mkdir()
    for name in ("base.yaml", "paper.yaml", "paper-s3.yaml", "universe.yaml"):
        text = (REPO / "config" / name).read_text(encoding="utf-8")
        (config / name).write_text(text.replace("2026-10-26", "2024-12-30"), encoding="utf-8")
    store = Store()
    monkeypatch.setattr(daily, "GcsStateStore", lambda bucket: store)
    monkeypatch.setattr(daily, "fetch", fixture_fetch)
    monkeypatch.setattr(daily, "WallClock", lambda: CLOCK)
    monkeypatch.setattr(daily, "assert_no_live_credentials", lambda *args: None)
    monkeypatch.setattr(
        daily, "TemporaryDirectory", lambda **kwargs: TemporaryDirectory(dir=tmp_path, **kwargs)
    )
    return store, config


def invoke(config: Path, *args: str) -> Any:
    result = CliRunner().invoke(
        app, ["paper", "daily", "--config-dir", str(config), "--git-commit", "fixture-sha", *args]
    )
    logs = [json.loads(line) for line in result.stdout.splitlines()]
    assert logs
    for entry in logs:
        assert {"ts", "severity", "event", "exit_code"} <= entry.keys()
        assert datetime.fromisoformat(entry["ts"]).utcoffset() == timedelta(0)
    assert logs[-1]["event"] == "daily.end"
    assert logs[-1]["exit_code"] == result.exit_code
    return result


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2024-10-28T20:16:00+00:00", "2024-10-28"),
        ("2024-10-28T20:15:59+00:00", "2024-10-25"),
        ("2024-10-28T20:16:01+00:00", "2024-10-28"),
        ("2024-10-28T22:00:00+00:00", "2024-10-28"),
        ("2024-10-27T22:00:00+00:00", "2024-10-25"),
        ("2024-12-25T22:00:00+00:00", "2024-12-24"),
    ],
)
def test_closed_session(now: str, expected: str) -> None:
    assert last_closed_session(
        TradingCalendar(), datetime.fromisoformat(now)
    ) == date.fromisoformat(expected)


def test_cloud_init_repeat_and_reports(job: tuple[Store, Path]) -> None:
    store, config = job
    first = invoke(config, "--state-uri", "gs://bucket/state/a", "--init")
    assert first.exit_code == 0, first.output
    assert store.writes[:2] == [
        "reports/paper/status/latest.json",
        "reports/paper-s3/status/latest.json",
    ]
    assert store.writes[-1] == "state/a"
    reports = {k: v for k, v in store.objects.items() if "/reports/daily/" in k}
    assert len(reports) == 8
    second = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert second.exit_code == 0, second.output
    logs = [json.loads(line) for line in second.stdout.splitlines()]
    assert [x["sessions_added"] for x in logs if x["event"] == "daily.book"] == [0, 0]
    assert {k: store.objects[k] for k in reports} == reports
    assert all(store.writes.count(k) == 1 for k in reports)
    assert store.objects["state/a"][1] == 2
    assert store.objects["reports/paper/status/latest.json"][1] == 2


def test_missing_existing_conflict(job: tuple[Store, Path]) -> None:
    store, config = job
    missing = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert missing.exit_code == 4
    assert "--init" in missing.stdout
    assert not store.writes
    assert invoke(config, "--state-uri", "gs://bucket/state/a", "--init").exit_code == 0
    before = dict(store.objects)
    assert invoke(config, "--state-uri", "gs://bucket/state/a", "--init").exit_code == 5
    store.conflict = True
    result = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert result.exit_code == 5
    assert "another writer" in result.stdout
    assert store.objects["state/a"] == before["state/a"]
    assert {k: v for k, v in store.objects.items() if "/reports/daily/" in k} == {
        k: v for k, v in before.items() if "/reports/daily/" in k
    }


@pytest.mark.parametrize(
    ("failed_suffix", "error"),
    [
        ("status/latest.json", CloudError),
        ("status/latest.json", PreconditionFailed),
        ("reports/daily/2024-12-31.md", CloudError),
    ],
)
def test_report_failure_replays_before_saving_state(
    job: tuple[Store, Path],
    monkeypatch: pytest.MonkeyPatch,
    failed_suffix: str,
    error: type[CloudError],
) -> None:
    store, config = job
    store.objects["state/a"] = pack(config.parent / "empty"), 7
    archive = store.objects["state/a"]
    original = store.upload

    def upload(name: str, data: bytes, generation: int) -> int:
        if name == f"reports/paper-s3/{failed_suffix}":
            raise error("report unavailable")
        return original(name, data, generation)

    monkeypatch.setattr(store, "upload", upload)
    result = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert result.exit_code == 4, result.output
    assert store.objects["state/a"] == archive
    assert "state/a" not in store.writes
    logs = [json.loads(line) for line in result.stdout.splitlines()]
    assert logs[-2]["event"] == "daily.upload"
    assert logs[-2]["exit_code"] == 4
    published = {k: v for k, v in store.objects.items() if "/reports/daily/" in k}
    if failed_suffix.startswith("reports/daily/"):
        assert published
    else:
        assert not published

    monkeypatch.setattr(store, "upload", original)
    retry = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert retry.exit_code == 0, retry.output
    assert store.objects["state/a"][1] == 8
    assert f"reports/paper-s3/{failed_suffix}" in store.objects
    assert len([k for k in store.objects if "/reports/daily/" in k]) == 8
    assert all(store.objects[k] == v for k, v in published.items())
    assert all(store.writes.count(k) == 1 for k in published)
    assert store.writes[-1] == "state/a"


def test_archive_conflict_after_reports_retries_without_rewriting(job: tuple[Store, Path]) -> None:
    store, config = job
    store.objects["state/a"] = pack(config.parent / "empty"), 3
    archive = store.objects["state/a"]
    store.conflict = True
    result = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert result.exit_code == 5, result.output
    assert "another writer" in result.stdout
    assert store.objects["state/a"] == archive
    reports = {k: v for k, v in store.objects.items() if "/reports/daily/" in k}
    assert len(reports) == 8
    assert "state/a" not in store.writes

    store.conflict = False
    retry = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert retry.exit_code == 0, retry.output
    assert store.objects["state/a"][1] == 4
    assert all(store.objects[k] == v for k, v in reports.items())
    assert all(store.writes.count(k) == 1 for k in reports)
    assert store.writes[-1] == "state/a"


@pytest.mark.parametrize(("error", "code"), [(DataSourceError, 4), (ConfigError, 2), (OSError, 4)])
def test_fetch_failure(
    job: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch, error: type[Exception], code: int
) -> None:
    store, config = job

    def fail(*args: Any) -> Any:
        raise error("private error")

    monkeypatch.setattr(daily, "fetch", fail)
    result = invoke(config, "--state-uri", "gs://bucket/state/a", "--init")
    assert result.exit_code == code
    assert "daily.book" not in result.stdout
    assert "private error" not in result.stdout
    assert not store.writes


def test_mismatch_runs_other_book(job: tuple[Store, Path], tmp_path: Path) -> None:
    store, config = job
    assert invoke(config, "--state-uri", "gs://bucket/state/a", "--init").exit_code == 0
    restored = tmp_path / "corrupt"
    archive, generation = store.objects["state/a"]
    unpack(archive, restored)
    with Database(restored / "data/paper/paper.db") as db, db.transaction() as conn:
        conn.execute("UPDATE paper_sessions SET snapshot_hash = ?", ("corrupt-hash",))
    store.objects["state/a"] = pack(restored), generation
    result = invoke(config, "--state-uri", "gs://bucket/state/a")
    assert result.exit_code == 6
    books = [json.loads(line) for line in result.stdout.splitlines() if '"daily.book"' in line]
    assert [entry["book"] for entry in books] == ["paper", "paper-s3"]
    assert [entry["state"] for entry in books] == ["HALTED", "RUNNING"]
    assert json.loads(store.objects["reports/paper/status/latest.json"][0])["state"] == "HALTED"
    assert "state/a" in store.objects
    assert json.loads(result.stdout.splitlines()[-1])["action"] == (
        "stop; do not delete state; report to the designer"
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [(ConfigError, 2), (DataError, 4), (InstanceLockedError, 5), (OSError, 4), (sqlite3.Error, 4)],
)
def test_book_exception_runs_both(
    job: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch, error: type[Exception], code: int
) -> None:
    store, config = job
    calls = []

    def fail(*args: Any) -> Any:
        calls.append(args[2])
        raise error("private")

    monkeypatch.setattr(daily, "book", fail)
    assert invoke(config, "--state-uri", "gs://bucket/state/a", "--init").exit_code == code
    assert calls == ["paper", "paper-s3"]
    assert "state/a" in store.objects


def test_local_parity(job: tuple[Store, Path], tmp_path: Path) -> None:
    store, config = job
    work = tmp_path / "local"
    assert invoke(config, "--work-dir", str(work)).exit_code == 0
    for name in ("paper", "paper-s3"):
        separate = daily.book(config, work, name, CLOCK, "fixture-sha")
        assert separate.exit_code == 0
        assert separate.sessions_added == 0
        before = (work / "data" / name / "status/latest.json").read_bytes()
        result = CliRunner().invoke(
            app,
            [
                "paper",
                "run",
                "--config",
                str(config / f"{name}.yaml"),
                "--data-dir",
                str(work / "data/bars"),
                "--state-dir",
                str(tmp_path / name),
                "--git-commit",
                "fixture-sha",
            ],
        )
        assert result.exit_code == 0, result.output
        actual = json.loads((tmp_path / name / "status/latest.json").read_bytes())
        expected = json.loads(before)
        for key in ("as_of", "data_stale"):
            actual.pop(key)
            expected.pop(key)
        assert actual == expected
    assert not store.writes


def test_guard_and_commit_failure(job: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    store, config = job

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ConfigError("private")

    monkeypatch.setattr(daily, "assert_no_live_credentials", fail)
    assert invoke(config).exit_code == 2
    assert not store.writes
    monkeypatch.setattr(daily, "assert_no_live_credentials", lambda *args: None)
    main = importlib.import_module("aitrader.cli.main")

    def no_commit(*args: Any, **kwargs: Any) -> Any:
        raise DataError("no git")

    monkeypatch.setattr(main, "_git_commit", no_commit)
    assert invoke(config).exit_code == 4


def test_invalid_options_and_dirty_restore(job: tuple[Store, Path], tmp_path: Path) -> None:
    store, config = job
    assert invoke(config, "--init").exit_code == 5
    assert invoke(config, "--reports-uri", "gs://b/p").exit_code == 5
    assert invoke(config, "--state-uri", "invalid").exit_code == 5
    work = tmp_path / "dirty"
    (work / "data/bars").mkdir(parents=True)
    assert (
        invoke(config, "--state-uri", "gs://b/state/a", "--init", "--work-dir", str(work)).exit_code
        == 5
    )
    store.objects["state/a"] = pack(tmp_path / "empty"), 1
    assert invoke(config, "--state-uri", "gs://b/state/a", "--work-dir", str(work)).exit_code == 5


def test_report_conflicts_and_upload_error(
    job: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, config = job
    original = store.upload

    def upload(name: str, data: bytes, generation: int) -> int:
        if "/reports/daily/" in name:
            raise PreconditionFailed("exists")
        return original(name, data, generation)

    monkeypatch.setattr(store, "upload", upload)
    assert (
        invoke(
            config, "--state-uri", "gs://b/state/a", "--init", "--reports-uri", "gs://other/prefix/"
        ).exit_code
        == 0
    )

    def fail(*args: Any) -> Any:
        raise CloudError("private")

    monkeypatch.setattr(store, "upload", fail)
    assert invoke(config, "--state-uri", "gs://b/state/a").exit_code == 4
    # Direct helper call also covers non-files and preexisting reports.
    root = tmp_path / "data/paper/reports/daily"
    root.mkdir(parents=True)
    (root / "directory").mkdir()
    (root / "old.md").write_text("old")
    daily._upload_reports(store, "prefix", tmp_path, {root / "old.md"})


def test_container_files() -> None:
    docker = (REPO / "Dockerfile").read_text()
    for required in (
        "FROM python:3.14-slim",
        "ghcr.io/astral-sh/uv:0.11.32",
        "uv sync --frozen --no-dev",
        "USER app",
        "WORKDIR /app",
        "COPY config/ config/",
        "ARG GIT_COMMIT",
        "ENV AITRADER_GIT_COMMIT=$GIT_COMMIT",
        'ENTRYPOINT ["aitrader", "paper", "daily"]',
    ):
        assert required in docker
    for name in (".dockerignore", ".gcloudignore"):
        ignore = (REPO / name).read_text().splitlines()
        expected = {
            ".git",
            ".venv",
            "data/",
            "results/",
            "bt-*",
            ".env*",
            "**/*token*",
            "config/live*.yaml",
            "!config/live.example.yaml",
        }
        if name == ".gcloudignore":
            expected = {
                line
                if line.startswith(("**/", ".env"))
                else "!/" + line[1:]
                if line.startswith("!")
                else "/" + line
                for line in expected
            }
        assert expected <= set(ignore)


def test_fetch_inputs_and_missing_credentials(
    job: tuple[Store, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config = job
    monkeypatch.setattr(
        daily, "Secrets", lambda: SimpleNamespace(data_api_key=None, data_api_secret=None)
    )
    with pytest.raises(ConfigError, match="credentials"):
        ORIGINAL_FETCH(config, tmp_path, CLOCK)
    fake_value = "fixture-only"
    monkeypatch.setattr(
        daily,
        "Secrets",
        lambda: SimpleNamespace(data_api_key=fake_value, data_api_secret=fake_value),
    )
    monkeypatch.setattr(daily, "AlpacaDataClient", lambda key, secret: "client")
    calls = []

    def fetch(*args: Any) -> Any:
        calls.append(args)
        return SimpleNamespace(snapshot_hashes={"raw": "fixture"})

    monkeypatch.setattr(daily, "fetch_universe", fetch)
    summary = ORIGINAL_FETCH(config, tmp_path, CLOCK)
    assert summary["session"] == date(2024, 12, 31)
    assert summary["snapshot_hashes"] == {"raw": "fixture"}
    assert calls[0][0] == "client"
    assert calls[0][2:5] == (date(2016, 1, 4), date(2024, 12, 31), tmp_path / "data/bars")
    assert calls[0][-1] == CLOCK.now()


def test_book_missing_bars(job: tuple[Store, Path], tmp_path: Path) -> None:
    _, config = job
    result = daily.book(config, tmp_path / "missing", "paper", CLOCK, "fixture-sha")
    assert result.exit_code == 4
    assert result.status.state == "HALTED"


def test_commit_precedence(
    job: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, config = job
    main = importlib.import_module("aitrader.cli.main")
    commits = []

    def commit(value: str | None) -> str:
        commits.append(value)
        return value or "git-sha"

    monkeypatch.setattr(main, "_git_commit", commit)
    monkeypatch.setenv("AITRADER_GIT_COMMIT", "env-sha")
    assert invoke(config, "--work-dir", str(tmp_path / "explicit")).exit_code == 0
    args = ["paper", "daily", "--config-dir", str(config), "--work-dir", str(tmp_path / "env")]
    assert CliRunner().invoke(app, args).exit_code == 0
    monkeypatch.delenv("AITRADER_GIT_COMMIT")
    args[-1] = str(tmp_path / "git")
    assert CliRunner().invoke(app, args).exit_code == 0
    assert commits == ["fixture-sha", "env-sha", None]

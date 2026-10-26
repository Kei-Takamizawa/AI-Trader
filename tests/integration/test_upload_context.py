"""Check upload contents rather than only the spelling of ignore rules."""

import os
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def match_segments(pattern: list[str], path: list[str]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return match_segments(pattern[1:], path) or bool(path and match_segments(pattern, path[1:]))
    return bool(path and fnmatchcase(path[0], pattern[0]) and match_segments(pattern[1:], path[1:]))


def excluded(path: str, rules: str, *, docker: bool = False) -> bool:
    """Apply the subset used here, including parent directories and ordered negation."""
    parts = path.split("/")
    ignored = False
    for line in rules.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line.removeprefix("!")
        directory = pattern.endswith("/")
        anchored = docker or pattern.startswith("/") or "/" in pattern.rstrip("/")
        segments = pattern.strip("/").split("/")
        starts = [0] if anchored else range(len(parts))
        ends = range(1, len(parts) + (0 if directory else 1))
        if any(match_segments(segments, parts[start:end]) for start in starts for end in ends):
            ignored = not negate
    return ignored


def required_paths() -> list[str]:
    """Enumerate names only; private file contents are never opened."""
    paths = []
    for root, directories, files in os.walk(REPO):
        directories[:] = [name for name in directories if name != ".git"]
        for name in files:
            path = (Path(root) / name).relative_to(REPO).as_posix()
            if (
                path.startswith("src/")
                or path in {"pyproject.toml", "uv.lock", "README.md", "Dockerfile"}
                or (
                    path.startswith("config/")
                    and (
                        not fnmatchcase(path, "config/live*.yaml")
                        or path == "config/live.example.yaml"
                    )
                )
            ):
                paths.append(path)
    assert "src/aitrader/data/__init__.py" in paths
    return paths


def assert_uploads(rules: str, *, docker: bool = False) -> None:
    # Cache artifacts are not source files, even when generated inside src/.
    missing = [
        path
        for path in required_paths()
        if "__pycache__" not in path.split("/") and excluded(path, rules, docker=docker)
    ]
    assert not missing, f"Required files excluded: {missing}"


@pytest.mark.parametrize("name", [".gcloudignore", ".dockerignore"])
def test_upload_context(name: str) -> None:
    rules = (REPO / name).read_text(encoding="utf-8")
    docker = name == ".dockerignore"
    assert_uploads(rules, docker=docker)
    for path in (
        "data/x.csv",
        "results/x",
        "bt-a/x",
        ".env",
        ".env.local",
        ".ai/CURRENT_TASK.md",
        "config/live.yaml",
        "config/live.private.yaml",
        "token.txt",
        "nested/api_token.json",
        "nested/deep/credential.txt",
        "nested/__pycache__/module.pyc",
        "nested/.pytest_cache/x",
    ):
        assert excluded(path, rules, docker=docker), path
    assert not excluded("config/live.example.yaml", rules, docker=docker)
    assert not excluded("src/aitrader/data/bars.py", rules, docker=docker)


def test_old_gcloudignore_drops_required_source() -> None:
    rules = (REPO / ".gcloudignore").read_text(encoding="utf-8")
    old = "\n".join(line.removeprefix("/").replace("!/", "!") for line in rules.splitlines())
    with pytest.raises(AssertionError, match=r"src/aitrader/data/__init__\.py"):
        assert_uploads(old)


@pytest.mark.parametrize("docker", [False, True])
def test_matcher_semantics(docker: bool) -> None:
    assert excluded("nested/data/x", "data/", docker=docker) is (not docker)
    assert not excluded("nested/data/x", "/data/", docker=docker)
    assert not excluded("data.txt", "data/", docker=docker)
    assert not excluded("data", "data/", docker=docker)
    assert excluded("data/x", "data/", docker=docker)
    assert not excluded("ab/cd", "a*d", docker=docker)
    assert excluded("a/b/token.txt", "**/*token*", docker=docker)
    assert excluded("token.txt", "**/*token*", docker=docker)
    assert not excluded("a/b/x", "**/x\n!**/x", docker=docker)
    assert excluded("a/b/x", "!**/x\n**/x", docker=docker)

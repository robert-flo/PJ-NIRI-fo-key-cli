from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from key_cli.clipboard import backend


def test_watcher_lock_is_single_instance_and_status_uses_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    first = backend.acquire_watcher_lock()
    try:
        assert first.name.endswith("clipboard-watch.lock")
        assert first.fileno() >= 0
        assert os.get_inheritable(first.fileno()) is True
        assert backend.watcher_running() is True
        with pytest.raises(BlockingIOError):
            backend.acquire_watcher_lock()
    finally:
        first.close()

    assert backend.watcher_running() is False


def test_clipboard_status_has_capabilities_without_watcher(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        backend,
        "executable",
        lambda name: f"/usr/bin/{name}",
    )

    result = backend.run_command(SimpleNamespace(action="status"))
    payload = result.json()

    assert result.exit_code != 0
    assert payload["schemaVersion"] == 1
    assert payload["command"] == "clipboard.status"
    assert payload["ok"] is False
    assert payload["watcherRunning"] is False
    assert payload["capabilities"] == {
        "inspect": True,
        "preview": True,
        "mimeRestore": True,
        "mimeAwareStore": True,
        "singleRepresentation": True,
        "multiMime": False,
        "originalMimePreserved": False,
    }
    assert payload["error"]["code"] == "cliphist_watcher_inactive"


def daemon_args():
    return SimpleNamespace(action=None, daemon=True)


def test_daemon_is_a_no_op_when_a_watcher_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(backend.subprocess, "Popen", lambda *a, **k: calls.append(a))

    lock = backend.acquire_watcher_lock()
    try:
        result = backend.run_command(daemon_args())
    finally:
        lock.close()

    assert result.exit_code == 0
    assert result.json()["started"] is False
    assert calls == []


def test_daemon_prefers_the_systemd_unit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(backend, "executable", lambda name: f"/usr/bin/{name}")
    runs = []

    def fake_run(argv, **kwargs):
        runs.append(argv[1:])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    monkeypatch.setattr(
        backend.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn a watcher")
    )

    result = backend.run_command(daemon_args())

    assert result.exit_code == 0
    assert result.json()["via"] == "systemd"
    assert runs == [
        ["--user", "cat", backend.WATCHER_UNIT],
        ["--user", "start", backend.WATCHER_UNIT],
    ]


def test_daemon_spawns_a_watcher_when_the_unit_cannot_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(backend, "executable", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(backend, "current_key_executable", lambda: "/usr/local/bin/key")

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=0 if argv[2] == "cat" else 1)

    spawned = []
    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    monkeypatch.setattr(
        backend.subprocess, "Popen", lambda argv, **kwargs: spawned.append((argv, kwargs))
    )

    result = backend.run_command(daemon_args())

    assert result.exit_code == 0
    assert result.json()["via"] == "process"
    assert spawned[0][0] == ["/usr/local/bin/key", "clipboard", "watch"]
    assert spawned[0][1]["start_new_session"] is True


def test_clipboard_without_action_or_daemon_is_a_usage_error() -> None:
    result = backend.run_command(SimpleNamespace(action=None, daemon=False))
    assert result.exit_code == 2
    assert result.json()["error"]["code"] == "clipboard_action_required"

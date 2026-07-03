import json


def test_write_runtime_identity_records_pid_commit_and_repo(tmp_path, monkeypatch):
    import runtime_identity

    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "runtime.json"
    monkeypatch.setattr(runtime_identity, "git_identity", lambda _repo: ("abc1234", "feature/test"))

    identity = runtime_identity.write_runtime_identity(
        repo,
        pid=4242,
        service_label="com.portfolioguru.bot",
        path=target,
    )

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == identity
    assert written["app"] == "portfolio-guru"
    assert written["pid"] == 4242
    assert written["commit"] == "abc1234"
    assert written["branch"] == "feature/test"
    assert written["repo_root"] == str(repo.resolve())
    assert written["backend_dir"] == str((repo / "backend").resolve())
    assert written["service_label"] == "com.portfolioguru.bot"
    assert written["started_at"]

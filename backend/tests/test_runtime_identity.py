import json


def test_write_runtime_identity_records_pid_commit_and_repo(tmp_path, monkeypatch):
    import runtime_identity

    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "runtime.json"
    full_sha = "a" * 40
    monkeypatch.setattr(runtime_identity, "git_identity", lambda _repo: (full_sha, "feature/test"))

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
    assert written["commit"] == full_sha
    assert written["branch"] == "feature/test"
    assert written["repo_root"] == str(repo.resolve())
    assert written["backend_dir"] == str((repo / "backend").resolve())
    assert written["service_label"] == "com.portfolioguru.bot"
    assert written["started_at"]


def test_git_identity_requests_full_head(monkeypatch, tmp_path):
    import runtime_identity

    calls = []

    def fake_check_output(args, **_kwargs):
        calls.append(args)
        return ("a" * 40 + "\n") if "rev-parse" in args else "main\n"

    monkeypatch.setattr(runtime_identity.subprocess, "check_output", fake_check_output)
    commit, branch = runtime_identity.git_identity(tmp_path)
    assert commit == "a" * 40
    assert branch == "main"
    assert calls[0][-2:] == ["rev-parse", "HEAD"]

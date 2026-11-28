import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def run(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, encoding="utf-8", check=False)


def make_repo(root):
    registry = root / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("# 注册表\n模块清单记录真实项目模块。\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "agent@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Agent Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)


def test_help_and_empty_topic():
    help_result = run("--help")
    assert help_result.returncode == 0
    assert all(name in help_result.stdout for name in ("run", "render", "accept", "sync", "status", "revalidate"))
    assert run("run", "--topic", " ").returncode == 2


def test_rule_cli_outputs_manifest_and_word(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    result = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(output), "--generation", "rule")
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["generation"] == "rule"
    assert os.path.isfile(manifest["artifacts"]["docx"])


def test_accept_rejects_unknown_case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    created = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(output), "--generation", "rule")
    run_dir = json.loads(created.stdout)["artifacts"]["run_dir"]
    evidence = tmp_path / "acceptance.json"
    evidence.write_text(json.dumps([{"id": "AE-001", "case_id": "AC-404", "command": "pytest -q", "environment": "test", "executed_at": "2026-11-27T19:00:00+08:00", "exit_code": 0, "result": "passed", "output_summary": "ok", "artifact": "test.txt"}]), encoding="utf-8")
    result = run("accept", "--run-dir", run_dir, "--evidence", str(evidence))
    assert result.returncode == 2
    assert "未知验收用例" in result.stderr


def test_changed_source_invalidates_review_and_returns_5(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    created = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(tmp_path / "out"), "--generation", "rule")
    run_dir = json.loads(created.stdout)["artifacts"]["run_dir"]
    chapter_path = os.path.join(run_dir, "chapters.json")
    with open(chapter_path, encoding="utf-8") as handle:
        chapters = json.load(handle)
    chapters["planning"]["content"]["scope"] = ["源稿已变化"]
    with open(chapter_path, "w", encoding="utf-8") as handle:
        json.dump(chapters, handle, ensure_ascii=False)
    result = run("render", "--run-dir", run_dir)
    assert result.returncode == 5
    assert "重新审查" in result.stderr


def test_status_sync_and_revalidate_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    created = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(tmp_path / "out"), "--generation", "rule")
    assert created.returncode == 0, created.stderr
    run_dir = json.loads(created.stdout)["artifacts"]["run_dir"]
    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "rb") as handle:
        before_status = handle.read()
    status = run("status", "--run-dir", run_dir)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["run_id"] == os.path.basename(run_dir)
    with open(manifest_path, "rb") as handle:
        assert handle.read() == before_status

    update = tmp_path / "sync.json"
    update.write_text("{}", encoding="utf-8")
    synced = run("sync", "--run-dir", run_dir, "--update", str(update))
    assert synced.returncode == 0, synced.stderr
    synced_manifest = json.loads(synced.stdout)
    revalidated = run("revalidate", "--run-dir", run_dir)
    assert revalidated.returncode == 0, revalidated.stderr
    revalidated_manifest = json.loads(revalidated.stdout)
    assert revalidated_manifest["generation"] == synced_manifest["generation"]
    assert revalidated_manifest["document"]["revision"] == synced_manifest["document"]["revision"]
    assert not os.path.exists(os.path.join(run_dir, ".lifecycle.lock"))


def test_malformed_deployment_entries_return_validation_exit_code(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    created = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(tmp_path / "out"), "--generation", "rule")
    assert created.returncode == 0, created.stderr
    run_dir = json.loads(created.stdout)["artifacts"]["run_dir"]
    run_id = json.loads(created.stdout)["run_id"]
    for field in ("commands", "health_checks"):
        for entry in (None, "bad"):
            payload = {
                "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": False, "review": "approved", "ci": "passed"}],
                "merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True, "run_id": run_id, "scope": "PR #9"},
                "deployment": {
                    "authorized": True,
                    "run_id": run_id,
                    "scope": f"deployment:production@{'c' * 40}",
                    "environment": "production",
                    "merge_commit": "c" * 40,
                    "commands": [{"command": "docker compose up -d", "executed_at": "2026-11-28T11:00:00+08:00", "exit_code": 0, "output_summary": "containers started"}],
                    "health_checks": [{"name": "api-health", "result": "passed", "output_summary": "HTTP 200"}],
                    "rollback": "restore previous approved container image",
                },
            }
            payload["deployment"][field] = [entry]
            update = tmp_path / "malformed-deployment.json"
            update.write_text(json.dumps(payload), encoding="utf-8")
            result = run("sync", "--run-dir", run_dir, "--update", str(update))
            assert result.returncode == 2, result.stderr
            assert field in result.stderr
            assert "Traceback" not in result.stderr

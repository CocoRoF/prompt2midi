"""tools/ 의 CI 점검 스크립트 시험 (임시 git 저장소에서 실행)."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _repo(tmp_path, files):
    shutil.copytree(ROOT / "tools", tmp_path / "tools")
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)


def _run(tmp_path, script):
    return subprocess.run([sys.executable, f"tools/{script}"], cwd=tmp_path, capture_output=True, text=True)


FAKE = "sk-" + "ant-api03-" + "A" * 30


def test_scan_finds_key_and_hides_value(tmp_path):
    _repo(tmp_path, {"a.py": f"KEY = '{FAKE}'\n"})
    r = _run(tmp_path, "scan_secrets.py")
    assert r.returncode == 1
    assert "a.py:1" in r.stdout
    assert FAKE not in r.stdout


def test_scan_allowlist_marker(tmp_path):
    _repo(tmp_path, {"a.py": f"KEY = '{FAKE}'  # pragma: allowlist secret\n"})
    assert _run(tmp_path, "scan_secrets.py").returncode == 0


def test_scan_clean_repo_passes():
    # 이 레포 자체에는 비밀 문자열이 없어야 한다
    r = subprocess.run([sys.executable, "tools/scan_secrets.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_license_check_reports_missing(tmp_path):
    _repo(tmp_path, {
        "pyproject.toml": '[project]\nname="x"\ndependencies=["mido>=1","Foo_Bar>=2"]\n'
                          '[project.optional-dependencies]\ndev=["pytest"]\n',
        "data/sources.csv": "name,type\nmido 1.3.3,python dependency (runtime)\n",
    })
    r = _run(tmp_path, "check_licenses.py")
    assert r.returncode == 1
    assert "foo-bar" in r.stdout and "pytest" in r.stdout and "mido" not in r.stdout.split("없는 의존성:")[1]


def test_license_check_passes_for_this_repo():
    r = subprocess.run([sys.executable, "tools/check_licenses.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_ci_status_renders_snapshot():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import ci_status
    finally:
        sys.path.remove(str(ROOT / "tools"))
    data = {"total_count": 2, "check_runs": [
        {"name": "test (3.12)", "status": "completed", "conclusion": "success", "started_at": "t0", "completed_at": "t1", "html_url": "https://example.test/1"},
        {"name": "docker", "status": "in_progress", "conclusion": None, "started_at": "t0", "completed_at": None, "html_url": "https://example.test/2"},
    ]}
    md = ci_status.render("o/r", "abc123", data, "2026-01-01 00:00")
    assert "abc123" in md and "스냅샷" in md and "자동으로 갱신되지 않는" in md
    assert "| docker | in_progress | - |" in md and "| test (3.12) | completed | success |" in md

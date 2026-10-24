"""backup_state.sh --prune: newest N plus the last few deploys' tarballs survive; the dry
run deletes nothing. Runs the real script against a scratch root with its own git tags."""
import os
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "deploy", "backup_state.sh")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def root(tmp_path):
    code = tmp_path / "jarvis_code"
    code.mkdir()
    _git(code, "init", "-q")
    _git(code, "commit", "-q", "--allow-empty", "-m", "init")
    (tmp_path / "backups").mkdir()
    return tmp_path


def _make(root, labels, tags):
    """One tarball per label, oldest first; `tags` are created as deploy tags."""
    for t in tags:
        _git(root / "jarvis_code", "tag", t)
    for i, label in enumerate(labels):
        f = root / "backups" / f"state-{label}-20261001-0000{i:02d}Z.tar.gz"
        f.write_bytes(b"x")
        os.utime(f, (1_000_000 + i, 1_000_000 + i))


def _prune(root, *args):
    r = subprocess.run(["bash", SCRIPT, "--prune", *args], capture_output=True, text=True,
                       env={**os.environ, "JARVIS_ROOT": str(root)})
    assert r.returncode == 0, r.stderr
    return {p.removeprefix("state-").rsplit("-", 2)[0] for p in os.listdir(root / "backups")}


def test_keeps_newest_n_and_last_deploys(root):
    deploys = [f"deploy-2026-10-{d:02d}-1" for d in range(1, 9)]  # 8 deploys, oldest first
    manual = [f"manual{i}" for i in range(4)]
    _make(root, deploys + manual, deploys)
    left = _prune(root, "3")
    # newest 3 are manual; the floor keeps the newest 5 deploys
    assert left == {"manual1", "manual2", "manual3", *deploys[-5:]}


def test_untagged_deploy_label_gets_no_floor(root):
    # a snapshot whose deploy aborted before tagging is just an old tarball
    _make(root, ["deploy-2026-10-01-1", "deploy-2026-10-02-1", "x1", "x2"], ["deploy-2026-10-02-1"])
    assert _prune(root, "2") == {"x1", "x2", "deploy-2026-10-02-1"}


def test_dry_run_deletes_nothing(root):
    labels = [f"deploy-2026-10-{d:02d}-1" for d in range(1, 15)]
    _make(root, labels, labels)
    before = set(os.listdir(root / "backups"))
    r = subprocess.run(["bash", SCRIPT, "--prune", "--dry-run", "2"], capture_output=True,
                       text=True, env={**os.environ, "JARVIS_ROOT": str(root)})
    assert r.returncode == 0, r.stderr
    assert set(os.listdir(root / "backups")) == before
    assert "would prune" in r.stderr and "nothing deleted" in r.stderr


def test_bad_count_is_refused(root):
    r = subprocess.run(["bash", SCRIPT, "--prune", "ten"], capture_output=True, text=True,
                       env={**os.environ, "JARVIS_ROOT": str(root)})
    assert r.returncode != 0 and "integer" in r.stderr

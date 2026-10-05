#!/usr/bin/env python3
"""Disposable repository fixture for Task 2.2 setup tests (issue #7).

Builds a git repository shaped like the A1 fixture: existing CI,
``.gitissue.yml``, committed sources, then *dirty* developer work — a
modified tracked file plus an untracked file — the setup must preserve
byte-for-byte. Optional competing-automation files
(``.mergify.yml`` etc.) model the A4 conflicting-owner fixture.

``manifest_text`` emits a schema-v1 ``.factory-kit.yml`` candidate inside
the kit's restricted-YAML subset so ``schema.load_manifest`` accepts it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(repo: Path, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True)


def manifest_text(repo_id="R_TEST0001", *, owner="testowner",
                  name="testrepo", model="dspark/montimage-dgx-spark",
                  skills="issue-resolver: \"0.19.0\"",
                  contexts="CI") -> str:
    return f"""# candidate .factory-kit.yml — test fixture
factory_kit: 1
identity:
  repo_id: "{repo_id}"
  owner: {owner}
  name: {name}
registration:
  owner: {owner}
authorization:
  execution_opt_in: true
  github_actors: [{owner}]
  github_roles: [maintainer]
  telegram_users: [12345]
  telegram_chats: [-100123]
runtime:
  name: hermes-kanban
  roles:
    implementation:
      model: "{model}"
    review:
      model: "{model}"
  tools:
    allow: [git, gh]
    deny: []
  capabilities:
    allow: [workspace.scratch]
    deny: []
skills:
  auto_discover: false
  approved:
    {skills}
verification:
  acceptance_commands:
    - "make test"
  required_checks:
    provider: github
    contexts: [{contexts}]
    conclusions: [success]
evidence:
  event_retention_days: 30
endpoint:
  preview:
    provider: vercel
    environment: preview
    visibility: unlisted
    ttl_hours: 24
    cleanup_minutes: 60
    smoke:
      command: "curl -fsS -o /dev/null -w '%{{http_code}}' {{url}}"
      expect: "200"
      marker: "MoneyMind"
  merge:
    method: squash
    approval_expiry_minutes: 60
    max_smoke_age_minutes: 10
  disabled: [production_deploy, package_publish, autonomous_merge]
"""


GITISSUE_YML = """# existing IDD configuration — the factory never writes it
platform: github
issue:
  auto_normalize: true
resolve:
  approval_gate: auto
security:
  allow_pattern: ""
"""

CI_YML = """name: CI Quality Checks
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: make test
"""

APP_PY = 'def handler():\n    return "ok"\n'
APP_PY_DIRTY = 'def handler():\n    return "ok"  # developer WIP\n'
SCRATCH = "# developer scratch notes — untracked, must survive\n"


def build_repo(root, *, with_ci=True, with_gitissue=True,
               dirty=True, automation=()) -> Path:
    """Create the fixture repository at ``root`` and return its Path."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")

    if with_ci:
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "ci.yml").write_text(CI_YML, encoding="utf-8")
    if with_gitissue:
        (root / ".gitissue.yml").write_text(GITISSUE_YML,
                                            encoding="utf-8")
    (root / "app.py").write_text(APP_PY, encoding="utf-8")
    (root / "README.md").write_text("# fixture project\n",
                                    encoding="utf-8")
    for rel in automation:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# competing automation owner\n",
                        encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=test@example.test",
         "-c", "user.name=Test", "commit", "-q", "-m", "initial")

    if dirty:
        # Developer work the setup must preserve: one dirty tracked file
        # and one untracked file.
        (root / "app.py").write_text(APP_PY_DIRTY, encoding="utf-8")
        (root / "scratch-notes.txt").write_text(SCRATCH,
                                                encoding="utf-8")
    return root


def snapshot(root) -> dict:
    """sha256 of every file under root (excluding .git) — the
    byte-for-byte preservation oracle."""
    root = Path(root)
    import hashlib
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(full.read_bytes()).hexdigest()
    return out

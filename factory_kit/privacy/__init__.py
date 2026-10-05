"""factory_kit.privacy — retention cleanup and guarded export (§5.2).

PRD §5.2 (issue #18 / Task 3.4, F07 A4/A5): the clock-controlled
retention pass that prunes proposed log/audit windows without losing
replay-resistant identity, and the guarded history export that can
never carry a credential canary.

- :mod:`factory_kit.privacy.retention` — ``retention_policy`` (the
  bounded ``evidence.*_retention_days`` config), ``cleanup`` (the
  clock-controlled prune: worker-log detail at 7 days, detailed audit
  metadata 30 days after completion, identity tombstones retained) and
  ``describe_retention`` — the export/delete distinction explained.
- :mod:`factory_kit.privacy.export` — ``history_export`` (repo-scoped
  history through the §7.1 redaction boundary; detail columns excluded
  by default and only on explicit ``expanded=True``) and
  ``secret_leaks`` — the canary self-check every export runs.
- :mod:`factory_kit.privacy.boundary` — hostile boundary probes for
  external-workload admission (Task 4.4 / A3–A4): filesystem-read,
  network-egress, privileged-write and credential-scope fixtures
  executed under the host's real isolation mechanism; worktrees,
  prompt rules and branch protection are never evidence.
- :mod:`factory_kit.privacy.consent` — the external-pilot
  consent/admission gate (Task 4.4 / A1–A5): the durable obligations
  record before collection, per-participant consent + supported
  workload/trust classification, revocation that blocks collection and
  export, and preview-path validation without broadened authority.
"""

from . import boundary, consent, export, retention

__all__ = ["boundary", "consent", "export", "retention"]

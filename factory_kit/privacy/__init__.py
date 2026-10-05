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
"""

from . import export, retention

__all__ = ["export", "retention"]

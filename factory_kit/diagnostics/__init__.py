"""factory_kit.diagnostics — local status/summary views (§7.2).

PRD §7.1–§7.2 (issue #14 / Task 2.9, F07): the local diagnostic report
and the per-work status summary — product view (installation attempts,
completion rate, intervention minutes), technical view (task state,
queue age, usage) and evidence view (revision, checks, review) — plus
the redacted export paths. All reads are local; no separate dashboard
or telemetry service is introduced (A4).

- :mod:`factory_kit.diagnostics.report` — ``work_status``,
  ``diagnostic_report``, ``export_diagnostics``, ``pilot_export``,
  ``telegram_summary``.
"""

from . import report

__all__ = ["report"]

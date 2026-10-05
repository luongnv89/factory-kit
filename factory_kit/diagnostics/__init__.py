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
- :mod:`factory_kit.diagnostics.views` — the Task-3.4 view layer
  (``status_view``, ``operations_view``, ``render_status_text``,
  ``render_operations_text``): queue age, remote-observation freshness
  and notification-outbox tallies on top of the report projections,
  rendered as plain accessible text.
"""

from . import report, views

__all__ = ["report", "views"]

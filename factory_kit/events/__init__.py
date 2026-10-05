"""factory_kit.events — typed §7.1 event contracts.

PRD §7.1 (issue #14 / Task 2.9, F07): the measurement backbone every
producer emits through — one vocabulary of event kinds, the required
identity/runtime/model/verdict/observation properties each must carry,
the measured-or-unknown usage shape (separate token/billing/quota/
duration fields, never zero-filled), and the redaction rules that keep
issue bodies, code, logs, chat text, usernames and secrets out of
every aggregate property.

- :mod:`factory_kit.events.schema` — the event/usage contracts,
  validation, normalization and redaction helpers.
"""

from . import schema

__all__ = ["schema"]

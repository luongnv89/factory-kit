"""Revision-bound preview lifecycle (issue #15 / Task 3.1, F11).

``PreviewService`` is the scoped deployment boundary; ``PreviewPort``
is the provider seam behind which the deployment credential lives —
workers and test processes never receive either.
"""

from .port import (  # noqa: F401
    PreviewAmbiguity,
    PreviewError,
    PreviewPort,
    ScriptedPreview,
    VercelCliPreview,
)
from .pages import GitHubPagesPreview  # noqa: F401
from .service import PreviewService  # noqa: F401

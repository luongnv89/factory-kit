"""Serialized publication intents + remote-effect boundary
(issue #10 / Task 2.5)."""

from factory_kit.publication.intents import (
    OPERATION_BRANCH_PUBLISH,
    OPERATION_PR_PUBLISH,
    PublicationBroker,
    verify_request,
)
from factory_kit.publication.remote import (
    GhCliRemote,
    RemoteAmbiguity,
    RemoteError,
    RemotePort,
    ScriptedRemote,
)

__all__ = [
    "OPERATION_BRANCH_PUBLISH",
    "OPERATION_PR_PUBLISH",
    "PublicationBroker",
    "verify_request",
    "GhCliRemote",
    "RemoteAmbiguity",
    "RemoteError",
    "RemotePort",
    "ScriptedRemote",
]

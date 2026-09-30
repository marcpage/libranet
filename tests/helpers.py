"""Helpers more than one test file uses, which are not fixtures.

Fixtures that are shared are in ``conftest.py``. What only one file uses
stays in that file.
"""

from __future__ import annotations
from json import loads
from queue import Empty

from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.messaging.envelope import Message
from libranet.messaging.queues import ModuleQueues
from libranet.problems import PROBLEM_CONTENT_TYPE
from libranet.webserver.http_types import Response


def published(queues: ModuleQueues) -> list[Message]:
    """Everything published to ``queues`` since it was last asked for, in order."""
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def new_identity() -> NodeIdentity:
    """A node identity with a key of its own."""
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


def problem_type(response: Response) -> str:
    """The type of the problem ``response`` reports, checked to agree with its status."""
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    problem = loads(response.body)
    assert problem["status"] == response.status
    return str(problem["type"])

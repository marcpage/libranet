"""Helpers more than one test file uses, which are not fixtures.

Fixtures that are shared are in ``conftest.py``. What only one file uses
stays in that file.
"""

from __future__ import annotations
from dataclasses import replace
from hashlib import sha256
from json import loads
from queue import Empty
from zlib import compress

from libranet.bundle.encryption import Aes256Cbc
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import Entry, FileBundle
from libranet.cas.content_id import ContentId
from libranet.config.models import LibranetConfig
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


def with_node_key(config: LibranetConfig) -> LibranetConfig:
    """``config``, once its node key exists, as the supervisor makes it before any module starts."""
    NodeIdentity.load_or_create(config)
    return config


def new_identity() -> NodeIdentity:
    """A node identity with a key of its own."""
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


def encrypted_part(data: bytes) -> PartPath:
    """``data`` as a backup names it, stored as one encrypted part (BundleSpecification §7).

    Derived here rather than by :class:`~libranet.bundle.parts.PartWriter`,
    so that a change to how parts are encrypted, which would stop them
    deduplicating with those already stored, fails a test.
    """
    key = sha256(data).digest()
    compressed = compress(data, 9)
    ciphertext = Aes256Cbc(key).encrypt(compressed if len(compressed) < len(data) else data)
    return PartPath(ContentId.for_data(ciphertext, "sha256"), key)


def without_part_sizes(entries: dict[str, Entry]) -> dict[str, Entry]:
    """``entries`` as a node recorded them before it recorded each part's size."""
    return {
        path: replace(entry, part_sizes_bytes=None) if isinstance(entry, FileBundle) else entry
        for path, entry in entries.items()
    }


def problem_type(response: Response) -> str:
    """The type of the problem ``response`` reports, checked to agree with its status."""
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    problem = loads(response.body)
    assert problem["status"] == response.status
    return str(problem["type"])

"""RFC 9457 Problem Details, the body of every Libranet HTTP error (HttpApi §17).

Shared by every error path: the web server builds its error responses from
:class:`Problem`. Libranet-specific problem types live under
:data:`PROBLEM_TYPE_BASE`; errors fully described by their status code use
``about:blank``, whose ``title`` is by convention the status phrase.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Final, Mapping

from libranet.json_format import compact_json

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"
ABOUT_BLANK: Final = "about:blank"
PROBLEM_TYPE_BASE: Final = "https://libranet.org/problems/"
INVALID_CONTENT_ADDRESS: Final = PROBLEM_TYPE_BASE + "invalid-content-address"
INVALID_SEARCH_PREFIX: Final = PROBLEM_TYPE_BASE + "invalid-search-prefix"
CONTENT_UNAVAILABLE: Final = PROBLEM_TYPE_BASE + "content-unavailable"
CONTENT_TOO_LARGE: Final = PROBLEM_TYPE_BASE + "content-too-large"
SIGNATURE_REQUIRED: Final = PROBLEM_TYPE_BASE + "signature-required"
INVALID_SIGNATURE: Final = PROBLEM_TYPE_BASE + "invalid-signature"
INVALID_LIST: Final = PROBLEM_TYPE_BASE + "invalid-list"
CREDENTIAL_REQUIRED: Final = PROBLEM_TYPE_BASE + "credential-required"
INVALID_CONFIG_REQUEST: Final = PROBLEM_TYPE_BASE + "invalid-config-request"
UNUSABLE_BUNDLE: Final = PROBLEM_TYPE_BASE + "unusable-bundle"
_STANDARD_MEMBERS: Final = frozenset({"type", "title", "status", "detail", "instance"})


class InvalidProblemError(ValueError):
    """A body is not a usable Problem Details object."""


@dataclass(frozen=True)
class Problem:
    """One Problem Details object.

    ``extensions`` holds problem-type-specific members (HttpApi §17.3); they
    may not reuse a standard member's name.
    """

    status: int
    title: str
    type: str = ABOUT_BLANK
    detail: str | None = None
    instance: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clashes = _STANDARD_MEMBERS.intersection(self.extensions)

        if clashes:
            raise InvalidProblemError(
                f"Extension members may not redefine: {', '.join(sorted(clashes))}"
            )

    @classmethod
    def for_status(
        cls,
        status: HTTPStatus | int,
        detail: str | None = None,
        instance: str | None = None,
    ) -> Problem:
        """An ``about:blank`` problem titled with the status phrase."""
        status = HTTPStatus(status)
        return cls(status=status.value, title=status.phrase, detail=detail, instance=instance)

    def to_dict(self) -> dict[str, Any]:
        """The JSON object, omitting unset optional members."""
        body: dict[str, Any] = {"type": self.type, "title": self.title, "status": self.status}

        if self.detail is not None:
            body["detail"] = self.detail

        if self.instance is not None:
            body["instance"] = self.instance

        body.update(self.extensions)
        return body

    def to_json(self) -> bytes:
        """The UTF-8 encoded JSON body."""
        return compact_json(self.to_dict())

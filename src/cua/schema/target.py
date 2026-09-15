from __future__ import annotations

from .common import NameMatch, StrictModel


class ContainerHint(StrictModel):
    """The enclosing landmark, section, or row a target sits in."""

    role: str
    accessible_name: str | None


class EvidenceRef(StrictModel):
    ax_snapshot: str
    screenshot: str | None


class TargetDescriptor(StrictModel):
    """Independent signals identifying one control, recorded at discovery.

    Signals only. The ranked order they are tried in belongs to the resolver, so
    re-ranking tiers never requires re-cutting artifacts. There is deliberately no
    field a selector, XPath, or coordinate could be stored in.
    """

    role: str
    accessible_name: str | None
    name_match: NameMatch
    label: str | None
    nearby_text: list[str]
    container: ContainerHint | None
    ordinal: int | None
    frame_path: list[str]
    evidence: EvidenceRef | None

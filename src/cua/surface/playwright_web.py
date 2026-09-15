"""Web implementation of the Surface port.

Playwright is transport only. Perception is the accessibility tree read over CDP,
one call per frame, stitched into a single element graph keyed by iframe titles.
Acting on a resolved node stamps a one-off attribute on it over CDP and hands the
resulting locator to Playwright, so its actionability checks (visible, enabled,
stable) do the waiting; no fixed delay anywhere.
"""

from __future__ import annotations

import re
import uuid
from types import TracebackType
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Browser, CDPSession, Locator, Page, Playwright, sync_playwright

from .actions import Click, Navigate, ReadText, SelectOption, SurfaceAction, TypeText
from .errors import ActionNotApplicable, StaleNode
from .graph import ElementNode, Observation

TAG = "data-cua-node"
_WS = re.compile(r"\s+")
_LABEL_SOURCES = {"labelfor", "label", "labelwrapped"}
_SKIPPED_ROLES = {"InlineTextBox"}


class PlaywrightWebSurface:
    def __init__(self, base_url: str, headless: bool = False) -> None:
        self._base_url = base_url
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        # node_id -> backendDOMNodeId, for the latest observation only. Ids carry
        # the observation sequence so a node from an earlier snapshot is always
        # stale, even when Chrome would still recognise it.
        self._handles: dict[str, int] = {}
        self._sequence = 0

    def __enter__(self) -> PlaywrightWebSurface:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page = self._browser.new_page()
        self._cdp = self._page.context.new_cdp_session(self._page)
        self._cdp.send("DOM.enable")
        self._cdp.send("Accessibility.enable")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("surface is not open; use it as a context manager")
        return self._page

    @property
    def cdp(self) -> CDPSession:
        if self._cdp is None:
            raise RuntimeError("surface is not open; use it as a context manager")
        return self._cdp

    # -- observe -----------------------------------------------------------------

    def observe(self) -> Observation:
        self.page.wait_for_load_state("domcontentloaded")
        root = self.cdp.send("Page.getFrameTree")["frameTree"]
        self._sequence += 1
        handles: dict[str, int] = {}
        nodes: list[ElementNode] = []
        self._walk_frame(root, [], handles, nodes)
        self._handles = handles
        return Observation(location=urlsplit(self.page.url).path, nodes=nodes)

    def _walk_frame(
        self,
        frame_tree: dict[str, Any],
        frame_path: list[str],
        handles: dict[str, int],
        out: list[ElementNode],
    ) -> None:
        frame_id = frame_tree["frame"]["id"]
        raw = self.cdp.send("Accessibility.getFullAXTree", {"frameId": frame_id})["nodes"]
        by_id = {n["nodeId"]: n for n in raw}
        roots = [n for n in raw if n.get("parentId") is None]
        prefix = f"{self._sequence}:{frame_id[:8]}:"
        child_frames = {c["frame"]["id"]: c for c in frame_tree.get("childFrames", [])}
        iframe_titles: dict[str, str] = {}

        order = 0

        def visit(ax: dict[str, Any], parent_id: str | None) -> None:
            nonlocal order
            role = _value(ax.get("role"))
            if ax.get("ignored") or role in _SKIPPED_ROLES:
                for child in ax.get("childIds", []):
                    if child in by_id:
                        visit(by_id[child], parent_id)
                return
            node_id = prefix + str(ax["nodeId"])
            name = _value(ax.get("name"))
            node = ElementNode(
                node_id=node_id,
                role=role,
                name=name,
                value=_value(ax["value"]) if "value" in ax else None,
                text=_text(ax, by_id),
                label=_label(ax) or _term_for(ax, by_id),
                frame_path=list(frame_path),
                parent_id=parent_id,
                order=order,
            )
            order += 1
            out.append(node)
            handles[node_id] = ax["backendDOMNodeId"]
            if role == "Iframe":
                described = self.cdp.send(
                    "DOM.describeNode", {"backendNodeId": ax["backendDOMNodeId"]}
                )["node"]
                child_frame = described.get("frameId")
                if child_frame is not None:
                    iframe_titles[child_frame] = name
            for child in ax.get("childIds", []):
                if child in by_id:
                    visit(by_id[child], node_id)

        for root_ax in roots:
            visit(root_ax, None)

        for child_id, child_tree in child_frames.items():
            title = iframe_titles.get(child_id, "")
            self._walk_frame(child_tree, [*frame_path, title], handles, out)

    # -- act ---------------------------------------------------------------------

    def act(self, action: SurfaceAction, node: ElementNode | None) -> str | None:
        if isinstance(action, Navigate):
            if node is not None:
                raise ActionNotApplicable("navigate takes no target node")
            self.page.goto(urljoin(self._base_url, action.location), wait_until="domcontentloaded")
            return None
        if node is None:
            raise ActionNotApplicable(f"{type(action).__name__} requires a target node")
        if node.node_id not in self._handles:
            raise StaleNode(node)
        if isinstance(action, ReadText):
            return node.text

        locator = self._locate(node)
        if isinstance(action, Click):
            locator.click()
        elif isinstance(action, TypeText):
            locator.fill(action.text)
        elif isinstance(action, SelectOption):
            locator.select_option(label=action.option)
        else:
            raise ActionNotApplicable(f"unsupported action {type(action).__name__}")
        return None

    def _locate(self, node: ElementNode) -> Locator:
        """Stamp a one-off tag on the node so Playwright can drive it. The tag is
        an internal handle on a node the AX resolver already chose; it never leaves
        this adapter."""
        tag = uuid.uuid4().hex
        # Node ids are only valid against a requested document; each navigation is a
        # new document, so request it every time rather than track it.
        self.cdp.send("DOM.getDocument", {"depth": 0})
        node_ids = self.cdp.send(
            "DOM.pushNodesByBackendIdsToFrontend",
            {"backendNodeIds": [self._handles[node.node_id]]},
        )["nodeIds"]
        self.cdp.send("DOM.setAttributeValue", {"nodeId": node_ids[0], "name": TAG, "value": tag})
        for frame in self.page.frames:
            locator = frame.locator(f'[{TAG}="{tag}"]')
            if locator.count() == 1:
                return locator
        # Observed, but gone from the document since: the page moved under us.
        raise StaleNode(node)


# -- AX node helpers ---------------------------------------------------------------


def _value(field: dict[str, Any] | None) -> str:
    if not field:
        return ""
    value = field.get("value")
    return str(value) if value is not None else ""


def _text(ax: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    if "value" in ax and _value(ax["value"]):
        return _value(ax["value"])
    parts: list[str] = []

    def collect(n: dict[str, Any]) -> None:
        if _value(n.get("role")) == "StaticText":
            parts.append(_value(n.get("name")))
            return
        for child in n.get("childIds", []):
            if child in by_id:
                collect(by_id[child])

    collect(ax)
    return _WS.sub(" ", " ".join(parts)).strip()


def _label(ax: dict[str, Any]) -> str | None:
    for source in ax.get("name", {}).get("sources", []):
        if source.get("type") != "relatedElement" or "value" not in source:
            continue
        if source.get("nativeSource") in _LABEL_SOURCES or source.get("attribute") == "aria-labelledby":
            return _value(source["value"]) or None
    return None


def _term_for(ax: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str | None:
    """A `definition` is labelled by the `term` that precedes it in its list."""
    if _value(ax.get("role")) != "definition":
        return None
    parent = by_id.get(ax.get("parentId", ""))
    if parent is None:
        return None
    last_term: str | None = None
    for sibling_id in parent.get("childIds", []):
        sibling = by_id.get(sibling_id)
        if sibling is None:
            continue
        if sibling["nodeId"] == ax["nodeId"]:
            return last_term
        if _value(sibling.get("role")) == "term":
            last_term = _value(sibling.get("name")) or None
    return None

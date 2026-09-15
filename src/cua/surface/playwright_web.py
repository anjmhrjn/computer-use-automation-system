"""Web implementation of the Surface port.

Playwright is transport only. Perception is the accessibility tree read over CDP,
one call per frame, stitched into a single element graph keyed by iframe titles.
Acting on a resolved node stamps a one-off attribute on it over CDP and hands the
resulting locator to Playwright, so its actionability checks (visible, enabled,
stable) do the waiting; no fixed delay anywhere.

`act()` is dispatch and `observe()` is the only readiness wait, bounded by
`ready_timeout_ms`: a page that is still loading when the bound elapses is reported
as `SurfaceNotReady`, and the replay engine's own step deadline decides how long
that may go on. Chrome suspends renderer-bound DevTools commands while a navigation
is pending, with no timeout, so the adapter tracks in-flight navigation requests
itself and never touches CDP while one is outstanding. Nothing Playwright-typed
leaves this module.
"""

from __future__ import annotations

import time
import uuid
from types import TracebackType
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import (
    Browser,
    CDPSession,
    Frame,
    Locator,
    Page,
    Playwright,
    Request,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from . import ax
from .actions import Click, Navigate, ReadText, SelectOption, SurfaceAction, TypeText
from .errors import ActionNotApplicable, StaleNode, SurfaceNotReady
from .graph import ElementNode, Observation

TAG = "data-cua-node"
_SKIPPED_ROLES = {"InlineTextBox"}
_CARRIER = """function () {
  let node = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  for (let root = node.getRootNode(); root instanceof ShadowRoot; root = node.getRootNode()) {
    node = root.host;
  }
  return node;
}"""


class PlaywrightWebSurface:
    def __init__(
        self, base_url: str, headless: bool = False, ready_timeout_ms: int = 2000
    ) -> None:
        self._base_url = base_url
        self._headless = headless
        self._ready_timeout_ms = ready_timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        # node_id -> backendDOMNodeId, for the latest observation only. Ids carry
        # the observation sequence so a node from an earlier snapshot is always
        # stale, even when Chrome would still recognise it.
        self._handles: dict[str, int] = {}
        self._sequence = 0
        # Navigation requests issued but not yet answered. While any is outstanding
        # the renderer is unreachable over CDP, so observe() must not try.
        self._pending: set[Request] = set()

    def __enter__(self) -> PlaywrightWebSurface:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page = self._browser.new_page()
        self._page.on("request", self._on_request)
        self._page.on("framenavigated", self._on_committed)
        self._page.on("requestfailed", lambda request: self._pending.discard(request))
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

    def _on_request(self, request: Request) -> None:
        if request.is_navigation_request():
            self._pending.add(request)

    def _on_committed(self, frame: Frame) -> None:
        self._pending = {r for r in self._pending if r.frame != frame}

    def _settle(self) -> None:
        """Block until no navigation is outstanding in any frame, then until the
        document is parsed. Every wait here is on an event, and one bound caps all
        of them together."""
        deadline = time.monotonic() + self._ready_timeout_ms / 1000
        try:
            while self._pending:
                remaining_ms = (deadline - time.monotonic()) * 1000
                if remaining_ms <= 0:
                    raise SurfaceNotReady(f"navigation not settled within {self._ready_timeout_ms}ms")
                # The waiter resolves before the listener runs, so apply it here too.
                self._on_committed(self.page.wait_for_event("framenavigated", timeout=remaining_ms))
            self.page.wait_for_load_state("domcontentloaded", timeout=self._ready_timeout_ms)
        except PlaywrightTimeout as exc:
            raise SurfaceNotReady(f"navigation not settled within {self._ready_timeout_ms}ms") from exc

    # -- observe -----------------------------------------------------------------

    def observe(self) -> Observation:
        self._settle()
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

        def visit(raw: dict[str, Any], parent_id: str | None) -> None:
            nonlocal order
            role = ax.value(raw.get("role"))
            if raw.get("ignored") or role in _SKIPPED_ROLES:
                for child in raw.get("childIds", []):
                    if child in by_id:
                        visit(by_id[child], parent_id)
                return
            node_id = prefix + str(raw["nodeId"])
            name = ax.value(raw.get("name"))
            node = ElementNode(
                node_id=node_id,
                role=role,
                name=name,
                value=ax.value(raw["value"]) if "value" in raw else None,
                text=ax.text(raw, by_id),
                label=ax.label(raw) or ax.term_for(raw, by_id),
                frame_path=list(frame_path),
                parent_id=parent_id,
                order=order,
            )
            order += 1
            out.append(node)
            handles[node_id] = raw["backendDOMNodeId"]
            if role == "Iframe":
                described = self.cdp.send(
                    "DOM.describeNode", {"backendNodeId": raw["backendDOMNodeId"]}
                )["node"]
                child_frame = described.get("frameId")
                if child_frame is not None:
                    iframe_titles[child_frame] = name
            for child in raw.get("childIds", []):
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
            try:
                self.page.goto(
                    urljoin(self._base_url, action.location),
                    wait_until="domcontentloaded",
                    timeout=self._ready_timeout_ms,
                )
            except PlaywrightTimeout:
                # The navigation is in flight; whether it ever lands is observe()'s
                # report, and the engine's step deadline bounds it.
                pass
            return None
        if node is None:
            raise ActionNotApplicable(f"{type(action).__name__} requires a target node")
        if node.node_id not in self._handles:
            raise StaleNode(node)
        if isinstance(action, ReadText):
            return node.text

        locator = self._locate(node)
        timeout = self._ready_timeout_ms
        try:
            if isinstance(action, Click):
                # A click that starts a navigation must not wait for it to finish.
                locator.click(timeout=timeout)
            elif isinstance(action, TypeText):
                locator.fill(action.text, timeout=timeout)
            elif isinstance(action, SelectOption):
                locator.select_option(label=action.option, timeout=timeout)
            else:
                raise ActionNotApplicable(f"unsupported action {type(action).__name__}")
        except PlaywrightTimeout as exc:
            if self._pending:
                # The action happened and started a navigation that has not answered
                # yet; that is observe()'s report, not an action failure.
                return None
            raise ActionNotApplicable(
                f"{node.role} {node.name!r} not actionable within {timeout}ms"
            ) from exc
        return None

    # -- capture -----------------------------------------------------------------

    def capture(self, mask: list[ElementNode]) -> bytes:
        for node in mask:
            if node.node_id not in self._handles:
                raise StaleNode(node)
        # `_locate` raises StaleNode for a node gone from the document, so a mask
        # is complete or the capture does not happen.
        locators = [self._locate(node) for node in mask]
        try:
            return self.page.screenshot(
                full_page=True, mask=locators, timeout=self._ready_timeout_ms
            )
        except PlaywrightTimeout as exc:
            raise SurfaceNotReady(f"capture not completed within {self._ready_timeout_ms}ms") from exc

    def _locate(self, node: ElementNode) -> Locator:
        """Stamp a one-off tag on the node so Playwright can drive it. The tag is
        an internal handle on a node the AX resolver already chose; it never leaves
        this adapter."""
        tag = uuid.uuid4().hex
        node_id = self._element_id(self._handles[node.node_id])
        self.cdp.send("DOM.setAttributeValue", {"nodeId": node_id, "name": TAG, "value": tag})
        for frame in self.page.frames:
            locator = frame.locator(f'[{TAG}="{tag}"]')
            if locator.count() == 1:
                return locator
        # Observed, but gone from the document since: the page moved under us.
        raise StaleNode(node)

    def _element_id(self, backend_id: int) -> int:
        """The DOM node id of the element that carries this AX node. A `StaticText`
        is a DOM text node, which cannot hold an attribute; and a text node inside
        a control's user-agent shadow tree (the typed value of an `<input>`) cannot
        be edited at all, so the walk climbs out of shadow trees to the host. The
        result bounds the AX node on screen, wider than it, never narrower."""
        # Node ids are only valid against a requested document; each navigation is a
        # new document, so request it every time rather than track it.
        self.cdp.send("DOM.getDocument", {"depth": 0})
        target = self.cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})["object"]["objectId"]
        carrier = self.cdp.send(
            "Runtime.callFunctionOn", {"objectId": target, "functionDeclaration": _CARRIER}
        )["result"]["objectId"]
        return self.cdp.send("DOM.requestNode", {"objectId": carrier})["nodeId"]

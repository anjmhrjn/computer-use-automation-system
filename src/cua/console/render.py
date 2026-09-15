"""HTML for the mock console. Server-rendered, no script, every value escaped."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from cua.schema import FailureKind, ResolutionKind

if TYPE_CHECKING:
    from .server import Pending

_STYLE = """
body{font:14px/1.4 system-ui,sans-serif;max-width:56rem;margin:2rem auto;padding:0 1rem;color:#222}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:.4rem .6rem;text-align:left}
dt{font-weight:600;margin-top:.6rem}dd{margin:0}code{background:#f4f4f4;padding:0 .2rem}
form{margin-top:1.5rem;padding:1rem;border:1px solid #ccc}label{display:block;margin:.6rem 0 .2rem}
textarea{width:100%;height:5rem}button{margin-right:.5rem;padding:.4rem .9rem}
.pending{color:#a60}.resolved{color:#276}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><h1>{escape(title)}</h1>{body}</body></html>"
    )


def index_page(items: list[Pending]) -> str:
    if not items:
        rows = "<tr><td colspan='5'>No interventions yet.</td></tr>"
    else:
        rows = "".join(
            "<tr>"
            f"<td><a href='/interventions/{escape(p.request.request_id)}'>{escape(p.request.request_id)}</a></td>"
            f"<td>{escape(p.request.capability_id)}</td>"
            f"<td><code>{escape(p.request.step_id)}</code></td>"
            f"<td>{escape(p.request.failure.kind.value)}</td>"
            f"<td class='{'resolved' if p.resolution else 'pending'}'>"
            f"{escape(p.resolution.kind.value) if p.resolution else 'pending'}</td>"
            "</tr>"
            for p in items
        )
    table = (
        "<table><thead><tr><th>request</th><th>capability</th><th>step</th>"
        f"<th>failure</th><th>status</th></tr></thead><tbody>{rows}</tbody></table>"
        "<p><a href='/'>refresh</a></p>"
    )
    return _page("Operator console", table)


def detail_page(pending: Pending) -> str:
    request = pending.request
    failure = request.failure
    facts = (
        "<dl>"
        f"<dt>Run</dt><dd><code>{escape(request.run_id)}</code></dd>"
        f"<dt>Capability</dt><dd>{escape(request.capability_id)}</dd>"
        f"<dt>Failed step</dt><dd><code>{escape(request.step_id)}</code></dd>"
        f"<dt>Failure</dt><dd><b>{escape(failure.kind.value)}</b></dd>"
        f"<dt>Expected</dt><dd>{escape(failure.expected)}</dd>"
        f"<dt>Observed</dt><dd>{escape(failure.observed)}</dd>"
        f"<dt>Location</dt><dd><code>{escape(request.location or '(not observable)')}</code></dd>"
        f"<dt>Inputs (names only)</dt><dd>{escape(', '.join(request.inputs) or '—')}</dd>"
        f"<dt>Requested</dt><dd>{escape(request.requested_at)}</dd>"
        "</dl>"
    )
    if pending.resolution is not None:
        r = pending.resolution
        outcome = (
            f"<p class='resolved'>Resolved: <b>{escape(r.kind.value)}</b>"
            + (f" from <code>{escape(r.resume_from)}</code>" if r.resume_from else "")
            + (f"<br>Note: {escape(r.note)}" if r.note else "")
            + "</p>"
        )
        return _page(f"Intervention {request.request_id}", facts + outcome + "<p><a href='/'>back</a></p>")

    options = "".join(
        f"<option value='{escape(s)}'{' selected' if s == request.step_id else ''}>{escape(s)}</option>"
        for s in request.resumable_steps
    )
    approve = (
        f"<button name='kind' value='{ResolutionKind.approve.value}'>Approve risky step and retry</button>"
        if failure.kind is FailureKind.approval_required
        else ""
    )
    form = (
        f"<form method='post' action='/interventions/{escape(request.request_id)}/resolve'>"
        "<p class='pending'>Automation is paused and the browser is yours. Put the page right, "
        "then choose how the run continues.</p>"
        f"<label for='resume_from'>Resume from step</label><select id='resume_from' name='resume_from'>{options}</select>"
        "<label for='note'>Operator note (recorded as evidence)</label><textarea id='note' name='note'></textarea>"
        "<p>"
        f"<button name='kind' value='{ResolutionKind.retry.value}'>Retry from selected step</button>"
        f"{approve}"
        f"<button name='kind' value='{ResolutionKind.abort.value}'>Abort run</button>"
        "</p></form>"
    )
    return _page(f"Intervention {request.request_id}", facts + form)

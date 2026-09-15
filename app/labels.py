"""Every user-visible string a TargetDescriptor could key on lives here so that
variant-b differs from MemberServe 3.1 in names only, never in structure. That is
the delta the item-12 tenant overlay must be able to express."""

from __future__ import annotations

Labels = dict[str, str]

VARIANTS: dict[str, Labels] = {
    "a": {
        "app_name": "MemberServe 3.1",
        "search_form": "Member search",
        "search_lead": "Find a member",
        "member_id": "Member ID",
        "search_button": "Search",
        "record_frame": "Member Record",
        "record_heading": "Member Record",
        "coverage_group": "Coverage",
        "plan_status": "Plan Status",
        "renewal_date": "Renewal Date",
    },
    "b": {
        "app_name": "MemberServe 3.1 (Tenant B)",
        "search_form": "Member lookup",
        "search_lead": "Look up a member",
        "member_id": "Member No.",
        "search_button": "Find",
        "record_frame": "Record",
        "record_heading": "Member File",
        "coverage_group": "Plan",
        "plan_status": "Status",
        "renewal_date": "Renews On",
    },
}

DEFAULT_PORTS: dict[str, int] = {"a": 5000, "b": 5001}

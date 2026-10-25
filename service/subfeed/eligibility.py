"""What may appear in the feed. Never: Shorts, live, upcoming, post-live, finished live
streams, members-only, age-restricted, or anything not public.

Works on both flat channel-listing entries (little metadata) and full video details.
`flat=True` means "this is a listing entry": absent fields are unknown rather than disqualifying,
so the video still gets enriched and judged again with the full metadata.
"""
from __future__ import annotations

MIN_DURATION_S = 61  # anything of 60 s or less is a Short
BAD_LIVE_STATUS = {"is_live", "is_upcoming", "post_live", "was_live"}
# reasons that can change later (a premiere starts, a stream finishes processing)
RECHECK_REASONS = {"upcoming", "live", "post_live"}


def ineligible_reason(entry: dict, flat: bool = False) -> str | None:
    url = entry.get("url") or ""
    if "/shorts/" in url:
        return "short"
    status = entry.get("live_status")
    if status == "is_upcoming":
        return "upcoming"
    if status == "is_live":
        return "live"
    if status == "post_live":
        return "post_live"
    if status == "was_live":
        return "was_live"
    avail = entry.get("availability")
    if avail not in (None, "public"):
        return "not_public"  # subscriber_only (members), needs_auth, premium_only, private, unlisted
    if (entry.get("age_limit") or 0) >= 18:
        return "age_restricted"
    duration = entry.get("duration")
    if duration is None:
        return None if flat else "no_duration"
    if duration < MIN_DURATION_S:
        return "short"
    return None


def is_eligible(entry: dict) -> bool:
    return ineligible_reason(entry) is None

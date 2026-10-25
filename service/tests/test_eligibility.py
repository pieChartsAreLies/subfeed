import json
from pathlib import Path

from subfeed import eligibility
from subfeed.eligibility import ineligible_reason, is_eligible

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def long_form(**kw):
    base = {"id": "abc", "url": "https://www.youtube.com/watch?v=abc", "duration": 600,
            "live_status": "not_live", "availability": "public", "age_limit": 0}
    base.update(kw)
    return base


def test_drops_shorts_by_duration_and_url():
    # real listing of Fireship's Shorts tab: every entry is a /shorts/ URL with no duration
    shorts = load("ytdlp_flat_shorts_Fireship.json")["entries"]
    assert len(shorts) >= 10
    assert all("/shorts/" in e["url"] for e in shorts)
    assert not any(is_eligible(e) for e in shorts)
    assert all(ineligible_reason(e, flat=True) == "short" for e in shorts)
    # a Short that is served under a normal watch URL is caught by duration
    assert not is_eligible(long_form(duration=45))
    assert not is_eligible(long_form(duration=60))
    assert is_eligible(long_form(duration=61))
    # a long video under a /shorts/ URL is still dropped
    assert not is_eligible(long_form(url="https://www.youtube.com/shorts/abc"))


def test_drops_live_upcoming_post_live():
    # real listings: NASA has is_live, is_upcoming and finished (was_live) streams; Lofi Girl is live
    entries = load("ytdlp_flat_streams_NASA.json")["entries"] + load("ytdlp_flat_streams_LofiGirl.json")["entries"]
    statuses = {e["live_status"] for e in entries}
    assert {"is_live", "is_upcoming", "was_live"} <= statuses
    assert not any(is_eligible({**e, "duration": e["duration"] or 3600}) for e in entries)
    assert not any(ineligible_reason(e, flat=True) is None for e in entries)
    assert ineligible_reason(long_form(live_status="post_live")) == "post_live"
    assert ineligible_reason(long_form(live_status="is_upcoming")) == "upcoming"
    assert ineligible_reason(long_form(live_status="is_live")) == "live"
    assert ineligible_reason(long_form(live_status="was_live")) == "was_live"


def test_drops_members_and_age_restricted():
    # synthetic: members-only and age-gated videos were not available to capture
    assert ineligible_reason(long_form(availability="subscriber_only")) == "not_public"
    assert ineligible_reason(long_form(availability="needs_auth")) == "not_public"
    assert ineligible_reason(long_form(availability="premium_only")) == "not_public"
    assert ineligible_reason(long_form(availability="unlisted")) == "not_public"
    assert ineligible_reason(long_form(age_limit=18)) == "age_restricted"
    assert is_eligible(long_form(availability="public", age_limit=0))


def test_keeps_finished_premiere():
    # a finished premiere is an ordinary upload: not_live, has a release timestamp and a duration
    premiere = long_form(live_status="not_live", release_timestamp=1792800000, timestamp=1792800000, duration=900)
    assert is_eligible(premiere)
    # while it has not aired it is dropped, and flagged for a recheck
    upcoming = long_form(live_status="is_upcoming", duration=None, release_timestamp=1792800000)
    assert ineligible_reason(upcoming) == "upcoming"
    assert "upcoming" in eligibility.RECHECK_REASONS


def test_flat_listing_leaves_unknowns_for_enrichment():
    e = {"id": "x", "url": "https://www.youtube.com/watch?v=x", "duration": 500, "live_status": None,
         "availability": None, "timestamp": None}
    assert ineligible_reason(e, flat=True) is None      # needs enrichment, not rejection
    assert ineligible_reason({**e, "duration": None}, flat=True) is None
    assert ineligible_reason({**e, "duration": None}) == "no_duration"


def test_real_long_form_info_is_eligible():
    for vid in ("MtxP2pyCvYA", "On3Tws3IbTk", "P8CVqDKeOKk"):
        info = load(f"ytdlp_info_{vid}.json")
        entry = {"url": f"https://www.youtube.com/watch?v={vid}", **{
            k: info.get(k) for k in ("duration", "live_status", "availability", "age_limit")}}
        assert is_eligible(entry), (vid, ineligible_reason(entry))

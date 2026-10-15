import datetime as dt
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brand  # noqa: E402
import lightd  # noqa: E402

D = dt.datetime.fromisoformat
COLOURS = {"acme.com": "#ff0000", "yourcompany.com": "#0000ff", "amazon.co.uk": "#ff9900"}


@pytest.fixture
def cfg():
    return json.loads((ROOT / "config.example.json").read_text())


def decide(cfg, when, in_call, events):
    return lightd.decide(D(when), in_call, events, cfg, COLOURS.__getitem__)


def meeting(title, start, end, *emails):
    return {"title": title, "start": D(start), "end": D(end), "emails": list(emails)}


EVENTS = [
    meeting("Acme QBR", "2026-10-15T10:00", "2026-10-15T11:00",
            "me@yourcompany.com", "a@acme.com", "b@eu.acme.com", "someone@gmail.com"),
    meeting("Standup", "2026-10-15T11:30", "2026-10-15T11:45",
            "me@yourcompany.com", "c@yourcompany.com", "room@resource.calendar.google.com"),
]


def test_company_domain_ignores_own_free_mail_and_rooms():
    emails = ["me@yourcompany.com", "x@gmail.com", "r@resource.calendar.google.com", "p@aws.acme.com", "q@acme.com"]
    assert lightd.company_domain(emails, ["yourcompany.com"]) == "acme.com"
    assert lightd.company_domain(["me@yourcompany.com"], ["yourcompany.com"]) is None


def test_registrable_handles_two_level_suffixes():
    assert lightd.registrable("shop.amazon.co.uk") == "amazon.co.uk"
    assert lightd.registrable("eu.acme.com") == "acme.com"


def test_schedule_and_day_accents(cfg):
    assert decide(cfg, "2026-10-15T07:50", False, []) == (None, "outside office hours")
    assert decide(cfg, "2026-10-15T10:00", False, [])[0] == {"ct": 5000, "bri": 100}
    assert decide(cfg, "2026-10-12T08:40", False, [])[0] == {"color": "#4fc3f7", "bri": 80}  # Monday accent
    assert decide(cfg, "2026-10-16T15:20", False, [])[0] == {"color": "#ff9e3d", "bri": 65}  # Friday accent
    assert decide(cfg, "2026-10-15T17:10", False, [])[0] is None
    assert decide(cfg, "2026-10-17T10:00", False, []) == (None, "not an office day")


def test_external_call_uses_company_colour(cfg):
    target, reason = decide(cfg, "2026-10-15T10:20", True, EVENTS)
    assert target == {"color": "#ff0000", "bri": 80}
    assert "acme.com" in reason


def test_internal_call_uses_own_colour(cfg):
    assert decide(cfg, "2026-10-15T11:35", True, EVENTS)[0] == {"color": "#0000ff", "bri": 80}


def test_call_without_calendar_event(cfg):
    assert decide(cfg, "2026-10-15T14:00", True, EVENTS)[0] == {"color": "#8a5cff", "bri": 80}


def test_preview_before_external_meeting_only(cfg):
    assert decide(cfg, "2026-10-15T09:58", False, EVENTS)[0] == {"color": "#ff0000", "bri": 35}
    # an internal meeting is about to start: no preview, the schedule continues
    assert decide(cfg, "2026-10-15T11:28", False, EVENTS) == ({"ct": 5000, "bri": 100}, "schedule 09:30 (thu)")


def test_meetings_match_with_slack_around_start_and_end(cfg):
    assert decide(cfg, "2026-10-15T11:05", True, EVENTS)[0] == {"color": "#ff0000", "bri": 80}  # QBR overran
    assert decide(cfg, "2026-10-15T11:25", True, EVENTS)[0] == {"color": "#0000ff", "bri": 80}  # joined standup early


def test_hex_helpers():
    assert brand.parse_hex("#F00") == "#ff0000"
    assert brand.parse_hex("rgb(255, 0, 0)") == "#ff0000"
    assert brand.vivid("#1ed760") and not brand.vivid("#ffffff") and not brand.vivid("#111111")

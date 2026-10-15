"""HueLights daemon: office schedule + call colours for a Hue Bluetooth light.

Every few seconds it decides what the light should look like:
  1. On a call (any mic or camera in use)  -> brand colour of the company you're meeting
     (from the current Google Calendar event's attendee domains, via macOS Calendar).
  2. A meeting with an external company starts in a couple of minutes -> dim preview of its colour.
  3. Otherwise, on office days -> the time-of-day schedule from config.json.
It only writes to the light when that decision changes, so manual tweaks survive until the
next transition. Runs inside HueLights.app (see install.sh); status goes to status.json.
"""
import asyncio
import collections
import datetime as dt
import json
import logging
import pathlib
import subprocess
import sys
import threading
import time

import brand
import hue

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
STATUS = HERE / "status.json"
CALLSTATE = HERE / "callstate"
LOG = pathlib.Path.home() / "Library/Logs/HueLights.log"

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FREE_MAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "seznam.cz", "email.cz",
    "centrum.cz", "post.cz", "volny.cz", "gmx.com", "gmx.de", "mail.ru",
}
POLL_SECONDS = 3
CALENDAR_REFRESH_SECONDS = 120
RETRY_SECONDS = 20

log = logging.getLogger("huelights")


# ---------------------------------------------------------------- calendar

class Calendar:
    """Reads events from macOS Calendar (which syncs Google Calendar) through EventKit."""

    def __init__(self):
        import EventKit
        from Foundation import NSDate

        self.EventKit, self.NSDate = EventKit, NSDate
        self.store = EventKit.EKEventStore.alloc().init()
        done, result = threading.Event(), {}

        def granted(ok, error):
            result["ok"] = bool(ok)
            done.set()

        if hasattr(self.store, "requestFullAccessToEventsWithCompletion_"):
            self.store.requestFullAccessToEventsWithCompletion_(granted)
        else:
            self.store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, granted)
        done.wait(300)
        self.granted = result.get("ok", False)
        log.info("calendar access granted=%s", self.granted)

    def sources(self):
        """Calendars macOS knows about, e.g. ['Google: you@company.com', 'iCloud: Home']."""
        calendars = self.store.calendarsForEntityType_(self.EventKit.EKEntityTypeEvent) or []
        return sorted({f"{c.source().title()}: {c.title()}" for c in calendars})

    def events(self, start, end):
        if not self.granted:
            return []
        self.store.refreshSourcesIfNecessary()
        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(
            self.NSDate.dateWithTimeIntervalSince1970_(start.timestamp()),
            self.NSDate.dateWithTimeIntervalSince1970_(end.timestamp()),
            None,
        )
        found = []
        for ev in self.store.eventsMatchingPredicate_(predicate) or []:
            if ev.isAllDay():
                continue
            emails, declined = [], False
            for person in list(ev.attendees() or []) + ([ev.organizer()] if ev.organizer() else []):
                url = str(person.URL().absoluteString()) if person.URL() else ""
                if url.lower().startswith("mailto:"):
                    emails.append(url[7:].lower())
                if person.isCurrentUser() and person.participantStatus() == self.EventKit.EKParticipantStatusDeclined:
                    declined = True
            if declined:
                continue
            found.append({
                "title": str(ev.title() or ""),
                "start": dt.datetime.fromtimestamp(ev.startDate().timeIntervalSince1970()),
                "end": dt.datetime.fromtimestamp(ev.endDate().timeIntervalSince1970()),
                "emails": sorted(set(emails)),
            })
        return found


# ---------------------------------------------------------------- decisions

def registrable(domain):
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def company_domain(emails, my_domains):
    """Most common external company domain among attendees, or None if everyone is internal."""
    counts = collections.Counter()
    for email in emails:
        domain = email.rsplit("@", 1)[-1]
        if domain in FREE_MAIL or domain.endswith("calendar.google.com"):
            continue
        if any(domain == mine or domain.endswith("." + mine) for mine in my_domains):
            continue
        counts[registrable(domain)] += 1
    return counts.most_common(1)[0][0] if counts else None


def current_event(events, now, minutes):
    """The meeting you're most likely in right now: overlapping now, latest start wins."""
    slack = dt.timedelta(minutes=minutes)
    live = [e for e in events if e["start"] - slack <= now <= e["end"] + slack and e["emails"]]
    return max(live, key=lambda e: e["start"]) if live else None


def schedule_target(now, cfg):
    day = DAYS[now.weekday()]
    if day not in cfg["office_days"]:
        return None, "not an office day"
    slots = cfg["schedule"] + cfg.get("day_accents", {}).get(day, [])
    slots = sorted(slots, key=lambda s: s[0])  # same time: accent sorts after base and wins
    hhmm = now.strftime("%H:%M")
    active = [s for s in slots if s[0] <= hhmm]
    if not active or active[-1][1] is None:
        return None, "outside office hours"
    return dict(active[-1][1]), f"schedule {active[-1][0]} ({day})"


def decide(now, in_call, events, cfg, colour_for):
    """Return (target, reason). target None means 'leave the light alone / switch off after office'."""
    call = cfg["call"]
    my_domains = cfg["my_domains"]
    if in_call:
        event = current_event(events, now, call["event_match_minutes"])
        if not event:
            return {"color": call["no_calendar_colour"], "bri": call["brightness"]}, "call (no calendar event)"
        domain = company_domain(event["emails"], my_domains) or my_domains[0]
        return {"color": colour_for(domain), "bri": call["brightness"]}, f"call: {event['title']} -> {domain}"

    soon = now + dt.timedelta(minutes=call["preview_minutes"])
    upcoming = [e for e in events if now < e["start"] <= soon and company_domain(e["emails"], my_domains)]
    if upcoming:
        event = min(upcoming, key=lambda e: e["start"])
        domain = company_domain(event["emails"], my_domains)
        return {"color": colour_for(domain), "bri": call["preview_brightness"]}, f"preview: {event['title']} -> {domain}"

    return schedule_target(now, cfg)


# ---------------------------------------------------------------- daemon

def read_callstate():
    try:
        out = subprocess.run([str(CALLSTATE)], capture_output=True, text=True, timeout=5).stdout
        state = json.loads(out)
        return bool(state["mic"] or state["cam"])
    except Exception as e:
        log.warning("callstate failed: %s", e)
        return False


def load_config():
    return json.loads(CONFIG.read_text())


async def find_light():
    """Scan once on first start; the address is remembered in hue_light.json."""
    while not hue.CONFIG.exists():
        log.info("no light saved yet, scanning for a Hue Bluetooth light...")
        await hue.scan()
        if not hue.CONFIG.exists():
            await asyncio.sleep(30)


def log_calendar_overview(calendar, cfg):
    """One line at startup so it's easy to see whether the calendar is wired up."""
    log.info("calendars: %s", calendar.sources())
    now = dt.datetime.now()
    week = calendar.events(now, now + dt.timedelta(days=7))
    companies = collections.Counter(
        company_domain(e["emails"], cfg["my_domains"]) or "internal" for e in week if e["emails"]
    )
    log.info("meetings in the next 7 days: %d, by company: %s", len(week), dict(companies))


def write_status(now, in_call, reason, target, applied, events, cfg):
    upcoming = [
        {"start": e["start"].strftime("%a %H:%M"),
         "company": company_domain(e["emails"], cfg["my_domains"]) or ("internal" if e["emails"] else "-")}
        for e in events if e["end"] > now
    ][:8]
    STATUS.write_text(json.dumps({
        "time": now.isoformat(timespec="seconds"), "in_call": in_call, "reason": reason,
        "target": target, "applied": applied, "upcoming": upcoming,
    }, indent=2))


async def run():
    cfg = load_config()
    await find_light()
    calendar = await asyncio.to_thread(Calendar)
    await asyncio.to_thread(log_calendar_overview, calendar, cfg)

    events, last_calendar = [], 0.0
    last_active, applied, retry_at = 0.0, None, 0.0

    def colour_for(domain):
        return brand.brand_colour(domain, cfg.get("brand_overrides"))

    while True:
        now = dt.datetime.now()
        if time.time() - last_calendar > CALENDAR_REFRESH_SECONDS:
            cfg = load_config()
            try:
                events = await asyncio.to_thread(calendar.events, now - dt.timedelta(hours=6), now + dt.timedelta(hours=12))
                for e in events:  # warm the brand cache before calls start
                    domain = company_domain(e["emails"], cfg["my_domains"])
                    if domain and e["start"] > now:
                        await asyncio.to_thread(colour_for, domain)
            except Exception:
                log.exception("calendar refresh failed")
            last_calendar = time.time()

        if read_callstate():
            last_active = time.time()
        in_call = bool(last_active) and time.time() - last_active < cfg["call"]["end_grace_seconds"]

        target, reason = await asyncio.to_thread(decide, now, in_call, events, cfg, colour_for)
        write_status(now, in_call, reason, target, applied, events, cfg)

        if target != applied and time.time() >= retry_at:
            # leaving the managed window (after office, call ended at night) -> optionally switch off
            send = target if target is not None else ({"on": False} if cfg.get("turn_off_after_office") else None)
            try:
                if send is not None:
                    await hue.apply(send)
                    log.info("applied %s (%s)", send, reason)
                applied = target
            except Exception as e:
                log.warning("could not reach light (%s); retrying in %ss", e, RETRY_SECONDS)
                retry_at = time.time() + RETRY_SECONDS

        await asyncio.sleep(POLL_SECONDS)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
    log.info("HueLights starting")
    asyncio.run(run())


if __name__ == "__main__":
    main()

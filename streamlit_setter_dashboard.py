#!/usr/bin/env python3
"""
streamlit_setter_dashboard.py - LIVE setter SCORECARD (William, IG side).

A daily gut-check scorecard, not a dashboard: verdict-first, read top-down.
Daily view (default) = "is he on track today + near the termination line".
Weekly view (toggle) = "how is the week / trend going".

Data: Calendly + GHL pulled into setter_cache.json by `sync`; the page reads the
cache so it loads instantly. Design = solid vivid cards on the #07070b dark system.

RUN:   python -m streamlit run streamlit_setter_dashboard.py
SYNC:  python streamlit_setter_dashboard.py sync
TEST:  python streamlit_setter_dashboard.py test
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import Counter

try:
    from zoneinfo import ZoneInfo
    BIZ_TZ = ZoneInfo("America/Chicago")
except Exception:
    BIZ_TZ = timezone(timedelta(hours=-5))

# ── TARGETS + TERMINATION RULE (Claude's rec; Arman/Noah to confirm) ─────────────
DAILY_FLOOR = 3
WEEKLY_TARGET = 20
TERMINATION_STREAK = 3   # consecutive zero-booking days = out

CAL_BASE = "https://api.calendly.com"
CAL_USER = "https://api.calendly.com/users/f04b69c0-b4ef-4774-ba1f-58e296f188b3"
IG_EVENT_TYPE = "https://api.calendly.com/event_types/18d04c8e-2a46-4263-9e6b-a49537dd6bc9"
YT_EVENT_TYPE = "https://api.calendly.com/event_types/520c32e6-c6cd-47bb-8249-1da28e6c736d"
GHL_BASE = "https://services.leadconnectorhq.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) setter-dashboard"

# Palette
BG, CARD, BORDER = "#07070b", "#0d0d14", "rgba(255,255,255,0.08)"
TXT, MUTE, DIM = "#f2f2fa", "#9a9ab0", "#6a6a82"
RED, AMBER, GREEN, BLUE = "#f87171", "#fbbf24", "#4ade80", "#60a5fa"
# Solid vivid card fills (matches the vivid wireframe, not faint tints)
RED_BG, RED_BD = "#48201f", "rgba(248,113,113,0.55)"
AMBER_BG, AMBER_BD = "#37300f", "rgba(251,191,36,0.55)"
GREEN_BG, GREEN_BD = "#163021", "rgba(74,222,128,0.55)"

ICON_WARN = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px">'
             '<path d="M12 9v4"/><path d="M10.36 3.6 2.26 17.1A1.9 1.9 0 0 0 3.9 20h16.2a1.9 1.9 0 0 0 1.64-2.9L13.64 3.6a1.9 1.9 0 0 0-3.28 0z"/><path d="M12 17h.01"/></svg>')
ICON_CAL = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px">'
            '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M16 3v4M8 3v4M4 11h16"/></svg>')


def _secret(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        try:
            import streamlit as st
            v = st.secrets.get(name, "")
        except Exception:
            v = ""
    return (v or "").strip()


# ── Calendly ─────────────────────────────────────────────────────────────────────
def fetch_calendly_events(days_back: int = 70, days_fwd: int = 35) -> list:
    token = _secret("CALENDLY_API_TOKEN")
    if not token:
        raise RuntimeError("CALENDLY_API_TOKEN not set")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": UA}
    now = datetime.now(timezone.utc)
    params = {
        "user": CAL_USER,
        "min_start_time": (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "max_start_time": (now + timedelta(days=days_fwd)).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "count": 100, "sort": "start_time:asc",
    }
    events, url = [], CAL_BASE + "/scheduled_events"
    for _ in range(20):
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        events.extend(data.get("collection", []) or [])
        nxt = (data.get("pagination") or {}).get("next_page_token")
        if not nxt:
            break
        params = dict(params)
        params["page_token"] = nxt
    return events


def _created_local(ev: dict):
    raw = ev.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(BIZ_TZ)
    except Exception:
        return None


def booking_metrics(events: list) -> dict:
    now = datetime.now(BIZ_TZ)
    today = now.date()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    ig = [e for e in events if e.get("event_type") == IG_EVENT_TYPE]
    yt = [e for e in events if e.get("event_type") == YT_EVENT_TYPE]

    def cdate(e):
        d = _created_local(e)
        return d.date() if d else None

    ig_dates = [(e, cdate(e)) for e in ig]
    today_booked = sum(1 for _, d in ig_dates if d == today)
    week_booked = sum(1 for e, d in ig_dates if d and _created_local(e) >= monday)

    daily = {}
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        daily[day] = sum(1 for _, d in ig_dates if d == day)

    weekly = Counter()
    for e, d in ig_dates:
        if not d:
            continue
        iso = datetime.combine(d, datetime.min.time()).isocalendar()
        weekly[(iso[0], iso[1])] += 1

    active = [e for e in ig if e.get("status") == "active"]
    canceled = [e for e in ig if e.get("status") == "canceled"]

    def is_reschedule(e):
        c = e.get("cancellation") or {}
        return "reschedul" in (c.get("reason") or "").lower() or c.get("canceler_type") == "host"

    nowu = datetime.now(timezone.utc)
    upcoming = 0
    for e in active:
        try:
            if datetime.fromisoformat((e.get("start_time") or "").replace("Z", "+00:00")) >= nowu:
                upcoming += 1
        except Exception:
            pass

    return {
        "today_booked": today_booked, "week_booked": week_booked,
        "daily": daily, "weekly": dict(weekly),
        "ig_total": len(ig), "ig_active": len(active), "ig_canceled": len(canceled),
        "reschedules": sum(1 for e in canceled if is_reschedule(e)),
        "true_cancels": sum(1 for e in canceled if not is_reschedule(e)),
        "upcoming": upcoming, "yt_total": len(yt),
        "zero_days": sum(1 for v in daily.values() if v == 0),
    }


# ── GHL activity (kept for cache/future; not shown on the scorecard) ─────────────
def fetch_ghl_activity(days: int = 7) -> dict:
    token, loc = _secret("GHL_PIT_TOKEN"), _secret("GHL_LOCATION_ID")
    if not token or not loc:
        raise RuntimeError("GHL creds not set")
    headers = {"Authorization": f"Bearer {token}", "Version": "2021-04-15", "Accept": "application/json"}
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    active = awaiting = 0
    start_after = None
    for _ in range(12):
        params = {"locationId": loc, "limit": 100, "sort": "desc", "sortBy": "last_message_date"}
        if start_after is not None:
            params["startAfterDate"] = start_after
        r = requests.get(GHL_BASE + "/conversations/search", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        convos = r.json().get("conversations", []) or []
        if not convos:
            break
        stop = False
        for c in convos:
            lmd = c.get("lastMessageDate") or c.get("dateUpdated")
            if isinstance(lmd, str):
                try:
                    lmd = int(datetime.fromisoformat(lmd.replace("Z", "+00:00")).timestamp() * 1000)
                except Exception:
                    lmd = None
            if lmd is None:
                continue
            if lmd < cutoff:
                stop = True
                break
            if c.get("lastMessageType") == "TYPE_INSTAGRAM":
                active += 1
                if c.get("lastMessageDirection") == "inbound":
                    awaiting += 1
        if stop or len(convos) < 100:
            break
        nxt = convos[-1].get("lastMessageDate") or convos[-1].get("dateUpdated")
        if isinstance(nxt, str):
            try:
                nxt = int(datetime.fromisoformat(nxt.replace("Z", "+00:00")).timestamp() * 1000)
            except Exception:
                break
        start_after = nxt
    return {"active_threads": active, "awaiting_reply": awaiting}


# ── Cache ────────────────────────────────────────────────────────────────────────
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setter_cache.json")


def _serialize(m):
    m = dict(m)
    m["daily"] = [[d.isoformat(), v] for d, v in m["daily"].items()]
    m["weekly"] = [[k[0], k[1], v] for k, v in m["weekly"].items()]
    return m


def _deserialize(m):
    from datetime import date
    m = dict(m)
    m["daily"] = {date.fromisoformat(d): v for d, v in m["daily"]}
    m["weekly"] = {(y, w): v for y, w, v in m["weekly"]}
    return m


def fetch_all() -> dict:
    events = fetch_calendly_events()
    m = booking_metrics(events)
    try:
        ghl = fetch_ghl_activity()
    except Exception:
        ghl = {"active_threads": None, "awaiting_reply": None}
    return {"generated_at": datetime.now(BIZ_TZ).isoformat(), "metrics": _serialize(m), "ghl": ghl}


def write_cache(data: dict):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def read_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sync():
    data = fetch_all()
    write_cache(data)
    print("cache written:", CACHE_PATH, "at", data["generated_at"])


def _cli_test():
    ev = fetch_calendly_events()
    m = booking_metrics(ev)
    print(f"today {m['today_booked']}/{DAILY_FLOOR} | week {m['week_booked']}/{WEEKLY_TARGET} | "
          f"streak {zero_streak(m['daily'])} | zero-days {m['zero_days']}")


# ── Scorecard logic + render ─────────────────────────────────────────────────────
def zero_streak(daily: dict) -> int:
    streak = 0
    for d, v in sorted(daily.items(), reverse=True):
        if v == 0:
            streak += 1
        else:
            break
    return streak


def _bar(h, color, label, last, value):
    outline = "outline:2px solid rgba(255,255,255,0.22);outline-offset:2px;" if last else ""
    lc = TXT if last else MUTE
    return (f'<div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:7px;">'
            f'<span style="font-size:12px;font-weight:700;color:{lc};">{value}</span>'
            f'<div style="width:100%;max-width:38px;height:{h}px;background:{color};border-radius:6px 6px 0 0;{outline}"></div>'
            f'<span style="font-size:11px;color:{lc};">{label}</span></div>')


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stApp"], [data-testid="stApp"] *,
.stMarkdown, .stMarkdown *, button, span, div, p, h1, h2, h3 {{ font-family:'Inter', system-ui, sans-serif !important; }}
#MainMenu, footer, header {{ visibility:hidden; }}
.stApp {{ background:{BG} !important; }}
.block-container {{ padding:24px 30px 44px !important; max-width:1120px !important; }}
div[data-testid="stSegmentedControl"] button {{ font-size:12px !important; font-weight:600 !important; }}
</style>
"""


def _verdict_card(label, num, denom, on_good, badge_good, badge_bad, sub):
    bg, bd, col = (GREEN_BG, GREEN_BD, GREEN) if on_good else (RED_BG, RED_BD, RED)
    arrow = "&#8593;" if on_good else "&#8595;"
    badge = badge_good if on_good else badge_bad
    return f"""
<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;background:{bg};border:1px solid {bd};border-radius:18px;padding:24px 28px;margin-bottom:14px;">
  <div><div style="font-size:13px;font-weight:600;color:{col};margin-bottom:7px;">{label}</div>
  <div style="display:flex;align-items:baseline;gap:11px;"><span style="font-size:52px;font-weight:800;color:{TXT};line-height:1;letter-spacing:-2px;">{num}</span><span style="font-size:20px;color:{MUTE};">/ {denom} booked</span></div></div>
  <div style="text-align:right;"><span style="font-size:15px;font-weight:700;color:{col};background:rgba(0,0,0,0.32);border:1px solid {bd};padding:9px 17px;border-radius:10px;white-space:nowrap;">{arrow} {badge}</span>
  <div style="font-size:12.5px;color:{MUTE};margin-top:10px;">{sub}</div></div>
</div>"""


def render_scorecard(m: dict, view: str) -> str:
    now = datetime.now(BIZ_TZ)
    days_elapsed = now.weekday() + 1
    days_left = max(0, 7 - days_elapsed)
    dl = f"{days_left} day{'s' if days_left != 1 else ''} left"
    pace = WEEKLY_TARGET * days_elapsed / 7.0
    week_on_pace = m["week_booked"] >= pace * 0.85
    week_pct = min(100, round(100 * m["week_booked"] / WEEKLY_TARGET))

    if view == "Weekly":
        hero = _verdict_card("This week", m["week_booked"], WEEKLY_TARGET, week_on_pace,
                             "On pace", "Behind", dl)
        wk = m["weekly"]
        keys = sorted(wk.keys())[-7:]
        vals = [wk[k] for k in keys]
        maxv = max(vals + [WEEKLY_TARGET, 1])
        bars = "".join(_bar(max(6, round(v / maxv * 90)), GREEN, f"W{k[1]}", i == len(keys) - 1, v)
                       for i, (k, v) in enumerate(zip(keys, vals)))
        best = max(vals) if vals else 0
        avg = round(sum(vals) / len(vals)) if vals else 0
        stat = lambda lbl, val, c: (f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:16px;padding:16px 20px;">'
                                    f'<div style="font-size:12.5px;color:{MUTE};">{lbl}</div>'
                                    f'<div style="font-size:28px;font-weight:800;color:{c};margin-top:5px;">{val}</div></div>')
        return hero + f"""
<div style="background:{CARD};border:1px solid {BORDER};border-radius:18px;padding:20px 24px 14px;margin-bottom:14px;">
  <div style="font-size:12.5px;color:{MUTE};margin-bottom:18px;">Bookings per week &middot; target {WEEKLY_TARGET}</div>
  <div style="display:flex;align-items:flex-end;gap:16px;height:108px;">{bars}</div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;">
  {stat("Best week", best, TXT)}{stat("7-week average", avg, TXT)}{stat("Zero-days (14d)", m['zero_days'], AMBER)}
</div>"""

    # ── Daily view ──
    today = m["today_booked"]
    floor_hit = today >= DAILY_FLOOR
    need = max(0, DAILY_FLOOR - today)
    streak = zero_streak(m["daily"])
    hero = _verdict_card("Today", today, DAILY_FLOOR, floor_hit, "On track", "Behind",
                         "floor cleared" if floor_hit else f"{need} more to clear the floor")

    if streak >= TERMINATION_STREAK:
        tcol, tbg, tbd, ttitle, tsub = RED, RED_BG, RED_BD, "Termination line hit", "rule breached"
    elif streak == TERMINATION_STREAK - 1:
        tcol, tbg, tbd, ttitle, tsub = RED, RED_BG, RED_BD, f"{streak} of {TERMINATION_STREAK} zero-days", "1 more zero-booking day and he's out"
    elif streak >= 1:
        tcol, tbg, tbd, ttitle, tsub = AMBER, AMBER_BG, AMBER_BD, f"{streak} of {TERMINATION_STREAK} zero-days", f"{TERMINATION_STREAK - streak} more zero-booking days and he's out"
    else:
        tcol, tbg, tbd, ttitle, tsub = GREEN, GREEN_BG, GREEN_BD, "No zero-day streak", "clear of the termination line"
    pips = "".join(
        f'<span style="width:42px;height:10px;border-radius:5px;background:{tcol if i < streak else "rgba(255,255,255,0.13)"};"></span>'
        for i in range(TERMINATION_STREAK))

    wcol = GREEN if week_on_pace else RED
    wbadge = "On pace" if week_on_pace else "Behind"
    items = sorted(m["daily"].items())[-9:]
    dmax = max([v for _, v in items] + [1])
    dbars = "".join(
        _bar(6 if v == 0 else max(10, round(v / dmax * 80)),
             RED if v == 0 else (AMBER if v >= 4 else BLUE),
             str(d.day), i == len(items) - 1, v)
        for i, (d, v) in enumerate(items))

    return hero + f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;">
  <div style="background:{tbg};border:1px solid {tbd};border-radius:18px;padding:18px 22px;">
    <div style="font-size:12.5px;font-weight:600;color:{tcol};margin-bottom:14px;">{ICON_WARN}Termination watch</div>
    <div style="display:flex;gap:8px;margin-bottom:14px;">{pips}</div>
    <div style="font-size:15px;font-weight:700;color:{TXT};">{ttitle}</div>
    <div style="font-size:12.5px;color:{MUTE};margin-top:4px;">{tsub}</div>
  </div>
  <div style="background:{CARD};border:1px solid {BORDER};border-radius:18px;padding:18px 22px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;"><span style="font-size:12.5px;color:{MUTE};">{ICON_CAL}This week</span><span style="font-size:12.5px;font-weight:700;color:{wcol};">{wbadge}</span></div>
    <div style="display:flex;align-items:baseline;gap:9px;margin-bottom:12px;"><span style="font-size:30px;font-weight:800;color:{TXT};">{m['week_booked']}</span><span style="font-size:14px;color:{DIM};">/ {WEEKLY_TARGET} &middot; {dl}</span></div>
    <div style="height:7px;background:rgba(255,255,255,0.08);border-radius:7px;overflow:hidden;"><div style="height:100%;width:{week_pct}%;background:{wcol};border-radius:7px;"></div></div>
  </div>
</div>
<div style="background:{CARD};border:1px solid {BORDER};border-radius:18px;padding:20px 24px 14px;margin-bottom:14px;">
  <div style="font-size:12.5px;color:{MUTE};margin-bottom:18px;">Last 9 days &middot; the run of days is what matters, not the total</div>
  <div style="display:flex;align-items:flex-end;gap:10px;height:94px;">{dbars}</div>
</div>
<div style="background:{CARD};border:1px solid {BORDER};border-radius:16px;padding:15px 20px;font-size:13px;color:{MUTE};line-height:1.55;">
  Bookings are sticking. {m['reschedules']} of the cancels are reschedules, only {m['true_cancels']} real drop-offs. <span style="color:{TXT};font-weight:600;">One thing to fix:</span> he's coaching the money answer on the form, which pads the count with leads who cannot pay.
</div>"""


# ── Streamlit UI ─────────────────────────────────────────────────────────────────
def _run_streamlit():
    import streamlit as st
    st.set_page_config(page_title="Setter Scorecard: William", page_icon="📊",
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)

    cached = read_cache()
    if cached is None:
        with st.spinner("First data pull (slow, up to ~2 min). Caching so every load after this is instant..."):
            cached = fetch_all()
            write_cache(cached)
    m = _deserialize(cached["metrics"])
    try:
        updated = datetime.fromisoformat(cached["generated_at"]).strftime("%b %d, %I:%M %p")
    except Exception:
        updated = ""

    hL, hR = st.columns([3, 1.1])
    with hL:
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;color:{DIM};text-transform:uppercase;letter-spacing:1.6px;margin-bottom:3px;">Setter scorecard</div>'
            f'<div style="font-size:24px;font-weight:800;color:{TXT};letter-spacing:-0.8px;line-height:1.05;">William</div>'
            f'<div style="font-size:11px;color:{DIM};margin-top:4px;">updated {updated}</div>',
            unsafe_allow_html=True)
    with hR:
        view = st.segmented_control("view", ["Daily", "Weekly"], default="Daily",
                                    label_visibility="collapsed") or "Daily"

    st.markdown('<div style="height:18px;"></div>' + render_scorecard(m, view), unsafe_allow_html=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        _cli_test()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        sync()
        return
    _run_streamlit()


if __name__ == "__main__":
    main()

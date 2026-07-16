#!/usr/bin/env python3
"""
WhatsApp Chat Analyzer
======================

Takes an exported WhatsApp chat (a .zip, or the _chat.txt / "WhatsApp Chat with X.txt"
directly) and produces a single self-contained interactive HTML report of chat statistics.

Usage
-----
    python whatsapp_analyzer.py path/to/chat.zip
    python whatsapp_analyzer.py path/to/chat.zip -o report.html
    python whatsapp_analyzer.py "_chat.txt" --date-order dmy

Options
-------
    -o / --output        Output HTML path (default: <input>_analysis.html next to input)
    --date-order         Force date order: dmy | mdy | ymd  (default: auto-detect)
    --session-gap        Minutes of silence that starts a new "conversation" (default: 180 = 3 hours)
    --me                 Your own display name, for framing (optional)

No third-party packages required (standard library only). The HTML report is fully
self-contained (inline CSS + inline SVG charts) and works offline — nothing is uploaded.
"""

import argparse
import html
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# --------------------------------------------------------------------------- #
#  Constants: media / system / emoji / stopwords
# --------------------------------------------------------------------------- #

LTR = "‎"   # left-to-right mark WhatsApp sprinkles before media/system lines
RTL = "‏"
NBSP = " "
NNBSP = " "  # narrow no-break space (iOS puts this before AM/PM sometimes)
ZWJ = "‍"

# iOS type-specific "no media" placeholders  ->  normalized media type
IOS_MEDIA = {
    "image omitted": "image",
    "video omitted": "video",
    "audio omitted": "audio",
    "sticker omitted": "sticker",
    "gif omitted": "gif",
    "document omitted": "document",
    "contact card omitted": "contact",
    "location omitted": "location",
}

# Android with-media file prefixes  ->  media type  (e.g. "IMG-2024...jpg (file attached)")
ANDROID_PREFIX = {
    "IMG": "image", "VID": "video", "PTT": "audio", "AUD": "audio",
    "STK": "sticker", "GIF": "gif", "DOC": "document",
}

EXT_TYPE = {
    "jpg": "image", "jpeg": "image", "png": "image", "webp": "sticker", "gif": "gif",
    "mp4": "video", "mov": "video", "3gp": "video",
    "opus": "audio", "m4a": "audio", "mp3": "audio", "aac": "audio", "ogg": "audio", "wav": "audio",
    "pdf": "document", "doc": "document", "docx": "document", "xls": "document",
    "xlsx": "document", "ppt": "document", "pptx": "document", "txt": "document",
    "vcf": "contact",
}

# Phrases that mark a line as a WhatsApp system / group event (no real sender).
SYSTEM_MARKERS = [
    "messages and calls are end-to-end encrypted",
    "your security code with",
    "changed the subject",
    "changed this group's icon",
    "changed the group description",
    "changed their phone number",
    "changed to a new number",
    "created group",
    "created this group",
    "added",
    "removed",
    "left",
    "joined using this group's invite link",
    "you're now an admin",
    "now an admin",
    "you were added",
    "you joined",
    "pinned a message",
    "turned on disappearing messages",
    "turned off disappearing messages",
    "this group's settings",
    "waiting for this message",
    "missed voice call",
    "missed video call",
    "security code changed",
    "blocked this contact",
    "you blocked",
    "you unblocked",
    "deleted this group",
    "changed the group's settings",
    "reset this group's invite link",
]

DELETED_MARKERS = [
    "this message was deleted",
    "you deleted this message",
    "this message was deleted.",
]

EDITED_SUFFIXES = ["<this message was edited>"]

VIEW_ONCE_MARKERS = ["view once", "one-time", "one time", "view-once"]

# Emoji: core pictographic ranges. Grouped so ZWJ sequences + skin tones count as ONE.
_EMO = ("\U0001F300-\U0001FAFF"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "\U00002B00-\U00002BFF"
        "\U00002300-\U000023FF"
        "\U0001F000-\U0001F0FF"
        "\U0001F1E6-\U0001F1FF")
EMOJI_SEQ = re.compile(
    "[" + _EMO + "]"
    "(?:" + ZWJ + "[" + _EMO + "]|[️\U0001F3FB-\U0001F3FF])*"
)

URL_RE = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # letters only (no digits/underscore)

STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each
few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself
him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't
my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's
these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd we'll
we're we've were weren't what what's when when's where where's which while who who's whom why why's with won't
would wouldn't you you'd you'll you're you've your yours yourself yourselves u ur im dont doesnt cant ok okay
yeah yep yup nope na haha haha hahaha lol lmao hmm hey hi hello oh ah yes no got get one like just
hai ha haan nahi nahin kya ka ki ke ko se me mein mai main ye yeh wo woh voh aur bhi hi tha thi the na
toh to bas kar kr hi hu hun ho hai hain kyu kyun acha accha thik theek ab ye bhai yaar re le lo de do
""".split())


# --------------------------------------------------------------------------- #
#  Reading the export
# --------------------------------------------------------------------------- #

def read_chat_text(path):
    """Return (chat_text, display_name) from a .zip, .txt, or folder."""
    display = os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]

    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if fn.lower().endswith(".txt"):
                with open(os.path.join(path, fn), "rb") as f:
                    return _decode(f.read()), _name_from_filename(fn, display)
        raise SystemExit(f"No .txt file found in folder: {path}")

    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            txts = [n for n in z.namelist() if n.lower().endswith(".txt")]
            if not txts:
                raise SystemExit("No .txt file found inside the zip.")
            # Prefer _chat.txt / 'WhatsApp Chat with ...'
            txts.sort(key=lambda n: (
                "_chat" not in n.lower(),
                "whatsapp chat" not in n.lower(),
                len(n),
            ))
            chosen = txts[0]
            return _decode(z.read(chosen)), _name_from_filename(chosen, display)

    with open(path, "rb") as f:
        return _decode(f.read()), _name_from_filename(os.path.basename(path), display)


def _decode(raw):
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def _name_from_filename(fn, fallback):
    m = re.search(r"WhatsApp Chat (?:with|-) (.+?)\.txt$", fn, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if "_chat" in fn.lower():
        return fallback
    return os.path.splitext(fn)[0]


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #

# iOS:      [2024-03-15, 10:30:45 PM] Sender: message
# iOS(alt): [15/03/2024, 22:30:45] Sender: message
IOS_RE = re.compile(r"^[‎‏]*\[(?P<stamp>[^\]]+)\]\s*(?P<rest>.*)$", re.DOTALL)

# Android:  15/03/2024, 22:30 - Sender: message
# Android:  3/15/24, 10:30 PM - Sender: message
AND_RE = re.compile(
    r"^[‎‏]*(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?\s?[Mm]\.?)?)"
    r"\s*[-–]\s*(?P<rest>.*)$",
    re.DOTALL,
)

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([APap])\.?\s?[Mm]\.?", re.IGNORECASE)
TIME_RE_24 = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _clean(s):
    return s.replace(LTR, "").replace(RTL, "").replace(NNBSP, " ").replace(NBSP, " ")


def split_stamp(stamp):
    """iOS bracket content -> (date_str, time_str). Split on the last comma."""
    stamp = _clean(stamp).strip()
    if "," in stamp:
        d, t = stamp.rsplit(",", 1)
        return d.strip(), t.strip()
    # Some locales use no comma; split on first space group
    parts = stamp.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return stamp, ""


def detect_date_order(date_strings, forced=None):
    """Return (year_idx, month_idx, day_idx) into the split date parts."""
    if forced == "ymd":
        return (0, 1, 2)
    if forced == "dmy":
        return (2, 1, 0)
    if forced == "mdy":
        return (2, 0, 1)

    maxv = [0, 0, 0]
    year_first = 0
    year_last = 0
    for ds in date_strings:
        parts = re.split(r"[./\-]", ds.strip())
        if len(parts) != 3:
            continue
        try:
            vals = [int(p) for p in parts]
        except ValueError:
            continue
        for i in range(3):
            maxv[i] = max(maxv[i], vals[i])
        if len(parts[0]) == 4:
            year_first += 1
        elif len(parts[2]) == 4:
            year_last += 1

    if year_first > year_last and year_first > 0:
        y = 0
        a, b = 1, 2  # remaining
    else:
        y = 2
        a, b = 0, 1

    # decide which of a,b is the day using >12 evidence
    if maxv[a] > 12 >= maxv[b]:
        day, month = a, b
    elif maxv[b] > 12 >= maxv[a]:
        day, month = b, a
    else:
        # ambiguous defaults
        if y == 0:            # ISO year-first -> Y-M-D
            month, day = a, b
        else:                 # year-last -> assume D/M/Y (international default)
            day, month = a, b
    return (y, month, day)


def parse_date(ds, order):
    parts = re.split(r"[./\-]", ds.strip())
    if len(parts) != 3:
        return None
    try:
        vals = [int(p) for p in parts]
    except ValueError:
        return None
    y = vals[order[0]]
    mo = vals[order[1]]
    d = vals[order[2]]
    if y < 100:
        y += 2000 if y < 70 else 1900
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        # try swapping month/day if clearly wrong
        if 1 <= d <= 12 and 1 <= mo <= 31:
            mo, d = d, mo
        else:
            return None
    return y, mo, d


def parse_time(ts):
    ts = _clean(ts).strip()
    m = TIME_RE.search(ts)
    if m:
        h, mi, se, ap = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4).lower()
        if ap == "p" and h != 12:
            h += 12
        elif ap == "a" and h == 12:
            h = 0
        return h, mi, se
    m = TIME_RE_24.search(ts)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return None


def classify(text):
    """Return (kind, media_type, view_once, edited, clean_text)."""
    t = _clean(text)
    edited = False
    low = t.strip().lower()
    for suf in EDITED_SUFFIXES:
        if low.endswith(suf):
            edited = True
            t = t.strip()[: -len(suf)].strip()
            low = t.strip().lower()

    view_once = any(v in low for v in VIEW_ONCE_MARKERS)

    # deleted
    for d in DELETED_MARKERS:
        if low == d or low.startswith(d):
            return "deleted", None, view_once, edited, t

    # iOS type-specific media
    for phrase, mtype in IOS_MEDIA.items():
        if phrase in low:
            return "media", mtype, view_once, edited, t

    # generic Android media
    if "<media omitted>" in low or low == "media omitted":
        return "media", "media", view_once, edited, t

    # iOS attached / Android "(file attached)"
    m = re.search(r"<attached:\s*([^>]+)>", t, re.IGNORECASE)
    if not m:
        m = re.search(r"([A-Za-z0-9._\-]+\.\w{2,4})\s*\(file attached\)", t, re.IGNORECASE)
    if m:
        fname = m.group(1)
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        prefix = fname.split("-", 1)[0].upper()
        mtype = ANDROID_PREFIX.get(prefix) or EXT_TYPE.get(ext, "document")
        return "media", mtype, view_once, edited, t

    if "location:" in low or ("maps.google" in low and "http" in low) or "live location" in low:
        return "media", "location", view_once, edited, t
    if low == "null" or low == "":
        return "system", None, view_once, edited, t

    return "text", None, view_once, edited, t


def looks_system(rest_before_colon, whole):
    low = whole.lower()
    return any(mk in low for mk in SYSTEM_MARKERS)


def parse(text, forced_order=None):
    """Parse chat text into a list of message dicts + meta."""
    lines = text.split("\n")

    # -- pass 1: split lines into (date, time, rest) raw records, joining continuations
    raw_records = []   # (date_str or None, time_str or None, rest, is_new)
    date_strings = []
    for line in lines:
        line = line.rstrip("\n").rstrip("\r")
        mi = IOS_RE.match(line)
        if mi and ("]" in line):
            d, t = split_stamp(mi.group("stamp"))
            # Only accept as a new message if the stamp actually contains a time
            if parse_time(t) is not None or TIME_RE_24.search(_clean(t)):
                raw_records.append([d, t, mi.group("rest"), True])
                date_strings.append(d)
                continue
        ma = AND_RE.match(line)
        if ma:
            raw_records.append([ma.group("date"), ma.group("time"), ma.group("rest"), True])
            date_strings.append(ma.group("date"))
            continue
        # continuation of previous message
        if raw_records:
            raw_records[-1][2] += "\n" + line
        else:
            # preamble before first stamped line
            raw_records.append([None, None, line, False])

    order = detect_date_order(date_strings, forced_order)

    # -- pass 2: build messages
    messages = []
    for d, t, rest, is_new in raw_records:
        if not is_new:
            continue
        dt = None
        if d and t:
            dparts = parse_date(d, order)
            tparts = parse_time(t)
            if dparts and tparts:
                try:
                    dt = datetime(dparts[0], dparts[1], dparts[2],
                                  tparts[0] % 24, tparts[1], min(tparts[2], 59))
                except ValueError:
                    dt = None

        rest_clean = _clean(rest)
        # sender split on first ': '
        sender = None
        body = rest_clean
        if ": " in rest_clean:
            cand_sender, cand_body = rest_clean.split(": ", 1)
            # a real sender name shouldn't itself look like a system event / be too long
            if "\n" not in cand_sender and len(cand_sender) <= 60 and not looks_system(cand_sender, cand_sender):
                sender = cand_sender.strip()
                body = cand_body

        if sender is None or looks_system(None, rest_clean) and sender is None:
            kind = "system"
            mtype = view_once = edited = None
            ctext = rest_clean
        else:
            kind, mtype, view_once, edited, ctext = classify(body)

        if sender is None:
            kind = "system"

        emojis = EMOJI_SEQ.findall(ctext) if kind == "text" else []
        links = URL_RE.findall(ctext) if kind in ("text", "media") else []
        n_words = len(ctext.split()) if kind == "text" else 0
        n_chars = len(ctext) if kind == "text" else 0

        messages.append({
            "dt": dt,
            "sender": sender,
            "kind": kind,
            "media_type": mtype,
            "view_once": bool(view_once),
            "edited": bool(edited),
            "text": ctext,
            "n_words": n_words,
            "n_chars": n_chars,
            "emojis": emojis,
            "links": links,
            "has_q": ("?" in ctext) if kind == "text" else False,
        })

    meta = {"date_order": order, "n_lines": len(lines)}
    return messages, meta


# --------------------------------------------------------------------------- #
#  Analysis
# --------------------------------------------------------------------------- #

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def analyze(messages, session_gap_min=180):
    S = {}
    real = [m for m in messages if m["sender"] and m["kind"] != "system"]
    dated = [m for m in real if m["dt"]]
    dated.sort(key=lambda m: m["dt"])

    senders = sorted({m["sender"] for m in real})
    S["senders"] = senders
    S["n_participants"] = len(senders)
    S["total_messages"] = len(real)
    S["total_system"] = sum(1 for m in messages if m["kind"] == "system")

    # ---- per-person accumulators
    P = {s: defaultdict(int) for s in senders}
    words_counter = {s: Counter() for s in senders}
    emoji_counter = {s: Counter() for s in senders}
    hour_by_sender = {s: Counter() for s in senders}
    longest_msg = {s: {"chars": 0, "text": ""} for s in senders}
    link_domains = Counter()
    link_dom_by_sender = {s: Counter() for s in senders}
    all_words = Counter()
    all_emoji = Counter()
    media_types = Counter()
    media_by_sender = {s: Counter() for s in senders}
    view_once_by_sender = Counter()
    common_msgs = Counter()

    hour_hist = Counter()
    weekday_hist = Counter()
    month_hist = Counter()        # 'YYYY-MM'
    date_hist = Counter()         # date -> count
    heat = defaultdict(int)       # (weekday, hour) -> count

    for m in real:
        s = m["sender"]
        P[s]["messages"] += 1
        P[s]["words"] += m["n_words"]
        P[s]["chars"] += m["n_chars"]
        if m["kind"] == "text":
            P[s]["text_messages"] += 1
            if m["has_q"]:
                P[s]["questions"] += 1
            if m["n_chars"] > longest_msg[s]["chars"]:
                longest_msg[s] = {"chars": m["n_chars"], "text": m["text"]}
            norm = m["text"].strip().lower()
            if 0 < len(norm) <= 30:
                common_msgs[norm] += 1
            for w in WORD_RE.findall(m["text"].lower()):
                if len(w) >= 3 and w not in STOPWORDS:
                    words_counter[s][w] += 1
                    all_words[w] += 1
        if m["edited"]:
            P[s]["edited"] += 1
        if m["kind"] == "deleted":
            P[s]["deleted"] += 1
        if m["kind"] == "media":
            P[s]["media"] += 1
            media_types[m["media_type"]] += 1
            media_by_sender[s][m["media_type"]] += 1
            if m["view_once"]:
                view_once_by_sender[s] += 1
        if m["view_once"]:
            P[s]["view_once"] += 1
        for e in m["emojis"]:
            emoji_counter[s][e] += 1
            all_emoji[e] += 1
            P[s]["emojis"] += 1
        for l in m["links"]:
            P[s]["links"] += 1
            dom = re.sub(r"^https?://(www\.)?", "", l, flags=re.IGNORECASE).split("/")[0].lower()
            if dom:
                link_domains[dom] += 1
                link_dom_by_sender[s][dom] += 1

        if m["dt"]:
            hour_hist[m["dt"].hour] += 1
            weekday_hist[m["dt"].weekday()] += 1
            month_hist[m["dt"].strftime("%Y-%m")] += 1
            date_hist[m["dt"].date()] += 1
            heat[(m["dt"].weekday(), m["dt"].hour)] += 1
            hour_by_sender[s][m["dt"].hour] += 1
            P[s]["dated"] += 1

    S["per_person"] = {}
    for s in senders:
        d = P[s]
        msgs = d["messages"] or 1
        txt = d["text_messages"] or 1
        S["per_person"][s] = {
            "messages": d["messages"],
            "words": d["words"],
            "chars": d["chars"],
            "text_messages": d["text_messages"],
            "avg_words": d["words"] / txt,
            "avg_chars": d["chars"] / txt,
            "media": d["media"],
            "emojis": d["emojis"],
            "links": d["links"],
            "questions": d["questions"],
            "deleted": d["deleted"],
            "edited": d["edited"],
            "view_once": d["view_once"],
            "share": d["messages"] / (len(real) or 1) * 100,
            "top_words": words_counter[s].most_common(10),
            "top_emojis": emoji_counter[s].most_common(5),
            "top_links": link_dom_by_sender[s].most_common(5),
            "longest": longest_msg[s],
            "peak_hour": (hour_by_sender[s].most_common(1)[0][0] if hour_by_sender[s] else None),
        }

    # ---- time patterns
    S["hour_hist"] = [hour_hist.get(h, 0) for h in range(24)]
    S["weekday_hist"] = [weekday_hist.get(w, 0) for w in range(7)]
    S["month_hist"] = sorted(month_hist.items())
    S["heat"] = {f"{w},{h}": heat.get((w, h), 0) for w in range(7) for h in range(24)}
    S["peak_hour"] = max(range(24), key=lambda h: hour_hist.get(h, 0)) if hour_hist else None
    S["peak_weekday"] = (max(range(7), key=lambda w: weekday_hist.get(w, 0)) if weekday_hist else None)
    if date_hist:
        busiest = max(date_hist.items(), key=lambda kv: kv[1])
        S["busiest_date"] = (busiest[0].isoformat(), busiest[1])
        S["active_days"] = len(date_hist)
        S["date_hist"] = sorted((d.isoformat(), c) for d, c in date_hist.items())
    else:
        S["busiest_date"] = None
        S["active_days"] = 0
        S["date_hist"] = []

    # ---- span / export start & end
    if dated:
        first, last = dated[0], dated[-1]
        span_days = (last["dt"].date() - first["dt"].date()).days + 1
        S["first_dt"] = first["dt"].isoformat()
        S["last_dt"] = last["dt"].isoformat()
        S["span_days"] = span_days
        S["msgs_per_active_day"] = len(real) / (S["active_days"] or 1)
        S["first_message"] = {"sender": first["sender"], "dt": first["dt"].isoformat(),
                              "text": _preview(first["text"], first["kind"])}
        S["last_message"] = {"sender": last["sender"], "dt": last["dt"].isoformat(),
                             "text": _preview(last["text"], last["kind"])}
        # silence between consecutive active dates -> longest gap + longest streak
        days_sorted = sorted(date_hist.keys())
        longest_gap = (0, None, None)
        streak = best_streak = 1
        streak_start = best_start = days_sorted[0] if days_sorted else None
        for i in range(1, len(days_sorted)):
            gap = (days_sorted[i] - days_sorted[i - 1]).days
            if gap > longest_gap[0]:
                longest_gap = (gap, days_sorted[i - 1].isoformat(), days_sorted[i].isoformat())
            if gap == 1:
                streak += 1
                if streak > best_streak:
                    best_streak, best_start = streak, streak_start
            else:
                streak = 1
                streak_start = days_sorted[i]
        S["longest_gap_days"] = longest_gap[0]
        S["longest_gap"] = {"days": longest_gap[0], "from": longest_gap[1], "to": longest_gap[2]}
        S["longest_streak_days"] = best_streak
    else:
        for k in ("first_dt", "last_dt", "span_days", "first_message", "last_message",
                  "longest_gap", "longest_streak_days"):
            S[k] = None
        S["span_days"] = 0
        S["msgs_per_active_day"] = 0

    # ---- first-message-of-day (who greets first)
    first_of_day = Counter()
    by_date = defaultdict(list)
    for m in dated:
        by_date[m["dt"].date()].append(m)
    for day, msgs in by_date.items():
        msgs.sort(key=lambda m: m["dt"])
        first_of_day[msgs[0]["sender"]] += 1
    S["first_of_day"] = first_of_day.most_common()

    # ---- conversation sessions, starters, response times, monologues, double-texts
    gap = timedelta(minutes=session_gap_min)
    starters = Counter()
    reply_times = {s: [] for s in senders}   # seconds
    double_texts = Counter()
    monologue = Counter()
    n_sessions = 0
    cur_streak_sender = None
    cur_streak_len = 0
    longest_monologue = (0, None)

    prev = None
    for m in dated:
        if prev is None or (m["dt"] - prev["dt"]) > gap:
            n_sessions += 1
            starters[m["sender"]] += 1
            cur_streak_sender = m["sender"]
            cur_streak_len = 1
        else:
            if m["sender"] == prev["sender"]:
                double_texts[m["sender"]] += 1
                cur_streak_len += 1
            else:
                # reply to a different person within a session
                dt_s = (m["dt"] - prev["dt"]).total_seconds()
                if 0 <= dt_s <= gap.total_seconds():
                    reply_times[m["sender"]].append(dt_s)
                cur_streak_sender = m["sender"]
                cur_streak_len = 1
        if cur_streak_len > longest_monologue[0]:
            longest_monologue = (cur_streak_len, m["sender"])
        monologue[m["sender"]] = max(monologue[m["sender"]], cur_streak_len)
        prev = m

    S["n_sessions"] = n_sessions
    S["starters"] = starters.most_common()
    S["double_texts"] = double_texts.most_common()
    S["longest_monologue"] = {"len": longest_monologue[0], "sender": longest_monologue[1]}
    S["reply_stats"] = {}
    all_reply = []
    for s in senders:
        rts = sorted(reply_times[s])
        all_reply += rts
        if rts:
            S["reply_stats"][s] = {
                "median_s": _median(rts),
                "avg_s": sum(rts) / len(rts),
                "count": len(rts),
            }
        else:
            S["reply_stats"][s] = {"median_s": None, "avg_s": None, "count": 0}
    S["overall_median_reply_s"] = _median(sorted(all_reply)) if all_reply else None

    # ---- totals
    S["total_words"] = sum(P[s]["words"] for s in senders)
    S["total_chars"] = sum(P[s]["chars"] for s in senders)
    S["total_emojis"] = sum(P[s]["emojis"] for s in senders)
    S["total_links"] = sum(P[s]["links"] for s in senders)
    S["total_media"] = sum(media_types.values())
    S["total_deleted"] = sum(P[s]["deleted"] for s in senders)
    S["total_edited"] = sum(P[s]["edited"] for s in senders)
    S["total_view_once"] = sum(view_once_by_sender.values())
    S["media_types"] = media_types.most_common()
    S["media_by_sender"] = {s: dict(media_by_sender[s]) for s in senders}
    S["view_once_by_sender"] = view_once_by_sender.most_common()
    S["top_words"] = all_words.most_common(30)
    S["top_emojis"] = all_emoji.most_common(15)
    S["link_domains"] = link_domains.most_common(12)
    S["common_msgs"] = [(t, c) for t, c in common_msgs.most_common(15) if c > 1]

    # ---- awards
    S["awards"] = compute_awards(S)
    return S


def _preview(text, kind, n=140):
    if kind == "media":
        return "[media]"
    if kind == "deleted":
        return "[deleted message]"
    t = " ".join(text.split())
    return (t[:n] + "…") if len(t) > n else t


def _median(sorted_list):
    n = len(sorted_list)
    if n == 0:
        return None
    if n % 2:
        return sorted_list[n // 2]
    return (sorted_list[n // 2 - 1] + sorted_list[n // 2]) / 2


def compute_awards(S):
    pp = S["per_person"]
    senders = S["senders"]
    if not senders:
        return []
    awards = []

    def mk(label, desc, valfn, fmt, reverse=True, pool=None, skip_top_zero=True):
        """Build an award with a FULL ranking (best-first) of all members."""
        cands = list(pool if pool is not None else senders)
        if not cands:
            return
        ranked = sorted(cands, key=lambda s: valfn(s), reverse=reverse)
        top_val = valfn(ranked[0])
        if skip_top_zero and (top_val is None or top_val <= 0):
            return
        ranking = [{"name": s, "value": fmt(valfn(s)), "raw": valfn(s)} for s in ranked]
        awards.append({"title": label, "detail": desc, "winner": ranked[0],
                       "value": fmt(top_val), "ranking": ranking})

    mk("🗣️ Chatterbox", "Most messages sent",
       lambda s: pp[s]["messages"], lambda v: f"{v:,} msgs")

    avg_pool = [s for s in senders if pp[s]["text_messages"] >= 3]
    mk("📚 The Novelist", "Longest messages on average", pool=avg_pool,
       valfn=lambda s: pp[s]["avg_chars"], fmt=lambda v: f"{v:.0f} chars/msg")
    mk("🔤 One-Word Wonder", "Shortest messages on average", pool=avg_pool,
       valfn=lambda s: pp[s]["avg_words"], fmt=lambda v: f"{v:.1f} words/msg",
       reverse=False, skip_top_zero=False)

    mk("😄 Emoji Lord", "Most emojis used",
       lambda s: pp[s]["emojis"], lambda v: f"{v:,} emojis")
    mk("📷 Media Mogul", "Most photos/videos/etc. shared",
       lambda s: pp[s]["media"], lambda v: f"{v:,} media")
    mk("❓ Question Master", "Most messages with a question",
       lambda s: pp[s]["questions"], lambda v: f"{v:,} questions")
    mk("🔗 Link Dropper", "Most links shared",
       lambda s: pp[s]["links"], lambda v: f"{v:,} links")
    mk("🫥 The Retractor", "Most deleted messages",
       lambda s: pp[s]["deleted"], lambda v: f"{v:,} deleted")

    starts = dict(S["starters"])
    mk(f"🚀 Conversation Starter", f"Kicked off the most conversations (of {S['n_sessions']})",
       lambda s: starts.get(s, 0), lambda v: f"{v:,} starts")

    # reply-speed awards share one pool (members with >=3 measured replies)
    rs = {s: v for s, v in S["reply_stats"].items() if v["median_s"] is not None and v["count"] >= 3}
    reply_pool = list(rs.keys())
    if reply_pool:
        mk("⚡ Speed Replier", "Fastest median reply", pool=reply_pool,
           valfn=lambda s: rs[s]["median_s"], fmt=lambda v: _fmt_dur(v),
           reverse=False, skip_top_zero=False)
        if len(reply_pool) >= 2:
            mk("🐢 The Ghoster", "Slowest median reply", pool=reply_pool,
               valfn=lambda s: rs[s]["median_s"], fmt=lambda v: _fmt_dur(v),
               reverse=True, skip_top_zero=False)
    return awards


def _fmt_dur(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


# --------------------------------------------------------------------------- #
#  SVG chart helpers
# --------------------------------------------------------------------------- #

PALETTE = ["#25D366", "#128C7E", "#34B7F1", "#7C5CFC", "#F5A623", "#EF476F",
           "#06D6A0", "#FFD166", "#B388FF", "#FF8A65", "#4DB6AC", "#9575CD"]


def color_for(i):
    return PALETTE[i % len(PALETTE)]


def esc(s):
    return html.escape(str(s), quote=True)


def svg_hbars(data, unit="", height_per=30, width=560, color=None, fmt=None):
    """Horizontal bar chart. data = list of (label, value)."""
    if not data:
        return "<p class='muted'>No data.</p>"
    fmt = fmt or (lambda v: f"{v:,.0f}")
    maxv = max(v for _, v in data) or 1
    labelw = 150
    barw = width - labelw - 70
    h = height_per * len(data) + 10
    rows = []
    for i, (label, v) in enumerate(data):
        y = i * height_per + 5
        w = max(2, barw * v / maxv)
        c = color or color_for(i)
        rows.append(
            f'<text x="{labelw-8}" y="{y+16}" text-anchor="end" class="svglabel">{esc(_short(label,22))}</text>'
            f'<rect x="{labelw}" y="{y+4}" width="{w:.1f}" height="18" rx="4" fill="{c}"><title>{esc(label)}: {fmt(v)}{esc(unit)}</title></rect>'
            f'<text x="{labelw+w+6:.1f}" y="{y+18}" class="svgval">{esc(fmt(v))}{esc(unit)}</text>'
        )
    return f'<svg viewBox="0 0 {width} {h}" width="100%" class="chart" role="img">{"".join(rows)}</svg>'


def _fmt_gap(mins):
    mins = int(mins)
    if mins % 60 == 0:
        h = mins // 60
        return f"{h} hour" + ("s" if h != 1 else "")
    if mins >= 60:
        return f"{mins/60:.1f} hours"
    return f"{mins} minute" + ("s" if mins != 1 else "")


def _short_num(v):
    v = float(v)
    if v >= 1000:
        s = f"{v/1000:.1f}".rstrip("0").rstrip(".")
        return s + "k"
    return f"{int(round(v)):,}"


def svg_vbars(values, labels, highlight=None, width=680, height=180, color="#25D366", show_values=True):
    """Vertical bar chart (e.g. hour of day)."""
    if not values:
        return "<p class='muted'>No data.</p>"
    maxv = max(values) or 1
    n = len(values)
    pad_l, pad_b, pad_t = 28, 22, (20 if show_values else 10)
    plot_w = width - pad_l - 8
    plot_h = height - pad_b - pad_t
    bw = plot_w / n
    val_fs = 9 if n > 12 else 11
    bars = []
    for i, v in enumerate(values):
        bh = plot_h * v / maxv
        x = pad_l + i * bw
        y = pad_t + (plot_h - bh)
        c = "#128C7E" if (highlight is not None and i == highlight) else color
        bars.append(
            f'<rect x="{x+1:.1f}" y="{y:.1f}" width="{max(1,bw-2):.1f}" height="{bh:.1f}" rx="2" fill="{c}">'
            f'<title>{esc(labels[i])}: {v:,}</title></rect>'
        )
        if show_values and v > 0:
            bars.append(
                f'<text x="{x+bw/2:.1f}" y="{y-3:.1f}" text-anchor="middle" '
                f'style="font-size:{val_fs}px" class="svgval">{esc(_short_num(v))}</text>'
            )
        if n <= 24 and (i % (2 if n > 12 else 1) == 0):
            bars.append(f'<text x="{x+bw/2:.1f}" y="{height-8}" text-anchor="middle" class="svgtick">{esc(labels[i])}</text>')
    return f'<svg viewBox="0 0 {width} {height}" width="100%" class="chart" role="img">{"".join(bars)}</svg>'


def svg_line(points, width=680, height=200, color="#128C7E"):
    """points = list of (label, value). Area+line timeline."""
    if len(points) < 2:
        return svg_vbars([v for _, v in points], [l for l, _ in points]) if points else "<p class='muted'>No data.</p>"
    vals = [v for _, v in points]
    maxv = max(vals) or 1
    pad_l, pad_b, pad_t = 34, 26, 10
    plot_w = width - pad_l - 10
    plot_h = height - pad_b - pad_t
    n = len(points)
    xs = [pad_l + plot_w * i / (n - 1) for i in range(n)]
    ys = [pad_t + plot_h * (1 - v / maxv) for v in vals]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"{pad_l},{pad_t+plot_h} " + line + f" {xs[-1]:.1f},{pad_t+plot_h}"
    dots = []
    step = max(1, n // 12)
    for i in range(0, n, step):
        dots.append(f'<circle cx="{xs[i]:.1f}" cy="{ys[i]:.1f}" r="2.5" fill="{color}"><title>{esc(points[i][0])}: {vals[i]:,}</title></circle>')
    labels = []
    for i in range(0, n, max(1, n // 6)):
        labels.append(f'<text x="{xs[i]:.1f}" y="{height-8}" text-anchor="middle" class="svgtick">{esc(points[i][0])}</text>')
    labels.append(f'<text x="2" y="{pad_t+8}" class="svgtick">{maxv:,}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" class="chart" role="img">'
            f'<polygon points="{area}" fill="{color}22"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'{"".join(dots)}{"".join(labels)}</svg>')


def svg_heatmap(heat, width=680):
    """7x24 weekday x hour heatmap."""
    cell = (width - 60) / 24
    ch = 20
    maxv = max(heat.values()) if heat else 1
    maxv = maxv or 1
    rects = []
    for w in range(7):
        y = 14 + w * ch
        rects.append(f'<text x="52" y="{y+14}" text-anchor="end" class="svgtick">{WEEKDAYS[w][:3]}</text>')
        for h in range(24):
            v = heat.get(f"{w},{h}", 0)
            x = 58 + h * cell
            inten = v / maxv
            fill = f"rgba(18,140,126,{0.08 + 0.92*inten:.3f})" if v else "rgba(0,0,0,0.04)"
            rects.append(f'<rect x="{x:.1f}" y="{y}" width="{cell-1:.1f}" height="{ch-2}" rx="2" fill="{fill}"><title>{WEEKDAYS[w]} {h:02d}:00 — {v:,}</title></rect>')
    for h in range(0, 24, 3):
        x = 58 + h * cell
        rects.append(f'<text x="{x:.1f}" y="{14+7*ch+12}" class="svgtick">{h:02d}</text>')
    total_h = 14 + 7 * ch + 20
    return f'<svg viewBox="0 0 {width} {total_h}" width="100%" class="chart" role="img">{"".join(rects)}</svg>'


def _short(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- #
#  HTML report
# --------------------------------------------------------------------------- #

CSS = """
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  background:#ece5dd; color:#111b21; line-height:1.5; }
.wrap { max-width:1040px; margin:0 auto; padding:24px 18px 80px; }
header.hero { background:linear-gradient(135deg,#075E54,#128C7E); color:#fff; border-radius:18px;
  padding:28px 30px; margin-bottom:22px; box-shadow:0 8px 24px rgba(0,0,0,.15); }
header.hero h1 { margin:0 0 6px; font-size:26px; }
header.hero .sub { opacity:.9; font-size:14px; }
.chips { margin-top:14px; display:flex; flex-wrap:wrap; gap:8px; }
.chip { background:rgba(255,255,255,.18); padding:4px 11px; border-radius:20px; font-size:12.5px; }
.grid { display:grid; gap:14px; }
.kpis { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); }
.card { background:#fff; border-radius:14px; padding:18px 20px; box-shadow:0 2px 8px rgba(0,0,0,.06); }
.kpi { text-align:center; }
.kpi .num { font-size:26px; font-weight:700; color:#075E54; }
.kpi .lbl { font-size:12.5px; color:#54656f; margin-top:2px; }
section { margin-top:26px; }
section > h2 { font-size:18px; margin:0 0 4px; display:flex; align-items:center; gap:8px; }
section > .hint { color:#54656f; font-size:13px; margin:0 0 12px; }
.awards { grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); align-items:start; }
.award { background:#fff; border-radius:14px; box-shadow:0 2px 8px rgba(0,0,0,.06);
  border-left:4px solid #25D366; overflow:hidden; }
.award > summary { padding:15px 16px 14px; cursor:pointer; list-style:none; position:relative; }
.award > summary::-webkit-details-marker { display:none; }
.award > summary:hover { background:#f7faf9; }
.award .t { font-weight:700; font-size:14px; display:block; padding-right:18px; }
.award .w { font-size:16px; color:#075E54; font-weight:700; margin:3px 0; display:block; }
.award .d { font-size:12px; color:#54656f; display:block; }
.award .v { font-size:12.5px; margin-top:4px; font-weight:600; display:block; }
.award .caret { position:absolute; top:14px; right:14px; color:#8696a0; font-size:12px; transition:transform .15s; }
.award[open] .caret { transform:rotate(180deg); }
.rank-table { width:100%; border-collapse:collapse; font-size:13px; border-top:1px solid #eef0f1; }
.rank-table td { padding:6px 16px; border-bottom:1px solid #f2f4f5; }
.rank-table td.rk { width:30px; color:#8696a0; text-align:center; }
.rank-table td.rv { text-align:right; font-variant-numeric:tabular-nums; color:#54656f; }
.rank-table tr.win td { background:#f0faf5; font-weight:700; color:#075E54; }
.rank-table tr.win td.rv { color:#075E54; }
table { width:100%; border-collapse:collapse; font-size:13.5px; }
th,td { padding:8px 10px; text-align:left; border-bottom:1px solid #eef0f1; }
th { color:#54656f; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; cursor:default;}
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
tr:hover td { background:#f7faf9; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media(max-width:720px){ .two{grid-template-columns:1fr;} }
.chart { display:block; }
.svglabel { font-size:12px; fill:#111b21; }
.svgval { font-size:11px; fill:#54656f; }
.svgtick { font-size:10px; fill:#8696a0; }
.muted { color:#8696a0; font-size:13px; }
.pill { display:inline-block; background:#e7f4ef; color:#075E54; border-radius:12px; padding:2px 9px;
  font-size:12px; margin:2px 4px 2px 0; }
.emoji-big { font-size:20px; }
.msgbox { background:#f7faf9; border-left:3px solid #25D366; padding:8px 12px; border-radius:8px;
  font-size:13px; margin:6px 0; }
.note { background:#fff8e1; border:1px solid #ffe082; border-radius:10px; padding:12px 15px;
  font-size:13px; color:#5f4b1b; }
footer { margin-top:40px; color:#54656f; font-size:12px; text-align:center; }
.rank { color:#8696a0; width:26px; }
.sub-lbl { font-size:11px; color:#8696a0; text-transform:uppercase; letter-spacing:.03em; margin:6px 0 3px; }
@media print {
  @page { size: A4; margin: 12mm; }
  body { background:#fff; }
  .wrap { max-width:100%; padding:0; }
  header.hero { box-shadow:none; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  section, .card, .award, .two > .card { break-inside: avoid; page-break-inside: avoid; }
  section > h2 { break-after: avoid; }
  .award { border:1px solid #e6e9ea; }
  .award .caret { display:none; }
  .award > summary { cursor:default; }
  * { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
}
"""


def h(s):
    return html.escape(str(s))


def build_html(S, chat_name, source_name, me=None, expand_all=False):
    P = S["per_person"]
    senders = S["senders"]
    senders_by_msgs = sorted(S["senders"], key=lambda s: -P[s]["messages"])

    out = []
    out.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    out.append(f"<title>{h(chat_name)} — WhatsApp Analysis</title>")
    out.append(f"<style>{CSS}</style></head><body><div class='wrap'>")

    # hero
    out.append("<header class='hero'>")
    out.append(f"<h1>💬 {h(chat_name)}</h1>")
    span = ""
    if S["first_dt"]:
        span = f"{S['first_dt'][:10]} → {S['last_dt'][:10]} · {S['span_days']:,} days · {S['active_days']:,} active days"
    out.append(f"<div class='sub'>{h(span)}</div>")
    out.append("<div class='chips'>")
    out.append(f"<span class='chip'>👥 {S['n_participants']} participant(s)</span>")
    out.append(f"<span class='chip'>💬 {S['total_messages']:,} messages</span>")
    if S["peak_hour"] is not None:
        out.append(f"<span class='chip'>🕐 Peak hour {S['peak_hour']:02d}:00</span>")
    if S["peak_weekday"] is not None:
        out.append(f"<span class='chip'>📅 Busiest {WEEKDAYS[S['peak_weekday']]}</span>")
    out.append("</div></header>")

    # KPIs
    kpis = [
        (f"{S['total_messages']:,}", "Messages"),
        (f"{S['total_words']:,}", "Words"),
        (f"{S['total_media']:,}", "Media"),
        (f"{S['total_emojis']:,}", "Emojis"),
        (f"{S['total_links']:,}", "Links"),
        (f"{S['active_days']:,}", "Active days"),
        (f"{S['msgs_per_active_day']:.0f}", "Msgs/active day"),
        (f"{S['n_sessions']:,}", "Conversations"),
    ]
    out.append("<div class='grid kpis'>")
    for num, lbl in kpis:
        out.append(f"<div class='card kpi'><div class='num'>{h(num)}</div><div class='lbl'>{h(lbl)}</div></div>")
    out.append("</div>")

    # Export snapshot (start & end)
    out.append("<section><h2>📌 Export snapshot</h2>")
    out.append("<p class='hint'>What this export covers — where it starts, where it ends, and the quiet stretches in between.</p>")
    out.append("<div class='card'>")
    if S["first_message"]:
        fm, lm = S["first_message"], S["last_message"]
        out.append(f"<p><strong>First message</strong> — {h(fm['dt'][:16].replace('T',' '))} · <strong>{h(fm['sender'])}</strong></p>")
        out.append(f"<div class='msgbox'>{h(fm['text'])}</div>")
        out.append(f"<p><strong>Last message</strong> — {h(lm['dt'][:16].replace('T',' '))} · <strong>{h(lm['sender'])}</strong></p>")
        out.append(f"<div class='msgbox'>{h(lm['text'])}</div>")
        if S["longest_streak_days"]:
            out.append(f"<p>🔥 Longest daily streak: <strong>{S['longest_streak_days']:,} days</strong> in a row with at least one message.</p>")
        if S["longest_gap"] and S["longest_gap"]["days"]:
            g = S["longest_gap"]
            out.append(f"<p>🤫 Longest silence: <strong>{g['days']:,} days</strong> (from {h(g['from'])} to {h(g['to'])}).</p>")
        if S["busiest_date"]:
            out.append(f"<p>🏆 Busiest single day: <strong>{h(S['busiest_date'][0])}</strong> with <strong>{S['busiest_date'][1]:,}</strong> messages.</p>")
    else:
        out.append("<p class='muted'>No timestamped messages were found — the date format may be unusual. Try the <code>--date-order</code> option.</p>")
    out.append("</div></section>")

    # Awards
    if S["awards"]:
        out.append("<section><h2>🏅 Awards</h2><p class='hint'>Playful superlatives based on the numbers. "
                   "Click any card to expand the full ranking of everyone, with their numbers.</p>")
        out.append("<div class='grid awards'>")
        medals = ["🥇", "🥈", "🥉"]
        for a in S["awards"]:
            out.append("<details class='award'%s>" % (" open" if expand_all else ""))
            out.append("<summary>"
                       f"<span class='t'>{h(a['title'])}</span>"
                       f"<span class='w'>{h(a['winner'])}</span>"
                       f"<span class='d'>{h(a['detail'])}</span>"
                       f"<span class='v'>{h(a['value'])}</span>"
                       "<span class='caret'>▾</span></summary>")
            out.append("<table class='rank-table'>")
            for i, r in enumerate(a["ranking"]):
                rk = medals[i] if i < 3 else f"{i + 1}"
                cls = " class='win'" if i == 0 else ""
                out.append(f"<tr{cls}><td class='rk'>{rk}</td><td>{h(r['name'])}</td>"
                           f"<td class='rv'>{h(r['value'])}</td></tr>")
            out.append("</table></details>")
        out.append("</div></section>")

    # Who messaged most
    out.append("<section><h2>💬 Who messaged the most</h2>")
    out.append("<div class='card'>")
    out.append(svg_hbars([(s, P[s]["messages"]) for s in senders_by_msgs]))
    out.append("</div></section>")

    # Per-person table
    out.append("<section><h2>🧮 Per-person breakdown</h2>")
    out.append("<div class='card' style='overflow-x:auto'>")
    out.append("<table><thead><tr><th>Person</th><th class='num'>Msgs</th><th class='num'>Share</th>"
               "<th class='num'>Words</th><th class='num'>Avg words</th><th class='num'>Avg chars</th>"
               "<th class='num'>Media</th><th class='num'>Emojis</th><th class='num'>Links</th>"
               "<th class='num'>Q's</th><th class='num'>Edited</th><th class='num'>Deleted</th></tr></thead><tbody>")
    for s in senders_by_msgs:
        d = P[s]
        out.append(
            f"<tr><td>{h(s)}</td><td class='num'>{d['messages']:,}</td><td class='num'>{d['share']:.1f}%</td>"
            f"<td class='num'>{d['words']:,}</td><td class='num'>{d['avg_words']:.1f}</td>"
            f"<td class='num'>{d['avg_chars']:.0f}</td><td class='num'>{d['media']:,}</td>"
            f"<td class='num'>{d['emojis']:,}</td><td class='num'>{d['links']:,}</td>"
            f"<td class='num'>{d['questions']:,}</td><td class='num'>{d['edited']:,}</td>"
            f"<td class='num'>{d['deleted']:,}</td></tr>")
    out.append("</tbody></table></div></section>")

    # Time patterns
    out.append("<section><h2>🕒 When do you chat?</h2>")
    out.append("<div class='two'>")
    out.append("<div class='card'><h3 style='margin:0 0 8px;font-size:14px'>By hour of day</h3>"
               + svg_vbars(S["hour_hist"], [f"{i:02d}" for i in range(24)], highlight=S["peak_hour"]) + "</div>")
    out.append("<div class='card'><h3 style='margin:0 0 8px;font-size:14px'>By day of week</h3>"
               + svg_vbars(S["weekday_hist"], [d[:3] for d in WEEKDAYS], highlight=S["peak_weekday"]) + "</div>")
    out.append("</div>")
    out.append("<div class='card' style='margin-top:14px'><h3 style='margin:0 0 8px;font-size:14px'>Weekly rhythm (weekday × hour)</h3>"
               + svg_heatmap(S["heat"]) + "</div>")
    # monthly timeline
    if S["month_hist"]:
        out.append("<div class='card' style='margin-top:14px'><h3 style='margin:0 0 8px;font-size:14px'>Messages over time (by month)</h3>"
                   + svg_line(S["month_hist"]) + "</div>")
    out.append("</section>")

    # Content: words + emojis
    out.append("<section><h2>🔤 What do you talk about?</h2>")
    out.append("<div class='two'>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Top words</h3>")
    if S["top_words"]:
        out.append(svg_hbars([(w, c) for w, c in S["top_words"][:15]], color="#7C5CFC"))
    else:
        out.append("<p class='muted'>No words to show.</p>")
    out.append("</div>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Top emojis</h3>")
    if S["top_emojis"]:
        for e, c in S["top_emojis"]:
            out.append(f"<span class='pill'><span class='emoji-big'>{h(e)}</span> {c:,}</span>")
    else:
        out.append("<p class='muted'>No emojis found.</p>")
    out.append("</div></div>")
    # common short messages
    if S["common_msgs"]:
        out.append("<div class='card' style='margin-top:14px'><h3 style='margin:0 0 8px;font-size:14px'>Most repeated one-liners</h3>")
        for t, c in S["common_msgs"]:
            out.append(f"<span class='pill'>{h(t)} · {c:,}</span>")
        out.append("</div>")
    out.append("</section>")

    # Media breakdown + view once
    out.append("<section><h2>📎 Media &amp; attachments</h2>")
    out.append("<div class='two'>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Media by type</h3>")
    if S["media_types"]:
        labelmap = {"image":"🖼️ Images","video":"🎬 Videos","audio":"🎙️ Voice/Audio","sticker":"🩹 Stickers",
                    "gif":"🎞️ GIFs","document":"📄 Documents","contact":"👤 Contacts","location":"📍 Location","media":"📦 Media (generic)"}
        out.append(svg_hbars([(labelmap.get(t, t), c) for t, c in S["media_types"]], color="#34B7F1"))
    else:
        out.append("<p class='muted'>No media placeholders found (expected if the chat had no media, or you exported text-only with a locale I should tune).</p>")
    out.append("</div>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Media shared per person</h3>")
    media_pp = [(s, P[s]["media"]) for s in senders_by_msgs if P[s]["media"] > 0]
    out.append(svg_hbars(media_pp, color="#34B7F1") if media_pp else "<p class='muted'>No media.</p>")
    out.append("</div></div>")
    # view once note
    out.append("<div class='note' style='margin-top:14px'>")
    out.append(f"<strong>👁️ One-time-view (view once) media:</strong> {S['total_view_once']:,} explicitly detected. ")
    out.append("Heads-up: WhatsApp writes the <em>same</em> \"omitted\" placeholder for view-once media as for normal media, because the file is never saved to disk. "
               "So in a text-only export, view-once media that carries no special marker cannot be told apart from ordinary media with 100% certainty. "
               "This report counts every explicit view-once signal it can find; if your export uses a token I haven't matched yet, send me one sample line and I'll add it.")
    out.append("</div></section>")

    # Behavior: starters, replies, monologues
    out.append("<section><h2>🔁 Conversation behavior</h2>")
    out.append("<div class='two'>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Who starts conversations</h3>")
    out.append(f"<p class='hint'>A new conversation = a gap of over {_fmt_gap(S.get('_gap_min', 180))}. {S['n_sessions']:,} total.</p>")
    if S["starters"]:
        out.append(svg_hbars([(s, c) for s, c in S["starters"]], color="#F5A623"))
    else:
        out.append("<p class='muted'>Not enough timestamped data.</p>")
    out.append("</div>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Reply speed (median)</h3>")
    rs_rows = [(s, S["reply_stats"][s]) for s in senders if S["reply_stats"][s]["median_s"] is not None]
    rs_rows.sort(key=lambda kv: kv[1]["median_s"])
    if rs_rows:
        out.append("<table><thead><tr><th>Person</th><th class='num'>Median</th><th class='num'>Average</th><th class='num'>Replies</th></tr></thead><tbody>")
        for s, v in rs_rows:
            out.append(f"<tr><td>{h(s)}</td><td class='num'>{h(_fmt_dur(v['median_s']))}</td>"
                       f"<td class='num'>{h(_fmt_dur(v['avg_s']))}</td><td class='num'>{v['count']:,}</td></tr>")
        out.append("</tbody></table>")
        if S["overall_median_reply_s"] is not None:
            out.append(f"<p class='hint'>Overall median reply time: <strong>{h(_fmt_dur(S['overall_median_reply_s']))}</strong>.</p>")
    else:
        out.append("<p class='muted'>Not enough back-and-forth to measure.</p>")
    out.append("</div></div>")
    # first of day + monologue
    out.append("<div class='two' style='margin-top:14px'>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>First message of the day</h3>")
    if S["first_of_day"]:
        out.append(svg_hbars([(s, c) for s, c in S["first_of_day"]], color="#06D6A0", unit=" days"))
    else:
        out.append("<p class='muted'>No data.</p>")
    out.append("</div>")
    out.append("<div class='card'><h3 style='margin:0 0 10px;font-size:14px'>Double-texting (consecutive msgs)</h3>")
    if S["double_texts"]:
        out.append(svg_hbars([(s, c) for s, c in S["double_texts"]], color="#EF476F"))
        lm = S["longest_monologue"]
        if lm["sender"]:
            out.append(f"<p class='hint'>Longest monologue: <strong>{h(lm['sender'])}</strong> sent <strong>{lm['len']:,}</strong> messages in a row.</p>")
    else:
        out.append("<p class='muted'>No data.</p>")
    out.append("</div></div>")
    out.append("</section>")

    # Link domains
    if S["link_domains"]:
        out.append("<section><h2>🔗 Most-shared link domains</h2><div class='card'>")
        out.append(svg_hbars([(d, c) for d, c in S["link_domains"]], color="#128C7E"))
        out.append("</div></section>")

    # Per-person spotlights (top words + longest)
    out.append("<section><h2>🧑‍🤝‍🧑 Per-person spotlights</h2><div class='grid' style='grid-template-columns:repeat(auto-fit,minmax(280px,1fr))'>")
    for s in senders_by_msgs:
        d = P[s]
        out.append("<div class='card'>")
        out.append(f"<h3 style='margin:0 0 6px;font-size:15px;color:#075E54'>{h(s)}</h3>")
        out.append(f"<p class='hint' style='margin:0 0 8px'>{d['messages']:,} msgs · {d['avg_words']:.1f} words/msg · peak {('%02d:00'%d['peak_hour']) if d['peak_hour'] is not None else '—'}</p>")
        if d["top_words"]:
            out.append("<div class='sub-lbl'>Top words</div><div style='margin-bottom:6px'>"
                       + "".join(f"<span class='pill'>{h(w)} {c}</span>" for w, c in d["top_words"][:6]) + "</div>")
        if d["top_emojis"]:
            out.append("<div class='sub-lbl'>Top 5 emojis</div><div style='margin-bottom:6px'>"
                       + "".join(f"<span class='pill'><span class='emoji-big'>{h(e)}</span> {c:,}</span>"
                                 for e, c in d["top_emojis"][:5]) + "</div>")
        if d.get("top_links"):
            out.append("<div class='sub-lbl'>Top 5 links</div><div style='margin-bottom:6px'>"
                       + "".join(f"<span class='pill'>\U0001F517 {h(dom)} ({c:,})</span>"
                                 for dom, c in d["top_links"][:5]) + "</div>")
        elif d["links"]:
            out.append("<div class='sub-lbl'>Top 5 links</div><p class='muted' style='margin:0 0 6px'>"
                       f"{d['links']:,} link(s), no repeated domain</p>")
        if d["longest"]["chars"]:
            out.append(f"<div class='msgbox'>Longest msg ({d['longest']['chars']:,} chars): {h(_short(d['longest']['text'],160))}</div>")
        out.append("</div>")
    out.append("</div></section>")

    # methodology footer
    out.append("<footer>")
    out.append(f"Generated from <code>{h(source_name)}</code> · {S['total_messages']:,} messages · "
               f"{S['total_system']:,} system events skipped.<br>")
    out.append(f"All processing runs locally. A new conversation is counted after a silence of more than "
               f"{_fmt_gap(S.get('_gap_min', 180))} (this drives the conversation count, starters, and reply "
               f"times; reply times also exclude any gap longer than that). "
               f"Word counts exclude common stopwords. Emoji grouping treats ZWJ sequences (e.g. family emojis) as one.")
    out.append("</footer>")

    out.append("</div></body></html>")
    return "".join(out)


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #

def _find_chromium():
    import shutil
    for n in ("msedge", "microsoft-edge", "microsoft-edge-stable", "google-chrome",
              "google-chrome-stable", "chrome", "chromium", "chromium-browser", "brave-browser"):
        p = shutil.which(n)
        if p:
            return p
    for cand in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if os.path.exists(cand):
            return cand
    return None


def export_pdf(expanded_html, pdf_path, out_html_path):
    """Render expanded_html to pdf_path. Returns (ok, detail).

    headless Edge/Chrome (no install needed) -> weasyprint if installed ->
    else save a fully-expanded *_print.html for a one-click Ctrl+P -> Save as PDF.
    """
    import subprocess, tempfile, pathlib

    browser = _find_chromium()
    if browser:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
        try:
            tmp.write(expanded_html)
            tmp.close()
            url = pathlib.Path(tmp.name).as_uri()
            for mode in (["--headless=new"], ["--headless"]):
                cmd = [browser] + mode + [
                    "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                    "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000",
                    "--print-to-pdf=" + pdf_path, url,
                ]
                try:
                    subprocess.run(cmd, timeout=120,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    continue
                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 800:
                    return True, "via " + os.path.basename(browser)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    try:
        from weasyprint import HTML as _WHTML
        _WHTML(string=expanded_html).write_pdf(pdf_path)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 800:
            return True, "via weasyprint"
    except Exception:
        pass

    fallback = os.path.splitext(out_html_path)[0] + "_print.html"
    try:
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(expanded_html)
    except OSError:
        pass
    return False, "no Edge/Chrome/weasyprint found"


def main():
    ap = argparse.ArgumentParser(description="Analyze an exported WhatsApp chat into an HTML report.")
    ap.add_argument("input", help="Path to exported chat .zip, _chat.txt, or a folder")
    ap.add_argument("-o", "--output", help="Output HTML path")
    ap.add_argument("--date-order", choices=["dmy", "mdy", "ymd"], default=None,
                    help="Force date interpretation (default: auto-detect)")
    ap.add_argument("--session-gap", type=int, default=180,
                    help="Minutes of silence that starts a new conversation (default 180, i.e. 3 hours)")
    ap.add_argument("--me", default=None, help="Your own display name (optional framing)")
    ap.add_argument("--pdf", action="store_true",
                    help="Also export a PDF with everything expanded (uses Edge/Chrome if available)")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        raise SystemExit(f"Input not found: {args.input}")

    text, chat_name = read_chat_text(args.input)
    messages, meta = parse(text, forced_order=args.date_order)
    if not any(m["sender"] for m in messages):
        raise SystemExit("Could not parse any messages. If this is an unusual locale, "
                         "try --date-order, or share a few sample lines.")
    S = analyze(messages, session_gap_min=args.session_gap)
    S["_gap_min"] = args.session_gap

    out_path = args.output
    if not out_path:
        base = os.path.splitext(os.path.basename(args.input.rstrip("/\\")))[0]
        out_path = os.path.join(os.path.dirname(os.path.abspath(args.input)) or ".",
                                f"{base}_analysis.html")

    html_str = build_html(S, chat_name, os.path.basename(args.input), me=args.me)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    if args.pdf:
        pdf_path = os.path.splitext(out_path)[0] + ".pdf"
        expanded = build_html(S, chat_name, os.path.basename(args.input), me=args.me, expand_all=True)
        ok, detail = export_pdf(expanded, pdf_path, out_path)
        if ok:
            print(f"\U0001F4D5 PDF written to: {pdf_path}  ({detail})")
        else:
            fallback = os.path.splitext(out_path)[0] + "_print.html"
            print(f"⚠  Couldn't auto-generate the PDF ({detail}).")
            print(f"   Saved a print-ready, fully-expanded page instead: {fallback}")
            print(f"   Open it in your browser and choose Print -> Save as PDF (Ctrl+P).")

    # console summary
    print(f"✅ Analyzed {S['total_messages']:,} messages from {S['n_participants']} participant(s).")
    if S["first_dt"]:
        print(f"   Span: {S['first_dt'][:10]} → {S['last_dt'][:10]}  ({S['span_days']:,} days, {S['active_days']:,} active)")
    top = sorted(S["senders"], key=lambda s: -S["per_person"][s]["messages"])[:3]
    for s in top:
        print(f"   · {s}: {S['per_person'][s]['messages']:,} msgs")
    print(f"\U0001F4C4 Report written to: {out_path}")


if __name__ == "__main__":
    main()

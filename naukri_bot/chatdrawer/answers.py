"""Decide what to put in a widget. No DOM access here — fillers.py acts, engine.py verifies.

One rule set for every widget that carries options (radio, checkbox, select, chips): the v1
policy stack via `hooks.classify_radio` (safe-default location/consent matches, PII/legal/
employer traps -> discard) and only then the LLM. Free-text widgets go through
`hooks.text_answer`, which already knows booleans, years-of-experience, the QA dictionary and
DOB (a single "DD/MM/YYYY" field, patched into v1 alongside this module). Date controls that
aren't a single text field (day/month/year selects or inputs) are answered here directly from
config, because v1 has no equivalent — this is new coverage, not reused.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

DOB_CODE = "DOB_MISSING"
DATE_UNKNOWN_CODE = "DATE_UNKNOWN"
POLICY_CODE = "POLICY"
STALE_PAIR_CODE = "STALE_PAIR"
NO_ANSWER_CODE = "NO_ANSWER"
UNSUPPORTED_CODE = "UNSUPPORTED_WIDGET"

# Naukri's chatbot declares `constraints: [{"name": "max_length", "value": 100}]` on free-text
# questions (botapi/v5/respond, captured live 2026-09-23) but its composer does not enforce it:
# a longer answer is accepted into the conversation and then the final apply call rejects the
# WHOLE application with 406 "incomplete information" (2/2 captures: 188- and 280-char answers
# on 100-limit questions, every other answer present). The composer is a contenteditable div, so
# the limit is not visible in the DOM; an <input> with its own maxLength wins over this default.
NAUKRI_TEXT_MAX = 100

logger = logging.getLogger("naukri_unattended")


def _fit_text(value, limit):
    """Shorten `value` to at most `limit` chars: first sentence if it fits, else cut at a word."""
    v = " ".join(str(value).split())
    if len(v) <= limit:
        return v
    first = re.split(r"(?<=[.!?])\s", v, maxsplit=1)[0]
    if len(first) <= limit:
        return first
    cut = v[:limit + 1]
    cut = cut[:cut.rfind(" ")] if " " in cut else v[:limit]
    return cut.rstrip(" ,;:-")


@dataclass
class Answer:
    value: str                 # for radio/checkbox/select/chips: the exact option text to pick
    source: str                 # "policy" | "llm" | "config" | "rules"
    parts: Optional[dict] = None  # date_split only: {"day": "06", "month": "06", "year": "2001"}
    values: Optional[List[str]] = None  # checkbox only: every option to tick (value = first of them)


@dataclass
class Discard:
    code: str
    detail: str = ""


def _dob_parts(config):
    dob = str((config.get("personal_info", {}) or {}).get("date_of_birth", "")).strip()
    if not dob:
        return None
    for sep in ("/", "-", "."):
        if sep in dob:
            bits = [b.strip() for b in dob.split(sep) if b.strip()]
            break
    else:
        return None
    if len(bits) != 3:
        return None
    day, month, year = bits  # config is documented as DD/MM/YYYY
    if len(year) == 2:
        year = ("20" if int(year) < 50 else "19") + year
    return {"day": day.zfill(2), "month": month.zfill(2), "year": year}


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]


def _match_date_option(options, role, parts):
    want = parts[role]
    for o in options:
        t = (o.get("text") or "").strip()
        if role == "month":
            if t.lower().startswith(_MONTHS[int(want) - 1][:3]):
                return t
            if t.lstrip("0") == want.lstrip("0"):
                return t
        elif t.lstrip("0") == want.lstrip("0") or t == want:
            return t
    return None


def _is_dob_question(q):
    ql = (q or "").lower()
    return "date of birth" in ql or " dob" in f" {ql}" or ql.startswith("dob")


_JOINING_RX = re.compile(r"join|\bdoj\b|start date|available|availability|earliest|commence")


def _date_for_question(q, config):
    """(day/month/year parts, None) for a date control, or (None, Discard).

    2026-09-24: every date control used to get the date of birth whatever it asked -- a joining-date
    question was sent the candidate's date of birth. Now: DOB only for DOB questions,
    today + notice period for joining/availability dates, and anything else (last working day,
    interview date...) is skipped rather than guessed."""
    if _is_dob_question(q):
        dob = _dob_parts(config)
        if not dob:
            return None, Discard(DOB_CODE, "personal_info.date_of_birth missing or unparseable in config")
        return dob, None
    if _JOINING_RX.search((q or "").lower()):
        days = (config.get("personal_info", {}) or {}).get("notice_days") or 15
        d = date.today() + timedelta(days=int(days))
        return {"day": f"{d.day:02d}", "month": f"{d.month:02d}", "year": str(d.year)}, None
    return None, Discard(DATE_UNKNOWN_CODE, f"no known answer for date question '{(q or '')[:80]}'")


def decide(bot, widget, hooks, config, loc_variants):
    kind = widget.get("kind")
    q = widget.get("q", "")

    if kind == "unknown":
        return Discard(UNSUPPORTED_CODE, f"no handler for this control: {q[:80]}")

    if kind == "date_split":
        parts_needed = [p.get("role") for p in widget.get("parts", [])]
        when, skip = _date_for_question(q, config)
        if skip:
            return skip
        if not all(r in when for r in parts_needed if r):
            return Discard(DATE_UNKNOWN_CODE, f"date parts {parts_needed} not all known")
        picked = {}
        for p in widget.get("parts", []):
            role = p.get("role")
            if not role:
                continue
            if p.get("options"):
                m = _match_date_option(p["options"], role, when)
                if not m:
                    return Discard(DATE_UNKNOWN_CODE, f"no matching {role} option for {when[role]}")
                picked[role] = m
            else:
                picked[role] = when[role]
        return Answer(value="/".join(picked.get(r, "") for r in ("day", "month", "year")),
                      source="config", parts=picked)

    if kind in ("date", "date_text"):
        when, skip = _date_for_question(q, config)
        if skip:
            return skip
        value = f"{when['year']}-{when['month']}-{when['day']}" if kind == "date" \
            else f"{when['day']}/{when['month']}/{when['year']}"
        return Answer(value=value, source="config")

    if kind in ("radio", "checkbox", "select", "chips"):
        options = [o.get("text", "") for o in widget.get("options", []) if o.get("text")]
        if not options:
            return Discard(NO_ANSWER_CODE, "no options parsed")
        if hooks.q_opts_mismatch(q, options):
            return Discard(STALE_PAIR_CODE, f"question/options look mismatched: {options[:4]}")
        if kind == "checkbox":
            # Multi-select. Personal facts come from config first (canary 2026-09-15: a single
            # LLM pick answered "What are the languages you know?" with only 'Kannada').
            lower = {o.strip().lower(): o for o in options}
            if "language" in q.lower():
                langs = [str(x).strip().lower() for x in
                         ((config.get("personal_info", {}) or {}).get("languages") or []) if str(x).strip()]
                picks = [lower[l] for l in langs if l in lower]
                if picks:
                    return Answer(value=picks[0], source="config", values=picks)
            raw = hooks.llm_answer(f"{q} (Select ALL options that apply. Options: {' | '.join(options)}. "
                                   f"Reply with the exact option texts separated by ' | '.)", options=None, config=config)
            picks = []
            for part in re.split(r"\s*[|;,]\s*", raw or ""):
                o = lower.get(part.strip().strip('"\'').lower())
                if o and o not in picks:
                    picks.append(o)
            if picks:
                return Answer(value=picks[0], source="llm", values=picks)
        if _is_dob_question(q):
            dob = _dob_parts(config)
            if dob:
                m = _match_date_option(widget.get("options", []), "year", dob) if any(
                    (o.get("text") or "").strip().isdigit() and len(o.get("text", "").strip()) == 4
                    for o in widget.get("options", [])) else None
                if m:
                    return Answer(value=m, source="config")
        decision, payload = hooks.classify_radio(q, options, loc_variants)
        if decision == "select" and payload in options:
            return Answer(value=payload, source="policy")
        if decision == "discard":
            return Discard(POLICY_CODE, str(payload))
        pick = hooks.llm_answer(q, options=options, config=config)
        if pick and pick in options:
            return Answer(value=pick, source="llm")
        return Discard(NO_ANSWER_CODE, "no rule or LLM answer matched an option")

    if kind in ("text", "textarea", "composer"):
        try:
            ans = hooks.text_answer(bot, q)
        except Exception as e:
            return Discard(NO_ANSWER_CODE, f"text_answer raised: {e}")
        if ans:
            # 2026-09-16: hooks.text_answer (_unattended_text_answer) stamps
            # bot._last_text_answer_source before returning so a fabrication-risk LLM answer
            # logs as src=llm, not src=rules -- previously every free-text answer, including
            # LLM output, was logged identically and indistinguishable in the watcher.
            src = getattr(bot, "_last_text_answer_source", None) or "rules"
            limit = widget.get("maxLength") or NAUKRI_TEXT_MAX
            value = _fit_text(ans, limit)
            if len(value) < len(str(ans)):
                logger.info(f"[chat-v2] shortened answer {len(str(ans))}->{len(value)} chars "
                            f"(limit {limit}) q='{q[:60]}'")
            return Answer(value=value, source=src)
        return Discard(NO_ANSWER_CODE, "no rule, QA-dictionary or LLM answer")

    return Discard(UNSUPPORTED_CODE, f"unhandled widget kind '{kind}'")

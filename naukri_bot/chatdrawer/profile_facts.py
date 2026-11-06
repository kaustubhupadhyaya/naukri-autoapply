"""Read the user's own live Naukri profile for facts that should never go stale in config.

Built 2026-09-15 while chasing a bad LLM answer to "What are the languages you know?". The
profile page's "Personal details" section (where Naukri keeps languages, among other things)
turned out to be genuinely empty for this account — checked live, twice, nothing to scrape — so
this does NOT solve languages. What it found instead, live-sampling the same page: the
account's Naukri-visible CTC and notice period **did not match `config.local.json`** at all
(checked as a boolean, no values printed). Those two are asked on nearly every application, so
keeping them synced to the profile Naukri itself shows recruiters is worth more than the
languages question this was originally chasing.

Only reads. Never edits the profile, never submits the "Add" forms for the empty sections.
"""
import json
import logging
import re
import time

logger = logging.getLogger("naukri_unattended")

PROFILE_URL = "https://www.naukri.com/mnjuser/profile"

# em.icon[name="X"] -> immediately followed by a span carrying the value in its `title` attribute
# (confirmed live 2026-09-15; see naukri_bot/chatdrawer/profile_facts.py module docstring history).
_PROBE_JS = r"""
JSON.stringify((function () {
  function valueFor(iconName) {
    var icon = document.querySelector('em.icon[name="' + iconName + '"]');
    if (!icon) return null;
    var sib = icon.nextElementSibling;
    if (!sib) return null;
    var t = sib.getAttribute('title');
    return (t && t.trim()) ? t.trim() : (sib.innerText || '').trim();
  }
  return {
    url: location.href, ready: document.readyState, bodyLen: (document.body.innerText || '').length,
    salary: valueFor('Salary'),
    experience: valueFor('Experience'),
    location: valueFor('Location'),
    notice: valueFor('Date')
  };
})())
"""

_CACHED_FACTS = None


def _parse_lpa(text):
    """'₹ Twelve lakh ' / '₹ 12,00,000' -> 12.0 (LPA). Naukri's title attr uses words, the
    visible span uses digits; try digits first (unambiguous), words as fallback."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        try:
            return round(int(digits) / 100000.0, 2)
        except ValueError:
            pass
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
             "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
    m = re.search(r"\b(" + "|".join(words) + r")\b", text.lower())
    return float(words[m.group(1)]) if m else None


def _parse_experience_years(text):
    """'2 Year(s) 6 Month(s)' -> 2.5"""
    if not text:
        return None
    y = re.search(r"(\d+)\s*Year", text)
    m = re.search(r"(\d+)\s*Month", text)
    years = int(y.group(1)) if y else 0
    months = int(m.group(1)) if m else 0
    if not y and not m:
        return None
    return round(years + months / 12.0, 2)


def _parse_notice_days(text):
    """'Available to join in 15 Days or less' -> 15; 'Immediately' / 'Serving notice period' etc."""
    if not text:
        return None
    tl = text.lower()
    if "immediate" in tl:
        return 0
    m = re.search(r"(\d+)\s*day", tl)
    return int(m.group(1)) if m else None


def get_profile_facts(driver, timeout=20.0, force=False):
    """One-time (cached) read of the user's own Naukri profile page. Returns {} on any failure —
    never raises, never blocks the bot's own flow on a scrape it doesn't strictly need."""
    global _CACHED_FACTS
    if _CACHED_FACTS is not None and not force:
        return _CACHED_FACTS
    facts = {}
    try:
        original_url = driver.current_url
    except Exception:
        original_url = None
    try:
        driver.get(PROFILE_URL)
        deadline = time.time() + timeout
        raw = None       # last successful read, never clobbered by a later failed attempt
        last_exc = None  # so a total failure says WHY, instead of just "nothing found"
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            try:
                r = driver.execute_script("return " + _PROBE_JS)
                if r:
                    raw = r
                    data = json.loads(raw)
                    if data.get("ready") == "complete" and (data.get("salary") or data.get("notice")):
                        break
            except Exception as e:
                last_exc = f"{type(e).__name__}: {str(e)[:150]}"
            time.sleep(0.5)
        if raw is None and last_exc:
            logger.info(f"👤 Naukri profile facts: execute_script failed on all {attempts} attempts "
                       f"({last_exc})")
        if raw:
            data = json.loads(raw)
            if data.get("salary"):
                facts["current_ctc_text"] = data["salary"]
                lpa = _parse_lpa(data["salary"])
                if lpa:
                    facts["current_ctc_lpa"] = lpa
            if data.get("experience"):
                facts["experience_text"] = data["experience"]
                yrs = _parse_experience_years(data["experience"])
                if yrs is not None:
                    facts["experience_years"] = yrs
            if data.get("location"):
                facts["location"] = data["location"]
            if data.get("notice"):
                facts["notice_period_text"] = data["notice"]
                days = _parse_notice_days(data["notice"])
                if days is not None:
                    facts["notice_period_days"] = days
        if facts:
            logger.info(f"👤 Naukri profile facts read: {list(facts.keys())}")
        else:
            diag = json.loads(raw) if raw else {}
            logger.info(f"👤 Naukri profile facts: none found "
                       f"(url={diag.get('url', '?')[:70]!r} ready={diag.get('ready')} bodyLen={diag.get('bodyLen')})")
    except Exception as e:
        logger.debug(f"Profile facts scrape failed (non-fatal): {e}")
    finally:
        if original_url:
            try:
                driver.get(original_url)
            except Exception:
                pass
    _CACHED_FACTS = facts
    return facts


def apply_facts_to_config(config, facts):
    """Attach live profile facts to the in-memory config (never written to disk — same
    'runtime overlay only' rule as the rest of this file's config handling) for
    _get_resume_digest to read. Deliberately does NOT overwrite config.local.json's
    chatbot_answers values here — those also feed the direct keyword-answer path
    (_get_keyword_answer), and a candidate may legitimately declare a different CTC to the bot
    than what's public on their Naukri profile. Instead, any mismatch is logged once so the
    user can decide (this is how the CTC/notice-period mismatch below was actually found)."""
    if not facts:
        return
    config["_profile_facts"] = facts
    cb = config.get("chatbot_answers", {}) or {}
    cfg_ctc = re.search(r"[\d.]+", str(cb.get("current_ctc") or ""))
    live_ctc = facts.get("current_ctc_lpa")
    if cfg_ctc and live_ctc is not None and abs(float(cfg_ctc.group()) - live_ctc) > 0.5:
        logger.warning(f"⚠️ config chatbot_answers.current_ctc doesn't match the live Naukri "
                       f"profile ({live_ctc:g} LPA) — using the live figure for open-ended "
                       f"LLM answers; config is unchanged (used as-is for direct keyword answers)")
    live_days = facts.get("notice_period_days")
    cfg_notice = str(cb.get("notice_period") or "")
    if live_days is not None and cfg_notice and str(live_days) not in cfg_notice \
            and not (live_days == 0 and "immediate" in cfg_notice.lower()):
        logger.warning(f"⚠️ config chatbot_answers.notice_period doesn't match the live Naukri "
                       f"profile ('{facts.get('notice_period_text')}') — using the live figure "
                       f"for open-ended LLM answers; config is unchanged")

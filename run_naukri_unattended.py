"""Untracked unattended runner for the Naukri bot. Leaves tracked files untouched.

Why it exists: the shipped Easy-Apply flow targets an obsolete Naukri DOM
(question selector never matches, answers only go to <input>, radio/select
questions unhandled, submit search wants a "submit" button while Naukri shows a
disabled-until-answered "Save"). Result was 18 attempted / 0 applied.

What this wrapper monkeypatches (reversible by deleting this file):
 N1 ChatbotMixin._handle_chatbot -> real Q&A: question bubbles, contenteditable
    chat box, radio/select groups, per-answer send, Save enabled-state gate.
 N2 ApplicationMixin._handle_easy_apply_submission_improved -> tries the Save
    button first (wait-for-enabled + 3 click strategies + verification),
    falls back to the original submit scan for non-chatbot flows.
 N3 Title/company extraction refreshed (logging/DB only, zero risk).
 N4 External-apply: skipped WITHOUT opening tabs (default; the only safe
    unattended behaviour). Set NAUKRI_SKIP_EXTERNAL=0 to restore tracked
    behaviour (opens company-site tabs for manual filling).
 N5 Answering policy (user-voted): safe defaults FIRST, Gemini SECOND,
    discard unknowns (never misrepresent):
      - Yes: relocate / join / notice-serve / WFO-hybrid-onsite / F2F /
             contract / travel / BGV consent (only when a Yes-like option exists)
      - Location: option matching config location (Bengaluru/Bangalore/...),
                  never a wrong city
      - CTC/salary/notice/experience/phone/email/name: config values
      - qa_dictionary.json exact matches (user-fillable for free text)
      - Gemini (only if GEMINI_API_KEY env is set, never committed): free-text
        answers + picking among radio options
      - DISCARD (job skipped, no lies): employer-specific ("employed by X",
        "currently employed", company traps), legal/health/conviction,
        visa/citizenship, PII traps (PAN/Aadhaar/bank),
        anything unmapped with no AI available.
      - DOB: answered from personal_info.date_of_birth (config.local.json, "DD/MM/YYYY") when
        present — a single free-text "DD/MM/YYYY" field only; a 3-way split day/month/year
        drawer needs the chatdrawer v2 engine (naukri_bot/chatdrawer/), not this v1 path.
 N6 Trailing input() ("Press Enter to close...") neutralised for headless runs.

Runtime config overlay (in memory only, files never written):
  config.local.json (cap 20) + GEMINI_API_KEY env + NAUKRI_SKIP_EXTERNAL env.

Usage:
  .venv\\Scripts\\python.exe run_naukri_unattended.py [--self-test]
"""
import os
import sys
import json
import time


def _early_profile():
    """--profile X / --profile=X / NAUKRI_PROFILE, read before anything else so the single-flight
    lock, log file and resume paths belong to that account. build_bot() parses the same inputs."""
    for i, a in enumerate(sys.argv):
        if a.startswith("--profile="):
            return a.split("=", 1)[1].strip() or "default"
        if a == "--profile" and i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip() or "default"
    return os.environ.get("NAUKRI_PROFILE", "default").strip() or "default"


# 2026-09-29: one supervised bot per Naukri account. The default profile (account 1) keeps the
# exact lock/mutex/log/marker names it always had; every other profile gets its own.
_EARLY_PROFILE = _early_profile()
_IS_DEFAULT_PROFILE = _EARLY_PROFILE == "default"
_PROFILE_SUFFIX = "" if _IS_DEFAULT_PROFILE else f".{_EARLY_PROFILE}"

# Single-flight file lock FIRST (before every other import): a same-second duplicate
# interpreter spawns per launch. Loser exits here in <1s, before touching Edge/DB/logs.
try:
    import msvcrt as _msvcrt

    _LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), f".bot_singleflight{_PROFILE_SUFFIX}.lock")
    _LOCK_FH = open(_LOCK_PATH, "a+b")
    try:
        _msvcrt.locking(_LOCK_FH.fileno(), _msvcrt.LK_NBLCK, 1)
    except OSError:
        print("Duplicate bot instance detected via single-flight lock - exiting.", flush=True)
        sys.exit(0)
except SystemExit:
    raise
except Exception as _e:
    print(f"Single-flight lock unavailable ({_e}) - proceeding without it.", flush=True)
import re
import logging
import sqlite3
import subprocess
import requests
import random
import urllib.parse
import faulthandler
from datetime import datetime

# H5 (2026-09-15): 68 launches/24h left no trace of *why* — stderr is overwritten on every
# relaunch (naukri_keepalive.ps1) and there was no crash dump for native/interpreter deaths.
# This alone can't fix the overwrite, but it turns a silent segfault/hang into a traceback in
# whatever stderr file this launch does get.
try:
    faulthandler.enable()
except Exception:
    pass

REPO = os.path.dirname(os.path.abspath(__file__))
os.chdir(REPO)

# Per-account directory for everything a second instance would otherwise share with account 1
# (log, telemetry, screenshots, daily-limit marker). Default profile = the repo root, as before.
_PROFILE_DIR = REPO if _IS_DEFAULT_PROFILE else os.path.join(REPO, "profiles", _EARLY_PROFILE)
_EARLY_CFG = {}
if not _IS_DEFAULT_PROFILE:
    os.makedirs(_PROFILE_DIR, exist_ok=True)
    try:
        with open(os.path.join(_PROFILE_DIR, "config.json"), encoding="utf-8") as _fh:
            _EARLY_CFG = json.load(_fh)
    except Exception:
        _EARLY_CFG = {}
    # Claim the root logger before `import main` does: main.py only adds its repo-root
    # naukri_bot.log handler when the root logger has none, so this account never writes there.
    import logging as _logging
    from logging.handlers import RotatingFileHandler as _RFH

    _root_logger = _logging.getLogger()
    if not _root_logger.handlers:
        _fmt = _logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        _pfh = _RFH(os.path.join(_PROFILE_DIR, "naukri_bot.log"), maxBytes=10 * 1024 * 1024,
                    backupCount=5, encoding="utf-8")
        _pfh.setFormatter(_fmt)
        _pch = _logging.StreamHandler()
        _pch.setFormatter(_fmt)
        _root_logger.setLevel(_logging.INFO)
        _root_logger.addHandler(_pfh)
        _root_logger.addHandler(_pch)

# N6: never block on stdin in unattended runs.
try:
    import builtins
    _orig_input = builtins.input
    builtins.input = lambda *a, **k: ""
except Exception:
    pass

import main  # noqa: F401  -- side effect only: root logging + win32 UTF-8 wrap
from naukri_bot.core.naukri_bot import NaukriBot
from naukri_bot.chatbot.chatbot import ChatbotMixin
from naukri_bot.modules.application import ApplicationMixin
from naukri_bot.modules.auth import AuthMixin

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    InvalidSessionIdException,
    WebDriverException,
)

logger = logging.getLogger("naukri_unattended")

# ---------------------------------------------------------------- policy tables
# 2026-09-24 (user's call): the old blanket DISCARD list (employer history, visa, criminal,
# health, PII...) skipped the whole job, and 10 jobs were lost to it every cycle. Every
# option-question now goes to the LLM, which answers from the resume + the stated facts in the
# profile digest (employment history, work authorization). Only personal demographics remain
# special: pick a neutral option when one is offered; with no neutral option, skip — nothing on
# file answers them and guessing (e.g. from the name) would misrepresent the candidate.
DEMOGRAPHIC_SUBSTR = ("gender", "marital", "married", "religion", "caste", "pregnan")
NEUTRAL_PREFERRED_SUBSTR = DEMOGRAPHIC_SUBSTR + ("health", "disab", "veteran", "ex-serviceman", "military")
NEUTRAL_OPTION_SUBSTR = ("prefer not", "not to say", "choose not", "not to self-identify",
                         "not to disclose", "decline", "rather not", "do not wish", "not wish to")
DISCARD_SUBSTR = DEMOGRAPHIC_SUBSTR
SAFE_YES_SUBSTR = (
    # safe affirmative consents / availability (only when a Yes-like option exists)
    "relocat", "willing to join", "join immediately", "immediate join",
    "serve notice", "work from office", "work from client", "hybrid",
    "on-site", "onsite", "face to face", "f2f", "interview",
    "contract", "contractual", "travel", "willing to work",
    "background verification", "bgv", "drug test",
)
LOCATION_WORDS = (
    "location", "city", "cities", "based in", "work location",
    "preferred", "current location", "live in", "resid", "job location",
)
YES_LIKE = ("yes", "agree", "i do", "i have", "willing", "sure", "ok", "confirm")

# Shared with naukri_bot/chatdrawer (v2 engine) via the Hooks injected in the install() call
# near the bottom of this file - one constant instead of two copies drifting apart.
# Both paths come from the environment or the active profile's config:
#   env   NAUKRI_RESUME_FILE / NAUKRI_RESUME_UPLOAD_FILE
#   json  {"resume": {"text_file": "...", "upload_file": "..."}}
# The text file is what the LLM answers from; the upload file is sent to Naukri.
def _resume_config():
    cfg = _EARLY_CFG.get("resume") or {}
    if not cfg:
        for _name in ("config.local.json", "config.json"):
            try:
                with open(os.path.join(REPO, _name), encoding="utf-8") as _fh:
                    cfg = (json.load(_fh).get("resume") or {})
                break
            except Exception:
                continue
    return cfg or {}


_RESUME_CFG = _resume_config()
RESUME_FILE = (os.environ.get("NAUKRI_RESUME_FILE") or _RESUME_CFG.get("text_file") or "").strip()
RESUME_UPLOAD_FILE = (os.environ.get("NAUKRI_RESUME_UPLOAD_FILE") or _RESUME_CFG.get("upload_file") or "").strip()
_RESUME_NAMES = {os.path.basename(p).lower() for p in (RESUME_FILE, RESUME_UPLOAD_FILE) if p}

SAVE_XPATH = (
    "//button[contains(translate(normalize-space(.),"
    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'save')]"
)
FOOTER_SAVE_XPATH = (
    "//div[contains(@class,'footerWrapper') or contains(@class,'Footer')]"
    "//button"
)


def _match_option(options, needles):
    for opt in options:
        ol = (opt or "").strip().lower()
        if any(n in ol for n in needles):
            return opt
    return None


# Short tokens need word boundaries: the bare substring "fir" (First Information Report) matched
# "confirm"/"first" and discarded every "I confirm ..." consent question (v2 canary 2026-09-15).
_DISCARD_RX = [(t, re.compile(r"\b" + re.escape(t) + r"\b") if len(t) <= 4 else None) for t in DISCARD_SUBSTR]


def _discard_hit(q):
    for token, rx in _DISCARD_RX:
        if (rx.search(q) if rx else token in q):
            return token
    return None


def classify_radio(question, options, location_variants):
    """('select', option) | ('gemini', None) | ('discard', reason). No side effects apart from an info
    log line when the offers rule picks an option."""
    q = (question or "").lower()
    opts = [o for o in (options or []) if o and str(o).strip()]
    if not opts:
        return ("discard", "no options parsed")
    if any(t in q for t in NEUTRAL_PREFERRED_SUBSTR):
        neutral = _match_option(opts, list(NEUTRAL_OPTION_SUBSTR))
        if neutral:
            return ("select", neutral)
    hit = _discard_hit(q)
    if hit:
        return ("discard", f"personal demographic with no neutral option ('{hit}')")
    # 2026-10-14: offers in hand (user fact 10-08: none) -> the "No" option. Ahead of SAFE_YES, which
    # would tick Yes on an offers question that also says "interview". No No-like option -> the LLM,
    # never the SAFE_YES / location rules below.
    offers = _offers_answer(q)
    if offers:
        pick = next((o for o in opts if o.strip().lower() == offers.lower()), None) \
            or next((o for o in opts if _NO_OPTION_RX.search(o.strip().lower())), None)
        if pick:
            logger.info(f"[offers rule] via=options q='{(question or '')[:70]}' a='{pick}'")
            return ("select", pick)
        return ("gemini", None)
    if q:
        loc_hit = _match_option(opts, [v.lower() for v in location_variants])
        if loc_hit and any(w in q for w in LOCATION_WORDS):
            return ("select", loc_hit)
        yes_hit = _match_option(opts, list(YES_LIKE))
        if yes_hit and any(s in q for s in SAFE_YES_SUBSTR):
            return ("select", yes_hit)
    # Unknown Yes/No or value question -> let Gemini choose among options,
    # else discard (never blind-pick options[0]).
    return ("gemini", None)


def _load_qa_dictionary():
    try:
        with open(os.path.join(REPO, "qa_dictionary.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k).strip().lower(): str(v).strip()
                for k, v in data.items() if str(v).strip()}
    except Exception:
        return {}


QA_DICT = _load_qa_dictionary()

# ----------------------------------------------- profile digest & LLM answering
_CACHED_PROFILE_DIGEST = None


def _resume_text(path):
    """Plain text of the resume file: .docx via the stdlib (the venv has no docx/pdf library),
    .pdf via whichever extractor happens to be installed. "" when unreadable."""
    if not path or not os.path.exists(path):
        return ""
    if path.lower().endswith(".docx"):
        try:
            import html
            import zipfile
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            paras = []
            for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
                t = "".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", p))
                if t.strip():
                    paras.append(html.unescape(t))
            return "\n".join(paras)
        except Exception as e:
            logger.warning(f"⚠️ Could not read resume {path}: {e!r}")
            return ""
    for extractor in ("pypdf", "pdfminer.high_level", "fitz"):
        try:
            mod = __import__(extractor)
            if extractor == "pypdf":
                text = "\n".join(page.extract_text() or "" for page in mod.PdfReader(path).pages)
            elif extractor == "pdfminer.high_level":
                text = mod.extract_text(path)
            else:
                text = "\n".join(page.get_text() for page in mod.open(path))
            if text.strip():
                return text
        except Exception:
            continue
    return ""


# Optional private knowledge module: a Python file exposing prompt_block(closing=False).
# Set NAUKRI_KNOWLEDGE_MODULE or config "knowledge_module"; when unset the digest is unchanged.
KNOWLEDGE_MODULE = os.environ.get("NAUKRI_KNOWLEDGE_MODULE", "").strip()


def _knowledge_block(path=None):
    """One prompt block from an optional knowledge module; "" when it is not configured,
    missing or unreadable. Never raises."""
    path = path or KNOWLEDGE_MODULE
    if not path or not os.path.exists(path):
        return ""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("portal_knowledge", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        block = mod.prompt_block(closing=False)      # this bot has its own way to say "not in the profile" (INSUFFICIENT_DATA)
        if block:
            logger.info(f"📚 Knowledge file facts added to the digest ({len(block)} chars)")
        return block
    except Exception as e:
        logger.warning(f"⚠️ Knowledge file not added to the digest: {e!r}")
        return ""


def _get_resume_digest(config=None):
    """Build and cache a concise profile digest from config values and resume text."""
    global _CACHED_PROFILE_DIGEST
    if _CACHED_PROFILE_DIGEST:
        return _CACHED_PROFILE_DIGEST

    config = config or {}
    cp = config.get("candidate_profile") or {}
    pi = config.get("personal_info", {}) or {}
    facts = config.get("_profile_facts") or {}
    job = config.get("job_search", {}) or {}
    up = config.get("user_profile", {}) or {}
    cb = config.get("chatbot_answers", {}) or {}

    resume_path = (config.get("resume") or {}).get("text_file") or RESUME_FILE
    pdf_text = _resume_text(resume_path)
    if pdf_text:
        logger.info(f"📄 Resume text read from {resume_path} ({len(pdf_text)} chars)")
    elif resume_path:
        logger.warning(f"⚠️ Resume file unreadable or missing: {resume_path} - falling back to "
                       "enhanced_config.json resume_text (may be stale)")

    enh_skills = []
    enh_resume = ""
    enh_path = os.path.join(REPO, "enhanced_config.json")
    if cp.get("use_enhanced_config", True) and os.path.exists(enh_path):
        try:
            with open(enh_path, encoding="utf-8") as fh:
                enh_profile = (json.load(fh).get("user_profile") or {})
                enh_skills = enh_profile.get("core_skills", []) or []
                enh_resume = enh_profile.get("resume_text", "") or ""
        except Exception:
            pass

    def _text(value):
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip() if value not in (None, "") else ""

    def _ctc(value):
        value = _text(value)
        if value and not any(u in value.lower() for u in ("lpa", "lakh", "lac")):
            value = f"{value} LPA"
        return value

    name = _text(cp.get("name") or pi.get("full_name") or up.get("name")) or "Candidate"
    roles = _text(cp.get("target_roles") or up.get("preferred_roles") or job.get("keywords"))
    exp_years = _text(pi.get("total_experience"))
    if not exp_years and facts.get("experience_years") is not None:
        exp_years = f"{facts['experience_years']:g} years"
    if not exp_years and up.get("experience_years") not in (None, ""):
        exp_years = f"{up['experience_years']} years"
    domain = _text(cp.get("experience_domain"))
    location_line = _text(facts.get("location") or pi.get("current_location") or job.get("location"))
    preferred = _text(cp.get("preferred_location") or up.get("location_preference"))
    notice_line = _text(pi.get("notice_period") or cb.get("notice_period") or facts.get("notice_period_text"))
    cur_ctc = _ctc(pi.get("current_ctc") or cb.get("current_ctc")
                   or (f"{pi['current_ctc_lpa']:g} LPA" if pi.get("current_ctc_lpa") else "")
                   or (f"{facts['current_ctc_lpa']:g} LPA" if facts.get("current_ctc_lpa") else ""))
    exp_ctc = _ctc(pi.get("expected_ctc") or cb.get("expected_ctc")
                   or (f"{pi['expected_ctc_lpa']:g} LPA" if pi.get("expected_ctc_lpa") else "")
                   or (f"{facts['expected_ctc_lpa']:g} LPA" if facts.get("expected_ctc_lpa") else ""))

    skills_list = []
    for source in (cp.get("key_skills"), up.get("core_skills"), enh_skills):
        for s in (source or []):
            s = str(s).strip()
            if s and s not in skills_list and len(skills_list) < 20:
                skills_list.append(s)

    employment = _text(cp.get("employment_history") or pi.get("employment_history"))
    authorization = _text(cp.get("work_authorization") or pi.get("work_authorization"))
    relocation = cp.get("relocation", pi.get("relocation"))
    offers = cp.get("has_job_offer", pi.get("has_job_offer", pi.get("offers_in_hand")))

    lines = [
        "Candidate Profile:",
        f"Name: {name}",
    ]
    if roles:
        lines.append(f"Target Roles: {roles}")
    if exp_years:
        lines.append(f"Total Experience: {exp_years}" + (f" ({domain})" if domain else ""))
    if skills_list:
        lines += [
            "Skill Experience: every listed skill was used within the total experience above; "
            "no single skill exceeds it.",
            f"Key Skills: {', '.join(skills_list)}",
        ]
    if location_line:
        lines.append(f"Current Location: {location_line}")
    if preferred:
        lines.append(f"Preferred Location: {preferred} (open to Hybrid / On-site / Relocation)")
    if notice_line:
        lines.append(f"Notice Period: {notice_line}")
        if pi.get("serving_notice") is False:
            lines[-1] += " (not currently serving notice; no last working day)"
    if cur_ctc:
        lines.append(f"Current CTC: {cur_ctc}")
    if exp_ctc:
        lines.append(f"Expected CTC: {exp_ctc}")
    if isinstance(relocation, bool):
        if relocation:
            lines.append("Relocation & Availability: Yes")
    elif relocation not in (None, ""):
        lines.append(f"Relocation & Availability: {_text(relocation)}")
    if employment:
        lines.append(f"Complete Employment History: {employment}")
    if authorization:
        lines.append(f"Work Authorization: {authorization}")
    if offers is False:
        lines.append("Job Offers: currently holds no job offer from any company (none in hand, none in pipeline). "
                     "Answer 'do you have/hold an offer' questions 'No', and questions about such an offer's "
                     "amount, offered CTC or joining date 'Not applicable'.")
    if pdf_text:
        lines.append(f"Resume (authoritative): {pdf_text.strip()[:8000]}")
    elif enh_resume:
        lines.append(f"Profile Summary: {enh_resume.strip()[:8000]}")

    kb = _knowledge_block(config.get("knowledge_module") or KNOWLEDGE_MODULE)
    if kb:
        lines.append(kb)

    _CACHED_PROFILE_DIGEST = "\n".join(lines)
    logger.info(f"📄 Resume profile digest cached ({len(_CACHED_PROFILE_DIGEST)} chars)")
    return _CACHED_PROFILE_DIGEST


def _refresh_profile_digest(config):
    """Force-rebuild the cached digest — used once live Naukri-profile facts become available
    (they can't be read until the driver exists, which is after the digest's first, driver-less
    build in build_bot()). See naukri_bot/chatdrawer/profile_facts.py."""
    global _CACHED_PROFILE_DIGEST
    _CACHED_PROFILE_DIGEST = None
    return _get_resume_digest(config)


_LAST_LLM_MODEL = "unknown"

# 2026-09-16: no channel here previously carried any anti-fabrication instruction, and the
# free-text prompt gave the model no way to decline -- confirmed root cause of a fabricated
# answer ("PostgreSQL pharmaceutical sales and inventory tables") to a real employer. Sent as a
# real `system` field where the backend supports one (local proxy, Gemini); embedded into the
# prompt text itself for every backend, since OpenCode Zen's CLI has no separate system channel.
SYSTEM_NO_FABRICATION = (
    "You are answering job-application screening questions on a real candidate's behalf, to be "
    "sent to a real employer. Never invent a specific fact (an employer, project, table, tool, "
    "dashboard, KPI or number) that is not present in the candidate profile you are given. If a "
    "question asks for a specific detail the profile does not contain, say so exactly as the user "
    "message instructs instead of fabricating a plausible-sounding one."
)


def _call_local_proxy(prompt):
    """Call local Antigravity proxy with adequate token budget for reasoning models."""
    global _LAST_LLM_MODEL
    url = "http://127.0.0.1:8080/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": "dummy",
        "anthropic-version": "2023-06-01",
    }
    for model_name in ("gemini-2.5-flash", "gemini-3.7-flash-tiered", "gemini-3.8-flash-tiered"):
        # Fast model gets a small budget (1-line answers); reasoning-tiered models keep
        # room for thinking tokens so they don't starve.
        _budget = 256 if model_name == "gemini-2.5-flash" else 1024
        payload = {
            "model": model_name,
            "max_tokens": _budget,
            "system": SYSTEM_NO_FABRICATION,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", [])
                text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
                if text:
                    _LAST_LLM_MODEL = f"Local Proxy ({model_name})"
                    logger.info(f"🧠 Local proxy ({model_name}) answered question")
                    return text
        except Exception as e:
            logger.debug(f"Local LLM proxy ({model_name}) failed: {e}")
    return None


def _call_opencode_zen(prompt):
    """Call free model on OpenCode Zen as zero-cost fallback."""
    exe = os.environ.get("NAUKRI_OPENCODE_EXE", "").strip()
    if not exe:
        import shutil
        exe = shutil.which("opencode") or ""
    if not exe or not os.path.exists(exe):
        return None
    try:
        cmd = [exe, "run", "-m", "opencode/muse-spark-1.3-contributor-free", "--pure", prompt]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and p.stdout:
            lines = [
                l.strip() for l in p.stdout.splitlines()
                if l.strip() and not l.startswith(">") and not l.startswith("Skill") and not l.startswith("→")
            ]
            if lines:
                ans = lines[-1].strip()
                logger.info(f"🧠 OpenCode Zen (muse-spark-1.3) answered question: {ans}")
                return ans
    except Exception as e:
        logger.debug(f"OpenCode Zen call failed: {e}")
    return None


def _call_gemini_fallback(prompt, config=None):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key and config:
        key = str(config.get("gemini_api_key", "")).strip()
    if not key:
        enh_path = os.path.join(REPO, "enhanced_config.json")
        if os.path.exists(enh_path):
            try:
                with open(enh_path, encoding="utf-8") as fh:
                    key = json.load(fh).get("gemini_api_key", "").strip()
            except Exception:
                pass
    if not key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=SYSTEM_NO_FABRICATION)
        resp = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 30, "temperature": 0.1}
        )
        if resp and resp.text:
            return resp.text.strip()
    except Exception as e:
        logger.debug(f"Gemini API fallback error: {e}")
    return None


def _match_llm_option(resp_text, options):
    if not resp_text or not options:
        return None
    r = resp_text.strip().strip('"\'`').strip().lower()
    if not r:
        return None

    # 1. Exact match
    for opt in options:
        if opt and opt.strip().lower() == r:
            return opt

    # 2. Word boundary match
    for opt in options:
        ol = opt.strip().lower()
        if ol and re.search(r'\b' + re.escape(ol) + r'\b', r):
            return opt

    # 3. Substring match
    for opt in options:
        ol = opt.strip().lower()
        if ol and (ol in r or r in ol):
            return opt

    return None


def _llm_answer_question(question_text, options=None, config=None):
    """Grounded LLM question answering: local proxy first, Gemini fallback second."""
    q = (question_text or "").strip()
    if not q:
        return None
    # Session memo: identical questions repeat across jobs — answer once, reuse instantly.
    try:
        _mkey = (_qkey(q), tuple(sorted(str(o).strip().lower() for o in (options or []) if str(o).strip())))
        if _mkey in _LLM_MEMO:
            _memo_ans = _LLM_MEMO[_mkey]
            logger.info(f"🤖 [memo hit]: Q='{q[:70]}' -> '{str(_memo_ans)[:60]}'")
            return _memo_ans
    except Exception:
        _mkey = None
    digest = _get_resume_digest(config)

    if options:
        opts_clean = [str(o).strip() for o in options if str(o).strip()]
        if not opts_clean:
            return None
        opts_str = "\n".join(f"- {o}" for o in opts_clean)
        prompt = (
            f"{digest}\n\n"
            f'Question: "{q}"\n\n'
            f"Available Options:\n{opts_str}\n\n"
            "CRITICAL INSTRUCTION: Select EXACTLY one option from the Available Options list above "
            "that best matches the candidate profile. Output ONLY the exact text of the chosen option from the list, nothing else."
        )
    else:
        opts_clean = None
        prompt = (
            f"{digest}\n\n"
            f'Question: "{q}"\n\n'
            "Instruction: Answer in the first person (as the candidate) in ONE short phrase or sentence of "
            "at most 90 characters -- Naukri rejects the whole application if an answer is longer. Use ONLY facts stated in the "
            "candidate profile above (the named employers, projects, tools, metrics and dates in the "
            "Resume / Profile Summary, and the structured fields above it). If the structured fields "
            "(e.g. an 'X Experience' line) disagree with the resume text, the structured fields are "
            "authoritative. NEVER invent a specific employer, project, table, dataset, dashboard, KPI, tool "
            "or number that is not written above -- if the question asks for a specific detail (e.g. 'which "
            "tables', 'what KPIs', 'describe a project you built') that the profile genuinely does not "
            "contain, reply with EXACTLY the single word INSUFFICIENT_DATA instead of guessing. Output ONLY "
            "the answer (or INSUFFICIENT_DATA), no explanations, no preamble."
        )

    raw_answer = _call_local_proxy(prompt)
    model_used = _LAST_LLM_MODEL if _LAST_LLM_MODEL != "unknown" else "Local Proxy"
    if not raw_answer:
        raw_answer = _call_opencode_zen(prompt)
        model_used = "OpenCode Zen (muse-spark)"
    if not raw_answer:
        raw_answer = _call_gemini_fallback(prompt, config)
        model_used = "Google Gemini Direct (gemini-2.5-flash)"
    if not raw_answer:
        logger.warning(f"⚠️ [AI Answering Failed]: No model was able to answer question: '{q[:80]}'")
        return None

    # 2026-09-16: the free-text prompt can decline with INSUFFICIENT_DATA rather than fabricate.
    # Re-ask once, explicitly grounded to the listed skills only, instead of discarding the whole
    # application (a discard in chatdrawer v2 aborts the job entirely -- user's call: no
    # applications lost to this). The memo is deliberately NOT written for either the sentinel or
    # the retried answer below, so a possibly-imperfect retry is never replayed verbatim to every
    # later employer asking the same question -- only a first-try, fully-grounded answer is cached.
    is_retry = False
    if not opts_clean and raw_answer.strip().strip('"\'').strip().upper().replace(" ", "_") == "INSUFFICIENT_DATA":
        logger.info(f"🤖 [{model_used}] declined (INSUFFICIENT_DATA) for Q='{q[:70]}' — re-asking for a general answer")
        retry_prompt = (
            f"{digest}\n\n"
            f'Question: "{q}"\n\n'
            "Instruction: The specific detail asked for is not in the candidate profile above. Answer "
            "honestly and generally using ONLY the skills, experience and roles actually listed above -- "
            "do NOT invent a specific employer, project, table, dashboard, tool or number that isn't "
            "listed. Answer in the first person in ONE short phrase of at most 90 characters (Naukri "
            "rejects longer answers). Output ONLY the answer, no explanations."
        )
        raw_answer = _call_local_proxy(retry_prompt) or _call_opencode_zen(retry_prompt) \
            or _call_gemini_fallback(retry_prompt, config)
        is_retry = True
        if not raw_answer:
            logger.warning(f"⚠️ [AI Answering Failed]: retry also produced nothing for '{q[:80]}'")
            return None

    if opts_clean:
        chosen = _match_llm_option(raw_answer, opts_clean)
        logger.info(f"🤖 [{model_used}]: Q='{q[:70]}' -> Chosen Option='{chosen}' (Raw: '{raw_answer.strip()[:40]}')")
        try:
            if _mkey is not None and chosen:
                _LLM_MEMO[_mkey] = chosen
        except Exception:
            pass
        return chosen
    else:
        line = raw_answer.splitlines()[0].strip().strip('"\'`')
        line = re.sub(r'^(Answer|A):\s*', '', line, flags=re.IGNORECASE).strip()
        logger.info(f"🤖 [{model_used}]: Q='{q[:70]}' -> Answer='{line}'" + (" [retry]" if is_retry else ""))
        try:
            if _mkey is not None and line and not is_retry:
                _LLM_MEMO[_mkey] = line
        except Exception:
            pass
        return line if line else None


def _is_mandatory(q_text, element=None):
    """Detect if a question or form field is marked mandatory/required."""
    if q_text:
        if "*" in q_text or re.search(r'\b(required|mandatory)\b', q_text, re.IGNORECASE):
            return True
    if element is not None:
        try:
            req = element.get_attribute("required")
            if req and str(req).lower() not in ("false", "0", "null"):
                return True
            aria_req = element.get_attribute("aria-required")
            if aria_req and str(aria_req).lower() == "true":
                return True
            el_cls = (element.get_attribute("class") or "").lower()
            if any(k in el_cls for k in ("mandatory", "required", "star", "asterisk")):
                return True
            for xp in (
                "./ancestor::div[contains(@class,'botmsg') or contains(@class,'question') or contains(@class,'form') or contains(@class,'field') or contains(@class,'ssrc')][1]",
                "./ancestor::div[contains(@id,'SingleSelect') or contains(@id,'Radio') or count(.//input)>0][1]",
                "./ancestor::div[1]",
                "./ancestor::div[2]",
            ):
                try:
                    anc = element.find_element(By.XPATH, xp)
                    anc_txt = anc.text or ""
                    if "*" in anc_txt or re.search(r'\b(required|mandatory)\b', anc_txt, re.IGNORECASE):
                        return True
                    anc_cls = (anc.get_attribute("class") or "").lower()
                    if any(k in anc_cls for k in ("mandatory", "required")):
                        return True
                    if anc.find_elements(By.XPATH, ".//*[contains(@class,'mandatory') or contains(@class,'required') or contains(@class,'star') or contains(@class,'asterisk')]"):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


def _close_chatbot_drawer(driver):
    """Attempt to close/cancel the chatbot drawer or modal dialog to abort incomplete application."""
    logger.info("🛑 Closing/canceling chatbot drawer to abort incomplete application")
    close_selectors = [
        "//div[contains(@class,'chatbot') or contains(@class,'drawer') or contains(@class,'modal')]//span[contains(@class,'close') or contains(@class,'cross')]",
        "//div[contains(@class,'chatbot') or contains(@class,'drawer') or contains(@class,'modal')]//button[contains(@class,'close') or contains(@class,'cross') or contains(@class,'cancel')]",
        "//div[contains(@class,'chatbot') or contains(@class,'drawer') or contains(@class,'modal')]//*[@aria-label='Close' or @aria-label='close']",
        "//div[contains(@class,'crossIcon') or contains(@class,'cross-icon')]",
        "//span[contains(@class,'crossIcon') or contains(@class,'close-popup')]",
        "//button[contains(@class,'close') or contains(@class,'cancel')]",
        "//a[contains(@class,'close') or contains(@class,'cancel')]",
        "//div[contains(@class,'chatbot')]//*[name()='svg' and (contains(@class,'close') or contains(@class,'cross'))]",
    ]
    closed = False
    for xp in close_selectors:
        try:
            for el in _displayed(driver.find_elements(By.XPATH, xp)):
                try:
                    el.click()
                    closed = True
                    break
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", el)
                        closed = True
                        break
                    except Exception:
                        continue
            if closed:
                break
        except Exception:
            continue

    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass
    time.sleep(1.0)


def _check_rejection_messages(driver):
    """Fast rejection inspection using JavaScript innerText (5ms execution, zero DOM search stalls)."""
    rejection_phrases = (
        "incomplete information",
        "oops!",
        "mandatory question",
        "mandatory questions",
        "not accepted due to",
        "please answer all mandatory questions",
        "application was not accepted",
        "fill all mandatory",
        "answer all required",
    )
    try:
        body_text = (driver.execute_script("return (document.body ? document.body.innerText : '')") or "").lower()
        for p in rejection_phrases:
            if p in body_text:
                idx = body_text.find(p)
                snippet = body_text[max(0, idx - 20):idx + 100]
                return snippet.strip().replace("\n", " ")
    except Exception as e:
        logger.debug(f"Fast rejection check error: {e}")
    return None

# ------------------------------------------------------- small DOM helpers
_Q_XPATH = (
    ".//div[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'botmsg')"
    " or contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bot-msg')"
    " or contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'question')]"
)


def _displayed(elements):
    out = []
    for el in elements:
        try:
            if el.is_displayed():
                out.append(el)
        except Exception:
            pass
    return out


def _drawer_from_save_button(driver):
    """Save-anchored drawer detection: find the visible Save/Submit button first, then take
    the SMALLEST in-viewport ancestor sized like a drawer (>=300x200). No class-name
    dependence (Naukri uses hashed CSS-module classes). The Save footer is the stable
    landmark — container-first matching kept landing on the wrong subtree."""
    try:
        driver.implicitly_wait(0)
        btns = driver.find_elements(
            By.XPATH,
            ".//*[normalize-space(translate(string(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'))='save' "
            "or normalize-space(translate(string(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'))='save & apply' "
            "or normalize-space(translate(string(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'))='submit']")
        try:
            logger.info(f"🔍 Save-anchor scan: {len(btns)} save-text button(s) page-wide")
        except Exception:
            pass
        best = None
        best_area = None
        for b in btns:
            try:
                if not b.is_displayed():
                    continue
                t = ((b.text or "") + " " + (b.get_attribute("value") or "")).strip().lower()
                if not t or any(c in t for c in ("cancel", "close", "skip", "save job")):
                    continue
                anc = driver.execute_script("""
                    var el = arguments[0], best = null, bestArea = Infinity;
                    for (var i = 0; i < 14 && el; i++) {
                        el = el.parentElement;
                        if (!el || el === document.body || el === document.documentElement) break;
                        var r = el.getBoundingClientRect();
                        if (r.width >= 300 && r.width <= 800 && r.height >= 200 && r.left < window.innerWidth && r.right > 0) {
                            var html = (el.innerHTML || '').toLowerCase();
                            var hasQ = html.indexOf('contenteditable') >= 0 || html.indexOf('ssrc__radio') >= 0
                                || html.indexOf('singleselect') >= 0 || html.indexOf('type="text"') >= 0
                                || html.indexOf("type='text'") >= 0 || html.indexOf('<select') >= 0
                                || html.indexOf('<textarea') >= 0 || html.indexOf('botmsg') >= 0
                                || html.indexOf('bot-msg') >= 0 || html.indexOf('quick-reply') >= 0
                                || html.indexOf('type="radio"') >= 0 || html.indexOf("type='radio'") >= 0;
                            if (!hasQ) continue;
                            var a = r.width * r.height;
                            if (a < bestArea) { bestArea = a; best = el; }
                        }
                    }
                    return best;
                """, b)
                if anc is None:
                    continue
                try:
                    r = driver.execute_script(
                        "var x=arguments[0].getBoundingClientRect();return [x.width, x.height];", anc)
                    area = float(r[0]) * float(r[1])
                except Exception:
                    area = None
                if best is None or (area is not None and (best_area is None or area < best_area)):
                    best = anc
                    best_area = area
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        if best is not None:
            try:
                logger.info("💬 Drawer located via Save-button anchor")
            except Exception:
                pass
            return best
    except Exception:
        pass
    return None


def _find_chat_drawer(driver):
    """Return the visible chatbot drawer/modal container element, or None.

    Save-anchored first (stable landmark); falls back to container scoring.
    Job-page decoys (hidden file inputs, page Apply buttons) never qualify.
    """
    hit = _drawer_from_save_button(driver)
    if hit is not None:
        return hit
    try:
        driver.implicitly_wait(0)
        candidates = driver.find_elements(
            By.CSS_SELECTOR,
            "div[class*='chatbot'], div[class*='drawer'], div[class*='modal'], div[class*='Modal'], "
            "div[class*='dialog'], div[class*='Dialog'], div[class*='bottomSheet'], div[class*='popup']")
        scored = []
        for c in candidates:
            try:
                if not c.is_displayed():
                    continue
                try:
                    rect = driver.execute_script(
                        "var r=arguments[0].getBoundingClientRect();"
                        "return [r.width, r.height, r.left, r.right, window.innerWidth];", c)
                    if not (rect[0] > 0 and rect[1] > 0 and rect[2] < rect[4] and rect[3] > 0):
                        continue  # off-screen slide-in template, not the open drawer
                    if not (rect[0] >= 300 and rect[1] >= 200):
                        continue  # floating help bubble / collapsed widget, not the application drawer
                except Exception:
                    continue
                inner = (c.get_attribute("innerHTML") or "").lower()
                if any(k in inner for k in ("contenteditable", "ssrc__radio", "singleselect",
                                           "type=\"text\"", "type='text'", "<select",
                                           "<textarea", "botmsg", "bot-msg", "quick-reply")):
                    try:
                        area = float(rect[0]) * float(rect[1])
                    except Exception:
                        area = 0
                    scored.append((area, c))
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        if scored:
            scored.sort(key=lambda t: t[0], reverse=True)
            best = scored[0][1]
            try:
                logger.info(f"💬 Drawer selected by area (largest of {len(scored)} candidates)")
            except Exception:
                pass
            return best
    except Exception:
        pass
    finally:
        try:
            driver.implicitly_wait(0)
        except Exception:
            pass
    return None


def _find_save_button(root):
    """Identify the true chatbot-drawer Save / Submit button WITHIN the drawer element.
    Never call with the full driver — page-wide Apply buttons must never match."""
    # Relative XPaths — searched INSIDE the drawer element only, page buttons unreachable.
    # NOTE: Naukri renders Save as a non-<button> control (div/span). The universal
    # exact-text selector matches any element whose ENTIRE text is save/submit —
    # ancestors never match since their text includes questions/options too.
    _up = "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'"
    selectors = (
        f".//*[normalize-space(translate(string(.),{_up}))='save']",
        f".//*[normalize-space(translate(string(.),{_up}))='save & apply']",
        f".//*[normalize-space(translate(string(.),{_up}))='submit']",
        ".//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save') and not(contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save job'))]",
        ".//button[normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='save & apply']",
        ".//button[normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='submit']",
        ".//button[contains(@class,'save') or contains(@class,'Save') or contains(@class,'submit') or contains(@class,'Submit')]",
        ".//input[@type='submit' or @type='button'][contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save')]",
    )
    try:
        for sel in selectors:
            try:
                for el in root.find_elements(By.XPATH, sel):
                    if el.is_displayed():
                        txt = (el.text or el.get_attribute("value") or "").strip().lower()
                        if not txt or any(c in txt for c in ("cancel", "close", "skip", "save job", "type message", "send")):
                            continue
                        if any(k in txt for k in ("save", "submit")):
                            return el
            except StaleElementReferenceException:
                return None
            except Exception:
                continue
    except Exception:
        pass
    return None


def _click_radio_option(driver, input_el, label_el=None):
    """Click a radio option across channels: visible label/pill first (custom-styled),
    then native input click, then JS clicks. Verifies via is_selected()."""
    try:
        if label_el is not None:
            try:
                if label_el.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", label_el)
                    try:
                        label_el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", label_el)
                    time.sleep(0.5)
                    try:
                        if input_el.is_selected():
                            return True
                    except Exception:
                        return True
            except Exception:
                pass
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", input_el)
    except Exception:
        pass
    try:
        input_el.click()
        return True
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].click();", input_el)
        time.sleep(0.5)
        try:
            if input_el.is_selected():
                return True
        except Exception:
            return True
        return True
    except Exception:
        return False


def _qkey(t):
    return " ".join((t or "").strip().lower().split())[:120]


_LLM_MEMO = {}


def _q_opts_mismatch(q, options):
    """Detect stale-question pairing: experience-years question with duration options
    (or vice versa). A wrong answer is worse than a discard (never misrepresent)."""
    ql = (q or "").lower()
    opts = [(o or "").lower() for o in (options or []) if o]
    if not ql or not opts:
        return False
    asks_years = any(p in ql for p in ("how many years", "years of experience", "total experience", "number of years"))
    asks_notice = any(p in ql for p in ("notice", "join", "available", "reliev"))
    dur_hits = sum(1 for o in opts if any(k in o for k in ("day", "month", "week", "immediate", "serving", "notice")))
    if asks_years and not asks_notice and dur_hits >= max(1, len(opts) // 2):
        return True
    # 2026-09-24: removed the "quantity question + Yes/No options = stale pair" rule. Both jobs it
    # blocked were genuine pairs, confirmed against Naukri's own questionnaire payload: "Do you have
    # PF for your current company?" [YES/NO] (flagged only because of the word "current ") and a
    # recruiter who really offered Yes/No for "How many years of GenAI experience?". Yes/No is a
    # legitimate answer set for any question, and v2 reads the options from the same message.
    return False


_PLACEHOLDER_TEXTS = (
    "type message here", "type your message", "write a message", "enter your answer",
    "type here", "type your answer", "enter message", "write here", "ask something",
)


def _is_placeholder(text):
    t = (text or "").strip().lower().rstrip(".… ")
    return bool(t) and t in _PLACEHOLDER_TEXTS


def _box_current_value(box):
    """Current composer value with placeholder-as-text treated as empty (contenteditable
    composers render 'Type message here...' as real text — without this the box looks
    permanently 'filled' and Q2+ is never typed)."""
    try:
        tag = (box.tag_name or "").lower()
        if tag in ("input", "textarea"):
            v = (box.get_attribute("value") or "").strip()
            if v:
                return v
            ph = (box.get_attribute("placeholder") or "").strip()
            cur = v or ph
        else:
            cur = (box.text or "").strip()
            if not cur:
                try:
                    cur = (box.get_attribute("textContent") or "").strip()
                except Exception:
                    cur = ""
        if _is_placeholder(cur):
            return ""
        return cur
    except Exception:
        return ""


def _location_variants(config):
    loc = str(config.get("job_search", {}).get("location", "bengaluru") or "bengaluru")
    variants = {loc, loc.lower()}
    if "bengaluru" in loc.lower() or "bangalore" in loc.lower():
        variants |= {"bengaluru", "bangalore", "bengaluru / bangalore"}
    return sorted(variants)


_TELEMETRY_FILE = os.path.join(_PROFILE_DIR, "naukri_telemetry_12h.jsonl")
# job-tracker source for this account (matches _get_profile_paths()["source_name"])
_SOURCE_NAME = "naukri" if _IS_DEFAULT_PROFILE else f"naukri-{_EARLY_PROFILE}"


def _log_telemetry(fmt, question, answer, success, extra=None):
    """Structured telemetry logger for 12h monitoring."""
    try:
        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": fmt,
            "question": (question or "")[:150],
            "answer": (str(answer) if answer is not None else "")[:100],
            "success": bool(success),
            "extra": extra or {}
        }
        with open(_TELEMETRY_FILE, "a", encoding="utf-8") as tf:
            tf.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"Telemetry log error: {e}")


# ------------------------------------------------------- N1 chatbot rewrite
def _unattended_handle_chatbot(self, timeout=12):
    """Answer the Naukri Easy-Apply chatbot. Returns True when application was saved."""
    self._chatbot_detected = False
    self._chatbot_ok = True
    self._chatbot_unanswered = []
    self._chatbot_mandatory_unanswered = []
    driver = self.driver

    # Enforce zero implicit wait for lightning-fast element checks
    try:
        driver.implicitly_wait(0)
    except Exception:
        pass

    # 1. Drawer present? Locate the actual drawer ELEMENT (scoped container) — page-wide
    # signals caused false positives on the job page (decoy file inputs, page Apply button).
    drawer = _find_chat_drawer(driver)
    if drawer is None:
        # Drawer may animate in after Easy Apply click — one short re-check, then give up fast
        time.sleep(2.0)
        drawer = _find_chat_drawer(driver)
    if drawer is None:
        logger.info("No chatbot drawer element — proceeding to standard submit path")
        return True

    self._chatbot_detected = True
    self._resume_attached = False
    self._chatbot_answered_qs = []
    self._chatbot_given_answers = []
    _prev_qsig = None
    _last_qchange = time.time()
    logger.info("💬 Chatbot drawer found (unattended handler)")
    try:
        _dcls = (drawer.get_attribute("class") or "")[:120]
        _did = drawer.get_attribute("id") or ""
        _dtag = drawer.tag_name or ""
        _dr = driver.execute_script(
            "var r=arguments[0].getBoundingClientRect();return [r.width,r.height,r.left,r.top];", drawer)
        logger.info(f"🧭 Drawer identity: <{_dtag}> id='{_did}' class='{_dcls}' rect={_dr}")
    except Exception as e:
        logger.info(f"🧭 Drawer identity unreadable: {e}")
    time.sleep(2.5)  # Allow initial chatbot bubbles and questions to render
    content_grace_until = time.time() + 15  # drawer content loads async — don't judge it empty too early
    deadline = time.time() + 100
    last_progress = time.time()
    loc_variants = _location_variants(self.config)

    while time.time() < deadline:
        progressed = False

        # Re-resolve drawer each pass (it re-renders after every answer: stale AND
        # detached-but-not-stale references must both be refreshed, else all queries
        # silently return empty and the loop discards answered drawers).
        try:
            _drawer_alive = bool(drawer.is_displayed())
        except Exception:
            _drawer_alive = False
        if not _drawer_alive:
            drawer = _find_chat_drawer(driver)
            if drawer is None:
                # H4 (2026-09-15): the drawer disappearing after at least one answer was
                # sent is not necessarily a failure — Naukri auto-submits after the last
                # answer and navigates to /myapply/... (confirmed live: 3+ real successes
                # were discarded this way, e.g. 'Applied to "Informatica Etl Tester"' in the
                # discard dump). Check Naukri's own verdict before giving up.
                if getattr(self, "_chatbot_answered_qs", None):
                    ev = None
                    for _ in range(5):
                        ev = _applied_evidence(driver)
                        if ev:
                            break
                        time.sleep(2.0)
                    if ev == "applied":
                        logger.info("✅ Drawer closed after answering — Naukri shows Applied (confirmed)")
                        self._chatbot_ok = True
                        _log_telemetry("save_submission", "chatbot_drawer", "applied_after_drawer_close", True)
                        return True
                    if ev == "external":
                        logger.info("↗️ Drawer closed — Naukri redirected to the company site, not applied")
                        self._chatbot_ok = False
                        return False
                    if ev == "rejected":
                        logger.warning("⚠️ Drawer closed — Naukri shows the incomplete-information rejection")
                        self._chatbot_ok = False
                        return False
                break
            progressed = True

        # ---- collect visible question texts (excluding inputs/footers/options)
        qtexts = []
        try:
            for el in drawer.find_elements(By.XPATH, _Q_XPATH):
                try:
                    t = (el.text or "").strip()
                    if t and el.is_displayed() and len(t) > 1:
                        qtexts.append(t)
                except Exception:
                    continue
        except Exception:
            pass

        # Settle tracking: a newly arrived question resets the Save quiet timer so the
        # bot never saves 1s before the next question streams in (the Izmo .Net Core case).
        try:
            _qsig = tuple(qtexts)
            if _qsig != _prev_qsig:
                _prev_qsig = _qsig
                _last_qchange = time.time()
        except Exception:
            pass

        # ---- A0. quick-reply chips & suggestion pills (e.g. [ Bengaluru ], [ Yes ], [ 30 Days ])
        # Also covers BARE leaf pills (div/span/li, no chip class — e.g. consent [Yes]).
        # Leaf-only + short-text + exclusion list keeps containers and bubbles out; only
        # the LLM/boolean-matched single is ever clicked.
        try:
            chip_els = _displayed(drawer.find_elements(
                By.CSS_SELECTOR,
                "div[class*='msg'] button, div[class*='chat'] button, "
                "div[class*='botmsg'] button, div[class*='bot-msg'] button, "
                "div[class*='content'] button, div[class*='options'] button, "
                "div[class*='quick-reply'] button, "
                "div[class*='chip'], span[class*='chip'], button[class*='chip'], "
                "div[class*='pill'], span[class*='pill'], button[class*='pill'], "
                "ul[class*='chips'] li, [class*='option-bubble'], button, "
                "div[class*='msg'] div, div[class*='msg'] span, div[class*='msg'] li, "
                "div[class*='options'] span, div[class*='quick-reply'] span, "
                "div[class*='botmsg'] div, div[class*='bot-msg'] span"
            ))
            valid_chips = []
            try:
                _given = set(getattr(self, "_chatbot_given_answers", []) or [])
            except Exception:
                _given = set()
            for ch in chip_els:
                try:
                    txt = (ch.text or "").strip()
                except Exception:
                    continue
                # Leaf only: reject multi-line containers (e.g. "Upload Resume\nI'll do it later")
                if not txt or "\n" in txt or len(txt) > 40:
                    continue
                if any(k in txt.lower() for k in ("save", "apply", "cancel", "close", "skip", "send", "submit", "type message")):
                    continue
                # Never treat our OWN previous answer bubbles as clickable options
                # (e.g. stale "3 years" bubble clicked for a relocate question).
                if txt.strip().lower() in _given:
                    continue
                try:
                    if ch.find_elements(By.XPATH, "./*"):
                        continue  # container, not a leaf pill
                except Exception:
                    pass
                try:
                    _uanc = ch.find_elements(
                        By.XPATH,
                        "./ancestor::*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'user') "
                        "or contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'outgoing') "
                        "or contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'sender')][1]")
                    if _uanc:
                        continue  # outgoing/user bubble, not an option
                except Exception:
                    pass
                valid_chips.append((ch, txt))

            resume_path = RESUME_UPLOAD_FILE
            q_recent = (qtexts[-1] if qtexts else "").lower()

            # Dedicated Resume Upload & Existing Resume Attachment (hidden input aware, never types filename)
            if "resume" in q_recent or "upload" in q_recent or "cv" in q_recent or any("resume" in txt.lower() for _, txt in valid_chips):
                try:
                    _nfi = len(drawer.find_elements(By.CSS_SELECTOR, "input[type='file']"))
                    logger.info(f"📄 Resume branch: chips={[t for _, t in valid_chips][:10]} file_inputs={_nfi} q='{q_recent[:60]}'")
                except Exception:
                    pass
                existing_chip = None
                for ch, txt in valid_chips:
                    if any(k in txt.lower() for k in ("existing", "profile", "saved", "attached", "keep", "current", "continue")):
                        existing_chip = (ch, txt)
                        break
                if existing_chip:
                    logger.info(f"Chatbot chip clicked (using existing resume): '{existing_chip[1]}'")
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", existing_chip[0])
                    try:
                        existing_chip[0].click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", existing_chip[0])
                    progressed = True
                    time.sleep(1.5)
                    _log_telemetry("quick_reply_chip", "Please upload your resume.", existing_chip[1], True)
                elif os.path.exists(resume_path):
                    # Human order: click 'Upload Resume' FIRST to expose/enable the upload UI,
                    # then attach to the live file input and verify files.length == 1.
                    upload_chip = None
                    for ch, txt in valid_chips:
                        if "upload" in txt.lower() and "resume" in txt.lower():
                            upload_chip = (ch, txt)
                            break
                    if upload_chip and not getattr(self, "_resume_attached", False):
                        logger.info(f"Clicking '{upload_chip[1]}' chip first to expose resume upload UI...")
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", upload_chip[0])
                        try:
                            upload_chip[0].click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", upload_chip[0])
                        time.sleep(1.5)
                        try:
                            fresh = _find_chat_drawer(driver)
                            if fresh is not None:
                                drawer = fresh
                        except Exception:
                            pass
                        progressed = True

                    def _try_attach_resume():
                        attached = False
                        try:
                            file_inputs = drawer.find_elements(By.CSS_SELECTOR, "input[type='file']")
                        except StaleElementReferenceException:
                            return False
                        except Exception:
                            file_inputs = []
                        for fi in file_inputs:
                            try:
                                acc = fi.get_attribute("accept") or ""
                                dis = fi.get_attribute("disabled")
                                logger.info(f"🔍 file input: accept='{acc}' disabled={dis} displayed={fi.is_displayed()}")
                            except Exception:
                                pass
                            try:
                                driver.execute_script("arguments[0].style.display='block';arguments[0].style.visibility='visible';arguments[0].style.opacity='1';arguments[0].removeAttribute('hidden');arguments[0].removeAttribute('disabled');", fi)
                            except Exception:
                                pass
                            try:
                                fi.send_keys(resume_path)
                                try:
                                    nfiles = driver.execute_script("return (arguments[0].files ? arguments[0].files.length : -1);", fi)
                                except Exception:
                                    nfiles = -1
                                try:
                                    fname = driver.execute_script("return (arguments[0].files && arguments[0].files.length ? arguments[0].files[0].name : '');", fi)
                                except Exception:
                                    fname = ""
                                logger.info(f"📄 Resume file attached via input[type=file]: {resume_path} (verified files={nfiles} name='{fname}')")
                                if nfiles == 1:
                                    attached = True
                                    break
                                logger.warning(f"⚠️ send_keys accepted but files.length={nfiles} — trying next file input (if any)")
                            except Exception as e:
                                logger.warning(f"⚠️ File upload threw ({type(e).__name__}): {str(e)[:120]}")
                                continue
                        return attached
                    uploaded_file = _try_attach_resume()
                    if uploaded_file:
                        progressed = True
                        self._resume_attached = True
                        time.sleep(2.5)
                        _log_telemetry("file_upload", "Please upload your resume.", os.path.basename(resume_path), True)
                    if not uploaded_file:
                        # Upload chip was already clicked above; drawer may have re-rendered — re-resolve and retry once
                        try:
                            fresh = _find_chat_drawer(driver)
                            if fresh is not None:
                                drawer = fresh
                        except Exception:
                            pass
                        time.sleep(1.0)
                        if _try_attach_resume():
                            uploaded_file = True
                            progressed = True
                            self._resume_attached = True
                            time.sleep(2.5)
                            _log_telemetry("file_upload", "Please upload your resume.", os.path.basename(resume_path), True)
                    if not uploaded_file:
                        logger.warning("⚠️ Resume file input not accepting file (files.length=0) — will NOT type filename into chat box")
                        try:
                            diag = driver.execute_script("""
                                var d = arguments[0];
                                var btns = [];
                                d.querySelectorAll('button').forEach(function(b){
                                    var t = (b.innerText || '').trim().replace(/\\s+/g,' ');
                                    if (t) btns.push(t.slice(0,60));
                                });
                                var inputs = [];
                                document.querySelectorAll('input[type=file]').forEach(function(f, i){
                                    inputs.push('#'+i+' acc='+(f.accept||'')+' dis='+f.disabled+' vis='+(f.offsetParent!==null)+' files='+(f.files?f.files.length:-1));
                                });
                                var rtxt = [];
                                d.querySelectorAll('*').forEach(function(el){
                                    if (el.children.length===0) {
                                        var t=(el.innerText||'').trim();
                                        if (t && /resume|upload|attach|biodata|cv/i.test(t) && t.length<120) rtxt.push(t.slice(0,100));
                                    }
                                });
                                return 'BTN:['+btns.slice(0,12).join('|')+'] FILE:'+inputs.join(';')+' RTXT:['+[...new Set(rtxt)].slice(0,6).join('|')+']';
                            """, drawer)
                            logger.warning(f"🔬 Upload-area diagnostic: {diag}")
                        except Exception as e:
                            logger.debug(f"Upload diagnostic failed: {e}")

            if valid_chips:
                q = qtexts[-1] if qtexts else ""
                ql = q.lower()
                # Resume already attached via file input — NEVER click any chip for a resume question
                # (both "Upload Resume" and "I'll do it later" would undo the attachment → rejection)
                if getattr(self, "_resume_attached", False) and any(
                        k in ql for k in ("resume", "upload your", "attach", "cv", "biodata")):
                    logger.info("⏭️ Resume already attached — skipping all chips for resume question")
                    progressed = True
                    time.sleep(0.5)
                    continue
                if getattr(self, "_resume_attached", False):
                    valid_chips = [(ch, txt) for ch, txt in valid_chips
                                   if not any(k in txt.lower() for k in ("resume", "upload", "attach", "cv", "later", "skip"))]
                chip_texts = [txt for _, txt in valid_chips]
                matched_chip = None

                # Location matching
                if any(w in ql for w in LOCATION_WORDS):
                    matched_chip = _match_option(chip_texts, [v.lower() for v in loc_variants])

                # Boolean Yes/No matching
                if not matched_chip and any(t.lower() in ("yes", "no") for t in chip_texts):
                    if any(neg in ql for neg in ("criminal", "convicted", "fired", "terminated", "felony", "visa sponsorship")):
                        matched_chip = _match_option(chip_texts, ["no"])
                    else:
                        matched_chip = _match_option(chip_texts, ["yes"])

                if not matched_chip:
                    matched_chip = _llm_answer_question(q, options=chip_texts, config=self.config)
                    if matched_chip not in chip_texts:
                        matched_chip = _match_option(chip_texts, [matched_chip.lower()] if matched_chip else [])

                if matched_chip:
                    for ch, txt in valid_chips:
                        if txt == matched_chip:
                            logger.info(f"Chatbot chip clicked: '{q[:80]}' -> '{txt}'")
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ch)
                            try:
                                ch.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", ch)
                            progressed = True
                            time.sleep(1.0)
                            _log_telemetry("quick_reply_chip", q, txt, True)
                            try:
                                self._chatbot_answered_qs.append(_qkey(q))
                                self._chatbot_given_answers.append(txt.strip().lower())
                            except Exception:
                                pass
                            break
        except Exception as e:
            logger.debug(f"chip handling: {e}")

        # ---- A. free-text chat boxes & inputs (drawer-scoped). Broad on purpose:
        # Naukri swaps composer markup between questions (contenteditable div with varying
        # classes, or inputs of varying type). Missing it means boxes=0 with visible
        # questions — the Q2-never-typed failure.
        try:
            boxes = _displayed(drawer.find_elements(
                By.CSS_SELECTOR, "[contenteditable='true'], [contenteditable='']"))
            for el in _displayed(drawer.find_elements(
                By.CSS_SELECTOR,
                "textarea, input:not([type]), input[type='text'], input[type='search'], "
                "input[type='number'], input[type='tel'], input[type='email'], input[type='url'], "
                "div[role='textbox'], [role='textbox']")):
                if el not in boxes:
                    boxes.append(el)
        except StaleElementReferenceException:
            drawer = _find_chat_drawer(driver)
            if drawer is None:
                break
            boxes = []
        except Exception:
            boxes = []
        for box in boxes:
            try:
                is_input_tag = (box.tag_name or "").lower() in ("input", "textarea")
                current_val = _box_current_value(box)
                if current_val:
                    continue  # already filled
                q = qtexts[-1] if qtexts else ""
                if not q:
                    try:
                        bid = box.get_attribute("id")
                        if bid:
                            labs = drawer.find_elements(By.XPATH, f".//label[@for='{bid}']")
                            if labs and labs[0].text:
                                q = labs[0].text.strip()
                    except Exception:
                        pass
                if not q:
                    try:
                        anc = box.find_element(By.XPATH, "./ancestor::div[contains(@class,'field') or contains(@class,'form') or contains(@class,'botmsg') or contains(@class,'question')][1]")
                        q = (anc.text or "").strip()
                    except Exception:
                        pass

                # NEVER type resume filename into chat box — file must go via input[type=file] only
                ql_box = (q or "").lower()
                if any(k in ql_box for k in ("resume", "upload your", "attach", "cv", ".pdf")):
                    logger.info(f"⏭️ Skipping free-text typing for resume/upload question (handled by file input): '{q[:60]}'")
                    continue

                ans = self._unattended_text_answer(q)
                # Safety: never send a bare filename as a chat answer
                if ans and (ans.strip().lower().endswith(".pdf")
                            or any(name in ans.lower() for name in _RESUME_NAMES)):
                    logger.warning(f"⚠️ Blocking filename-as-answer '{ans[:50]}' — resume must attach via file input, not chat text")
                    continue
                if not ans:
                    if _is_mandatory(q, box):
                        self._chatbot_ok = False
                        self._chatbot_mandatory_unanswered.append(q or "text input")
                        logger.warning(f"⚠️ Mandatory question unanswered: '{q[:90]}' -> Discarding job to prevent incomplete application")
                        _close_chatbot_drawer(driver)
                        return False
                    if q and q not in self._chatbot_unanswered:
                        self._chatbot_unanswered.append(q)
                    continue

                logger.info(f"Chatbot Q: '{q[:90]}' -> A: '{ans[:60]}'")
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
                except Exception:
                    pass
                try:
                    box.click()
                except Exception:
                    pass

                if is_input_tag:
                    try:
                        box.clear()
                    except Exception:
                        pass
                    try:
                        box.send_keys(ans)
                        driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true})); arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", box)
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles:true})); arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", box, ans)
                        except Exception:
                            continue
                else:
                    try:
                        driver.execute_script(
                            "var el=arguments[0],v=arguments[1];el.focus();"
                            "document.execCommand('selectAll',false,null);"
                            "document.execCommand('insertText',false,v);"
                            "el.dispatchEvent(new Event('input',{bubbles:true}));",
                            box, ans)
                    except Exception:
                        try:
                            box.send_keys(ans)
                        except Exception:
                            continue
                time.sleep(0.5)
                # Robust per-answer send: parent/sibling icon click + send selectors + JS keydown/keypress/keyup + Selenium RETURN
                sent = False

                # 1. Immediate parent/sibling send button or SVG search
                try:
                    js_clicked = driver.execute_script("""
                        var box = arguments[0];
                        var p = box.parentElement;
                        for (var i = 0; i < 4 && p; i++) {
                            var btns = p.querySelectorAll("button, [role='button'], .send, [class*='send'], svg, i, [data-automation*='send']");
                            for (var b of btns) {
                                if (b !== box && b.offsetParent !== null) {
                                    var t = (b.innerText || '').toLowerCase();
                                    if (!t.includes('save') && !t.includes('cancel')) {
                                        b.click();
                                        return true;
                                    }
                                }
                            }
                            p = p.parentElement;
                        }
                        return false;
                    """, box)
                    if js_clicked:
                        sent = True
                except Exception:
                    pass

                if not sent:
                    send_selectors = (
                        "//button[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send')]",
                        "//span[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send')]",
                        "//div[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send')]",
                        "//*[contains(@class,'sendMsg') or contains(@class,'send-btn') or contains(@class,'SendMessage')]",
                        "//*[@aria-label='Send' or @aria-label='send']",
                        "//*[@data-automation='sendMessage' or @data-automation='sendBtn']"
                    )
                    for sxp in send_selectors:
                        try:
                            btns = _displayed(drawer.find_elements(By.XPATH, "." + sxp))
                            for b in btns:
                                btxt = (b.text or "").strip().lower()
                                if "save" in btxt:
                                    continue
                                try:
                                    b.click()
                                    sent = True
                                    break
                                except Exception:
                                    driver.execute_script("arguments[0].click();", b)
                                    sent = True
                                    break
                            if sent:
                                break
                        except Exception:
                            continue

                # Dispatch synthetic JS KeyboardEvents for Enter (keydown, keypress, keyup) with keyCode 13
                try:
                    driver.execute_script("""
                        var el = arguments[0];
                        var opts = {key:'Enter', code:'Enter', keyCode:13, which:13, charCode:13, bubbles:true, cancelable:true};
                        el.dispatchEvent(new KeyboardEvent('keydown', opts));
                        el.dispatchEvent(new KeyboardEvent('keypress', opts));
                        el.dispatchEvent(new KeyboardEvent('keyup', opts));
                    """, box)
                except Exception:
                    pass

                # Selenium send_keys Return fallback
                try:
                    box.send_keys(Keys.RETURN)
                except Exception:
                    pass

                # Verify bubble or cleared input (placeholder-as-text counts as cleared)
                time.sleep(1.2)
                try:
                    if not _box_current_value(box):
                        sent = True
                except Exception:
                    pass

                _log_telemetry("text_input", q, ans, sent)
                if sent:
                    try:
                        self._chatbot_answered_qs.append(_qkey(q))
                        self._chatbot_given_answers.append((ans or "").strip().lower())
                    except Exception:
                        pass
                progressed = True
                time.sleep(1.0)
            except StaleElementReferenceException:
                progressed = True
                continue
            except Exception as e:
                logger.debug(f"chatbox handling: {e}")
                continue

        # ---- B. radio groups (grouped by name, fallback by container, drawer-scoped)
        # NOTE: custom-styled radios hide the native input (display:none) — search WITHOUT
        # the display filter so hidden inputs are found; clicking goes through the visible
        # label/pill or JS click (see _click_radio_option).
        try:
            _seen_ids = set()

            def _collect(inps):
                out = []
                for _r in inps:
                    try:
                        _kk = (_r.get_attribute("id") or _r.get_attribute("name") or "",
                               _r.get_attribute("value") or "")
                        if _kk in _seen_ids:
                            continue
                        _seen_ids.add(_kk)
                        out.append(_r)
                    except StaleElementReferenceException:
                        continue
                    except Exception:
                        continue
                return out

            radios = _collect(drawer.find_elements(By.CSS_SELECTOR, "input.ssrc__radio"))
            radios += _collect(drawer.find_elements(By.CSS_SELECTOR, "input[type='radio']"))
        except StaleElementReferenceException:
            drawer = _find_chat_drawer(driver)
            if drawer is None:
                break
            radios = []
        except Exception:
            radios = []
        groups = {}
        for r in radios:
            try:
                key = r.get_attribute("name") or ""
                if not key:
                    try:
                        anc = r.find_element(By.XPATH, "./ancestor::div[@id][1]")
                        key = anc.get_attribute("id") or ""
                    except Exception:
                        key = ""
                groups.setdefault(key or f"anon-{len(groups)}", []).append(r)
            except StaleElementReferenceException:
                progressed = True
            except Exception:
                continue
        loc_variants = _location_variants(self.config)
        for key, grp in groups.items():
            try:
                if any(r.is_selected() for r in grp):
                    continue
            except StaleElementReferenceException:
                progressed = True
                continue
            except Exception:
                continue
            labels = []
            label_els = []
            for r in grp:
                try:
                    rid = r.get_attribute("id") or ""
                    lab = None
                    lab_txt = ""
                    if rid:
                        try:
                            lab = drawer.find_element(By.XPATH, f".//label[@for='{rid}']")
                            lab_txt = ((lab.text if lab is not None else "") or "").strip()
                        except NoSuchElementException:
                            lab = None
                    if not lab_txt:
                        lab_txt = (r.get_attribute("value") or "").strip()
                    if not lab_txt:
                        # Custom pill radios: option text lives in a visible ancestor container
                        try:
                            pill = r.find_element(
                                By.XPATH,
                                "./ancestor::*[self::label or self::div or self::span]"
                                "[normalize-space(string(.))!=''][1]")
                            lab_txt = " ".join((pill.text or "").split())[:60]
                            if pill is not None and lab is None:
                                try:
                                    if pill.is_displayed():
                                        lab = pill
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    labels.append(lab_txt)
                    label_els.append(lab)
                except Exception:
                    labels.append("")
                    label_els.append(None)
            # question text: ALWAYS prefer the group's own container text (qtexts[-1] is
            # often a stale/different question — e.g. notice-period options paired with an
            # Azure-experience question — which makes the LLM pick a nonsense option).
            q = ""
            try:
                cont = grp[0].find_element(
                    By.XPATH, "./ancestor::div[count(.//input[@type='radio'])>0][1]")
                ctxt = (cont.text or "").strip()
                for lb in labels:
                    if lb:
                        ctxt = ctxt.replace(lb, " ")
                ctxt = " ".join(ctxt.split())
                if ctxt:
                    q = ctxt[:300]
            except Exception:
                q = ""
            if not q:
                q = qtexts[-1] if qtexts else ""
            if _q_opts_mismatch(q, labels):
                logger.warning(f"⚠️ Question/options mismatch (stale q): '{q[:70]}' vs {labels[:4]} — discarding job, never misrepresenting")
                self._chatbot_ok = False
                self._chatbot_mandatory_unanswered.append(q or f"radio:{key}")
                _close_chatbot_drawer(driver)
                return False
            decision, payload = classify_radio(q, labels, loc_variants)
            if decision == "select":
                # 2026-10-14: the exact option first -- a substring match can pick "Not applicable" for "No".
                target = payload if payload in labels else _match_option(labels, [payload.lower()])
                idx = labels.index(target) if target in labels else -1
                if idx < 0:
                    if _is_mandatory(q, grp[0]):
                        self._chatbot_ok = False
                        self._chatbot_mandatory_unanswered.append(q or f"radio:{key}")
                        logger.warning(f"⚠️ Mandatory question unanswered: '{q[:80]}' -> Discarding job to prevent incomplete application")
                        _close_chatbot_drawer(driver)
                        return False
                    self._chatbot_unanswered.append(q or f"radio:{key}")
                    continue
                logger.info(f"Chatbot radio: '{q[:80]}' -> '{labels[idx]}' (safe default)")
                try:
                    el = grp[idx]
                    _lab = label_els[idx] if idx < len(label_els) else None
                    if _click_radio_option(driver, el, _lab):
                        progressed = True
                        time.sleep(1.0)
                        _log_telemetry("radio_button", q, labels[idx], True)
                        try:
                            self._chatbot_answered_qs.append(_qkey(q))
                            self._chatbot_given_answers.append((labels[idx] or "").strip().lower())
                        except Exception:
                            pass
                    else:
                        logger.debug("radio click failed on all channels")
                        continue
                except Exception as e:
                    logger.debug(f"radio click: {e}")
                    continue
            else:
                pick = None
                if decision != "discard":
                    pick = _llm_answer_question(q, options=labels, config=self.config)
                if pick and pick in labels:
                    idx = labels.index(pick)
                    logger.info(f"Chatbot radio: '{q[:80]}' -> '{pick}' (LLM)")
                    try:
                        el = grp[idx]
                        _lab = label_els[idx] if idx < len(label_els) else None
                        if _click_radio_option(driver, el, _lab):
                            progressed = True
                            time.sleep(1.0)
                            _log_telemetry("radio_button", q, pick, True)
                            try:
                                self._chatbot_answered_qs.append(_qkey(q))
                                self._chatbot_given_answers.append((pick or "").strip().lower())
                            except Exception:
                                pass
                        else:
                            raise Exception("all radio click channels failed")
                    except Exception:
                        if _is_mandatory(q, grp[0]):
                            self._chatbot_ok = False
                            self._chatbot_mandatory_unanswered.append(q or f"radio:{key}")
                            logger.warning(f"⚠️ Mandatory question unanswered: '{q[:80]}' -> Discarding job to prevent incomplete application")
                            _close_chatbot_drawer(driver)
                            return False
                        self._chatbot_unanswered.append(q or f"radio:{key}")
                else:
                    if _is_mandatory(q, grp[0]):
                        self._chatbot_ok = False
                        self._chatbot_mandatory_unanswered.append(q or f"radio:{key}")
                        logger.warning(f"⚠️ Mandatory question unanswered: '{q[:80]}' -> Discarding job to prevent incomplete application")
                        _close_chatbot_drawer(driver)
                        return False
                    logger.info(f"Chatbot radio UNANSWERED/DISCARDED: '{q[:80]}'")
                    if q and q not in self._chatbot_unanswered:
                        self._chatbot_unanswered.append(q)

        # ---- C. native <select> dropdowns (drawer-scoped)
        try:
            selects = _displayed(drawer.find_elements(By.TAG_NAME, "select"))
        except StaleElementReferenceException:
            drawer = _find_chat_drawer(driver)
            if drawer is None:
                break
            selects = []
        except Exception:
            selects = []
        for sel_el in selects:
            try:
                from selenium.webdriver.support.ui import Select
                sel = Select(sel_el)
                cur = sel.first_selected_option.text.strip()
                if cur and cur.lower() not in ("select an option", "select", "--select--", ""):
                    continue
                opts = [o.text.strip() for o in sel.options if o.text.strip()]
                # Container-first question (same stale-qtexts hazard as radios)
                q = ""
                try:
                    _scont = sel_el.find_element(
                        By.XPATH, "./ancestor::div[count(.//select)>0][1]")
                    _sctxt = " ".join(((_scont.text or "").strip()).split())
                    for _o in opts:
                        if _o:
                            _sctxt = _sctxt.replace(_o, " ")
                    _sctxt = " ".join(_sctxt.split())
                    if _sctxt:
                        q = _sctxt[:300]
                except Exception:
                    q = ""
                if not q:
                    q = qtexts[-1] if qtexts else ""
                if _q_opts_mismatch(q, opts):
                    logger.warning(f"⚠️ Question/options mismatch (stale q): '{q[:70]}' vs {opts[:4]} — discarding job, never misrepresenting")
                    self._chatbot_ok = False
                    self._chatbot_mandatory_unanswered.append(q or "select")
                    _close_chatbot_drawer(driver)
                    return False
                target = None
                if any(w in q.lower() for w in LOCATION_WORDS):
                    target = _match_option(opts, [v.lower() for v in loc_variants])
                if target is None:
                    pick = _llm_answer_question(q, options=opts, config=self.config)
                    if pick and pick in opts:
                        target = pick
                if target:
                    logger.info(f"Chatbot select: '{q[:80]}' -> '{target}'")
                    sel.select_by_visible_text(target)
                    progressed = True
                    time.sleep(1.0)
                    _log_telemetry("dropdown_select", q, target, True)
                    try:
                        self._chatbot_answered_qs.append(_qkey(q))
                        self._chatbot_given_answers.append((target or "").strip().lower())
                    except Exception:
                        pass
                else:
                    if _is_mandatory(q, sel_el):
                        self._chatbot_ok = False
                        self._chatbot_mandatory_unanswered.append(q or "select")
                        logger.warning(f"⚠️ Mandatory question unanswered: '{q[:80]}' -> Discarding job to prevent incomplete application")
                        _close_chatbot_drawer(driver)
                        return False
                    if q and q not in self._chatbot_unanswered:
                        self._chatbot_unanswered.append(q)
            except Exception as e:
                logger.debug(f"select handling: {e}")
                continue

        # ---- D. gate on Save state (drawer-scoped — page Apply buttons unreachable)
        try:
            save = _find_save_button(drawer)
        except StaleElementReferenceException:
            drawer = _find_chat_drawer(driver)
            if drawer is None:
                break
            save = None
        try:
            save_enabled = bool(
                save and save.is_displayed() and save.is_enabled()
                and "disabled" not in (save.get_attribute("class") or "").lower()
                and save.get_attribute("disabled") is None
                and str(save.get_attribute("aria-disabled") or "").lower() != "true"
            )
        except StaleElementReferenceException:
            save_enabled = False
            progressed = True
        except Exception:
            save_enabled = False

        if save is not None and not save_enabled:
            # Throttled visibility: log Save presence/state every ~15s while waiting
            try:
                if time.time() - getattr(self, "_last_save_state_log", 0) > 15:
                    self._last_save_state_log = time.time()
                    _st = ""
                    try:
                        _st = (save.get_attribute("class") or "")[:60]
                    except Exception:
                        pass
                    logger.info(f"⏳ Save present but disabled (class='{_st}') — waiting for answers to register...")
            except Exception:
                pass

        try:
            if time.time() - getattr(self, "_last_pass_log", 0) > 12:
                self._last_pass_log = time.time()
                _nq = len(qtexts)
                _nb = len(boxes) if "boxes" in dir() else -1
                _nr = len(radios) if "radios" in dir() else -1
                _ns = len(selects) if "selects" in dir() else -1
                logger.info(f"🔎 pass: qtexts={_nq} boxes={_nb} radios={_nr} selects={_ns} save={'yes' if save else 'no'}/{'enabled' if save_enabled else 'disabled'}")
        except Exception:
            pass

        if save_enabled:
            # Enforce stabilization: do not click Save if an action was taken less than 3.5s ago
            if (time.time() - last_progress < 3.5):
                time.sleep(1.0)
                continue
            # Settle rule: require quiet drawer (no new questions for 8s AND no action for
            # 10s). Multi-question drawers stream Q2 right after Q1 is answered.
            if (time.time() - last_progress < 10) or (time.time() - _last_qchange < 8):
                time.sleep(1.0)
                continue
            # Unanswered-question block: a visible '?' never committed must gate Save,
            # even if the input box momentarily looks settled (the Izmo Q2 race).
            try:
                _answered = set(getattr(self, "_chatbot_answered_qs", []) or [])
                _open_q = [t for t in qtexts if "?" in t and _qkey(t) not in _answered]
            except Exception:
                _open_q = []
            if _open_q:
                logger.info(f"⏳ Open question(s) awaiting answers ({len(_open_q)}) — holding Save: '{_open_q[0][:70]}'")
                time.sleep(1.5)
                continue

            # Prevent premature save if there are empty interactive input boxes still awaiting input
            unanswered_boxes = []
            try:
                for bx in _displayed(drawer.find_elements(By.CSS_SELECTOR, "[contenteditable='true'], [contenteditable=''], textarea, input:not([type]), input[type='text'], input[type='search'], input[type='number'], input[type='tel'], input[type='email'], input[type='url'], div[role='textbox'], [role='textbox']")):
                    if not _box_current_value(bx):
                        unanswered_boxes.append(bx)
            except Exception:
                pass
            if unanswered_boxes and (time.time() - last_progress < 8):
                time.sleep(1.0)
                continue

            # Prevent premature save if there are still question chips or option bubbles on screen
            if valid_chips and (time.time() - last_progress < 8):
                time.sleep(1.0)
                continue

            # Check for uncommitted text in boxes before allowing Save
            uncommitted = False
            try:
                for bx in _displayed(drawer.find_elements(By.CSS_SELECTOR, "[contenteditable='true'], [contenteditable=''], textarea, input:not([type]), input[type='text'], input[type='search'], input[type='number'], input[type='tel'], input[type='email'], input[type='url'], div[role='textbox'], [role='textbox']")):
                    val = _box_current_value(bx)
                    if val:
                        logger.warning(f"⚠️ Box has uncommitted text '{val[:40]}', attempting send before Save...")
                        try:
                            bx.send_keys(Keys.RETURN)
                            time.sleep(1.0)
                        except Exception:
                            pass
                        val_after = (bx.get_attribute("value") or "").strip() if (bx.tag_name or "").lower() in ("input", "textarea") else (bx.text or "").strip()
                        if val_after and val_after.lower() != "type message here...":
                            uncommitted = True
            except Exception:
                pass

            if uncommitted:
                logger.warning("⚠️ Mandatory text answer remains uncommitted in box -> Discarding job to prevent incomplete application")
                self._chatbot_ok = False
                _close_chatbot_drawer(driver)
                return False

            if self._chatbot_mandatory_unanswered:
                logger.warning("⚠️ Save is enabled but mandatory questions remain unanswered -> Discarding")
                self._chatbot_ok = False
                _close_chatbot_drawer(driver)
                return False

            logger.info("✅ Chatbot questions answered — Clicking Save now...")
            try:
                _stag = (save.tag_name or "")
                _scls = (save.get_attribute("class") or "")[:80]
                _saria = save.get_attribute("aria-disabled")
                _sdis = save.get_attribute("disabled")
                _stxt = ((save.text or "") or "")[:30]
                logger.info(f"🎯 Save target: <{_stag}> text='{_stxt}' class='{_scls}' aria-disabled={_saria} disabled={_sdis}")
            except Exception:
                pass
            try:
                _qs = driver.execute_script("return (document.body ? document.body.innerText : '').slice(0, 1200);")
                logger.info(f"🧾 Drawer state before Save: '{(_qs or '').replace(chr(10), ' | ')[:600]}'")
            except Exception:
                pass
            try:
                driver.save_screenshot(os.path.join(_PROFILE_DIR, "debug_chatbot_before_save.png"))
                logger.info("📸 Saved pre-save drawer screenshot to debug_chatbot_before_save.png")
            except Exception as e:
                logger.debug(f"Pre-save screenshot skipped: {e}")
            # If Save matched a container, drill to the real inner control first
            _save_target = save
            try:
                for _cand in save.find_elements(By.XPATH, ".//button | .//input[@type='button' or @type='submit'] | .//*[@role='button']"):
                    try:
                        _ct = ((_cand.text or "") + " " + (_cand.get_attribute("value") or "")).strip().lower()
                        _cc = (_cand.get_attribute("class") or "").lower()
                        if _cand.is_displayed() and ("save" in _ct or "submit" in _ct or "save" in _cc or "submit" in _cc):
                            _save_target = _cand
                            try:
                                logger.info(f"🎯 Save inner control: <{(_cand.tag_name or '')}> text='{_ct[:30]}'")
                            except Exception:
                                pass
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", _save_target)
            except Exception:
                pass
            time.sleep(0.3)
            _save_clicked = False
            for _click_try in range(3):
                try:
                    _save_target.click()
                    _save_clicked = True
                    break
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", _save_target)
                        _save_clicked = True
                        break
                    except Exception as e:
                        logger.debug(f"Save click try {_click_try + 1} failed: {e}")
                        time.sleep(1.0)
            if not _save_clicked:
                # Last resort: coordinate click (container divs sometimes ignore element clicks)
                try:
                    from selenium.webdriver.common.action_chains import ActionChains as _AC
                    _AC(driver).move_to_element(_save_target).click().perform()
                    _save_clicked = True
                    logger.info("🖱️ Save clicked via ActionChains coordinates")
                except Exception as e:
                    logger.error(f"❌ Save click failed on all channels: {e}")
                    _close_chatbot_drawer(driver)
                    return False
            time.sleep(2.0)

            rej = _check_rejection_messages(driver)
            if rej:
                logger.error(f"❌ Application rejected due to incomplete information: '{rej}'")
                _log_telemetry("rejection", "application_rejected", rej[:100], False)
                _close_chatbot_drawer(driver)
                self._chatbot_ok = False
                return False
            # Drawer-state check: if the drawer is still open with identical questions,
            # the click landed nowhere — retry once via coordinates before giving up.
            try:
                _still_open = _find_chat_drawer(driver) is not None
            except Exception:
                _still_open = False
            if _still_open and not _is_applied_on_page(driver):
                logger.info("↩️ Drawer still open after Save click — retrying via ActionChains")
                try:
                    from selenium.webdriver.common.action_chains import ActionChains as _AC2
                    _AC2(driver).move_to_element(_save_target).click().perform()
                    time.sleep(3.0)
                except Exception as e:
                    logger.debug(f"Save retry click failed: {e}")

            # STRICT confirmation: a click without rejection is NOT proof (page-level Save
            # favorite clicks also produce no rejection). Require applied-state evidence,
            # checked twice (success toasts can render a few seconds late).
            confirmed = False
            for _attempt in range(2):
                try:
                    if _is_applied_on_page(driver):
                        confirmed = True
                        break
                except Exception:
                    pass
                # H3 (2026-09-15): the old fallback here matched 'apply confirmation' /
                # 'applied' anywhere in the URL or body — sampled live, that title/text
                # appears on BOTH a real 200 success AND a 202 "redirected to the company
                # website" page, so it could confirm an application that never happened.
                # `_applied_evidence` reads Naukri's own verdict (multiApplyResp code /
                # 'Applied to "') instead of guessing from ambiguous text.
                try:
                    if _applied_evidence(driver) == "applied":
                        confirmed = True
                        break
                except Exception:
                    pass
                if not confirmed and _attempt == 0:
                    time.sleep(3.0)
            if not confirmed:
                logger.warning("⚠️ Save clicked, no rejection, but NO applied-state evidence — will NOT count as applied")
                _log_telemetry("rejection", "save_unconfirmed", "no_applied_state", False)
                _close_chatbot_drawer(driver)
                self._chatbot_ok = False
                return False

            logger.info("✅ Chatbot application submitted & confirmed via Save")
            self._chatbot_ok = True
            self._chatbot_unanswered = []
            _log_telemetry("save_submission", "chatbot_drawer", "save_confirmed", True)
            return True
        if progressed:
            last_progress = time.time()
            time.sleep(1.5)
            continue
        if self._chatbot_unanswered and time.time() - last_progress > 12:
            break
        if not save and not boxes and not radios and not selects:
            if time.time() < content_grace_until:
                time.sleep(1.5)
                continue  # drawer content still loading — keep polling, don't discard yet
            break  # genuinely nothing to do
        time.sleep(1.5)

    try:
        _qs = driver.execute_script("return (document.body ? document.body.innerText : '').slice(0, 800);")
        logger.warning(f"🧾 Discard drawer dump: '{(_qs or '').replace(chr(10), ' | ')[:500]}'")
    except Exception:
        pass
    try:
        driver.save_screenshot(os.path.join(_PROFILE_DIR, "debug_drawer_empty.png"))
        logger.info("📸 Saved empty-drawer screenshot to debug_drawer_empty.png")
    except Exception as e:
        logger.debug(f"Empty-drawer screenshot skipped: {e}")
    logger.warning("⚠️ Chatbot finished without confirmed Save submission -> Discarding")
    _log_telemetry("rejection", "chatbot_no_save", "discarded_no_confirmed_save", False)
    self._chatbot_ok = False
    _close_chatbot_drawer(driver)
    return False


# 2026-09-16: a bare prefix test ("can you", "have you"...) used to answer EVERY question
# starting that way with "Yes", including ones that continue into an open-ended ask --
# "Can you describe a Tableau dashboard you built from scratch?" was answered "Yes". These
# markers mean the sentence keeps going past yes/no into a request for specifics.
_OPEN_ENDED_MARKERS = ("describe", "tell me", "explain", "elaborat", "walk me", "walk us",
                       "share", "list ", "what ", "which ", "how ", "why ")

# 2026-09-24 (user's call): identity-document numbers are never shared at application time; the
# answer says so instead of the LLM's "not provided in my candidate profile". "Name as per Aadhar
# card" and "As per Aadhar, First Name..." ask for a name, not a number, so they are excluded.
_ID_DOC_RX = re.compile(r"\b(pan|aadha?ar|passport|bank account|uan)\b")
_ID_ASK_RX = re.compile(r"number|\bnum\b|\bno\.|\bno:|\bno\s*\?|\bno\s*$|card|details")
_ID_DOC_LABEL = {"pan": "PAN", "aadhaar": "Aadhaar", "aadhar": "Aadhaar", "passport": "passport",
                 "bank account": "bank account", "uan": "UAN"}
_DEMOGRAPHIC_TEXT_RX = re.compile(r"\b(gender|marital status|religion|caste)\b")
# Company/employment-history questions are not safe to answer "Yes" by prefix alone ("Have you
# received an offer letter before?", "Have you been employed by X?") -- the LLM answers them
# from the employment history in the profile digest instead.
_HISTORY_MARKERS = ("employ", "worked", "work with", "associated", "offer letter", "offer released",
                    "registered", "previously", "before", "career break", "visa", "sponsor",
                    "citizen", "authoriz")

# 2026-09-24: pay/notice answers come from the values the user confirmed in
# config.personal_info. Before this, "Expected CTC & Offer(if any)" was answered with the CURRENT
# CTC (keyword 'ctc' matched before 'expected'), notice came from a 30-day default, the
# yes/no prefix rule answered "Are you currently serving the notice period?" with "Yes", and the
# LLM guessed serving/not-serving differently per employer.
_PAY_RX = re.compile(r"\bctc\b|salary|compensation|annual package|(fixed|variable)\s*(pay|component|salary|ctc)")
_CUR_PAY_RX = re.compile(r"(current|present|existing|drawn)\W+(annual\W+)?(ctc|salary|package|compensation|fixed)"
                         r"|current\s*(&|and|/)\s*expected")
# 2026-10-08: "ECTC" / "CCTC" abbreviations. "ECTC?" fell through every rule (\bctc\b needs a word boundary
# before the c) and the keyword map answered it with the current CTC on 09-26 and 10-05.
_ECTC_RX = re.compile(r"\be[\s.-]?ctc\b|\bexp(ected)?\.?\s*ctc\b")
_CCTC_RX = re.compile(r"\bc[\s.-]?ctc\b|\bcurr(ent)?\.?\s*ctc\b")
# Offers in hand. User fact (2026-10-08): no offers in hand. 2026-10-14: the 10-08 pattern knew only
# "offers in hand / on hand / in pipeline", "other offers" and "holding offers", so "Do you have offer?
# If yes, please give Offer Amount and Last Working D..." still got the yes/no default "Yes" (account 1
# 09-28; account 2 10-01, 10-05, 10-08), "Any offer? If yes then mention offer amount" went to the LLM
# (it declined, or wrote "Open to discussing current offers during interviews."), and the pay rule
# answered "Any live offer in hand , if yes please mention offered CTC and DOJ." with the current CTC
# (10-04).
_OFFERS_RX = re.compile(
    r"\boffers?\s+(in|on)[\s-]*hand\b"                                       # offer(s) in hand / on hand
    r"|\bholding\s+((any|an?|other)\s+)?(job\s+)?offers?\b|\bhold\s+any\s+(job\s+)?offers?\b"  # holding any offer
    r"|\boffers?\s*(or|and|/|&)\s*pipelines?\b|\bpipelines?\s*(or|and|/|&)\s*offers?\b"   # offer or pipeline
    r"|\boffers?\s+in\s+(the\s+)?pipelines?\b"
    r"|\bdo\s+(you|u)\s+((currently|already)\s+)?(have|hold)\s+"
    r"((any|an?|other|live|active|current|existing|competing|pending|verbal|written)\s+)*(job\s+)?offers?\b"
    r"|\b(have|has|got|there)\s+any\s+((live|active|other|current|existing|job)\s+)*offers?\b"
    r"|^\W*any\s+((live|active|other|current|existing|job)\s+)*offers?\b"   # "Any offer? ..."
    r"|\bin[\s-]*hand\s+(job\s+)?offers?\b"                                 # in-hand offer
    r"|\b(other|live|active)\s+(job\s+)?offers?\b"                          # other / live offers
    r"|\boffers?\s*\(\s*if\s+any\s*\)"                                      # "Offer(if any)"
)
# A follow-up that asks for the details of an offer the candidate may hold: "If you're holding any
# offer, please mention the value of the offer and...", "If yes, please mention the offer amount".
_OFFERS_FOLLOWUP_RX = re.compile(
    r"^\W*(if|in\s+case)\s+(of\s+)?(yes|any|(you|u)\s*(['’]?re|\s+are|\s+r)?\s+(have|hold|holding|got))\b.*\boffer")
# Never an offers-in-hand question, whatever else it says: counter-offer policy, the offer-letter
# history question "Have you received an offer letter before?" (a prior
# application, answered from the employment history), and negated statements such as "I confirm that
# I do not hold any other offers." (a consent line, not a question to answer "No").
_OFFERS_NOT_RX = re.compile(r"\bcounter[\s-]*offers?\b|\boffer\s*letter|\boffer\s+released\b"
                            r"|\b(not|never|no|\w+n['’]t)\s+(\w+\s+){0,3}offers?\b")
# Notice-period / joining-policy words block only a bare conditional ("If you have an offer, can you
# join earlier?"). A direct offers question keeps its "No" whatever follows: logged wordings are cut at
# 70 characters, and many go on "...and notice period" / "...when can you join".
_OFFERS_JOIN_RX = re.compile(r"\bnotice\b|\bjoin\b|\b(immediate|early|earlier)\s+joining\b"
                             r"|\bjoining\s+(time|timeline)\b|\bhow\s+soon\b")
_NO_OPTION_RX = re.compile(r"^\W*(no|none|nil|nope|not\s+(applicable|yet|currently|at\s+(the\s+)?moment)|n/?a)\b")
_NUMERIC_HINT_RX = re.compile(r"numeric|in inr|in rupees|\bin rs\b|only numbers|digits")
_LACS_HINT_RX = re.compile(r"in lacs|in lakhs?|in lpa|lacs per annum|lakhs per annum")
_COMPANY_RX = re.compile(r"company|organi[sz]ation|employer|payroll")


def _pay_notice_answer(ql, pi, location=""):
    """Current/expected CTC, notice period and serving-notice questions from confirmed values.
    None when the question is not about these (or the values are not configured)."""
    cur, exp = pi.get("current_ctc_lpa"), pi.get("expected_ctc_lpa")
    numeric, lacs = bool(_NUMERIC_HINT_RX.search(ql)), bool(_LACS_HINT_RX.search(ql))

    def amt(lpa):
        return str(int(round(lpa * 100000))) if numeric else (f"{lpa:g}" if lacs else f"{lpa:g} LPA")

    parts = []
    yes_no = ql.startswith(("are you", "do you", "will you", "would you", "can you", "is ", "have you"))
    if yes_no and "serving" not in ql:
        return None  # "Are you OK with a 30% hike on current CTC?" wants yes/no, not an amount
    asks_exp = bool(_ECTC_RX.search(ql)) or ("expect" in ql and bool(_PAY_RX.search(ql) or "expectation" in ql))
    if cur and (_CUR_PAY_RX.search(ql) or _CCTC_RX.search(ql) or (_PAY_RX.search(ql) and not asks_exp)):
        if "variable" in ql and "fixed" not in ql:
            parts.append(("Variable pay", "0 (all fixed)"))
        else:
            parts.append(("Current CTC", amt(cur) + ("" if numeric or lacs else " (all fixed)")))
    if exp and asks_exp:
        parts.append(("Expected CTC", amt(exp)))
    if pi.get("notice_period") and "notice" in ql and not ql.startswith(("are you", "currently serving")):
        in_days = re.search(r"in days|number of days|no\.? of days", ql) and pi.get("notice_days")
        parts.append(("Notice", str(pi["notice_days"]) if in_days else pi["notice_period"]))
    if pi.get("serving_notice") is False and re.search(r"serving|last working|\blwd\b|relieving", ql):
        parts.append((None, "not serving notice"))
    if not parts:
        return None
    # 2026-10-14: an offer mentioned beside the candidate's own CTC or notice gets the user's fact (10-08:
    # no offers in hand). Was "no other offers", and only beside the expected CTC.
    if "offer" in ql and not _OFFERS_NOT_RX.search(ql) \
            and any(l in ("Current CTC", "Expected CTC", "Notice") for l, _ in parts):
        parts.append((None, "no offers in hand"))
    if len(parts) == 1:
        label, value = parts[0]
        if label is None:
            return "No, I am not serving notice" if ql.startswith(("are you", "currently serving", "is ")) \
                else "Not serving notice"
        return value
    if "location" in ql and location:
        parts.insert(0, ("Location", location))
    return "; ".join(f"{l}: {v}" if l else v for l, v in parts)


def _offers_answer(ql, pay=None):
    """'No' for a question asking whether the candidate holds a job offer, 'Not applicable' for a
    follow-up asking that offer's amount or date, None for anything else (lowercased `ql`).

    `pay` is _pay_notice_answer's answer to the same question. It stands when the question also asks
    the candidate's own expected/current CTC or notice period, and then already ends with the fact:
    "Expected CTC & Offer(if any)" -> "Expected CTC: <x> LPA; no offers in hand". A bare "CTC" next to
    an offer is the offer's CTC, not a pay question."""
    if _OFFERS_NOT_RX.search(ql):
        return None
    direct = bool(_OFFERS_RX.search(ql))
    followup = bool(_OFFERS_FOLLOWUP_RX.search(ql))
    if not direct and (not followup or _OFFERS_JOIN_RX.search(ql)):
        return None
    if pay and (_ECTC_RX.search(ql) or _CCTC_RX.search(ql) or _CUR_PAY_RX.search(ql) or "expect" in ql
                or re.search(r"\bnotice\b", ql)):
        return None
    return "Not applicable" if followup else "No"


def _unattended_text_answer(self, question):
    """Safe-defaults-first text answering. Returns answer string or None.

    Also stamps self._last_text_answer_source ("config"|"rules"|"llm") so chatdrawer v2's
    answers.py can log which source actually answered a composer/text question -- previously
    every non-empty return here was logged as src=rules even when the LLM (and its fabrication
    risk) produced the text, making a fabricated answer indistinguishable from a safe rule hit.
    """
    self._last_text_answer_source = "rules"
    q = (question or "").strip()
    if not q:
        return None
    ql = q.lower()

    # Date of birth (2026-09-15): single free-text "DD/MM/YYYY" field. A 3-way split
    # day/month/year drawer is not a text box at all and is handled by chatdrawer v2 instead.
    if "date of birth" in ql or re.search(r'\bdob\b', ql):
        dob = str((self.config.get("personal_info", {}) or {}).get("date_of_birth", "")).strip()
        if dob:
            self._last_text_answer_source = "config"
            return dob

    id_doc = _ID_DOC_RX.search(ql)
    if id_doc and _ID_ASK_RX.search(ql) and "name" not in ql:
        return f"I can't provide my {_ID_DOC_LABEL[id_doc.group(1)]} number right now; I'll share it at a later stage."
    if _DEMOGRAPHIC_TEXT_RX.search(ql):
        return "Prefer not to say"
    pay = _pay_notice_answer(ql, self.config.get("personal_info", {}) or {},
                             (self.config.get("job_search", {}) or {}).get("location", ""))
    # 2026-10-08: "Do you have any offers in hand?" got the yes/no default "Yes". The bot's own pay
    # answer has said "no other offers" since 09-24; this keeps the two consistent. 2026-10-14: wider
    # match (_OFFERS_RX), and checked before the pay answer is used ("offered CTC" got the current CTC);
    # the pay answer now ends "no offers in hand".
    offers = _offers_answer(ql, pay)
    if offers:
        self._last_text_answer_source = "config"
        logger.info(f"[offers rule] via=text q='{q[:70]}' a='{offers}'")
        return offers
    if pay:
        self._last_text_answer_source = "config"
        return pay

    # Boolean Yes/No questions should NEVER be answered as "3 years" -- but only when the
    # question really is yes/no; an open-ended continuation must fall through to the (grounded)
    # LLM instead of collapsing to "Yes". 2026-10-14: never for an offer question the offers rule left
    # alone; the LLM's digest states the fact (no offers in hand).
    if any(ql.startswith(p) for p in ("do you", "are you", "have you", "will you", "can you", "would you", "is there", "is your", "should")) \
            and not any(m in ql for m in _OPEN_ENDED_MARKERS) \
            and not any(h in ql for h in _HISTORY_MARKERS) and "offer" not in ql:
        if any(neg in ql for neg in ("criminal", "convicted", "fired", "terminated", "felony", "visa sponsorship")):
            return "No"
        return "Yes"

    # Specific quantitative experience questions
    if any(p in ql for p in ("how many years", "years of experience", "total experience", "number of years")):
        return f"{self.config.get('user_profile', {}).get('experience_years', '3')} years"

    # Company / payroll questions go straight to the LLM (it knows the employment history); the
    # keyword map answered "Current Payroll company? As per Aadhar, First Name..." with just the
    # name, because 'name' matched first. 2026-10-14: offer questions too -- the substring map answers
    # any "...offered CTC..." with the current CTC.
    if not _COMPANY_RX.search(ql) and "offer" not in ql:
        try:
            ans = ChatbotMixin._get_keyword_answer(self, q)
            if ans:
                return ans
        except Exception:
            pass
        hit = QA_DICT.get(ql)
        if hit:
            return hit
    try:
        ans = _llm_answer_question(q, options=None, config=getattr(self, "config", None))
        if ans:
            self._last_text_answer_source = "llm"
            return ans
    except Exception as e:
        logger.debug(f"LLM text answer error: {e}")
    return None


def _unattended_gemini_pick(self, question, options):
    """Ask LLM to choose among radio/select options. Returns option text or None."""
    if not options:
        return None
    try:
        return _llm_answer_question(question, options=options, config=getattr(self, "config", None))
    except Exception as e:
        logger.debug(f"LLM pick error: {e}")
        return None


ChatbotMixin._handle_chatbot = _unattended_handle_chatbot
ChatbotMixin._unattended_text_answer = _unattended_text_answer
ChatbotMixin._unattended_gemini_pick = _unattended_gemini_pick

# ------------------------------------------------- chatdrawer v2 (dedicated chat-window module)
# Wraps the v1 handler just installed above rather than replacing it: v2 only runs when a bot
# instance is opted in (config.bot_behavior.chat_engine == "v2" or env NAUKRI_CHAT_ENGINE=v2),
# so it can be canary-tested against real applications before becoming the default. See
# naukri_bot/chatdrawer/__init__.py for the contract both paths honor.
try:
    from naukri_bot.chatdrawer import install as _install_chatdrawer_v2, Hooks as _ChatV2Hooks

    def _log_chat_v2(record):
        record = dict(record, timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with open(_TELEMETRY_FILE, "a", encoding="utf-8") as tf:
            tf.write(json.dumps(record, ensure_ascii=False) + "\n")

    _install_chatdrawer_v2(ChatbotMixin, _ChatV2Hooks(
        classify_radio=classify_radio,
        llm_answer=_llm_answer_question,
        text_answer=_unattended_text_answer,
        location_variants=_location_variants,
        q_opts_mismatch=_q_opts_mismatch,
        resume_path=RESUME_UPLOAD_FILE,
        telemetry=_log_chat_v2,
    ))
except Exception as e:
    logger.warning(f"chatdrawer v2 install skipped ({e!r}) — v1 chat handler remains active")

# -------------------------------------------- N2 Save-aware submit + N3 titles
_orig_submit_improved = ApplicationMixin._handle_easy_apply_submission_improved


# H2 (2026-09-15): a visible modal/dialog containing form controls, or None. Used to scope
# _find_save_button — it must NEVER be called with the whole driver/page (its own docstring
# already said so): page-wide it matched job-card bookmark "Save" buttons on the search-results
# page behind the job, and every one of those got clicked and counted as an applied submission
# (13 in an 18-minute window; 202 external-redirects and Naukri error pages included). This is
# the confirmed root cause of the "Applied (Easy Apply)" false-confirm rows.
def _find_modal_form(driver):
    try:
        driver.implicitly_wait(0)
        modals = driver.find_elements(
            By.CSS_SELECTOR,
            "div[class*='modal' i], div[class*='Modal'], div[class*='dialog' i], [role='dialog'], [aria-modal='true']")
        for m in modals:
            try:
                if not m.is_displayed():
                    continue
                if m.find_elements(By.CSS_SELECTOR, "input, select, textarea, button"):
                    return m
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            driver.implicitly_wait(1)
        except Exception:
            pass
    return None


def _save_aware_submit(self):
    if getattr(self, "_chatbot_ok", True) is False or getattr(self, "_chatbot_mandatory_unanswered", []):
        logger.warning("⏭️ Chatbot left mandatory/unanswered questions — skipping submit scan")
        return False
    driver = self.driver
    # Fast path: Easy Apply click may have applied already (no drawer, no Save) — verify in ~5ms
    # instead of burning 26s hunting legacy submit selectors.
    try:
        if _is_applied_on_page(driver):
            logger.info("✅ Page already shows applied state — counting as applied (no submit hunt)")
            return True
    except Exception:
        pass
    modal = _find_modal_form(driver)
    save = _find_save_button(modal) if modal is not None else None
    if save is not None:
        try:
            logger.info("🔍 Found Save button — waiting for enabled state...")
            try:
                driver.implicitly_wait(0)
                if not save.is_enabled():
                    WebDriverWait(driver, 6).until(lambda d: save.is_displayed() and save.is_enabled())
            except Exception:
                pass
            finally:
                try:
                    driver.implicitly_wait(1)
                except Exception:
                    pass
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", save)
            except Exception:
                pass
            time.sleep(0.3)
            clicked = False
            try:
                save.click()
                clicked = True
            except Exception:
                pass
            if not clicked:
                try:
                    driver.execute_script("arguments[0].click();", save)
                    clicked = True
                except Exception as e:
                    logger.debug(f"Save JS click: {e}")
            if not clicked:
                logger.error("❌ Save click failed")
                return False
            time.sleep(2)

            rej = _check_rejection_messages(driver)
            if rej:
                logger.error(f"❌ Application rejected due to incomplete information: '{rej}'")
                try:
                    driver.save_screenshot(os.path.join(_PROFILE_DIR, "debug_incomplete_app.png"))
                    logger.info("📸 Saved screenshot to debug_incomplete_app.png")
                except Exception as e:
                    logger.debug(f"Screenshot error: {e}")
                _close_chatbot_drawer(driver)
                return False

            # H3 (2026-09-15): _verify_application_submitted() (application.py) returns True
            # whenever no button[type=submit] is on the page — true for almost any page, which
            # is how a job-card bookmark "Save" click got counted as a submitted application.
            # Naukri's own verdict (multiApplyResp / 'Applied to "') is the only thing trusted.
            try:
                ev = None
                for _attempt in range(2):
                    ev = _applied_evidence(driver)
                    if ev:
                        break
                    time.sleep(2.0)
                if ev == "applied":
                    logger.info("✅ Save submission CONFIRMED (Naukri shows Applied)")
                    return True
                rej = _check_rejection_messages(driver)
                if rej or ev == "rejected":
                    logger.error(f"❌ Application rejected due to incomplete information: '{rej or ev}'")
                    try:
                        driver.save_screenshot(os.path.join(_PROFILE_DIR, "debug_incomplete_app.png"))
                        logger.info("📸 Saved screenshot to debug_incomplete_app.png")
                    except Exception:
                        pass
                    return False
                if ev == "external":
                    logger.info("↗️ Save led to a company-site redirect — not an Easy Apply submission")
                    return False
                logger.warning("⚠️ Save clicked, no Naukri verdict found — will NOT count as applied")
                return False
            except Exception:
                return False
        except TimeoutException:
            logger.info("Save stayed disabled (questions pending) — will not submit")
            _close_chatbot_drawer(driver)
            return False
        except Exception as e:
            logger.debug(f"Save flow: {e}")
            return False

    # Legacy modal-form hunt burned 84s and destabilized the driver with zero hits.
    # Only run it when a modal form with inputs actually exists; otherwise fail fast.
    try:
        has_modal_form = driver.execute_script(
            "var n=0;"
            "document.querySelectorAll(\"div[class*='modal' i], div[class*='Modal'], div[class*='dialog' i]\").forEach(function(m){"
            "  if (m.offsetParent!==null) n += m.querySelectorAll('input,select,textarea,button').length;});"
            "return n;") or 0
    except Exception:
        has_modal_form = 0
    if not has_modal_form:
        logger.info("⏭️ No modal form present — skipping legacy submit hunt (fail fast, no driver churn)")
        return False
    try:
        driver.implicitly_wait(0)
        result = _orig_submit_improved(self)
    finally:
        try:
            driver.implicitly_wait(0)
        except Exception:
            pass
    if result:
        rej = _check_rejection_messages(driver)
        if rej:
            logger.error(f"❌ Application rejected due to incomplete information: '{rej}'")
            try:
                driver.save_screenshot(os.path.join(_PROFILE_DIR, "debug_incomplete_app.png"))
                logger.info("📸 Saved screenshot to debug_incomplete_app.png")
            except Exception:
                pass
            _close_chatbot_drawer(driver)
            return False
        # H3: the legacy path's own _verify_application_submitted() (application.py) also uses
        # the "no submit button on page = success" heuristic. Downgrade its True to a Naukri
        # verdict when one is available; keep it only when there is truly nothing to check.
        ev = _applied_evidence(driver)
        if ev == "applied":
            logger.info("✅ Legacy submit CONFIRMED (Naukri shows Applied)")
        elif ev in ("external", "rejected"):
            logger.warning(f"⚠️ Legacy submit reported success, but Naukri verdict is '{ev}' — not counting as applied")
            return False
    return result


def _fresh_title_company(driver, job_url):
    title, company = "Unknown", "Unknown"
    try:
        driver.implicitly_wait(0)
        try:
            metas = driver.find_elements(By.XPATH, "//meta[@property='og:title']")
            if metas:
                content = metas[0].get_attribute("content") or ""
                if content.strip():
                    title = content.strip()[:120]
        except Exception:
            pass
        if title == "Unknown":
            for xp in ("//h1[contains(@class,'title') or contains(@class,'Title')]",
                       "//h1", "//div[contains(@class,'jd-title')]"):
                try:
                    els = _displayed(driver.find_elements(By.XPATH, xp))
                    if els and (els[0].text or "").strip():
                        title = els[0].text.strip().split("\n")[0][:120]
                        break
                except Exception:
                    continue
        for css in ("div[class*='comp-name' i]", "div[class*='CompName']",
                    ".jd-header-comp-name", "div[class*='company' i]"):
            try:
                els = _displayed(driver.find_elements(By.CSS_SELECTOR, css))
                if els and (els[0].text or "").strip():
                    company = els[0].text.strip().split("\n")[0][:80]
                    break
            except Exception:
                continue
        if title == "Unknown":
            try:
                slug = job_url.rstrip("/").split("/")[-1].replace("-", " ")
                if slug:
                    title = slug[:100]
            except Exception:
                pass
    finally:
        try:
            driver.implicitly_wait(1)
        except Exception:
            pass
    return title, company


def _has_live_apply_button(driver):
    """Zero-wait snapshot: is a real, clickable Apply control currently visible? Reuses
    EASY_APPLY_SELECTORS (defined below; module-level names resolve at call time, so the forward
    reference is fine) rather than a new guessed selector, so a genuinely already-applied page
    (which hides/disables its own Apply button) does not trigger a false override here."""
    try:
        driver.implicitly_wait(0)
        for selector in EASY_APPLY_SELECTORS:
            try:
                by = By.XPATH if selector.startswith("//") else By.CSS_SELECTOR
                for c in driver.find_elements(by, selector):
                    try:
                        if c.is_displayed() and c.is_enabled():
                            txt = (c.text or "").strip().lower()
                            cid = (c.get_attribute("id") or "").lower()
                            if "applied" in txt or "save job" in txt or "company site" in txt \
                                    or "company-site" in cid:
                                continue
                            return True
                    except StaleElementReferenceException:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            driver.implicitly_wait(1)
        except Exception:
            pass
    return False


def _is_applied_on_page(driver):
    """Broad already-applied check — zero delay.

    2026-09-16: the loose page-wide "already applied" text scan below is unscoped and cost a
    real job -- at 10:40:29 it fired and the bot skipped a Big Data Engineer posting that had a
    visible, enabled Apply button and no DB row (watcher incident APPLY_NOT_PRESSED, high; see
    docs/STRUCTURAL_FAULTS.md). Rather than guess a new scoping selector without a live DOM
    sample of what it actually matched, corroborate it against EASY_APPLY_SELECTORS -- a
    selector list this codebase already trusts. A currently-visible, enabled Apply control is
    strong evidence the text match found something else on the page (a similar-jobs rail, a nav
    widget); a genuinely already-applied job hides or disables its own Apply button, so this
    override does not fire there.
    """
    try:
        driver.implicitly_wait(0)
        try:
            for el in _displayed(driver.find_elements(By.CSS_SELECTOR, ".already-applied-layer")):
                return True  # a dedicated already-applied overlay class -- trust this alone
        except Exception:
            pass
        matched_loose_text = False
        try:
            els = driver.find_elements(
                By.XPATH,
                "//*[contains(translate(normalize-space(text()),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                "'abcdefghijklmnopqrstuvwxyz'),'already applied')]")
            for el in els[:6]:
                try:
                    if el.is_displayed():
                        matched_loose_text = True
                        break
                except Exception:
                    continue
        except Exception:
            pass
        if matched_loose_text:
            if _has_live_apply_button(driver):
                logger.info(
                    "⚠️ 'already applied' text matched but a live Apply button is also visible "
                    "-- treating page as NOT applied (unscoped text match overridden)")
            else:
                return True
        if _applied_evidence(driver) == "applied":
            return True
    finally:
        try:
            driver.implicitly_wait(1)
        except Exception:
            pass
    return False


# H3 (2026-09-15): Naukri's own verdict, read off the result page it navigates to after a
# chatbot Save or a legacy submit — /myapply/... with multiApplyResp={"<jobId>":<code>} in the
# URL (200 = applied, 202 = "redirected to the company website" = NOT applied) and/or the page
# text 'Applied to "<title>"'. Every prior success check (URL contains 'applied'/'confirmation'/
# 'success', or body contains 'apply confirmation') fires for BOTH codes — sampled live, the
# title "Apply Confirmation" appears on 202 pages too — which is how page-wide-Save clicks on a
# job-card bookmark button, and 202 external-redirects, both got written to the DB as
# "Applied (Easy Apply)". This is the only success oracle that should be trusted.
def _applied_evidence(driver):
    """Returns 'applied' | 'external' | 'rejected' | None from Naukri's own result page."""
    try:
        driver.implicitly_wait(0)
        url = driver.current_url or ""
        if "/myapply/" not in url.lower():
            return None
        try:
            decoded = urllib.parse.unquote(url)
            m = re.search(r"multiApplyResp=(\{[^}]*\})", decoded, re.IGNORECASE)
            if m:
                resp = json.loads(m.group(1))
                code = next(iter(resp.values())) if resp else None
                if code in (200, "200"):
                    return "applied"
                if code in (202, "202"):
                    return "external"
                if code in (406, "406"):  # "Oops! ... incomplete information" page
                    return "rejected"
        except Exception as e:
            logger.debug(f"multiApplyResp parse: {e}")
        try:
            body = (driver.execute_script("return document.body ? document.body.innerText : ''") or "").lower()
        except Exception:
            body = ""
        if 'applied to "' in body:
            return "applied"
        if "redirected to the company website" in body:
            return "external"
        if any(p in body for p in ("oops!", "not accepted due to", "incomplete information")):
            return "rejected"
        return None
    finally:
        try:
            driver.implicitly_wait(1)
        except Exception:
            pass


def _get_page_with_retry(driver, url, max_retries=2, timeout=5):
    """Fast page load with up to 2 reload attempts if dropping."""
    for attempt in range(max_retries + 1):
        try:
            if attempt == 0:
                driver.get(url)
            else:
                logger.info(f"🔄 Reloading page (attempt {attempt}/{max_retries}): {url[:70]}...")
                driver.refresh()
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
            return True
        except TimeoutException:
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            logger.warning(f"⚠️ Page load timed out after {max_retries} reloads: {url[:70]}")
            return False
        except Exception as e:
            logger.debug(f"Page load attempt {attempt} error: {e}")
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return False
    return False


ApplicationMixin._handle_easy_apply_submission_improved = _save_aware_submit

# ------------------------------------------------- N4 apply-one replacement
EASY_APPLY_SELECTORS = [
    "button.apply-button",
    "button[class*='apply-button']",
    "button[id*='apply']",
    ".job-apply-button",
    "button[class*='apply' i]",
    "a.apply-button",
    "button[data-automation*='apply' i]",
    "button[aria-label*='apply' i]",
    # H1 (2026-09-15): both XPaths below match on button TEXT and used to accept "Apply on
    # company site" (contains 'apply', not 'applied') -> the bot clicked through to the
    # employer's own site and Naukri counted it as External (202), while the false-confirm
    # bug (H2/H3) then wrote it to the DB as "Applied (Easy Apply)". Excluding "company site"
    # here is the actual fix; H2/H3 make a repeat impossible even if this slips.
    "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply') and not(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'applied')) and not(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'save job')) and not(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'company site'))]",
    "//a[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply') and not(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'applied')) and not(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'company site'))]",
]


# Naukri's apply-workflow POST answers 403 {"customErrorCode": 403009, "message": "Daily quota of jobs
# exceeded"} once the account's daily apply quota is used (caught by the watcher 2026-09-15). No drawer
# opens, so without this the bot logged "Application failed" and kept cycling all day. The status is
# read from the page's own Resource Timing entry (same-origin, so responseStatus is exposed); the
# buffer is cleared just before the Apply click so the entry can't be dropped by a full buffer.
_APPLY_API_JS = (
    "var es = performance.getEntriesByType('resource').filter(function (e) {"
    "  return e.name.indexOf('apply-workflow/v1/apply') >= 0; });"
    "if (!es.length) return null;"
    "var e = es[es.length - 1];"
    "return (typeof e.responseStatus === 'number') ? e.responseStatus : -1;")


def _apply_api_status(driver, wait_s=4.0):
    deadline = time.time() + wait_s
    while True:
        try:
            st = driver.execute_script(_APPLY_API_JS)
        except Exception:
            st = None
        if st is not None or time.time() >= deadline:
            return st
        time.sleep(0.5)


def _quota_reset_ts():
    import datetime as _dt
    tomorrow = _dt.datetime.combine(_dt.date.today() + _dt.timedelta(days=1), _dt.time(0, 5))
    return max(time.time() + 3600, tomorrow.timestamp())


NAUKRI_DAILY_LIMIT_MARKERS = (
    "there was an error while processing your request",
    "please try again later",
    "daily quota of jobs exceeded",
    "daily quota exceeded",
)


def record_naukri_daily_limit(reason=""):
    today_str = datetime.now().strftime("%Y-%m-%d")
    marker_path = os.path.join(_PROFILE_DIR, f"NAUKRI_DAILY_LIMIT_{today_str}")
    try:
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {reason}\n")
    except Exception:
        pass
    logger.warning(
        f"⛔ Naukri daily apply limit signal confirmed ({reason}). "
        f"Daily marker written: {marker_path}. Shutting down browser and exiting for today.")


def _check_naukri_daily_limit_signal(driver):
    """Check if Naukri daily apply quota error or banner is present (e.g. Image 1 error banner)."""
    # 1. Check API status (Resource Timing)
    if _apply_api_status(driver) == 403:
        return True, "Naukri apply API returned HTTP 403 (daily quota exceeded)"
    # 2. Check DOM / banner text (as in Image 1: 'There was an error while processing your request, please try again later')
    try:
        body_text = (driver.execute_script("""
            var el = document.querySelector('.error-container, .server-err, .toast-message, .banner, .error, [class*="error"], [class*="alert"]');
            var txt = el ? el.innerText : (document.body ? document.body.innerText : '');
            return txt.toLowerCase();
        """) or "")
        if "there was an error while processing your request" in body_text:
            return True, "error banner: 'There was an error while processing your request, please try again later'"
        if "daily quota of jobs exceeded" in body_text:
            return True, "error banner: 'Daily quota of jobs exceeded'"
    except Exception:
        pass
    return False, ""


def _poll_easy_apply_button(driver, timeout=8.0):
    """Poll for the job-page Apply button (eager-load hydration race: the button often
    renders seconds after <body> exists). Zero implicit wait, 0.5s poll interval."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            driver.implicitly_wait(0)
            for selector in EASY_APPLY_SELECTORS:
                try:
                    by = By.XPATH if selector.startswith("//") else By.CSS_SELECTOR
                    cand = driver.find_elements(by, selector)
                    for c in cand:
                        try:
                            if c.is_displayed() and c.is_enabled():
                                txt = (c.text or "").strip().lower()
                                cid = (c.get_attribute("id") or "").lower()
                                if "applied" in txt or "save job" in txt or "company site" in txt \
                                        or "company-site" in cid:
                                    continue
                                return c
                        except StaleElementReferenceException:
                            continue
                except Exception:
                    continue
        finally:
            try:
                driver.implicitly_wait(1)
            except Exception:
                pass
        time.sleep(0.5)
    return None


def _js_click_apply_fallback(driver):
    """Last-resort: page-wide JS search for a visible Apply button/link and click it in-page."""
    try:
        clicked = driver.execute_script("""
            var els = document.querySelectorAll('button, a, div[role=button], span[role=button]');
            for (var el of els) {
                var t = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).trim().toLowerCase();
                var id = (el.id || '').toLowerCase();
                if (!t || t.indexOf('applied') >= 0 || t.indexOf('save job') >= 0) continue;
                if (t.indexOf('company site') >= 0 || id.indexOf('company-site') >= 0) continue;
                // H1 (2026-09-15): 'apply on' used to also match "Apply on company site",
                // sending the bot to the employer's own site instead of Naukri's Easy Apply.
                if (t === 'apply' || t.indexOf('apply now') >= 0 || t.indexOf('easy apply') >= 0) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) { el.click(); return t.slice(0, 40); }
                }
            }
            return '';
        """)
        if clicked:
            logger.info(f"✅ JS fallback clicked Apply control ('{clicked}')")
            return True
    except Exception as e:
        logger.debug(f"JS apply fallback error: {e}")
    return False


def _enforce_single_tab(driver, tag=""):
    """Close any extra tabs/windows so the bot always reuses ONE Edge window (no new window per job).

    5a (2026-09-15): the outer finally-block timing (below) can only say WHICH of its 4 calls to
    this function was slow, not what inside it blocked. Sub-step timing added here so the next
    real ~40s sample is conclusive without another restart-and-wait cycle.
    """
    _t0 = time.time()
    _sub = []
    try:
        handles = driver.window_handles
        _sub.append(f"handles({len(handles)})={time.time() - _t0:.1f}s")
        if len(handles) > 1:
            _t1 = time.time()
            main = driver.current_window_handle
            _sub.append(f"main_handle={time.time() - _t1:.1f}s")
            # Bug found 2026-09-15 (post-mortem on a dead browser, ~1h after the fix below
            # first ran live): this loop assumed `main` always matches one entry in `handles`.
            # When it doesn't (stale current_window_handle, a race with an in-flight
            # navigation/popup), `h != main` was true for EVERY handle, and the CDP close below
            # closed all of them — the whole browser exits when its last window closes. Guard 1:
            # never trust a `main` absent from the fresh handle list. Guard 2 (belt-and-suspenders
            # against any other way this same shape of bug could recur): track how many handles
            # are left as we go and refuse to close the last one, regardless of what `main` said.
            if main not in handles:
                logger.warning(f"⚠️ current_window_handle ('{main}') isn't in window_handles "
                               f"({len(handles)} handles) — keeping the first handle instead of "
                               f"trusting a stale reference (this is what closed the whole "
                               f"browser earlier tonight)")
                main = handles[0]
            remaining = len(handles)
            for h in handles:
                if h == main:
                    continue
                if remaining <= 1:
                    logger.warning("⚠️ refusing to close the last remaining tab/window")
                    break
                # 5a fix (2026-09-15), evidence-based: switch_to.window()+close() on an extra
                # tab blocked ~20s before raising WebDriverException (seen live, twice per job
                # cleanup = the exact ~40s stall). Root cause: that path waits on the target
                # tab's own renderer to respond, and this tab doesn't. Closing it at the CDP
                # level (Chromium's window handle IS its CDP targetId) doesn't need the tab to
                # cooperate — the browser process just kills the target. Falls back to the old
                # (slow but previously-only) path only if the CDP call itself fails.
                _t2 = time.time()
                try:
                    driver.execute_cdp_cmd("Target.closeTarget", {"targetId": h})
                    _sub.append(f"cdp_close={time.time() - _t2:.1f}s")
                    remaining -= 1
                except Exception as e:
                    _sub.append(f"cdp_close:ERR({type(e).__name__})={time.time() - _t2:.1f}s")
                    _t2b = time.time()
                    try:
                        driver.switch_to.window(h)
                        driver.close()
                        remaining -= 1
                    except Exception as e2:
                        _sub.append(f"switch_close:ERR({type(e2).__name__})")
                    finally:
                        _sub.append(f"switch_close={time.time() - _t2b:.1f}s")
            _t3 = time.time()
            driver.switch_to.window(main)
            _sub.append(f"switch_main={time.time() - _t3:.1f}s")
            logger.debug(f"🧹 Closed extra tabs, single-tab enforced ({len(handles)}→1)")
    except Exception as e:
        _sub.append(f"outer:ERR({type(e).__name__})")
    _total = time.time() - _t0
    if _total > 1.0:
        logger.info(f"⏱️ _enforce_single_tab[{tag}] took {_total:.1f}s ({', '.join(_sub)})")


def _unattended_apply_one(self, job_url):
    """High-speed skip-external apply path with zero-wait probing & 2-attempt reload. Single-tab only.

    Stamps self._last_apply_outcome before most `return False`s so the caller
    (naukri_bot/modules/application.py) can tell a correct skip (quota paused, no Easy Apply
    button) from a real failure -- 2026-09-16: 11 of 15 "❌ Application failed" lines on one day
    were external skips working exactly as designed, logged identically to genuine failures.
    """
    original_tab = None
    driver = self.driver
    _t_entry = time.time()
    self._last_apply_outcome = None
    if getattr(self, "_apply_blocked_until", 0) > time.time():
        self._last_apply_outcome = "quota_blocked"
        return False  # daily quota already refused today; don't burn ~50s per job finding out again
    try:
        _enforce_single_tab(driver, tag="entry")
        original_tab = driver.current_window_handle
        if not _get_page_with_retry(driver, job_url, max_retries=2, timeout=5):
            return False

        if _is_applied_on_page(driver):
            logger.info("⏩ Page shows already applied")
            return False

        job_title, company = _fresh_title_company(driver, job_url)
        logger.info(f"📋 {job_title} at {company}")

        try:
            driver.execute_script("try { performance.clearResourceTimings(); } catch (e) {}")
        except Exception:
            pass
        # Polled probe for Easy Apply buttons (hydration race) + JS text fallback
        easy_apply_button = _poll_easy_apply_button(driver, timeout=8.0)
        js_clicked = False
        if not easy_apply_button:
            js_clicked = _js_click_apply_fallback(driver)

        if easy_apply_button or js_clicked:
            if js_clicked and not easy_apply_button:
                logger.info("✅ Apply control already clicked via JS fallback")
            else:
                logger.info("✅ Found Easy Apply button")
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", easy_apply_button)
                except Exception:
                    pass
                try:
                    easy_apply_button.click()
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", easy_apply_button)
                    except Exception as e:
                        logger.error(f"Easy Apply click failed: {e}")
                        return False

            # 1-click apply: Easy Apply may complete instantly with no drawer — check first.
            # 2026-09-16: prefer Naukri's own verdict (_applied_evidence) when it's available --
            # it hard-gates on the /myapply/ redirect URL, so page text can't fool it. At 1.5s the
            # redirect often hasn't landed yet, so fall back to the now button-corroborated
            # _is_applied_on_page rather than trust a text match alone at this DB-writing site.
            time.sleep(1.5)
            is_lim, lim_reason = _check_naukri_daily_limit_signal(driver)
            if is_lim:
                self._last_apply_outcome = "quota_blocked"
                record_naukri_daily_limit(lim_reason)
                try:
                    driver.quit()
                except Exception:
                    pass
                sys.exit(44)
            try:
                _ev = _applied_evidence(driver)
                if _ev == "applied" or (_ev is None and _is_applied_on_page(driver)):
                    logger.info(f"✅ Easy Apply completed instantly (no questions asked, evidence={_ev or 'page-scan'})")
                    try:
                        self._save_job_application(
                            self._extract_job_id(job_url), job_url,
                            "Applied (Easy Apply)", f"{job_title} at {company}")
                    except Exception as e:
                        logger.debug(f"db save: {e}")
                    return True
                elif _ev in ("external", "rejected"):
                    logger.info(f"↗️ Easy Apply click navigated but Naukri verdict is '{_ev}' — not counting as applied")
            except Exception:
                pass

            self._chatbot_detected = False
            self._chatbot_ok = True
            self._chatbot_unanswered = []
            self._chatbot_mandatory_unanswered = []
            self._chat_v2_result = None  # never read the previous job's verdict for this one
            chatbot_success = self._handle_chatbot(timeout=5)

            if getattr(self, "_chatbot_detected", False):
                if chatbot_success and getattr(self, "_chatbot_ok", True) and not getattr(self, "_chatbot_mandatory_unanswered", []):
                    logger.info("✅ Easy Apply (Chatbot) successfully completed & confirmed")
                    try:
                        self._save_job_application(
                            self._extract_job_id(job_url), job_url,
                            "Applied (Easy Apply)", f"{job_title} at {company}")
                    except Exception as e:
                        logger.debug(f"db save: {e}")
                    return True
                else:
                    # Drawer questions may have applied despite unconfirmed Save — verify, don't assume failure
                    try:
                        if _is_applied_on_page(driver):
                            logger.info("✅ Page shows applied despite unconfirmed Save — counting as applied")
                            try:
                                self._save_job_application(
                                    self._extract_job_id(job_url), job_url,
                                    "Applied (Easy Apply)", f"{job_title} at {company}")
                            except Exception as e:
                                logger.debug(f"db save: {e}")
                            return True
                    except Exception:
                        pass
                    # 2026-09-23: a Naukri 406 used to leave no DB row, so the same job was
                    # re-submitted (and rejected again) every ~18-min cycle -- 9 jobs x ~65
                    # rejections each. Record Naukri's verdict once; is_job_already_applied then
                    # blocks it. Bot-side aborts (POLICY/STUCK/...) never reach Naukri's final
                    # apply call, so they stay retryable as before.
                    _v2 = getattr(self, "_chat_v2_result", None)
                    if _v2 is not None and _v2.outcome == "REJECTED_INCOMPLETE":
                        try:
                            self._save_job_application(
                                self._extract_job_id(job_url), job_url,
                                "Rejected by Naukri (406 incomplete)", f"{job_title} at {company}")
                        except Exception as e:
                            logger.debug(f"db save: {e}")
                    logger.warning("⏭️ Chatbot incomplete / rejected — discarding job")
                    return False

            # Non-chatbot standard Easy Apply modal form
            if self._handle_easy_apply_submission_improved():
                try:
                    self._save_job_application(
                        self._extract_job_id(job_url), job_url,
                        "Applied (Easy Apply)", f"{job_title} at {company}")
                except Exception as e:
                    logger.debug(f"db save: {e}")
                return True
            return False

        logger.info(f"↗️ No Easy Apply — external skipped instantly (page+probe took {time.time() - _t_entry:.1f}s)")
        self.skipped += 1
        self._last_apply_outcome = "external_skip"
        try:
            _t0 = time.time()
            self._save_job_application(
                self._extract_job_id(job_url), job_url,
                "External (Manual Required)", f"{job_title} at {company}")
            logger.info(f"⏱️ skip-path db save took {time.time() - _t0:.1f}s")
        except Exception as e:
            logger.debug(f"db save: {e}")
        return False
    except Exception as e:
        logger.error(f"Error in _unattended_apply_one: {e}")
        return False
    finally:
        # Measured, not yet changed (2026-09-15): ~40s of bot-log silence follows every verdict
        # and only this cleanup runs there. Log its duration so the watcher can confirm the
        # cause before anyone "fixes" it blind.
        # Measured 40-53s here on every job path (watcher SLOW_TAB_CLEANUP). Capping the page-load
        # timeout did not change it (still 40.1s), so that guess was reverted; time each call to
        # find which one actually blocks before changing anything else.
        _t_fin = time.time()
        _steps = []

        def _timed(name, fn):
            t = time.time()
            try:
                return fn()
            except Exception as e:
                _steps.append(f"{name}:ERR({type(e).__name__})")
                return None
            finally:
                _steps.append(f"{name}={time.time() - t:.1f}s")

        _timed("enforce1", lambda: _enforce_single_tab(driver, tag="enforce1"))
        _cur = _timed("current_handle", lambda: driver.current_window_handle)
        if original_tab and _cur and _cur != original_tab:
            _timed("switch_back", lambda: driver.switch_to.window(original_tab))
        _timed("enforce2", lambda: _enforce_single_tab(driver, tag="enforce2"))
        _fin = time.time() - _t_fin
        if _fin > 1.0:
            logger.info(f"⏱️ tab cleanup took {_fin:.1f}s ({', '.join(_steps)})")


# ---------------------------------------- N7 official tally + Excel tracker
_TRACKER_PATH = os.environ.get("NAUKRI_TRACKER_PATH", "").strip()
_REC_STATE = {"last": 0.0, "first_done": False}
_REC_EVERY = 40 * 60
_REC_URL = "https://www.naukri.com/mnjuser/myapplications"


def _run_tracker(*args):
    """True when the optional external tracker finished cleanly. Disabled when
    NAUKRI_TRACKER_PATH is unset; failures are logged instead of reported as success."""
    if not _TRACKER_PATH or not os.path.exists(_TRACKER_PATH):
        logger.debug("External tracker not configured (NAUKRI_TRACKER_PATH); skipping")
        return False
    try:
        r = subprocess.run([sys.executable, _TRACKER_PATH, *args], cwd=REPO, timeout=120,
                           stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            logger.warning(f"⚠️ tracker {args[:1]} exited {r.returncode}")
        return r.returncode == 0
    except Exception as e:
        logger.warning(f"⚠️ tracker {args[:1]} failed: {e!r}")
        return False


def _report_official(source, count, method):
    _run_tracker("official", "--source", source,
                 "--count", "unknown" if count is None else str(count),
                 "--method", (method or "")[:200])


_REC_IMPLICIT_RESTORE = 15  # must match config.local.json webdriver.implicit_wait


def _rec_ready_naukri(drv):
    """Wait condition for data-bearing elements on myapplications or profile."""
    try:
        for sel in (
            "[class*='applicationCard']",
            "[class*='applied-job']",
            ".jobCard",
            "[class*='jobCard']",
            "[class*='applied-status']",
            ".applied-jobs",
            ".count",
            "[class*='count']",
            ".empty-state",
            ".no-applications",
        ):
            if drv.find_elements(By.CSS_SELECTOR, sel):
                return True
        t = (drv.find_element(By.TAG_NAME, "body").text or "").lower()
        return any(w in t for w in (
            "applied jobs", "application status", "my applications",
            "no applications", "total applied", "applied: ", "applications: "
        ))
    except Exception:
        return False


def _parse_naukri_official(driver):
    try:
        body = driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        body = ""

    cands = []
    # Multi-pattern regex: count-before-label, count-after-label, colon-separated,
    # "showing X of Y applications", etc.
    for pat, tag in (
        (r"(\d[\d,]{0,6})\s+(?:total\s+)?applications?", "N applications"),
        (r"applied\s+jobs?\s*\(?\s*(\d[\d,]{0,6})\s*\)?", "Applied Jobs (N)"),
        (r"applications?\s*[:–\-()]?\s*\(?\s*(\d[\d,]{0,6})", "Applications: N"),
        (r"applied\s*[:–\-]?\s*(\d[\d,]{0,6})", "Applied: N"),
        (r"(?:of|of\s+about)\s+(\d[\d,]{0,6})\s+(?:job\s+)?applications?", "of N applications"),
        (r"(\d[\d,]{0,6})\s+applied\b", "N applied"),
    ):
        try:
            for m in re.finditer(pat, body, re.IGNORECASE):
                try:
                    cands.append((int(m.group(1).replace(",", "")), f"text regex on page ({tag})"))
                except Exception:
                    continue
        except Exception:
            continue

    # Targeted count elements on Naukri
    for sel in (
        "[class*='applied-count']",
        "[class*='application-count']",
        ".app-count",
        ".applied-count",
        ".total-applications",
        "span.count",
    ):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                txt = (el.text or "").strip()
                m = re.search(r"(\d[\d,]{0,6})", txt)
                if m:
                    cands.append((int(m.group(1).replace(",", "")), f"targeted count element ({sel})"))
        except Exception:
            continue

    if cands:
        best = max(cands, key=lambda x: x[0])
        return best[0], best[1]

    lowered = (body or "").lower()
    if "appl" not in lowered and ("log in" in lowered or "login" in lowered
                                  or "sign in" in lowered):
        try:
            url = driver.current_url
        except Exception:
            url = "?"
        logger.warning(f"Official tally: login redirect suspected (url={url})")
        return None, "not parsed (login redirect suspected)"

    # Zero implicit wait around the selector probes so misses cost ~0s
    try:
        driver.implicitly_wait(0)
        for css in (".jobCard", "[class*='jobCard']", "[class*='applicationCard']",
                    "[class*='applied-job']", ".cardContainer"):
            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                if els:
                    return len(els), f"visible card count ({css}, page 1 — approximate)"
            except Exception:
                continue
    finally:
        try:
            driver.implicitly_wait(_REC_IMPLICIT_RESTORE)
        except Exception:
            pass
    try:
        url = driver.current_url
    except Exception:
        url = "?"
    logger.warning(f"Official tally not parsed (url={url} "
                   f"body={body[:300]!r}...)")
    return None, "not parsed"


def _reconcile_nk_periodic(driver, force=False):
    now = time.time()
    if not force and (now - _REC_STATE["last"]) < _REC_EVERY:
        return
    _REC_STATE["last"] = now
    count, method = None, "error"
    try:
        orig = driver.current_window_handle
        driver.switch_to.new_window("tab")
        try:
            try:
                driver.get(_REC_URL)
            except TimeoutException:
                logger.warning("Official tally: page-load timeout — retrying once")
                try:
                    driver.get(_REC_URL)
                except TimeoutException as e2:
                    method = f"page-load timeout: {e2!r}"
                    _report_official(_SOURCE_NAME, count, method)
                    logger.info(f"Official tally: naukri count={count} via {method}")
                    return

            try:
                WebDriverWait(driver, 10).until(_rec_ready_naukri)
            except TimeoutException:
                pass
            time.sleep(1.5)

            try:
                cur = driver.current_url or ""
            except Exception:
                cur = ""

            # If redirected to homepage, attempt to navigate via "Applications" link
            if "myapplications" not in cur:
                try:
                    app_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='myapplications'], a[href*='applied']")
                    for al in app_links:
                        href = al.get_attribute("href") or ""
                        if "myapplications" in href:
                            driver.get(href)
                            try:
                                WebDriverWait(driver, 10).until(_rec_ready_naukri)
                            except TimeoutException:
                                pass
                            time.sleep(1.5)
                            cur = driver.current_url or ""
                            break
                except Exception:
                    pass

            if "myapplications" not in cur:
                # Still redirected — try parsing count from current page stats before declaring redirect
                count, parse_m = _parse_naukri_official(driver)
                if count is not None and count > 0:
                    method = f"from redirect page ({cur[:60]}): {parse_m}"
                    _report_official(_SOURCE_NAME, count, method)
                    logger.info(f"Official tally: naukri count={count} via {method}")
                    return

                try:
                    excerpt = (driver.find_element(By.TAG_NAME, "body").text or "")[:300]
                except Exception:
                    excerpt = ""
                method = f"redirected (url={cur[:120]})"
                logger.warning(f"Official tally: landing check failed — {method} "
                               f"body={excerpt!r}...")
                _report_official(_SOURCE_NAME, None, method)
                logger.info(f"Official tally: naukri count=None via {method}")
                return

            count, method = _parse_naukri_official(driver)
            if count is None:
                try:
                    excerpt = (driver.find_element(By.TAG_NAME, "body").text or "")[:300]
                except Exception:
                    excerpt = ""
                logger.warning(f"Official tally parse failed (url={cur} "
                               f"body={excerpt!r}...)")
        finally:
            try:
                driver.close()
            except Exception:
                pass
            try:
                driver.switch_to.window(orig)
            except Exception:
                pass
    except Exception as e:
        method = f"error: {e!r}"
    _report_official(_SOURCE_NAME, count, method)
    logger.info(f"Official tally: naukri count={count} via {method}")


# N7b: write application_date/company_name for every new row so the tracker can
# date-filter correctly (the tracked INSERT omits those columns → NULL dates),
# and support profile-isolated database and selector cache paths.
try:
    from naukri_bot.utils.database import DatabaseMixin as _DBMixin
    _orig_init_db = _DBMixin.init_job_database

    def _isolated_init_job_database(self):
        db_path = getattr(self, "_db_path", None)
        if not db_path:
            return _orig_init_db(self)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            self.db_conn = sqlite3.connect(db_path, check_same_thread=False)
            cursor = self.db_conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS applied_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_url TEXT NOT NULL,
                    job_title TEXT,
                    company_name TEXT,
                    application_date TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                    status TEXT,
                    notes TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_id ON applied_jobs(job_id)')
            self.db_conn.commit()
            logger.info(f"✅ Job database initialized ({db_path})")
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            self.db_conn = None

    _DBMixin.init_job_database = _isolated_init_job_database

    def _dated_save_job_application(self, job_id, job_url, status, notes=''):
        if not self.db_conn:
            return
        try:
            cursor = self.db_conn.cursor()
            # 2026-09-16: this used INSERT OR REPLACE on the job_id primary key, which
            # unconditionally overwrites every column -- so a later transient verdict (e.g.
            # "External (Manual Required)" when the Apply button just fails to hydrate within
            # 8s) could silently downgrade a job already recorded as genuinely applied, losing
            # both the true status and its real application_date. Refuse any write that would
            # replace an "Applied*" status with a non-"Applied*" one for the same job_id.
            try:
                cursor.execute("SELECT status FROM applied_jobs WHERE job_id = ?", (job_id,))
                row = cursor.fetchone()
                existing_status = (row[0] or "") if row else ""
            except sqlite3.Error:
                existing_status = ""
            if existing_status.strip().lower().startswith("applied") \
                    and not status.strip().lower().startswith("applied"):
                logger.warning(
                    f"⛔ Refusing to downgrade job {job_id} from '{existing_status}' to '{status}' "
                    "— existing applied status kept")
                return

            job_title = job_url.split('/')[-1].replace('-', ' ')[:100]
            company = ""
            if notes and " at " in notes:
                company = notes.rsplit(" at ", 1)[-1].strip()[:80]
            cursor.execute(
                "INSERT OR REPLACE INTO applied_jobs "
                "(job_id, job_url, job_title, company_name, application_date, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, job_url, job_title, company,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), status, notes))
            self.db_conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database save error: {e}")

    _DBMixin._save_job_application = _dated_save_job_application
except Exception as e:
    logger.warning(f"dated-save patch skipped: {e!r}")


# N7c: profile-isolated selector cache persistence.
try:
    from naukri_bot.utils.selectors import SelectorCacheMixin as _SCMixin

    def _isolated_load_selector_cache(self):
        cache_file = getattr(self, "_cache_path", 'selector_cache.json')
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    loaded_cache = json.load(f)
                    for key, value in loaded_cache.items():
                        if value:
                            self.selector_cache[key] = value
                cached_count = len([v for v in self.selector_cache.values() if v])
                logger.info(f"✅ Loaded selector cache with {cached_count} cached selectors ({cache_file})")
        except Exception as e:
            logger.debug(f"Could not load selector cache: {e}")

    def _isolated_save_selector_cache(self):
        cache_file = getattr(self, "_cache_path", 'selector_cache.json')
        try:
            os.makedirs(os.path.dirname(os.path.abspath(cache_file)), exist_ok=True)
            cache_to_save = {k: v for k, v in self.selector_cache.items() if v}
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_to_save, f, indent=2)
            logger.debug(f"💾 Selector cache saved ({cache_file}): {cache_to_save}")
        except Exception as e:
            logger.error(f"Could not save selector cache: {e}")

    _SCMixin.load_selector_cache = _isolated_load_selector_cache
    _SCMixin.save_selector_cache = _isolated_save_selector_cache
except Exception as e:
    logger.warning(f"selector cache patch skipped: {e!r}")


# N7d: session end → final official tally + Excel sync, with profile-isolated reports.
try:
    from naukri_bot.utils.session import SessionMixin as _SessionMixin
    _orig_save_results = _SessionMixin.save_results

    def _tracked_save_results(self):
        try:
            if getattr(self, "driver", None):
                _reconcile_nk_periodic(self.driver, force=True)
        except Exception:
            pass

        session_dir = getattr(self, "_session_dir", REPO)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = os.path.join(session_dir, f'naukri_session_{timestamp}.json')
        try:
            session_data = {
                'timestamp': timestamp,
                'date': datetime.now().isoformat(),
                'statistics': {
                    'total_jobs_found': len(self.joblinks),
                    'applications_sent': self.applied,
                    'applications_failed': self.failed,
                    'jobs_skipped': self.skipped,
                    'external_tabs_opened': len(self.external_tabs_opened),
                    'success_rate': round((self.applied / max(self.applied + self.failed, 1)) * 100, 2),
                    'submit_button_success_rate': round(
                        (self.performance_stats['submit_button_success'] /
                         max(self.performance_stats['submit_button_success'] +
                             self.performance_stats['submit_button_failures'], 1)) * 100, 2
                    )
                },
                'applications': {
                    'successful': self.applied_list['passed'],
                    'failed': self.applied_list['failed']
                },
                'config_used': {
                    'keywords': self.config.get('job_search', {}).get('keywords', []),
                    'location': self.config.get('job_search', {}).get('location', '')
                },
                'performance': self.performance_stats,
                'cached_selectors': {k: v for k, v in self.selector_cache.items() if v}
            }
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=4, ensure_ascii=False)
            logger.info(f"📊 Session report saved: {report_file}")
        except Exception as e:
            logger.error(f"Failed to save results: {e}")

        if _run_tracker("sync"):
            logger.info("📊 Excel tracker synced")

    _SessionMixin.save_results = _tracked_save_results
except Exception as e:
    logger.warning(f"save_results patch skipped: {e!r}")


# ---------------------------------------- N8 Robust Session Recovery + Search Loop
try:
    from naukri_bot.core.webdriver import DriverMixin as _WDMixin
    from naukri_bot.modules.search import SearchMixin as _SearchMixin

    _orig_recover_session = _WDMixin.recover_session

    # 2026-09-30: fail-closed driver resolution. When Edge auto-updated to 154, webdriver_manager's
    # midnight cache expiry sent it online, its error was swallowed, and Selenium Manager (which
    # only knows the retired msedgedriver.azureedge.net) fell back to a stale 149 driver from its
    # own cache; the old setup_driver fallback then started Edge WITHOUT the saved profile (a Temp
    # scoped_dir) and the bot typed the stored password - ~12 times since 09-14. Now: use a driver
    # whose major version matches the installed Edge, or exit 46 with a marker the keepalive honours.
    # Never Selenium Manager, never PATH, never config edge_driver_path, never a profile-less Edge.
    DRIVER_UNAVAILABLE_EXIT = 46

    def _file_version(path):
        """Windows file version ('154.0.4258.37') via version.dll; no subprocess."""
        try:
            import ctypes
            from ctypes import wintypes
            size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
            if not size:
                return None
            buf = ctypes.create_string_buffer(size)
            if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf):
                return None
            ptr, length = ctypes.c_void_p(), wintypes.UINT()
            if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
                return None
            fixed = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
            ms, ls = fixed[2], fixed[3]
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:
            return None

    def _edge_browser_version():
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if base:
                v = _file_version(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
                if v:
                    return v
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\BLBeacon") as k:
                return str(winreg.QueryValueEx(k, "version")[0])
        except Exception:
            return None

    def _exc_text(e):
        """Selenium 4.15 keeps the message in .msg, so {e!r} printed just 'SessionNotCreatedException()'."""
        msg = getattr(e, "msg", None) or str(e) or ""
        return f"{type(e).__name__}: {' '.join(str(msg).split())}"

    def _free_ram_mb():
        """Available physical memory in MB (Task Manager's 'Available'), or None if unreadable."""
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return int(st.ullAvailPhys // (1024 * 1024))
        except Exception:
            return None

    def _wait_for_ram(min_mb=1024, max_min=10, poll_sec=30):
        """2026-10-08: bounded wait for free memory before starting Edge (same rule as the LinkedIn
        supervisor). On 10-07/10-08 Edge starts took 28-492 s or failed outright while 0.5 GB was free
        and 5+ browser bots ran at once. Launches anyway after max_min; never an open loop."""
        free = _free_ram_mb()
        if free is None or free >= min_mb:
            return
        logger.warning(f"🧠 Free RAM {free} MB < {min_mb} MB - waiting up to {max_min} min before starting Edge")
        deadline = time.time() + max_min * 60
        _hb = time.time()
        while time.time() < deadline:
            time.sleep(poll_sec)
            free = _free_ram_mb()
            if free is None or free >= min_mb:
                logger.info(f"🧠 Free RAM {free} MB - starting Edge")
                return
            if time.time() - _hb >= 120:
                _hb = time.time()
                logger.info(f"🫀 still waiting for free RAM: {free} MB free, need {min_mb} MB, {int((deadline - time.time()) // 60)} min of the wait left")
        logger.warning(f"🧠 Free RAM still {free} MB after {max_min} min - starting Edge anyway (bounded wait)")

    def _driver_unavailable(reason):
        """Write the marker the keepalive honours and exit 46 (never fall back to anything)."""
        marker = os.path.join(_PROFILE_DIR, "NAUKRI_DRIVER_UNAVAILABLE")
        try:
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {reason}\n")
        except Exception:
            pass
        logger.error(f"⛔ Edge driver unavailable: {reason}. Marker written: {marker}; exiting "
                     f"{DRIVER_UNAVAILABLE_EXIT} so the keepalive retries later.")
        sys.exit(DRIVER_UNAVAILABLE_EXIT)

    def _resolve_matching_edgedriver():
        """(driver_path, edge_version) with the driver's major == installed Edge's major."""
        edge_version = _edge_browser_version()
        if not edge_version:
            _driver_unavailable("could not read the installed Edge version")
        major = edge_version.split(".")[0]
        # 1) local webdriver_manager cache, no network. Folder names can lie (a '153.0.4234.46'
        #    folder held a .48 binary), so read each binary's own file version.
        import glob as _glob
        found = []
        for exe in _glob.glob(os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "edgedriver",
                                           "win64", "*", "msedgedriver.exe")):
            v = _file_version(exe)
            if v and v.split(".")[0] == major:
                found.append((v == edge_version, tuple(int(x) for x in v.split(".")), exe, v))
        if found:
            found.sort(reverse=True)
            _, _, exe, v = found[0]
            logger.info(f"🧩 Edge {edge_version} -> msedgedriver {v} (~/.wdm cache)")
            return exe, edge_version
        # 2) download exactly this Edge build's driver from msedgedriver.microsoft.com
        try:
            from webdriver_manager.microsoft import EdgeChromiumDriverManager
            exe = EdgeChromiumDriverManager(version=edge_version).install()
            v = _file_version(exe) or ""
            if v.split(".")[0] == major:
                logger.info(f"🧩 Edge {edge_version} -> msedgedriver {v} (downloaded)")
                return exe, edge_version
            _driver_unavailable(f"downloaded driver {v or '?'} does not match Edge {edge_version}")
        except SystemExit:
            raise
        except Exception as e:
            _driver_unavailable(f"no cached driver for Edge {edge_version} and download failed: {e!r}")

    def _wait_profile_free(profile_dir, timeout=60):
        """A running Edge holds <profile>/lockfile open; a stale one (crash) can simply be removed."""
        lock = os.path.join(profile_dir, "lockfile")
        deadline = time.time() + timeout
        while os.path.exists(lock):
            try:
                os.remove(lock)
                break
            except OSError:
                if time.time() > deadline:
                    return False
                time.sleep(3)
        return True

    def _eager_setup_driver(self):
        """High-speed setup: page_load_strategy='eager' so driver.get returns instantly without waiting for ad networks."""
        try:
            logger.info("🚀 Setting up high-speed browser (eager page load strategy)...")
            from selenium import webdriver
            from selenium.webdriver.edge.service import Service as EdgeService
            options = webdriver.EdgeOptions()
            options.page_load_strategy = 'eager'  # DO NOT block on third-party ads/trackers
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            if self.config['webdriver'].get('headless', False):
                options.add_argument("--headless")

            # Persistent Edge profile: reuse login cookies so no fresh Microsoft/Naukri login window per launch
            profile_dir = self.config['webdriver'].get('user_data_dir')
            if not profile_dir:
                profile_dir = os.path.join(REPO, "browser_profile", "edge_default")
                try:
                    os.makedirs(profile_dir, exist_ok=True)
                except Exception:
                    pass
            # Written back so every later setup (recovery included) keeps the SAME saved profile.
            self.config['webdriver']['user_data_dir'] = profile_dir
            options.add_argument(f"user-data-dir={profile_dir}")
            # Per-profile extra switches (e.g. account 2's --disable-sync/--disable-extensions: its
            # Edge profile carries extensions synced from the user's Microsoft account). None by default.
            for _extra in (self.config.get('webdriver', {}).get('extra_args') or []):
                options.add_argument(str(_extra))

            driver_path, edge_version = _resolve_matching_edgedriver()
            if not _wait_profile_free(profile_dir):
                _driver_unavailable(f"Edge profile still in use after 60s: {profile_dir}")
            # 2026-10-08: a driver/browser version mismatch is the only start failure that writes the
            # driver-unavailable marker (exit 46). A slow or failed start on a memory-starved PC
            # ("Timed out receiving message from renderer", DevToolsActivePort) gets one retry after a
            # RAM wait, then exit 1, which the keepalive retries with its normal backoff.
            driver = None
            for _start_try in (1, 2):
                _wait_for_ram()
                try:
                    driver = webdriver.Edge(service=EdgeService(executable_path=driver_path), options=options)
                    break
                except Exception as e:
                    why = _exc_text(e)
                    if re.search(r"only supports (microsoft edge|chrome) version|version of microsoft edge webdriver", why, re.I):
                        _driver_unavailable(f"Edge {edge_version} rejected {driver_path}: {why}"[:500])
                    logger.warning(f"⚠️ Edge {edge_version} start {_start_try}/2 failed (driver version is fine): {why[:400]}")
                    if not _wait_profile_free(profile_dir):
                        _driver_unavailable(f"Edge profile still in use after 60s: {profile_dir}")
            if driver is None:
                logger.error("❌ Edge did not start twice in a row (not a driver mismatch); exiting 1 so the keepalive retries.")
                sys.exit(1)
            logger.info("✅ High-speed driver ready (eager mode)")
            try:
                os.remove(os.path.join(_PROFILE_DIR, "NAUKRI_DRIVER_UNAVAILABLE"))
            except OSError:
                pass
            self.driver = driver
            self.driver.set_page_load_timeout(15)
            try:
                self.driver.set_script_timeout(10)
            except Exception:
                pass
            self.driver.implicitly_wait(1)
            self.wait = WebDriverWait(self.driver, 5)
            return True
        except SystemExit:
            raise
        except Exception as e:
            _driver_unavailable(f"high-speed driver setup error: {_exc_text(e)}"[:500])

    _WDMixin.setup_driver = _eager_setup_driver

    def _robust_recover_session(self):
        """Recover from dead / crashed Edge driver and re-establish authenticated session."""
        logger.info("🔄 Robust session recovery initiated...")
        try:
            if getattr(self, "driver", None):
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
        except Exception:
            pass

        # Cool down 2 seconds for OS socket / driver cleanup
        time.sleep(2)

        for attempt in range(1, 3):
            logger.info(f"🔄 Driver recreate attempt {attempt}/2...")
            try:
                if not self.setup_driver():
                    logger.error(f"❌ setup_driver failed on attempt {attempt}")
                    time.sleep(2)
                    continue

                # Driver created, now login
                if self.login():
                    logger.info("✅ Session recovered & logged in successfully!")
                    return True
                else:
                    logger.warning(f"⚠️ Login failed on recovery attempt {attempt}")
                    try:
                        if self.driver:
                            self.driver.quit()
                    except Exception:
                        pass
                    self.driver = None
                    time.sleep(2)
            except Exception as e:
                logger.error(f"❌ Exception during recovery attempt {attempt}: {e!r}")
                try:
                    if self.driver:
                        self.driver.quit()
                except Exception:
                    pass
                self.driver = None
                time.sleep(2)

        logger.error("❌ All session recovery attempts failed.")
        return False

    _WDMixin.recover_session = _robust_recover_session

    def _instant_handle_popups(self):
        """Zero-delay popup handler with zero implicit wait."""
        try:
            self.driver.implicitly_wait(0)
            for selector in ("span.close-popup", "button.close", "div.cross-icon",
                             "[aria-label='Close']", ".crossIcon", "button[title='Close']"):
                try:
                    btns = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for b in btns:
                        if b.is_displayed():
                            try:
                                b.click()
                            except Exception:
                                self.driver.execute_script("arguments[0].click();", b)
                            break
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            try:
                self.driver.implicitly_wait(1)
            except Exception:
                pass

    AuthMixin._handle_popups = _instant_handle_popups

    def _robust_verify_login(self, timeout=12):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                current_url = (self.driver.current_url or "").lower()
                if 'nlogin' not in current_url and '/login' not in current_url and ('mnjuser' in current_url or 'naukri.com' in current_url):
                    for ind in ('.nI-gNb-drawer__icon', '.view-profile-wrapper', '[data-automation="profileDropdown"]', '.user-name', '.profile-img'):
                        els = self.driver.find_elements(By.CSS_SELECTOR, ind)
                        if els and any(el.is_displayed() for el in els):
                            logger.info("✅ Login verified via profile indicator")
                            return True
                    if 'mnjuser/homepage' in current_url:
                        logger.info("✅ Login verified via homepage redirect")
                        return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    AuthMixin._verify_login_success = _robust_verify_login

    # 2026-09-29: an account whose config stores no password (account 2; its credentials.email is
    # kept only because the keyword answer map reads it) runs on its saved browser session only.
    # If that session is gone, never type anything into the login form: write a marker the
    # keepalive reads (it then rests instead of relaunching) and exit 45.
    # 2026-09-30: account 1 stores a password, and a profile-less Edge (see the driver fix above)
    # made it type that password ~12 times since 09-14. Both accounts are now session-only unless
    # NAUKRI_ALLOW_PASSWORD_LOGIN=1 is set by hand for a deliberate, attended login; the keepalive
    # never sets it. A lost session writes the login-required marker and exits 45 instead.
    _orig_login = AuthMixin.login

    def _naukri_api_signed_in(self):
        """Naukri's own profile API answers 200 with the page's current access token. The token is read
        from document.cookie inside the page and never leaves it; only the HTTP status comes back."""
        try:
            status = self.driver.execute_async_script(r"""
                const done = arguments[arguments.length - 1];
                const tok = (document.cookie.match(/(?:^|; )nauk_at=([^;]+)/) || [])[1];
                if (!tok) { done(0); return; }
                fetch('https://www.naukri.com/cloudgateway-mynaukri/resman-aggregator-services/v1/users/self/dashboard?properties=profilePerformance',
                      {credentials: 'include', headers: {accept: 'application/json', appid: '105', systemid: 'Naukri',
                       clientid: 'd3skt0p', authorization: 'Bearer ' + tok}})
                  .then(r => done(r.status)).catch(() => done(-1));
            """)
            return status == 200, status
        except Exception as e:
            return False, _exc_text(e)[:120]

    def _save_login_evidence(self, note):
        """Screenshot + URL/title/cookie NAMES (never values) for a failed session check."""
        ev_dir = os.path.join(_PROFILE_DIR, "login_evidence")
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        info = {"time": stamp, "note": note}
        try:
            os.makedirs(ev_dir, exist_ok=True)
            info["url"] = self.driver.current_url
            info["title"] = self.driver.title
            info["cookie_names"] = sorted({c.get("name") for c in self.driver.get_cookies()})
            shot = os.path.join(ev_dir, f"{stamp}.png")
            if self.driver.save_screenshot(shot):
                info["screenshot"] = shot
        except Exception as e:
            info["error"] = _exc_text(e)[:200]
        try:
            with open(os.path.join(ev_dir, f"{stamp}.json"), "w", encoding="utf-8") as fh:
                json.dump(info, fh, indent=1)
        except Exception:
            pass
        return info

    def _session_only_login(self):
        cfg = self.config or {}
        has_creds = bool((cfg.get("naukri_credentials") or {}).get("password")
                         or (cfg.get("credentials") or {}).get("password"))
        if has_creds and os.environ.get("NAUKRI_ALLOW_PASSWORD_LOGIN") == "1":
            return _orig_login(self)
        # 2026-10-08: one 15 s look used to write a whole-day marker. When the access token has expired,
        # Naukri refreshes it through its login page; on a starved PC that took longer than 15 s, and
        # account 2 was marked logged out on 10-07 and 10-08 while Naukri was issuing it fresh tokens
        # (about 100 applications lost). Now: up to 3 looks over about 6 minutes, each a fresh
        # navigation; signed in = a profile indicator on the page or a 200 from Naukri's own profile
        # API. Evidence is saved before the marker is ever written.
        looks = 3
        last = {}
        readable = False     # did any look get an answer from the page (its URL)? Three renderer timeouts in a row are a hung browser, not a sign-out
        for look in range(1, looks + 1):
            try:
                # A tab that closed or crashed under memory pressure ("no such window", seen 10-08 04:33)
                # is not a sign-out: carry on in any tab the browser still has.
                try:
                    self.driver.current_window_handle
                except Exception:
                    handles = self.driver.window_handles
                    if handles:
                        self.driver.switch_to.window(handles[0])
                self.driver.get("https://www.naukri.com/mnjuser/homepage")
            except TimeoutException:
                logger.info(f"Session check {look}/{looks}: homepage still loading after the page timeout; checking anyway")
            except Exception as e:
                logger.warning(f"Session check failed (driver error, not a login failure): {_exc_text(e)[:300]}")
                try:
                    self.driver.quit()
                except Exception:
                    pass
                sys.exit(1)
            if self._verify_login_success(timeout=45):
                logger.info("✅ Logged in via saved profile session (session-only account)")
                return True
            ok, status = _naukri_api_signed_in(self)
            if ok:
                logger.info("✅ Logged in via saved profile session (Naukri profile API answered 200)")
                return True
            try:
                last = {"url": self.driver.current_url, "title": self.driver.title, "api": status}
                readable = True
            except Exception as e:
                last = {"error": _exc_text(e)[:200], "api": status}
            # 2026-10-21: on 10-19 15:25 account 1 was marked logged out while the browser sat on its own signed-in homepage ("Home | Mynaukri",
            # is_login cookie): the profile indicator and the API call had both timed out. A signed-out visitor is sent to the login page, so staying
            # on /mnjuser/homepage is itself the proof.
            _u = str(last.get("url", ""))
            if "/mnjuser/homepage" in _u and "login" not in _u.lower():
                logger.info(f"✅ Logged in via saved profile session (the browser stayed on the signed-in homepage {last.get('title', '')[:40]!r}; "
                            "the profile indicator and the API check were slow)")
                return True
            if look < looks:
                wait_s = 60 * look
                logger.warning(f"Session check {look}/{looks}: not signed in yet ({last}); next look in {wait_s} s")
                time.sleep(wait_s)

        if not readable:
            # 2026-10-21 16:52: account 1 was marked logged out for the day after three looks that all ended in "Timed out receiving message from
            # renderer" (the browser was starved of memory right after a reopen); the saved evidence has no URL, title or cookies at all, so the
            # browser never showed a login page. That is not a sign-out. End this run WITHOUT the day marker: the keepalive starts a fresh browser.
            logger.warning(f"Session check inconclusive: the browser never answered in {looks} looks ({last.get('error', '')[:120]}). "
                           "This is a hung browser, not a sign-out, so no login marker is written; ending this run so a fresh browser is started.")
            try:
                self.driver.quit()
            except Exception:
                pass
            sys.exit(1)

        evidence = _save_login_evidence(self, f"{looks} looks failed; last: {last}")
        marker = os.path.join(_PROFILE_DIR, f"NAUKRI_LOGIN_REQUIRED_{datetime.now():%Y-%m-%d}")
        try:
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} saved session not logged in after {looks} looks; "
                         f"evidence: {evidence.get('screenshot') or evidence}\n")
        except Exception:
            pass
        logger.error(f"⛔ Saved Naukri session is not logged in after {looks} looks (unattended runs never type a password). "
                     f"Log in once by hand in this profile's Edge; marker written: {marker}; evidence: {evidence}")
        try:
            self.driver.quit()
        except Exception:
            pass
        sys.exit(45)

    AuthMixin.login = _session_only_login

    # 2026-10-21: browser-on-demand, adaptive freshness window and heartbeats (per-profile switches in config job_search:
    # close_browser_min, idle_backoff_max_min, job_age_adaptive; all off or unchanged when absent). All times are this PC's IST clock.
    _CYCLE_STATE = os.path.join(_PROFILE_DIR, "naukri_cycle_state.json")

    def _read_last_cycle_done():
        try:
            with open(_CYCLE_STATE, "r", encoding="utf-8") as fh:
                return float(json.load(fh)["last_cycle_done"])
        except Exception:
            return None

    def _write_last_cycle_done(ts):
        try:
            with open(_CYCLE_STATE, "w", encoding="utf-8") as fh:
                json.dump({"last_cycle_done": ts, "at": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")}, fh)
        except Exception:
            pass

    def _reap_profile_browser(profile_dir):
        """Stop any Edge still holding this bot's profile after driver.quit(): a clean quit leaves none, a crash can leave some."""
        if not profile_dir:
            return
        try:
            env = dict(os.environ, NK_PROFILE_DIR=str(profile_dir))
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
                  "Where-Object { $_.CommandLine -like ('*' + $env:NK_PROFILE_DIR + '*') } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, timeout=45,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), capture_output=True)
        except Exception as e:
            logger.debug(f"profile browser sweep skipped: {e}")

    def _unattended_search_and_apply(self):
        """Session-resilient page-by-page search and application loop with 10x-100x speedup & continuous cycles."""
        keywords = self.config['job_search']['keywords']
        location = self.config['job_search']['location']
        pages_per_keyword = self.config['job_search']['pages_per_keyword']
        is_all_day = getattr(self, "_is_all_day", False)
        # 0 / null / absent = unlimited
        session_cap = self.config['job_search'].get('max_applications_per_session') or 0

        # 2026-10-19: freshness filter (see naukri_bot/modules/search.py): the searches read the recently posted jobs instead of the
        # same relevance-ordered top cards every cycle. Config job_search.job_age_days (default 3, 0 = off); NAUKRI_JOB_AGE_DAYS overrides.
        from naukri_bot.modules.search import adaptive_job_age_days, job_age_days_from, search_url
        # 2026-10-21: with job_search.job_age_adaptive the window is 1 day while the last complete cycle is under 20 h old and the full
        # configured window (3) after a longer gap, so a bot that was away never misses what was posted meanwhile.
        last_cycle_done = _read_last_cycle_done()
        job_age_days = adaptive_job_age_days(self.config, last_cycle_done, time.time())
        if job_age_days:
            logger.info(f"🆕 Freshness filter on: only jobs posted in the last {job_age_days} day(s) (Naukri jobAge={job_age_days}).")
        else:
            logger.info("🆕 Freshness filter off: searching in Naukri's relevance order only.")
        logger.info("🫀 Heartbeat on: this bot writes a log line at least every 5 minutes in every state (working, pause, waiting for "
                    "memory); the keepalive stops a bot whose log stays silent for 10 minutes.")
        try:   # the keepalive's hang guard only trusts a bot that has announced this (an older bot is silent in its long pauses)
            with open(os.path.join(_PROFILE_DIR, "NAUKRI_HEARTBEAT_ON"), "w", encoding="utf-8") as _fh:
                _fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} pid {os.getpid()}\n")
        except Exception:
            pass
        close_browser_s = int(os.environ.get("NAUKRI_CLOSE_BROWSER_MIN") or self.config['job_search'].get('close_browser_min', 0) or 0) * 60
        if close_browser_s and is_all_day:
            logger.info(f"🪫 Browser-on-demand on: the browser closes during any pause of {close_browser_s // 60} min or more and reopens "
                        f"before the next cycle.")

        apply_timestamps = []
        hourly_limit = int(os.environ.get("NAUKRI_HOURLY_CAP", "80"))
        cycle_delay_min = int(os.environ.get("NAUKRI_CYCLE_DELAY_MIN", "2"))

        # One-time live Naukri-profile read (2026-09-15): the driver only exists once we get
        # here, so this couldn't happen at the digest's first build in build_bot(). Never blocks
        # the run on failure — see naukri_bot/chatdrawer/profile_facts.py.
        try:
            from naukri_bot.chatdrawer.profile_facts import get_profile_facts, apply_facts_to_config
            _facts = get_profile_facts(self.driver)
            if _facts:
                apply_facts_to_config(self.config, _facts)
                _refresh_profile_digest(self.config)
        except Exception as e:
            logger.debug(f"Profile facts step skipped: {e}")

        def _is_card_easy_apply_fast(card):
            try:
                txt = (card.text or "").lower()
                if "apply on company site" in txt or "company site" in txt:
                    return False
                return True
            except Exception:
                return True

        cycle_count = 0
        # 2026-10-02: idle backoff. The all-day loop used to wait a flat 5 minutes between cycles whatever the last cycle found: on a day when
        # account 1 had applied to everything the searches can show (32 applications in 19.5 h, 12 of them in the first cycle) it still re-read
        # the same 10 keywords x 4 pages ~120 times, ~9,800 search-page loads, all day, with Edge and the CPU busy on a memory-starved machine.
        # Now each consecutive cycle that applies to nothing doubles the cooldown (5, 10, 20, 40, then the cap, 60 min by default) and the first
        # application resets it. NAUKRI_IDLE_BACKOFF=0 restores the flat 5 minutes; NAUKRI_IDLE_BACKOFF_MAX_MIN sets the cap.
        idle_streak = 0
        idle_backoff_on = os.environ.get("NAUKRI_IDLE_BACKOFF", "1") != "0"
        idle_backoff_max_s = max(300, int(os.environ.get("NAUKRI_IDLE_BACKOFF_MAX_MIN")
                                          or self.config['job_search'].get('idle_backoff_max_min', 60) or 60) * 60)
        while True:
            cycle_count += 1
            cycle_applies_start = self.applied
            cycle_page_fail = 0     # pages that did not load this cycle: such a cycle is not counted as complete (adaptive window)
            _age_now = adaptive_job_age_days(self.config, last_cycle_done, time.time())
            if _age_now != job_age_days:
                job_age_days = _age_now
                logger.info(f"🆕 Freshness window for this cycle: {job_age_days or 'off'} day(s).")
            # 2026-09-16: within-cycle dedupe now lives here, reset every cycle, instead of in
            # self.joblinks (which is never reset for the life of the process -- see below).
            self._cycle_seen_urls = set()

            if is_all_day:
                logger.info(f"🔄 ================= ALL-DAY SEARCH CYCLE #{cycle_count} =================")
                logger.info(f"📊 Total Applied So Far: {self.applied} | Mode: 24/7 Continuous (Hourly Pacing: {hourly_limit}/h)")
            else:
                logger.info("🔍 Starting page-by-page job search and application (session-guarded)...")

            for keyword in keywords:
                if not is_all_day and session_cap and self.applied >= session_cap:
                    logger.info(f"✋ Reached application limit ({session_cap})")
                    return

                logger.info(f"🔎 Searching for: {keyword}")

                for page in range(1, pages_per_keyword + 1):
                    if not is_all_day and session_cap and self.applied >= session_cap:
                        logger.info(f"✋ Reached application limit ({session_cap})")
                        return

                    # Daily quota / cap check: close browser and exit cleanly for the day
                    _blocked = getattr(self, "_apply_blocked_until", 0)
                    today_applied = 0
                    try:
                        import sqlite3
                        with sqlite3.connect(f"file:{getattr(self, '_db_path', None) or os.path.join(REPO, 'naukri_jobs.db')}?mode=ro", uri=True) as _c:
                            _row = _c.execute(
                                "SELECT COUNT(*) FROM applied_jobs WHERE status = 'Applied (Easy Apply)' "
                                "AND date(application_date) = date('now', 'localtime')"
                            ).fetchone()
                            today_applied = _row[0] if _row else 0
                    except Exception:
                        today_applied = self.applied
                    marker_file = os.path.join(_PROFILE_DIR, f"NAUKRI_DAILY_LIMIT_{datetime.now():%Y-%m-%d}")

                    if _blocked > time.time() or today_applied >= 50 or os.path.exists(marker_file):
                        record_naukri_daily_limit(f"Daily quota reached ({today_applied} applied today)")
                        logger.info(f"🛑 Daily quota reached for today ({today_applied} applied). Shutting down browser and exiting cleanly.")
                        try:
                            self.driver.quit()
                        except Exception:
                            pass
                        sys.exit(44)

                    # Hourly rate check in all-day mode
                    if is_all_day:
                        now_ts = time.time()
                        apply_timestamps = [t for t in apply_timestamps if now_ts - t < 3600]
                        if len(apply_timestamps) >= hourly_limit:
                            wait_needed = int(3600 - (now_ts - apply_timestamps[0])) + 15
                            if wait_needed > 0:
                                logger.info(f"⏳ Hourly application rate reached ({len(apply_timestamps)}/{hourly_limit} in last hour).")
                                logger.info(f"⏸️ Pacing pause for {wait_needed // 60}m {wait_needed % 60}s to maintain natural cadence...")
                                _pace_end = time.time() + wait_needed
                                while time.time() < _pace_end:
                                    time.sleep(min(240, max(1, _pace_end - time.time())))
                                    if time.time() < _pace_end:
                                        logger.info(f"🫀 pacing pause: {int((_pace_end - time.time()) // 60)} min left")
                                now_ts = time.time()
                                apply_timestamps = [t for t in apply_timestamps if now_ts - t < 3600]

                    # Ensure valid session before page navigation
                    if not self.ensure_valid_session():
                        logger.error("❌ Session recovery failed before page load. Halting search.")
                        return

                    url = search_url(keyword, location, page, job_age_days)

                    logger.info(f"📄 Page {page}")
                    if not _get_page_with_retry(self.driver, url, max_retries=2, timeout=6):
                        # 2026-09-30: a slow page (tonight: CPU starvation) is not a dead browser.
                        # Rebuilding the whole browser cost ~20 min; skip to the next keyword instead
                        # and only recover when the session itself is gone.
                        cycle_page_fail += 1
                        if self.check_session_validity():
                            logger.warning(f"⚠️ Search page {page} did not load but the browser session is alive; "
                                           f"skipping to the next keyword")
                            break
                        logger.warning(f"⚠️ Could not load search page {page} and the session is dead, attempting session recovery...")
                        if self.recover_session():
                            if not _get_page_with_retry(self.driver, url, max_retries=1, timeout=6):
                                logger.error(f"❌ Failed to load page {page} after recovery. Skipping page.")
                                cycle_page_fail += 1
                                continue
                        else:
                            logger.error("❌ Session recovery failed. Ending search process.")
                            return

                    try:
                        self._handle_popups()

                        job_cards = self._get_job_cards_fast()
                        if not job_cards:
                            # Search cards hydrate after body-ready under eager loads —
                            # poll briefly before abandoning the whole keyword.
                            for _retry in range(4):
                                time.sleep(2.0)
                                try:
                                    job_cards = self._get_job_cards_fast()
                                except Exception:
                                    job_cards = []
                                if job_cards:
                                    logger.info(f"✅ Job cards appeared after ~{(_retry + 1) * 2}s hydration wait")
                                    break
                        if not job_cards and job_age_days >= 3 and page == 1:
                            # 2026-10-19: a 3-day window for these keywords is never really empty, so no cards on page 1 under the
                            # filter is a slow page (first look after a restart: 4 of the first 5 keywords on each bot). One reload and a
                            # longer look before the whole keyword is skipped. (2026-10-21: only for the 3-day window; a 1-day window
                            # can really be empty for a narrow keyword.)
                            logger.info("No cards on page 1 under the freshness filter; reloading once for a longer look")
                            try:
                                self.driver.refresh()
                            except Exception:
                                pass
                            for _retry in range(6):
                                time.sleep(2.5)
                                try:
                                    job_cards = self._get_job_cards_fast()
                                except Exception:
                                    job_cards = []
                                if job_cards:
                                    logger.info(f"✅ Job cards appeared after the reload (~{(_retry + 1) * 2.5:.0f}s)")
                                    break
                        if not job_cards:
                            logger.info("No jobs found on this page, moving to next keyword.")
                            if page == 1 and job_age_days >= 3:
                                cycle_page_fail += 1     # an empty first page under the 3-day window is a page that did not load
                            break

                        page_job_links = []
                        _cycle_seen = self._cycle_seen_urls
                        _n_not_easy = _n_seen = _n_blocked = _n_irrelevant = 0
                        for card in job_cards:
                            try:
                                if not _is_card_easy_apply_fast(card):
                                    _n_not_easy += 1
                                    continue
                                job_url = self._extract_job_url_fast(card)
                                if not job_url:
                                    continue
                                if job_url in _cycle_seen:
                                    _n_seen += 1
                                    continue
                                job_id = self._extract_job_id(job_url)
                                if self.is_job_already_applied(job_id):
                                    _n_blocked += 1
                                    continue
                                if not self._is_job_relevant_fast(card):
                                    _n_irrelevant += 1
                                    continue
                                page_job_links.append(job_url)
                                _cycle_seen.add(job_url)
                            except Exception as e:
                                logger.debug(f"Error extracting job: {e}")

                        if page_job_links:
                            logger.info(f"✅ Found {len(page_job_links)} new Easy-Apply candidate jobs on this page. Applying now...")
                            prev_applied = self.applied
                            self.apply_to_jobs(page_job_links)
                            new_apps = self.applied - prev_applied
                            # 2026-09-16: extend joblinks with what was ATTEMPTED, not merely
                            # discovered. It used to be appended during discovery, before the apply
                            # attempt, and is never reset for the life of the process -- so a job
                            # that failed transiently (quota 403, an aborted chat drawer, a stale
                            # already-applied page, a Save with no verdict; none of these write a
                            # DB row) was blacklisted in memory forever with no way back. Within-
                            # cycle dedupe is now _cycle_seen_urls above, reset every cycle, so a
                            # transient failure is retried on the bot's very next pass.
                            self.joblinks.extend(page_job_links)
                            if new_apps > 0:
                                for _ in range(new_apps):
                                    apply_timestamps.append(time.time())
                            if not self.check_session_validity():
                                if not self.recover_session():
                                    logger.error("❌ Session died during apply and could not be recovered. Stopping.")
                                    return
                        else:
                            # 2026-09-16: this used to be a bare constant, logged 16 times in a row
                            # with zero attribution during the drought investigation -- it could not
                            # distinguish "0 cards on the page" from "20 cards, all rejected", nor
                            # which of 4 gates rejected them.
                            logger.info(
                                f"No new jobs on this page to apply for. (cards={len(job_cards)} "
                                f"notEasyApply={_n_not_easy} seenThisCycle={_n_seen} "
                                f"alreadyInDB={_n_blocked} notRelevant={_n_irrelevant})")
                    except (InvalidSessionIdException, WebDriverException) as we:
                        logger.warning(f"⚠️ Session error on page {page}: {we!r}")
                        cycle_page_fail += 1
                        if not self.recover_session():
                            logger.error("❌ Could not recover session. Stopping.")
                            return
                    except Exception as e:
                        logger.error(f"Error on page {page} for keyword '{keyword}': {e}")
                        cycle_page_fail += 1
                        continue

            cycle_applies = self.applied - cycle_applies_start
            logger.info(f"✅ Completed search cycle #{cycle_count}. Applied this cycle: {cycle_applies}. Cumulative: {self.applied}")
            idle_streak = 0 if cycle_applies > 0 else idle_streak + 1
            if cycle_page_fail == 0:
                last_cycle_done = time.time()
                _write_last_cycle_done(last_cycle_done)
            else:
                logger.info(f"ℹ️ {cycle_page_fail} page(s) did not load in this cycle, so it does not count as complete: the freshness "
                            f"window is measured from the last complete cycle.")

            # Trigger periodic reconciliation and tracker sync at the end of each cycle
            try:
                if getattr(self, "driver", None):
                    _reconcile_nk_periodic(self.driver, force=True)
                _run_tracker("sync")
            except Exception as e:
                logger.debug(f"End-of-cycle sync error: {e}")

            if not is_all_day:
                logger.info("🏁 Session mode complete. Exiting search loop.")
                return

            # All-day mode: idle cooldown between cycles with natural jitter
            jitter = random.randint(-180, 180)  # +/- 3 minutes
            sleep_duration = max(300, (cycle_delay_min * 60) + jitter)
            if idle_backoff_on and idle_streak > 1:
                sleep_duration = min(max(sleep_duration, idle_backoff_max_s), sleep_duration * (2 ** (idle_streak - 1)))
                sleep_duration += random.randint(0, 120)    # a little jitter on the long waits too
                logger.info(f"🪫 {idle_streak} search cycles in a row found nothing new to apply to: the cooldown backs off "
                            f"(cap {idle_backoff_max_s // 60}m) and resets to {max(300, cycle_delay_min * 60) // 60}m after the next application.")
            wake_time = datetime.fromtimestamp(time.time() + sleep_duration).strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"💤 All keywords scanned. Entering all-day cooldown of {sleep_duration // 60}m {sleep_duration % 60}s...")
            logger.info(f"⏰ Next search cycle starts at: {wake_time}")

            # 2026-10-21: browser-on-demand. The measured day (10-20): this bot kept Edge open all day while it scanned for 3 to 11 hours
            # of it; Edge is ~0.5 GB on a 16 GB PC that has 0.3 to 1 GB free. During a long pause the browser is closed (the Naukri login
            # cookies are persistent, so the saved profile logs straight back in) and reopened by the same recovery path the bot already
            # uses after a crash. Nothing is lost: a pause is at most idle_backoff_max_min and the search window is days wide.
            browser_closed = False
            if is_all_day and close_browser_s and sleep_duration >= close_browser_s and getattr(self, "driver", None):
                logger.info(f"🪫 Pause of {sleep_duration // 60} min: closing the browser to free memory; it reopens before the next cycle.")
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
                _reap_profile_browser(self.config.get('webdriver', {}).get('user_data_dir'))
                browser_closed = True

            # Sleep in intervals so process remains responsive; a heartbeat line every 4 min keeps the log moving (the keepalive stops a
            # bot whose log is silent for 10 min)
            sleep_start = time.time()
            _last_hb = sleep_start
            while time.time() - sleep_start < sleep_duration:
                rem = sleep_duration - (time.time() - sleep_start)
                time.sleep(min(30, max(1, rem)))
                if time.time() - _last_hb >= 240 and time.time() - sleep_start < sleep_duration:
                    _last_hb = time.time()
                    left = sleep_duration - (time.time() - sleep_start)
                    logger.info(f"🫀 pause: {int(left // 60)} min left, browser {'closed' if browser_closed else 'open'}")

            if browser_closed:
                logger.info("🌅 Pause over: reopening the browser for the next cycle...")
                if not self.recover_session():
                    logger.error("❌ Could not reopen the browser after the pause. Ending process.")
                    return
            # Before next cycle, verify session health
            elif not self.ensure_valid_session():
                logger.warning("⚠️ Session expired during idle cooldown. Attempting recovery before next cycle...")
                if not self.recover_session():
                    logger.error("❌ Could not recover session after idle period. Ending process.")
                    return

    def _instant_extract_job_url(self, job_card):
        """Zero-delay URL extraction using modern working selectors and zero implicit wait."""
        try:
            self.driver.implicitly_wait(0)
            for selector in ('a[href*="job-listings"]', 'a.title', '.title a', '.jobTuple-title a'):
                try:
                    links = job_card.find_elements(By.CSS_SELECTOR, selector)
                    if links:
                        href = links[0].get_attribute('href')
                        if href and 'job-listings' in href:
                            return href
                except Exception:
                    continue
            return None
        finally:
            try:
                self.driver.implicitly_wait(1)
            except Exception:
                pass

    _SearchMixin._extract_job_url_fast = _instant_extract_job_url
    _SearchMixin.search_and_apply_page_by_page = _unattended_search_and_apply
except Exception as e:
    logger.warning(f"session recovery patch skipped: {e!r}")


# ------------------------------------------------------- runner + profile + self-test
def _get_profile_paths(profile=None):
    """Compute and ensure directory structure for an isolated profile."""
    if not profile or profile in ("default", "profile1"):
        p1_cfg = os.path.join(REPO, "profiles", "profile1", "config.json")
        if profile == "profile1" and os.path.exists(p1_cfg):
            pdir = os.path.join(REPO, "profiles", "profile1")
            return {
                "profile": "profile1",
                "dir": pdir,
                "config": p1_cfg,
                "db": os.path.join(pdir, "naukri_jobs.db"),
                "log": os.path.join(pdir, "naukri_bot.log"),
                "cache": os.path.join(pdir, "selector_cache.json"),
                "edge_profile": os.path.join(pdir, "edge_profile"),
                "source_name": "naukri-profile1"
            }
        config_path = "config.local.json" if os.path.exists(os.path.join(REPO, "config.local.json")) else "config.json"
        return {
            "profile": "default",
            "dir": REPO,
            "config": os.path.join(REPO, config_path),
            "db": os.path.join(REPO, "naukri_jobs.db"),
            "log": os.path.join(REPO, "naukri_bot.log"),
            "cache": os.path.join(REPO, "selector_cache.json"),
            "edge_profile": "",
            "source_name": "naukri"
        }

    pdir = os.path.join(REPO, "profiles", profile)
    os.makedirs(pdir, exist_ok=True)
    cfg = os.path.join(pdir, "config.json")
    if not os.path.exists(cfg):
        base_cfg = "config.local.json" if os.path.exists(os.path.join(REPO, "config.local.json")) else "config.json"
        try:
            with open(os.path.join(REPO, base_cfg), "r", encoding="utf-8") as f:
                data = json.load(f)
            if "credentials" in data:
                data["credentials"]["email"] = f"{profile}_user@example.com"
                data["credentials"]["password"] = "ENTER_PASSWORD_HERE"
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Created template config for {profile} at {cfg}")
        except Exception as e:
            logger.warning(f"Could not scaffold config for {profile}: {e}")

    return {
        "profile": profile,
        "dir": pdir,
        "config": cfg,
        "db": os.path.join(pdir, "naukri_jobs.db"),
        "log": os.path.join(pdir, "naukri_bot.log"),
        "cache": os.path.join(pdir, "selector_cache.json"),
        "edge_profile": os.path.join(pdir, "edge_profile"),
        "source_name": f"naukri-{profile}"
    }


def build_bot(profile=None):
    if profile is None:
        for arg in sys.argv:
            if arg.startswith("--profile="):
                profile = arg.split("=", 1)[1].strip()
        if not profile and "--profile" in sys.argv:
            idx = sys.argv.index("--profile")
            if idx + 1 < len(sys.argv):
                profile = sys.argv[idx + 1].strip()
        if not profile:
            profile = os.environ.get("NAUKRI_PROFILE", "default").strip()

    paths = _get_profile_paths(profile)
    logger.info(f"👤 Active profile: '{paths['profile']}' (config: {paths['config']})")

    is_all_day = "--all-day" in sys.argv or os.environ.get("NAUKRI_ALL_DAY", "0").lower() in ("1", "true")

    bot = NaukriBot(config_file=paths["config"])
    bot._profile_paths = paths
    bot._db_path = paths["db"]
    bot._cache_path = paths["cache"]
    bot._session_dir = paths["dir"]
    bot._is_all_day = is_all_day
    if paths["edge_profile"]:
        bot.config.setdefault("webdriver", {})["user_data_dir"] = paths["edge_profile"]

    # 2026-09-29: NaukriBot.__init__ opened the DB and selector cache before the profile paths
    # above existed, i.e. on account 1's repo-root files. Re-open both on this profile's files.
    if paths["profile"] != "default":
        try:
            if getattr(bot, "db_conn", None):
                bot.db_conn.close()
        except Exception:
            pass
        bot.init_job_database()
        bot.load_selector_cache()

    if is_all_day:
        logger.info("☀️ All-Day 24/7 continuous apply mode enabled (hourly pacing + idle cycles)")

    # Isolated file log handler for non-default profiles (skipped when the early setup at the
    # top of this file already routes the root logger to the same file)
    if paths["profile"] != "default":
        try:
            target = os.path.abspath(paths["log"])
            if not any(os.path.abspath(getattr(h, "baseFilename", "")) == target
                       for h in logging.getLogger().handlers):
                fh = logging.FileHandler(paths["log"], encoding="utf-8")
                fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
                logging.getLogger().addHandler(fh)
            logger.info(f"📝 Profile log routed to {paths['log']}")
        except Exception as e:
            logger.warning(f"Could not attach profile file handler: {e}")

    # In-memory overlays only — files never written.
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        bot.config["gemini_api_key"] = key
    else:
        bot.config.pop("gemini_api_key", None)
    try:
        bot._init_gemini_if_configured()
    except Exception:
        pass
    try:
        _get_resume_digest(bot.config)
    except Exception as e:
        logger.debug(f"Resume digest init: {e}")
    skip_external = os.environ.get("NAUKRI_SKIP_EXTERNAL", "1") != "0"
    if skip_external:
        ApplicationMixin._apply_to_single_job = _unattended_apply_one
        logger.info("↗️ External-apply skipped by policy (no company tabs will open)")
    else:
        logger.info("↗️ External-apply ENABLED (company tabs will open for manual fill)")
    logger.info(f"🤖 Gemini fallback: {'ON' if getattr(bot, 'gemini_model', None) else 'OFF (discard unknowns)'}")
    return bot


def self_test():
    """No-browser verification: patch wiring + pure policy unit tests."""
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name, flush=True)
        if not cond:
            ok = False

    bot = build_bot()
    check("chatbot patched", ChatbotMixin._handle_chatbot is _unattended_handle_chatbot)
    check("submit Save-aware", ApplicationMixin._handle_easy_apply_submission_improved is _save_aware_submit)
    check("skip-external apply-one",
          (ApplicationMixin._apply_to_single_job is _unattended_apply_one)
          == (os.environ.get("NAUKRI_SKIP_EXTERNAL", "1") != "0"))
    check("session recovery patched", _WDMixin.recover_session is _robust_recover_session)
    check("search resilient", _SearchMixin.search_and_apply_page_by_page is _unattended_search_and_apply)
    check("cap unlimited (0/null/absent)",
          not bot.config.get("job_search", {}).get("max_applications_per_session"))
    check("all-day flag wired", hasattr(bot, "_is_all_day"))

    # Profile path isolation check
    p_def = _get_profile_paths("default")
    p_two = _get_profile_paths("profile2")
    check("profile default isolated", p_def["profile"] == "default" and p_def["db"].endswith("naukri_jobs.db"))
    check("profile profile2 isolated", p_two["profile"] == "profile2" and "profiles" in p_two["db"] and p_two["source_name"] == "naukri-profile2")

    # Resume digest caching check
    digest = _get_resume_digest(bot.config)
    check("resume digest cached", bool(digest) and "Candidate Profile:" in digest)

    # Mandatory question detection checks
    check("mandatory detect (*)", _is_mandatory("Notice Period *") is True)
    check("mandatory detect (required)", _is_mandatory("Years of experience (Required)") is True)
    check("mandatory detect (non-mandatory)", _is_mandatory("Preferred Location") is False)

    # Option matching checks
    check("match option exact", _match_llm_option("Yes", ["Yes", "No"]) == "Yes")
    check("match option substring", _match_llm_option("15 Days", ["Immediate", "15 Days", "30 Days"]) == "15 Days")

    # Offline / online LLM question answering checks against 3 realistic questions
    print("Testing _llm_answer_question with 3 realistic questions...", flush=True)
    ans1 = _llm_answer_question("Total years of experience with PySpark?", config=bot.config)
    print(f"  Q1: 'Total years of experience with PySpark?' -> '{ans1}'", flush=True)
    check("LLM Q1 (years with PySpark)", bool(ans1) and any(c.isdigit() for c in str(ans1)))

    ans2 = _llm_answer_question("Are you willing to work in Bengaluru?", options=["Yes", "No"], config=bot.config)
    print(f"  Q2: 'Are you willing to work in Bengaluru?' -> '{ans2}'", flush=True)
    check("LLM Q2 (work in Bengaluru)", ans2 == "Yes")

    ans3 = _llm_answer_question("Notice period?", options=["Immediate", "15 Days", "30 Days", "60 Days", "90 Days"], config=bot.config)
    print(f"  Q3: 'Notice period?' (options) -> '{ans3}'", flush=True)
    check("LLM Q3 (notice period options)", ans3 in ["Immediate", "15 Days", "30 Days"])

    cities = ["Coimbatore", "Bengaluru", "Pune", "USA"]
    locs = ["bengaluru", "bangalore"]
    d, p = classify_radio("Are you currently living in or ready to relocate to Bengaluru?",
                          ["Yes", "No"], locs)
    check("relocate Yes/No -> select Yes", d == "select" and p == "Yes")
    # 2026-09-24: employer/criminal/PII questions now go to the LLM (see DEMOGRAPHIC_SUBSTR).
    d, p = classify_radio("Have you been employed by Berkadia or any entity before?",
                          ["Yes", "No"], locs)
    check("employer question -> gemini", d == "gemini")
    d, p = classify_radio("Which city do you live in?", cities, locs)
    check("city list -> Bengaluru", d == "select" and p == "Bengaluru")
    d, p = classify_radio("Gender", ["Male", "Female", "I choose not to self-identify"], locs)
    check("demographic -> neutral option", d == "select" and p == "I choose not to self-identify")
    d, p = classify_radio("Gender", ["Male", "Female"], locs)
    check("demographic without neutral -> discard", d == "discard")
    d, p = classify_radio("Preferred work location?", ["Pune", "USA"], ["chennai"])
    check("no matching city -> gemini/discard, never wrong city",
          (d == "select" and p in ("Pune", "USA")) is False)
    d, p = classify_radio("Years of experience with ETL?", [], locs)
    check("no options -> discard", d == "discard")
    try:
        ans = ChatbotMixin._get_keyword_answer(bot, "What is your expected CTC?")
        check("keyword map intact", bool(ans))
    except Exception:
        check("keyword map intact", False)

    print("SELF-TEST " + ("PASSED" if ok else "FAILED"), flush=True)
    return ok


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    # Single-flight mutex: a same-second duplicate interpreter spawns per launch and
    # fights over the Edge profile/DB/log. Loser exits cleanly here, before touching anything.
    try:
        import ctypes as _ctypes

        _kernel32 = _ctypes.windll.kernel32
        _mutex_name = "Global\\NaukriAutoApplyBotSingleFlight" + ("" if _IS_DEFAULT_PROFILE else f"-{_EARLY_PROFILE}")
        _mutex = _kernel32.CreateMutexW(None, True, _mutex_name)
        _err = _kernel32.GetLastError()
        if not _mutex or _err == 183:  # 183 = already exists -> another instance holds it
            try:
                if _mutex:
                    _kernel32.CloseHandle(_mutex)
            except Exception:
                pass
            print("Another bot instance already holds the single-flight mutex - exiting duplicate.", flush=True)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as _e:
        print(f"Single-flight mutex unavailable ({_e}) - proceeding without it.", flush=True)
    bot = build_bot()
    sys.exit(0 if bot.run() else 1)

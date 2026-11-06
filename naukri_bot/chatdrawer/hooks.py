"""The seam between chatdrawer (v2) and the v1 answer stack it reuses rather than re-derives.

v1 (run_naukri_unattended.py) already has a working answer stack: safe-default policy tables,
a local-proxy/OpenCode-Zen/Gemini LLM chain with a session memo, and stale-question guards.
Rebuilding that would be pure risk for no gain, so v2 takes it by injection at install() time
instead of importing run_naukri_unattended (which would be a circular import — that module
imports this package to call install()).
"""
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


@dataclass
class Hooks:
    # (question, options, location_variants) -> ("select", option) | ("gemini", None) | ("discard", reason)
    classify_radio: Callable[[str, List[str], List[str]], Tuple[str, Optional[str]]]
    # (question_text, options=None, config=None) -> answer string | chosen option | None
    llm_answer: Callable[..., Optional[str]]
    # (self_bot, question) -> answer string | None  (rules -> QA_DICT -> LLM, bound to the bot instance)
    text_answer: Callable[[object, str], Optional[str]]
    # (config) -> list of acceptable location strings, lowercase
    location_variants: Callable[[dict], List[str]]
    # (question, options) -> True if they look like a stale/mismatched pairing
    q_opts_mismatch: Callable[[str, List[str]], bool]
    resume_path: str
    # (record dict) -> None: one line per chat window for the account's telemetry file (2026-10-08: v2 wrote
    # none, so nobody could count which screening questions were answered how). Optional.
    telemetry: Optional[Callable[[dict], None]] = None

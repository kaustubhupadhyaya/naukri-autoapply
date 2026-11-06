"""Dedicated chat-drawer module (v2): the last, most fragile stage of an application, split out
of the 3400-line run_naukri_unattended.py into its own package with one model of the screen
(perception.py, probe.js — shared with naukri_watch), one decision function (answers.py, reusing
v1's policy/LLM stack by injection), one set of act-then-verify fillers (fillers.py), and one
conversation loop that trusts only Naukri's own verdict (engine.py).

Usage (wired near the bottom of run_naukri_unattended.py, right where v1 installs its own
ChatbotMixin._handle_chatbot):

    from naukri_bot.chatdrawer import install, Hooks
    install(ChatbotMixin, Hooks(classify_radio, _llm_answer_question, _unattended_text_answer,
            _location_variants, _q_opts_mismatch, RESUME_FILE))

install() wraps whatever `_handle_chatbot` is already on the mixin (v1's handler) rather than
replacing it: v2 only runs when explicitly turned on, per bot instance, via
`config.bot_behavior.chat_engine: "v2"` or env `NAUKRI_CHAT_ENGINE=v2` — v1 stays the default
so this can be canary-tested against a couple of real applications before it drives all of them.
Both paths honor the same contract: set self._chatbot_detected / _chatbot_ok /
_chatbot_mandatory_unanswered / _chatbot_unanswered, return True iff the application was
actually confirmed applied (or there was no drawer at all).
"""
import logging
import os

from . import engine
from .hooks import Hooks

__all__ = ["install", "Hooks", "engine"]

logger = logging.getLogger("naukri_unattended")


def _use_v2(bot):
    if os.environ.get("NAUKRI_CHAT_ENGINE", "").strip().lower() == "v2":
        return True
    try:
        return str((bot.config or {}).get("bot_behavior", {}).get("chat_engine", "")).strip().lower() == "v2"
    except Exception:
        return False


def install(chatbot_mixin, hooks, budgets=None):
    if getattr(chatbot_mixin, "_chatdrawer_v2_installed", False):
        return
    v1_handler = chatbot_mixin._handle_chatbot

    def _dispatch_handle_chatbot(self, timeout=12):
        if not _use_v2(self):
            return v1_handler(self, timeout=timeout)

        result = engine.run(self, hooks, budgets=budgets)
        self._chat_v2_result = result
        logger.info(f"[chat-v2] outcome={result.outcome} job={result.job_id} reason='{result.reason[:120]}' "
                    f"answered={len(result.qa)} ms={result.ms:.0f}")
        for i, x in enumerate(result.qa, 1):
            logger.info(f"[chat-v2] q#{i} kind={x['kind']} src={x['source']} verify={'ok' if x['verify_ok'] else 'FAIL'} "
                        f"ms={x['ms']:.0f} q='{(x['q'] or '')[:70]}' a='{str(x.get('answer'))[:60]}'")
        if getattr(hooks, "telemetry", None) and result.outcome != engine.NO_DRAWER:
            try:
                hooks.telemetry({"format": "chat_v2", "job_id": result.job_id, "outcome": result.outcome,
                                 "reason": (result.reason or "")[:200],
                                 "qa": [{"q": (x.get("q") or "")[:160], "kind": x.get("kind"),
                                         "answer": None if x.get("answer") is None else str(x.get("answer"))[:100],
                                         "source": x.get("source"), "verify_ok": bool(x.get("verify_ok"))}
                                        for x in result.qa]})
            except Exception as e:
                logger.debug(f"[chat-v2] telemetry write failed: {e!r}")

        if result.outcome == engine.NO_DRAWER:
            self._chatbot_detected = False
            self._chatbot_ok = True
            self._chatbot_mandatory_unanswered = []
            self._chatbot_unanswered = []
            return True

        self._chatbot_detected = True
        self._chatbot_ok = result.outcome == engine.APPLIED
        self._chatbot_mandatory_unanswered = [] if result.outcome == engine.APPLIED else [result.reason]
        self._chatbot_unanswered = [x["q"] for x in result.qa if not x["verify_ok"]]
        return result.outcome == engine.APPLIED

    chatbot_mixin._handle_chatbot = _dispatch_handle_chatbot
    chatbot_mixin._chatdrawer_v2_installed = True
    logger.info("🧩 chatdrawer v2 installed (dispatch: config.bot_behavior.chat_engine=='v2' "
                "or env NAUKRI_CHAT_ENGINE=v2; v1 stays the default otherwise)")

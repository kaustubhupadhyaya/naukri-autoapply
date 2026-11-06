"""The dedicated chat-drawer conversation loop.

Naukri's chat protocol, confirmed by live sampling (2026-09-15): "Save" (div.sendMsgbtn_container)
SENDS the current answer and the next question streams in; there is no separate final submit —
after the last answer Naukri auto-submits on its own and navigates to /myapply/... with
multiApplyResp={"<jobId>": 200|202} in the URL, or shows the "Oops! ... incomplete information"
banner. So the only things this loop ever does are: read the active question, answer it, verify
the answer registered, press Save, and wait for the transcript (or the page) to move — then ask
Naukri what happened. It never closes the drawer and never guesses at success.
"""
import logging
import time

from . import fillers
from .answers import Discard, decide
from .perception import Perception

logger = logging.getLogger("naukri_unattended")

APPLIED = "APPLIED"
REJECTED_INCOMPLETE = "REJECTED_INCOMPLETE"
EXTERNAL = "EXTERNAL"
NAUKRI_ERROR = "NAUKRI_ERROR"
UNKNOWN = "UNKNOWN"
NO_DRAWER = "NO_DRAWER"

DEFAULT_BUDGETS = {
    "drawer_appear_s": 8,
    "drawer_total_s": 150,
    "settle_s": 12,
    "quiet_ms": 500,
    "verify_s": 3,
    "save_wait_s": 5,
    "save_advance_s": 8,
    "verdict_wait_s": 12,
    "max_stuck": 3,
    "idle_s": 20,
}


class Result:
    def __init__(self, outcome, reason, qa, ms, job_id=None):
        self.outcome = outcome
        self.reason = reason
        self.qa = qa
        self.ms = ms
        self.job_id = job_id

    @property
    def applied(self):
        return self.outcome == APPLIED

    def __repr__(self):
        return f"Result({self.outcome}, {self.reason!r}, {len(self.qa)} answered, {self.ms:.0f}ms)"


def _verdict_from_snap(snap):
    if snap is None:
        return None
    resp = snap.apply_resp or {}
    code = None
    if resp:
        code = next(iter(resp.values()), None)
        try:
            code = int(code)
        except (TypeError, ValueError):
            pass
    if snap.banner("reject") or code == 406:
        return REJECTED_INCOMPLETE
    if snap.banner("error"):
        return NAUKRI_ERROR
    if snap.banner("redirect") or code == 202:
        return EXTERNAL
    if snap.banner("success") or code == 200:
        return APPLIED
    return None


def _qkey(t):
    return " ".join((t or "").strip().lower().split())[:120]


def _pick_widget(d):
    """The widget the drawer wants answered next: prefer one matching the active bot bubble
    and not yet filled; a lone composer is only a candidate while there IS an active question
    (Naukri's own placeholder text otherwise makes an idle composer look like a target)."""
    widgets = [w for w in d.get("widgets", []) if w.get("kind") != "chips"]
    active = d.get("activeQuestion") or ""
    unfilled = [w for w in widgets if w.get("filled") is False]
    if not unfilled:
        return None
    matching = [w for w in unfilled if active and _qkey(w.get("q")) and
                (_qkey(w.get("q")) in _qkey(active) or _qkey(active) in _qkey(w.get("q")))]
    if matching:
        return matching[-1]
    non_composer = [w for w in unfilled if w.get("kind") != "composer"]
    if non_composer:
        return non_composer[-1]
    if active:
        return unfilled[-1]
    return None


def _resume_widget(d):
    return next((w for w in d.get("widgets", []) if w.get("kind") == "file" and not w.get("filled")), None)


def _upload_chip(d, kind_words):
    chips = next((w for w in d.get("widgets", []) if w.get("kind") == "chips"), None)
    if not chips:
        return None
    for o in chips.get("options", []):
        t = (o.get("text") or "").lower()
        if any(k in t for k in kind_words):
            return o
    return None


def _verify(widget_before, snap_after, ref_q):
    """Compare the widget's `filled` state / value in a fresh snapshot. Matches by question
    text since refs from the pre-fill snapshot are not valid in a re-probed one."""
    for w in snap_after.widgets:
        if w.get("kind") == widget_before.get("kind") and _qkey(w.get("q")) == _qkey(ref_q):
            return w
    # composer's q can shift once the answer is sent and a new question streams in;
    # match by kind alone when there's exactly one candidate of that kind.
    same_kind = [w for w in snap_after.widgets if w.get("kind") == widget_before.get("kind")]
    return same_kind[0] if len(same_kind) == 1 else None


def _handle_resume(driver, p, snap, hooks, budgets, qa):
    d = snap.drawer
    widget = _resume_widget(d)
    if widget is None:
        return snap, False
    existing = _upload_chip(d, ("existing", "profile", "saved", "attached", "keep", "current", "continue"))
    t0 = time.time()
    if existing is not None:
        r = fillers._click_ladder(driver, [snap.el(existing.get("ref"))])
        qa.append({"q": "resume", "kind": "chips", "answer": existing.get("text"),
                   "source": "existing_resume", "verify_ok": r.ok, "ms": (time.time() - t0) * 1000})
        snap2 = p.wait_until(lambda s: s.phase != "job" or (s.drawer and s.drawer.get("mutN", 0) > d.get("mutN", 0)),
                             budgets["save_advance_s"])
        return snap2 or snap, True
    upload = _upload_chip(d, ("upload",))
    if upload is not None:
        fillers._click_ladder(driver, [snap.el(upload.get("ref"))])
        snap = p.wait_until(lambda s: s.drawer and _resume_widget(s.drawer) is not None,
                            budgets["save_advance_s"]) or snap
        d = snap.drawer
        widget = _resume_widget(d) or widget
    fr = fillers.fill_file(driver, snap, widget, hooks.resume_path)
    qa.append({"q": "resume", "kind": "file", "answer": hooks.resume_path, "source": "config",
              "verify_ok": fr.ok, "ms": (time.time() - t0) * 1000,
              "note": "unverifiable via files.length (Naukri may clear it after reading); "
                      "success is judged by whether the drawer advances"})
    snap2 = p.wait_until(lambda s: s.drawer and s.drawer.get("mutN", 0) > d.get("mutN", 0),
                         budgets["save_advance_s"]) or snap
    return snap2, True


def run(bot, hooks, budgets=None):
    b = dict(DEFAULT_BUDGETS)
    if budgets:
        b.update(budgets)
    driver = bot.driver
    config = bot.config
    p = Perception(driver)
    t0 = time.time()
    qa = []

    def elapsed():
        return (time.time() - t0) * 1000.0

    snap = p.wait_until(lambda s: s.drawer or s.phase == "result", b["drawer_appear_s"])
    if snap is None:
        return Result(UNKNOWN, "no readable page", qa, elapsed())
    if not snap.drawer:
        v = _verdict_from_snap(snap)
        return Result(v or NO_DRAWER, "no chat drawer", qa, elapsed(), job_id=snap.job_id)

    loc_variants = hooks.location_variants(config)
    job_id = snap.job_id
    deadline = t0 + b["drawer_total_s"]
    acted, idle_since = {}, None  # qsig -> times v2 answered it; start of a do-nothing stretch

    while time.time() < deadline:
        snap = p.wait_until(lambda s: s.phase == "result" or not s.drawer or
                            (Perception.settled(s, b["quiet_ms"]) and (s.drawer.get("activeQuestion") or s.drawer.get("save"))),
                            b["settle_s"], poll=0.15)
        if snap is None:
            return Result(UNKNOWN, "lost the page mid-conversation", qa, elapsed(), job_id)
        if snap.phase == "result" or not snap.drawer:
            v = _verdict_from_snap(snap)
            if v is None:
                snap2 = p.wait_until(lambda s: _verdict_from_snap(s) is not None, b["verdict_wait_s"])
                v = _verdict_from_snap(snap2) if snap2 else None
                snap = snap2 or snap
            return Result(v or UNKNOWN, "result page" if snap.phase == "result" else "drawer closed",
                          qa, elapsed(), snap.job_id or job_id)

        d = snap.drawer
        if d.get("errorChip"):
            return Result(f"ABORTED_CHATBOT_ERROR", "drawer shows a 'Try again' error chip", qa, elapsed(), job_id)

        qsig = d.get("qsig", "")
        # "Stuck" only counts rounds where v2 actually answered this question and the drawer
        # did not move on. Idle re-probes are bounded by idle_s instead (the first canary
        # aborted a consent question in 4s by counting three idle passes as "stuck").
        if acted.get(qsig, 0) >= b["max_stuck"]:
            return Result("ABORTED_STUCK", f"answered '{(d.get('activeQuestion') or '')[:60]}' {acted[qsig]}x "
                          "and the drawer never moved on", qa, elapsed(), job_id)

        snap, did_resume = _handle_resume(driver, p, snap, hooks, b, qa)
        if did_resume:
            acted[qsig] = acted.get(qsig, 0) + 1
            idle_since = None
            continue
        d = snap.drawer

        widget = _pick_widget(d)
        chips = next((w for w in d.get("widgets", []) if w.get("kind") == "chips"), None)
        if widget is None and chips is not None and d.get("activeQuestion"):
            # Quick-reply question (live example: consent -> a lone [Yes] chip, Save hidden and
            # disabled). The chip click itself sends the answer; success = the transcript moves.
            chips = dict(chips, q=d.get("activeQuestion"))
            t_a = time.time()
            ans = decide(bot, chips, hooks, config, loc_variants)
            if isinstance(ans, Discard):
                qa.append({"q": chips["q"][:160], "kind": "chips", "answer": None, "source": ans.code,
                           "verify_ok": False, "ms": (time.time() - t_a) * 1000, "note": ans.detail})
                logger.warning(f"[chat-v2] discard: {ans.code} - {ans.detail[:120]}")
                return Result(f"ABORTED_{ans.code}", ans.detail, qa, elapsed(), job_id)
            before_mut, before_msgs = d.get("mutN", 0), d.get("msgCount", 0)
            fr = fillers.fill(driver, snap, chips, ans)
            moved = p.wait_until(
                lambda s: s.phase == "result" or not s.drawer or
                (s.drawer.get("mutN", 0) > before_mut and
                 (s.drawer.get("msgCount", 0) > before_msgs or s.drawer.get("qsig") != qsig)),
                b["save_advance_s"], poll=0.2)
            ok = bool(fr.ok and moved is not None and (
                moved.phase == "result" or not moved.drawer or moved.drawer.get("msgCount", 0) > before_msgs
                or moved.drawer.get("qsig") != qsig))
            ms = (time.time() - t_a) * 1000
            qa.append({"q": chips["q"][:160], "kind": "chips", "answer": ans.value, "source": ans.source,
                       "verify_ok": ok, "ms": ms, "channel": fr.channel})
            logger.info(f"[chat-v2] q kind=chips src={ans.source} verify={'ok' if ok else 'FAIL'} ch={fr.channel} "
                        f"ms={ms:.0f} q='{chips['q'][:70]}' a='{str(ans.value)[:60]}'")
            acted[qsig] = acted.get(qsig, 0) + 1
            idle_since = None
            continue
        if widget is None:
            if d.get("save") and d["save"].get("enabled"):
                pass  # nothing left to fill, fall through to Save
            else:
                idle_since = idle_since or time.time()
                if time.time() - idle_since > b["idle_s"]:
                    kinds = [w.get("kind") for w in d.get("widgets", [])]
                    return Result("ABORTED_IDLE",
                                  f"nothing answerable for {b['idle_s']}s: widgets={kinds} "
                                  f"active='{(d.get('activeQuestion') or '')[:60]}' "
                                  f"save_enabled={bool(d.get('save') and d['save'].get('enabled'))}",
                                  qa, elapsed(), job_id)
                time.sleep(0.5)
                continue
        else:
            idle_since = None
            t_a = time.time()
            ans = decide(bot, widget, hooks, config, loc_variants)
            if isinstance(ans, Discard):
                qa.append({"q": widget.get("q", "")[:160], "kind": widget["kind"], "answer": None,
                          "source": ans.code, "verify_ok": False, "ms": (time.time() - t_a) * 1000,
                          "note": ans.detail})
                logger.warning(f"[chat-v2] discard: {ans.code} - {ans.detail[:120]}")
                return Result(f"ABORTED_{ans.code}", ans.detail, qa, elapsed(), job_id)

            fr = fillers.fill(driver, snap, widget, ans)
            verify_snap = p.wait_until(lambda s: s.drawer is not None, b["verify_s"], poll=0.3) or snap
            check = _verify(widget, verify_snap, widget.get("q", "")) if verify_snap.drawer else None
            ok = fr.ok and bool(check) and check.get("filled") is not False
            if not ok:  # one retry against a fresh snapshot before giving up on this question
                fresh_widget = _verify(widget, verify_snap, widget.get("q", "")) if verify_snap.drawer else None
                if fresh_widget is not None:
                    fr2 = fillers.fill(driver, verify_snap, fresh_widget, ans)
                    verify_snap2 = p.wait_until(lambda s: s.drawer is not None, b["verify_s"], poll=0.3) or verify_snap
                    check2 = _verify(widget, verify_snap2, widget.get("q", ""))
                    ok = fr2.ok and bool(check2) and check2.get("filled") is not False
                    fr = fr2
            ms = (time.time() - t_a) * 1000
            qa.append({"q": widget.get("q", "")[:160], "kind": widget["kind"], "answer": ans.value,
                      "source": ans.source, "verify_ok": ok, "ms": ms, "channel": fr.channel})
            logger.info(f"[chat-v2] q kind={widget['kind']} src={ans.source} verify={'ok' if ok else 'FAIL'} "
                       f"ch={fr.channel} ms={ms:.0f} q='{(widget.get('q') or '')[:70]}' a='{str(ans.value)[:60]}'")
            if not ok:
                return Result("ABORTED_FILL_FAILED",
                              f"could not verify the answer registered: {fr.note or 'no widget match after fill'}",
                              qa, elapsed(), job_id)
            snap = verify_snap

        snap = p.wait_until(lambda s: s.drawer and s.drawer.get("save") and s.drawer["save"].get("enabled"),
                            b["save_wait_s"], poll=0.2) or snap
        d = snap.drawer
        save = d.get("save") if d else None
        if not save or not save.get("enabled"):
            continue  # more questions must still be pending; loop back and re-probe
        before_mut, before_msgs = d.get("mutN", 0), d.get("msgCount", 0)
        r = fillers._click_ladder(driver, [snap.el(save.get("leafRef")), snap.el(save.get("ref")),
                                           snap.el(save.get("containerRef"))])
        if not r.ok:
            return Result("ABORTED_SAVE_CLICK_FAILED", r.note, qa, elapsed(), job_id)
        acted[qsig] = acted.get(qsig, 0) + 1
        idle_since = None
        snap = p.wait_until(
            lambda s: s.phase == "result" or not s.drawer or
            (s.drawer.get("mutN", 0) > before_mut and (s.drawer.get("msgCount", 0) > before_msgs or
             s.drawer.get("qsig") != qsig)),
            b["save_advance_s"], poll=0.2) or snap

    return Result(UNKNOWN, "drawer total-time budget exceeded", qa, elapsed(), job_id)

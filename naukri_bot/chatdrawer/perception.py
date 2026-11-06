"""One model of the screen, shared with the failure watcher (naukri_watch).

`probe.js` is a single read-only JS function that returns a JSON model of the current page
plus (when asked) the WebElements each `ref` in that model points at. `Perception` runs it
through Selenium and gives the engine a `Snap` to read and a `wait_until` to replace fixed
sleeps: instead of "sleep 10s then check", callers wait on a real signal (the drawer's own
`quietMs`/`mutN` mutation clock, a message count, a save-enabled flag...).
"""
import json
import os
import time

PROBE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.js")
_PROBE_SRC = open(PROBE_PATH, encoding="utf-8").read()
_EXPR = "return (" + _PROBE_SRC + ")(arguments[0]);"


class Snap:
    """One probe result: model dict + the WebElements its `ref`/`inputRef`/`leafRef` index."""

    __slots__ = ("model", "els", "ts")

    def __init__(self, model, els):
        self.model = model
        self.els = els
        self.ts = time.time()

    def el(self, ref):
        if ref is None or ref < 0 or ref >= len(self.els):
            return None
        return self.els[ref]

    # -- convenience passthroughs
    @property
    def phase(self):
        return self.model.get("phase")

    @property
    def job_id(self):
        return self.model.get("jobId")

    @property
    def drawer(self):
        return self.model.get("drawer")

    @property
    def widgets(self):
        d = self.drawer
        return d.get("widgets", []) if d else []

    @property
    def save(self):
        d = self.drawer
        return d.get("save") if d else None

    @property
    def banners(self):
        return self.model.get("banners", [])

    @property
    def apply_resp(self):
        return self.model.get("applyResp")

    def banner(self, kind):
        return next((b["text"] for b in self.banners if b["kind"] == kind), None)


class Perception:
    def __init__(self, driver):
        self.driver = driver

    def snap(self, with_html=False):
        opts = {"withElements": True}
        if with_html:
            opts["withHtml"] = True
        try:
            self.driver.implicitly_wait(0)
            result = self.driver.execute_script(_EXPR, opts)
        finally:
            try:
                self.driver.implicitly_wait(1)
            except Exception:
                pass
        if not result or "json" not in result:
            return None
        return Snap(json.loads(result["json"]), result.get("els", []))

    def wait_until(self, predicate, budget_s, poll=0.2, with_html=False):
        """Re-probe until predicate(snap) is truthy or budget_s elapses. Returns the last Snap
        seen (which may fail the predicate — callers check), or None if the page never
        produced a readable snapshot at all (mid-navigation, driver hiccup)."""
        deadline = time.time() + budget_s
        last = None
        while True:
            try:
                last = self.snap(with_html=with_html) or last
            except Exception:
                pass
            if last is not None:
                try:
                    if predicate(last):
                        return last
                except Exception:
                    pass
            if time.time() >= deadline:
                return last
            time.sleep(poll)

    @staticmethod
    def settled(snap, quiet_ms=400):
        d = snap.drawer if snap else None
        return bool(d) and not d.get("busy") and d.get("quietMs", 0) >= quiet_ms

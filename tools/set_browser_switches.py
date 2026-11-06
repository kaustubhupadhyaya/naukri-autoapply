"""Turn the browser-on-demand switches on or off for ONE Naukri profile (2026-10-21). Prints only the keys it changes, never credentials.

    python tools\\set_browser_switches.py naukri2 5       close Edge in every pause of 5 min or more (the shortest pause is 5 min, so: every
                                                         pause), pause cap 120 min, adaptive 1-day window (and removes the lean-Edge flags,
                                                         which gave no memory gain). 30 was tried first on 2026-10-21 and left Edge open for
                                                         about an hour after every restart or application, because the cooldown starts at
                                                         5 min and only doubles after idle cycles (5, 5, 11, 20, 41 ...).
    python tools\\set_browser_switches.py default 5       the same for account 1 (config.local.json)
    python tools\\set_browser_switches.py naukri2 --off   remove all of it again

The bot reads its config at start, so restart that account's chain afterwards: pause-appliers 2m --only naukri2   (or naukri1).
What each key does is in run_naukri_unattended.py (search for "browser-on-demand") and naukri_bot/modules/search.py (adaptive_job_age_days).
"""
import hashlib
import json
import os
import sys

NK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_KEYS = ("job_age_adaptive", "job_age_narrow_days", "job_age_widen_after_hours", "close_browser_min", "idle_backoff_max_min")
LEAN = ["--blink-settings=imagesEnabled=false", "--disable-background-networking", "--disable-component-update",
        "--disable-default-apps", "--no-first-run"]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    profile, arg = sys.argv[1], sys.argv[2]
    path = os.path.join(NK, "config.local.json") if profile == "default" else os.path.join(NK, "profiles", profile, "config.json")
    raw = open(path, "rb").read()
    cfg = json.loads(raw.decode("utf-8-sig"))
    rest = lambda: hashlib.sha256(json.dumps({k: v for k, v in cfg.items() if k not in ("job_search", "webdriver")}, sort_keys=True).encode()).hexdigest()
    before = rest()
    js, wd = cfg.setdefault("job_search", {}), cfg.setdefault("webdriver", {})
    changed = []
    if arg == "--off":
        for k in JS_KEYS:
            if k in js:
                changed.append(f"job_search.{k}: {js.pop(k)!r} removed")
        args = [a for a in (wd.get("extra_args") or []) if a not in LEAN]
        if args != (wd.get("extra_args") or []):
            wd["extra_args"] = args
            changed.append("webdriver.extra_args: lean flags removed")
    else:
        want = {"job_age_adaptive": True, "job_age_narrow_days": 1, "job_age_widen_after_hours": 20,
                "close_browser_min": int(arg), "idle_backoff_max_min": 120}
        for k, v in want.items():
            if js.get(k) != v:
                changed.append(f"job_search.{k}: {js.get(k)!r} -> {v!r}")
                js[k] = v
        # The lean-Edge flags were tried on Naukri 2 on 2026-10-21 and gave no measurable memory gain, so they are never added; any
        # that an earlier run of this tool left behind are removed.
        have = list(wd.get("extra_args") or [])
        keep = [a for a in have if a not in LEAN]
        if keep != have:
            wd["extra_args"] = keep
            changed.append("webdriver.extra_args: lean flags removed")
    assert before == rest(), "everything outside job_search and webdriver must stay identical"
    if changed:
        open(path, "wb").write(json.dumps(cfg, indent=2 if b'\n  "' in raw else 4, ensure_ascii=False).encode("utf-8"))
    print(profile, "->", os.path.relpath(path, NK))
    print("\n".join("  " + c for c in changed) or "  (nothing to change)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

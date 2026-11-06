# Why the Naukri auto-apply fixes kept failing (2026-09-07 → 09-15)

A review of the fix history in the OpenCode sessions and the 2026-09-15 handoff. Every claim below
was checked against the live bot on 2026-09-15: the log, the DB, and read-only CDP sampling of
the bot's own Edge.

## What was actually happening

| Symptom people saw | What was really going on | Evidence |
|---|---|---|
| "0 confirmed applies" | Real successes were logged as discards: the success check never read Naukri's own verdict | `debug_drawer_empty.png` shows `Applied to "Informatica ETL Developer"` on a "Discarding" path; 3+ more found in the log |
| 17 "Applied (Easy Apply)" rows in 25 min | The bot clicked **Apply on company site** (Naukri answers `multiApplyResp` 202, "redirected to the company website"), then clicked a job-card **bookmark "Save"** page-wide and counted it as submitted | Result URLs sampled live; watcher: 17 `FALSE_CONFIRM`; rows relabelled |
| "Oops! Your application was not accepted due to incomplete information" | The bot **closed the chat drawer mid-questionnaire**. Naukri then submits what it has and answers 406 | Live watch: 3/3 rejections followed a click on the drawer's close X |
| "Switching jobs without pressing Apply" | Never proven against the live Apply button; later "fixes" broadened the selectors until they matched the external button | Selectors in `EASY_APPLY_SELECTORS` / `_js_click_apply_fallback` |
| "A twin process keeps spawning" | That's the normal Windows venv redirector: `.venv\Scripts\python.exe` always starts the base Python as a child. The "duplicate killer" murdered the real worker | Reproduced; keepalive log shows the 2-minute death loop it caused |

Naukri's protocol, as observed: in the chat drawer, **Save sends the current answer** and the next
question streams in. After the last answer Naukri submits on its own and goes to
`/myapply/...?multiApplyResp={"<jobId>": code}`. Codes: **200** applied, **202** external
redirect, **406** rejected for incomplete answers. An empty `{}` with "There was an error while
processing your job application" is a Naukri-side error.

## The structural faults

1. **The success oracle was invented, not read.** Success meant "URL contains `applied`/`confirmation`",
   "body contains `apply confirmation`" (present on 202 pages too), or "no `button[type=submit]` on
   the page" (`_verify_application_submitted`). Naukri's own code was never checked. Every later
   diagnosis ("zero applies", "the resume is the prime blocker") rested on this blind oracle.
2. **Self-certifying verification.** Fixes were declared done on `compile PASS`, `--self-test`, or log
   lines the bot wrote about itself. At least 3 "fixed" claims were contradicted by the same night's log.
3. **The chat protocol was misread.** The per-answer Save was treated as the final submit. "Drawer
   still open after Save" became "click failed", then retry, then discard, then `_close_chatbot_drawer`.
   That close is what produced the rejections.
4. **No single model of the screen.** Each widget handler (chips, text, radio, select) had its own
   selectors and its own idea of which question it belonged to (`qtexts[-1]` vs container-first). That
   caused stale question/option pairing, e.g. a notice-period option chosen for a years question.
5. **Shotgun actions with unconditional success.** `_click_radio_option` returned True either way.
   Sending text fired three channels at once (the nearest button/svg, a synthetic Enter, and RETURN).
6. **Patch layering.** Every symptom got another gate or sleep: settle 10 s/8 s, content grace 15 s,
   deadline 100 s, implicit wait 15→1→0 s, LLM tokens 30→1024→256. Nothing was removed, and the
   speed fixes and correctness fixes pulled against each other.
7. **Broadening without looking.** Apply selectors were widened to "text contains apply" and a Save
   search ran page-wide. Nobody opened the live page to see what matched. Both became false applies.
8. **Normal platform behaviour misdiagnosed as a bug.** The venv redirector "twin" produced kill logic,
   then a keeper, a mutex and a file lock, all fighting a non-problem, plus hours of relaunch churn and
   Edge window pileups.
9. **Debugging in production under an auto-relauncher.** Each edit meant kill → relaunch → orphaned
   windows. stderr was overwritten on every launch, so 68 launches in 24 h left no crash trace.
10. **No version control, no observability.** The 3400-line runner was never committed (no diff, bisect
    or rollback). Failure paths logged at DEBUG, which never reached the file. Screenshots were
    overwritten. No job id on log lines. The handoff even pointed at the wrong transcript.
11. **Policy and oracle mismatches.** DOB was on the discard list with no value configured, there was
    no checkbox or date handling, and resume success was judged by `files.length`, which Naukri
    clears after reading.

## What replaced it

- **`watch.cmd` (`naukri_watch/`)**: a separate, read-only observer. `live` attaches to the bot's own Edge
  over CDP and tails the log; `postmortem` works from the log alone. It records the bot's belief next to
  Naukri's truth: `FALSE_CONFIRM`, `FALSE_DISCARD`, `APPLICATION_REJECTED` (with the cause),
  `EXTERNAL_APPLY_CLICKED`, `APPLY_NOT_PRESSED`, `STALL` (with where the time went), `FILL_NOT_REGISTERED`,
  `SAVE_WITH_EMPTY_FIELDS`, `FORMAT_*` for new control types (date split, checkbox, unknown), and more.
- **`naukri_bot/chatdrawer/probe.js`**: one model of the page and drawer, shared by the watcher and the bot.
- **v1 hotfixes H1–H5** in `run_naukri_unattended.py`: no external-button clicks, Save lookup scoped to a
  real modal, Naukri's verdict as the only success oracle, a drawer that vanishes after answers checked
  against Naukri, faulthandler plus kept stderr.
- **`naukri_bot/chatdrawer/` v2 engine**: a conversation loop. Answer the active question → verify it
  registered → Save (send) → wait for the transcript to move → read Naukri's verdict. It never closes the
  drawer and reuses v1's answer policy and LLM stack. Enabled with `bot_behavior.chat_engine: "v2"`.

## Rules going forward

- Proof is Naukri's code or page, or a watcher incident. Never a line the bot wrote about itself.
- Look at the live page (watcher `samples/`) before widening any selector.
- One change → commit → restart → `watch.cmd live` → read the report. Roll back with git, not by hand.

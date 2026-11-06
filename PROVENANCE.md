# Provenance

This repository is a sanitized extraction from a private working repository. It carries one new
initial commit; none of the private history, branches, remotes or credentials were copied.

## Upstream origin

The original codebase is the 2022 project
[`lordzohar/Naukri-autoapply-bot`](https://github.com/lordzohar/Naukri-autoapply-bot), whose first
commit is dated 2022-03-29. That project supplied the Selenium login, search, application, config,
database and utility code that this repository's `naukri_bot` package descends from, together with
`main.py` and `requirements.txt`.

The upstream repository does not carry a license file. Its code is therefore unlicensed, not
public domain, and not MIT.

## What was added later

The following were written after the 2022 base and are the maintainer's original additions:

- `run_naukri_unattended.py` (hardened unattended runner, chat policy, submit verification)
- `run_local.py`
- `naukri_bot/chatdrawer/` (v2 chat-drawer engine and page probe)
- `tools/`
- `docs/STRUCTURAL_FAULTS.md` (published here as `STRUCTURAL_FAULTS.md`)
- `README.md`, `PROVENANCE.md`, `LICENSE-PENDING.md`, CI workflow and example configs

The remaining `naukri_bot` modules (`core/`, `modules/`, `utils/`, `chatbot/`, `scoring/`) are
inherited-lineage code. Many were restructured or patched after 2022, but they still contain
substantial upstream code and remain unlicensed.

## Sanitization performed

The extraction deliberately excludes:

- `.git/`, remotes and every commit from the private repository
- `config.json`, `config.local.json`, `enhanced_config.json`, `.github/test_config.json`
- `profiles/`, `sessions/`, `browser_profile/`, `login_evidence/`, `.ua/`, `brag-output/`
- logs, telemetry, SQLite databases, session snapshots, daily-limit markers and screenshots
- `all resumes/`, resume files, `qa_dictionary.json`, `chatbot_questions.csv`
- personal documents, agent instructions, keepalive/watch scripts and private tooling
- hardcoded resume paths, the candidate's name, email, phone, employer history, compensation,
  knowledge-module path, tracker path and local OpenCode path

Resume locations, the optional knowledge module and the optional tracker are now read from
environment variables or local config, never from committed source. Example configs contain
placeholders only.

## License status

The 2022 inherited modules are unlicensed. The original additions are source-available but not
open source. See `LICENSE-PENDING.md`. Permission from the upstream author is required before the
inherited modules can be redistributed under any license.

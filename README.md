# naukri-autoapply

A Selenium bot that searches Naukri.com and completes Easy Apply applications. It is a cleaned,
sanitized copy of a private working repository, published so the code can be read and reviewed.

Use it only with your own account, at your own risk. Automating Naukri may breach its terms of
service, and a wrong answer on an application is your responsibility. Every value the bot sends
comes from your local config or your resume.

## Requirements

- Python 3.10 or newer
- Microsoft Edge and a matching `msedgedriver` (Selenium Manager can usually install it)
- A Naukri account

## Setup

```bash
git clone https://github.com/<your-account>/naukri-autoapply.git
cd naukri-autoapply
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Create the two local config files from the examples:

```bash
copy config.example.json config.local.json
copy enhanced_config.example.json enhanced_config.json
```

Fill in credentials, job search terms, personal details, and the optional Gemini settings. Both
real config files are gitignored.

### Resume

The bot reads resume text for the AI answer path and uploads a resume file when Naukri asks for
one. Set both paths either in `config.local.json`:

```json
"resume": {
  "text_file": "C:\\path\\to\\resume.docx",
  "upload_file": "C:\\path\\to\\resume.pdf"
}
```

or with environment variables:

```
NAUKRI_RESUME_FILE
NAUKRI_RESUME_UPLOAD_FILE
```

`.docx`, `.pdf` and text extraction from `enhanced_config.json` (`user_profile.resume_text`) are
supported. Do not commit a resume to this repository.

## Run

Check the wiring and policy rules without starting a browser:

```bash
python run_naukri_unattended.py --self-test
```

Run the supervised unattended runner:

```bash
python run_naukri_unattended.py
```

Run the original smaller entry point:

```bash
python main.py
```

`run_local.py` is the same as `main.py` but always reads `config.local.json`.

The first sign-in normally needs a visible browser. Sessions are reused after that. Password login
from config is disabled unless you deliberately set `NAUKRI_ALLOW_PASSWORD_LOGIN=1` for an
attended run.

## Environment variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Gemini key, read at runtime; never store it in the repo |
| `NAUKRI_RESUME_FILE` | Resume used as the AI text source |
| `NAUKRI_RESUME_UPLOAD_FILE` | Resume file uploaded to Naukri |
| `NAUKRI_SKIP_EXTERNAL` | `1` (default) skips company-site applications; `0` opens them |
| `NAUKRI_ALLOW_PASSWORD_LOGIN` | `1` enables config-password login for an attended run |
| `NAUKRI_PROFILE` | Profile name under `profiles/` (default: `default`) |
| `NAUKRI_CHAT_ENGINE` | `v2` enables the newer chat-drawer engine |
| `NAUKRI_TRACKER_PATH` | Optional external application-tracker script |
| `NAUKRI_KNOWLEDGE_MODULE` | Optional Python module supplying extra profile facts |
| `NAUKRI_OPENCODE_EXE` | Optional path to an OpenCode CLI used as an LLM fallback |

## Repository layout

```
main.py                      minimal entry point
run_local.py                 entry point using config.local.json
run_naukri_unattended.py     supervised runner and hardened apply/chat policies
naukri_bot/                  Selenium bot package
  core/ modules/ utils/      inherited search, login, apply and config modules
  chatbot/                   v1 chat answers
  chatdrawer/                v2 chat-drawer engine
  scoring/                   optional Gemini job scoring
tools/                       maintenance scripts
STRUCTURAL_FAULTS.md         notes from the private debugging history
```

Generated files (`naukri_jobs.db`, logs, session JSON, screenshots, browser profiles, telemetry)
are gitignored. `tools/debug_submit_triage.csv` is generated output and is not committed.

## CI

GitHub Actions byte-compiles the Python tree and scans tracked files for provider tokens,
private keys, password literals, personal Windows paths and candidate identifier fields. It never runs the
bot against Naukri.

## License status

This is not an open-source release. See `LICENSE-PENDING.md` and `PROVENANCE.md`. The 2022
inherited modules carry no license, and no permission is granted for them until they are rewritten
or the original author's permission is obtained.

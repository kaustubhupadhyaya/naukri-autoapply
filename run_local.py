"""Runner that points NaukriBot at config.local.json instead of config.json
(max_applications_per_session = 0 => unlimited)."""
import sys

import main  # noqa: F401  -- side effect only: root logging + win32 UTF-8 wrap; main() is __main__-guarded
from naukri_bot.core.naukri_bot import NaukriBot

if __name__ == "__main__":
    bot = NaukriBot(config_file="config.local.json")
    sys.exit(0 if bot.run() else 1)

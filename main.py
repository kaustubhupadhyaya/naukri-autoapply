"""Entry point for the Naukri auto-apply bot."""
import sys
import logging
from logging.handlers import RotatingFileHandler

# Windows console UTF-8 fix (matches original Naukri_Edge behavior)
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

# Root logging: rotating file (10MB x5) + console, same format as before
_root = logging.getLogger()
_root.setLevel(logging.INFO)
if not _root.handlers:
    _fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    _fh = RotatingFileHandler("naukri_bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    _ch = logging.StreamHandler()
    _fh.setFormatter(_fmt)
    _ch.setFormatter(_fmt)
    _root.addHandler(_fh)
    _root.addHandler(_ch)

from naukri_bot.core.naukri_bot import NaukriBot


def main():
    try:
        bot = NaukriBot()
        return 0 if bot.run() else 1
    except Exception as e:
        logging.getLogger(__name__).error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

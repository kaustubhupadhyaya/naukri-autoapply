import sqlite3
import sys
import os

db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "naukri_jobs.db")
try:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM applied_jobs WHERE status = 'Applied (Easy Apply)' AND date(application_date) = date('now', 'localtime')")
        row = c.fetchone()
        print(row[0] if row else 0)
except Exception as e:
    sys.stderr.write(str(e) + "\n")
    print(-1)

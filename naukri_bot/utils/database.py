"""SQLite applied-jobs dedup store.

Restructured from Naukri_Edge.py (2025-10-12 "IMPROVED VERSION").
Methods are moved verbatim from the original class; behavior is unchanged.
"""
import os
import sys
import json
import time
import random
import sqlite3
import logging
import platform
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    InvalidSessionIdException,
    ElementNotInteractableException,
)

logger = logging.getLogger(__name__)


class DatabaseMixin:

    def init_job_database(self):
        """Initialize SQLite database"""
        try:
            self.db_conn = sqlite3.connect('naukri_jobs.db', check_same_thread=False)
            cursor = self.db_conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS applied_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_url TEXT NOT NULL,
                    job_title TEXT,
                    company_name TEXT,
                    application_date TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                    status TEXT,
                    notes TEXT
                )
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_job_id ON applied_jobs(job_id)
            ''')

            self.db_conn.commit()
            logger.info("✅ Job database initialized")

        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            self.db_conn = None

    def _extract_job_id(self, job_url):
        """Extract job ID"""
        try:
            parts = job_url.split('-')
            if parts[-1].isdigit() and len(parts[-1]) > 8:
                return parts[-1]
            return str(abs(hash(job_url)))[-12:]
        except:
            return str(abs(hash(job_url)))[-12:]

    def is_job_already_applied(self, job_id):
        """Check if this job is blocked from being attempted again.

        A row alone is NOT proof we applied. Rows whose own status says the bot
        never completed an application must stay retryable, or they blacklist a
        live job forever. 2026-09-16: 'Unverified%' rows are exactly that case --
        they were relabelled by hand after being confirmed against Naukri as
        never-applied (the page-wide-Save false-confirm bug), so they must not
        block. External/Skipped rows are left blocking for now: they genuinely
        have no Easy Apply button, and re-probing them costs a page load each.
        """
        if not self.db_conn:
            return False

        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM applied_jobs "
                "WHERE job_id = ? AND status NOT LIKE 'Unverified%'",
                (job_id,)
            )
            count = cursor.fetchone()[0]
            return count > 0
        except sqlite3.Error as e:
            logger.error(f"Database query error: {e}")
            return False

    def _save_job_application(self, job_id, job_url, status, notes=''):
        """Save application to database.

        2026-09-16: run_naukri_unattended.py normally shadows this with a class-patched version
        (_dated_save_job_application) that also writes application_date/company_name and carries
        the same guard below -- this copy only runs if that patch import ever fails, so it stays
        defensively consistent rather than silently regressing INSERT OR REPLACE's ability to
        downgrade an "Applied*" status to a weaker one for the same job_id.
        """
        if not self.db_conn:
            return

        try:
            cursor = self.db_conn.cursor()
            try:
                cursor.execute("SELECT status FROM applied_jobs WHERE job_id = ?", (job_id,))
                row = cursor.fetchone()
                existing_status = (row[0] or "") if row else ""
            except sqlite3.Error:
                existing_status = ""
            if existing_status.strip().lower().startswith("applied") \
                    and not status.strip().lower().startswith("applied"):
                logger.warning(
                    f"Refusing to downgrade job {job_id} from '{existing_status}' to '{status}' "
                    "-- existing applied status kept")
                return

            job_title = job_url.split('/')[-1].replace('-', ' ')[:100]

            # 2026-10-21: always write the IST time ourselves; SQLite's CURRENT_TIMESTAMP is UTC (house rule: IST only, and the
            # daily cap counts date(application_date) against the local date).
            cursor.execute("""
                INSERT OR REPLACE INTO applied_jobs
                (job_id, job_url, job_title, application_date, status, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (job_id, job_url, job_title, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), status, notes))

            self.db_conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database save error: {e}")

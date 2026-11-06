"""Session report + resource cleanup.

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


class SessionMixin:

    def save_results(self):
        """Save session results"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

            session_data = {
                'timestamp': timestamp,
                'date': datetime.now().isoformat(),
                'statistics': {
                    'total_jobs_found': len(self.joblinks),
                    'applications_sent': self.applied,
                    'applications_failed': self.failed,
                    'jobs_skipped': self.skipped,
                    'external_tabs_opened': len(self.external_tabs_opened),
                    'success_rate': round((self.applied / max(self.applied + self.failed, 1)) * 100, 2),
                    'submit_button_success_rate': round(
                        (self.performance_stats['submit_button_success'] /
                         max(self.performance_stats['submit_button_success'] +
                             self.performance_stats['submit_button_failures'], 1)) * 100, 2
                    )
                },
                'applications': {
                    'successful': self.applied_list['passed'],
                    'failed': self.applied_list['failed']
                },
                'config_used': {
                    'keywords': self.config['job_search']['keywords'],
                    'location': self.config['job_search']['location']
                },
                'performance': self.performance_stats,
                'cached_selectors': {k: v for k, v in self.selector_cache.items() if v}
            }

            report_file = f'naukri_session_{timestamp}.json'
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=4, ensure_ascii=False)

            logger.info(f"📊 Session report saved: {report_file}")

        except Exception as e:
            logger.error(f"Failed to save results: {e}")

    def cleanup(self):
        """Clean up resources"""
        try:
            if self.external_tabs_opened:
                logger.info(f"\n{'='*60}")
                logger.info(f"📌 {len(self.external_tabs_opened)} external tabs remain open")
                logger.info("💡 You can now manually fill these applications")
                logger.info(f"{'='*60}\n")

            if self.db_conn:
                self.db_conn.close()
                logger.info("Database closed")
        except:
            pass

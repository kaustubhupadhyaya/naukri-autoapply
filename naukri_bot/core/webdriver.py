"""WebDriver setup + session recovery.

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


class DriverMixin:

    def setup_driver(self):
        """Setup WebDriver"""
        try:
            logger.info("🚀 Setting up browser...")

            options = webdriver.EdgeOptions()

            # Stealth options
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            if self.config['webdriver'].get('headless', False):
                options.add_argument("--headless")
                logger.info("Running in headless mode")

            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            # 2026-09-30: never start Edge without the saved profile. A profile-less Edge has no
            # Naukri cookies, which made the bot type the stored password (~12 times since 09-14).
            if not self.config['webdriver'].get('user_data_dir'):
                logger.error("❌ Refusing to start Edge without webdriver.user_data_dir (saved profile)")
                return False
            options.add_argument(f"user-data-dir={self.config['webdriver']['user_data_dir']}")

            # One method only. The old fallbacks (config edge_driver_path -> a stale 152 driver in
            # C:\WebDrivers, then Selenium Manager -> a stale 149 driver) can never run a newer Edge.
            try:
                from webdriver_manager.microsoft import EdgeChromiumDriverManager
                driver_path = EdgeChromiumDriverManager().install()
                driver = webdriver.Edge(service=EdgeService(executable_path=driver_path), options=options)
                logger.info("✅ Driver setup successful (auto-download)")
            except Exception as e:
                logger.error(f"❌ Driver setup failed: {e!r}")
                return False

            self.driver = driver
            self.wait = WebDriverWait(self.driver, 5)

            self.driver.maximize_window()
            self.driver.implicitly_wait(self.config['webdriver'].get('implicit_wait', 2))
            self.driver.set_page_load_timeout(self.config['webdriver'].get('page_load_timeout', 30))

            logger.info("✅ WebDriver ready")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to setup driver: {e}")
            return False

    def check_session_validity(self):
        """Check if WebDriver session is still valid"""
        try:
            _ = self.driver.current_url
            return True
        except InvalidSessionIdException:
            logger.warning("⚠️ Session invalid, needs recovery")
            return False
        except Exception:
            return False

    def recover_session(self):
        """Recover from invalid session"""
        try:
            logger.info("🔄 Recovering session...")
            try:
                self.driver.quit()
            except:
                pass

            if not self.setup_driver():
                return False

            return self.login()

        except Exception as e:
            logger.error(f"Failed to recover session: {e}")
            return False

    def ensure_valid_session(self):
        """Ensure session is valid before operations"""
        if not self.check_session_validity():
            return self.recover_session()
        return True

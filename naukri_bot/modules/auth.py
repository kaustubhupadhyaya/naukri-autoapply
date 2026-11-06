"""Login / auth + popup handling.

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


class AuthMixin:

    def login(self):
        """Enhanced login with adaptive selector caching"""
        max_retries = 3

        for attempt in range(max_retries):
            try:
                logger.info(f"🔐 Login attempt {attempt + 1}/{max_retries}")

                self.driver.get('https://www.naukri.com/nlogin/login')
                self.smart_delay(1, 2, probability=0.5)

                self.wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))

                # Persistent Edge profile: already logged in -> nlogin redirects to
                # homepage (no email field). Detect and skip credential entry.
                try:
                    _u = (self.driver.current_url or "").lower()
                    if 'nlogin' not in _u and '/login' not in _u:
                        try:
                            if self._verify_login_success():
                                logger.info("✅ Already logged in via saved profile — skipping credential entry")
                                return True
                        except Exception:
                            pass
                except Exception:
                    pass

                # Email field
                email_selectors = [
                    '#usernameField',
                    '#emailTxt',
                    '#username',
                    "input[placeholder*='Email']",
                    "input[name='email']",
                    "input[type='email']"
                ]

                try:
                    email_field = self.find_element_adaptive(email_selectors, 'login_email', timeout=5)
                    email = self.config.get('naukri_credentials', {}).get('username') or \
                            self.config.get('credentials', {}).get('email')
                    self.human_type(email_field, email)
                    logger.info("✅ Email entered")
                except Exception as e:
                    logger.error(f"Failed to find email field: {e}")
                    continue

                self.smart_delay(0.3, 0.7, probability=0.5)

                # Password field
                password_selectors = [
                    '#passwordField',
                    '#password',
                    '#pwdTxt',
                    "input[placeholder*='Password']",
                    "input[name='password']",
                    "input[type='password']"
                ]

                try:
                    password_field = self.find_element_adaptive(password_selectors, 'login_password', timeout=5)
                    password = self.config.get('naukri_credentials', {}).get('password') or \
                               self.config.get('credentials', {}).get('password')
                    self.human_type(password_field, password)
                    logger.info("✅ Password entered")
                except Exception as e:
                    logger.error(f"Failed to find password field: {e}")
                    continue

                self.smart_delay(0.3, 0.7, probability=0.5)

                # Login button
                login_button_selectors = [
                    "button[type='submit']",
                    ".loginButton",
                    "button.btn-primary",
                    "//button[contains(text(), 'Login')]"
                ]

                login_button = None
                for selector in login_button_selectors:
                    try:
                        if selector.startswith('//'):
                            login_button = WebDriverWait(self.driver, 3).until(
                                EC.element_to_be_clickable((By.XPATH, selector))
                            )
                        else:
                            login_button = WebDriverWait(self.driver, 3).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )

                        if login_button and login_button.is_displayed():
                            logger.info(f"✅ Found login button")
                            break
                    except:
                        continue

                if not login_button:
                    logger.error("❌ Could not find login button")
                    continue

                try:
                    login_button.click()
                except ElementClickInterceptedException:
                    self.driver.execute_script("arguments[0].click();", login_button)

                self.smart_delay(3, 5, probability=0.8)

                if self._verify_login_success():
                    logger.info("✅ Login successful!")
                    return True
                else:
                    logger.warning(f"❌ Login attempt {attempt + 1} failed")
                    if attempt < max_retries - 1:
                        self.smart_delay(5, 10, probability=1.0)
                        continue

            except Exception as e:
                logger.error(f"Login attempt {attempt + 1} error: {e}")
                if attempt < max_retries - 1:
                    self.smart_delay(5, 10, probability=1.0)
                    continue

        logger.error("❌ All login attempts failed")
        return False

    def _verify_login_success(self):
        """Enhanced login verification"""
        try:
            current_url = self.driver.current_url.lower()
            if 'nlogin' in current_url or '/login' in current_url:
                return False

            profile_indicators = [
                '.nI-gNb-drawer__icon',
                '.view-profile-wrapper',
                '[data-automation="profileDropdown"]',
                '.user-name',
                '.profile-img'
            ]

            for indicator in profile_indicators:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, indicator)
                    if elements and any(el.is_displayed() for el in elements):
                        logger.info(f"✅ Login verified")
                        return True
                except:
                    continue

            return False

        except Exception as e:
            logger.error(f"Error verifying login: {e}")
            return False

    def _handle_popups(self):
        """Handle common popups"""
        try:
            short_wait = WebDriverWait(self.driver, 3)

            close_button_selectors = [
                "span.close-popup",
                "button.close",
                "div.cross-icon",
                "[aria-label='Close']",
                ".crossIcon",
                "button[title='Close']"
            ]

            for selector in close_button_selectors:
                try:
                    close_button = short_wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )

                    try:
                        close_button.click()
                    except:
                        self.driver.execute_script("arguments[0].click();", close_button)

                    self.smart_delay(0.5, 1.0, probability=0.5)
                    break

                except TimeoutException:
                    continue

        except Exception as e:
            logger.debug(f"Popup handling: {e}")

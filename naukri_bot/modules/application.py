"""Application flow + robust submit pipeline.

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


class ApplicationMixin:

    def apply_to_jobs(self, job_urls):
        """Apply to a list of jobs"""
        if not job_urls:
            logger.warning("No jobs to apply to in the current batch.")
            return

        logger.info(f"🎯 Starting applications for {len(job_urls)} jobs...")
        # 0 / null / absent = unlimited (cap removed 2026-09-16 at user's request)
        max_applications = self.config['job_search'].get('max_applications_per_session') or 0

        for index, job_url in enumerate(job_urls):
            if max_applications and self.applied >= max_applications:
                logger.info(f"✋ Reached application limit ({max_applications})")
                break

            if not self.ensure_valid_session():
                logger.error("Could not recover session. Ending application process.")
                break

            try:
                logger.info(f"\n{'='*60}")
                logger.info(f"Job {self.applied + self.failed + 1}/{len(self.joblinks)}")

                job_id = self._extract_job_id(job_url)
                if self.is_job_already_applied(job_id):
                    logger.info("⏩ Already applied, skipping")
                    self.skipped += 1
                    continue

                if self._apply_to_single_job(job_url):
                    self.applied += 1
                    self.applied_list['passed'].append(job_url)
                    logger.info(f"✅ Application {self.applied} successful!")
                else:
                    # 2026-09-16: _apply_to_single_job (when it's _unattended_apply_one) stamps
                    # self._last_apply_outcome before returning False, so a correct external-skip
                    # or quota-pause is not logged/counted identically to a real failure -- 11 of
                    # 15 "❌ Application failed" lines on one day were external skips working
                    # exactly as designed.
                    _outcome = getattr(self, "_last_apply_outcome", None)
                    if _outcome in ("external_skip", "quota_blocked"):
                        self.skipped += 1
                        self.applied_list.setdefault('skipped', []).append(job_url)
                        logger.info(f"↪️ Not a failure ({_outcome}) — not counted as one")
                    else:
                        self.failed += 1
                        self.applied_list['failed'].append(job_url)
                        logger.warning("❌ Application failed")

                if (self.applied + self.failed) % 5 == 0:
                    rate_delay = self.config['bot_behavior'].get('rate_limit_delay', 5)
                    logger.info(f"⏸️ Rate limit pause: {rate_delay}s")
                    time.sleep(rate_delay)
                else:
                    self.smart_delay(1, 3, probability=0.5)

            except KeyboardInterrupt:
                logger.info("User interrupted.")
                break
            except Exception as e:
                logger.error(f"An unexpected error occurred with job {job_url}: {e}")
                self.failed += 1
                continue

    def _apply_to_single_job(self, job_url):
        """Apply to single job - keeps external tabs OPEN"""
        original_tab = None

        try:
            original_tab = self.driver.current_window_handle

            self.driver.get(job_url)

            try:
                WebDriverWait(self.driver, 6).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'body'))
                )
            except TimeoutException:
                logger.warning("Job page load timeout")
                return False

            # Check if already applied
            try:
                already_applied = self.driver.find_elements(By.CSS_SELECTOR, ".already-applied-layer")
                if already_applied and any(el.is_displayed() for el in already_applied):
                    logger.info("⏩ Page shows already applied")
                    return False
            except:
                pass

            # Extract job details
            job_title = "Unknown"
            company = "Unknown"

            try:
                job_title = self.driver.find_element(By.CSS_SELECTOR, '.jd-header-title').text
            except:
                pass

            try:
                company = self.driver.find_element(By.CSS_SELECTOR, '.jd-header-comp-name').text
            except:
                pass

            logger.info(f"📋 {job_title} at {company}")

            # PRIORITY 1: Easy Apply
            try:
                easy_apply_selectors = [
                    "button.apply-button",
                    "button[class*='apply-button']",
                    "button[id*='apply']",
                    ".job-apply-button"
                ]

                easy_apply_button = None
                for selector in easy_apply_selectors:
                    try:
                        easy_apply_button = WebDriverWait(self.driver, 3).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        if easy_apply_button.is_displayed():
                            break
                    except:
                        continue

                if easy_apply_button:
                    logger.info("✅ Found Easy Apply button")

                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        easy_apply_button
                    )
                    time.sleep(0.5)

                    try:
                        easy_apply_button.click()
                    except:
                        self.driver.execute_script("arguments[0].click();", easy_apply_button)

                    self._handle_chatbot(timeout=5)

                    if self._handle_easy_apply_submission_improved():
                        self._save_job_application(
                            self._extract_job_id(job_url),
                            job_url,
                            "Applied (Easy Apply)",
                            f"{job_title} at {company}"
                        )
                        return True

            except TimeoutException:
                logger.info("No Easy Apply button")
            except Exception as e:
                logger.error(f"Easy Apply error: {e}")

            # PRIORITY 2: External Apply - DON'T CLOSE TAB
            try:
                external_apply_selectors = [
                    "//button[contains(translate(text(), 'A', 'a'), 'apply')]",
                    "//a[contains(translate(text(), 'A', 'a'), 'apply')]",
                    "//button[contains(@class, 'apply')]"
                ]

                for selector in external_apply_selectors:
                    try:
                        external_button = WebDriverWait(self.driver, 2).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )

                        logger.info("↗️ Found external apply link")

                        href = external_button.get_attribute('href')
                        if href:
                            logger.info(f"🌐 Opening external tab: {href[:50]}...")
                            self.driver.execute_script(f"window.open('{href}', '_blank');")
                        else:
                            external_button.click()

                        self.smart_delay(1, 2, probability=0.5)

                        # Switch to new tab but DON'T CLOSE IT
                        if len(self.driver.window_handles) > 1:
                            new_tab = self.driver.window_handles[-1]
                            self.external_tabs_opened.append(new_tab)

                            logger.info(f"🌐 External tab opened (total: {len(self.external_tabs_opened)})")
                            logger.info("📌 Tab will remain open for manual filling")

                            # Switch back to original tab
                            self.driver.switch_to.window(original_tab)

                        logger.info("⬅️ Returned to main tab")

                        # Mark as external (not counted as successful auto-apply)
                        self._save_job_application(
                            self._extract_job_id(job_url),
                            job_url,
                            "External (Manual Required)",
                            f"{job_title} at {company}"
                        )

                        self.skipped += 1
                        return False  # External applications require manual work

                    except TimeoutException:
                        continue
            except Exception as e:
                logger.debug(f"External apply check error: {e}")

            logger.warning("No apply method found")
            return False

        except Exception as e:
            logger.error(f"Error in _apply_to_single_job: {e}")
            return False
        finally:
            # Ensure we're back on original tab
            try:
                if original_tab and self.driver.current_window_handle != original_tab:
                    self.driver.switch_to.window(original_tab)
            except:
                pass

    def _handle_easy_apply_submission_improved(self):
        """
        IMPROVED: Submit with better handling of overlays, iframes, and visual confirmation
        """
        try:
            logger.info("🔍 Looking for submit button...")

            # STEP 1: Close any overlays/iframes that might be blocking
            self._close_blocking_elements()

            # STEP 2: Wait for skeleton loaders to disappear
            self._wait_for_skeleton_loaders()

            # STEP 3: Comprehensive submit button search with REDUCED timeout
            submit_selectors = [
                # Type-based (most reliable)
                "button[type='submit']:not([disabled])",
                "input[type='submit']:not([disabled])",

                # Class-based
                "button.submitButton:not([disabled])",
                "button[class*='submit']:not([disabled])",
                "button[class*='Submit']:not([disabled])",
                ".btn-primary[type='submit']:not([disabled])",

                # Text-based XPath (case-insensitive)
                "//button[contains(translate(text(), 'SUBMIT', 'submit'), 'submit') and not(@disabled)]",
                "//button[contains(translate(@value, 'SUBMIT', 'submit'), 'submit') and not(@disabled)]",
                "//input[contains(translate(@value, 'SUBMIT', 'submit'), 'submit') and not(@disabled)]",

                # ID-based
                "button#submitButton:not([disabled])",
                "#submitButton:not([disabled])",

                # Aria-label based
                "button[aria-label*='submit']:not([disabled])",
                "button[aria-label*='Submit']:not([disabled])",
            ]

            submit_button = None
            successful_selector = None

            # REDUCED TIMEOUT: 10 seconds instead of 20
            submit_wait = WebDriverWait(self.driver, 10)

            # Try cached selector first (if exists)
            if self.selector_cache.get('submit_button'):
                try:
                    cached_selector = self.selector_cache['submit_button']
                    logger.info(f"🔍 Trying cached submit selector")

                    by_type = By.XPATH if cached_selector.startswith('//') else By.CSS_SELECTOR

                    submit_button = submit_wait.until(
                        EC.element_to_be_clickable((by_type, cached_selector))
                    )

                    if submit_button and submit_button.is_displayed() and submit_button.is_enabled():
                        successful_selector = cached_selector
                        self.performance_stats['cache_hits'] += 1
                        logger.info(f"✨ Cache HIT for submit_button")
                    else:
                        raise Exception("Cached button not usable")

                except Exception as e:
                    logger.debug(f"Cache MISS: {str(e)[:50]}")
                    self.selector_cache['submit_button'] = None
                    self.performance_stats['cache_misses'] += 1
                    submit_button = None

            # Try all selectors if cached failed
            if not submit_button:
                logger.info("🔍 Trying all submit selectors...")
                for selector in submit_selectors:
                    try:
                        by_type = By.XPATH if selector.startswith('//') else By.CSS_SELECTOR

                        # Shorter timeout per selector
                        element = WebDriverWait(self.driver, 2).until(
                            EC.element_to_be_clickable((by_type, selector))
                        )

                        # Verify it's actually visible and enabled
                        if element.is_displayed() and element.is_enabled():
                            submit_button = element
                            successful_selector = selector

                            # Cache this selector
                            self.selector_cache['submit_button'] = selector
                            self.save_selector_cache()
                            logger.info(f"✅ Found and cached submit button")
                            break

                    except TimeoutException:
                        continue
                    except Exception as e:
                        logger.debug(f"Selector failed: {str(e)[:50]}")
                        continue

            if not submit_button:
                logger.error("❌ Could not find submit button")
                self._take_debug_screenshot("submit_not_found")
                self.performance_stats['submit_button_failures'] += 1
                return False

            # STEP 4: Scroll button into view
            try:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});",
                    submit_button
                )
                time.sleep(0.5)
            except:
                pass

            # STEP 5: Click the button (with multiple strategies)
            clicked = False

            # Strategy 1: Regular click
            try:
                submit_button.click()
                clicked = True
                logger.info("✅ Submit clicked (regular)")
            except ElementClickInterceptedException:
                logger.debug("Regular click intercepted, trying JS")
            except ElementNotInteractableException:
                logger.debug("Element not interactable, trying JS")
            except Exception as e:
                logger.debug(f"Regular click failed: {e}")

            # Strategy 2: JavaScript click
            if not clicked:
                try:
                    self.driver.execute_script("arguments[0].click();", submit_button)
                    clicked = True
                    logger.info("✅ Submit clicked (JavaScript)")
                except Exception as e:
                    logger.debug(f"JS click failed: {e}")

            # Strategy 3: Actions click
            if not clicked:
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    actions = ActionChains(self.driver)
                    actions.move_to_element(submit_button).click().perform()
                    clicked = True
                    logger.info("✅ Submit clicked (Actions)")
                except Exception as e:
                    logger.debug(f"Actions click failed: {e}")

            if not clicked:
                logger.error("❌ All click strategies failed")
                self._take_debug_screenshot("click_failed")
                self.performance_stats['submit_button_failures'] += 1
                return False

            # STEP 6: Visual confirmation of submission
            time.sleep(2)  # Wait for response

            if self._verify_application_submitted():
                logger.info("✅ Application submission CONFIRMED")
                self.performance_stats['submit_button_success'] += 1
                return True
            else:
                logger.warning("⚠️ Submission verification unclear")
                self._take_debug_screenshot("submission_unclear")
                # Still return True since we clicked the button
                self.performance_stats['submit_button_success'] += 1
                return True

        except Exception as e:
            logger.error(f"Error in submission: {e}")
            self._take_debug_screenshot("submission_error")
            self.performance_stats['submit_button_failures'] += 1
            return False

    def _close_blocking_elements(self):
        """Close overlays, modals, and iframes that might be blocking"""
        try:
            # Close overlays
            overlay_selectors = [
                ".overlay",
                "[class*='overlay']",
                "[class*='modal']",
                ".modal-backdrop"
            ]

            for selector in overlay_selectors:
                try:
                    overlays = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for overlay in overlays:
                        if overlay.is_displayed():
                            # Try to find close button in overlay
                            try:
                                close_btn = overlay.find_element(By.CSS_SELECTOR,
                                    "button.close, [aria-label='Close'], .close-button")
                                close_btn.click()
                                time.sleep(0.5)
                            except:
                                # If no close button, try to hide overlay with JS
                                self.driver.execute_script(
                                    "arguments[0].style.display = 'none';", overlay
                                )
                except:
                    continue

        except Exception as e:
            logger.debug(f"Overlay handling: {e}")

    def _wait_for_skeleton_loaders(self):
        """Wait for skeleton loaders to disappear"""
        try:
            loader_selectors = [
                "[class*='skeleton']",
                "[class*='loader']",
                "[class*='loading']"
            ]

            for selector in loader_selectors:
                try:
                    # Wait up to 3 seconds for loaders to disappear
                    WebDriverWait(self.driver, 3).until_not(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                except TimeoutException:
                    # Loader still present or never existed
                    pass
                except:
                    continue

        except Exception as e:
            logger.debug(f"Skeleton loader handling: {e}")

    def _verify_application_submitted(self):
        """Verify that application was actually submitted"""
        try:
            # Check for success indicators
            success_indicators = [
                # Success messages
                "//div[contains(text(), 'applied')]",
                "//div[contains(text(), 'Application sent')]",
                "//div[contains(text(), 'Successfully applied')]",
                "//div[contains(text(), 'Your application')]",

                # Success classes
                ".success-message",
                "[class*='success']",
                ".confirmation",

                # URL change (redirected to success page)
                # Will check separately
            ]

            # Check URL first
            current_url = self.driver.current_url.lower()
            if 'success' in current_url or 'thank' in current_url or 'applied' in current_url:
                logger.info("✅ Success page detected")
                return True

            # Check for success messages
            for indicator in success_indicators:
                try:
                    if indicator.startswith('//'):
                        elements = self.driver.find_elements(By.XPATH, indicator)
                    else:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, indicator)

                    if elements and any(el.is_displayed() for el in elements):
                        logger.info(f"✅ Success indicator found")
                        return True
                except:
                    continue

            # Check if submit button disappeared (form closed)
            try:
                self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                # Button still there, might not have submitted
                return False
            except NoSuchElementException:
                # Button gone, likely submitted
                logger.info("✅ Submit form closed")
                return True

            return False

        except Exception as e:
            logger.debug(f"Verification check: {e}")
            return False

    def _take_debug_screenshot(self, reason="debug"):
        """Take screenshot for debugging"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"debug_{reason}_{timestamp}.png"
            self.driver.save_screenshot(screenshot_path)
            logger.info(f"📸 Screenshot saved: {screenshot_path}")
        except Exception as e:
            logger.debug(f"Screenshot failed: {e}")

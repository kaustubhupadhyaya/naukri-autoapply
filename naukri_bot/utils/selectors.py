"""Adaptive selector cache (persist + replay).

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


class SelectorCacheMixin:

    def load_selector_cache(self):
        """Load previously successful selectors from cache"""
        cache_file = 'selector_cache.json'
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    loaded_cache = json.load(f)
                    for key, value in loaded_cache.items():
                        if value:
                            self.selector_cache[key] = value
                cached_count = len([v for v in self.selector_cache.values() if v])
                logger.info(f"✅ Loaded selector cache with {cached_count} cached selectors")
        except Exception as e:
            logger.debug(f"Could not load selector cache: {e}")

    def save_selector_cache(self):
        """Save successful selectors with better persistence"""
        cache_file = 'selector_cache.json'
        try:
            cache_to_save = {k: v for k, v in self.selector_cache.items() if v}
            with open(cache_file, 'w') as f:
                json.dump(cache_to_save, f, indent=2)
            logger.debug(f"💾 Selector cache saved: {cache_to_save}")
        except Exception as e:
            logger.error(f"Could not save selector cache: {e}")

    def find_element_adaptive(self, selectors, selector_type, by_type=By.CSS_SELECTOR, timeout=3):
        """Adaptively find element with improved caching"""
        # Try cached selector first
        if self.selector_cache.get(selector_type):
            try:
                cached_selector = self.selector_cache[selector_type]
                cache_by_type = By.XPATH if cached_selector.startswith('//') else by_type

                element = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((cache_by_type, cached_selector))
                )

                self.performance_stats['cache_hits'] += 1
                logger.debug(f"✨ Cache HIT for {selector_type}")
                return element

            except Exception:
                logger.debug(f"❌ Cache MISS for {selector_type}")
                self.selector_cache[selector_type] = None
                self.performance_stats['cache_misses'] += 1

        # Try all selectors
        for selector in selectors:
            try:
                current_by_type = By.XPATH if selector.startswith('//') else by_type
                element = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((current_by_type, selector))
                )

                # Cache this successful selector
                self.selector_cache[selector_type] = selector
                self.save_selector_cache()
                logger.debug(f"✅ Found and cached {selector_type}")
                return element

            except Exception:
                continue

        raise NoSuchElementException(f"Could not find element with any selector for {selector_type}")

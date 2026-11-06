"""Shared helpers: delays, typing, Gemini init.

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


class HelpersMixin:

    def _init_gemini_if_configured(self):
        """Initialize Gemini AI if API key is present"""
        try:
            if 'gemini_api_key' in self.config and self.config['gemini_api_key']:
                import google.generativeai as genai
                genai.configure(api_key=self.config['gemini_api_key'])
                self.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
                logger.info("✅ Gemini AI initialized")
            else:
                self.gemini_model = None
                logger.info("ℹ️ Gemini AI not configured (optional)")
        except Exception as e:
            self.gemini_model = None
            logger.warning(f"⚠️ Gemini AI initialization failed: {e}")

    def smart_delay(self, min_seconds=None, max_seconds=None, probability=0.3):
        """Ultra-minimal delays"""
        if min_seconds is None:
            min_seconds = self.config['bot_behavior']['min_delay']
        if max_seconds is None:
            max_seconds = self.config['bot_behavior']['max_delay']

        if random.random() < probability:
            delay = random.uniform(min_seconds, max_seconds)
            time.sleep(delay)

    def human_type(self, element, text, typing_delay=None):
        """Type text like a human"""
        try:
            if typing_delay is None:
                typing_delay = self.config['bot_behavior']['typing_delay']

            element.clear()
            for char in text:
                element.send_keys(char)
                if random.random() < 0.1:
                    time.sleep(random.uniform(0.01, typing_delay))

        except StaleElementReferenceException:
            logger.warning("Element became stale during typing, retrying...")
            element.send_keys(text)

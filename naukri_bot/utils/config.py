"""Configuration loading + default template.

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


class ConfigMixin:

    def load_config(self):
        """Load configuration with validation"""
        try:
            if not os.path.exists(self.config_file):
                logger.warning(f"Config file not found: {self.config_file}")
                return self._create_default_config()

            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Support both credential formats
            if 'credentials' in config and 'naukri_credentials' not in config:
                config['naukri_credentials'] = config['credentials']
            elif 'naukri_credentials' in config and 'credentials' not in config:
                config['credentials'] = config['naukri_credentials']

            logger.info("✅ Configuration loaded successfully")
            return config

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return self._create_default_config()

    def _create_default_config(self):
        """Create default configuration template"""
        logger.warning("⚠️ Creating default configuration template...")
        default_config = {
            "credentials": {
                "email": "YOUR_EMAIL_HERE",
                "password": "YOUR_PASSWORD_HERE"
            },
            "naukri_credentials": {
                "username": "YOUR_EMAIL_HERE",
                "password": "YOUR_PASSWORD_HERE"
            },
            "personal_info": {
                "firstname": "YOUR_FIRSTNAME",
                "lastname": "YOUR_LASTNAME",
                "phone": "YOUR_PHONE",
                "current_ctc": "12 LPA",
                "expected_ctc": "18 LPA",
                "notice_period": "30 days"
            },
            "user_profile": {
                "name": "Your Name",
                "experience_years": "3",
                "current_role": "Software Engineer",
                "core_skills": ["Python", "SQL", "Data Engineering"]
            },
            "job_search": {
                "keywords": ["Python Developer", "Data Engineer"],
                "location": "Bangalore",
                "experience": "2",
                "max_applications_per_session": 0,
                "pages_per_keyword": 5,
                "job_age_days": 7,
                "preferred_companies": [],
                "avoid_companies": []
            },
            "webdriver": {
                "edge_driver_path": "C:\\WebDrivers\\msedgedriver.exe",
                "implicit_wait": 5,
                "page_load_timeout": 45,
                "headless": False,
                "user_data_dir": ""
            },
            "bot_behavior": {
                "min_delay": 0.2,
                "max_delay": 0.8,
                "typing_delay": 0.03,
                "scroll_pause": 0.5,
                "rate_limit_delay": 5
            },
            "gemini_api_key": ""
        }

        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4)

        logger.warning("❗ IMPORTANT: Please update credentials in config.json before running!")
        return default_config

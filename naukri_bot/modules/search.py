"""Job search, card scraping, relevance.

Restructured from Naukri_Edge.py (2025-10-12 "IMPROVED VERSION").
Methods are moved verbatim from the original class; behavior is unchanged.
"""
import re
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
from urllib.parse import quote

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

# 2026-10-19: Naukri's freshness filter is the URL parameter jobAge; the search page offers 1, 3, 7, 15 and 30 days (its own page
# state shows the parameter parsed). Relevance order alone shows the same top cards every cycle: on 10-17/10-18 the two Naukri bots
# read about 90,000 cards between them, every one already applied to or filtered out, and saved 4 applications. With the filter the
# pages we read are the recently posted jobs. Only what is shown changes; the relevance, experience, title and stack gates and the
# already-applied check are untouched.
JOB_AGE_VALUES = (1, 3, 7, 15, 30)
DEFAULT_JOB_AGE_DAYS = 3


def job_age_days_from(config) -> int:
    """Freshness window in days for the search URLs: env NAUKRI_JOB_AGE_DAYS, else config job_search.job_age_days, else 3.
    0 (or null) turns the filter off. A value Naukri does not offer is moved up to the next one it does (30 at most)."""
    raw = os.environ.get("NAUKRI_JOB_AGE_DAYS")
    if raw is None or str(raw).strip() == "":
        raw = (config.get('job_search') or {}).get('job_age_days', DEFAULT_JOB_AGE_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 0
    if days <= 0:
        return 0
    return next((v for v in JOB_AGE_VALUES if v >= days), JOB_AGE_VALUES[-1])


def adaptive_job_age_days(config, last_cycle_done, now) -> int:
    """Freshness window for THIS cycle (times are epoch seconds on this PC's IST clock). With job_search.job_age_adaptive on, a bot that
    finished a complete cycle within the last job_age_widen_after_hours (default 20) only needs the jobs posted since then, so it asks for
    job_age_narrow_days (default 1): about a third of the result pages. After a longer gap, or on its first cycle, it asks for the
    configured window (default 3) so nothing posted while it was away is missed. The env NAUKRI_JOB_AGE_DAYS still overrides everything."""
    base = job_age_days_from(config)
    js = config.get('job_search') or {}
    if not base or not js.get('job_age_adaptive') or os.environ.get("NAUKRI_JOB_AGE_DAYS") not in (None, ""):
        return base
    narrow = job_age_days_from({'job_search': {'job_age_days': js.get('job_age_narrow_days', 1)}}) or base
    try:
        widen_s = float(js.get('job_age_widen_after_hours', 20) or 20) * 3600
    except (TypeError, ValueError):
        widen_s = 20 * 3600
    if last_cycle_done is None or (now - last_cycle_done) > widen_s:
        return base
    return min(base, narrow)


def search_url(keyword: str, location: str, page: int, job_age_days: int = 0) -> str:
    """The search-results URL for one keyword, city and page, with the freshness filter when job_age_days is set."""
    kw = keyword.lower().replace(' ', '-')
    loc = location.lower().replace(' ', '-')
    if not job_age_days:
        return f"https://www.naukri.com/{kw}-jobs-in-{loc}-{page}"
    query = f"?k={quote(keyword.lower())}&l={quote(location.lower())}&jobAge={job_age_days}"
    if page <= 1:
        # the "-1" form is a 301 to this canonical URL
        return f"https://www.naukri.com/{kw}-jobs-in-{loc}{query}"
    # 2026-10-19: with the filter on, pages 2.. of a keyword showed identical cards (counts repeated for 3-4 pages in a row): the path
    # suffix alone did not page. The search page's own state reads an explicit pageNo, so it is sent as well as the suffix.
    return f"https://www.naukri.com/{kw}-jobs-in-{loc}-{page}{query}&pageNo={page}"


class SearchMixin:

    def scrape_job_links(self):
        """Deprecated: scraping is now handled per-page by search_and_apply_page_by_page()."""
        logger.warning("scrape_job_links() is deprecated. Use search_and_apply_page_by_page().")
        return False

    def search_and_apply_page_by_page(self):
        """Scrapes and applies to jobs on a page-by-page basis."""
        logger.info("🔍 Starting page-by-page job search and application...")

        keywords = self.config['job_search']['keywords']
        location = self.config['job_search']['location']
        pages_per_keyword = self.config['job_search']['pages_per_keyword']
        # 0 / null / absent = unlimited
        max_applications = self.config['job_search'].get('max_applications_per_session') or 0

        for keyword in keywords:
            logger.info(f"🔎 Searching for: {keyword}")

            for page in range(1, pages_per_keyword + 1):
                if max_applications and self.applied >= max_applications:
                    logger.info(f"✋ Reached application limit ({max_applications})")
                    return

                try:
                    url = search_url(keyword, location, page, job_age_days_from(self.config))

                    logger.info(f"📄 Page {page}")
                    self.driver.get(url)

                    WebDriverWait(self.driver, 8).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 'body'))
                    )
                    self.smart_delay(1, 2, probability=0.3)
                    self._handle_popups()

                    job_cards = self._get_job_cards_fast()

                    if not job_cards:
                        logger.info("No jobs found on this page, moving to next keyword.")
                        break

                    page_job_links = []
                    for card in job_cards:
                        try:
                            job_url = self._extract_job_url_fast(card)
                            if job_url and job_url not in self.joblinks:
                                job_id = self._extract_job_id(job_url)
                                if not self.is_job_already_applied(job_id) and self._is_job_relevant_fast(card):
                                    page_job_links.append(job_url)
                                    self.joblinks.append(job_url)
                        except Exception as e:
                            logger.debug(f"Error extracting job: {e}")

                    if page_job_links:
                        logger.info(f"✅ Found {len(page_job_links)} new jobs on this page. Applying now...")
                        self.apply_to_jobs(page_job_links)
                    else:
                        logger.info("No new jobs on this page to apply for.")

                except Exception as e:
                    logger.error(f"Error on page {page} for keyword '{keyword}': {e}")
                    continue

    def _get_job_cards_fast(self):
        """Fast job card extraction"""
        selectors = ['.srp-jobtuple-wrapper', '.jobTuple', '[data-job-id]']

        for selector in selectors:
            try:
                cards = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if cards:
                    return cards
            except:
                continue
        return []

    def _extract_job_url_fast(self, job_card):
        """Fast URL extraction"""
        try:
            link_selectors = ['.title a', '.jobTuple-title a', 'a[href*="job-listings"]']

            for selector in link_selectors:
                try:
                    link = job_card.find_element(By.CSS_SELECTOR, selector)
                    href = link.get_attribute('href')
                    if href and 'job-listings' in href:
                        return href
                except:
                    continue
            return None
        except:
            return None

    def _is_job_relevant_fast(self, job_card):
        """Fast relevance check with seniority and agency filtering."""
        try:
            text = job_card.text.lower()

            avoid_companies = [c.lower() for c in self.config['job_search'].get('avoid_companies', [])]
            # Standard staffing agency / anonymous signatures
            agency_signatures = ["leading client", "client of", "featured staffing", "reputed mnc", "confidential client"]
            avoid_all = avoid_companies + agency_signatures
            if any(company in text for company in avoid_all):
                return False

            # Seniority / Experience Range Gate:
            # Candidate has ~3.3 years experience. Roles requiring min_exp > 4 years auto-disqualify
            # in ATS filters. Skip them to protect application quota and conversion rates.
            # 2026-10-08 (interim, until the shared fit gate): the user's band is a minimum of at most
            # 5 years (config max_min_experience). Naukri cards print the range as "5-10 Yrs"; the
            # 10-05 regex only knew the job page's "5 to 10 years" and never matched a card.
            exp_match = (re.search(r'(\d+)\s*-\s*(\d+)\s*yrs?\b', text)
                         or re.search(r'(\d+)\s+to\s+(\d+)\s+years', text))
            if exp_match:
                min_exp, max_exp = int(exp_match.group(1)), int(exp_match.group(2))
                max_min_allowed = int(self.config.get('job_search', {}).get('max_min_experience', 4))
                min_max_allowed = int(self.config.get('job_search', {}).get('min_max_experience', 2))
                if min_exp > max_min_allowed or max_exp < min_max_allowed:
                    return False

            # 2026-10-08 user decision: Lead / Staff / Principal / Head / Manager / Architect titles
            # (and Director / VP / Chief) are never applied to; "Senior" is fine when the band fits.
            try:
                title = job_card.find_element(By.CSS_SELECTOR, "a.title, .title").text.lower()
            except Exception:
                title = text.split("\n", 1)[0]
            if re.search(r'(?<![a-z0-9])(lead|staff|principal|head|manager|architect|director|vp|'
                         r'vice president|chief)(?![a-z0-9])', title):
                return False
            # 2026-10-08 user decision: never open a job built on a stack that is not on the resume
            # (same list as job-tracker\instahyre_apply.py until both use the shared fit rules).
            if re.search(r"(?<![a-z0-9])(java|j2ee|spring boot|\.net|dotnet|c#|php|golang|ruby|rails|salesforce|sap|"
                         r"abap|mainframe|cobol|pl ?/ ?sql|plsql|oracle developer|oracle apps|power ?bi|tableau|qlik|"
                         r"informatica|iics|ab ?initio|datastage|talend|pentaho|ssis|sharepoint|servicenow|react|angular|"
                         r"node\.?js|full ?stack|front ?end|embedded|firmware|palantir|mulesoft|guidewire|pega)(?![a-z0-9])",
                         title):
                return False

            # 2026-09-16: this used to fall through to an unconditional `return True`,
            # which made the keyword check dead code -- every Easy-Apply card passed and
            # Naukri's ~26/day server-side quota got spent on clerical/e-commerce roles
            # (12 of 20 slots on 09-15, 2 of 5 on 09-16). The match is now authoritative.
            #
            # It matches `relevance_keywords` (role tokens), NOT `keywords` (search-URL
            # phrases). Measured against 237 real cards: matching the search phrases
            # literally rejected 'ETL Lead', 'ETL Pentaho Developer', 'Airflow developer'
            # and 'Informatica developer' -- target roles that simply do not contain the
            # two-word phrase 'ETL Developer'. Falls back to `keywords` if unset.
            js = self.config['job_search']
            tokens = [k.lower() for k in (js.get('relevance_keywords') or js['keywords'])]

            # Word-ish boundaries, not bare substrings: a bare 'etl'/'dbt' would match
            # inside unrelated words, which is exactly how the 'fir' token once matched
            # 'confirm' and discarded every consent question.
            return any(
                re.search(r'(?<![a-z0-9])' + re.escape(tok) + r'(?![a-z0-9])', text)
                for tok in tokens
            )
        except Exception:
            # A card we cannot read is not evidence of relevance -- but it is also not
            # evidence against it, and dropping it silently loses real jobs. Keep the
            # permissive behaviour only for this genuinely-unknown case.
            return True

"""Chatbot Q&A: keyword answers + Gemini.

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


class ChatbotMixin:

    def _handle_chatbot(self, timeout=3):
        """Handle chatbot with Gemini"""
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='chatbot']"))
            )
            logger.info("💬 Chatbot detected")

            start_time = time.time()
            max_interaction_time = 30

            while (time.time() - start_time) < max_interaction_time:
                try:
                    question_element = self.driver.find_element(
                        By.CSS_SELECTOR,
                        "div[class*='chatbot'] div[class*='question']"
                    )
                    question_text = question_element.text.strip()

                    if not question_text:
                        break

                    logger.info(f"Chatbot question: '{question_text}'")

                    answer = self._get_keyword_answer(question_text)

                    if not answer and self.gemini_model:
                        answer = self._get_gemini_answer(question_text)

                    if answer:
                        input_field = self.driver.find_element(
                            By.CSS_SELECTOR,
                            "div[class*='chatbot'] input"
                        )
                        input_field.clear()
                        input_field.send_keys(answer)

                        submit_button = self.driver.find_element(
                            By.CSS_SELECTOR,
                            "div[class*='chatbot'] button"
                        )
                        submit_button.click()

                        self.smart_delay(1, 2, probability=0.5)
                    else:
                        break

                except NoSuchElementException:
                    break
                except Exception as e:
                    logger.debug(f"Chatbot interaction error: {e}")
                    break

            return True

        except TimeoutException:
            return False
        except Exception as e:
            logger.error(f"Chatbot handler error: {e}")
            return False

    def _get_keyword_answer(self, question):
        """Fast keyword-based answering"""
        question_lower = question.lower()

        # 'expected' must come before 'ctc'/'salary' (first match wins): "Expected CTC?" was
        # answered with the current CTC (2026-09-24).
        keyword_answers = {
            'experience': f"{self.config.get('user_profile', {}).get('experience_years', '3')} years",
            'years': f"{self.config.get('user_profile', {}).get('experience_years', '3')} years",
            'expected': self.config.get('personal_info', {}).get('expected_ctc', '18 LPA'),
            'ctc': self.config.get('personal_info', {}).get('current_ctc', '12 LPA'),
            'salary': self.config.get('personal_info', {}).get('current_ctc', '12 LPA'),
            'notice': self.config.get('personal_info', {}).get('notice_period', '30 days'),
            'location': self.config.get('job_search', {}).get('location', 'Bengaluru'),
            'phone': self.config.get('personal_info', {}).get('phone', ''),
            'email': self.config.get('credentials', {}).get('email', ''),
            'name': f"{self.config.get('personal_info', {}).get('firstname', '')} {self.config.get('personal_info', {}).get('lastname', '')}"
        }

        for keyword, answer in keyword_answers.items():
            if keyword in question_lower:
                return answer

        return None

    def _get_gemini_answer(self, question):
        """Get answer from Gemini AI"""
        if not self.gemini_model:
            return None

        try:
            user_profile = self.config.get('user_profile', {})
            personal_info = self.config.get('personal_info', {})

            context = f"""
            - Name: {user_profile.get('name', 'Candidate')}
            - Experience: {user_profile.get('experience_years', '3')} years
            - Current CTC: {personal_info.get('current_ctc', '12 LPA')}
            - Expected CTC: {personal_info.get('expected_ctc', '18 LPA')}
            - Notice Period: {personal_info.get('notice_period', '30 days')}
            """

            prompt = f"""Answer concisely (max 5 words).
            
            Context: {context}
            Question: "{question}"
            
            Answer:"""

            response = self.gemini_model.generate_content(prompt)
            answer = response.text.strip().replace('"', '')
            logger.info(f"Gemini answer: '{answer}'")
            return answer

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return None

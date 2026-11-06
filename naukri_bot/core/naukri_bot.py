"""Core NaukriBot orchestrator, composed from behavior-preserving mixins
extracted verbatim from Naukri_Edge.py (2025-10-12 "IMPROVED VERSION")."""
import os
import logging

from ..utils.config import ConfigMixin
from ..utils.selectors import SelectorCacheMixin
from ..utils.database import DatabaseMixin
from ..utils.helpers import HelpersMixin
from ..utils.session import SessionMixin
from .webdriver import DriverMixin
from ..modules.auth import AuthMixin
from ..modules.search import SearchMixin
from ..modules.application import ApplicationMixin
from ..chatbot.chatbot import ChatbotMixin

logger = logging.getLogger(__name__)


class NaukriBot(
    ConfigMixin,
    SelectorCacheMixin,
    DatabaseMixin,
    HelpersMixin,
    SessionMixin,
    DriverMixin,
    AuthMixin,
    SearchMixin,
    ApplicationMixin,
    ChatbotMixin,
):
    """Complete Naukri Bot - restructured from Naukri_Edge.py (behavior preserved)."""

    def __init__(self, config_file='config.json'):
        """Initialize bot with configuration"""
        self.config_file = config_file
        self.config = self.load_config()
        self.driver = None
        self.wait = None
        self.db_conn = None

        # Statistics
        self.joblinks = []
        self.applied = 0
        self.failed = 0
        self.skipped = 0
        self.applied_list = {'passed': [], 'failed': []}

        # Repository root for session saves
        self.repo_root = os.path.dirname(os.path.abspath(__file__))

        # ADAPTIVE SELECTOR CACHE
        self.selector_cache = {
            'login_email': None,
            'login_password': None,
            'login_button': None,
            'submit_button': None,
            'job_card': None,
            'apply_button': None
        }
        self.load_selector_cache()

        # Performance tracking
        self.performance_stats = {
            'avg_job_scrape_time': 0,
            'avg_apply_time': 0,
            'total_jobs_processed': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'submit_button_success': 0,
            'submit_button_failures': 0
        }

        # Track external tabs opened
        self.external_tabs_opened = []

        # Initialize components
        self.init_job_database()
        self._init_gemini_if_configured()

        logger.info("✅ Bot initialized successfully")

    def run(self):
        """Main execution"""
        try:
            print("=" * 60)
            print("🚀 NAUKRI BOT - IMPROVED VERSION")
            print("=" * 60)

            if not self.setup_driver():
                return False

            if not self.login():
                return False

            # This is the new, integrated method
            self.search_and_apply_page_by_page()

            return True

        except KeyboardInterrupt:
            logger.info("⚠️ Process interrupted")
            return False
        except Exception as e:
            logger.error(f"❌ Fatal error: {e}")
            return False
        finally:
            self.save_results()
            
            # Print summary
            total_processed = self.applied + self.failed
            success_rate = (self.applied / max(total_processed, 1)) * 100
            
            print("\n" + "=" * 60)
            print("🎉 SESSION COMPLETE")
            print("=" * 60)
            print(f"🔍 Jobs Found: {len(self.joblinks)}")
            print(f"✅ Applications Sent: {self.applied}")
            print(f"❌ Applications Failed: {self.failed}")
            print(f"⏭️  Jobs Skipped: {self.skipped}")
            print(f"🌐 External Tabs Opened: {len(self.external_tabs_opened)}")
            print(f"📈 Success Rate: {success_rate:.1f}%")
            print(f"💾 Cached Selectors: {len([v for v in self.selector_cache.values() if v])}")
            print(f"⚡ Cache Hits: {self.performance_stats['cache_hits']}")
            print(f"🔄 Cache Misses: {self.performance_stats['cache_misses']}")
            print(f"🎯 Submit Success: {self.performance_stats['submit_button_success']}")
            print(f"❌ Submit Failures: {self.performance_stats['submit_button_failures']}")
            print("=" * 60)
            
            self.cleanup()
            
            if self.driver:
                try:
                    input("\nPress Enter to close ALL browser tabs (including external)...")
                except:
                    pass
                
                try:
                    self.driver.quit()
                    logger.info("Browser closed (all tabs)")
                except:
                    pass

"""Playwright-based engine for JavaScript-heavy configurator pages."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.async_api import async_playwright, Browser, Page, Playwright

from crawler.base import CrawlConfig, VehicleData, VehicleOption
from crawler.engines.base_engine import BaseEngine

logger = logging.getLogger(__name__)


class PlaywrightEngine(BaseEngine):
    """Crawl engine using Playwright for pages requiring JS rendering."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def _ensure_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
        return self._browser

    async def _new_page(self) -> Page:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
        )
        page = await context.new_page()
        return page

    async def _dismiss_cookies(self, page: Page) -> None:
        """Try to dismiss common cookie consent dialogs."""
        cookie_selectors = [
            'button[id*="accept"]',
            'button[class*="accept"]',
            'button[data-testid*="accept"]',
            'button:has-text("Alle akzeptieren")',
            'button:has-text("Accept All")',
            'button:has-text("Akzeptieren")',
            'button:has-text("Zustimmen")',
            '#onetrust-accept-btn-handler',
            '.cookie-consent-accept',
            '[data-action="accept-cookies"]',
        ]
        for sel in cookie_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    logger.info(f"Dismissed cookie dialog with selector: {sel}")
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

    async def _execute_js_triggers(self, page: Page, triggers: list[str]) -> None:
        """Execute any JS triggers from the crawl config."""
        for trigger in triggers:
            try:
                await page.evaluate(trigger)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"JS trigger failed: {trigger!r} → {e}")

    async def get_page_html(self, url: str, config: CrawlConfig) -> str:
        """Fetch fully rendered HTML using Playwright."""
        page = await self._new_page()
        try:
            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=config.wait_timeout_ms)
            await self._dismiss_cookies(page)

            if config.wait_selector:
                try:
                    await page.wait_for_selector(
                        config.wait_selector, timeout=config.wait_timeout_ms
                    )
                except Exception:
                    logger.warning(f"Wait selector {config.wait_selector!r} timed out")

            await self._execute_js_triggers(page, config.js_triggers)

            # Allow dynamic content to settle
            await asyncio.sleep(2)
            return await page.content()
        finally:
            await page.close()

    async def extract_vehicles(
        self, url: str, config: CrawlConfig
    ) -> list[VehicleData]:
        """Extract vehicle data from a JS-rendered configurator page."""
        page = await self._new_page()
        vehicles: list[VehicleData] = []

        try:
            logger.info(f"Playwright: navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=config.wait_timeout_ms)
            await self._dismiss_cookies(page)

            if config.wait_selector:
                try:
                    await page.wait_for_selector(
                        config.wait_selector, timeout=config.wait_timeout_ms
                    )
                    logger.info(f"Wait selector matched: {config.wait_selector}")
                except Exception:
                    logger.warning(f"Wait selector timed out: {config.wait_selector}")

            await self._execute_js_triggers(page, config.js_triggers)
            await asyncio.sleep(2)

            # Scroll to load lazy content
            await self._scroll_page(page)

            card_sel = config.selectors.get("vehicle_card", "")
            if not card_sel:
                logger.warning("No vehicle_card selector — trying generic extraction")
                return await self._generic_extract(page, url, config)

            cards = page.locator(card_sel)
            count = await cards.count()
            logger.info(f"Found {count} vehicle cards with selector {card_sel!r}")

            for i in range(count):
                card = cards.nth(i)
                try:
                    vehicle = await self._extract_from_card(card, url, config)
                    if vehicle and vehicle.model:
                        vehicles.append(vehicle)
                except Exception as e:
                    logger.warning(f"Failed to extract card {i}: {e}")

            return vehicles

        finally:
            await page.close()

    async def _scroll_page(self, page: Page, scrolls: int = 5) -> None:
        """Scroll down the page to trigger lazy-loaded content."""
        for _ in range(scrolls):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(0.5)
        # Scroll back to top
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

    async def _extract_from_card(
        self, card: Any, page_url: str, config: CrawlConfig
    ) -> VehicleData | None:
        """Extract vehicle data from a single card element."""
        selectors = config.selectors

        model = ""
        if sel := selectors.get("model_name"):
            try:
                el = card.locator(sel).first
                model = self.clean_text(await el.text_content() or "")
            except Exception:
                pass

        if not model:
            # Fallback: try heading elements
            for tag in ["h2", "h3", "h4", ".model-name", "[class*='title']"]:
                try:
                    el = card.locator(tag).first
                    text = self.clean_text(await el.text_content() or "")
                    if text and len(text) < 80:
                        model = text
                        break
                except Exception:
                    continue

        price_text = ""
        if sel := selectors.get("price"):
            try:
                el = card.locator(sel).first
                price_text = await el.text_content() or ""
            except Exception:
                pass

        if not price_text:
            # Try common price patterns
            for sel in ["[class*='price']", "[data-price]", ".price", "span:has-text('€')"]:
                try:
                    el = card.locator(sel).first
                    price_text = await el.text_content() or ""
                    if price_text:
                        break
                except Exception:
                    continue

        variant = ""
        if sel := selectors.get("variant"):
            try:
                el = card.locator(sel).first
                variant = self.clean_text(await el.text_content() or "")
            except Exception:
                pass

        fuel_type = ""
        if sel := selectors.get("fuel_type"):
            try:
                el = card.locator(sel).first
                fuel_type = self.clean_text(await el.text_content() or "")
            except Exception:
                pass

        image_url = ""
        if sel := selectors.get("image"):
            try:
                el = card.locator(sel).first
                image_url = await el.get_attribute("src") or await el.get_attribute("data-src") or ""
            except Exception:
                pass

        link = ""
        if sel := selectors.get("options_link"):
            try:
                el = card.locator(sel).first
                link = await el.get_attribute("href") or ""
            except Exception:
                pass

        return VehicleData(
            brand="",  # Filled by the brand crawler
            model=model,
            variant=variant,
            base_price=self.parse_price(price_text),
            fuel_type=fuel_type,
            url=link or page_url,
            image_url=image_url,
        )

    async def _generic_extract(
        self, page: Page, url: str, config: CrawlConfig
    ) -> list[VehicleData]:
        """Fallback: extract any structured vehicle-like data from the page."""
        logger.info("Using generic extraction (no specific selectors)")
        vehicles: list[VehicleData] = []

        # Try to find any elements that look like vehicle listings
        generic_selectors = [
            "[class*='vehicle']", "[class*='model']", "[class*='car-card']",
            "[class*='product-card']", "[class*='tile']", "article",
        ]

        for sel in generic_selectors:
            cards = page.locator(sel)
            count = await cards.count()
            if count >= 3:  # Likely a listing
                logger.info(f"Generic: found {count} elements with {sel!r}")
                for i in range(min(count, 50)):
                    card = cards.nth(i)
                    vehicle = await self._extract_from_card(card, url, config)
                    if vehicle and vehicle.model:
                        vehicles.append(vehicle)
                if vehicles:
                    break

        return vehicles

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None

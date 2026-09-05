"""Network resilience layer: retries, backoff, user-agent rotation, browser pool."""

from __future__ import annotations

import asyncio
import logging
import random
import subprocess
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

# --- User-Agent Rotation ---

USER_AGENTS = [
    # Chrome (macOS, Windows, Linux)
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    # Safari
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
    # Firefox
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
        "Gecko/20100101 Firefox/127.0"
    ),
    # Edge
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"
    ),
]


def get_random_user_agent() -> str:
    """Return a random modern browser user-agent string."""
    return random.choice(USER_AGENTS)


# --- Exponential Backoff Retry ---

T = TypeVar("T")


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    multiplier: float = 2.0,
    jitter: float = 1.0,
    **kwargs: Any,
) -> T:
    """Execute an async function with exponential backoff retry.

    Args:
        func: Async callable to execute.
        max_retries: Maximum number of retries (total attempts = max_retries + 1).
        base_delay: Initial delay in seconds before first retry.
        multiplier: Delay multiplier for each subsequent retry.
        jitter: Random jitter range (0 to jitter seconds) added to each delay.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (multiplier ** attempt) + random.uniform(0, jitter)
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"All {max_retries + 1} attempts failed. Last error: {e}"
                )
    raise last_error  # type: ignore[misc]


# --- Browser Pool (Playwright) ---

class BrowserPool:
    """Shared Playwright browser instance for connection reuse.

    Reduces handshake overhead by keeping a single Chromium instance alive
    and creating lightweight browser contexts per request.
    """

    _playwright: Any = None
    _browser: Any = None
    _lock: asyncio.Lock | None = None

    @classmethod
    async def _ensure_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def acquire(cls) -> "BrowserPool":
        """Get or create the shared browser pool."""
        lock = await cls._ensure_lock()
        async with lock:
            if cls._browser is None:
                from playwright.async_api import async_playwright

                cls._playwright = await async_playwright().start()
                cls._browser = await cls._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-extensions",
                    ],
                )
                logger.info("Browser pool started (Chromium)")
        return cls()

    async def fetch_html(
        self,
        url: str,
        *,
        wait_selector: str | None = None,
        timeout_ms: int = 30_000,
        user_agent: str | None = None,
    ) -> str:
        """Fetch fully-rendered page HTML via Playwright.

        Creates a fresh browser context per call for cookie isolation,
        reusing the shared browser instance for connection pooling.
        """
        ua = user_agent or get_random_user_agent()
        context = await self._browser.new_context(
            user_agent=ua,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            extra_http_headers={
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            },
        )
        try:
            page = await context.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if resp and resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} for {url}")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 10_000))
                except Exception:
                    logger.debug(f"wait_selector '{wait_selector}' timed out, proceeding anyway")
            html = await page.content()
            if not html or len(html) < 100:
                raise RuntimeError(f"Empty or minimal response from {url}")
            return html
        finally:
            await context.close()

    @classmethod
    async def close(cls) -> None:
        """Shut down the shared browser."""
        lock = await cls._ensure_lock()
        async with lock:
            if cls._browser:
                await cls._browser.close()
                cls._browser = None
            if cls._playwright:
                await cls._playwright.stop()
                cls._playwright = None
            logger.info("Browser pool closed")


# --- Curl fallback (sync, for environments without Playwright) ---

def fetch_html_curl(url: str, timeout: int = 30) -> str:
    """Fetch HTML using curl subprocess (fallback).

    Useful when Playwright is unavailable or for lightweight fetches.
    """
    ua = get_random_user_agent()
    result = subprocess.run(
        [
            "curl", "-sL", "--compressed", "--http1.1",
            "--max-time", str(timeout),
            "--retry", "2", "--retry-delay", "3",
            "-H", f"User-Agent: {ua}",
            "-H", "Accept-Language: de-DE,de;q=0.9,en;q=0.8",
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Encoding: gzip, deflate, br",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed (code {result.returncode}): {result.stderr[:200]}")
    if not result.stdout or len(result.stdout) < 100:
        raise RuntimeError(f"curl returned empty/minimal response for {url}")
    return result.stdout

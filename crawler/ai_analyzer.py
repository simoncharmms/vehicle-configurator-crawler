"""AI-powered page analyzer using Claude to determine crawl strategy."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import anthropic

from crawler.base import CrawlConfig, EngineType

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are a web scraping expert. Analyze the following HTML content from a vehicle configurator page and determine the best crawl strategy.

URL: {url}
Page title: {title}

HTML (truncated to first 15000 chars):
```html
{html}
```

Respond with a JSON object containing:
{{
  "engine": "playwright" or "beautifulsoup",
  "reasoning": "Why this engine was chosen",
  "selectors": {{
    "vehicle_card": "CSS selector for each vehicle/model card",
    "model_name": "CSS selector for model name within a card",
    "price": "CSS selector for price within a card",
    "variant": "CSS selector for variant/trim name if visible",
    "fuel_type": "CSS selector for fuel type indicator if visible",
    "image": "CSS selector for vehicle image",
    "options_link": "CSS selector for 'configure' or details link"
  }},
  "js_triggers": ["List of JS expressions to execute before scraping, e.g. scroll actions, cookie consent clicks"],
  "wait_selector": "CSS selector to wait for before scraping (indicates page loaded)",
  "data_source": "dom" or "api",
  "api_endpoints": ["If data comes from XHR/fetch, list the API URLs found in the HTML"],
  "confidence": 0.0 to 1.0,
  "notes": "Any relevant observations"
}}

Rules:
- Use "playwright" if the page requires JavaScript rendering (React/Angular/Vue, dynamic content loading)
- Use "beautifulsoup" only if the HTML already contains all vehicle data statically
- Provide the most specific CSS selectors possible
- If you see API endpoints in script tags, prefer "api" data_source
- Confidence 0.8+ means you're fairly sure the selectors will work
- Confidence below 0.5 means significant uncertainty

Return ONLY the JSON object, no markdown fencing or explanation."""


@dataclass
class AnalysisResult:
    """Result from the AI page analyzer."""
    config: CrawlConfig
    data_source: str  # "dom" or "api"
    api_endpoints: list[str]
    reasoning: str

    def __repr__(self) -> str:
        return (
            f"<AnalysisResult engine={self.config.engine.value} "
            f"confidence={self.config.confidence:.2f} "
            f"data_source={self.data_source}>"
        )


class AIAnalyzer:
    """Uses Claude to analyze configurator pages and determine crawl strategy."""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self._last_call_time: float = 0
        self._min_interval: float = 2.0  # Rate limit: 1 call per 2 seconds

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_time = time.time()

    def analyze(self, url: str, html: str, title: str = "") -> AnalysisResult:
        """Analyze a page and return the recommended crawl strategy."""
        self._rate_limit()

        # Truncate HTML to avoid token limits
        html_truncated = html[:15000]

        prompt = ANALYSIS_PROMPT.format(url=url, title=title, html=html_truncated)

        logger.info(f"Analyzing {url} with Claude ({self.model})...")

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            # Strip markdown fencing if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
            return self._parse_response(data)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            return self._fallback_result(url)
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return self._fallback_result(url)

    def _parse_response(self, data: dict) -> AnalysisResult:
        """Parse Claude's JSON response into an AnalysisResult."""
        engine_str = data.get("engine", "playwright")
        engine = EngineType.PLAYWRIGHT if engine_str == "playwright" else EngineType.BEAUTIFULSOUP

        selectors = data.get("selectors", {})
        # Clean out empty/null selectors
        selectors = {k: v for k, v in selectors.items() if v}

        config = CrawlConfig(
            engine=engine,
            selectors=selectors,
            js_triggers=data.get("js_triggers", []),
            wait_selector=data.get("wait_selector", ""),
            confidence=float(data.get("confidence", 0.5)),
            notes=data.get("notes", ""),
        )

        return AnalysisResult(
            config=config,
            data_source=data.get("data_source", "dom"),
            api_endpoints=data.get("api_endpoints", []),
            reasoning=data.get("reasoning", ""),
        )

    def _fallback_result(self, url: str) -> AnalysisResult:
        """Return a conservative fallback when analysis fails."""
        logger.warning(f"Using fallback strategy for {url}")
        return AnalysisResult(
            config=CrawlConfig(
                engine=EngineType.PLAYWRIGHT,
                selectors={},
                confidence=0.1,
                notes="Fallback: AI analysis failed, using Playwright with no specific selectors",
            ),
            data_source="dom",
            api_endpoints=[],
            reasoning="Fallback due to analysis failure",
        )


def analyze_url(url: str, api_key: str | None = None) -> AnalysisResult:
    """Convenience function: fetch a URL and analyze it."""
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    analyzer = AIAnalyzer(api_key=api_key)
    return analyzer.analyze(url=url, html=resp.text, title="")

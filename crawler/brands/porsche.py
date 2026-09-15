"""Porsche configurator crawler.

Strategy:    Playwright rendering with ``networkidle`` of
             https://www.porsche.com/germany/models/ which is an SPA
             listing all model families and variants.  Option extraction
             via per-model page probing with API capture (MPI compare
             API and embedded script data).
Resilience:  Uses ``retry_with_backoff`` (2 attempts, exponential delay).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType,
    OptionData, VehicleData,
)
from crawler.option_mappings import normalize_option_name, get_category
from crawler.brands.registry import BrandRegistry
from crawler.brands.mercedes import (
    _search_json_for_options,
    _extract_options_from_scripts,
    _extract_options_from_text,
    _dedupe_options,
)
from crawler.network import retry_with_backoff, BrowserPool, get_random_user_agent

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PorscheMarket:
    """One Porsche market whose public site and configurator were verified."""

    code: str
    country_path: str
    locale: str
    accept_language: str

    @property
    def models_url(self) -> str:
        return f"https://www.porsche.com/{self.country_path}/models/"


# Verified on 2026-09-15 with the live Porsche public model page and
# configurator. Every locale below returned model links and priced
# configurator options in its market's native currency. Denmark is excluded:
# Porsche's own alternate-locale list did not publish a da-DK model page, so
# no configurator route was assumed.
PORSCHE_MARKETS: dict[str, PorscheMarket] = {
    m.code: m for m in (
        PorscheMarket("DE", "germany", "de-DE", "de-DE,de;q=0.9,en;q=0.8"),
        PorscheMarket("AT", "de-AT", "de-AT", "de-AT,de;q=0.9,en;q=0.8"),
        PorscheMarket("CH", "swiss/de", "de-CH", "de-CH,de;q=0.9,en;q=0.8"),
        PorscheMarket("FR", "france", "fr-FR", "fr-FR,fr;q=0.9,en;q=0.8"),
        PorscheMarket("IT", "italy", "it-IT", "it-IT,it;q=0.9,en;q=0.8"),
        PorscheMarket("ES", "spain", "es-ES", "es-ES,es;q=0.9,en;q=0.8"),
        PorscheMarket("NL", "netherlands/nl", "nl-NL", "nl-NL,nl;q=0.9,en;q=0.8"),
        PorscheMarket("BE", "belgium/nl", "nl-BE", "nl-BE,nl;q=0.9,en;q=0.8"),
        PorscheMarket("PL", "poland", "pl-PL", "pl-PL,pl;q=0.9,en;q=0.8"),
        PorscheMarket("GB", "uk", "en-GB", "en-GB,en;q=0.9"),
        PorscheMarket("SE", "sweden", "sv-SE", "sv-SE,sv;q=0.9,en;q=0.8"),
        PorscheMarket("NO", "norway/no", "no-NO", "no-NO,no;q=0.9,en;q=0.8"),
    )
}

PORSCHE_SUPPORTED_MARKETS: tuple[str, ...] = tuple(PORSCHE_MARKETS)


def get_market(market: str) -> PorscheMarket:
    """Look up a verified Porsche market by ISO code (case-insensitive)."""
    key = (market or "DE").upper()
    if key not in PORSCHE_MARKETS:
        raise ValueError(
            f"Porsche market '{market}' not supported. "
            f"Available: {', '.join(PORSCHE_SUPPORTED_MARKETS)}"
        )
    return PORSCHE_MARKETS[key]


# Top-level model family names to detect in headings
MODEL_FAMILIES = {"718", "911", "Taycan", "Panamera", "Macan", "Cayenne"}

# Max models to probe for option data
MAX_OPTION_PROBES = 25  # Increased from 5 for broader cross-brand option coverage

# Slug mapping for model family detail pages
_PORSCHE_FAMILY_SLUGS: dict[str, str] = {
    "718": "718",
    "911": "911",
    "taycan": "taycan",
    "panamera": "panamera",
    "macan": "macan",
    "cayenne": "cayenne",
}


@BrandRegistry.register
class PorscheCrawler(BrandCrawler):
    brand = "Porsche"
    base_url = "https://www.porsche.com"
    SUPPORTED_MARKETS = PORSCHE_SUPPORTED_MARKETS

    @property
    def market_config(self) -> PorscheMarket:
        return get_market(self.market)

    @property
    def models_url(self) -> str:
        return self.market_config.models_url

    @property
    def configurator_url(self) -> str:
        """Market-specific Porsche models entry point used by this crawler."""
        return self.models_url

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=3.0,
            confidence=0.7,
            notes=(
                "Porsche SPA models page (networkidle) plus configurator DOM "
                f"prices. Market: {self.market} "
                f"(locale {self.market_config.locale})."
            ),
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=1.0,
            )
        except Exception as e:
            logger.warning(
                f"Porsche [{self.market}]: all retry attempts exhausted: {e}"
            )
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                errors=[f"All attempts failed: {e}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            logger.info(
                f"Porsche [{self.market}]: fetching {self.models_url} "
                "(networkidle)"
            )
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(
                self.models_url,
                wait_until="networkidle",
                timeout_ms=45_000,
            )
            soup = BeautifulSoup(html, "lxml")

            vehicles = self._extract_from_page(soup)
            if vehicles:
                logger.info(
                    f"Porsche [{self.market}]: extracted {len(vehicles)} vehicles"
                )
            else:
                errors.append(
                    f"No vehicles found on Porsche models page ({self.market})"
                )

            # --- Option extraction phase ---
            if vehicles:
                try:
                    await self._enrich_options(vehicles, pool, cfg)
                except Exception as e:
                    logger.warning(
                        f"Porsche [{self.market}]: option extraction failed: {e}"
                    )
                    errors.append(f"Option extraction partial/failed: {e}")

        except Exception as e:
            logger.error(f"Porsche [{self.market}] crawl error: {e}")
            errors.append(str(e))
        finally:
            try:
                await BrowserPool.close()
            except Exception:
                pass

        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_page(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract Porsche model variants from the SPA-rendered models page.

        The page renders <h3> headings for every variant under each model
        family.  Prices are not displayed on the overview.
        """
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        for h3 in soup.find_all("h3"):
            text = h3.get_text(strip=True)
            if not text or len(text) > 80:
                continue
            # Skip "Modellvarianten" family headers
            if "Modellvarianten" in text:
                continue
            # Must start with a known model family name
            if not any(text.startswith(family) for family in MODEL_FAMILIES):
                continue
            if text in seen:
                continue
            seen.add(text)

            # Determine fuel type from variant name
            fuel_type = ""
            text_lower = text.lower()
            if "electric" in text_lower:
                fuel_type = "electric"
            elif "e-hybrid" in text_lower:
                fuel_type = "hybrid"
            elif "taycan" in text_lower:
                fuel_type = "electric"

            # Determine model family
            family = ""
            for f in MODEL_FAMILIES:
                if text.startswith(f):
                    family = f
                    break

            vehicles.append(VehicleData(
                brand=self.brand,
                model=text,
                variant=family,
                base_price=None,  # Porsche doesn't show prices on overview
                currency=self.currency,
                market=self.market,
                fuel_type=fuel_type,
                url=self.models_url,
            ))

        return vehicles

    # ------------------------------------------------------------------
    # Option extraction
    # ------------------------------------------------------------------

    async def _enrich_options(
        self,
        vehicles: list[VehicleData],
        pool: BrowserPool,
        config: CrawlConfig,
    ) -> None:
        """Probe Porsche model family pages for option/equipment data.

        Uses a two-phase approach:

        Phase 1 — Option names from model family pages:
            Navigates to per-family detail pages and extracts options
            from the MPI compare API and leasing data.  Also discovers
            configurator links with model codes.

        Phase 2 — Real prices from the configurator DOM:
            For each model family, navigates to the Porsche configurator
            SPA (``configurator.porsche.com``) and extracts option-price
            pairs from the rendered DOM.
        """
        # Deduplicate by model family — only probe each family once
        probed_families: set[str] = set()
        probe_count = 0
        # Collect configurator model codes per family from page links
        family_model_codes: dict[str, list[str]] = {}

        for vehicle in vehicles:
            if probe_count >= MAX_OPTION_PROBES:
                break

            family = (vehicle.variant or "").lower()
            if not family or family in probed_families:
                continue

            slug = _PORSCHE_FAMILY_SLUGS.get(family)
            if not slug:
                continue

            probed_families.add(family)
            probe_count += 1

            try:
                await asyncio.sleep(config.rate_limit_seconds)

                probe_url = f"{self.models_url}{slug}/"
                logger.info(
                    f"Porsche [{self.market}] options: probing family "
                    f"{family} → {probe_url}"
                )

                html, api_responses = await pool.fetch_with_api_capture(
                    probe_url,
                    extra_wait_ms=6000,
                    timeout_ms=40_000,
                )

                # Build a family-wide option list
                family_options: list[OptionData] = []
                family_prices: dict[str, float] = {}  # model_code -> price

                for resp in api_responses:
                    api_url = resp.get("url", "")
                    data = resp.get("data")

                    # --- MPI compare API: technical data with options ---
                    if "pccompare" in api_url and isinstance(data, dict):
                        mpi_opts = _extract_porsche_mpi_options(
                            data, self.brand,
                        )
                        family_options.extend(mpi_opts)

                    # --- Leasing data API: prices per model code ---
                    if "leasing-data" in api_url and isinstance(data, dict):
                        for code, info in data.items():
                            if isinstance(info, dict):
                                for item in info.get("summary", {}).get("items", []):
                                    label = item.get("label", "")
                                    if "Listenpreis" in label or "Grundpreis" in label:
                                        p = _parse_porsche_price(
                                            item.get("value", "")
                                        )
                                        if p:
                                            family_prices[code] = p

                    # --- Generic JSON option search ---
                    found = _search_json_for_options(data, self.brand)
                    family_options.extend(found)

                # Script-based extraction from page HTML
                if not family_options:
                    soup = BeautifulSoup(html, "lxml")
                    family_options = _extract_options_from_scripts(
                        soup, self.brand,
                    )
                    if not family_options:
                        family_options = _extract_porsche_features_from_html(
                            soup, self.brand,
                        )

                # Text regex fallback
                if not family_options:
                    family_options = _extract_options_from_text(
                        html, self.brand,
                    )

                # Discover configurator model codes from page links
                model_codes = _extract_porsche_configurator_codes(
                    html, self.market_config.locale,
                )
                if model_codes:
                    family_model_codes[family] = model_codes
                    logger.info(
                        f"Porsche [{self.market}] options: {family} "
                        "configurator codes: "
                        f"{model_codes[:3]}{'...' if len(model_codes) > 3 else ''}"
                    )

                # Apply collected options to all vehicles in this family
                if family_options:
                    deduped = _dedupe_options(family_options)
                    for option in deduped:
                        option.currency = self.currency
                    family_vehicles = [
                        v for v in vehicles
                        if (v.variant or "").lower() == family
                    ]
                    for v in family_vehicles:
                        v.available_options = list(deduped)

                    logger.info(
                        f"Porsche [{self.market}] options: family {family} → "
                        f"{len(deduped)} options applied to "
                        f"{len(family_vehicles)} vehicles"
                    )

            except Exception as e:
                logger.debug(
                    f"Porsche [{self.market}] options: family {family} failed: {e}"
                )

        # Phase 2: fetch real prices from the configurator DOM
        if family_model_codes:
            try:
                await self._apply_configurator_prices(
                    vehicles, pool, family_model_codes,
                )
            except Exception as e:
                logger.warning(
                    f"Porsche [{self.market}] configurator pricing failed: {e}"
                )

    async def _apply_configurator_prices(
        self,
        vehicles: list[VehicleData],
        pool: BrowserPool,
        family_model_codes: dict[str, list[str]],
    ) -> None:
        """Fetch real per-option prices from the Porsche configurator SPA.

        For each model family, navigates to the Porsche configurator page
        for one representative model code and extracts option-price pairs
        from the rendered DOM via ``BrowserPool.extract_dom_prices()``.
        """
        applied_count = 0

        # Limit to 2 families max — the configurator SPA is very heavy
        families_to_probe = list(family_model_codes.items())[:2]

        for family, codes in families_to_probe:
            if not codes:
                continue

            # Use first model code as representative
            model_code = codes[0]
            config_url = (
                "https://configurator.porsche.com/"
                f"{self.market_config.locale}/mode/model/{model_code}"
            )
            logger.info(
                f"Porsche [{self.market}] prices: loading configurator for {family} "
                f"({model_code}) → {config_url}"
            )

            try:
                raw_pairs = await self._extract_configurator_prices(
                    pool, config_url,
                )

                if not raw_pairs:
                    logger.debug(
                        f"Porsche [{self.market}] prices: no DOM prices for "
                        f"{family}"
                    )
                    continue

                # Parse extracted name-price pairs
                price_map: dict[str, float] = {}  # name_lower → price
                for pair in raw_pairs:
                    name = pair.get("name", "")
                    price_text = pair.get("price", "")
                    if not name or not price_text:
                        continue
                    price = _parse_porsche_price(price_text)
                    if price and 50 <= price <= 100_000:
                        price_map[name.lower()] = price
                        # Also store by first significant word for fuzzy match
                        words = name.split()
                        if len(words) > 0:
                            price_map[name] = price  # Keep original case too

                logger.info(
                    f"Porsche [{self.market}] prices: {family} → "
                    f"{len(price_map)} price entries"
                )

                # Apply prices to vehicles in this family
                family_vehicles = [
                    v for v in vehicles
                    if (v.variant or "").lower() == family
                ]

                for v in family_vehicles:
                    # Match existing options by name
                    for opt in v.available_options:
                        if opt.price is not None:
                            continue  # Already has a price
                        matched_price = _match_porsche_price(
                            opt.brand_specific_name, price_map,
                        )
                        if matched_price is not None:
                            opt.price = matched_price
                            opt.currency = self.currency
                            applied_count += 1

                    # Also add priced options from the configurator
                    # that weren't already present
                    existing_names = {
                        (o.standardized_name or o.brand_specific_name.lower())
                        for o in v.available_options
                    }
                    for pair in raw_pairs:
                        name = pair.get("name", "")
                        price_text = pair.get("price", "")
                        if not name or not price_text:
                            continue
                        # Skip total-price attributes, not vehicle options.
                        if re.search(r"Gesamtpreis|Gesamtbetrag", name, re.IGNORECASE):
                            continue
                        price = _parse_porsche_price(price_text)
                        if not price or price < 50 or price > 100_000:
                            continue
                        std = normalize_option_name(name, self.brand)
                        key = std or name.lower()
                        if key in existing_names:
                            continue
                        existing_names.add(key)
                        cat = (
                            get_category(std)
                            if std
                            else _categorize_porsche_option(name)
                        )
                        v.available_options.append(OptionData(
                            standardized_name=std or "",
                            brand_specific_name=name,
                            price=price,
                            category=cat,
                            currency=self.currency,
                        ))
                        applied_count += 1

                    v.available_options = _dedupe_options(
                        v.available_options,
                    )

            except Exception as e:
                logger.debug(
                    f"Porsche [{self.market}] prices: {family} "
                    f"({model_code}) failed: {e}"
                )

            # Rate-limit between configurator loads
            await asyncio.sleep(5.0)

        logger.info(
            f"Porsche [{self.market}] prices: applied {applied_count} prices total"
        )

    async def _extract_configurator_prices(
        self, pool: BrowserPool, url: str,
    ) -> list[dict[str, str]]:
        """Read priced options from the locale-specific configurator DOM.

        ``BrowserPool.extract_dom_prices`` is intentionally German/EUR-specific.
        Porsche's French UI uses a space as the thousands separator, so retain the
        existing careful browser lifecycle here while accepting the market's own
        money formatting.
        """
        context = await pool._browser.new_context(
            user_agent=get_random_user_agent(),
            locale=self.market_config.locale,
            timezone_id="Europe/Berlin",
            extra_http_headers={
                "Accept-Language": self.market_config.accept_language,
            },
        )
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="commit", timeout=120_000)
            await page.wait_for_timeout(20_000)

            try:
                cookie_btn = page.locator("button").filter(
                    has_text=re.compile(
                        r"Alle akzeptieren|Accept All|Akzeptieren|"
                        r"Tout accepter|Accepter",
                        re.IGNORECASE,
                    ),
                )
                if await cookie_btn.count() > 0:
                    await cookie_btn.first.click()
                    await page.wait_for_timeout(3_000)
            except Exception:
                pass

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3_000)
            return await page.evaluate(
                r"""
                () => {
                    const pairs = [];
                    const seen = new Set();
                    const money = /(?=.*\d)(?=.*(?:€|£|\bCHF\b|\bPLN\b|\bSEK\b|\bNOK\b|\bDKK\b|\bkr\b))/;
                    document.querySelectorAll('*').forEach(el => {
                        const text = el.textContent.trim();
                        if (
                            !text || text.length > 100 ||
                            el.children.length !== 0 || !money.test(text)
                        ) {
                            return;
                        }
                        let parent = el.parentElement;
                        for (let i = 0; i < 6 && parent; i++, parent = parent.parentElement) {
                            const parts = parent.textContent.trim().split(text);
                            if (!parts[0]) continue;
                            const name = parts[0].trim().replace(/\s+/g, ' ');
                            if (name.length >= 3 && name.length <= 300) {
                                const key = `${name}|${text}`;
                                if (!seen.has(key)) {
                                    seen.add(key);
                                    pairs.push({name, price: text});
                                }
                                break;
                            }
                        }
                    });
                    return pairs;
                }
                """
            )
        finally:
            await context.close()


# ------------------------------------------------------------------
# Porsche configurator code extraction & price matching
# ------------------------------------------------------------------

def _parse_porsche_price(text: str) -> float | None:
    """Parse Porsche's locale-specific money strings without FX conversion.

    Porsche uses ``158.700,00 €`` in Germany, ``CHF 195'200.00`` in
    Switzerland and ``£110,105.00`` in the UK.  The last separator is a
    decimal mark only when it is followed by exactly two digits; all other
    separators are grouping marks.
    """
    if not text:
        return None
    numeric = re.sub(r"[^0-9.,']", "", text)
    if not numeric or not re.search(r"\d", numeric):
        return None

    numeric = numeric.replace("'", "")
    comma = numeric.rfind(",")
    dot = numeric.rfind(".")
    last_sep = max(comma, dot)
    has_decimal = (
        last_sep >= 0
        and len(numeric) - last_sep - 1 == 2
        and numeric[last_sep + 1:].isdigit()
    )
    if has_decimal:
        integer = re.sub(r"[.,]", "", numeric[:last_sep])
        normalized = f"{integer}.{numeric[last_sep + 1:]}"
    else:
        normalized = re.sub(r"[.,]", "", numeric)
    try:
        return float(normalized)
    except ValueError:
        return None


def _extract_porsche_configurator_codes(
    html: str, locale: str = "de-DE",
) -> list[str]:
    """Extract Porsche configurator model codes from page links.

    Configurator links on model family pages follow the pattern::

        https://configurator.porsche.com/{locale}/mode/model/{code}

    Returns a deduplicated list of model codes.
    """
    codes = re.findall(
        rf'configurator\.porsche\.com/{re.escape(locale)}/mode/model/([A-Za-z0-9]+)',
        html,
    )
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def _match_porsche_price(
    option_name: str, price_map: dict[str, float],
) -> float | None:
    """Match a Porsche option name to extracted configurator prices.

    Tries exact match first, then substring matching (longest name
    first to avoid false positives).
    """
    name_lower = option_name.lower().strip()
    if not name_lower:
        return None

    # Exact match
    if name_lower in price_map:
        return price_map[name_lower]

    # Substring match: check if any price_map key contains or is contained in the name
    for price_name, price in sorted(
        price_map.items(), key=lambda x: -len(x[0]),
    ):
        pn = price_name.lower()
        if len(pn) >= 6 and (pn in name_lower or name_lower in pn):
            return price

    return None


# ------------------------------------------------------------------
# Porsche-specific extraction helpers
# ------------------------------------------------------------------

def _extract_porsche_mpi_options(
    data: dict, brand: str,
) -> list[OptionData]:
    """Extract option data from the MPI compare API response.

    The response at ``mpi.pccompare.aws.porsche-preview.cloud`` contains:
    - ``models`` – list of model dicts with specs, interior options,
      color groups, and drivetrain info
    - ``technicalData`` – list of dicts with ``options`` (transmission,
      battery, drivetrain variants) and ``technicalSpecification``

    Returns recognizable options mapped to standard names.
    """
    options: list[OptionData] = []
    seen: set[str] = set()

    # --- From models list ---
    for model in data.get("models", []):
        if not isinstance(model, dict):
            continue

        # Generic equipment-like lists
        for key in ("features", "equipment", "standardEquipment", "options"):
            items = model.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        name = item.get("name", item.get("label", ""))
                        price = item.get("price", item.get("surcharge"))
                        if name:
                            std = normalize_option_name(name, brand)
                            opt_key = std or name.lower()
                            if opt_key in seen:
                                continue
                            seen.add(opt_key)
                            options.append(OptionData(
                                standardized_name=std or "",
                                brand_specific_name=name,
                                price=float(price) if price else None,
                                category=get_category(std) if std else _categorize_porsche_option(name),
                                code=str(item.get("id", "")),
                            ))

        # Interior design options (leather, Race-Tex, etc.)
        for ido in model.get("interiorDesignOptions", []):
            if isinstance(ido, dict):
                mat_name = ido.get("name", "")
                std = normalize_option_name(mat_name, brand)
                opt_key = std or mat_name.lower()
                if opt_key and opt_key not in seen:
                    seen.add(opt_key)
                    options.append(OptionData(
                        standardized_name=std or "",
                        brand_specific_name=mat_name,
                        price=None,
                        category=get_category(std) if std else "interior",
                    ))

        # Drivetrain info from model metadata
        wd = model.get("wheelDrive", "")
        if wd and "all" in wd.lower():
            if "allrad" not in seen:
                seen.add("allrad")
                options.append(OptionData(
                    standardized_name="allrad",
                    brand_specific_name=wd,
                    price=None,
                    category="drivetrain",
                ))

    # --- From technicalData list ---
    for td in data.get("technicalData", []):
        if not isinstance(td, dict):
            continue
        for opt in td.get("options", []):
            if isinstance(opt, dict):
                name = opt.get("name", "")
                if name:
                    std = normalize_option_name(name, brand)
                    opt_key = std or name.lower()
                    if opt_key in seen:
                        continue
                    seen.add(opt_key)
                    options.append(OptionData(
                        standardized_name=std or "",
                        brand_specific_name=name,
                        price=None,
                        category=get_category(std) if std else _categorize_porsche_option(name),
                        code=str(opt.get("id", "")),
                    ))

    return options


def _categorize_porsche_option(name: str) -> str:
    """Categorize a Porsche option by name when no standard mapping exists."""
    low = name.lower()
    if any(w in low for w in ("transmission", "doppelkupplung", "pdk", "manual", "getriebe")):
        return "drivetrain"
    if any(w in low for w in ("battery", "batterie", "akku")):
        return "drivetrain"
    if any(w in low for w in ("leather", "leder", "race-tex", "alcantara", "interior")):
        return "interior"
    if any(w in low for w in ("sport", "chrono", "pasm", "suspension", "fahrwerk")):
        return "drivetrain"
    return "other"


def _extract_porsche_features_from_html(
    soup: BeautifulSoup, brand: str,
) -> list[OptionData]:
    """Extract feature/option references from Porsche model page HTML.

    Porsche model pages list feature highlights in structured sections.
    Extracts recognizable option names and normalizes them.
    """
    options: list[OptionData] = []
    seen: set[str] = set()

    # Look for feature/highlight text in the page
    # Porsche pages use specific CSS classes for feature sections
    for el in soup.find_all(
        ["h3", "h4", "p", "span", "div"],
        string=re.compile(
            r"(?i)(PASM|PDK|Bose|Sport Chrono|LED Matrix|Panoramadach|"
            r"Head-Up|Sitzheizung|Adaptive|Luftfederung|Park Assist|"
            r"Surround View|Burmester|Lenkradheizung)"
        ),
    ):
        text = el.get_text(strip=True)
        if len(text) < 3 or len(text) > 120:
            continue

        std = normalize_option_name(text, brand)
        if not std:
            continue
        if std in seen:
            continue
        seen.add(std)

        options.append(OptionData(
            standardized_name=std,
            brand_specific_name=text,
            price=None,
            category=get_category(std),
        ))

    return options

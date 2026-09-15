"""Mercedes-Benz configurator crawler.

Strategy:    Pure JSON API extraction against the public OWCC configurator
             backend (``api.oneweb.mercedes-benz.com``).  The www host
             (``www.mercedes-benz.de``) is behind bot protection that
             answers datacenter IPs — including GitHub Actions runners —
             with HTTP 403, so it is no longer used for the daily crawl.

Pipeline:
    1. Type classes (Baureihen) come from the local catalogue in
       ``crawler/data/mercedes_type_classes.json``.  When the public
       configurator overview page happens to be reachable it is used to
       refresh that catalogue; failure is non-fatal.
    2. ``GET /entry?typeClass=<TC>`` returns ``startPage.preConfigs[]`` —
       one entry per motorization with name, base price (gross + net) and
       preview image.
    3. ``GET /entry?typeClass=<TC>&vehicleId=<VID>`` returns
       ``selectableComponents`` — every selectable option of that
       configuration with its **real** gross and net price.

Resilience:  Per-request retry with exponential backoff, user-agent
             rotation, bounded concurrency and per-type-class error
             isolation (a dead Baureihe never fails the whole brand).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType,
    OptionData, VehicleData,
)
from crawler.engines.base_engine import BaseEngine
from crawler.option_mappings import normalize_option_name, get_category
from crawler.brands.registry import BrandRegistry
from crawler.network import retry_with_backoff, get_random_user_agent

logger = logging.getLogger(__name__)

# --- Public endpoints -------------------------------------------------

OWCC_API_BASE = "https://api.oneweb.mercedes-benz.com/owcc-backend/api/v3"
OWCC_MARKET = "de_DE"          # legacy default (German market)
OWCC_PRODUCT = "CCci"


@dataclass(frozen=True)
class MercedesMarket:
    """One Mercedes market served by the OWCC configurator API.

    ``locale`` is the API path segment, ``host`` the public site used for the
    ``Origin``/``Referer`` headers and the vehicle URLs.
    """
    code: str        # ISO 3166-1 alpha-2
    locale: str      # OWCC locale, e.g. "fr_FR"
    host: str        # e.g. "www.mercedes-benz.fr"
    accept_language: str

    @property
    def origin(self) -> str:
        return f"https://{self.host}"


# Verified on 2026-09-15: every locale below returns HTTP 200 for
# ``entry?typeClass=W206`` *and* priced ``selectableComponents`` in the
# market's own currency.  Locales that answered but carry no option prices
# (en_AU, en_IN, pt_BR, th_TH, en_MY, es_AR) or only a handful (ko_KR, en_SG,
# es_MX) are deliberately excluded — they would add rows without prices.
MERCEDES_MARKETS: dict[str, MercedesMarket] = {
    m.code: m for m in (
        MercedesMarket("DE", "de_DE", "www.mercedes-benz.de", "de-DE,de;q=0.9,en;q=0.8"),
        MercedesMarket("AT", "de_AT", "www.mercedes-benz.at", "de-AT,de;q=0.9,en;q=0.8"),
        MercedesMarket("CH", "de_CH", "www.mercedes-benz.ch", "de-CH,de;q=0.9,en;q=0.8"),
        MercedesMarket("FR", "fr_FR", "www.mercedes-benz.fr", "fr-FR,fr;q=0.9,en;q=0.8"),
        MercedesMarket("IT", "it_IT", "www.mercedes-benz.it", "it-IT,it;q=0.9,en;q=0.8"),
        MercedesMarket("ES", "es_ES", "www.mercedes-benz.es", "es-ES,es;q=0.9,en;q=0.8"),
        MercedesMarket("PT", "pt_PT", "www.mercedes-benz.pt", "pt-PT,pt;q=0.9,en;q=0.8"),
        MercedesMarket("NL", "nl_NL", "www.mercedes-benz.nl", "nl-NL,nl;q=0.9,en;q=0.8"),
        MercedesMarket("BE", "nl_BE", "www.mercedes-benz.be", "nl-BE,nl;q=0.9,en;q=0.8"),
        MercedesMarket("LU", "fr_LU", "www.mercedes-benz.lu", "fr-LU,fr;q=0.9,en;q=0.8"),
        MercedesMarket("PL", "pl_PL", "www.mercedes-benz.pl", "pl-PL,pl;q=0.9,en;q=0.8"),
        MercedesMarket("CZ", "cs_CZ", "www.mercedes-benz.cz", "cs-CZ,cs;q=0.9,en;q=0.8"),
        MercedesMarket("SK", "sk_SK", "www.mercedes-benz.sk", "sk-SK,sk;q=0.9,en;q=0.8"),
        MercedesMarket("HU", "hu_HU", "www.mercedes-benz.hu", "hu-HU,hu;q=0.9,en;q=0.8"),
        MercedesMarket("RO", "ro_RO", "www.mercedes-benz.ro", "ro-RO,ro;q=0.9,en;q=0.8"),
        MercedesMarket("DK", "da_DK", "www.mercedes-benz.dk", "da-DK,da;q=0.9,en;q=0.8"),
        MercedesMarket("GB", "en_GB", "www.mercedes-benz.co.uk", "en-GB,en;q=0.9"),
    )
}

MERCEDES_SUPPORTED_MARKETS: tuple[str, ...] = tuple(MERCEDES_MARKETS)


def get_market(market: str) -> MercedesMarket:
    """Look up a supported market by ISO code (case-insensitive)."""
    key = (market or "DE").upper()
    if key not in MERCEDES_MARKETS:
        raise ValueError(
            f"Mercedes market '{market}' not supported. "
            f"Available: {', '.join(MERCEDES_SUPPORTED_MARKETS)}"
        )
    return MERCEDES_MARKETS[key]

CONFIGURATOR_OVERVIEW_URL = (
    "https://www.mercedes-benz.de/passengercars/configurator.html"
)
CONFIGURATOR_DEEPLINK = (
    "https://www.mercedes-benz.de/passengercars/mercedes-benz-cars/"
    "car-configurator.html/start/CCci/DE/de/tc/{type_class}"
)
MODELS_URL = CONFIGURATOR_OVERVIEW_URL

TYPE_CLASS_CATALOGUE = (
    Path(__file__).resolve().parent.parent / "data" / "mercedes_type_classes.json"
)

# Max vehicles (motorizations) to probe for option prices per crawl.
MAX_OPTION_PROBES = int(os.getenv("MERCEDES_MAX_OPTION_PROBES", "160"))
# Parallel API requests.  The API is a CDN-fronted read endpoint; keep
# this low enough to stay polite.
MAX_CONCURRENCY = int(os.getenv("MERCEDES_MAX_CONCURRENCY", "4"))

# Component-id prefixes used by the configurator API.
COMPONENT_PREFIX_CATEGORY = {
    "LU": "exterior",    # Lackierung (paint)
    "AU": "interior",    # Polster / Ausstattung (upholstery)
    "PV": "comfort",     # Vorrüstungen / comfort extras
    "PC": "packages",    # Pakete (packages)
    "GC": "drivetrain",  # Getriebe (transmission)
    "SA": "",            # Sonderausstattung — resolved by title heuristic
    "SC": "",            # Steuercodes — resolved by title heuristic
}

# Internal sales/steering codes that are exposed by the API but are not
# customer-facing equipment.
NON_OPTION_NAME_PATTERNS = (
    "steuercode",
    "einsatzfahrzeuge",
    "sonderschutz",
    "code für",
    "werkscode",
)

FUEL_BY_ENGINE_TYPE = {
    "ELECTRIC": "electric",
    "BEV": "electric",
    "HYBRID": "hybrid",
    "PLUGIN_HYBRID": "hybrid",
}

# Vehicle type mapping for Mercedes tags (kept for SSR fallback parsing)
FUEL_MAP = {
    "Elektrisch": "electric",
    "Electric": "electric",
    "Hybrid": "hybrid",
    "Plug-in": "hybrid",
    "AMG": "",
    "Neu": "",
    "New": "",
}


# ---------------------------------------------------------------------
# Type-class catalogue
# ---------------------------------------------------------------------

def load_type_classes(path: Path | None = None) -> dict[str, str]:
    """Load the ``{type_class: model_name}`` catalogue from disk."""
    p = path or TYPE_CLASS_CATALOGUE
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        classes = data.get("type_classes", {})
        return {k: v for k, v in classes.items() if isinstance(v, str)}
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Mercedes: type-class catalogue unreadable ({e})")
        return {}


def parse_type_classes_from_html(html: str) -> dict[str, str]:
    """Parse ``{type_class: model_name}`` pairs from the overview page.

    Configurator links look like
    ``…/car-configurator.html/start/CCci/DE/de/tc/W206``.  The link text
    carries the model name; generic call-to-action labels such as
    "Fahrzeug konfigurieren" are ignored.
    """
    found: dict[str, str] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml always present in prod
        soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        m = re.search(r"/tc/([A-Z0-9]{3,6})", a["href"])
        if not m:
            continue
        label = " ".join(a.get_text(" ", strip=True).split())
        if not label or "konfigurieren" in label.lower():
            continue
        found.setdefault(m.group(1), label)

    # Type classes without a usable label still count as discovered.
    for m in re.finditer(r"/tc/([A-Z0-9]{3,6})", html):
        found.setdefault(m.group(1), "")

    return found


# ---------------------------------------------------------------------
# OWCC configurator API client
# ---------------------------------------------------------------------

class MercedesConfiguratorAPI:
    """Thin, resilient client for the public OWCC configurator API.

    The ``session_id`` path segment is an opaque per-visit identifier;
    the backend accepts any value, so a random one is generated per run
    instead of hard-coding a value that can rot.
    """

    def __init__(
        self,
        session_id: str | None = None,
        timeout: float = 45.0,
        max_retries: int = 3,
        market: str = "DE",
    ) -> None:
        self.session_id = session_id or f"{random.getrandbits(32):08x}"
        self.timeout = timeout
        self.max_retries = max_retries
        self.market = get_market(market)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": get_random_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": self.market.accept_language,
            "Origin": self.market.origin,
            "Referer": f"{self.market.origin}/",
        })

    # --- low level ---

    def _url(self, path: str) -> str:
        return (
            f"{OWCC_API_BASE}/{self.market.locale}/{OWCC_PRODUCT}/"
            f"{self.session_id}/{path.lstrip('/')}"
        )

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET *path* with retries. Raises on permanent failure."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    self._url(path), params=params, timeout=self.timeout,
                )
                if resp.status_code in (400, 404):
                    raise LookupError(
                        f"HTTP {resp.status_code} for {path} {params or ''}"
                    )
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code} for {path}")
                return resp.json()
            except LookupError:
                raise
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = 1.5 * (2 ** attempt) + random.uniform(0, 0.75)
                    logger.debug(
                        f"Mercedes API retry {attempt + 1}/{self.max_retries} "
                        f"for {path}: {e} (sleep {delay:.1f}s)"
                    )
                    time.sleep(delay)
                    self.session.headers["User-Agent"] = get_random_user_agent()
        raise RuntimeError(f"Mercedes API failed for {path}: {last_error}")

    # --- endpoints ---

    def entry(self, type_class: str, vehicle_id: str | None = None) -> dict:
        """Configurator entry payload for a type class (optionally a config)."""
        params: dict[str, Any] = {"typeClass": type_class}
        if vehicle_id:
            params["vehicleId"] = vehicle_id
        data = self.get_json("entry", params)
        return data if isinstance(data, dict) else {}

    def components_info(self, model_id: str) -> dict:
        """Descriptive metadata (names, media, descriptions) per component."""
        data = self.get_json(f"models/{model_id}/componentsInfo")
        if isinstance(data, dict):
            info = data.get("componentsInfo", {})
            return info if isinstance(info, dict) else {}
        return {}

    def discover_type_classes(self) -> dict[str, str]:
        """Best-effort refresh of the catalogue from the overview page.

        The www host blocks datacenter IPs, so this is expected to fail
        in CI.  Failure is silent by design — the local catalogue is the
        source of truth.
        """
        try:
            resp = self.session.get(
                CONFIGURATOR_OVERVIEW_URL,
                headers={"Accept": "text/html,application/xhtml+xml"},
                timeout=15,
            )
            if resp.status_code != 200 or len(resp.text) < 5_000:
                return {}
            return parse_type_classes_from_html(resp.text)
        except Exception as e:
            logger.debug(f"Mercedes: catalogue refresh unavailable ({e})")
            return {}


# ---------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------

def _price_of(obj: Any) -> float | None:
    """Read a gross price from an OWCC ``price`` object."""
    if not isinstance(obj, dict):
        return None
    value = obj.get("price")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _net_price_of(obj: Any) -> float | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get("netPrice")
    return float(value) if isinstance(value, (int, float)) else None


def _fuel_type_from_preconfig(pre_config: dict) -> str:
    """Derive ``electric`` / ``hybrid`` / ``diesel`` / ``petrol``."""
    engine = (
        pre_config.get("plsInformationSection", {})
        .get("technicalData", {})
        .get("engine", {})
    )
    engine_type = str(engine.get("type", "")).upper()
    mapped = FUEL_BY_ENGINE_TYPE.get(engine_type, "")
    if mapped:
        return mapped

    name = pre_config.get("motorizationName", "")
    low = name.lower()
    if "elektrisch" in low or low.startswith("eq") or " eq " in low:
        return "electric"
    if "hybrid" in low:
        return "hybrid"
    if engine_type in ("COMBUSTOR", "ICE", ""):
        if re.search(r"\b\d{2,3}\s?d\b", name) or "cdi" in low or " d " in low:
            return "diesel"
        return "petrol" if engine_type else ""
    return ""


def parse_pre_configs(
    entry_payload: dict,
    type_class: str,
    model_name: str,
    brand: str = "Mercedes-Benz",
    market: str = "DE",
) -> list[VehicleData]:
    """Turn ``startPage.preConfigs`` into :class:`VehicleData` records."""
    vehicles: list[VehicleData] = []
    pre_configs = (
        entry_payload.get("startPage", {}).get("preConfigs", [])
        if isinstance(entry_payload.get("startPage"), dict)
        else []
    )
    seen: set[str] = set()

    for pc in pre_configs:
        if not isinstance(pc, dict):
            continue
        vehicle_id = pc.get("vehicleId", "")
        motorization = (pc.get("motorizationName") or "").strip()
        price_info = pc.get("priceInformation", {})
        base_price = _price_of(price_info.get("basePrice")) or _price_of(
            price_info.get("configurationPrice")
        )
        if not vehicle_id or not motorization or not base_price:
            continue
        if motorization in seen:
            continue
        seen.add(motorization)

        preview = pc.get("previewImage", {})
        vehicles.append(VehicleData(
            brand=brand,
            model=model_name or type_class,
            variant=motorization,
            base_price=base_price,
            currency=price_info.get("currencyISO", "EUR") or "EUR",
            market=(market or "DE").upper(),
            fuel_type=_fuel_type_from_preconfig(pc),
            url=_vehicle_url(type_class, market),
            image_url=preview.get("url", "") if isinstance(preview, dict) else "",
            raw_data={
                "type_class": type_class,
                "vehicle_id": vehicle_id,
                "pre_config_id": pc.get("preConfigId", ""),
                "base_price_net": _net_price_of(price_info.get("basePrice")),
            },
        ))

    return vehicles


def _vehicle_url(type_class: str, market: str = "DE") -> str:
    """Public configurator link for a type class.

    Only the German deep link path is verified, so other markets link to
    their own configurator host root rather than to a guessed path.
    """
    code = (market or "DE").upper()
    if code == "DE":
        return CONFIGURATOR_DEEPLINK.format(type_class=type_class)
    try:
        return f"{get_market(code).origin}/"
    except ValueError:
        return CONFIGURATOR_DEEPLINK.format(type_class=type_class)


def _category_for_component(component_id: str, name: str) -> str:
    """Category from the component-id prefix, falling back to the title."""
    prefix = component_id.split("-")[0].upper() if "-" in component_id else ""
    mapped = COMPONENT_PREFIX_CATEGORY.get(prefix, "")
    if mapped:
        return mapped
    return _guess_category_from_title(name)


def parse_selectable_components(
    entry_payload: dict,
    brand: str = "Mercedes-Benz",
    *,
    include_standard: bool = False,
    include_zero_price: bool = False,
) -> list[OptionData]:
    """Turn ``selectableComponents`` into priced :class:`OptionData`.

    Only *selectable* (non-standard) components with a real extra-cost
    price are returned by default — those are the options the price index
    is about.  Internal sales/steering codes are filtered out.
    """
    components = entry_payload.get("selectableComponents", {})
    if not isinstance(components, dict):
        return []

    options: list[OptionData] = []
    for comp_id, comp in components.items():
        if not isinstance(comp, dict):
            continue
        name = (comp.get("name") or "").strip()
        if not name or len(name) < 3:
            continue
        if comp.get("standard") and not include_standard:
            continue
        low = name.lower()
        if any(pat in low for pat in NON_OPTION_NAME_PATTERNS):
            continue

        price = _price_of(comp.get("price"))
        if price is None:
            continue
        if price < 0 or price > 250_000:
            continue
        if price == 0 and not include_zero_price:
            continue

        std = normalize_option_name(name, brand)
        options.append(OptionData(
            standardized_name=std or "",
            brand_specific_name=name,
            price=price,
            category=get_category(std) if std else _category_for_component(comp_id, name),
            code=str(comp.get("id") or comp_id),
        ))

    return options


# ---------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------

@BrandRegistry.register
class MercedesCrawler(BrandCrawler):
    brand = "Mercedes-Benz"
    base_url = "https://www.mercedes-benz.de"
    configurator_url = CONFIGURATOR_OVERVIEW_URL
    SUPPORTED_MARKETS = MERCEDES_SUPPORTED_MARKETS

    @property
    def market_config(self) -> MercedesMarket:
        return get_market(self.market)

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=0.25,
            confidence=0.95,
            notes=(
                "Direct OWCC configurator API (api.oneweb.mercedes-benz.com): "
                "type-class catalogue → entry preConfigs (base prices) → "
                "entry selectableComponents (real option prices). "
                "No www host, no browser — immune to the HTTP 403 bot wall. "
                f"Market: {self.market} (locale {self.market_config.locale})."
            ),
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=2.0,
            )
        except Exception as e:
            logger.warning(
                f"Mercedes [{self.market}]: all retry attempts exhausted: {e}"
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

        api = MercedesConfiguratorAPI(market=self.market)
        catalogue = load_type_classes()
        if not catalogue:
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                errors=["Type-class catalogue missing or empty"],
                strategy_used=cfg,
                duration_seconds=time.time() - start_time,
            )

        # Best-effort catalogue refresh (works only outside blocked networks).
        # Only the German overview URL is verified, so discovery runs for DE.
        if self.market == "DE":
            discovered = await asyncio.to_thread(api.discover_type_classes)
            new_classes = {
                tc: name for tc, name in discovered.items() if tc not in catalogue
            }
            if new_classes:
                logger.info(
                    f"Mercedes: discovered new type classes {sorted(new_classes)}"
                )
                catalogue = {**catalogue, **{k: v for k, v in new_classes.items() if v}}

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        # --- Phase 1: models & base prices, one request per type class ---
        async def fetch_models(type_class: str, model_name: str) -> list[VehicleData]:
            async with semaphore:
                await asyncio.sleep(cfg.rate_limit_seconds)
                try:
                    payload = await asyncio.to_thread(api.entry, type_class)
                except LookupError:
                    logger.info(
                        f"Mercedes [{self.market}]: type class {type_class} "
                        "not offered in this market — skipped"
                    )
                    return []
                except Exception as e:
                    errors.append(f"{type_class}: {e}")
                    return []
            return parse_pre_configs(
                payload, type_class, model_name, self.brand, self.market
            )

        model_results = await asyncio.gather(*[
            fetch_models(tc, name) for tc, name in catalogue.items()
        ])
        vehicles: list[VehicleData] = [v for group in model_results for v in group]

        if not vehicles:
            errors.append(
                f"No vehicles returned by the configurator API for market {self.market}"
            )
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                vehicles=[],
                errors=errors,
                strategy_used=cfg,
                duration_seconds=time.time() - start_time,
            )

        logger.info(
            f"Mercedes [{self.market}]: {len(vehicles)} motorizations across "
            f"{len(catalogue)} type classes"
        )

        # --- Phase 2: real option prices per configuration ---
        await self._enrich_options(vehicles, api, cfg, semaphore, errors)

        priced = sum(
            1 for v in vehicles for o in v.available_options
            if o.price is not None and o.price > 0
        )
        logger.info(f"Mercedes [{self.market}]: {priced} priced options extracted")
        if not priced:
            errors.append(
                "No priced options extracted from selectableComponents "
                f"(market {self.market})"
            )

        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    async def _enrich_options(
        self,
        vehicles: Iterable[VehicleData],
        api: MercedesConfiguratorAPI,
        config: CrawlConfig,
        semaphore: asyncio.Semaphore,
        errors: list[str],
    ) -> None:
        """Attach real option prices to each vehicle configuration."""
        targets = [
            v for v in vehicles
            if v.raw_data.get("vehicle_id") and v.raw_data.get("type_class")
        ][:MAX_OPTION_PROBES]

        async def enrich(vehicle: VehicleData) -> None:
            type_class = vehicle.raw_data["type_class"]
            vehicle_id = vehicle.raw_data["vehicle_id"]
            async with semaphore:
                await asyncio.sleep(config.rate_limit_seconds)
                try:
                    payload = await asyncio.to_thread(
                        api.entry, type_class, vehicle_id,
                    )
                except Exception as e:
                    logger.debug(
                        f"Mercedes options: {vehicle.model} {vehicle.variant} failed: {e}"
                    )
                    errors.append(f"{type_class} options: {e}")
                    return

            options = parse_selectable_components(payload, self.brand)
            if options:
                vehicle.available_options = _dedupe_options(options)
                logger.debug(
                    f"Mercedes options: {vehicle.model} {vehicle.variant} → "
                    f"{len(vehicle.available_options)} options"
                )

        await asyncio.gather(*[enrich(v) for v in targets])


# ------------------------------------------------------------------
# TypeClass extraction from configurator links
# ------------------------------------------------------------------

def _extract_type_class(html: str) -> str | None:
    """Extract the Mercedes typeClass from car-configurator links in HTML.

    Configurator links follow the pattern::

        car-configurator.html/start/CCci/DE/de/tc/{typeClass}

    Returns the first typeClass found (e.g. ``"W206"``) or ``None``.
    """
    match = re.search(
        r'car-configurator\.html/start/CCci/DE/de/tc/([A-Z0-9]+)',
        html,
    )
    return match.group(1) if match else None


# ------------------------------------------------------------------
# Mercedes ssrData equipment extraction
# ------------------------------------------------------------------

def _extract_equipment_from_ssr(
    soup: BeautifulSoup, brand: str,
) -> list[OptionData]:
    """Extract equipment items from Mercedes model-page ``ssrData`` scripts.

    Mercedes model overview pages embed structured equipment objects in
    large ``<script>`` tags.  Each object has ``equipmentId``, ``title``,
    ``isIncluded`` (standard vs. extra-cost), and a description.
    Items marked ``isIncluded=False`` are optional extras.

    Returns a list of :class:`OptionData` for **non-included** equipment
    (i.e. available paid options).  Prices are set to ``None`` because
    individual option pricing is only available in the configurator.
    """
    results: list[OptionData] = []

    for script in soup.find_all("script"):
        if not script.string or "equipmentId" not in script.string:
            continue

        text = script.string.strip()

        # ssrData scripts use  window.ssrData["<hash>"] = { ... };
        match = re.search(
            r'\["([a-f0-9]+)"\]\s*=\s*({.*})\s*;?\s*$',
            text,
            re.DOTALL,
        )
        if not match:
            continue

        try:
            data = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue

        # Recursively find equipment-like dicts in the tree.
        equip_items = _find_equipment_items(data)
        for item in equip_items:
            title = item.get("title", "").strip()
            eid = item.get("equipmentId", "")
            is_included = item.get("isIncluded", True)

            if not title or len(title) < 3:
                continue

            # Only non-included items are selectable options
            if is_included:
                continue

            std = normalize_option_name(title, brand)
            cat = get_category(std) if std else _guess_category_from_title(title)

            results.append(OptionData(
                standardized_name=std or "",
                brand_specific_name=title,
                price=None,  # not available on model overview pages
                category=cat,
                code=eid,
            ))

        if results:
            break  # Typically only one large ssrData script

    return results


def _find_equipment_items(obj: Any, depth: int = 0) -> list[dict]:
    """Recursively collect dicts containing ``equipmentId`` and ``title``."""
    if depth > 12 or obj is None:
        return []
    results: list[dict] = []
    if isinstance(obj, dict):
        if "equipmentId" in obj and "title" in obj:
            results.append(obj)
        for v in obj.values():
            results.extend(_find_equipment_items(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_equipment_items(item, depth + 1))
    return results


def _guess_category_from_title(title: str) -> str:
    """Heuristic category guess from an equipment title string."""
    low = title.lower()
    if any(w in low for w in ("sound", "audio", "burmester", "musik")):
        return "sound"
    if any(w in low for w in ("display", "head-up", "digital", "mbux")):
        return "technology"
    if any(w in low for w in ("sitz", "lenkrad", "heizung", "klima", "komfort", "standheizung")):
        return "comfort"
    if any(w in low for w in ("led", "licht", "light", "scheinwerfer")):
        return "lighting"
    if any(w in low for w in ("kamera", "assistent", "pre-safe", "airbag", "brems")):
        return "safety"
    if any(w in low for w in ("fahrwerk", "lenkung", "bremse", "4matic", "antrieb")):
        return "drivetrain"
    if any(w in low for w in ("dach", "panoram", "anhäng", "glas", "akustik")):
        return "exterior"
    if any(w in low for w in ("ambient", "innenraum", "leder", "mittelkonsole", "sonnen")):
        return "interior"
    if any(w in low for w in ("paket",)):
        return "packages"
    return "other"


# ------------------------------------------------------------------
# Generic option-extraction helpers (reused across brands)
# ------------------------------------------------------------------

def _search_json_for_options(
    data: Any, brand: str, *, depth: int = 0, _seen_ids: set | None = None,
) -> list[OptionData]:
    """Recursively search a JSON tree for option/equipment-like objects.

    Looks for dicts that have a name-like key **and** a price-like key
    with a sensible automotive option price (€50 – €25 000).
    """
    if depth > 12 or data is None:
        return []
    if _seen_ids is None:
        _seen_ids = set()

    results: list[OptionData] = []

    if isinstance(data, dict):
        # Check if this dict itself looks like an option entry
        name_val = _extract_name(data)
        price_val = _extract_price(data)

        if name_val and price_val is not None and 50 <= price_val <= 25_000:
            obj_id = id(data)
            if obj_id not in _seen_ids:
                _seen_ids.add(obj_id)
                std = normalize_option_name(name_val, brand)
                results.append(OptionData(
                    standardized_name=std or "",
                    brand_specific_name=name_val,
                    price=price_val,
                    category=get_category(std) if std else _guess_category(data),
                    code=str(data.get("code", data.get("id", ""))),
                ))

        # Recurse into values (prioritise equipment-like keys)
        for key in sorted(data.keys(), key=lambda k: 0 if _is_equipment_key(k) else 1):
            results.extend(
                _search_json_for_options(data[key], brand, depth=depth + 1, _seen_ids=_seen_ids)
            )

    elif isinstance(data, list):
        for item in data:
            results.extend(
                _search_json_for_options(item, brand, depth=depth + 1, _seen_ids=_seen_ids)
            )

    return results


def _extract_options_from_scripts(soup: BeautifulSoup, brand: str) -> list[OptionData]:
    """Search all <script> tags for embedded JSON containing options."""
    options: list[OptionData] = []
    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string.strip()

        # Try parsing as JSON
        for blob in _find_json_blobs(text):
            try:
                data = json.loads(blob)
                options.extend(_search_json_for_options(data, brand))
            except (json.JSONDecodeError, RecursionError):
                continue

    return options


def _extract_options_from_text(html: str, brand: str) -> list[OptionData]:
    """Regex-based fallback: find price patterns near known option keywords."""
    options: list[OptionData] = []

    # Pattern: option-like text followed by price  ("Lenkradheizung ... 350,00 €")
    price_pat = re.compile(
        r'([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s\-]{4,50}?)\s*'
        r'(?:ab\s+)?'
        r'([\d]{1,3}(?:\.?\d{3})*(?:,\d{2})?)\s*€',
        re.MULTILINE,
    )
    for m in price_pat.finditer(html):
        name = m.group(1).strip()
        price = BaseEngine.parse_price(m.group(2))
        if not price or price < 50 or price > 25_000:
            continue
        std = normalize_option_name(name, brand)
        if std:
            options.append(OptionData(
                standardized_name=std,
                brand_specific_name=name,
                price=price,
                category=get_category(std),
            ))

    return options


# --- Utility helpers ---

_NAME_KEYS = ("name", "label", "title", "bezeichnung", "description", "Name", "Label")
_PRICE_KEYS = (
    "price", "preis", "Price", "Preis", "grossPrice", "netPrice",
    "basePrice", "formattedPrice", "priceFormatted", "amount",
    "surcharge", "Aufpreis",
)
_EQUIP_KEYS = {
    "equipment", "equipments", "options", "extras", "ausstattung",
    "sonderausstattung", "packages", "features", "accessories",
    "sonderausstattungen", "Ausstattung", "Equipment",
}


def _extract_name(d: dict) -> str | None:
    for k in _NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and 2 < len(v) < 120:
            return v.strip()
    return None


def _extract_price(d: dict) -> float | None:
    for k in _PRICE_KEYS:
        v = d.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
        if isinstance(v, str):
            p = BaseEngine.parse_price(v)
            if p and p > 0:
                return p
    return None


def _is_equipment_key(key: str) -> bool:
    return key.lower() in _EQUIP_KEYS


def _guess_category(d: dict) -> str:
    cat = d.get("category", d.get("gruppe", d.get("group", "")))
    if isinstance(cat, str) and cat:
        low = cat.lower()
        if any(w in low for w in ("exterior", "außen", "aussen")):
            return "exterior"
        if any(w in low for w in ("interior", "innen")):
            return "interior"
        if any(w in low for w in ("comfort", "komfort")):
            return "comfort"
        if any(w in low for w in ("safety", "sicher")):
            return "safety"
        if any(w in low for w in ("drive", "antrieb", "fahrwerk")):
            return "drivetrain"
        if any(w in low for w in ("light", "licht")):
            return "lighting"
        if any(w in low for w in ("sound", "audio", "media")):
            return "sound"
    return "other"


def _find_json_blobs(text: str) -> list[str]:
    """Heuristically extract JSON objects/arrays from script text."""
    blobs: list[str] = []
    # Look for top-level assignments containing JSON
    for m in re.finditer(r'=\s*({[\s\S]{20,}?})\s*[;\n]', text):
        blobs.append(m.group(1))
    for m in re.finditer(r'=\s*(\[[\s\S]{20,}?\])\s*[;\n]', text):
        blobs.append(m.group(1))
    return blobs


def _dedupe_options(options: list[OptionData]) -> list[OptionData]:
    """Remove duplicate options, keeping the first occurrence."""
    seen: set[str] = set()
    deduped: list[OptionData] = []
    for o in options:
        key = o.standardized_name or o.brand_specific_name.lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(o)
    return deduped

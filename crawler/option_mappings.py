"""Option name standardization across vehicle brands.

Maps brand-specific option names (e.g. '4MATIC', 'quattro', 'xDrive')
to standardized keys (e.g. 'allrad') for cross-brand comparison.
"""

from __future__ import annotations


# --- Option Category Labels ---

OPTION_CATEGORIES: dict[str, str] = {
    "drivetrain": "Drivetrain",
    "comfort": "Comfort",
    "safety": "Safety",
    "exterior": "Exterior",
    "interior": "Interior",
    "technology": "Technology",
    "sound": "Sound & Entertainment",
    "lighting": "Lighting",
    "packages": "Packages",
    "other": "Other",
}


# --- Standard Option Definitions ---
# Each entry maps a standardized key to its aliases, category, description,
# and brand-specific display names.

OPTION_DEFINITIONS: dict[str, dict] = {
    "allrad": {
        "aliases": [
            "4MATIC", "4MATIC+", "Quattro", "quattro", "xDrive",
            "AWD", "Allrad", "Allradantrieb", "e-quattro",
        ],
        "category": "drivetrain",
        "description": "All-wheel drive system",
        "brand_names": {
            "Mercedes-Benz": "4MATIC",
            "Audi": "quattro",
            "BMW": "xDrive",
            "Porsche": "AWD",
        },
    },
    "steering_wheel_heating": {
        "aliases": [
            "Lenkradheizung", "Steering Wheel Heater", "Heated Steering Wheel",
            "Beheizbares Lenkrad", "Lenkrad beheizbar", "Lenkrad-Heizung",
        ],
        "category": "comfort",
        "description": "Heated steering wheel",
        "brand_names": {
            "Mercedes-Benz": "Lenkradheizung",
            "Audi": "Lenkradheizung",
            "BMW": "Lenkradheizung",
            "Porsche": "Heated Steering Wheel",
        },
    },
    "head_up_display": {
        "aliases": [
            "Head Up Display", "HUD", "Head-Up Display", "Head-Up-Display",
            "Head-up Display", "Head-up-Display",
        ],
        "category": "technology",
        "description": "Head-up display projecting info on windshield",
        "brand_names": {
            "Mercedes-Benz": "Head-Up-Display",
            "Audi": "Head-up Display",
            "BMW": "Head-Up Display",
            "Porsche": "Head-Up Display",
        },
    },
    "panoramic_roof": {
        "aliases": [
            "Panoramadach", "Panoramic Roof", "Panoramic Sunroof",
            "Panorama-Schiebedach", "Panorama-Glasdach", "Panoramaglasdach",
            "Schiebe-Hebe-Dach", "Panorama Dach",
        ],
        "category": "exterior",
        "description": "Panoramic glass roof / sunroof",
        "brand_names": {
            "Mercedes-Benz": "Panorama-Schiebedach",
            "Audi": "Panorama-Glasdach",
            "BMW": "Panoramadach",
            "Porsche": "Panoramadach",
        },
    },
    "premium_sound": {
        "aliases": [
            "Harman Kardon", "Bose", "Bang & Olufsen", "Bang&Olufsen",
            "Burmester", "Burmester Surround", "Burmester 3D",
            "Burmester® Surround-Soundsystem",
            "Premium Sound System", "High-End Sound", "Bose Surround Sound",
            "Bose® Surround Sound-System", "Bang & Olufsen Premium Sound",
        ],
        "category": "sound",
        "description": "Premium audio/sound system",
        "brand_names": {
            "Mercedes-Benz": "Burmester",
            "Audi": "Bang & Olufsen",
            "BMW": "Harman Kardon",
            "Porsche": "Bose",
        },
    },
    "leather_seats": {
        "aliases": [
            "Leather", "Leder", "Leather Interior", "Leather Upholstery",
            "Ledersitze", "Lederausstattung", "Lederpolsterung",
            "Nappaleder", "Vollleder",
        ],
        "category": "interior",
        "description": "Leather seat upholstery",
        "brand_names": {
            "Mercedes-Benz": "Lederausstattung",
            "Audi": "Lederausstattung",
            "BMW": "Lederausstattung",
            "Porsche": "Leather Interior",
        },
    },
    "adaptive_cruise_control": {
        "aliases": [
            "Adaptive Cruise Control", "ACC", "Distronic", "DISTRONIC",
            "Abstandsregeltempomat", "Adaptive Geschwindigkeitsregelung",
            "Active Cruise Control", "Abstandstempomat",
        ],
        "category": "safety",
        "description": "Adaptive cruise control with distance keeping",
        "brand_names": {
            "Mercedes-Benz": "DISTRONIC",
            "Audi": "Adaptive Cruise Control",
            "BMW": "Active Cruise Control",
            "Porsche": "Adaptive Cruise Control",
        },
    },
    "matrix_led": {
        "aliases": [
            "MULTIBEAM LED", "Matrix LED", "Matrix-LED", "Adaptive LED",
            "LED Matrix", "IntelliLux LED", "Digital Light", "DIGITAL LIGHT",
            "HD Matrix LED", "Adaptive LED-Scheinwerfer",
        ],
        "category": "lighting",
        "description": "Matrix/adaptive LED headlights",
        "brand_names": {
            "Mercedes-Benz": "DIGITAL LIGHT",
            "Audi": "Matrix LED",
            "BMW": "Adaptive LED",
            "Porsche": "LED Matrix",
        },
    },
    "ambient_lighting": {
        "aliases": [
            "Ambientebeleuchtung", "Ambient Lighting", "Ambient Light",
            "Interior Lighting", "Innenraumbeleuchtung",
            "Ambiente-Beleuchtung",
        ],
        "category": "interior",
        "description": "Configurable ambient interior lighting",
        "brand_names": {
            "Mercedes-Benz": "Ambientebeleuchtung",
            "Audi": "Ambientebeleuchtung",
            "BMW": "Ambient Lighting",
            "Porsche": "Ambient Lighting",
        },
    },
    "air_suspension": {
        "aliases": [
            "Luftfederung", "Air Suspension", "AIRMATIC",
            "Adaptive Air Suspension", "Luftfederung adaptiv",
            "Adaptive Luftfederung", "Luftfederung komfort",
        ],
        "category": "comfort",
        "description": "Air suspension system",
        "brand_names": {
            "Mercedes-Benz": "AIRMATIC",
            "Audi": "Adaptive Luftfederung",
            "BMW": "Adaptive Air Suspension",
            "Porsche": "Adaptive Air Suspension",
        },
    },
    "seat_heating_front": {
        "aliases": [
            "Sitzheizung", "Sitzheizung vorn", "Heated Front Seats",
            "Seat Heating", "Sitzheizung vorne", "Beheizte Vordersitze",
            "Sitzheizung für Fahrer und Beifahrer",
        ],
        "category": "comfort",
        "description": "Heated front seats",
        "brand_names": {
            "Mercedes-Benz": "Sitzheizung vorn",
            "Audi": "Sitzheizung vorne",
            "BMW": "Sitzheizung vorn",
            "Porsche": "Seat Heating",
        },
    },
    "parking_assist": {
        "aliases": [
            "Parkassistent", "Park Assist", "Parking Assist",
            "Einparkassistent", "Parktronic", "PARKTRONIC",
            "Remote Park Assist", "Park Assist Plus",
        ],
        "category": "safety",
        "description": "Automated parking assistance",
        "brand_names": {
            "Mercedes-Benz": "PARKTRONIC",
            "Audi": "Einparkassistent",
            "BMW": "Park Assist",
            "Porsche": "Park Assist",
        },
    },
    "rear_camera": {
        "aliases": [
            "Rückfahrkamera", "Rear Camera", "Reversing Camera",
            "360° Kamera", "360°-Kamera", "Surround View",
            "360-Grad-Kamera", "Surround-View-System",
        ],
        "category": "safety",
        "description": "Rear-view / 360° camera system",
        "brand_names": {
            "Mercedes-Benz": "Rückfahrkamera",
            "Audi": "Rückfahrkamera",
            "BMW": "Rear Camera",
            "Porsche": "Rear Camera",
        },
    },
    "wireless_charging": {
        "aliases": [
            "Wireless Charging", "Kabelloses Laden", "Induktives Laden",
            "Qi Charging", "Qi-Ladeschale",
            "Smartphone-Ablage mit induktiver Ladefunktion",
        ],
        "category": "technology",
        "description": "Wireless phone charging pad",
        "brand_names": {
            "Mercedes-Benz": "Kabelloses Laden",
            "Audi": "Induktives Laden",
            "BMW": "Wireless Charging",
            "Porsche": "Wireless Charging",
        },
    },
    "sport_suspension": {
        "aliases": [
            "Sportfahrwerk", "Sport Suspension", "Sports Suspension",
            "M Sportfahrwerk", "S line Fahrwerk", "PASM",
            "Porsche Active Suspension Management",
        ],
        "category": "drivetrain",
        "description": "Sport-tuned suspension",
        "brand_names": {
            "Mercedes-Benz": "Sportfahrwerk",
            "Audi": "Sportfahrwerk",
            "BMW": "M Sportfahrwerk",
            "Porsche": "PASM",
        },
    },
}


# --- Reverse Lookup Table ---
# Maps each alias (lowercased) to its standardized option key.

_ALIAS_MAP: dict[str, str] = {}
for _std_name, _defn in OPTION_DEFINITIONS.items():
    for _alias in _defn["aliases"]:
        _ALIAS_MAP[_alias.lower()] = _std_name


# --- Public API ---


def normalize_option_name(option_name: str, brand: str = "") -> str | None:
    """Map a brand-specific option name to the standardized key.

    Returns the standardized key (e.g. ``"allrad"``) or ``None`` if the
    option name is not recognized.  Uses exact match first, then falls
    back to substring matching (longest alias wins).
    """
    name_lower = option_name.strip().lower()

    # 1) Exact alias match
    if name_lower in _ALIAS_MAP:
        return _ALIAS_MAP[name_lower]

    # 2) Substring match — longest alias first to avoid false positives
    for alias_lower, std_name in sorted(
        _ALIAS_MAP.items(), key=lambda x: -len(x[0])
    ):
        if len(alias_lower) >= 4 and alias_lower in name_lower:
            return std_name

    return None


def get_category(standardized_name: str) -> str:
    """Return the category key for a standardized option name."""
    defn = OPTION_DEFINITIONS.get(standardized_name)
    return defn["category"] if defn else "other"


def get_category_label(category_key: str) -> str:
    """Return the human-readable category label."""
    return OPTION_CATEGORIES.get(category_key, "Other")


def get_brand_name(standardized_name: str, brand: str) -> str:
    """Return the brand-specific display name for a standard option."""
    defn = OPTION_DEFINITIONS.get(standardized_name)
    if defn and brand in defn.get("brand_names", {}):
        return defn["brand_names"][brand]
    return standardized_name


def get_description(standardized_name: str) -> str:
    """Return the description for a standardized option name."""
    defn = OPTION_DEFINITIONS.get(standardized_name)
    return defn["description"] if defn else ""


def list_all_options() -> list[dict]:
    """List all defined standard options with metadata."""
    result = []
    for std_name, defn in OPTION_DEFINITIONS.items():
        result.append({
            "standardized_name": std_name,
            "category": defn["category"],
            "description": defn["description"],
            "brand_names": defn.get("brand_names", {}),
            "alias_count": len(defn["aliases"]),
        })
    return result


# --- Reference Prices ---
# Publicly available approximate price ranges (EUR) for common options
# in the German market, sourced from manufacturer configurator websites.
# Used as dashboard fallback when live extraction isn't available.
# Source: official configurators as of 2026-Q3.

REFERENCE_PRICES: dict[str, dict[str, dict]] = {
    "allrad": {
        "Mercedes-Benz": {"price": 2856, "models": ["C-Klasse", "E-Klasse", "GLC"]},
        "Audi":          {"price": 2100, "models": ["A4", "A6", "Q5"]},
        "Porsche":       {"price": 0, "models": []},  # included in model variant
    },
    "steering_wheel_heating": {
        "Mercedes-Benz": {"price": 280, "models": ["C-Klasse", "E-Klasse", "GLC", "GLE"]},
        "Audi":          {"price": 250, "models": ["A3", "A4", "A6", "Q3", "Q5"]},
        "Porsche":       {"price": 250, "models": ["Cayenne", "Macan"]},
    },
    "head_up_display": {
        "Mercedes-Benz": {"price": 1100, "models": ["C-Klasse", "E-Klasse", "S-Klasse"]},
        "Audi":          {"price": 1350, "models": ["A6", "A7", "Q5", "e-tron GT"]},
        "Porsche":       {"price": 1580, "models": ["Cayenne", "Taycan", "Panamera"]},
    },
    "panoramic_roof": {
        "Mercedes-Benz": {"price": 1500, "models": ["C-Klasse", "E-Klasse", "GLC"]},
        "Audi":          {"price": 1580, "models": ["A4", "A6", "Q5"]},
        "Porsche":       {"price": 1750, "models": ["Cayenne", "Macan"]},
    },
    "premium_sound": {
        "Mercedes-Benz": {"price": 990, "models": ["C-Klasse", "E-Klasse", "S-Klasse"]},
        "Audi":          {"price": 850, "models": ["A4", "A6", "Q5"]},
        "Porsche":       {"price": 1020, "models": ["Cayenne", "Macan", "Taycan"]},
    },
    "leather_seats": {
        "Mercedes-Benz": {"price": 1680, "models": ["C-Klasse", "E-Klasse"]},
        "Audi":          {"price": 1450, "models": ["A4", "A6"]},
        "Porsche":       {"price": 0, "models": []},  # standard in most models
    },
    "adaptive_cruise_control": {
        "Mercedes-Benz": {"price": 1750, "models": ["C-Klasse", "E-Klasse", "GLC"]},
        "Audi":          {"price": 1550, "models": ["A3", "A4", "A6", "Q5"]},
        "Porsche":       {"price": 1790, "models": ["Cayenne", "Macan", "Taycan"]},
    },
    "matrix_led": {
        "Mercedes-Benz": {"price": 1900, "models": ["C-Klasse", "E-Klasse"]},
        "Audi":          {"price": 1750, "models": ["A4", "A6", "Q5"]},
        "Porsche":       {"price": 1690, "models": ["Cayenne", "Macan"]},
    },
    "ambient_lighting": {
        "Mercedes-Benz": {"price": 480, "models": ["C-Klasse", "E-Klasse", "GLC"]},
        "Audi":          {"price": 420, "models": ["A4", "A6", "A7", "Q5"]},
        "Porsche":       {"price": 510, "models": ["Cayenne", "Taycan"]},
    },
    "air_suspension": {
        "Mercedes-Benz": {"price": 1850, "models": ["E-Klasse", "S-Klasse", "GLE"]},
        "Audi":          {"price": 2100, "models": ["A6", "A7", "Q5", "e-tron GT"]},
        "Porsche":       {"price": 1820, "models": ["Cayenne", "Panamera", "Taycan"]},
    },
    "seat_heating_front": {
        "Mercedes-Benz": {"price": 340, "models": ["C-Klasse", "E-Klasse", "GLC", "GLE"]},
        "Audi":          {"price": 310, "models": ["A3", "A4", "A6", "Q3", "Q5"]},
        "Porsche":       {"price": 380, "models": ["Cayenne", "Macan", "911"]},
    },
    "parking_assist": {
        "Mercedes-Benz": {"price": 1200, "models": ["C-Klasse", "E-Klasse", "GLC"]},
        "Audi":          {"price": 980, "models": ["A3", "A4", "A6", "Q5"]},
        "Porsche":       {"price": 1150, "models": ["Cayenne", "Macan"]},
    },
    "rear_camera": {
        "Mercedes-Benz": {"price": 450, "models": ["C-Klasse", "GLC", "GLE"]},
        "Audi":          {"price": 400, "models": ["A3", "A4", "Q3", "Q5"]},
        "Porsche":       {"price": 610, "models": ["Cayenne", "Macan", "911"]},
    },
    "wireless_charging": {
        "Mercedes-Benz": {"price": 350, "models": ["C-Klasse", "E-Klasse", "S-Klasse"]},
        "Audi":          {"price": 300, "models": ["A4", "A6", "Q5"]},
        "Porsche":       {"price": 380, "models": ["Cayenne", "Taycan"]},
    },
    "sport_suspension": {
        "Mercedes-Benz": {"price": 640, "models": ["C-Klasse", "E-Klasse"]},
        "Audi":          {"price": 590, "models": ["A4", "A6"]},
        "Porsche":       {"price": 1290, "models": ["Cayenne", "Macan", "911"]},
    },
}


def get_reference_option_summary() -> list[dict]:
    """Build a reference option summary from known market prices.

    Used as dashboard fallback when live option extraction is unavailable.
    Each row contains brand-level detail matching the dashboard schema.
    """
    rows: list[dict] = []

    for std_name, brand_data in REFERENCE_PRICES.items():
        defn = OPTION_DEFINITIONS.get(std_name, {})
        brands_detail: dict[str, dict] = {}
        all_prices: list[float] = []
        total_models = 0

        for brand, info in brand_data.items():
            price = info["price"]
            models = info["models"]
            if price <= 0 or not models:
                continue

            brands_detail[brand] = {
                "name": get_brand_name(std_name, brand),
                "avg_price": price,
                "min_price": price,
                "max_price": price,
                "model_count": len(models),
            }
            all_prices.append(price)
            total_models += len(models)

        if not all_prices:
            continue

        rows.append({
            "standardized_name": std_name,
            "display_name": defn.get("description", std_name),
            "category": get_category(std_name),
            "category_label": get_category_label(get_category(std_name)),
            "brands": brands_detail,
            "overall_avg_price": round(sum(all_prices) / len(all_prices), 2),
            "overall_min_price": min(all_prices),
            "overall_max_price": max(all_prices),
            "total_model_count": total_models,
            "source": "reference",
        })

    rows.sort(key=lambda r: (-r["total_model_count"], r["standardized_name"]))
    return rows

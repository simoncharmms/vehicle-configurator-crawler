"""Option name standardization across vehicle brands.

Maps brand-specific option names (e.g. '4MATIC', 'quattro', 'xDrive')
to standardized keys (e.g. 'allrad') for cross-brand comparison.

Covers 30+ standard categories with German and English aliases
for Mercedes-Benz, Porsche, Lexus, Audi, and BMW.
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
    "climate": "Climate",
    "wheels": "Wheels & Tyres",
    "other": "Other",
}


# --- Standard Option Definitions ---
# Each entry maps a standardized key to its aliases, category, description,
# and brand-specific display names.

OPTION_DEFINITIONS: dict[str, dict] = {
    # ========== DRIVETRAIN ==========
    "allrad": {
        "aliases": [
            "4MATIC", "4MATIC+", "Quattro", "quattro", "xDrive",
            "AWD", "Allrad", "Allradantrieb", "e-quattro",
            "E-FOUR", "DIRECT4", "DIRECT 4",
        ],
        "category": "drivetrain",
        "description": "All-wheel drive system",
        "brand_names": {
            "Mercedes-Benz": "4MATIC",
            "Audi": "quattro",
            "BMW": "xDrive",
            "Porsche": "AWD",
            "Lexus": "E-FOUR",
        },
    },
    "sport_suspension": {
        "aliases": [
            "Sportfahrwerk", "Sport Suspension", "Sports Suspension",
            "M Sportfahrwerk", "S line Fahrwerk", "PASM",
            "Porsche Active Suspension Management",
            "Adaptive Variable Suspension", "AVS",
            "F SPORT Fahrwerk",
        ],
        "category": "drivetrain",
        "description": "Sport-tuned suspension",
        "brand_names": {
            "Mercedes-Benz": "Sportfahrwerk",
            "Audi": "Sportfahrwerk",
            "BMW": "M Sportfahrwerk",
            "Porsche": "PASM",
            "Lexus": "Adaptive Variable Suspension",
        },
    },
    "air_suspension": {
        "aliases": [
            "Luftfederung", "Air Suspension", "AIRMATIC",
            "Adaptive Air Suspension", "Luftfederung adaptiv",
            "Adaptive Luftfederung", "Luftfederung komfort",
            "Adaptive Variable Air Suspension",
        ],
        "category": "comfort",
        "description": "Air suspension system",
        "brand_names": {
            "Mercedes-Benz": "AIRMATIC",
            "Audi": "Adaptive Luftfederung",
            "BMW": "Adaptive Air Suspension",
            "Porsche": "Adaptive Air Suspension",
            "Lexus": "Adaptive Air Suspension",
        },
    },
    "manual_transmission": {
        "aliases": [
            "6-Gang Schaltgetriebe", "6-speed manual", "Manual Transmission",
            "Schaltgetriebe", "6-Gang-Handschaltgetriebe",
            "6-speed manual transmission", "6-speed sports transmission",
            "6-Speed GT Sports Transmission",
            "Manuelles Getriebe", "5-Gang Schaltgetriebe",
        ],
        "category": "drivetrain",
        "description": "Manual transmission",
        "brand_names": {
            "Mercedes-Benz": "6-Gang Schaltgetriebe",
            "Porsche": "6-speed manual transmission",
            "Audi": "6-Gang Schaltgetriebe",
        },
    },
    "dual_clutch_transmission": {
        "aliases": [
            "Doppelkupplung", "PDK", "Porsche Doppelkupplung",
            "7-speed Porsche Doppelkupplung",
            "DCT", "Doppelkupplungsgetriebe",
            "S tronic", "7-Gang S tronic",
        ],
        "category": "drivetrain",
        "description": "Dual-clutch transmission",
        "brand_names": {
            "Porsche": "PDK",
            "Audi": "S tronic",
        },
    },
    "automatic_transmission": {
        "aliases": [
            "Automatikgetriebe", "Automatic Gearbox",
            "8-speed automatic gearbox", "9G-TRONIC",
            "Tiptronic", "8-Gang Automatik",
            "8-speed Tiptronic S", "Automatik",
        ],
        "category": "drivetrain",
        "description": "Automatic transmission",
        "brand_names": {
            "Mercedes-Benz": "9G-TRONIC",
            "Porsche": "8-speed Tiptronic S",
            "Audi": "Tiptronic",
        },
    },

    # ========== COMFORT ==========
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
            "Lexus": "Lenkradheizung",
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
            "Lexus": "Sitzheizung",
        },
    },
    "seat_ventilation": {
        "aliases": [
            "Sitzbelüftung", "Seat Ventilation", "Ventilated Seats",
            "Sitzklimatisierung", "Belüftete Sitze",
            "Sitzbelüftung vorn", "Klimatisierte Sitze",
        ],
        "category": "comfort",
        "description": "Ventilated / cooled front seats",
        "brand_names": {
            "Mercedes-Benz": "Sitzbelüftung",
            "Audi": "Sitzbelüftung",
            "BMW": "Seat Ventilation",
            "Porsche": "Seat Ventilation",
            "Lexus": "Sitzklimatisierung",
        },
    },
    "massage_seats": {
        "aliases": [
            "Massagesitze", "Massage Seats", "Massagefunktion",
            "Sitz mit Massagefunktion", "Multikontur-Sitz",
            "First Class Sitze mit Massagefunktion",
            "Einzelsitze im Fond mit Massagefunktion",
        ],
        "category": "comfort",
        "description": "Seats with massage function",
        "brand_names": {
            "Mercedes-Benz": "Multikontur-Sitz",
            "Lexus": "Massagefunktion",
            "BMW": "Massagesitze",
            "Porsche": "Massage Seats",
        },
    },
    "electric_tailgate": {
        "aliases": [
            "Elektrische Heckklappe", "Electric Tailgate",
            "Heckklappe elektrisch", "Elektrisch öffnend",
            "Heckklappe elektrisch öffnend",
            "Heckklappe elektrisch öffnend und schließend",
            "Heckklappe elektrisch öffnend mit Memoryfunktion",
        ],
        "category": "comfort",
        "description": "Electrically operated tailgate",
        "brand_names": {
            "Mercedes-Benz": "Elektrische Heckklappe",
            "Lexus": "Heckklappe elektrisch öffnend",
            "BMW": "Electric Tailgate",
            "Audi": "Elektrische Heckklappe",
        },
    },
    "keyless_entry": {
        "aliases": [
            "Keyless Entry", "Schlüsselloser Zugang", "Keyless Go",
            "KEYLESS-GO", "Komfortzugang", "Smart Key",
            "Lexus Smart Key", "Advanced Key",
            "schlüsselloser Fahrzeugzugang",
        ],
        "category": "comfort",
        "description": "Keyless entry and start",
        "brand_names": {
            "Mercedes-Benz": "KEYLESS-GO",
            "Audi": "Komfortzugang",
            "BMW": "Komfortzugang",
            "Porsche": "Keyless Entry",
            "Lexus": "Lexus Smart Key",
        },
    },
    "memory_seats": {
        "aliases": [
            "Memory-Sitze", "Memory Seats", "Sitzmemory",
            "Memory-Paket", "Fahrersitz Memory",
            "Memoryfunktion", "Komfort-Einstieg",
            "Easy-Entry Komforteinstieg",
        ],
        "category": "comfort",
        "description": "Seat memory / easy-entry function",
        "brand_names": {
            "Mercedes-Benz": "Memory-Paket",
            "Lexus": "Komfort-Einstieg",
            "BMW": "Memory Seats",
        },
    },

    # ========== TECHNOLOGY ==========
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
            "Lexus": "Head-Up Display",
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
            "Lexus": "Kabelloses Laden",
        },
    },
    "digital_cockpit": {
        "aliases": [
            "Digitales Cockpit", "Digital Cockpit", "Virtual Cockpit",
            "Audi Virtual Cockpit", "Digital Instrument Cluster",
            "Digitaler Tacho", "Digitale Instrumente",
            "Digitaler Rückspiegel",
        ],
        "category": "technology",
        "description": "Digital instrument cluster / cockpit",
        "brand_names": {
            "Mercedes-Benz": "Digitales Cockpit",
            "Audi": "Audi Virtual Cockpit",
            "BMW": "Digital Cockpit",
            "Lexus": "Digitaler Rückspiegel",
        },
    },
    "navigation": {
        "aliases": [
            "Navigation", "Navigationssystem", "MBUX Navigation",
            "MMI Navigation", "Connected Navigation",
            "Cloud-basierte Navigation",
        ],
        "category": "technology",
        "description": "Built-in navigation system",
        "brand_names": {
            "Mercedes-Benz": "MBUX Navigation",
            "Audi": "MMI Navigation",
            "BMW": "Navigation",
            "Lexus": "Navigation",
        },
    },

    # ========== SAFETY ==========
    "adaptive_cruise_control": {
        "aliases": [
            "Adaptive Cruise Control", "ACC", "Distronic", "DISTRONIC",
            "Abstandsregeltempomat", "Adaptive Geschwindigkeitsregelung",
            "Active Cruise Control", "Abstandstempomat",
            "Radar-Abstandstempomat",
        ],
        "category": "safety",
        "description": "Adaptive cruise control with distance keeping",
        "brand_names": {
            "Mercedes-Benz": "DISTRONIC",
            "Audi": "Adaptive Cruise Control",
            "BMW": "Active Cruise Control",
            "Porsche": "Adaptive Cruise Control",
            "Lexus": "Adaptive Cruise Control",
        },
    },
    "parking_assist": {
        "aliases": [
            "Parkassistent", "Park Assist", "Parking Assist",
            "Einparkassistent", "Parktronic", "PARKTRONIC",
            "Remote Park Assist", "Park Assist Plus",
            "Intelligent Park Assist", "Lexus Intelligent Park Assist",
            "Parksensoren",
        ],
        "category": "safety",
        "description": "Automated parking assistance",
        "brand_names": {
            "Mercedes-Benz": "PARKTRONIC",
            "Audi": "Einparkassistent",
            "BMW": "Park Assist",
            "Porsche": "Park Assist",
            "Lexus": "Lexus Intelligent Park Assist",
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
            "Lexus": "360° Kamera",
        },
    },
    "blind_spot_monitor": {
        "aliases": [
            "Totwinkelassistent", "Blind Spot Monitor", "Blind Spot Assist",
            "Totwinkelwarnung", "Totwinkel-Assistent",
            "Ausstiegswarnung", "Ausstiegswarnung Safe Exit Assist",
            "Safe Exit Assist",
        ],
        "category": "safety",
        "description": "Blind spot monitoring / exit warning",
        "brand_names": {
            "Mercedes-Benz": "Totwinkelassistent",
            "Audi": "Totwinkelassistent",
            "BMW": "Blind Spot Assist",
            "Porsche": "Blind Spot Monitor",
            "Lexus": "Safe Exit Assist",
        },
    },
    "lane_keep_assist": {
        "aliases": [
            "Spurhalteassistent", "Lane Keep Assist", "Lane Keeping Assist",
            "Spurassistent", "Aktiver Spurhalteassistent",
            "Lane Departure Warning", "Spurverlassenswarnung",
        ],
        "category": "safety",
        "description": "Lane keeping / departure assist",
        "brand_names": {
            "Mercedes-Benz": "Aktiver Spurhalteassistent",
            "Audi": "Spurhalteassistent",
            "BMW": "Lane Keeping Assist",
            "Porsche": "Lane Keep Assist",
            "Lexus": "Spurhalteassistent",
        },
    },
    "emergency_braking": {
        "aliases": [
            "Notbremsassistent", "Emergency Braking", "Pre-Safe",
            "PRE-SAFE", "Notbremsfunktion", "Autonomous Emergency Braking",
            "City-Notbremsfunktion", "Pre-Collision System",
        ],
        "category": "safety",
        "description": "Automatic emergency braking system",
        "brand_names": {
            "Mercedes-Benz": "PRE-SAFE",
            "Audi": "Notbremsassistent",
            "BMW": "Emergency Braking",
            "Porsche": "Emergency Braking",
            "Lexus": "Pre-Collision System",
        },
    },
    "theft_protection": {
        "aliases": [
            "Diebstahlwarnanlage", "Theft Protection", "Anti-Theft",
            "Diebstahlschutzsystem", "Alarmanlage",
            "Diebstahlwarnanlage mit Abschleppschutz",
        ],
        "category": "safety",
        "description": "Vehicle theft protection / alarm system",
        "brand_names": {
            "Mercedes-Benz": "Alarmanlage",
            "Lexus": "Diebstahlwarnanlage",
            "BMW": "Alarmanlage",
        },
    },

    # ========== LIGHTING ==========
    "matrix_led": {
        "aliases": [
            "MULTIBEAM LED", "Matrix LED", "Matrix-LED", "Adaptive LED",
            "LED Matrix", "IntelliLux LED", "Digital Light", "DIGITAL LIGHT",
            "HD Matrix LED", "Adaptive LED-Scheinwerfer",
            "Quad-LED", "Quad-LED Frontscheinwerfer",
        ],
        "category": "lighting",
        "description": "Matrix/adaptive LED headlights",
        "brand_names": {
            "Mercedes-Benz": "DIGITAL LIGHT",
            "Audi": "Matrix LED",
            "BMW": "Adaptive LED",
            "Porsche": "LED Matrix",
            "Lexus": "Quad-LED Frontscheinwerfer",
        },
    },
    "ambient_lighting": {
        "aliases": [
            "Ambientebeleuchtung", "Ambient Lighting", "Ambient Light",
            "Interior Lighting", "Innenraumbeleuchtung",
            "Ambiente-Beleuchtung", "Ambiente Beleuchtung",
            "Ambientelicht",
        ],
        "category": "interior",
        "description": "Configurable ambient interior lighting",
        "brand_names": {
            "Mercedes-Benz": "Ambientebeleuchtung",
            "Audi": "Ambientebeleuchtung",
            "BMW": "Ambient Lighting",
            "Porsche": "Ambient Lighting",
            "Lexus": "Ambiente Beleuchtung",
        },
    },
    "auto_high_beam": {
        "aliases": [
            "Fernlichtassistent", "Auto High Beam", "Automatic High Beam",
            "Automatisches Fernlicht", "Leuchtweitenregulierung dynamisch",
            "Intelligent High-Beam",
        ],
        "category": "lighting",
        "description": "Automatic high beam control",
        "brand_names": {
            "Mercedes-Benz": "Fernlichtassistent",
            "Audi": "Fernlichtassistent",
            "BMW": "Auto High Beam",
            "Lexus": "Leuchtweitenregulierung dynamisch",
        },
    },
    "fog_lights": {
        "aliases": [
            "Nebelscheinwerfer", "Fog Lights", "LED Nebelscheinwerfer",
            "Nebelscheinwerfer vorne",
            "Nebelscheinwerfer vorne in LED-Technologie",
            "Abbiegelicht",
        ],
        "category": "lighting",
        "description": "Front fog lights / cornering lights",
        "brand_names": {
            "Mercedes-Benz": "Nebelscheinwerfer",
            "Lexus": "Nebelscheinwerfer vorne in LED-Technologie",
            "Audi": "LED Nebelscheinwerfer",
        },
    },

    # ========== SOUND ==========
    "premium_sound": {
        "aliases": [
            "Harman Kardon", "Bose", "Bang & Olufsen", "Bang&Olufsen",
            "Burmester", "Burmester Surround", "Burmester 3D",
            "Burmester® Surround-Soundsystem",
            "Premium Sound System", "High-End Sound", "Bose Surround Sound",
            "Bose® Surround Sound-System", "Bang & Olufsen Premium Sound",
            "Mark Levinson", "Premium Audiosystem",
        ],
        "category": "sound",
        "description": "Premium audio/sound system",
        "brand_names": {
            "Mercedes-Benz": "Burmester",
            "Audi": "Bang & Olufsen",
            "BMW": "Harman Kardon",
            "Porsche": "Bose",
            "Lexus": "Mark Levinson",
        },
    },

    # ========== EXTERIOR ==========
    "panoramic_roof": {
        "aliases": [
            "Panoramadach", "Panoramic Roof", "Panoramic Sunroof",
            "Panorama-Schiebedach", "Panorama-Glasdach", "Panoramaglasdach",
            "Schiebe-Hebe-Dach", "Panorama Dach",
            "Schiebedach",
        ],
        "category": "exterior",
        "description": "Panoramic glass roof / sunroof",
        "brand_names": {
            "Mercedes-Benz": "Panorama-Schiebedach",
            "Audi": "Panorama-Glasdach",
            "BMW": "Panoramadach",
            "Porsche": "Panoramadach",
            "Lexus": "Panoramadach",
        },
    },
    "privacy_glass": {
        "aliases": [
            "Privacy Glas", "Privacy Glass", "Getöntes Glas",
            "Wärmeschutzverglasung", "Verdunkeltes Glas",
            "verstärkte Tönung", "Akustikglas",
        ],
        "category": "exterior",
        "description": "Tinted / privacy glass",
        "brand_names": {
            "Mercedes-Benz": "Wärmeschutzverglasung",
            "Lexus": "Privacy Glas",
            "BMW": "Privacy Glass",
        },
    },
    "tow_hitch": {
        "aliases": [
            "Anhängerkupplung", "Tow Hitch", "Trailer Hitch",
            "Anhängelast", "Anhängerkupplung elektrisch",
            "Schwenkbare Anhängerkupplung",
        ],
        "category": "exterior",
        "description": "Trailer hitch / towing capability",
        "brand_names": {
            "Mercedes-Benz": "Anhängerkupplung",
            "Lexus": "Anhängelast",
            "BMW": "Anhängerkupplung",
            "Audi": "Anhängerkupplung",
        },
    },

    # ========== INTERIOR ==========
    "leather_seats": {
        "aliases": [
            "Leather", "Leder", "Leather Interior", "Leather Upholstery",
            "Ledersitze", "Lederausstattung", "Lederpolsterung",
            "Nappaleder", "Vollleder", "Race-Tex",
        ],
        "category": "interior",
        "description": "Leather seat upholstery",
        "brand_names": {
            "Mercedes-Benz": "Lederausstattung",
            "Audi": "Lederausstattung",
            "BMW": "Lederausstattung",
            "Porsche": "Leather Interior",
            "Lexus": "Lederausstattung",
        },
    },
    "heated_windshield": {
        "aliases": [
            "Beheizbare Frontscheibe", "Heated Windshield",
            "Windschutzscheibenheizung", "Heated Windscreen",
            "Standheizung", "Auxiliary Heater",
        ],
        "category": "comfort",
        "description": "Heated windshield / auxiliary heater",
        "brand_names": {
            "Mercedes-Benz": "Standheizung",
            "BMW": "Heated Windshield",
            "Audi": "Standheizung",
        },
    },

    # ========== CLIMATE ==========
    "climate_control": {
        "aliases": [
            "Klimaautomatik", "Climate Control", "Klimatisierungsautomatik",
            "Mehrzonenklimaautomatik", "4-Zonen-Klimaautomatik",
            "Elektrische Klimatisierungsautomatik",
            "Klimatisierungsautomatik mit Ionisierung",
            "Klimatisierungsautomatik mit Luftfeuchtigkeitssensor",
        ],
        "category": "climate",
        "description": "Automatic climate control",
        "brand_names": {
            "Mercedes-Benz": "Mehrzonenklimaautomatik",
            "Audi": "Klimaautomatik",
            "BMW": "Climate Control",
            "Lexus": "Klimatisierungsautomatik",
        },
    },
    "heated_rear_seats": {
        "aliases": [
            "Sitzheizung hinten", "Rear Seat Heating", "Heated Rear Seats",
            "Fondsitzheizung", "Sitzheizung Fond",
            "Knieheizung", "Infrarot-Heizung",
        ],
        "category": "comfort",
        "description": "Rear seat / knee heating",
        "brand_names": {
            "Mercedes-Benz": "Fondsitzheizung",
            "Lexus": "Knieheizung",
            "BMW": "Heated Rear Seats",
        },
    },

    # ========== NOISE ==========
    "active_noise_cancellation": {
        "aliases": [
            "Active Noise Cancellation", "ANC", "Geräuschdämpfung",
            "Geräuschdämpfung ANC", "Active Sound Design",
            "Akustik-Komfortpaket",
        ],
        "category": "comfort",
        "description": "Active noise cancellation",
        "brand_names": {
            "Lexus": "Geräuschdämpfung ANC",
            "Mercedes-Benz": "Akustik-Komfortpaket",
            "BMW": "Active Sound Design",
        },
    },

    # ========== EV / CHARGING ==========
    "ac_charging": {
        "aliases": [
            "AC Ladeanschluss", "AC Charging", "AC Lader",
            "22 kW AC", "11 kW AC", "Typ 2", "Mennekes",
            "3-phasig", "On-Board Charger",
        ],
        "category": "technology",
        "description": "AC charging capability",
        "brand_names": {
            "Lexus": "AC Ladeanschluss",
            "Mercedes-Benz": "AC Lader",
            "Porsche": "AC Charging",
        },
    },

    # ========== WHEELS ==========
    "alloy_wheels": {
        "aliases": [
            "Leichtmetallfelgen", "Alloy Wheels", "Leichtmetallräder",
            "Aluminiumfelgen", "LM-Felgen",
            "Alufelgen",
        ],
        "category": "wheels",
        "description": "Alloy wheels",
        "brand_names": {
            "Mercedes-Benz": "Leichtmetallfelgen",
            "Audi": "Leichtmetallfelgen",
            "BMW": "Leichtmetallfelgen",
            "Porsche": "Alloy Wheels",
            "Lexus": "Leichtmetallräder",
        },
    },

    # ========== DRIVER MONITOR ==========
    "driver_monitor": {
        "aliases": [
            "Fahrer-Monitor", "Driver Monitor", "Driver Attention Monitor",
            "Müdigkeitserkennung", "Aufmerksamkeitsassistent",
            "ATTENTION ASSIST",
        ],
        "category": "safety",
        "description": "Driver attention / fatigue monitoring",
        "brand_names": {
            "Mercedes-Benz": "ATTENTION ASSIST",
            "Lexus": "Fahrer-Monitor",
            "BMW": "Driver Attention Monitor",
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

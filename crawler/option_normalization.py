"""Option normalization layer for cross-brand mapping.

Maps brand-specific option names to standardized categories,
enabling cross-brand option comparison (e.g., "4MATIC" + "AWD" + "Quattro" → all map to "all_wheel_drive").
"""

from dataclasses import dataclass


@dataclass
class OptionCategory:
    """Standard option category with brand-specific synonyms."""
    standard_name: str
    display_name: str
    category: str  # drivetrain, comfort, technology, interior, sound, safety, lighting, etc.
    synonyms: dict[str, list[str]]  # { brand: [aliases] }


# Comprehensive option categories across all brands
OPTION_CATEGORIES = {
    # Drivetrain
    "all_wheel_drive": OptionCategory(
        standard_name="all_wheel_drive",
        display_name="All-Wheel Drive",
        category="drivetrain",
        synonyms={
            "mercedes-benz": ["4MATIC", "4-MATIC", "All-Wheel Drive", "Allradantrieb"],
            "porsche": ["All-Wheel Drive", "AWD", "Allradantrieb"],
            "lexus": ["AWD", "All-Wheel Drive", "Allrad"],
            "audi": ["Quattro", "quattro", "all-wheel drive"],
            "bmw": ["xDrive", "x Drive"],
            "volvo": ["AWD", "all-wheel drive"],
        }
    ),
    
    "manual_transmission": OptionCategory(
        standard_name="manual_transmission",
        display_name="Manual Transmission",
        category="drivetrain",
        synonyms={
            "mercedes-benz": ["6-speed manual", "manual transmission", "Schaltgetriebe"],
            "porsche": ["6-speed manual transmission with dual-mass flywheel", "6-speed sports transmission", "manual transmission"],
            "lexus": ["6-speed manual", "manual transmission"],
            "audi": ["6-speed manual", "S tronic manual"],
        }
    ),
    
    "automatic_transmission": OptionCategory(
        standard_name="automatic_transmission",
        display_name="Automatic Transmission",
        category="drivetrain",
        synonyms={
            "mercedes-benz": ["9-speed automatic", "8-speed automatic", "automatic"],
            "porsche": ["PDK", "Porsche Doppelkupplung", "8-speed automatic gearbox"],
            "lexus": ["8-speed automatic", "automatic transmission"],
            "audi": ["automatic transmission", "S tronic"],
        }
    ),
    
    # Comfort
    "steering_wheel_heating": OptionCategory(
        standard_name="steering_wheel_heating",
        display_name="Steering Wheel Heating",
        category="comfort",
        synonyms={
            "mercedes-benz": ["Steering wheel heating", "Lenkradheizung", "heated steering wheel"],
            "porsche": ["Heated steering wheel"],
            "lexus": ["Heated steering wheel", "steering wheel heating"],
        }
    ),
    
    "seat_heating": OptionCategory(
        standard_name="seat_heating",
        display_name="Seat Heating",
        category="comfort",
        synonyms={
            "mercedes-benz": ["Seat heating", "Sitzheizung"],
            "porsche": ["Seat heating", "heated seats"],
            "lexus": ["Heated seats", "seat heating"],
        }
    ),
    
    # Technology
    "head_up_display": OptionCategory(
        standard_name="head_up_display",
        display_name="Head-Up Display",
        category="technology",
        synonyms={
            "mercedes-benz": ["HUD", "Head-up Display", "DIGITAL LIGHT"],
            "porsche": ["HUD", "Head-up display"],
            "lexus": ["HUD", "head-up display"],
        }
    ),
    
    "adaptive_cruise_control": OptionCategory(
        standard_name="adaptive_cruise_control",
        display_name="Adaptive Cruise Control",
        category="technology",
        synonyms={
            "mercedes-benz": ["ACC", "Adaptive cruise control", "Aktiver Abstandsregeltempomat"],
            "porsche": ["Adaptive cruise control", "ACC"],
            "lexus": ["Adaptive cruise control"],
        }
    ),
    
    "lane_keeping_assist": OptionCategory(
        standard_name="lane_keeping_assist",
        display_name="Lane Keeping Assist",
        category="technology",
        synonyms={
            "mercedes-benz": ["LKA", "Lane keeping assist", "Spurhalteassistent"],
            "porsche": ["Lane keeping assist"],
            "lexus": ["Lane keeping assist"],
        }
    ),
    
    # Interior
    "leather_seats": OptionCategory(
        standard_name="leather_seats",
        display_name="Leather Seat Upholstery",
        category="interior",
        synonyms={
            "mercedes-benz": ["Leather upholstery", "Lederausstattung"],
            "porsche": ["Race-Tex", "leather", "Leather upholstery"],
            "lexus": ["Leather seats", "leather upholstery"],
        }
    ),
    
    "panoramic_sunroof": OptionCategory(
        standard_name="panoramic_sunroof",
        display_name="Panoramic Sunroof",
        category="interior",
        synonyms={
            "mercedes-benz": ["Panoramic sunroof", "Panoramadach"],
            "porsche": ["Panoramic sunroof"],
            "lexus": ["Panoramic sunroof", "panorama roof"],
        }
    ),
    
    # Sound
    "premium_sound_system": OptionCategory(
        standard_name="premium_sound_system",
        display_name="Premium Sound System",
        category="sound",
        synonyms={
            "mercedes-benz": ["Burmester", "Burmester 3D", "Burmester® 3D-Surround"],
            "porsche": ["Bose"],
            "lexus": ["Premium audio system", "Mark Levinson"],
        }
    ),
    
    # Safety
    "parking_sensors": OptionCategory(
        standard_name="parking_sensors",
        display_name="Parking Sensors",
        category="safety",
        synonyms={
            "mercedes-benz": ["Parking sensors", "Parktronic", "360° camera"],
            "porsche": ["Parking sensors"],
            "lexus": ["Parking sensors", "parking assist"],
        }
    ),
    
    "blind_spot_monitor": OptionCategory(
        standard_name="blind_spot_monitor",
        display_name="Blind Spot Monitor",
        category="safety",
        synonyms={
            "mercedes-benz": ["Blind spot assist", "Totwinkelassistent"],
            "porsche": ["Blind spot monitor"],
            "lexus": ["Blind spot monitor"],
        }
    ),
}


def normalize_option_name(option_name: str, brand: str) -> str | None:
    """Normalize an option name to standard category.
    
    Args:
        option_name: Brand-specific option name (e.g., "4MATIC", "Burmester")
        brand: Brand key (e.g., "mercedes-benz", "porsche", "lexus")
    
    Returns:
        Standard option name if found, None otherwise.
    
    Example:
        >>> normalize_option_name("4MATIC", "mercedes-benz")
        "all_wheel_drive"
        >>> normalize_option_name("PDK", "porsche")
        "automatic_transmission"
    """
    if not option_name:
        return None
    
    option_lower = option_name.lower().strip()
    
    # Search all categories for matching synonyms
    for std_name, category in OPTION_CATEGORIES.items():
        brand_synonyms = category.synonyms.get(brand, [])
        for syn in brand_synonyms:
            if syn.lower() == option_lower or option_lower in syn.lower():
                return std_name
    
    return None


def get_cross_brand_options() -> dict[str, list[str]]:
    """Get options that appear in multiple brands.
    
    Returns:
        { standard_option: [brands...] }
    """
    # This will be populated by analyzing actual crawl data
    cross_brand = {}
    for std_name, category in OPTION_CATEGORIES.items():
        brands = list(category.synonyms.keys())
        if len(brands) > 1:
            cross_brand[std_name] = brands
    
    return cross_brand


if __name__ == "__main__":
    # Test examples
    tests = [
        ("4MATIC", "mercedes-benz", "all_wheel_drive"),
        ("PDK", "porsche", "automatic_transmission"),
        ("Burmester 3D", "mercedes-benz", "premium_sound_system"),
        ("Quattro", "audi", "all_wheel_drive"),
        ("AWD", "lexus", "all_wheel_drive"),
    ]
    
    for option, brand, expected in tests:
        result = normalize_option_name(option, brand)
        status = "✓" if result == expected else "✗"
        print(f"{status} normalize_option_name('{option}', '{brand}') = {result} (expected: {expected})")
    
    print(f"\nCross-brand options: {len(get_cross_brand_options())}")

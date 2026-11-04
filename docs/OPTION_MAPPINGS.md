# Option Name Mappings and Standardization

Cross-brand standardisation of vehicle option names used by the crawler.
Each option has a **standardized key** (used in data/code) and one or more
**brand-specific aliases** that the extraction engine recognises.

---

## Drivetrain

### All-Wheel Drive (`allrad`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | 4MATIC / 4MATIC+ |
| Audi | quattro / e-quattro |
| BMW | xDrive |
| Porsche | AWD |

### Sport Suspension (`sport_suspension`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Sportfahrwerk |
| Audi | Sportfahrwerk / S line Fahrwerk |
| BMW | M Sportfahrwerk |
| Porsche | PASM (Porsche Active Suspension Management) |

### Sport / Performance Package (`sport_package`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | AMG Line / AMG Sport Package |
| Audi | S line |
| Volkswagen | R-Line |
| Range Rover | SV Dynamic |

### Performance Brakes (`performance_brakes`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | AMG High-Performance Bremsanlage |
| Audi | RS brakes |
| Porsche | PCCB |
| Volkswagen | R Performance brakes |

---

## Comfort

### Heated Steering Wheel (`steering_wheel_heating`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Lenkradheizung |
| Audi | Lenkradheizung |
| BMW | Lenkradheizung |
| Porsche | Heated Steering Wheel |

### Heated Front Seats (`seat_heating_front`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Sitzheizung vorn |
| Audi | Sitzheizung vorne |
| BMW | Sitzheizung vorn |
| Porsche | Seat Heating |

### Air Suspension (`air_suspension`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | AIRMATIC |
| Audi | Adaptive Luftfederung |
| BMW | Adaptive Air Suspension |
| Porsche | Adaptive Air Suspension |

---

## Interior

Leather and premium seat-material options are categorized as **Upholstery** in
dashboard filters.

### Leather Seats (`leather_seats`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Lederausstattung / Nappaleder |
| Audi | Lederausstattung / Vollleder |
| BMW | Lederausstattung |
| Porsche | Leather Interior |

### Ambient Lighting (`ambient_lighting`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Ambientebeleuchtung |
| Audi | Ambientebeleuchtung |
| BMW | Ambient Lighting |
| Porsche | Ambient Lighting |

### Premium Upholstery (`premium_upholstery`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Nappaleder |
| Audi | Valcona Leather |
| BMW | Extended Leather |
| Volkswagen | ArtVelours |
| Range Rover | Windsor Leather |

---

## Technology

### Head-Up Display (`head_up_display`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Head-Up-Display |
| Audi | Head-up Display |
| BMW | Head-Up Display |
| Porsche | Head-Up Display |

### Wireless Charging (`wireless_charging`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Kabelloses Laden |
| Audi | Induktives Laden / Qi-Ladeschale |
| BMW | Wireless Charging |
| Porsche | Wireless Charging |

---

## Safety

Driver-assistance options such as adaptive cruise control, lane assist,
blind-spot monitoring, parking assist and emergency braking are grouped under
the **ADAS & Driver Assistance** category in the dashboard filters.

### Adaptive Cruise Control (`adaptive_cruise_control`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | DISTRONIC |
| Audi | Adaptive Cruise Control / ACC |
| BMW | Active Cruise Control |
| Porsche | Adaptive Cruise Control |

### Parking Assist (`parking_assist`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | PARKTRONIC |
| Audi | Einparkassistent |
| BMW | Park Assist |
| Porsche | Park Assist |

### Rear / 360° Camera (`rear_camera`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Rückfahrkamera / 360°-Kamera |
| Audi | Rückfahrkamera / Surround-View-System |
| BMW | Rear Camera / Surround View |
| Porsche | Rear Camera |

---

## Lighting

### Matrix / Adaptive LED Headlights (`matrix_led`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | DIGITAL LIGHT / MULTIBEAM LED |
| Audi | Matrix LED / HD Matrix LED |
| BMW | Adaptive LED |
| Porsche | LED Matrix |

---

## Sound & Entertainment

### Premium Sound System (`premium_sound`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Burmester / Burmester 3D |
| Audi | Bang & Olufsen |
| BMW | Harman Kardon |
| Porsche | Bose / Bose Surround Sound |

---

## Exterior

### Panoramic Roof (`panoramic_roof`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Panorama-Schiebedach |
| Audi | Panorama-Glasdach |
| BMW | Panoramadach |
| Porsche | Panoramadach |

### Paint Upgrades (`metallic_paint`, `matte_paint`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | Metallic-Lackierung / MANUFAKTUR magno |
| Audi | Metalliclack / Matte Paint |
| BMW | Metallic Paint / Frozen Paint |
| Volkswagen | Metallic |
| Range Rover | Premium Metallic / Satin Finish |

## Wheels

### Lightweight Wheels (`lightweight_wheels`)

| Brand | Name |
|-------|------|
| Mercedes-Benz | AMG Forged Wheels |
| Audi | RS Forged Wheels |
| Volkswagen | R Performance Wheels |
| Range Rover | SV Performance Wheels |

### Wheel Size Upgrade (`wheel_size_upgrade`)

Includes 19" / 20" / 21" / 22" wheel-size naming variants.

---

## Adding New Mappings

Edit `crawler/option_mappings.py` → `OPTION_DEFINITIONS`.  Each entry needs:

```python
"new_option_key": {
    "aliases": ["Brand Name 1", "Brand Name 2", ...],
    "category": "comfort",           # one of the OPTION_CATEGORIES keys
    "description": "Human-readable",
    "brand_names": {
        "Mercedes-Benz": "...",
        "Audi": "...",
    },
}
```

The `normalize_option_name()` function uses these aliases for
case-insensitive exact match first, then longest-substring match.

"""
Unified crop catalog: two-level categories for UI, AquaCrop model mapping, GDD profiles.
"""

from __future__ import annotations

from typing import Any

# slug -> {label, category, aquacrop_model, gdd_key}
CROP_ENTRIES: dict[str, dict[str, str]] = {
  # Cereals
    "barley": {"label": "Barley", "category": "Cereals", "aquacrop_model": "Barley", "gdd_key": "barley"},
    "spring_wheat": {"label": "Spring Wheat", "category": "Cereals", "aquacrop_model": "Wheat", "gdd_key": "wheat"},
    "durum_wheat": {"label": "Durum Wheat", "category": "Cereals", "aquacrop_model": "Wheat", "gdd_key": "wheat"},
    "winter_wheat": {"label": "Winter Wheat", "category": "Cereals", "aquacrop_model": "Wheat", "gdd_key": "wheat"},
    "soft_wheat": {"label": "Soft Wheat", "category": "Cereals", "aquacrop_model": "Wheat", "gdd_key": "wheat"},
    "grain_corn": {"label": "Grain Corn", "category": "Cereals", "aquacrop_model": "Maize", "gdd_key": "corn"},
    "oats": {"label": "Oats", "category": "Cereals", "aquacrop_model": "Barley", "gdd_key": "oats"},
    "triticale": {"label": "Triticale", "category": "Cereals", "aquacrop_model": "Wheat", "gdd_key": "wheat"},
    # Forages
    "alfalfa_2cut": {"label": "Alfalfa (2-cut)", "category": "Forages", "aquacrop_model": "Wheat", "gdd_key": "alfalfa"},
    "alfalfa_3cut": {"label": "Alfalfa (3-cut)", "category": "Forages", "aquacrop_model": "Wheat", "gdd_key": "alfalfa"},
    "corn_silage": {"label": "Corn Silage", "category": "Forages", "aquacrop_model": "Maize", "gdd_key": "corn"},
    "barley_silage": {"label": "Barley Silage", "category": "Forages", "aquacrop_model": "Barley", "gdd_key": "barley"},
    "timothy_hay": {"label": "Timothy Hay", "category": "Forages", "aquacrop_model": "Wheat", "gdd_key": "alfalfa"},
    "grass_hay": {"label": "Grass Hay", "category": "Forages", "aquacrop_model": "Wheat", "gdd_key": "alfalfa"},
    "tame_pasture": {"label": "Tame Pasture", "category": "Forages", "aquacrop_model": "Wheat", "gdd_key": "alfalfa"},
    # Oil Seeds
    "canola": {"label": "Canola", "category": "Oil Seeds", "aquacrop_model": "Sunflower", "gdd_key": "canola"},
    "flax": {"label": "Flax", "category": "Oil Seeds", "aquacrop_model": "Sunflower", "gdd_key": "canola"},
    "sunflower": {"label": "Sunflower", "category": "Oil Seeds", "aquacrop_model": "Sunflower", "gdd_key": "sunflower"},
    "soybean": {"label": "Soybean", "category": "Oil Seeds", "aquacrop_model": "Soybean", "gdd_key": "soybean"},
    # Specialty
    "potato": {"label": "Potato", "category": "Specialty Crops", "aquacrop_model": "Potato", "gdd_key": "potato"},
    "sugar_beet": {"label": "Sugar Beet", "category": "Specialty Crops", "aquacrop_model": "SugarBeet", "gdd_key": "sugar_beet"},
    "dry_bean": {"label": "Dry Bean", "category": "Specialty Crops", "aquacrop_model": "Soybean", "gdd_key": "dry_bean"},
    "dry_pea": {"label": "Dry Pea", "category": "Specialty Crops", "aquacrop_model": "Soybean", "gdd_key": "dry_bean"},
    "chickpea": {"label": "Chickpea", "category": "Specialty Crops", "aquacrop_model": "Soybean", "gdd_key": "dry_bean"},
    "lentil": {"label": "Lentil", "category": "Specialty Crops", "aquacrop_model": "Soybean", "gdd_key": "dry_bean"},
    "faba_bean": {"label": "Faba Bean", "category": "Specialty Crops", "aquacrop_model": "Soybean", "gdd_key": "dry_bean"},
    "canola_seed": {"label": "Canola Seed", "category": "Specialty Crops", "aquacrop_model": "Sunflower", "gdd_key": "canola"},
    "hemp": {"label": "Hemp", "category": "Specialty Crops", "aquacrop_model": "Sunflower", "gdd_key": "canola"},
}

CROP_CATEGORY_ORDER = ("Cereals", "Forages", "Oil Seeds", "Specialty Crops")

# GDD stage profiles (gdd_key -> stages)
CROP_GDD_PROFILES: dict[str, list[tuple[int, float, str]]] = {
    "wheat": [
        (180, 0.65, "Early establishment"),
        (550, 0.90, "Vegetative growth"),
        (950, 1.08, "Mid-season growth"),
        (1300, 1.18, "Peak water demand"),
        (99999, 0.85, "Late season / maturity"),
    ],
    "canola": [
        (160, 0.62, "Early establishment"),
        (520, 0.92, "Vegetative growth"),
        (900, 1.10, "Flowering and pod set"),
        (1200, 1.16, "Peak water demand"),
        (99999, 0.86, "Late season / maturity"),
    ],
    "corn": [
        (200, 0.55, "Emergence and early growth"),
        (650, 0.92, "Vegetative growth"),
        (1100, 1.16, "Tasseling and silking"),
        (1600, 1.22, "Peak water demand"),
        (99999, 0.90, "Late season / maturity"),
    ],
    "barley": [
        (170, 0.62, "Early establishment"),
        (520, 0.88, "Vegetative growth"),
        (900, 1.05, "Heading and grain fill"),
        (1200, 1.12, "Peak water demand"),
        (99999, 0.84, "Late season / maturity"),
    ],
    "oats": [
        (170, 0.63, "Early establishment"),
        (540, 0.89, "Vegetative growth"),
        (930, 1.06, "Panicle and grain fill"),
        (1220, 1.13, "Peak water demand"),
        (99999, 0.85, "Late season / maturity"),
    ],
    "soybean": [
        (190, 0.60, "Early establishment"),
        (620, 0.90, "Vegetative growth"),
        (1020, 1.12, "Flowering and pod fill"),
        (1400, 1.18, "Peak water demand"),
        (99999, 0.88, "Late season / maturity"),
    ],
    "sunflower": [
        (180, 0.62, "Early establishment"),
        (580, 0.92, "Vegetative growth"),
        (1000, 1.14, "Flowering"),
        (1400, 1.20, "Peak water demand"),
        (99999, 0.88, "Late season / maturity"),
    ],
    "potato": [
        (180, 0.68, "Emergence"),
        (520, 0.96, "Canopy development"),
        (900, 1.18, "Tuber initiation and bulking"),
        (1300, 1.24, "Peak water demand"),
        (99999, 0.92, "Maturation"),
    ],
    "dry_bean": [
        (180, 0.60, "Early establishment"),
        (580, 0.92, "Vegetative growth"),
        (980, 1.10, "Flowering and pod fill"),
        (1320, 1.16, "Peak water demand"),
        (99999, 0.86, "Late season / maturity"),
    ],
    "alfalfa": [
        (150, 0.78, "Early regrowth"),
        (450, 1.00, "Canopy build"),
        (850, 1.12, "Active growth"),
        (1200, 1.18, "Peak water demand"),
        (99999, 0.98, "Late growth"),
    ],
    "sugar_beet": [
        (200, 0.70, "Emergence"),
        (600, 0.95, "Canopy development"),
        (1050, 1.15, "Root bulking"),
        (1450, 1.22, "Peak water demand"),
        (99999, 0.90, "Maturation"),
    ],
}

CROP_CONDITION_OPTIONS: dict[str, dict[str, Any]] = {
    "optimal": {"label": "Optimal", "soil_moisture_pct": 100.0, "hint": "Field at field capacity (FC)."},
    "mild_stress": {"label": "Mild Stress (50% water availability)", "soil_moisture_pct": 50.0},
    "moderate_stress": {"label": "Moderate Stress", "soil_moisture_pct": 35.0},
    "severe_stress": {"label": "Severe Stress (Near Wilting Point)", "soil_moisture_pct": 20.0},
    "wilting": {"label": "Wilting", "soil_moisture_pct": 10.0},
}


def crop_categories() -> list[str]:
    return list(CROP_CATEGORY_ORDER)


def crops_by_category() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {c: [] for c in CROP_CATEGORY_ORDER}
    for slug, meta in CROP_ENTRIES.items():
        cat = meta["category"]
        if cat in out:
            out[cat].append({"value": slug, "label": meta["label"]})
    for cat in out:
        out[cat].sort(key=lambda x: x["label"])
    return out


def crop_label(slug: str) -> str:
    meta = CROP_ENTRIES.get(slug or "")
    return meta["label"] if meta else (slug or "").replace("_", " ").title()


def resolve_aquacrop_model(slug: str) -> str:
    meta = CROP_ENTRIES.get(slug or "")
    if meta:
        return meta["aquacrop_model"]
    return "Wheat"


def resolve_gdd_key(slug: str) -> str:
    meta = CROP_ENTRIES.get(slug or "")
    if meta:
        return meta.get("gdd_key") or slug
    return "wheat"


def match_crop_slug(raw: str) -> str:
    """Best-effort match free text or legacy values to catalog slug."""
    if not raw:
        return "spring_wheat"
    text = str(raw).strip().lower().replace(" ", "_")
    if text in CROP_ENTRIES:
        return text
    for slug, meta in CROP_ENTRIES.items():
        if slug in text or text in slug:
            return slug
        label = meta["label"].lower().replace(" ", "_")
        if label in text or text in label:
            return slug
    aliases = {
        "wheat": "spring_wheat",
        "maize": "grain_corn",
        "corn": "grain_corn",
        "rice": "spring_wheat",
        "cotton": "spring_wheat",
        "sugarbeet": "sugar_beet",
        "pulse": "dry_pea",
    }
    for key, slug in aliases.items():
        if key in text:
            return slug
    return "spring_wheat"


def crop_catalog_context(selected_slug: str = "spring_wheat") -> dict:
    slug = match_crop_slug(selected_slug)
    return {
        "crop_categories_json": {
            "categories": crop_categories(),
            "by_category": crops_by_category(),
        },
        "selected_crop_slug": slug,
        "crop_display_label": crop_label(slug),
        "crop_condition_options": CROP_CONDITION_OPTIONS,
        "crop_condition_option_list": [
            {"key": k, "label": v["label"], "soil_moisture_pct": v.get("soil_moisture_pct")}
            for k, v in CROP_CONDITION_OPTIONS.items()
        ],
    }


def aquacrop_crop_options() -> list[dict[str, str]]:
    """Unique AquaCrop models used by the catalog (for legacy dropdowns)."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for meta in CROP_ENTRIES.values():
        model = meta["aquacrop_model"]
        if model not in seen:
            seen.add(model)
            out.append({"value": model, "label": model})
    return sorted(out, key=lambda x: x["label"])

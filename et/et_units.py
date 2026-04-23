import pandas as pd


MM_TO_INCHES = 0.0393701
INCHES_TO_MM = 25.4


def convert_units(value, from_unit="mm", to_unit="mm"):
    """Convert ET values between mm and inches."""
    if pd.isna(value):
        return value

    if from_unit == to_unit:
        return value

    if from_unit == "mm" and to_unit == "inches":
        return value * MM_TO_INCHES
    if from_unit == "inches" and to_unit == "mm":
        return value * INCHES_TO_MM
    return value


def format_et_value(value, unit="mm", decimal_places=None):
    """Format ET values with appropriate decimal places based on unit."""
    if pd.isna(value):
        return "N/A"

    if unit == "mm":
        places = decimal_places if decimal_places is not None else 1
        return f"{value:.{places}f}"

    places = decimal_places if decimal_places is not None else 3
    return f"{value:.{places}f}"


def get_unit_info(unit="mm"):
    """Get unit metadata for display and rounding."""
    if unit == "mm":
        return {
            "symbol": "mm",
            "name": "millimeters",
            "daily_label": "mm/day",
            "total_label": "mm",
            "decimal_places": 1,
            "daily_decimal_places": 1,
            "total_decimal_places": 0,
        }

    return {
        "symbol": "in",
        "name": "inches",
        "daily_label": "in/day",
        "total_label": "in",
        "decimal_places": 3,
        "daily_decimal_places": 3,
        "total_decimal_places": 1,
    }

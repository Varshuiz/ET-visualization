"""Calculator and download views split from the legacy monolith."""

from .views_legacy import (
    calculate_et_api,
    convert_et_units_api,
    download_comparison_csv,
    download_et_csv,
    download_method_csv,
    enhanced_comparison_calculator,
    hargreaves_only,
    index,
    maule_only,
    penman_monteith_only,
    priestley_taylor_only,
    process_single_method,
    process_single_method_enhanced,
)

__all__ = [
    "index",
    "priestley_taylor_only",
    "penman_monteith_only",
    "maule_only",
    "hargreaves_only",
    "enhanced_comparison_calculator",
    "process_single_method",
    "process_single_method_enhanced",
    "download_et_csv",
    "download_comparison_csv",
    "download_method_csv",
    "calculate_et_api",
    "convert_et_units_api",
]

from django.urls import path

from . import views
from .auth_supabase import login_required_if_supabase_configured as protect

app_name = "et"

urlpatterns = [
    # Auth & farmer dashboard (public auth routes)
    path("auth/register/", views.register_view, name="register"),
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path(
        "dashboard/runs/<str:run_type>/<uuid:run_id>/delete/",
        views.delete_run_view,
        name="delete_run",
    ),
    path("dashboard/et/<uuid:run_id>/", views.et_run_detail_view, name="et_run_detail"),
    path("dashboard/et/<uuid:run_id>/download-csv/", views.et_run_download_csv_view, name="et_run_download_csv"),
    path("et-calculation/<uuid:run_id>/", views.et_run_detail_view, name="et_calculation_detail"),
    path("dashboard/aquacrop/<uuid:run_id>/", views.aquacrop_run_detail_view, name="aquacrop_run_detail"),
    path("dashboard/forecast/<uuid:run_id>/", views.forecast_run_detail_view, name="forecast_run_detail"),
    path("farm/", views.farm_profile_view, name="farm_profile"),
    # Information pages (public)
    path("methods/", views.method_comparison_info, name="method_info"),
    path("help/", views.help_guide, name="help"),
    path("about/", views.about, name="about"),
    # ET calculator & tools (login required when Supabase is configured)
    path("", protect(views.index), name="index"),
    path("comparison/", protect(views.enhanced_comparison_calculator), name="comparison"),
    path("priestley-taylor/", protect(views.priestley_taylor_only), name="priestley_taylor"),
    path("penman-monteith/", protect(views.penman_monteith_only), name="penman_monteith"),
    path("enhanced-comparison/", protect(views.enhanced_comparison_calculator), name="enhanced_comparison"),
    path("maule/", protect(views.maule_only), name="maule"),
    path("hargreaves/", protect(views.hargreaves_only), name="hargreaves"),
    path("download-csv/", protect(views.download_et_csv), name="download_et_csv"),
    path("download-comparison-csv/", protect(views.download_comparison_csv), name="download_comparison_csv"),
    path(
        "download-method-csv/<str:method>/",
        protect(views.download_method_csv),
        name="download_method_csv",
    ),
    path("api/calculate-et/", protect(views.calculate_et_api), name="calculate_et_api"),
    path("api/weather-forecast/", protect(views.get_weather_forecast_api), name="weather_forecast_api"),
    path("api/convert-units/", protect(views.convert_et_units_api), name="convert_units_api"),
    path("fetch-data/", protect(views.acis_data_view), name="acis_fetch"),
    path("comparison-acis/", protect(views.comparison_with_acis), name="comparison_with_acis"),
    path("api/location-search/", protect(views.location_search_api), name="location_search_api"),
    path("update-comparison-plot/", protect(views.update_comparison_plot), name="update_comparison_plot"),
    path("env-canada-forecast/", protect(views.env_canada_forecast_view), name="env_canada_forecast"),
    path("aquacrop/", protect(views.aquacrop_simulation), name="aquacrop_simulation"),
    path(
        "api/aquacrop-season-prefill/",
        protect(views.aquacrop_season_prefill_api),
        name="aquacrop_season_prefill",
    ),
]

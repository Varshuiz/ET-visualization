# Add these URL patterns to your urls.py file

from django.urls import path
from . import views

app_name = 'et'

urlpatterns = [
    # Original views
    path('', views.index, name='index'),
path('comparison/', views.enhanced_comparison_calculator, name='comparison'),    path('priestley-taylor/', views.priestley_taylor_only, name='priestley_taylor'),
    path('penman-monteith/', views.penman_monteith_only, name='penman_monteith'),
    
    # New enhanced comparison view
    path('enhanced-comparison/', views.enhanced_comparison_calculator, name='enhanced_comparison'),
    
    # Individual method views for new methods (corrected)
    path('maule/', views.maule_only, name='maule'),
    path('hargreaves/', views.hargreaves_only, name='hargreaves'),
    
    # Download endpoints
    path('download-csv/', views.download_et_csv, name='download_et_csv'),
    path('download-comparison-csv/', views.download_comparison_csv, name='download_comparison_csv'),
    path('download-method-csv/<str:method>/', views.download_method_csv, name='download_method_csv'),
    
    # API endpoints
    path('api/calculate-et/', views.calculate_et_api, name='calculate_et_api'),
    path('api/weather-forecast/', views.get_weather_forecast_api, name='weather_forecast_api'),
    path('api/convert-units/', views.convert_et_units_api, name='convert_units_api'),
    
    # Information pages
    path('methods/', views.method_comparison_info, name='method_info'),
    path('help/', views.help_guide, name='help'),
    path('about/', views.about, name='about'),


    path('fetch-data/', views.acis_data_view, name='acis_fetch'),
    path('comparison-acis/', views.comparison_with_acis, name='comparison_with_acis'),

    path('api/location-search/', views.location_search_api, name='location_search_api'),
    path('update-comparison-plot/', views.update_comparison_plot, name='update_comparison_plot'),
    path('env-canada-forecast/', views.env_canada_forecast_view, name='env_canada_forecast'),
    path('aquacrop/', views.aquacrop_simulation, name='aquacrop_simulation'),


]
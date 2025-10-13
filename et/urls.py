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
]
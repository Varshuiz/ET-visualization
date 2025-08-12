

from django.urls import path
from . import views

app_name = 'et'

urlpatterns = [
    # Main ET Calculator pages
    path('', views.index, name='index'),  # Original simple calculator
    path('comparison/', views.comparison_calculator, name='comparison'),  # Multi-method comparison
    path('priestley-taylor/', views.priestley_taylor_only, name='priestley_taylor'),  # PT method only
    path('penman-monteith/', views.penman_monteith_only, name='penman_monteith'),  # PM method only
    
    # Download endpoints
    path('download/csv/', views.download_et_csv, name='download_et_csv'),
    path('download/comparison-csv/', views.download_comparison_csv, name='download_comparison_csv'),
    path('download/method-csv/<str:method>/', views.download_method_csv, name='download_method_csv'),
    
    # API endpoints for AJAX requests
    path('api/calculate-et/', views.calculate_et_api, name='calculate_et_api'),
    path('api/weather-forecast/', views.get_weather_forecast_api, name='weather_forecast_api'),
    
    # Information and help pages
    path('methods/', views.method_comparison_info, name='method_info'),
    path('help/', views.help_guide, name='help'),
    path('about/', views.about, name='about'),
]
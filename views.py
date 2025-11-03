
def env_canada_forecast_view(request):
    """
    View to display Environment Canada precipitation forecast
    """
    from .environment_canada_scraper import fetch_env_canada_forecast
    
    error_message = None
    df_forecast = None
    city_name = 'Calgary'  # Default
    
    if request.method == 'POST':
        city_name = request.POST.get('city_name', 'Calgary').strip()
        days = int(request.POST.get('days', 5))
        
        try:
            df = fetch_env_canada_forecast(city_name, days)
            
            # Convert to records for template
            df_forecast = df.to_dict('records')
            
            # Calculate total precipitation
            total_precip = df['Precipitation_mm'].sum()
            
            context = {
                'city_name': city_name,
                'df_forecast': df_forecast,
                'total_precip': total_precip,
                'available_cities': list(fetch_env_canada_forecast.__self__.LOCATION_CODES.keys()) if hasattr(fetch_env_canada_forecast, '__self__') else ['Calgary', 'Edmonton', 'Lethbridge', 'Red Deer']
            }
            
            return render(request, 'et/env_canada_forecast.html', context)
            
        except Exception as e:
            error_message = f"Error fetching forecast: {str(e)}"
    
    # Available cities
    from .environment_canada_scraper import EnvironmentCanadaScraper
    scraper = EnvironmentCanadaScraper()
    available_cities = list(scraper.LOCATION_CODES.keys())
    
    context = {
        'error_message': error_message,
        'city_name': city_name,
        'available_cities': available_cities,
    }
    
    return render(request, 'et/env_canada_forecast.html', context)

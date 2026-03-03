
def comparison_with_acis(request):
    """
    Enhanced comparison view that includes Environment Canada forecast
    """
    from .environment_canada_scraper import fetch_env_canada_forecast
    
    # ... existing code to get ACIS data ...
    
    # Check if we have ACIS data in session
    if 'acis_data' not in request.session:
        return redirect('et:acis_fetch')
    
    acis_data_json = request.session['acis_data']
    location_info = request.session.get('acis_location', {})
    
    df = pd.read_json(acis_data_json)
    df['Date'] = pd.to_datetime(df['Date'])
    
    # ... existing ET calculations ...
    
    # NEW: Fetch Environment Canada forecast for comparison
    env_canada_forecast = None
    try:
        # Get the city name from location description
        location_desc = location_info.get('description', 'Calgary')
        city_name = location_desc.split('(')[0].strip().split(',')[0]
        
        # Fetch 5-day forecast
        ec_df = fetch_env_canada_forecast(city_name, days=5)
        env_canada_forecast = ec_df.to_dict('records')
        
        # Add to context
        context['env_canada_forecast'] = env_canada_forecast
        context['ec_city_name'] = city_name
        
    except Exception as e:
        print(f"Could not fetch Environment Canada forecast: {e}")
    
    return render(request, 'et/comparison_with_acis.html', context)

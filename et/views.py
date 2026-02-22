import pandas as pd
import io
from django.shortcuts import render
from .forms import UploadFileForm
from math import exp
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import base64
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect 
import numpy as np
import requests
import xml.etree.ElementTree as ET
import feedparser
from datetime import datetime, date, timedelta
import calendar
import json
import pandas as pd
import numpy as np
import io
from io import StringIO
from .alberta_acis_scraper import fetch_alberta_acis_data, AlbertaACISScraper
from django.shortcuts import render
from django.http import JsonResponse
import pandas as pd
import json
from .aquacrop_simulator import AquaCropSimulator, run_aquacrop_simulation



# Unit conversion constants
MM_TO_INCHES = 0.0393701
INCHES_TO_MM = 25.4

def convert_units(value, from_unit='mm', to_unit='mm'):
    """Convert ET values between mm and inches"""
    if pd.isna(value):
        return value
    
    if from_unit == to_unit:
        return value
    
    if from_unit == 'mm' and to_unit == 'inches':
        return value * MM_TO_INCHES
    elif from_unit == 'inches' and to_unit == 'mm':
        return value * INCHES_TO_MM
    else:
        return value

def format_et_value(value, unit='mm', decimal_places=None):
    """Format ET values with appropriate decimal places based on unit"""
    if pd.isna(value):
        return "N/A"
    
    if unit == 'mm':
        places = decimal_places if decimal_places is not None else 1
        return f"{value:.{places}f}"
    else:  # inches
        places = decimal_places if decimal_places is not None else 3
        return f"{value:.{places}f}"

def get_unit_info(unit='mm'):
    """Get unit information for display"""
    if unit == 'mm':
        return {
            'symbol': 'mm',
            'name': 'millimeters',
            'daily_label': 'mm/day',
            'total_label': 'mm',
            'decimal_places': 1,
            'daily_decimal_places': 1,
            'total_decimal_places': 0
        }
    else:  # inches
        return {
            'symbol': 'in',
            'name': 'inches',
            'daily_label': 'in/day',
            'total_label': 'in',
            'decimal_places': 3,
            'daily_decimal_places': 3,
            'total_decimal_places': 1
        }

def saturation_vapor_pressure(T):
    """Compute saturation vapor pressure es from temperature T (°C)"""
    return 0.6108 * exp((17.27 * T) / (T + 237.3))

def delta_svp(T):
    """Slope of saturation vapor pressure curve (Δ) at temperature T in kPa/°C"""
    es = saturation_vapor_pressure(T)
    return 4098 * es / (T + 237.3)**2

def actual_vapor_pressure(T, RH):
    """Calculate actual vapor pressure from temperature and relative humidity"""
    if pd.isna(T) or pd.isna(RH):
        return np.nan
    es = saturation_vapor_pressure(T)
    return es * (RH / 100)

def psychrometric_constant(elevation=0):
    """Calculate psychrometric constant (γ) in kPa/°C"""
    P = 101.3 * ((293 - 0.0065 * elevation) / 293) ** 5.26
    return 0.000665 * P

def net_radiation_estimate(Rs, Tmax, Tmin, RH=None):
    """Estimate net radiation from solar radiation and temperature data"""
    if pd.isna(Rs) or pd.isna(Tmax) or pd.isna(Tmin):
        return np.nan
    
    # Net shortwave radiation (assuming albedo = 0.23 for grass reference crop)
    Rns = (1 - 0.23) * Rs
    
    # Stefan-Boltzmann constant (MJ K⁻⁴ m⁻² day⁻¹)
    sigma = 4.903e-9
    
    # Net longwave radiation estimation
    TmaxK = Tmax + 273.16
    TminK = Tmin + 273.16
    
    # Simplified clear sky radiation
    Rso = (0.75 + 2e-5 * 0) * Rs  # Assuming elevation = 0
    Rs_Rso = min(Rs / Rso, 1.0) if Rso > 0 else 0.8
    
    # Net longwave radiation
    if RH is not None and not pd.isna(RH):
        ea = actual_vapor_pressure((Tmax + Tmin)/2, RH)
        Rnl = sigma * (TmaxK**4 + TminK**4) / 2 * (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * Rs_Rso - 0.35)
    else:
        Rnl = sigma * (TmaxK**4 + TminK**4) / 2 * 0.2 * (1.35 * Rs_Rso - 0.35)
    
    Rn = Rns - Rnl
    return max(Rn, 0)

def priestley_taylor_ET(Tavg, Rn, alpha=1.26, gamma=0.066, lambda_val=2.45):
    """Priestley–Taylor ET estimation"""
    if pd.isna(Tavg) or pd.isna(Rn):
        return np.nan
    delta = delta_svp(Tavg)
    return alpha * (delta / (delta + gamma)) * (Rn / lambda_val)

def penman_monteith_ET(Tmax, Tmin, RH, u2, Rs, elevation=0):
    """Penman-Monteith ET₀ calculation (FAO-56 method)"""
    if any(pd.isna(val) for val in [Tmax, Tmin, RH, u2, Rs]):
        return np.nan
    
    # Mean temperature
    Tmean = (Tmax + Tmin) / 2
    
    # Slope of saturation vapour pressure curve
    delta = delta_svp(Tmean)
    
    # Psychrometric constant
    gamma = psychrometric_constant(elevation)
    
    # Saturation vapour pressure
    es = (saturation_vapor_pressure(Tmax) + saturation_vapor_pressure(Tmin)) / 2
    
    # Actual vapour pressure
    ea = actual_vapor_pressure(Tmean, RH)
    
    # Net radiation
    Rn = net_radiation_estimate(Rs, Tmax, Tmin, RH)
    
    # Soil heat flux (assumed to be 0 for daily calculations)
    G = 0
    
    # Wind term
    wind_term = 900 / (Tmean + 273) * u2 * (es - ea)
    
    # Penman-Monteith equation
    numerator = 0.408 * delta * (Rn - G) + gamma * wind_term
    denominator = delta + gamma * (1 + 0.34 * u2)
    
    ET0 = numerator / denominator
    return max(ET0, 0)

def calculate_growing_season_stats(df, et_method='ET_PM', unit='mm'):
    """Calculate growing season cumulative ET and statistics with unit conversion"""
    growing_season_stats = {}
    
    # Ensure we have a date column and ET data
    if 'Date' not in df.columns or et_method not in df.columns:
        return growing_season_stats
    
    # Convert Date to datetime if it's not already
    df['Date'] = pd.to_datetime(df['Date'])
    df['Month'] = df['Date'].dt.month
    df['Year'] = df['Date'].dt.year
    
    # Filter for growing season (May 1 - October 31)
    growing_season = df[df['Month'].isin([5, 6, 7, 8, 9, 10])].copy()
    
    if len(growing_season) == 0:
        return growing_season_stats
    
    # Convert units if needed
    if unit == 'inches':
        growing_season[et_method + '_converted'] = growing_season[et_method].apply(
            lambda x: convert_units(x, 'mm', 'inches')
        )
        et_col = et_method + '_converted'
    else:
        et_col = et_method
    
    # Calculate statistics by year
    yearly_stats = []
    for year in sorted(growing_season['Year'].unique()):
        year_data = growing_season[growing_season['Year'] == year].copy()
        
        if len(year_data) > 0:
            # Calculate cumulative ET
            year_data = year_data.sort_values('Date')
            year_data['Cumulative_ET'] = year_data[et_col].cumsum()
            
            yearly_stats.append({
                'year': year,
                'total_et': year_data[et_col].sum(),
                'avg_daily_et': year_data[et_col].mean(),
                'max_daily_et': year_data[et_col].max(),
                'min_daily_et': year_data[et_col].min(),
                'days_recorded': len(year_data),
                'final_cumulative': year_data['Cumulative_ET'].iloc[-1] if len(year_data) > 0 else 0
            })
    
    # Overall growing season statistics
    if len(yearly_stats) > 0:
        growing_season_stats = {
            'years_analyzed': len(yearly_stats),
            'yearly_stats': yearly_stats,
            'multi_year_avg_total': np.mean([y['total_et'] for y in yearly_stats]),
            'multi_year_avg_daily': np.mean([y['avg_daily_et'] for y in yearly_stats]),
            'highest_season_total': max([y['total_et'] for y in yearly_stats]),
            'lowest_season_total': min([y['total_et'] for y in yearly_stats]),
            'total_days_analyzed': sum([y['days_recorded'] for y in yearly_stats])
        }
    
    return growing_season_stats

def create_growing_season_plots(df, et_method='ET_PM', unit='mm'):
    """Create plots specific to growing season analysis with unit conversion"""
    plots = {}
    
    if 'Date' not in df.columns or et_method not in df.columns:
        return plots
    
    df['Date'] = pd.to_datetime(df['Date'])
    df['Month'] = df['Date'].dt.month
    df['Year'] = df['Date'].dt.year
    
    # Filter for growing season
    growing_season = df[df['Month'].isin([5, 6, 7, 8, 9, 10])].copy()
    
    if len(growing_season) == 0:
        return plots
    
    # Convert units if needed
    if unit == 'inches':
        growing_season[et_method + '_converted'] = growing_season[et_method].apply(
            lambda x: convert_units(x, 'mm', 'inches')
        )
        et_col = et_method + '_converted'
        unit_info = get_unit_info('inches')
    else:
        et_col = et_method
        unit_info = get_unit_info('mm')
    
    # Create figure with subplots
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    fig.patch.set_facecolor('white')
    
    # Plot 1: Cumulative ET by year
    ax1.set_facecolor('#f8fffe')
    colors = ['#087F8C', '#86A873', '#BB9F06', '#5AAA95']
    
    for i, year in enumerate(sorted(growing_season['Year'].unique())):
        year_data = growing_season[growing_season['Year'] == year].copy()
        year_data = year_data.sort_values('Date')
        year_data['Cumulative_ET'] = year_data[et_col].cumsum()
        year_data['Day_of_Year'] = year_data['Date'].dt.dayofyear
        
        color = colors[i % len(colors)]
        ax1.plot(year_data['Day_of_Year'], year_data['Cumulative_ET'], 
                label=f'{year}', color=color, linewidth=2.5)
    
    ax1.set_title('Cumulative ET During Growing Season', fontsize=14, fontweight='bold', color='#095256')
    ax1.set_xlabel('Day of Year', fontsize=12, color='#095256')
    ax1.set_ylabel(f'Cumulative ET ({unit_info["total_label"]})', fontsize=12, color='#095256')
    ax1.grid(True, alpha=0.3, color='#5AAA95')
    ax1.legend()
    
    # Plot 2: Monthly averages
    ax2.set_facecolor('#f8fffe')
    monthly_avg = growing_season.groupby('Month')[et_col].mean()
    months = ['May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct']
    month_numbers = [5, 6, 7, 8, 9, 10]
    
    bars = ax2.bar(months, [monthly_avg.get(m, 0) for m in month_numbers], 
                   color=['#86A873', '#87B374', '#88BD75', '#89C776', '#8AD177', '#8BDB78'])
    
    ax2.set_title('Average Daily ET by Month', fontsize=14, fontweight='bold', color='#095256')
    ax2.set_xlabel('Month', fontsize=12, color='#095256')
    ax2.set_ylabel(f'Average Daily ET ({unit_info["daily_label"]})', fontsize=12, color='#095256')
    ax2.grid(True, alpha=0.3, color='#5AAA95', axis='y')
    
    # Add value labels on bars
    for bar in bars:
        height = bar.get_height()
        ax2.annotate(f'{height:.{unit_info["daily_decimal_places"]}f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom',
                    fontsize=10, color='#095256')
    
    # Plot 3: Daily ET throughout growing season
    ax3.set_facecolor('#f8fffe')
    growing_season_sorted = growing_season.sort_values('Date')
    ax3.plot(growing_season_sorted['Date'], growing_season_sorted[et_col], 
             color='#087F8C', alpha=0.7, linewidth=1)
    ax3.plot(growing_season_sorted['Date'], 
             growing_season_sorted[et_col].rolling(window=7, min_periods=1).mean(),
             color='#095256', linewidth=2, label='7-day average')
    
    ax3.set_title('Daily ET During Growing Season', fontsize=14, fontweight='bold', color='#095256')
    ax3.set_xlabel('Date', fontsize=12, color='#095256')
    ax3.set_ylabel(f'Daily ET ({unit_info["daily_label"]})', fontsize=12, color='#095256')
    ax3.grid(True, alpha=0.3, color='#5AAA95')
    ax3.legend()
    
    # Plot 4: Seasonal totals by year
    ax4.set_facecolor('#f8fffe')
    yearly_totals = growing_season.groupby('Year')[et_col].sum()
    
    bars = ax4.bar(yearly_totals.index.astype(str), yearly_totals.values, 
                   color='#5AAA95', alpha=0.8)
    
    ax4.set_title('Total Growing Season ET by Year', fontsize=14, fontweight='bold', color='#095256')
    ax4.set_xlabel('Year', fontsize=12, color='#095256')
    ax4.set_ylabel(f'Total ET ({unit_info["total_label"]})', fontsize=12, color='#095256')
    ax4.grid(True, alpha=0.3, color='#5AAA95', axis='y')
    
    # Add value labels on bars
    for bar in bars:
        height = bar.get_height()
        ax4.annotate(f'{height:.{unit_info["total_decimal_places"]}f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom',
                    fontsize=10, color='#095256')
    
    plt.tight_layout()
    
    # Convert to base64
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
               facecolor='white', edgecolor='none')
    buf.seek(0)
    plots['growing_season_analysis'] = base64.b64encode(buf.read()).decode('utf-8')
    buf.close()
    plt.close()
    
    return plots



# Add unit toggle support to other views as well
def index(request):
    """Original simple ET calculator (Priestley-Taylor only) with unit toggle"""
    et_data = None
    et_stats = None
    plot_url = None
    
    # Get selected unit from request (default to mm)
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)

    forecast_data = get_lethbridge_forecast()

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['file']
            try:
                # [Previous processing code...]
                # Decode and read CSV safely
                csv_bytes = csv_file.read()
                csv_str = csv_bytes.decode('utf-8', errors='replace')
                df = pd.read_csv(io.StringIO(csv_str))

                # Clean and normalize column names
                df.columns = df.columns.str.strip().str.replace(r"[^\w\s]", "", regex=True).str.replace(" ", "_")

                # Parse Date column safely
                date_col = [col for col in df.columns if 'date' in col.lower()]
                if date_col:
                    df['Date'] = pd.to_datetime(df[date_col[0]], errors='coerce')
                else:
                    df['Date'] = pd.date_range(start='2024-01-01', periods=len(df), freq='D')

                # Find temperature and radiation columns
                temp_cols = [col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])]
                rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                
                if temp_cols and rad_cols:
                    df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                    df['Rn'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                else:
                    raise ValueError("Could not find temperature and solar radiation columns")

                # Compute ET using Priestley-Taylor method only
                df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rn']), axis=1)
                
                # Remove rows with NaN ET values
                df = df.dropna(subset=['ET'])
                
                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated")

                # Compute 5-day rolling average for smoothing
                df['ET_smooth'] = df['ET'].rolling(window=5, min_periods=1).mean()

                # Calculate statistics with unit conversion
                if selected_unit == 'inches':
                    et_stats = {
                        'avg': convert_units(df['ET'].mean(), 'mm', 'inches'),
                        'max': convert_units(df['ET'].max(), 'mm', 'inches'),
                        'min': convert_units(df['ET'].min(), 'mm', 'inches'),
                        'std': convert_units(df['ET'].std(), 'mm', 'inches')
                    }
                else:
                    et_stats = {
                        'avg': df['ET'].mean(),
                        'max': df['ET'].max(),
                        'min': df['ET'].min(),
                        'std': df['ET'].std()
                    }

                # Create the plot with unit conversion
                plt.figure(figsize=(12, 6))
                plt.style.use('default')
                
                # Set the background color
                plt.gca().set_facecolor('#f8fffe')
                
                # Convert data for plotting if needed
                if selected_unit == 'inches':
                    plot_et = df['ET'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                    plot_et_smooth = df['ET_smooth'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                else:
                    plot_et = df['ET']
                    plot_et_smooth = df['ET_smooth']
                
                # Plot the data
                plt.plot(df['Date'], plot_et, 
                        label='Daily ET₀', color='#86A873', alpha=0.6, linewidth=1.5)
                plt.plot(df['Date'], plot_et_smooth, 
                        label='5-day Rolling Average', color='#087F8C', linewidth=3)
                
                # Customize the plot
                plt.title('Evapotranspiration (ET₀) Over Time', 
                         fontsize=16, fontweight='bold', color='#095256', pad=20)
                plt.xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                plt.ylabel(f'ET₀ ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                
                # Customize grid
                plt.grid(True, alpha=0.3, color='#5AAA95')
                
                # Customize legend
                plt.legend(frameon=True, fancybox=True, shadow=True, 
                          loc='upper left', fontsize=10)
                
                # Rotate x-axis labels for better readability
                plt.xticks(rotation=45, ha='right')
                
                # Adjust layout
                plt.tight_layout()
                
                # Convert the plot to base64 string for HTML rendering
                buf = BytesIO()
                plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                           facecolor='white', edgecolor='none')
                buf.seek(0)
                plot_url = base64.b64encode(buf.read()).decode('utf-8')
                buf.close()
                plt.close()

                # Store CSV data in session for download
                request.session['et_data_csv'] = df[['Date', 'ET', 'ET_smooth']].to_csv(index=False)

                # Prepare data for rendering with unit conversion
                et_data = []
                for _, row in df.iterrows():
                    et_val = row['ET'] if not pd.isna(row['ET']) else 0
                    if selected_unit == 'inches' and et_val:
                        et_val = convert_units(et_val, 'mm', 'inches')
                    
                    et_data.append({
                        'Date': row['Date'],
                        'ET': round(et_val, unit_info['decimal_places']) if et_val else 0
                    })

            except Exception as e:
                print(f"File processing error: {e}")
                return render(request, 'et/index.html', {
                    'form': form,
                    'error_message': f"Error processing file: {str(e)}. Please check your CSV format."
                })

    else:
        form = UploadFileForm()
    
    context = {
        'form': form,
        'et_data': et_data,
        'et_stats': et_stats,
        'plot_url': plot_url,
        'forecast_data': forecast_data,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
    }
    
    return render(request, 'et/index.html', context)

def priestley_taylor_only(request):
    """Priestley-Taylor method only with unit toggle"""
    return process_single_method(request, 'PT', 'Priestley-Taylor', 'et/priestley_taylor.html')

def penman_monteith_only(request):
    """Penman-Monteith method only with unit toggle"""
    return process_single_method(request, 'PM', 'Penman-Monteith', 'et/penman_monteith.html')

def process_single_method(request, method_code, method_name, template_name):
    """Helper function to process single ET method calculations with unit toggle"""
    et_data = None
    et_stats = None
    growing_season_stats = None
    plot_url = None
    growing_season_plots = None
    
    # Get selected unit from request (default to mm)
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)
    
    forecast_data = get_lethbridge_forecast()

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # Process CSV file
                csv_file = request.FILES['file']
                csv_bytes = csv_file.read()
                csv_str = csv_bytes.decode('utf-8', errors='replace')
                df = pd.read_csv(io.StringIO(csv_str))

                # Clean column names
                df.columns = df.columns.str.strip().str.replace(r"[^\w\s]", "", regex=True).str.replace(" ", "_")

                # Parse Date column
                date_col = [col for col in df.columns if 'date' in col.lower()]
                if date_col:
                    df['Date'] = pd.to_datetime(df[date_col[0]], errors='coerce')
                else:
                    df['Date'] = pd.date_range(start='2024-01-01', periods=len(df), freq='D')

                # Process based on method
                if method_code == 'PT':
                    temp_cols = [col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])]
                    rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                    
                    if not temp_cols or not rad_cols:
                        raise ValueError("Missing required columns for Priestley-Taylor method")
                    
                    df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                    df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                    df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rs']), axis=1)
                    
                elif method_code == 'PM':
                    # Penman-Monteith requires more parameters
                    temp_cols = [col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])]
                    tmax_cols = [col for col in df.columns if any(term in col.lower() for term in ['tmax', 'max_temp', 'maximum_temp'])]
                    tmin_cols = [col for col in df.columns if any(term in col.lower() for term in ['tmin', 'min_temp', 'minimum_temp'])]
                    rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                    wind_cols = [col for col in df.columns if any(term in col.lower() for term in ['wind', 'wind_speed', 'ws'])]
                    rh_cols = [col for col in df.columns if any(term in col.lower() for term in ['rh', 'humidity', 'relative_humidity'])]

                    # Validate required columns
                    missing_cols = []
                    if not temp_cols and not (tmax_cols and tmin_cols):
                        missing_cols.append("Temperature (average or max/min)")
                    if not rad_cols:
                        missing_cols.append("Solar Radiation")

                    if missing_cols:
                        raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

                    # Assign temperature columns
                    if temp_cols:
                        df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                        if not tmax_cols or not tmin_cols:
                            df['Tmax'] = df['Tavg'] + 5
                            df['Tmin'] = df['Tavg'] - 5
                        else:
                            df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                            df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                    else:
                        df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                        df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                        df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2

                    # Assign other meteorological variables
                    df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                    
                    if wind_cols:
                        df['u2'] = pd.to_numeric(df[wind_cols[0]], errors='coerce')
                    else:
                        df['u2'] = 2.0  # Default wind speed
                        
                    if rh_cols:
                        df['RH'] = pd.to_numeric(df[rh_cols[0]], errors='coerce')
                    else:
                        df['RH'] = 65.0  # Default relative humidity

                    df['ET'] = df.apply(lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs']), axis=1)

                # Common processing for both methods
                df = df.dropna(subset=['ET'])
                
                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated")
                    
                df['ET_smooth'] = df['ET'].rolling(window=5, min_periods=1).mean()

                # Calculate statistics with unit conversion
                if selected_unit == 'inches':
                    et_stats = {
                        'avg': convert_units(df['ET'].mean(), 'mm', 'inches'),
                        'max': convert_units(df['ET'].max(), 'mm', 'inches'),
                        'min': convert_units(df['ET'].min(), 'mm', 'inches'),
                        'std': convert_units(df['ET'].std(), 'mm', 'inches')
                    }
                else:
                    et_stats = {
                        'avg': df['ET'].mean(),
                        'max': df['ET'].max(),
                        'min': df['ET'].min(),  
                        'std': df['ET'].std()
                    }

                # Calculate growing season statistics for single method
                df_temp = df.copy()
                df_temp['ET_METHOD'] = df_temp['ET']  # Rename for compatibility
                growing_season_stats = calculate_growing_season_stats(df_temp, 'ET_METHOD', selected_unit)
                growing_season_plots = create_growing_season_plots(df_temp, 'ET_METHOD', selected_unit)

                # Create single method plot with unit conversion
                plt.figure(figsize=(12, 6))
                plt.gca().set_facecolor('#f8fffe')
                
                colors = {'PT': '#86A873', 'PM': '#087F8C'}
                
                # Convert data for plotting if needed
                if selected_unit == 'inches':
                    plot_et = df['ET'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                    plot_et_smooth = df['ET_smooth'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                else:
                    plot_et = df['ET']
                    plot_et_smooth = df['ET_smooth']
                
                plt.plot(df['Date'], plot_et, label=f'Daily {method_name} ET₀', 
                        color=colors[method_code], alpha=0.6, linewidth=1.5)
                plt.plot(df['Date'], plot_et_smooth, label='5-day Rolling Average', 
                        color=colors[method_code], linewidth=3)
                
                plt.title(f'{method_name} Evapotranspiration', fontsize=16, fontweight='bold', color='#095256', pad=20)
                plt.xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                plt.ylabel(f'ET₀ ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                plt.grid(True, alpha=0.3, color='#5AAA95')
                plt.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=10)
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                
                # Convert plot to base64
                buf = BytesIO()
                plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                           facecolor='white', edgecolor='none')
                buf.seek(0)
                plot_url = base64.b64encode(buf.read()).decode('utf-8')
                buf.close()
                plt.close()

                # Store CSV data in session
                request.session['et_data_csv'] = df[['Date', 'ET', 'ET_smooth']].to_csv(index=False)

                # Prepare data for rendering with unit conversion
                et_data = []
                for _, row in df.iterrows():
                    et_val = row['ET'] if not pd.isna(row['ET']) else 0
                    if selected_unit == 'inches' and et_val:
                        et_val = convert_units(et_val, 'mm', 'inches')
                    
                    et_data.append({
                        'Date': row['Date'],
                        'ET': round(et_val, unit_info['decimal_places']) if et_val else 0
                    })

            except Exception as e:
                print(f"File processing error: {e}")
                return render(request, template_name, {
                    'form': form,
                    'method_name': method_name,
                    'selected_unit': selected_unit,
                    'unit_info': unit_info,
                    'error_message': f"Error processing file: {str(e)}. Please check your CSV format."
                })

    else:
        form = UploadFileForm()
    
    context = {
        'form': form,
        'method_name': method_name,
        'et_data': et_data,
        'et_stats': et_stats,
        'growing_season_stats': growing_season_stats,
        'plot_url': plot_url,
        'growing_season_plots': growing_season_plots,
        'forecast_data': forecast_data,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
    }
    
    return render(request, template_name, context)

def download_et_csv(request):
    """Download the computed ET data as CSV"""
    csv_data = request.session.get('et_data_csv')
    if not csv_data:
        return HttpResponse("No ET data found in session. Please upload and process a file first.", 
                          status=404)

    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="computed_et_data.csv"'
    return response

def download_comparison_csv(request):
    """Download the comparison ET data as CSV"""
    csv_data = request.session.get('et_data_csv')
    if not csv_data:
        return HttpResponse("No ET comparison data found in session. Please upload and process a file first.", 
                          status=404)

    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="et_comparison_data.csv"'
    return response

def download_method_csv(request, method):
    """Download specific method ET data as CSV"""
    csv_data = request.session.get('et_data_csv')
    if not csv_data:
        return HttpResponse("No ET data found in session. Please upload and process a file first.", 
                          status=404)

    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="et_{method.lower()}_data.csv"'
    return response

def calculate_et_api(request):
    """API endpoint for calculating ET values"""
    if request.method == 'POST':
        try:
            data = request.POST
            method = data.get('method', 'PT')
            
            # Process based on method
            if method == 'PT':
                tavg = float(data.get('temperature', 0))
                rs = float(data.get('solar_radiation', 0))
                et_value = priestley_taylor_ET(tavg, rs)
            elif method == 'PM':
                tmax = float(data.get('tmax', 0))
                tmin = float(data.get('tmin', 0))
                rh = float(data.get('humidity', 65))
                u2 = float(data.get('wind_speed', 2))
                rs = float(data.get('solar_radiation', 0))
                et_value = penman_monteith_ET(tmax, tmin, rh, u2, rs)
            else:
                return JsonResponse({'error': 'Invalid method'}, status=400)
            
            return JsonResponse({
                'et_value': round(et_value, 2) if not pd.isna(et_value) else 0,
                'method': method
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Method not allowed'}, status=405)

def get_weather_forecast_api(request):
    """API endpoint for weather forecast"""
    try:
        forecast_data = get_lethbridge_forecast()
        return JsonResponse({'forecast': forecast_data})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def method_comparison_info(request):
    """Information page about ET calculation methods"""
    return render(request, 'et/method_info.html')

def help_guide(request):
    """Help and user guide page"""
    return render(request, 'et/help.html')

def about(request):
    """About page"""
    return render(request, 'et/about.html')

def get_lethbridge_forecast():
    """Get weather forecast data for Lethbridge"""
    rss_url = "https://weather.gc.ca/rss/city/ab-52_e.xml"
    
    try:
        feed = feedparser.parse(rss_url)
        forecast_data = []
        
        for entry in feed.entries:
            published = entry.get("published", "Unknown Date")
            title = entry.get("title", "No Title")
            summary = entry.get("summary", "No Summary")

            forecast_data.append({
                "date": published,
                "title": title,
                "summary": summary
            })

        return forecast_data[:5]  # Return first 5 entries
        
    except Exception as e:
        print(f"Forecast fetch error: {e}")
        return []

# Additional utility functions for unit conversion API
def convert_et_units_api(request):
    """API endpoint for converting ET units"""
    if request.method == 'GET':
        try:
            value = float(request.GET.get('value', 0))
            from_unit = request.GET.get('from', 'mm')
            to_unit = request.GET.get('to', 'mm')
            
            converted_value = convert_units(value, from_unit, to_unit)
            
            return JsonResponse({
                'original_value': value,
                'original_unit': from_unit,
                'converted_value': round(converted_value, 4),
                'converted_unit': to_unit
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# Replace the incorrect "Maule_ET" function with the proper Maulé method

def maule_ET(Tmax, Tmin, Rs, RH=None, latitude=49.7):
    """
    Maulé ET estimation method
    
    The Maulé method is a simplified approach for estimating reference evapotranspiration
    that uses temperature and solar radiation data, with optional humidity correction.
    
    Parameters:
    - Tmax: Maximum temperature (°C)
    - Tmin: Minimum temperature (°C) 
    - Rs: Solar radiation (MJ/m²/day)
    - RH: Relative humidity (%) - optional
    - latitude: Latitude in degrees (default 49.7 for Lethbridge)
    
    Returns:
    - ET in mm/day
    """
    if any(pd.isna(val) for val in [Tmax, Tmin, Rs]):
        return np.nan
    
    # Mean temperature
    Tmean = (Tmax + Tmin) / 2
    
    # Temperature range
    Trange = Tmax - Tmin
    
    # Basic Maulé equation: ET = k * (Tmean + a) * Rs * f(RH)
    # Where k and a are empirical coefficients
    
    # Empirical coefficients for the Maulé method
    k = 0.0031  # Maulé coefficient
    a = 15.0    # Temperature offset
    
    # Base calculation
    ET_base = k * (Tmean + a) * Rs
    
    # Humidity correction factor (if RH is available)
    if RH is not None and not pd.isna(RH):
        # Humidity correction: higher humidity reduces ET
        humidity_factor = 1.0 - (RH - 50) / 200  # Normalized around 50% RH
        humidity_factor = max(0.7, min(1.3, humidity_factor))  # Constrain between 0.7 and 1.3
    else:
        humidity_factor = 1.0
    
    # Temperature range factor (larger ranges increase ET)
    range_factor = 1.0 + (Trange - 10) / 100  # Normalized around 10°C range
    range_factor = max(0.8, min(1.2, range_factor))  # Constrain between 0.8 and 1.2
    
    # Final Maulé ET calculation
    ET_maule = ET_base * humidity_factor * range_factor
    
    return max(ET_maule, 0)


# Update individual method views
def maule_only(request):
    """Maulé method only with unit toggle"""
    return process_single_method_enhanced(request, 'Maule', 'Maulé', 'et/maule.html')



def hargreaves_ET(Tmax, Tmin, Ra=None, latitude=49.7):
    """
    Hargreaves-Samani ET estimation method
    
    Parameters:
    - Tmax: Maximum temperature (°C)
    - Tmin: Minimum temperature (°C)
    - Ra: Extraterrestrial radiation (MJ/m²/day) - optional
    - latitude: Latitude in degrees (default 49.7 for Lethbridge)
    
    Returns:
    - ET in mm/day
    """
    if any(pd.isna(val) for val in [Tmax, Tmin]):
        return np.nan
    
    # Mean temperature
    Tmean = (Tmax + Tmin) / 2
    
    # Temperature range
    Trange = Tmax - Tmin
    
    if Ra is None:
        # Calculate extraterrestrial radiation if not provided
        from datetime import datetime
        day_of_year = 200  # Mid-season approximation
        
        # Solar declination
        solar_declination = 23.45 * np.sin(np.radians(360 * (284 + day_of_year) / 365))
        
        # Convert latitude to radians
        lat_rad = np.radians(latitude)
        decl_rad = np.radians(solar_declination)
        
        # Sunset hour angle
        ws = np.arccos(-np.tan(lat_rad) * np.tan(decl_rad))
        
        # Extraterrestrial radiation (MJ/m²/day)
        Ra = 37.6 * (ws * np.sin(lat_rad) * np.sin(decl_rad) + 
                     np.cos(lat_rad) * np.cos(decl_rad) * np.sin(ws))
    
    # Hargreaves coefficient
    C_H = 0.0023
    
    # Hargreaves ET equation
    ET_hargreaves = C_H * (Tmean + 17.8) * np.sqrt(Trange) * Ra
    
    return max(ET_hargreaves, 0)

def calculate_extraterrestrial_radiation(latitude, day_of_year):
    """
    Calculate extraterrestrial radiation for a given latitude and day of year
    
    Parameters:
    - latitude: Latitude in degrees
    - day_of_year: Day of year (1-365)
    
    Returns:
    - Ra: Extraterrestrial radiation (MJ/m²/day)
    """
    # Solar constant
    Gsc = 0.0820  # MJ/m²/min
    
    # Convert latitude to radians
    lat_rad = np.radians(latitude)
    
    # Inverse relative distance Earth-Sun
    dr = 1 + 0.033 * np.cos(2 * np.pi * day_of_year / 365)
    
    # Solar declination
    delta = 0.409 * np.sin(2 * np.pi * day_of_year / 365 - 1.39)
    
    # Sunset hour angle
    ws = np.arccos(-np.tan(lat_rad) * np.tan(delta))
    
    # Extraterrestrial radiation
    Ra = (24 * 60 / np.pi) * Gsc * dr * (
        ws * np.sin(lat_rad) * np.sin(delta) + 
        np.cos(lat_rad) * np.cos(delta) * np.sin(ws)
    )
    
    return Ra

# Enhanced comparison calculator with all four methods
# Replace your enhanced_comparison_calculator function with this fixed version

def enhanced_comparison_calculator(request):
    """Enhanced ET calculator with all four methods and growing season analysis"""
    et_data = None
    et_stats = None
    comparison_stats = None
    growing_season_stats = None
    plot_url = None
    growing_season_plots = None
    
    # Get selected unit from request
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)
    forecast_data = get_lethbridge_forecast()

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['file']
            try:
                # Process CSV file
                csv_bytes = csv_file.read()
                csv_str = csv_bytes.decode('utf-8', errors='replace')
                df = pd.read_csv(io.StringIO(csv_str))

                # Clean and normalize column names
                df.columns = df.columns.str.strip().str.replace(r"[^\w\s]", "", regex=True).str.replace(" ", "_")

                # Parse Date column
                date_col = [col for col in df.columns if 'date' in col.lower()]
                if date_col:
                    df['Date'] = pd.to_datetime(df[date_col[0]], errors='coerce')
                else:
                    df['Date'] = pd.date_range(start='2024-01-01', periods=len(df), freq='D')

                # Add day of year column for radiation calculations
                df['day_of_year'] = df['Date'].dt.dayofyear

                # Find required columns
                temp_cols = [col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])]
                tmax_cols = [col for col in df.columns if any(term in col.lower() for term in ['tmax', 'max_temp', 'maximum_temp'])]
                tmin_cols = [col for col in df.columns if any(term in col.lower() for term in ['tmin', 'min_temp', 'minimum_temp'])]
                rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                wind_cols = [col for col in df.columns if any(term in col.lower() for term in ['wind', 'wind_speed', 'ws'])]
                rh_cols = [col for col in df.columns if any(term in col.lower() for term in ['rh', 'humidity', 'relative_humidity'])]

                # Validate minimum required columns
                missing_cols = []
                if not temp_cols and not (tmax_cols and tmin_cols):
                    missing_cols.append("Temperature (average or max/min)")

                if missing_cols:
                    raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

                # Assign temperature columns
                if temp_cols:
                    df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                    if not tmax_cols or not tmin_cols:
                        df['Tmax'] = df['Tavg'] + 5
                        df['Tmin'] = df['Tavg'] - 5
                    else:
                        df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                        df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                else:
                    df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                    df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                    df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2

                # Assign solar radiation (with fallback estimation)
                if rad_cols:
                    df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                else:
                    # Estimate solar radiation from temperature if not available
                    df['Rs'] = (df['Tmax'] - df['Tmin']) * 0.16 * np.sqrt(12)
                
                # Assign wind speed (with default)
                if wind_cols:
                    df['u2'] = pd.to_numeric(df[wind_cols[0]], errors='coerce')
                else:
                    df['u2'] = 2.0  # Default wind speed
                    
                # Assign relative humidity (with default)
                if rh_cols:
                    df['RH'] = pd.to_numeric(df[rh_cols[0]], errors='coerce')
                else:
                    df['RH'] = 65.0  # Default relative humidity

                # Calculate extraterrestrial radiation for each day
                df['Ra'] = df.apply(lambda row: calculate_extraterrestrial_radiation(49.7, row['day_of_year']), axis=1)

                # Calculate ET using all four methods - with proper error handling
                # Priestley-Taylor
                try:
                    df['ET_PT'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rs']), axis=1)
                except Exception as e:
                    print(f"PT calculation failed: {e}")
                    df['ET_PT'] = np.nan

                # Penman-Monteith
                try:
                    df['ET_PM'] = df.apply(lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs']), axis=1)
                except Exception as e:
                    print(f"PM calculation failed: {e}")
                    df['ET_PM'] = np.nan

                # Maulé - FIXED: properly pass RH value
                try:
                    df['ET_Maule'] = df.apply(
                        lambda row: maule_ET(
                            row['Tmax'], 
                            row['Tmin'], 
                            row['Rs'], 
                            row['RH'] if not pd.isna(row['RH']) else None
                        ), 
                        axis=1
                    )
                except Exception as e:
                    print(f"Maule calculation failed: {e}")
                    df['ET_Maule'] = np.nan

                # Hargreaves
                try:
                    df['ET_Hargreaves'] = df.apply(lambda row: hargreaves_ET(row['Tmax'], row['Tmin'], row['Ra']), axis=1)
                except Exception as e:
                    print(f"Hargreaves calculation failed: {e}")
                    df['ET_Hargreaves'] = np.nan

                # Remove rows with ALL NaN ET values
                et_columns = ['ET_PT', 'ET_PM', 'ET_Maule', 'ET_Hargreaves']
                df = df.dropna(subset=et_columns, how='all')
                
                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated for any method")

                # Compute rolling averages for smoothing
                for method in ['PT', 'PM', 'Maule', 'Hargreaves']:
                    col = f'ET_{method}'
                    if col in df.columns:
                        df[f'{col}_smooth'] = df[col].rolling(window=5, min_periods=1).mean()

                # Calculate statistics for all methods with unit conversion
                et_stats = {}
                method_names = {
                    'PT': 'Priestley-Taylor',
                    'PM': 'Penman-Monteith', 
                    'Maule': 'Maulé',  
                    'Hargreaves': 'Hargreaves-Samani'
                }
                
                for method, name in method_names.items():
                    col = f'ET_{method}'
                    if col in df.columns and not df[col].isna().all():
                        # Convert values if needed
                        if selected_unit == 'inches':
                            avg_val = convert_units(df[col].mean(), 'mm', 'inches')
                            max_val = convert_units(df[col].max(), 'mm', 'inches')
                            min_val = convert_units(df[col].min(), 'mm', 'inches')
                            std_val = convert_units(df[col].std(), 'mm', 'inches')
                        else:
                            avg_val = df[col].mean()
                            max_val = df[col].max()
                            min_val = df[col].min()
                            std_val = df[col].std()
                        
                        et_stats[method] = {
                            'name': name,
                            'avg': avg_val,
                            'max': max_val,
                            'min': min_val,
                            'std': std_val
                        }

                # Enhanced comparison statistics
                comparison_stats = {}
                available_methods = [method for method in ['PT', 'PM', 'Maule', 'Hargreaves'] if f'ET_{method}' in df.columns and not df[f'ET_{method}'].isna().all()]
                
                if len(available_methods) >= 2:
                    # Calculate correlations between methods
                    et_cols = [f'ET_{method}' for method in available_methods]
                    corr_matrix = df[et_cols].corr()
                    
                    # Store correlation data
                    comparison_stats['correlations'] = {}
                    for i, method1 in enumerate(available_methods):
                        for j, method2 in enumerate(available_methods):
                            if i < j:  # Only store upper triangle
                                key = f'{method1} vs {method2}'
                                comparison_stats['correlations'][key] = corr_matrix.iloc[i, j]
                    
                    # Calculate mean differences (with unit conversion) - only if PM exists
                    if 'PM' in available_methods:
                        for method in available_methods:
                            if method != 'PM':
                                diff_mm = (df[f'ET_{method}'] - df['ET_PM']).mean()
                                if selected_unit == 'inches':
                                    comparison_stats[f'{method}_PM_diff'] = convert_units(diff_mm, 'mm', 'inches')
                                else:
                                    comparison_stats[f'{method}_PM_diff'] = diff_mm

                # Calculate growing season statistics for primary method (PM if available, otherwise first available)
                if 'ET_PM' in df.columns and not df['ET_PM'].isna().all():
                    growing_season_stats = calculate_growing_season_stats(df, 'ET_PM', selected_unit)
                    growing_season_plots = create_growing_season_plots(df, 'ET_PM', selected_unit)
                elif available_methods:
                    primary_method = available_methods[0]
                    growing_season_stats = calculate_growing_season_stats(df, f'ET_{primary_method}', selected_unit)
                    growing_season_plots = create_growing_season_plots(df, f'ET_{primary_method}', selected_unit)

                # Create enhanced comparison plot with all available methods
                fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
                fig.patch.set_facecolor('white')
                
                # Main ET comparison plot
                ax1.set_facecolor('#f8fffe')
                colors = {
                    'PT': '#86A873',
                    'PM': '#087F8C', 
                    'Maule': '#BB9F06',
                    'Hargreaves': '#5AAA95'
                }
                
                for method in available_methods:
                    col = f'ET_{method}'
                    # Convert data for plotting if needed
                    if selected_unit == 'inches':
                        plot_data = df[col].apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                        plot_data_smooth = df[f'{col}_smooth'].apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                    else:
                        plot_data = df[col]
                        plot_data_smooth = df[f'{col}_smooth']
                    
                    ax1.plot(df['Date'], plot_data, 
                            label=f'{method_names[method]}', 
                            color=colors[method], alpha=0.6, linewidth=1.5)
                    ax1.plot(df['Date'], plot_data_smooth, 
                            color=colors[method], linewidth=2.5, alpha=0.9)

                ax1.set_title('Evapotranspiration Method Comparison', 
                             fontsize=16, fontweight='bold', color='#095256', pad=20)
                ax1.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                ax1.set_ylabel(f'ET₀ ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                ax1.grid(True, alpha=0.3, color='#5AAA95')
                ax1.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=10)
                ax1.tick_params(axis='x', rotation=45)

                # Method differences from Penman-Monteith (reference) - only if PM exists
                ax2.set_facecolor('#f8fffe')
                if 'PM' in available_methods and len(available_methods) > 1:
                    for method in available_methods:
                        if method != 'PM':
                            col = f'ET_{method}'
                            diff = df['ET_PM'] - df[col]
                            if selected_unit == 'inches':
                                diff = diff.apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                            
                            ax2.plot(df['Date'], diff, color=colors[method], linewidth=2, alpha=0.7, 
                                    label=f'PM - {method_names[method]}')
                    
                    ax2.axhline(y=0, color='#095256', linestyle='--', alpha=0.8)
                    ax2.set_title('Differences from Penman-Monteith (Reference Method)', 
                                 fontsize=14, fontweight='bold', color='#095256')
                    ax2.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                    ax2.set_ylabel(f'Difference ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                    ax2.grid(True, alpha=0.3, color='#5AAA95')
                    ax2.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=9)
                    ax2.tick_params(axis='x', rotation=45)
                else:
                    # If no PM or only one method, show a message
                    ax2.text(0.5, 0.5, 'Difference plot requires Penman-Monteith method', 
                            ha='center', va='center', fontsize=14, color='#666')
                    ax2.set_xlim(0, 1)
                    ax2.set_ylim(0, 1)
                    ax2.axis('off')

                plt.tight_layout()
                
                # Convert plot to base64
                buf = BytesIO()
                plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                           facecolor='white', edgecolor='none')
                buf.seek(0)
                plot_url = base64.b64encode(buf.read()).decode('utf-8')
                buf.close()
                plt.close()

                # Store enhanced CSV data in session
                csv_columns = ['Date'] + [f'ET_{method}' for method in available_methods]
                request.session['et_data_csv'] = df[csv_columns].to_csv(index=False)

                # Prepare data for rendering with unit conversion
                et_data = []
                for _, row in df.iterrows():
                    data_row = {'Date': row['Date']}
                    
                    for method in available_methods:
                        col = f'ET_{method}'
                        val = row[col] if not pd.isna(row[col]) else 0
                        if selected_unit == 'inches' and val:
                            val = convert_units(val, 'mm', 'inches')
                        data_row[f'ET_{method}'] = round(val, unit_info['decimal_places']) if val else 0
                    
                    et_data.append(data_row)

            except Exception as e:
                print(f"File processing error: {e}")
                import traceback
                traceback.print_exc()
                return render(request, 'et/comparison.html', {
                    'form': form,
                    'error_message': f"Error processing file: {str(e)}. Please check your CSV format.",
                    'selected_unit': selected_unit,
                    'unit_info': unit_info,
                })

    else:
        form = UploadFileForm()
    
    context = {
        'form': form,
        'et_data': et_data,
        'et_stats': et_stats,
        'comparison_stats': comparison_stats,
        'growing_season_stats': growing_season_stats,
        'plot_url': plot_url,
        'growing_season_plots': growing_season_plots,
        'forecast_data': forecast_data,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
    }
    
    return render(request, 'et/comparison.html', context)


# Add these individual method views to your views.py


def hargreaves_only(request):
    """Hargreaves-Samani method only with unit toggle"""
    return process_single_method_enhanced(request, 'Hargreaves', 'Hargreaves-Samani', 'et/hargreaves.html')

def process_single_method_enhanced(request, method_code, method_name, template_name):
    """Enhanced helper function to process single ET method calculations with all four methods support"""
    et_data = None
    et_stats = None
    growing_season_stats = None
    plot_url = None
    growing_season_plots = None
    
    # Get selected unit from request (default to mm)
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)
    
    forecast_data = get_lethbridge_forecast()

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # Process CSV file
                csv_file = request.FILES['file']
                csv_bytes = csv_file.read()
                csv_str = csv_bytes.decode('utf-8', errors='replace')
                df = pd.read_csv(io.StringIO(csv_str))

                # Clean column names
                df.columns = df.columns.str.strip().str.replace(r"[^\w\s]", "", regex=True).str.replace(" ", "_")

                # Parse Date column
                date_col = [col for col in df.columns if 'date' in col.lower()]
                if date_col:
                    df['Date'] = pd.to_datetime(df[date_col[0]], errors='coerce')
                else:
                    df['Date'] = pd.date_range(start='2024-01-01', periods=len(df), freq='D')

                # Add day of year for radiation calculations
                df['day_of_year'] = df['Date'].dt.dayofyear

                # Find required columns
                temp_cols = [col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])]
                tmax_cols = [col for col in df.columns if any(term in col.lower() for term in ['tmax', 'max_temp', 'maximum_temp'])]
                tmin_cols = [col for col in df.columns if any(term in col.lower() for term in ['tmin', 'min_temp', 'minimum_temp'])]
                rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                wind_cols = [col for col in df.columns if any(term in col.lower() for term in ['wind', 'wind_speed', 'ws'])]
                rh_cols = [col for col in df.columns if any(term in col.lower() for term in ['rh', 'humidity', 'relative_humidity'])]

                # Process based on method requirements
                if method_code in ['Maule', 'Hargreaves']:
                    # Both Maulé and Hargreaves require temperature data
                    if not temp_cols and not (tmax_cols and tmin_cols):
                        raise ValueError("Missing required temperature columns for this method")
                    
                    # Assign temperature columns
                    if temp_cols:
                        df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                        if not tmax_cols or not tmin_cols:
                            df['Tmax'] = df['Tavg'] + 5
                            df['Tmin'] = df['Tavg'] - 5
                        else:
                            df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                            df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                    else:
                        df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                        df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                        df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2

                    # Calculate extraterrestrial radiation
                    df['Ra'] = df.apply(lambda row: calculate_extraterrestrial_radiation(49.7, row['day_of_year']), axis=1)

                    if method_code == 'Maule':
                        # Maulé needs solar radiation and optionally humidity
                        if rad_cols:
                            df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                        else:
                            # Estimate solar radiation if not available
                            df['Rs'] = (df['Tmax'] - df['Tmin']) * 0.16 * np.sqrt(12)
                        
                        # Assign humidity if available
                        if rh_cols:
                            df['RH'] = pd.to_numeric(df[rh_cols[0]], errors='coerce')
                        else:
                            df['RH'] = None  # Maulé can work without humidity
                        
                        # Apply Maulé method
                        df['ET'] = df.apply(lambda row: maule_ET(row['Tmax'], row['Tmin'], row['Rs'], 
                                                               row['RH'] if not pd.isna(row.get('RH', np.nan)) else None), axis=1)
                    
                    elif method_code == 'Hargreaves':
                        df['ET'] = df.apply(lambda row: hargreaves_ET(row['Tmax'], row['Tmin'], row['Ra']), axis=1)

                elif method_code == 'PT':
                    # Priestley-Taylor processing
                    if not temp_cols or not rad_cols:
                        raise ValueError("Missing required columns for Priestley-Taylor method (temperature and solar radiation)")
                    
                    df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                    df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                    df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rs']), axis=1)
                    
                elif method_code == 'PM':
                    # Penman-Monteith processing
                    missing_cols = []
                    if not temp_cols and not (tmax_cols and tmin_cols):
                        missing_cols.append("Temperature (average or max/min)")
                    if not rad_cols:
                        missing_cols.append("Solar Radiation")

                    if missing_cols:
                        raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

                    # Assign temperature columns
                    if temp_cols:
                        df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                        if not tmax_cols or not tmin_cols:
                            df['Tmax'] = df['Tavg'] + 5
                            df['Tmin'] = df['Tavg'] - 5
                        else:
                            df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                            df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                    else:
                        df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                        df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                        df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2

                    # Assign other meteorological variables
                    df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                    
                    if wind_cols:
                        df['u2'] = pd.to_numeric(df[wind_cols[0]], errors='coerce')
                    else:
                        df['u2'] = 2.0  # Default wind speed
                        
                    if rh_cols:
                        df['RH'] = pd.to_numeric(df[rh_cols[0]], errors='coerce')
                    else:
                        df['RH'] = 65.0  # Default relative humidity

                    df['ET'] = df.apply(lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs']), axis=1)

                # Common processing for all methods
                df = df.dropna(subset=['ET'])
                
                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated")
                    
                df['ET_smooth'] = df['ET'].rolling(window=5, min_periods=1).mean()

                # Calculate statistics with unit conversion
                if selected_unit == 'inches':
                    et_stats = {
                        'avg': convert_units(df['ET'].mean(), 'mm', 'inches'),
                        'max': convert_units(df['ET'].max(), 'mm', 'inches'),
                        'min': convert_units(df['ET'].min(), 'mm', 'inches'),
                        'std': convert_units(df['ET'].std(), 'mm', 'inches')
                    }
                else:
                    et_stats = {
                        'avg': df['ET'].mean(),
                        'max': df['ET'].max(),
                        'min': df['ET'].min(),  
                        'std': df['ET'].std()
                    }

                # Calculate growing season statistics for single method
                df_temp = df.copy()
                df_temp['ET_METHOD'] = df_temp['ET']  # Rename for compatibility
                growing_season_stats = calculate_growing_season_stats(df_temp, 'ET_METHOD', selected_unit)
                growing_season_plots = create_growing_season_plots(df_temp, 'ET_METHOD', selected_unit)

                # Create single method plot with unit conversion
                plt.figure(figsize=(12, 6))
                plt.gca().set_facecolor('#f8fffe')
                
                colors = {
                    'PT': '#86A873', 
                    'PM': '#087F8C',
                    'Maule': '#BB9F06',
                    'Hargreaves': '#5AAA95'
                }
                
                # Convert data for plotting if needed
                if selected_unit == 'inches':
                    plot_et = df['ET'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                    plot_et_smooth = df['ET_smooth'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                else:
                    plot_et = df['ET']
                    plot_et_smooth = df['ET_smooth']
                
                color = colors.get(method_code, '#087F8C')
                plt.plot(df['Date'], plot_et, label=f'Daily {method_name} ET₀', 
                        color=color, alpha=0.6, linewidth=1.5)
                plt.plot(df['Date'], plot_et_smooth, label='5-day Rolling Average', 
                        color=color, linewidth=3)
                
                plt.title(f'{method_name} Evapotranspiration', fontsize=16, fontweight='bold', color='#095256', pad=20)
                plt.xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                plt.ylabel(f'ET₀ ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                plt.grid(True, alpha=0.3, color='#5AAA95')
                plt.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=10)
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                
                # Convert plot to base64
                buf = BytesIO()
                plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                           facecolor='white', edgecolor='none')
                buf.seek(0)
                plot_url = base64.b64encode(buf.read()).decode('utf-8')
                buf.close()
                plt.close()

                # Store CSV data in session
                request.session['et_data_csv'] = df[['Date', 'ET', 'ET_smooth']].to_csv(index=False)

                # Prepare data for rendering with unit conversion
                et_data = []
                for _, row in df.iterrows():
                    et_val = row['ET'] if not pd.isna(row['ET']) else 0
                    if selected_unit == 'inches' and et_val:
                        et_val = convert_units(et_val, 'mm', 'inches')
                    
                    et_data.append({
                        'Date': row['Date'],
                        'ET': round(et_val, unit_info['decimal_places']) if et_val else 0
                    })

            except Exception as e:
                print(f"File processing error: {e}")
                return render(request, template_name, {
                    'form': form,
                    'method_name': method_name,
                    'selected_unit': selected_unit,
                    'unit_info': unit_info,
                    'error_message': f"Error processing file: {str(e)}. Please check your CSV format."
                })

    else:
        form = UploadFileForm()
    
    context = {
        'form': form,
        'method_name': method_name,
        'et_data': et_data,
        'et_stats': et_stats,
        'growing_season_stats': growing_season_stats,
        'plot_url': plot_url,
        'growing_season_plots': growing_season_plots,
        'forecast_data': forecast_data,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
    }
    
    return render(request, template_name, context)


    return max(ET0, 0)


def geocode_location(place_name, province="Alberta", country="Canada"):
    """
    Convert place name to coordinates using Nominatim (OpenStreetMap) geocoding
    Free, no API key required!
    
    Parameters:
    - place_name: Name of the place (e.g., "Lethbridge", "Calgary")
    - province: Province name (default "Alberta")
    - country: Country name (default "Canada")
    
    Returns:
    - dict with latitude, longitude, display_name
    """
    
    # Nominatim API (free, no key needed)
    url = "https://nominatim.openstreetmap.org/search"
    
    # Build search query
    search_query = f"{place_name}, {province}, {country}"
    
    params = {
        'q': search_query,
        'format': 'json',
        'limit': 1,
        'addressdetails': 1
    }
    
    headers = {
        'User-Agent': 'ET-Calculator/1.0 (Agricultural Research)'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code != 200:
            raise ValueError(f"Geocoding failed: HTTP {response.status_code}")
        
        results = response.json()
        
        if not results or len(results) == 0:
            raise ValueError(f"Location '{place_name}' not found")
        
        result = results[0]
        
        return {
            'latitude': float(result['lat']),
            'longitude': float(result['lon']),
            'display_name': result['display_name'],
            'place_name': place_name
        }
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Geocoding error: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error processing location: {str(e)}")


def reverse_geocode(latitude, longitude):
    """
    Convert coordinates back to place name
    Useful for displaying location from township/range
    """
    
    url = "https://nominatim.openstreetmap.org/reverse"
    
    params = {
        'lat': latitude,
        'lon': longitude,
        'format': 'json',
        'zoom': 10
    }
    
    headers = {
        'User-Agent': 'ET-Calculator/1.0 (Agricultural Research)'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if 'display_name' in result:
                return result['display_name']
        
        return f"{latitude:.4f}°N, {abs(longitude):.4f}°W"
        
    except:
        return f"{latitude:.4f}°N, {abs(longitude):.4f}°W"


# Alberta Cities and Towns Database
ALBERTA_LOCATIONS = {
    # Major Cities
    'Calgary': {'lat': 51.0447, 'lon': -114.0719, 'twp': 24, 'rge': 1, 'mer': '5th'},
    'Edmonton': {'lat': 53.5461, 'lon': -113.4938, 'twp': 53, 'rge': 25, 'mer': '4th'},
    'Red Deer': {'lat': 52.2681, 'lon': -113.8111, 'twp': 38, 'rge': 27, 'mer': '4th'},
    'Lethbridge': {'lat': 49.6942, 'lon': -112.8328, 'twp': 9, 'rge': 22, 'mer': '4th'},
    'Medicine Hat': {'lat': 50.0417, 'lon': -110.6775, 'twp': 13, 'rge': 6, 'mer': '4th'},
    'Grande Prairie': {'lat': 55.1707, 'lon': -118.7947, 'twp': 72, 'rge': 6, 'mer': '6th'},
    'Fort McMurray': {'lat': 56.7267, 'lon': -111.3790, 'twp': 88, 'rge': 9, 'mer': '4th'},
    'Medicine Lake Auto': {'lat': 54.0181, 'lon': 	-112.9767, 'twp': 52, 'rge': 3, 'mer': '5th'},

    
    # Medium Cities
    'Airdrie': {'lat': 51.2917, 'lon': -114.0144, 'twp': 26, 'rge': 1, 'mer': '5th'},
    'St. Albert': {'lat': 53.6303, 'lon': -113.6258, 'twp': 54, 'rge': 25, 'mer': '4th'},
    'Spruce Grove': {'lat': 53.5450, 'lon': -113.9006, 'twp': 53, 'rge': 26, 'mer': '4th'},
    'Leduc': {'lat': 53.2594, 'lon': -113.5514, 'twp': 50, 'rge': 25, 'mer': '4th'},
    'Okotoks': {'lat': 50.7264, 'lon': -113.9764, 'twp': 21, 'rge': 29, 'mer': '4th'},
    'Cochrane': {'lat': 51.1889, 'lon': -114.4678, 'twp': 25, 'rge': 4, 'mer': '5th'},
    'Camrose': {'lat': 53.0158, 'lon': -112.8403, 'twp': 47, 'rge': 20, 'mer': '4th'},
    'Lloydminster': {'lat': 53.2783, 'lon': -110.0050, 'twp': 50, 'rge': 1, 'mer': '4th'},
    'Brooks': {'lat': 50.5644, 'lon': -111.8986, 'twp': 19, 'rge': 14, 'mer': '4th'},
    'Wetaskiwin': {'lat': 52.9694, 'lon': -113.3769, 'twp': 46, 'rge': 25, 'mer': '4th'},
    'Cold Lake': {'lat': 54.4639, 'lon': -110.1817, 'twp': 63, 'rge': 4, 'mer': '4th'},
    'High River': {'lat': 50.5831, 'lon': -113.8711, 'twp': 19, 'rge': 28, 'mer': '4th'},
    'Sylvan Lake': {'lat': 52.3083, 'lon': -114.0972, 'twp': 39, 'rge': 1, 'mer': '5th'},
    'Canmore': {'lat': 51.0892, 'lon': -115.3580, 'twp': 25, 'rge': 10, 'mer': '5th'},
    'Chestermere': {'lat': 51.0503, 'lon': -113.8236, 'twp': 24, 'rge': 28, 'mer': '4th'},
    'Strathmore': {'lat': 51.0367, 'lon': -113.3978, 'twp': 24, 'rge': 25, 'mer': '4th'},
    'Beaumont': {'lat': 53.3572, 'lon': -113.4147, 'twp': 51, 'rge': 24, 'mer': '4th'},
    'Stony Plain': {'lat': 53.5264, 'lon': -114.0069, 'twp': 53, 'rge': 1, 'mer': '5th'},
    'Fort Saskatchewan': {'lat': 53.7111, 'lon': -113.2178, 'twp': 55, 'rge': 22, 'mer': '4th'},
    'Drumheller': {'lat': 51.4631, 'lon': -112.7086, 'twp': 28, 'rge': 19, 'mer': '4th'},
    'Banff': {'lat': 51.1784, 'lon': -115.5708, 'twp': 25, 'rge': 12, 'mer': '5th'},
    'Jasper': {'lat': 52.8737, 'lon': -118.0814, 'twp': 46, 'rge': 1, 'mer': '6th'},
    'Hinton': {'lat': 53.4047, 'lon': -117.5850, 'twp': 52, 'rge': 24, 'mer': '5th'},
    'Whitecourt': {'lat': 54.1428, 'lon': -115.6833, 'twp': 60, 'rge': 13, 'mer': '5th'},
    'Slave Lake': {'lat': 55.2817, 'lon': -114.7728, 'twp': 74, 'rge': 10, 'mer': '5th'},
    'Peace River': {'lat': 56.2297, 'lon': -117.2919, 'twp': 82, 'rge': 22, 'mer': '5th'},
}


def search_alberta_location(query):
    """
    Search Alberta locations database with fuzzy matching
    Falls back to allowing any station name for ACIS scraper
    """
    query = query.strip().lower()
    
    # Exact match in database
    for place, data in ALBERTA_LOCATIONS.items():
        if place.lower() == query:
            return {
                'place_name': place,
                'latitude': data['lat'],
                'longitude': data['lon'],
                'township': data.get('twp'),
                'range': data.get('rge'),
                'meridian': data.get('mer'),
                'source': 'database'
            }
    
    # Partial match in database
    for place, data in ALBERTA_LOCATIONS.items():
        if query in place.lower():
            return {
                'place_name': place,
                'latitude': data['lat'],
                'longitude': data['lon'],
                'township': data.get('twp'),
                'range': data.get('rge'),
                'meridian': data.get('mer'),
                'source': 'database'
            }
    
    # NEW: If not in database, treat it as a direct ACIS station name
    # Return generic Alberta coordinates - the scraper will find the station
    return {
        'place_name': query.title(),
        'latitude': 53.5,  # Central Alberta
        'longitude': -114.0,
        'township': None,
        'range': None,
        'meridian': None,
        'source': 'station_name',
        'is_direct_station': True
    }
# API endpoint for location search autocomplete
def location_search_api(request):
    """
    AJAX endpoint for location search
    Returns JSON with matching locations
    """
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'results': []})
    
    results = []
    query_lower = query.lower()
    
    # Search Alberta locations database
    for place, data in ALBERTA_LOCATIONS.items():
        if query_lower in place.lower():
            results.append({
                'name': place,
                'display': f"{place} (Twp {data.get('twp')}, Rge {data.get('rge')}, {data.get('mer')} Meridian)",
                'latitude': data['lat'],
                'longitude': data['lon'],
                'township': data.get('twp'),
                'range': data.get('rge'),
                'meridian': data.get('mer')
            })
    
    return JsonResponse({'results': results[:10]})  # Limit to 10 results


def acis_data_view(request):
    """
    View for fetching ACIS data - NOW WITH WEB SCRAPING from Alberta ACIS!
    """
    error_message = None
    df_preview = None
    success_message = None
    location_result = None
    scraping = False
    
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)
    
    if request.method == 'POST':
        try:
            location_type = request.POST.get('location_type', 'place')
            
            # Get date range first
            start_date = request.POST.get('start_date', '').strip()
            end_date = request.POST.get('end_date', '').strip()
            
            if not start_date or not end_date:
                raise ValueError("Please provide both start and end dates")
            
            # Validate dates
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                
                if end_dt < start_dt:
                    raise ValueError("End date must be after start date")
                
                if (end_dt - start_dt).days > 730:  # 2 years max
                    raise ValueError("Date range cannot exceed 2 years")
                
            except ValueError as e:
                if "does not match format" in str(e):
                    raise ValueError("Invalid date format")
                raise
            
            # Determine location
            if location_type == 'place':
                place_name = request.POST.get('place_name', '').strip()
                
                if not place_name:
                    raise ValueError("Please enter a location name")
                
                print(f"Searching for: {place_name}")
                location_result = search_alberta_location(place_name)
                
                if not location_result:
                    raise ValueError(f"Location '{place_name}' not found. Try 'Calgary', 'Lethbridge', etc.")
                
                latitude = location_result['latitude']
                longitude = location_result['longitude']
                station_name = location_result['place_name']
                location_desc = location_result['place_name']
                
                if location_result.get('township'):
                    location_desc += f" (Twp {location_result['township']}, Rge {location_result['range']}, {location_result['meridian']} Meridian)"
            
            elif location_type == 'township':
                township_str = request.POST.get('township', '').strip()
                range_str = request.POST.get('range', '').strip()
                
                if not township_str or not range_str:
                    raise ValueError("Please enter both Township and Range numbers")
                
                try:
                    township = int(township_str)
                    range_val = int(range_str)
                except ValueError:
                    raise ValueError("Township and Range must be numbers")
                
                meridian = request.POST.get('meridian', '4th')
                
                latitude, longitude = get_coordinates_from_township(township, range_val, meridian)
                
                # Find nearest station
                station_result = find_nearest_alberta_station(latitude, longitude)
                if station_result:
                    station_name = station_result['name']
                    location_desc = f"Township {township}, Range {range_val}, {meridian} Meridian (Nearest: {station_name})"
                else:
                    raise ValueError("No weather station found near this location")
            
            else:  # coordinates
                lat_str = request.POST.get('latitude', '').strip()
                lon_str = request.POST.get('longitude', '').strip()
                
                if not lat_str or not lon_str:
                    raise ValueError("Please enter both latitude and longitude")
                
                try:
                    latitude = float(lat_str)
                    longitude = float(lon_str)
                except ValueError:
                    raise ValueError("Latitude and longitude must be valid numbers")
                
                if not (49 <= latitude <= 60):
                    raise ValueError("Latitude must be between 49 and 60 for Alberta")
                if not (-120 <= longitude <= -110):
                    raise ValueError("Longitude must be between -120 and -110 for Alberta")
                
                # Find nearest station
                station_result = find_nearest_alberta_station(latitude, longitude)
                if station_result:
                    station_name = station_result['name']
                    location_desc = f"{latitude}°N, {abs(longitude)}°W (Nearest: {station_name})"
                else:
                    raise ValueError("No weather station found near this location")
            
            print(f"\n{'='*60}")
            print(f"FETCHING DATA FROM ALBERTA ACIS")
            print(f"{'='*60}")
            print(f"Location: {location_desc}")
            print(f"Station: {station_name}")
            print(f"Date range: {start_date} to {end_date}")
            print(f"{'='*60}\n")
            
            # Set scraping flag
            scraping = True
            
            # Fetch data using web scraper
            try:
                df = fetch_alberta_acis_data(station_name, start_date, end_date)
                
                # Validate data
                if df is None or len(df) == 0:
                    raise ValueError("No data returned from Alberta ACIS")
                
                # Check if we have ET data
                has_et = 'ET_ACIS' in df.columns and df['ET_ACIS'].notna().sum() > 0
                
                if not has_et:
                    print("⚠ Warning: Reference ET not found in data")
                    print(f"Available columns: {df.columns.tolist()}")
                
                # Ensure we have required columns
                required_cols = ['Date', 'Tmax', 'Tmin']
                missing_cols = [col for col in required_cols if col not in df.columns]
                
                if missing_cols:
                    raise ValueError(f"Missing required columns: {missing_cols}")
                
                # Calculate derived variables
                if 'Tavg' not in df.columns:
                    df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2
                
                # Add day of year for radiation calculations
                df['day_of_year'] = df['Date'].dt.dayofyear
                
                # Calculate extraterrestrial radiation
                df['Ra'] = df.apply(
                    lambda row: calculate_extraterrestrial_radiation(latitude, row['day_of_year']), 
                    axis=1
                )
                
                # Estimate solar radiation if not provided
                if 'Solar_Radiation' not in df.columns:
                    temp_range = (df['Tmax'] - df['Tmin']).clip(lower=1)
                    kRs = 0.16
                    df['Solar_Radiation'] = kRs * np.sqrt(temp_range) * df['Ra']
                    df['Solar_Radiation'] = df['Solar_Radiation'].clip(3, 40)
                
                df['Rs'] = df['Solar_Radiation']
                
                # Add defaults for RH and wind if not present
                if 'RH' not in df.columns:
                    df['RH'] = 65.0
                if 'Wind_Speed' not in df.columns:
                    df['Wind_Speed'] = 2.0
                
                df['u2'] = df['Wind_Speed']
                
                # Clean up
                df = df.drop('day_of_year', axis=1, errors='ignore')
                
                print(f"\n✓ Successfully fetched {len(df)} days of data")
                print(f"  Temperature range: {df['Tmax'].min():.1f}°C to {df['Tmax'].max():.1f}°C")
                
                if has_et:
                    et_valid = df['ET_ACIS'].notna().sum()
                    print(f"  Reference ET: {et_valid} valid values")
                    print(f"  ET range: {df['ET_ACIS'].min():.2f} to {df['ET_ACIS'].max():.2f} mm/day")
                
                # Store in session
                request.session['acis_data'] = df.to_json(date_format='iso')
                request.session['acis_location'] = {
                    'latitude': latitude,
                    'longitude': longitude,
                    'start_date': start_date,
                    'end_date': end_date,
                    'description': location_desc,
                    'station': station_name
                }
                
                # Preview
                df_preview = df.head(10).to_dict('records')
                success_message = f"✓ Successfully fetched {len(df)} days of weather data from Alberta ACIS!"
                
                if has_et:
                    success_message += f" Including {et_valid} days of Reference ET values."
                
            except Exception as scrape_error:
                print(f"\n✗ Web scraping failed: {scrape_error}")
                print("\nFalling back to manual CSV upload...")
                
                error_message = (
                    f"Automatic data fetch failed: {str(scrape_error)}\n\n"
                    f"Please manually download data from Alberta ACIS:\n"
                    f"1. Go to https://acis.alberta.ca/acis/weather-data-viewer.jsp\n"
                    f"2. Select station: {station_name}\n"
                    f"3. Date range: {start_date} to {end_date}\n"
                    f"4. Ensure 'Reference ET' is selected\n"
                    f"5. Download CSV and upload it below"
                )
                
        except ValueError as e:
            error_message = str(e)
            print(f"ValueError: {e}")
        except Exception as e:
            error_message = f"Unexpected error: {str(e)}"
            print(f"Full error: {e}")
            import traceback
            traceback.print_exc()
    
    context = {
        'error_message': error_message,
        'success_message': success_message,
        'df_preview': df_preview,
        'location_result': location_result,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
        'popular_locations': list(ALBERTA_LOCATIONS.keys())[:20],
        'scraping': scraping
    }
    
    return render(request, 'et/acis_fetch.html', context)


def find_nearest_alberta_station(latitude, longitude):
    """
    Find nearest Alberta ACIS weather station to given coordinates
    """
    
    # Alberta ACIS weather stations with coordinates
    ALBERTA_STATIONS_COORDS = {
        'Lethbridge': {'lat': 49.6942, 'lon': -112.8328},
        'Calgary': {'lat': 51.1139, 'lon': -114.0203},
        'Edmonton': {'lat': 53.3097, 'lon': -113.5800},
        'Red Deer': {'lat': 52.1822, 'lon': -113.8939},
        'Medicine Hat': {'lat': 50.0189, 'lon': -110.7208},
        'Medicine Lake Auto': {'lat': 54.0181, 'lon': -112.9767},
        'Brooks': {'lat': 50.5644, 'lon': -111.8986},
        'Vauxhall': {'lat': 50.0500, 'lon': -112.1333},
        'Taber': {'lat': 49.7833, 'lon': -112.1500},
        'Grande Prairie': {'lat': 55.1796, 'lon': -118.8850},
        'Fort McMurray': {'lat': 56.6532, 'lon': -111.2217},
    }
    
    from math import radians, cos, sin, asin, sqrt
    
    def haversine(lon1, lat1, lon2, lat2):
        """Calculate distance between two points on Earth (km)"""
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        km = 6371 * c
        return km
    
    nearest = None
    min_distance = float('inf')
    
    for station_name, coords in ALBERTA_STATIONS_COORDS.items():
        distance = haversine(longitude, latitude, coords['lon'], coords['lat'])
        
        if distance < min_distance:
            min_distance = distance
            nearest = {
                'name': station_name,
                'latitude': coords['lat'],
                'longitude': coords['lon'],
                'distance': distance
            }
    
    return nearest

def get_coordinates_from_township(township, range_val, meridian='4th'):
    """
    Convert Alberta township/range to coordinates
    
    Parameters:
    - township: Township number (e.g., 49)
    - range_val: Range number (e.g., 25)
    - meridian: Meridian (default '4th' for Lethbridge area)
    
    Returns:
    - (latitude, longitude) tuple
    """
    
    # Simplified conversion for Alberta
    # This is approximate - for production, use proper township/range conversion
    
    # Base coordinates (near Lethbridge)
    if meridian == '4th':
        base_lat = 49.0
        base_lon = -110.0
    elif meridian == '5th':
        base_lat = 49.0
        base_lon = -114.0
    elif meridian == '6th':
        base_lat = 49.0
        base_lon = -118.0
    else:
        base_lat = 49.0
        base_lon = -110.0
    
    # Each township is approximately 6 miles (9.66 km) north
    # Each range is approximately 6 miles (9.66 km) west
    lat_offset = (township - 1) * 0.087  # Approximate degrees per township
    lon_offset = (range_val - 1) * 0.087  # Approximate degrees per range
    
    latitude = base_lat + lat_offset
    longitude = base_lon - lon_offset
    
    return (latitude, longitude)


    if location_type == 'place':
        place_name = request.POST.get('place_name', '').strip()
    
    if not place_name:
        raise ValueError("Please enter a location name")
    
    print(f"Searching for: {place_name}")
    location_result = search_alberta_location(place_name)
    
    if not location_result:
        raise ValueError(f"Location '{place_name}' not found")
    
    # NEW: Check if this is a direct station name
    if location_result.get('is_direct_station'):
        # User typed a station name directly
        latitude = location_result['latitude']
        longitude = location_result['longitude']
        station_name = place_name  # Use the exact name they typed
        location_desc = f"{place_name} (Station)"
    else:
        # Found in our database
        latitude = location_result['latitude']
        longitude = location_result['longitude']
        station_name = location_result['place_name']
        location_desc = location_result['place_name']
        
        if location_result.get('township'):
            location_desc += f" (Twp {location_result['township']}, Rge {location_result['range']}, {location_result['meridian']} Meridian)"

def comparison_with_acis(request):
    """
    Enhanced ET calculator with ACIS data - INCLUDING ACIS ET VALUES FOR COMPARISON
    """
    et_data = None
    et_stats = None
    comparison_stats = None
    growing_season_stats = None
    plot_url = None
    growing_season_plots = None
    
    # Get ACIS data from session
    acis_data_json = request.session.get('acis_data')
    location_info = request.session.get('acis_location', {})
    
    if not acis_data_json:
        return redirect('et:acis_fetch')
    
    # Convert back to DataFrame
    df = pd.read_json(io.StringIO(acis_data_json))
    df['Date'] = pd.to_datetime(df['Date'])
    
    # Get selected unit
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)
    forecast_data = get_lethbridge_forecast()
    
    # NEW: Fetch Environment Canada forecast
    env_canada_forecast = None
    ec_city_name = None
    try:
        from .environment_canada_scraper import fetch_env_canada_forecast
        
        # Extract city name from location description
        location_desc = location_info.get('description', '')
        city_name = location_desc.split('(')[0].strip().split(',')[0]
        
        # Common city name mappings
        city_mapping = {
            'Twp': 'Calgary',
            'Township': 'Calgary',
        }
        
        # Use mapped name if needed
        for key, value in city_mapping.items():
            if key in city_name:
                city_name = value
                break
        
        # Fetch forecast
        ec_df = fetch_env_canada_forecast(city_name, days=5)
        env_canada_forecast = ec_df.to_dict('records')
        ec_city_name = city_name
        
        print(f"✓ Environment Canada forecast fetched for {city_name}")
        
    except Exception as e:
        print(f"Could not fetch Environment Canada forecast: {e}")
    
    try:
        # Add day of year column for radiation calculations
        df['day_of_year'] = df['Date'].dt.dayofyear
        
        # Get latitude from session
        latitude = location_info.get('latitude', 49.7)
        
        # Ensure we have temperature columns
        if 'Tmax' not in df.columns or 'Tmin' not in df.columns:
            raise ValueError("Missing temperature data")
        
        # Calculate Tavg if not present
        if 'Tavg' not in df.columns:
            df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2
        
        # Assign solar radiation
        if 'Solar_Radiation' in df.columns:
            df['Rs'] = df['Solar_Radiation']
        elif 'Rs' not in df.columns:
            # Estimate if not available
            df['Rs'] = (df['Tmax'] - df['Tmin']) * 0.16 * np.sqrt(12)
        
        # Assign wind speed
        if 'Wind_Speed' in df.columns:
            df['u2'] = df['Wind_Speed']
        elif 'u2' not in df.columns:
            df['u2'] = 2.0  # Default
        
        # Assign relative humidity
        if 'RH' not in df.columns:
            df['RH'] = 65.0  # Default
        
        # Calculate extraterrestrial radiation
        df['Ra'] = df.apply(
            lambda row: calculate_extraterrestrial_radiation(latitude, row['day_of_year']), 
            axis=1
        )
        
        # Calculate ET using all four methods (only if not already present)
        if 'ET_PT' not in df.columns:
            try:
                df['ET_PT'] = df.apply(
                    lambda row: priestley_taylor_ET(row['Tavg'], row['Rs']), 
                    axis=1
                )
            except Exception as e:
                print(f"PT calculation failed: {e}")
                df['ET_PT'] = np.nan
        
        if 'ET_PM' not in df.columns:
            try:
                df['ET_PM'] = df.apply(
                    lambda row: penman_monteith_ET(
                        row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs']
                    ), 
                    axis=1
                )
            except Exception as e:
                print(f"PM calculation failed: {e}")
                df['ET_PM'] = np.nan
        
        if 'ET_Maule' not in df.columns:
            try:
                df['ET_Maule'] = df.apply(
                    lambda row: maule_ET(
                        row['Tmax'], 
                        row['Tmin'], 
                        row['Rs'], 
                        row['RH'] if not pd.isna(row['RH']) else None
                    ), 
                    axis=1
                )
            except Exception as e:
                print(f"Maule calculation failed: {e}")
                df['ET_Maule'] = np.nan
        
        if 'ET_Hargreaves' not in df.columns:
            try:
                df['ET_Hargreaves'] = df.apply(
                    lambda row: hargreaves_ET(row['Tmax'], row['Tmin'], row['Ra']), 
                    axis=1
                )
            except Exception as e:
                print(f"Hargreaves calculation failed: {e}")
                df['ET_Hargreaves'] = np.nan
        
        # KEEP ACIS ET if it exists
        has_acis_et = 'ET_ACIS' in df.columns and df['ET_ACIS'].notna().sum() > 0
        
        # Remove rows with ALL NaN ET values (but keep ACIS ET)
        et_columns = ['ET_PT', 'ET_PM', 'ET_Maule', 'ET_Hargreaves']
        if has_acis_et:
            et_columns.append('ET_ACIS')
        
        df = df.dropna(subset=et_columns, how='all')
        
        if len(df) == 0:
            raise ValueError("No valid ET values could be calculated for any method")
        
        # Compute rolling averages for smoothing
        for method in ['PT', 'PM', 'Maule', 'Hargreaves']:
            col = f'ET_{method}'
            if col in df.columns:
                df[f'{col}_smooth'] = df[col].rolling(window=5, min_periods=1).mean()
        
        # Also smooth ACIS ET if available
        if has_acis_et:
            df['ET_ACIS_smooth'] = df['ET_ACIS'].rolling(window=5, min_periods=1).mean()
        
        # Calculate statistics for all methods with unit conversion
        et_stats = {}
        method_names = {
            'PT': 'Priestley-Taylor',
            'PM': 'Penman-Monteith', 
            'Maule': 'Maulé',  
            'Hargreaves': 'Hargreaves-Samani',
            'ACIS': 'ACIS Reference'
        }
        
        for method, name in method_names.items():
            col = f'ET_{method}'
            if col in df.columns and not df[col].isna().all():
                # Convert values if needed
                if selected_unit == 'inches':
                    avg_val = convert_units(df[col].mean(), 'mm', 'inches')
                    max_val = convert_units(df[col].max(), 'mm', 'inches')
                    min_val = convert_units(df[col].min(), 'mm', 'inches')
                    std_val = convert_units(df[col].std(), 'mm', 'inches')
                else:
                    avg_val = df[col].mean()
                    max_val = df[col].max()
                    min_val = df[col].min()
                    std_val = df[col].std()
                
                et_stats[method] = {
                    'name': name,
                    'avg': avg_val,
                    'max': max_val,
                    'min': min_val,
                    'std': std_val
                }
        
        # Enhanced comparison statistics
        comparison_stats = {}
        available_methods = [
            method for method in ['PT', 'PM', 'Maule', 'Hargreaves', 'ACIS'] 
            if f'ET_{method}' in df.columns and not df[f'ET_{method}'].isna().all()
        ]
        
        if len(available_methods) >= 2:
            # Calculate correlations between methods
            et_cols = [f'ET_{method}' for method in available_methods]
            corr_matrix = df[et_cols].corr()
            
            # Store correlation data
            comparison_stats['correlations'] = {}
            for i, method1 in enumerate(available_methods):
                for j, method2 in enumerate(available_methods):
                    if i < j:  # Only store upper triangle
                        key = f'{method1} vs {method2}'
                        comparison_stats['correlations'][key] = corr_matrix.iloc[i, j]
            
            # Calculate mean differences from ACIS if available, otherwise from PM
            reference_method = 'ACIS' if 'ACIS' in available_methods else (
                'PM' if 'PM' in available_methods else None
            )
            
            if reference_method:
                for method in available_methods:
                    if method != reference_method:
                        diff_mm = (df[f'ET_{method}'] - df[f'ET_{reference_method}']).mean()
                        if selected_unit == 'inches':
                            comparison_stats[f'{method}_{reference_method}_diff'] = convert_units(
                                diff_mm, 'mm', 'inches'
                            )
                        else:
                            comparison_stats[f'{method}_{reference_method}_diff'] = diff_mm
        
        # Calculate growing season statistics for primary method
        if 'ET_PM' in df.columns and not df['ET_PM'].isna().all():
            growing_season_stats = calculate_growing_season_stats(df, 'ET_PM', selected_unit)
            growing_season_plots = create_growing_season_plots(df, 'ET_PM', selected_unit)
        elif available_methods:
            primary_method = available_methods[0]
            growing_season_stats = calculate_growing_season_stats(
                df, f'ET_{primary_method}', selected_unit
            )
            growing_season_plots = create_growing_season_plots(
                df, f'ET_{primary_method}', selected_unit
            )
        
        # Create enhanced comparison plot
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        fig.patch.set_facecolor('white')
        
        # Main ET comparison plot
        ax1.set_facecolor('#f8fffe')
        colors = {
            'PT': '#86A873',
            'PM': '#087F8C', 
            'Maule': '#BB9F06',
            'Hargreaves': '#5AAA95',
            'ACIS': '#FF6B6B'
        }
        
        for method in available_methods:
            col = f'ET_{method}'
            smooth_col = f'{col}_smooth'
            
            # Convert data for plotting if needed
            if selected_unit == 'inches':
                plot_data = df[col].apply(
                    lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x
                )
                if smooth_col in df.columns:
                    plot_data_smooth = df[smooth_col].apply(
                        lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x
                    )
                else:
                    plot_data_smooth = plot_data
            else:
                plot_data = df[col]
                plot_data_smooth = df[smooth_col] if smooth_col in df.columns else plot_data
            
            # Use thicker line and different style for ACIS
            if method == 'ACIS':
                ax1.plot(
                    df['Date'], plot_data_smooth, 
                    label=f'{method_names[method]}', 
                    color=colors[method], linewidth=3.5, alpha=1.0, linestyle='--'
                )
            else:
                ax1.plot(
                    df['Date'], plot_data, 
                    label=f'{method_names[method]}', 
                    color=colors[method], alpha=0.6, linewidth=1.5
                )
                ax1.plot(
                    df['Date'], plot_data_smooth, 
                    color=colors[method], linewidth=2.5, alpha=0.9
                )
        
        # Add location info to title
        location_desc = location_info.get('description', 'ACIS Data')
        ax1.set_title(
            f'Evapotranspiration Method Comparison - {location_desc}', 
            fontsize=16, fontweight='bold', color='#095256', pad=20
        )
        ax1.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
        ax1.set_ylabel(
            f'ET₀ ({unit_info["daily_label"]})', 
            fontsize=12, fontweight='600', color='#095256'
        )
        ax1.grid(True, alpha=0.3, color='#5AAA95')
        ax1.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=10)
        ax1.tick_params(axis='x', rotation=45)
        
        # Method differences plot
        ax2.set_facecolor('#f8fffe')
        reference_method = 'ACIS' if 'ACIS' in available_methods else 'PM'
        
        if reference_method in available_methods and len(available_methods) > 1:
            for method in available_methods:
                if method != reference_method:
                    col = f'ET_{method}'
                    diff = df[f'ET_{reference_method}'] - df[col]
                    if selected_unit == 'inches':
                        diff = diff.apply(
                            lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x
                        )
                    
                    ax2.plot(
                        df['Date'], diff, color=colors[method], linewidth=2, alpha=0.7, 
                        label=f'{method_names[reference_method]} - {method_names[method]}'
                    )
            
            ax2.axhline(y=0, color='#095256', linestyle='--', alpha=0.8)
            ref_name = 'ACIS Reference' if reference_method == 'ACIS' else 'Penman-Monteith'
            ax2.set_title(
                f'Differences from {ref_name} (Reference Method)', 
                fontsize=14, fontweight='bold', color='#095256'
            )
            ax2.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
            ax2.set_ylabel(
                f'Difference ({unit_info["daily_label"]})', 
                fontsize=12, fontweight='600', color='#095256'
            )
            ax2.grid(True, alpha=0.3, color='#5AAA95')
            ax2.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=9)
            ax2.tick_params(axis='x', rotation=45)
        else:
            ax2.text(
                0.5, 0.5, 'Difference plot requires a reference method', 
                ha='center', va='center', fontsize=14, color='#666'
            )
            ax2.set_xlim(0, 1)
            ax2.set_ylim(0, 1)
            ax2.axis('off')
        
        plt.tight_layout()
        
        # Convert plot to base64
        buf = BytesIO()
        plt.savefig(
            buf, format='png', dpi=150, bbox_inches='tight',
            facecolor='white', edgecolor='none'
        )
        buf.seek(0)
        plot_url = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()
        plt.close()
        
        # Store enhanced CSV data in session
        csv_columns = ['Date'] + [f'ET_{method}' for method in available_methods]
        request.session['et_data_csv'] = df[csv_columns].to_csv(index=False)
        
        # Store the ET data for plot updates
        request.session['acis_data'] = df[csv_columns].to_json(date_format='iso')
        
        # Prepare data for rendering with unit conversion
        et_data = []
        for _, row in df.iterrows():
            data_row = {'Date': row['Date']}
            
            for method in available_methods:
                col = f'ET_{method}'
                val = row[col] if not pd.isna(row[col]) else 0
                if selected_unit == 'inches' and val:
                    val = convert_units(val, 'mm', 'inches')
                data_row[f'ET_{method}'] = round(val, unit_info['decimal_places']) if val else 0
            
            et_data.append(data_row)
    
    except Exception as e:
        print(f"Calculation error: {e}")
        import traceback
        traceback.print_exc()
        return render(request, 'et/comparison.html', {
            'error_message': f"Error calculating ET: {str(e)}",
            'selected_unit': selected_unit,
            'unit_info': unit_info,
        })
    
    context = {
        'et_data': et_data,
        'et_stats': et_stats,
        'comparison_stats': comparison_stats,
        'growing_season_stats': growing_season_stats,
        'plot_url': plot_url,
        'growing_season_plots': growing_season_plots,
        'forecast_data': forecast_data,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
        'acis_location': location_info,
        'has_acis_et': has_acis_et,
        'available_methods': available_methods,
        'env_canada_forecast': env_canada_forecast,
        'ec_city_name': ec_city_name,
    }
    
    return render(request, 'et/comparison.html', context)
# Add this new view to your views.py

def update_comparison_plot(request):
    """
    API endpoint to generate plot with only selected methods
    Called when user toggles checkboxes
    """
    try:
        # Get selected methods from request
        selected_methods = request.GET.getlist('methods')
        selected_unit = request.GET.get('unit', 'mm')
        
        if not selected_methods:
            return JsonResponse({'error': 'No methods selected'}, status=400)
        
        unit_info = get_unit_info(selected_unit)
        
        # Get data from session - CRITICAL: Use the correct session key
        acis_data_json = request.session.get('acis_data')
        et_data_csv = request.session.get('et_data_csv')
        
        if not acis_data_json and not et_data_csv:
            return JsonResponse({'error': 'No data in session'}, status=400)
        
        # Load data
        if acis_data_json:
            df = pd.read_json(io.StringIO(acis_data_json))
        elif et_data_csv:
            df = pd.read_csv(io.StringIO(et_data_csv))
        else:
            return JsonResponse({'error': 'No data available'}, status=400)
        
        df['Date'] = pd.to_datetime(df['Date'])
        
        # CRITICAL FIX: Check what columns actually exist in the dataframe
        print(f"DEBUG: DataFrame columns: {df.columns.tolist()}")
        print(f"DEBUG: Requested methods: {selected_methods}")
        
        # Filter to only include selected methods that exist
        available_et_cols = []
        available_methods = []
        for method in selected_methods:
            col_name = f'ET_{method}'
            if col_name in df.columns:
                available_et_cols.append(col_name)
                available_methods.append(method)
        
        print(f"DEBUG: Available ET columns: {available_et_cols}")
        print(f"DEBUG: Available methods: {available_methods}")
        
        if not available_et_cols:
            # Return detailed error about what's missing
            existing_cols = [col for col in df.columns if col.startswith('ET_')]
            return JsonResponse({
                'error': 'Selected methods not found in data',
                'requested': selected_methods,
                'available_columns': existing_cols,
                'all_columns': df.columns.tolist()
            }, status=400)
        
        # Create plot with selected methods
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        fig.patch.set_facecolor('white')
        
        # Main ET comparison plot
        ax1.set_facecolor('#f8fffe')
        
        colors = {
            'PT': '#86A873',
            'PM': '#087F8C', 
            'Maule': '#BB9F06',
            'Hargreaves': '#5AAA95',
            'ACIS': '#FF6B6B'
        }
        
        method_names = {
            'PT': 'Priestley-Taylor',
            'PM': 'Penman-Monteith', 
            'Maule': 'Maulé',  
            'Hargreaves': 'Hargreaves-Samani',
            'ACIS': 'ACIS Reference'
        }
        
        # Plot each selected method
        for method in available_methods:
            col = f'ET_{method}'
            if col in df.columns:
                # Apply unit conversion if needed
                values = df[col].copy()
                if selected_unit == 'inches':
                    values = values.apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                
                # Plot with appropriate style
                if method == 'ACIS':
                    ax1.plot(df['Date'], values, color=colors[method], linewidth=3, 
                            linestyle='--', alpha=0.9, label=method_names[method], zorder=10)
                else:
                    ax1.plot(df['Date'], values, color=colors[method], linewidth=2, 
                            alpha=0.7, label=method_names[method])
        
        ax1.set_title('ET Method Comparison', fontsize=16, fontweight='bold', color='#095256')
        ax1.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
        ax1.set_ylabel(f'ET ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
        ax1.grid(True, alpha=0.3, color='#5AAA95')
        ax1.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=10)
        ax1.tick_params(axis='x', rotation=45)
        
        # Difference plot
        if len(available_methods) > 1:
            # Use ACIS as reference if available, otherwise PM
            reference_method = 'ACIS' if 'ACIS' in available_methods else ('PM' if 'PM' in available_methods else available_methods[0])
            
            for method in available_methods:
                if method != reference_method:
                    col = f'ET_{method}'
                    if col in df.columns and f'ET_{reference_method}' in df.columns:
                        diff = df[f'ET_{reference_method}'] - df[col]
                        if selected_unit == 'inches':
                            diff = diff.apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                        
                        ax2.plot(df['Date'], diff, color=colors.get(method, '#666'), linewidth=2, alpha=0.7, 
                                label=f'{method_names[reference_method]} - {method_names[method]}')
            
            ax2.axhline(y=0, color='#095256', linestyle='--', alpha=0.8)
            ax2.set_title(f'Differences from {method_names[reference_method]} (Reference)', 
                         fontsize=14, fontweight='bold', color='#095256')
            ax2.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
            ax2.set_ylabel(f'Difference ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
            ax2.grid(True, alpha=0.3, color='#5AAA95')
            ax2.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=9)
            ax2.tick_params(axis='x', rotation=45)
        else:
            ax2.text(0.5, 0.5, 'Select multiple methods to see differences', 
                    ha='center', va='center', fontsize=14, color='#666')
            ax2.set_xlim(0, 1)
            ax2.set_ylim(0, 1)
            ax2.axis('off')
        
        plt.tight_layout()
        
        # Convert plot to base64
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                   facecolor='white', edgecolor='none')
        buf.seek(0)
        plot_url = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()
        plt.close()
        
        return JsonResponse({
            'plot_url': plot_url,
            'selected_methods': selected_methods
        })
        
    except Exception as e:
        print(f"Error updating plot: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)
    

def combine_day_night_forecasts(df_forecast):
    """Combine daytime and nighttime forecasts into single daily forecasts"""
    # Handle None or empty input
    if df_forecast is None or len(df_forecast) == 0:
        return []
    
    combined = []
    i = 0
    
    while i < len(df_forecast):
        current = df_forecast[i]
        
        # Skip alerts and current conditions
        period_lower = current['Period'].lower()
        if 'watch' in period_lower or 'warning' in period_lower or 'current conditions' in period_lower:
            i += 1
            continue
        
        # Check if this is a daytime forecast (doesn't contain "night")
        if 'night' not in period_lower:
            # Look ahead for the corresponding night forecast
            if i + 1 < len(df_forecast):
                next_forecast = df_forecast[i + 1]
                
                # Check if next entry is the night forecast for the same day
                if 'night' in next_forecast['Period'].lower():
                    # Extract day name from period (e.g., "Thursday: Mainly sunny. High 9." -> "Thursday")
                    day_name = current['Period'].split(':')[0] if ':' in current['Period'] else current['Period'].split('.')[0]
                    
                    # Combine day and night descriptions
                    day_desc = current['Forecast'].split('Forecast issued')[0].strip()
                    night_desc = next_forecast['Forecast'].split('Forecast issued')[0].strip()
                    
                    # Combine them
                    combined.append({
                        'Date': current['Date'],
                        'Period': day_name,  # Just the day name (e.g., "Thursday")
                        'Temp_High': current['Temp_High'],
                        'Temp_Low': next_forecast['Temp_Low'],
                        'Precipitation_mm': current['Precipitation_mm'] + next_forecast['Precipitation_mm'],
                        'Forecast': f"<strong>Day:</strong> {day_desc}<br><br><strong>Night:</strong> {night_desc}"
                    })
                    i += 2  # Skip both day and night entries
                else:
                    # Next entry is not night, just add current day
                    combined.append(current)
                    i += 1
            else:
                # No more entries, just add current
                combined.append(current)
                i += 1
        else:
            # This is a standalone night forecast (shouldn't happen normally, but handle it)
            combined.append(current)
            i += 1
    
    return combined

def env_canada_forecast_view(request):
    """
    Standalone view to display Environment Canada precipitation forecast
    """
    from .environment_canada_scraper import fetch_env_canada_forecast, EnvironmentCanadaScraper
    import pandas as pd
    import numpy as np
    import math
    
    error_message = None
    df_forecast = None
    city_name = 'Calgary'  # Default
    selected_days = 5  # Default

    # Get available cities
    scraper = EnvironmentCanadaScraper()
    all_cities = sorted(scraper.LOCATION_CODES.keys())
    
    # Organize cities by region
    cities_by_region = {
        'Major Cities': ['Calgary', 'Edmonton', 'Lethbridge', 'Red Deer', 'Medicine Hat', 
                        'Grande Prairie', 'Fort McMurray'],
        'Central Alberta': ['Airdrie', 'St. Albert', 'Spruce Grove', 'Camrose', 'Okotoks', 
                           'Cochrane', 'Strathmore', 'Leduc', 'Stony Plain', 'Beaumont',
                           'Fort Saskatchewan', 'Wetaskiwin', 'Sylvan Lake', 'Drumheller',
                           'Olds', 'Innisfail', 'Ponoka', 'Lacombe', 'Rimbey', 
                           'Rocky Mountain House'],
        'Southern Alberta': ['Brooks', 'Taber', 'Vauxhall', 'Coaldale', 'Picture Butte',
                            'Vulcan', 'Claresholm', 'Pincher Creek', 'Cardston',
                            'Fort Macleod', 'Blairmore', 'Crowsnest Pass', 'High River'],
        'Northern Alberta': ['Peace River', 'Slave Lake', 'Whitecourt', 'Hinton', 
                            'High Level', 'Fort Chipewyan', 'Rainbow Lake', 'Athabasca',
                            'Barrhead', 'Westlock', 'Mayerthorpe'],
        'Eastern Alberta': ['Lloydminster', 'Cold Lake', 'Vegreville', 'Vermilion',
                           'Wainwright', 'Provost', 'Coronation', 'Hanna', 'Oyen',
                           'Bonnyville', 'St. Paul', 'Lac La Biche'],
        'Mountain Towns': ['Banff', 'Jasper', 'Canmore'],
        'West-Central Alberta': ['Edson', 'Drayton Valley'],
    }
    
    # Filter to only include cities that exist in our codes
    cities_by_region = {
        region: [city for city in cities if city in scraper.LOCATION_CODES]
        for region, cities in cities_by_region.items()
    }
    
    if request.method == 'POST':
        city_name = request.POST.get('city_name', 'Calgary').strip()
        selected_days = int(request.POST.get('days', 5))
        
        try:
            df = fetch_env_canada_forecast(city_name, selected_days)
            
            # DEBUG: Print what we got from scraper
            print(f"\n{'='*80}")
            print(f"Raw DataFrame from scraper for {city_name}")
            print(f"{'='*80}")
            for idx, row in df.iterrows():
                print(f"Row {idx}: Period={row['Period']}, High={row['Temp_High']}, Low={row['Temp_Low']}, Type High={type(row['Temp_High'])}, Type Low={type(row['Temp_Low'])}")
            print(f"{'='*80}\n")
            
            # Helper function to safely check and convert temperature values
            def safe_temp_convert(value):
                """Convert temperature value to float or None"""
                # Check if value is None
                if value is None:
                    return None
                
                # Check if it's a pandas NA
                if pd.isna(value):
                    return None
                
                # Check if it's a numpy nan
                if isinstance(value, float):
                    if math.isnan(value):
                        return None
                    else:
                        return float(value)
                
                # Try to convert to float
                try:
                    temp_float = float(value)
                    if math.isnan(temp_float):
                        return None
                    return temp_float
                except (ValueError, TypeError):
                    return None
            
            # Convert to records for template
            df_forecast = []
            for idx, row in df.iterrows():
                temp_high = safe_temp_convert(row['Temp_High'])
                temp_low = safe_temp_convert(row['Temp_Low'])
                precip = safe_temp_convert(row['Precipitation_mm'])
                
                forecast_dict = {
                    'Date': row['Date'],
                    'Period': row['Period'],
                    'Temp_High': temp_high,
                    'Temp_Low': temp_low,
                    'Precipitation_mm': precip if precip is not None else 0.0,
                    'Forecast': str(row['Forecast']) if row['Forecast'] else ''
                }
                
                # DEBUG: Print converted values
                print(f"Converted Row {idx}: High={forecast_dict['Temp_High']}, Low={forecast_dict['Temp_Low']}")
                
                df_forecast.append(forecast_dict)
            
            # Calculate total precipitation
            total_precip = sum([f['Precipitation_mm'] for f in df_forecast])
            
            print(f"\n✓ Created {len(df_forecast)} forecast records")
            print(f"✓ Total precipitation: {total_precip:.1f} mm\n")
            if df_forecast:
                df_forecast_combined = combine_day_night_forecasts(df_forecast)
            else:
                df_forecast_combined = []
            context = {
                'city_name': city_name,
                'selected_days': selected_days,
                'df_forecast': df_forecast,
                'total_precip': total_precip,
                'available_cities': all_cities,
                'cities_by_region': cities_by_region,
            }
            
            return render(request, 'et/env_canada_forecast.html', context)
            
        except Exception as e:
            import traceback
            print(f"\n{'='*80}")
            print(f"ERROR in env_canada_forecast_view:")
            print(traceback.format_exc())
            print(f"{'='*80}\n")
            error_message = f"Error fetching forecast: {str(e)}"

    context = {
        'error_message': error_message,
        'city_name': city_name,
        'selected_days': selected_days,
        'available_cities': all_cities,
        'cities_by_region': cities_by_region,
    }
    
    return render(request, 'et/env_canada_forecast.html', context)

def aquacrop_simulation(request):
    """
    View for AquaCrop crop growth simulation
    """
    
    context = {
        'crops': list(AquaCropSimulator.AVAILABLE_CROPS.keys()),
        'soil_types': list(AquaCropSimulator.SOIL_TYPES.keys()),
        'irrigation_methods': [
            ('rainfed', 'Rainfed (No Irrigation)'),
            ('full', 'Full Irrigation (80% SMT)'),
            ('deficit', 'Deficit Irrigation (60% SMT)'),
        ],
    }
    
    if request.method == 'POST':
        try:
            # Get form data
            crop = request.POST.get('crop', 'Wheat')
            soil = request.POST.get('soil', 'Loam')
            irrigation = request.POST.get('irrigation', 'rainfed')
            start_date = request.POST.get('start_date', '2024-05-01')
            end_date = request.POST.get('end_date', '2024-09-01')
            
            # Handle weather data upload
            weather_df = None
            if 'weather_file' in request.FILES:
                file = request.FILES['weather_file']
                
                if file.name.endswith('.csv'):
                    weather_df = pd.read_csv(file)
                elif file.name.endswith(('.xlsx', '.xls')):
                    weather_df = pd.read_excel(file)
                else:
                    context['error_message'] = "Please upload a CSV or Excel file"
                    return render(request, 'et/aquacrop_simulation.html', context)
            
            # Run simulation
            results = run_aquacrop_simulation(
                crop=crop,
                soil=soil,
                start_date=start_date,
                end_date=end_date,
                irrigation=irrigation,
                weather_df=weather_df
            )
            
            # Add results to context
            context.update({
                'results': results,
                'selected_crop': crop,
                'selected_soil': soil,
                'selected_irrigation': irrigation,
                'start_date': start_date,
                'end_date': end_date,
                'has_results': True,
            })
            
        except Exception as e:
            context['error_message'] = f"Simulation error: {str(e)}"
    
    return render(request, 'et/aquacrop_simulation.html', context)


def aquacrop_api(request):
    """
    API endpoint for AquaCrop simulations
    Returns JSON results
    """
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            results = run_aquacrop_simulation(
                crop=data.get('crop', 'Wheat'),
                soil=data.get('soil', 'Loam'),
                start_date=data.get('start_date', '2024/05/01'),
                end_date=data.get('end_date', '2024/09/01'),
                irrigation=data.get('irrigation', 'rainfed'),
            )
            
            # Convert DataFrames to JSON-serializable format
            results_json = {
                'yield_fresh': float(results['yield_fresh']),
                'biomass': float(results['biomass']),
                'total_irrigation': float(results['total_irrigation']),
                'total_et': float(results['total_et']),
                'water_productivity': float(results['water_productivity']),
                'canopy_cover_max': float(results['canopy_cover_max']),
                'growth_chart': results.get('growth_chart'),
                'water_balance_chart': results.get('water_balance_chart'),
            }
            
            return JsonResponse({'success': True, 'results': results_json})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'POST request required'})





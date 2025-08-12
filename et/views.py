import pandas as pd
import io
from django.shortcuts import render
from .forms import UploadFileForm
from math import exp
import matplotlib.pyplot as plt
from io import BytesIO
import base64
from django.http import HttpResponse, JsonResponse
import numpy as np
import requests
import xml.etree.ElementTree as ET
import feedparser
from datetime import datetime, date
import calendar

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

def comparison_calculator(request):
    """Comprehensive ET calculator with growing season analysis and unit toggle"""
    et_data = None
    et_stats = None
    comparison_stats = None
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
            csv_file = request.FILES['file']
            try:
                # [Previous CSV processing code remains the same...]
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

                # Find required columns with flexible matching
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

                # Calculate ET using both methods (always in mm first)
                df['ET_PT'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rs']), axis=1)
                df['ET_PM'] = df.apply(lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs']), axis=1)

                # Remove rows with NaN ET values for primary method
                df = df.dropna(subset=['ET_PT'])
                
                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated")

                # Compute rolling averages for smoothing
                for method in ['PT', 'PM']:
                    df[f'ET_{method}_smooth'] = df[f'ET_{method}'].rolling(window=5, min_periods=1).mean()

                # Calculate statistics for both methods with unit conversion
                et_stats = {}
                comparison_stats = {}
                
                for method, name in [('PT', 'Priestley-Taylor'), ('PM', 'Penman-Monteith')]:
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

                # Comparison statistics
                if 'ET_PT' in df.columns and 'ET_PM' in df.columns:
                    diff_mm = (df['ET_PM'] - df['ET_PT']).mean()
                    if selected_unit == 'inches':
                        comparison_stats['PT_PM_diff'] = convert_units(diff_mm, 'mm', 'inches')
                    else:
                        comparison_stats['PT_PM_diff'] = diff_mm
                    comparison_stats['PT_PM_corr'] = df[['ET_PT', 'ET_PM']].corr().iloc[0, 1]

                # Calculate growing season statistics with unit conversion
                growing_season_stats = calculate_growing_season_stats(df, 'ET_PM', selected_unit)
                
                # Create growing season plots with unit conversion
                growing_season_plots = create_growing_season_plots(df, 'ET_PM', selected_unit)

                # Create comparison plot (existing code with unit conversion)
                fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
                fig.patch.set_facecolor('white')
                
                # Main ET comparison plot
                ax1.set_facecolor('#f8fffe')
                colors = {'PT': '#86A873', 'PM': '#087F8C'}
                
                for method in ['PT', 'PM']:
                    if f'ET_{method}' in df.columns and not df[f'ET_{method}'].isna().all():
                        # Convert data for plotting if needed
                        if selected_unit == 'inches':
                            plot_data = df[f'ET_{method}'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                            plot_data_smooth = df[f'ET_{method}_smooth'].apply(lambda x: convert_units(x, 'mm', 'inches'))
                        else:
                            plot_data = df[f'ET_{method}']
                            plot_data_smooth = df[f'ET_{method}_smooth']
                        
                        ax1.plot(df['Date'], plot_data, 
                                label=f'{et_stats[method]["name"]}', 
                                color=colors[method], alpha=0.6, linewidth=1.5)
                        ax1.plot(df['Date'], plot_data_smooth, 
                                color=colors[method], linewidth=2.5, alpha=0.9)

                ax1.set_title('Evapotranspiration Comparison: Priestley-Taylor vs Penman-Monteith', 
                             fontsize=16, fontweight='bold', color='#095256', pad=20)
                ax1.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                ax1.set_ylabel(f'ET₀ ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                ax1.grid(True, alpha=0.3, color='#5AAA95')
                ax1.legend(frameon=True, fancybox=True, shadow=True, loc='upper left', fontsize=10)
                ax1.tick_params(axis='x', rotation=45)

                # Difference plot
                if 'ET_PT' in df.columns and 'ET_PM' in df.columns:
                    ax2.set_facecolor('#f8fffe')
                    diff = df['ET_PM'] - df['ET_PT']
                    if selected_unit == 'inches':
                        diff = diff.apply(lambda x: convert_units(x, 'mm', 'inches'))
                    
                    ax2.plot(df['Date'], diff, color='#5AAA95', linewidth=2, alpha=0.7)
                    ax2.axhline(y=0, color='#095256', linestyle='--', alpha=0.8)
                    ax2.fill_between(df['Date'], diff, 0, alpha=0.3, color='#5AAA95')
                    
                    ax2.set_title('Difference: Penman-Monteith - Priestley-Taylor', 
                                 fontsize=14, fontweight='bold', color='#095256')
                    ax2.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                    ax2.set_ylabel(f'Difference ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                    ax2.grid(True, alpha=0.3, color='#5AAA95')
                    ax2.tick_params(axis='x', rotation=45)

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
                csv_columns = ['Date', 'ET_PT', 'ET_PM', 'ET_PT_smooth', 'ET_PM_smooth']
                available_columns = ['Date'] + [col for col in csv_columns[1:] if col in df.columns]
                request.session['et_data_csv'] = df[available_columns].to_csv(index=False)

                # Prepare data for rendering with unit conversion
                et_data = []
                for _, row in df.iterrows():
                    data_row = {'Date': row['Date']}
                    for method in ['PT', 'PM']:
                        col = f'ET_{method}'
                        if col in df.columns:
                            val = row[col] if not pd.isna(row[col]) else 0
                            if selected_unit == 'inches' and val:
                                val = convert_units(val, 'mm', 'inches')
                            data_row[f'ET_{method}'] = round(val, unit_info['decimal_places']) if val else 0
                    
                    # Calculate difference for display
                    if 'ET_PT' in data_row and 'ET_PM' in data_row and data_row['ET_PT'] and data_row['ET_PM']:
                        data_row['difference'] = round(data_row['ET_PM'] - data_row['ET_PT'], unit_info['decimal_places'])
                    else:
                        data_row['difference'] = None
                        
                    et_data.append(data_row)

            except Exception as e:
                print(f"File processing error: {e}")
                return render(request, 'et/comparison.html', {
                    'form': form,
                    'error_message': f"Error processing file: {str(e)}. Please check your CSV format."
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
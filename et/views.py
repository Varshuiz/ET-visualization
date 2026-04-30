"""Compatibility facade for split ET view modules.

This module keeps `et.views.*` import paths stable for URL routing and tests
while implementation progressively lives in focused modules.
"""

from .forecast_recommendations import build_historical_confidence, build_irrigation_confidence_plot
from .views_aquacrop import aquacrop_api, aquacrop_simulation
from .views_calculators import (
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
from .views_comparison import comparison_with_acis, update_comparison_plot
from .views_forecast import (
    _pt_daily_et_from_temperature,
    _resolve_city_lat_lon,
    combine_day_night_forecasts,
    env_canada_forecast_view,
    get_lethbridge_forecast,
    get_weather_forecast_api,
)
from .views_ingestion import acis_data_view
from .views_legacy import location_search_api
from .views_pages import about, help_guide, method_comparison_info

__all__ = [
    "index",
    "enhanced_comparison_calculator",
    "priestley_taylor_only",
    "penman_monteith_only",
    "maule_only",
    "hargreaves_only",
    "download_et_csv",
    "download_comparison_csv",
    "download_method_csv",
    "calculate_et_api",
    "get_weather_forecast_api",
    "convert_et_units_api",
    "method_comparison_info",
    "help_guide",
    "about",
    "acis_data_view",
    "comparison_with_acis",
    "location_search_api",
    "update_comparison_plot",
    "env_canada_forecast_view",
    "aquacrop_simulation",
    "aquacrop_api",
    "process_single_method",
    "process_single_method_enhanced",
    "get_lethbridge_forecast",
    "combine_day_night_forecasts",
    "_resolve_city_lat_lon",
    "_pt_daily_et_from_temperature",
    "build_historical_confidence",
    "build_irrigation_confidence_plot",
]
import base64
import calendar
import io
import json
import os
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from io import BytesIO, StringIO
from math import exp

import feedparser
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from .et_growing_season import (
    calculate_growing_season_stats,
    create_growing_season_plots,
    create_multi_method_growing_season_plots,
)
from .et_methods import (
    actual_vapor_pressure,
    calculate_extraterrestrial_radiation,
    calculate_extraterrestrial_radiation_vec,
    delta_svp,
    hargreaves_ET,
    hargreaves_ET_vec,
    maule_ET,
    maule_ET_vec,
    net_radiation_estimate,
    net_radiation_estimate_vec,
    penman_monteith_ET,
    penman_monteith_ET_vec,
    priestley_taylor_ET,
    priestley_taylor_ET_vec,
    psychrometric_constant,
    saturation_vapor_pressure,
)
from .et_units import convert_units, format_et_value, get_unit_info
from .location_services import (
    ALBERTA_LOCATIONS,
    geocode_location,
    get_coordinates_from_township,
    location_search_api,
    reverse_geocode,
    search_alberta_location,
)
from .weather_ingestion import fetch_openmeteo_historical_data, normalize_uploaded_weather_dataframe
from .eccc_weather import add_eccc_rh_to_dataframe
from .eccc_weather import build_aquacrop_weather_from_eccc
from .aquacrop_simulator import AquaCropSimulator, run_aquacrop_simulation
from .aquacrop_aggregation import (
    aggregate_aquacrop_timeseries,
    plot_aquacrop_timeseries,
    compute_yield_tha,
    build_yield_comparison_chart,
    format_yield_table,
)
from .forms import UploadFileForm
from .forecast_recommendations import (
    CROP_GDD_PROFILES,
    SOIL_IRRIGATION_FACTORS,
    build_historical_confidence,
    build_irrigation_confidence_plot,
    calculate_daily_gdd,
    gdd_stage_factor,
    merge_openmeteo_forecast_drivers,
    safe_temp_convert,
)
from .stations import ALBERTA_STATIONS_COORDS, find_nearest_alberta_station



# Add unit toggle support to other views as well
def index(request):
    """Home ET calculator: Priestley–Taylor (user Rn column) plus Penman–Monteith when drivers can be built."""
    et_data = None
    et_stats_pt = None
    et_stats_pm = None
    plot_url = None

    # Get selected unit from request (default to mm)
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'

    unit_info = get_unit_info(selected_unit)

    forecast_data = get_lethbridge_forecast()

    def _stats_for_series(series_mm):
        if selected_unit == 'inches':
            return {
                'avg': convert_units(series_mm.mean(), 'mm', 'inches'),
                'max': convert_units(series_mm.max(), 'mm', 'inches'),
                'min': convert_units(series_mm.min(), 'mm', 'inches'),
                'std': convert_units(series_mm.std(), 'mm', 'inches'),
            }
        return {
            'avg': float(series_mm.mean()),
            'max': float(series_mm.max()),
            'min': float(series_mm.min()),
            'std': float(series_mm.std()),
        }

    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['file']
            try:
                csv_bytes = csv_file.read()
                csv_str = csv_bytes.decode('utf-8', errors='replace')
                df = pd.read_csv(io.StringIO(csv_str))

                df.columns = df.columns.str.strip().str.replace(r"[^\w\s]", "", regex=True).str.replace(" ", "_")

                date_col = [col for col in df.columns if 'date' in col.lower()]
                if date_col:
                    df['Date'] = pd.to_datetime(df[date_col[0]], errors='coerce')
                else:
                    df['Date'] = pd.date_range(start='2024-01-01', periods=len(df), freq='D')

                temp_cols = [
                    col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])
                ]
                tmax_cols = [
                    col for col in df.columns if any(term in col.lower() for term in ['tmax', 'max_temp', 'maximum_temp'])
                ]
                tmin_cols = [
                    col for col in df.columns if any(term in col.lower() for term in ['tmin', 'min_temp', 'minimum_temp'])
                ]
                rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                wind_cols = [col for col in df.columns if any(term in col.lower() for term in ['wind', 'wind_speed', 'ws'])]
                rh_cols = [col for col in df.columns if any(term in col.lower() for term in ['rh', 'humidity', 'relative_humidity'])]

                if not temp_cols or not rad_cols:
                    raise ValueError("Could not find temperature and solar radiation columns")

                df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                df['Rn'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')

                # Priestley–Taylor (unchanged semantics: radiation column is treated as net radiation Rn).
                df['ET'] = priestley_taylor_ET_vec(df['Tavg'], df['Rn'])

                # Penman–Monteith: treat the same radiation column as shortwave Rs (typical station export).
                if tmax_cols and tmin_cols:
                    df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce').clip(lower=0)
                    df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                else:
                    df['Tmax'] = df['Tavg'] + 5.0
                    df['Tmin'] = df['Tavg'] - 5.0
                df['Tmax_clamped'] = df['Tmax'].clip(lower=0)
                df['Rs'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                if wind_cols:
                    df['u2'] = pd.to_numeric(df[wind_cols[0]], errors='coerce').fillna(2.0)
                else:
                    df['u2'] = 2.0
                if rh_cols:
                    df['RH'] = pd.to_numeric(df[rh_cols[0]], errors='coerce').fillna(65.0)
                else:
                    df['RH'] = 65.0
                df['day_of_year'] = df['Date'].dt.dayofyear
                lat_home = 49.7
                df['Ra'] = calculate_extraterrestrial_radiation_vec(lat_home, df['day_of_year'].to_numpy())
                try:
                    df['ET_PM'] = penman_monteith_ET_vec(
                        df['Tmax_clamped'], df['Tmin'], df['RH'], df['u2'], df['Rs'], df['Ra'], elevation=766
                    )
                except Exception:
                    df['ET_PM'] = np.nan

                df = df.dropna(subset=['ET'])

                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated")

                df['ET_smooth'] = df['ET'].rolling(window=5, min_periods=1).mean()
                pm_ok = 'ET_PM' in df.columns and not df['ET_PM'].isna().all()
                if pm_ok:
                    df['ET_PM_smooth'] = df['ET_PM'].rolling(window=5, min_periods=1).mean()
                else:
                    df['ET_PM_smooth'] = np.nan

                et_stats_pt = _stats_for_series(df['ET'])
                if pm_ok:
                    et_stats_pm = _stats_for_series(df['ET_PM'])

                plt.figure(figsize=(12, 6))
                plt.style.use('default')
                plt.gca().set_facecolor('#f8fffe')

                if selected_unit == 'inches':
                    plot_et = df['ET'].apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                    plot_et_smooth = df['ET_smooth'].apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                else:
                    plot_et = df['ET']
                    plot_et_smooth = df['ET_smooth']

                plt.plot(df['Date'], plot_et, label='Priestley–Taylor daily', color='#86A873', alpha=0.55, linewidth=1.4)
                plt.plot(df['Date'], plot_et_smooth, label='Priestley–Taylor (5-day avg)', color='#087F8C', linewidth=2.6)
                if pm_ok:
                    if selected_unit == 'inches':
                        plot_pm = df['ET_PM'].apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                        plot_pm_smooth = df['ET_PM_smooth'].apply(
                            lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x
                        )
                    else:
                        plot_pm = df['ET_PM']
                        plot_pm_smooth = df['ET_PM_smooth']
                    plt.plot(df['Date'], plot_pm, label='Penman–Monteith daily', color='#D62728', alpha=0.5, linewidth=1.4)
                    plt.plot(df['Date'], plot_pm_smooth, label='Penman–Monteith (5-day avg)', color='#A50F15', linewidth=2.4)

                plt.title(
                    'Evapotranspiration (ET₀) — Priestley–Taylor & Penman–Monteith',
                    fontsize=16,
                    fontweight='bold',
                    color='#095256',
                    pad=20,
                )
                plt.xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                plt.ylabel(f'ET₀ ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
                plt.grid(True, alpha=0.3, color='#5AAA95')
                plt.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=9)
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()

                buf = BytesIO()
                plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
                buf.seek(0)
                plot_url = base64.b64encode(buf.read()).decode('utf-8')
                buf.close()
                plt.close()

                download_cols = ['Date', 'ET', 'ET_smooth']
                if pm_ok:
                    download_cols.extend(['ET_PM', 'ET_PM_smooth'])
                request.session['et_data_csv'] = df[download_cols].to_csv(index=False)

                et_data = []
                for _, row in df.iterrows():
                    et_val = row['ET'] if not pd.isna(row['ET']) else 0
                    if selected_unit == 'inches' and et_val:
                        et_val = convert_units(et_val, 'mm', 'inches')
                    row_dict = {
                        'Date': row['Date'],
                        'ET': round(float(et_val), unit_info['decimal_places']) if et_val else 0,
                    }
                    if pm_ok and not pd.isna(row['ET_PM']):
                        pm_val = row['ET_PM']
                        if selected_unit == 'inches':
                            pm_val = convert_units(pm_val, 'mm', 'inches')
                        row_dict['ET_PM'] = round(float(pm_val), unit_info['decimal_places'])
                    else:
                        row_dict['ET_PM'] = None
                    et_data.append(row_dict)

            except Exception as e:
                print(f"File processing error: {e}")
                return render(
                    request,
                    'et/index.html',
                    {
                        'form': form,
                        'error_message': f"Error processing file: {str(e)}. Please check your CSV format.",
                        'selected_unit': selected_unit,
                        'unit_info': unit_info,
                    },
                )

    else:
        form = UploadFileForm()

    context = {
        'form': form,
        'et_data': et_data,
        'et_stats': et_stats_pt,
        'et_stats_pt': et_stats_pt,
        'et_stats_pm': et_stats_pm,
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
                    if 'tmax' in [c.lower() for c in df.columns] and 'tmin' in [c.lower() for c in df.columns]:
                        tmax_col = next(c for c in df.columns if c.lower() == 'tmax')
                        tmin_col = next(c for c in df.columns if c.lower() == 'tmin')
                        df['Tmax'] = pd.to_numeric(df[tmax_col], errors='coerce')
                        df['Tmin'] = pd.to_numeric(df[tmin_col], errors='coerce')
                    else:
                        # Fallback split when only average temperature is available.
                        df['Tmax'] = df['Tavg'] + 5
                        df['Tmin'] = df['Tavg'] - 5
                    df['Ra'] = df.apply(
                        lambda row: calculate_extraterrestrial_radiation(49.7, row['Date'].dayofyear),
                        axis=1
                    )
                    df['Rn'] = df.apply(
                        lambda row: net_radiation_estimate(row['Rs'], row['Tmax'], row['Tmin'], row['Ra'], elevation=766),
                        axis=1
                    )
                    df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rn']), axis=1)
                    
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

                    df['Ra'] = df.apply(
                        lambda row: calculate_extraterrestrial_radiation(49.7, row['Date'].dayofyear),
                        axis=1
                    )
                    df['ET'] = df.apply(
                        lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs'], row['Ra'], elevation=766),
                        axis=1
                    )

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
                plt.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=10)
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
                day_of_year = datetime.now().timetuple().tm_yday
                ra = calculate_extraterrestrial_radiation(49.7, day_of_year)
                et_value = penman_monteith_ET(tmax, tmin, rh, u2, rs, ra, elevation=766)
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
    # ECCC discontinued the old RSS feed in 2025; this project uses MSC Datamart XML instead.
    from .environment_canada_scraper import EnvironmentCanadaScraper

    cache_key = "lethbridge_msc_forecast_v1"
    cache_ttl = int(os.environ.get("LETHBRIDGE_FORECAST_CACHE_SECONDS", "900"))

    if cache_ttl > 0:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        scraper = EnvironmentCanadaScraper()
        df = scraper.fetch_forecast(city_name="Lethbridge", days=5)
        out = []
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                out.append(
                    {
                        "date": str(row.get("Date", "")),
                        "title": str(row.get("Period", "")),
                        "summary": str(row.get("Forecast", "")),
                    }
                )
        if cache_ttl > 0:
            cache.set(cache_key, out, cache_ttl)
        return out
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


# Update individual method views
def maule_only(request):
    """Maulé method only with unit toggle"""
    return process_single_method_enhanced(request, 'Maule', 'Maulé', 'et/maule.html')
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
                    df['Rn'] = df.apply(
                        lambda row: net_radiation_estimate(row['Rs'], row['Tmax'], row['Tmin'], row['Ra'], row['RH'], elevation=766),
                        axis=1
                    )
                    df['ET_PT'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rn']), axis=1)
                except Exception as e:
                    print(f"PT calculation failed: {e}")
                    df['ET_PT'] = np.nan

                # Penman-Monteith
                try:
                    df['ET_PM'] = df.apply(
                        lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs'], row['Ra'], elevation=766),
                        axis=1
                    )
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
                ax1.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=10)
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
                    ax2.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=9)
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
                    if tmax_cols and tmin_cols:
                        df['Tmax'] = pd.to_numeric(df[tmax_cols[0]], errors='coerce')
                        df['Tmin'] = pd.to_numeric(df[tmin_cols[0]], errors='coerce')
                    else:
                        df['Tmax'] = df['Tavg'] + 5
                        df['Tmin'] = df['Tavg'] - 5
                    df['Ra'] = df.apply(
                        lambda row: calculate_extraterrestrial_radiation(49.7, row['Date'].dayofyear),
                        axis=1
                    )
                    df['Rn'] = df.apply(
                        lambda row: net_radiation_estimate(
                            row['Rs'],
                            row['Tmax'],
                            row['Tmin'],
                            row['Ra'],
                            row['RH'] if 'RH' in df.columns else np.nan,
                            elevation=766
                        ),
                        axis=1
                    )
                    df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rn']), axis=1)
                    
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

                    df['Ra'] = df.apply(
                        lambda row: calculate_extraterrestrial_radiation(49.7, row['Date'].dayofyear),
                        axis=1
                    )
                    df['ET'] = df.apply(
                        lambda row: penman_monteith_ET(row['Tmax'], row['Tmin'], row['RH'], row['u2'], row['Rs'], row['Ra'], elevation=766),
                        axis=1
                    )

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
                plt.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=10)
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


def acis_data_view(request):
    """View for loading weather data used by ET method calculations."""
    error_message = None
    df_preview = None
    success_message = None
    location_result = None
    scraping = False
    data_source_mode = request.POST.get("data_source_mode", "exact_coordinates") if request.method == "POST" else "exact_coordinates"
    used_station = None
    
    selected_unit = request.GET.get('unit', 'mm')
    if selected_unit not in ['mm', 'inches']:
        selected_unit = 'mm'
    
    unit_info = get_unit_info(selected_unit)
    
    if request.method == 'POST':
        try:
            location_type = request.POST.get('location_type', 'place')
            uploaded_file = request.FILES.get('file')
            data_source_mode = request.POST.get("data_source_mode", "exact_coordinates")
            
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
                
                if (end_dt - start_dt).days > 365 * 30:  # 30 years max
                    raise ValueError("Date range cannot exceed 30 years")
                
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
            
            # Optional snap-to-nearest-station mode for coordinate sourcing.
            if data_source_mode == "nearest_station":
                station_result = find_nearest_alberta_station(latitude, longitude)
                if not station_result:
                    raise ValueError("Could not locate a nearby station for selected coordinates")
                used_station = station_result["name"]
                latitude = station_result["latitude"]
                longitude = station_result["longitude"]
                location_desc = f"{location_desc} | Using nearest station: {used_station}"
            else:
                used_station = "Exact farm coordinates"

            print(f"\n{'='*60}")
            print(f"FETCHING DATA FOR ET PIPELINE")
            print(f"{'='*60}")
            print(f"Location: {location_desc}")
            print(f"Station/source mode: {used_station}")
            print(f"Date range: {start_date} to {end_date}")
            print(f"{'='*60}\n")
            
            # Keep compatibility with template variable name.
            scraping = True

            # Backup path: user-uploaded CSV takes precedence when provided.
            try:
                if uploaded_file:
                    uploaded_bytes = uploaded_file.read()
                    uploaded_text = uploaded_bytes.decode('utf-8', errors='replace')
                    uploaded_df = pd.read_csv(io.StringIO(uploaded_text))
                    df = normalize_uploaded_weather_dataframe(uploaded_df)
                    source_name = "uploaded CSV"
                else:
                    # Primary deploy-safe historical source.
                    today_dt = date.today()
                    if end_dt.date() > today_dt:
                        raise ValueError("Historical fetch only supports dates up to today; upload CSV for future scenarios")
                    df = fetch_openmeteo_historical_data(latitude, longitude, start_date, end_date)
                    source_name = "Open-Meteo historical archive"
                
                # Validate data
                if df is None or len(df) == 0:
                    raise ValueError("No weather data returned from selected source")

                # Overlay RH from ECCC climate observations where available.
                df = add_eccc_rh_to_dataframe(df, latitude=latitude, longitude=longitude, prefer_eccc=True)
                
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
                    df['RH'] = np.nan
                df['RH'] = pd.to_numeric(df['RH'], errors='coerce').fillna(65.0)
                if 'Wind_Speed' not in df.columns:
                    df['Wind_Speed'] = 2.0
                
                df['u2'] = df['Wind_Speed']
                
                # Clean up
                df = df.drop('day_of_year', axis=1, errors='ignore')
                
                print(f"\nSuccessfully fetched {len(df)} days of data")
                print(f"  Temperature range: {df['Tmax'].min():.1f}°C to {df['Tmax'].max():.1f}°C")
                
                # Store in session
                request.session['acis_data'] = df.to_json(date_format='iso')
                request.session['acis_location'] = {
                    'latitude': latitude,
                    'longitude': longitude,
                    'start_date': start_date,
                    'end_date': end_date,
                    'description': location_desc,
                    'station': station_name,
                    'used_station': used_station,
                    'data_source_mode': data_source_mode,
                }
                
                # Preview
                df_preview = df.head(10).to_dict('records')
                success_message = (
                    f"Successfully loaded {len(df)} day(s) of weather data from {source_name}."
                )
                success_message += f" Source mode: {used_station}."
                
            except Exception as scrape_error:
                print(f"\nEnvironment Canada fetch failed: {scrape_error}")
                
                error_message = (
                    f"Automatic data fetch failed: {str(scrape_error)}\n\n"
                    f"Try another location/date range, or upload a CSV file as backup."
                )
                
        except ValueError as e:
            error_message = str(e)
            print(f"ValueError: {e}")
        except Exception as e:
            error_message = f"Unexpected error: {str(e)}"
            print(f"Full error: {e}")
            import traceback
            traceback.print_exc()
    
    nearest_station_hint = None
    if used_station and used_station != "Exact farm coordinates":
        nearest_station_hint = used_station

    context = {
        'error_message': error_message,
        'success_message': success_message,
        'df_preview': df_preview,
        'location_result': location_result,
        'used_station': used_station,
        'nearest_station_hint': nearest_station_hint,
        'data_source_mode': data_source_mode,
        'alberta_stations': [
            {'name': name, 'lat': coords['lat'], 'lon': coords['lon']}
            for name, coords in ALBERTA_STATIONS_COORDS.items()
        ],
        'selected_unit': selected_unit,
        'unit_info': unit_info,
        'popular_locations': list(ALBERTA_LOCATIONS.keys())[:20],
        'scraping': scraping
    }
    
    return render(request, 'et/acis_fetch.html', context)


def comparison_with_acis(request):
    """Enhanced ET calculator with method-only ET comparison."""
    et_data = None
    et_stats = None
    comparison_stats = None
    growing_season_stats = None
    plot_url = None
    growing_season_plots = None
    plot_warning = None
    
    # Get session data from input page
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
    # This page does not render forecast sidebars; skip slow network calls here.
    forecast_data = None
    env_canada_forecast = None
    ec_city_name = None
    
    try:
        # Clamp negative Tmax/Tavg (keep Tmin raw so temperature range is preserved).
        if 'Tmax' in df.columns:
            df['Tmax'] = pd.to_numeric(df['Tmax'], errors='coerce').clip(lower=0)
            # Used by several ET methods; must exist even if ET columns are already present in session.
            df['Tmax_clamped'] = df['Tmax'].clip(lower=0)
        if 'Tmin' in df.columns:
            df['Tmin'] = pd.to_numeric(df['Tmin'], errors='coerce')
        if 'Tavg' in df.columns:
            df['Tavg'] = pd.to_numeric(df['Tavg'], errors='coerce').clip(lower=0)

        # Add day of year column for radiation calculations
        df['day_of_year'] = df['Date'].dt.dayofyear
        
        # Get latitude from session
        latitude = location_info.get('latitude', 49.7)
        
        # We need temperature columns only when ET columns are not already present.
        existing_et_columns = ['ET_PT', 'ET_PM', 'ET_Maule', 'ET_Hargreaves']
        has_existing_et = any(col in df.columns for col in existing_et_columns)
        if ('Tmax' not in df.columns or 'Tmin' not in df.columns) and not has_existing_et:
            raise ValueError("Missing temperature data")
        
        # Calculate ET inputs only when ET series are not already available.
        if not has_existing_et:
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
            else:
                df['RH'] = pd.to_numeric(df['RH'], errors='coerce').fillna(65.0)
            
            # Calculate extraterrestrial radiation (vectorized; major speedup)
            df['Ra'] = calculate_extraterrestrial_radiation_vec(latitude, df['day_of_year'].to_numpy())
        else:
            # Session may include partial weather + partial ET; ensure required drivers exist
            # for any still-missing ET method columns.
            if 'Tavg' not in df.columns and {'Tmax', 'Tmin'}.issubset(df.columns):
                df['Tavg'] = (df['Tmax'] + df['Tmin']) / 2
            if 'Rs' not in df.columns and {'Tmax', 'Tmin'}.issubset(df.columns):
                df['Rs'] = (df['Tmax'] - df['Tmin']) * 0.16 * np.sqrt(12)
            if 'u2' not in df.columns:
                if 'Wind_Speed' in df.columns:
                    df['u2'] = df['Wind_Speed']
                else:
                    df['u2'] = 2.0
            if 'RH' not in df.columns:
                df['RH'] = 65.0
            else:
                df['RH'] = pd.to_numeric(df['RH'], errors='coerce').fillna(65.0)
            if 'Ra' not in df.columns and 'day_of_year' in df.columns:
                df['Ra'] = calculate_extraterrestrial_radiation_vec(latitude, df['day_of_year'].to_numpy())
        
        # Calculate ET using all four methods (only if not already present)
        if 'ET_PT' not in df.columns:
            try:
                df['Rn'] = net_radiation_estimate_vec(
                    df['Rs'], df['Tmax'], df['Tmin'], df['Ra'], df['RH'], elevation=766
                )
                df['ET_PT'] = priestley_taylor_ET_vec(df['Tavg'], df['Rn'])
            except Exception as e:
                print(f"PT calculation failed: {e}")
                df['ET_PT'] = np.nan
        
        need_pm = 'ET_PM' not in df.columns
        if not need_pm:
            need_pm = bool(df['ET_PM'].isna().all())
        if need_pm:
            try:
                if 'Tmax' in df.columns and 'Tmax_clamped' not in df.columns:
                    df['Tmax_clamped'] = pd.to_numeric(df['Tmax'], errors='coerce').clip(lower=0)
                df['ET_PM'] = penman_monteith_ET_vec(
                    df['Tmax_clamped'], df['Tmin'], df['RH'], df['u2'], df['Rs'], df['Ra'], elevation=766
                )
            except Exception as e:
                print(f"PM calculation failed: {e}")
                df['ET_PM'] = np.nan
        
        if 'ET_Maule' not in df.columns:
            try:
                df['ET_Maule'] = maule_ET_vec(df['Tmax_clamped'], df['Tmin'], df['Rs'], df['RH'], latitude=latitude)
            except Exception as e:
                print(f"Maule calculation failed: {e}")
                df['ET_Maule'] = np.nan
        
        if 'ET_Hargreaves' not in df.columns:
            try:
                df['ET_Hargreaves'] = hargreaves_ET_vec(df['Tmax_clamped'], df['Tmin'], df['Ra'], latitude=latitude)
            except Exception as e:
                print(f"Hargreaves calculation failed: {e}")
                df['ET_Hargreaves'] = np.nan
        
        # Remove rows with ALL NaN ET values across supported ET methods.
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
            'Hargreaves': 'Hargreaves-Samani',
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
            method for method in ['PT', 'PM', 'Maule', 'Hargreaves']
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
            
            # Calculate mean differences from PM when available.
            reference_method = 'PM' if 'PM' in available_methods else None
            
            if reference_method:
                for method in available_methods:
                    if method != reference_method:
                        diff_mm = (df[f'ET_{method}'] - df[f'ET_{reference_method}']).abs().mean()
                        if selected_unit == 'inches':
                            comparison_stats[f'{method}_{reference_method}_diff'] = convert_units(
                                diff_mm, 'mm', 'inches'
                            )
                        else:
                            comparison_stats[f'{method}_{reference_method}_diff'] = diff_mm
        
        # Persist ET outputs for table/download/update even if plotting fails.
        csv_columns = ['Date'] + [f'ET_{method}' for method in available_methods]
        request.session['et_data_csv'] = df[csv_columns].to_csv(index=False)
        request.session['comparison_et_data'] = df[csv_columns].to_json(date_format='iso')

        # Calculate growing season statistics for primary method
        if 'ET_PM' in df.columns and not df['ET_PM'].isna().all():
            growing_season_stats = calculate_growing_season_stats(df, 'ET_PM', selected_unit)
        elif available_methods:
            primary_method = available_methods[0]
            growing_season_stats = calculate_growing_season_stats(
                df, f'ET_{primary_method}', selected_unit
            )
        
        try:
            growing_season_plots = create_multi_method_growing_season_plots(
                df,
                selected_methods=available_methods,
                unit=selected_unit,
            )

            # Create enhanced comparison plot
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
            fig.patch.set_facecolor('white')

            # Main ET comparison plot
            ax1.set_facecolor('#f8fffe')
            colors = {
                'PT': '#1F77B4',
                'PM': '#D62728',
                'Maule': '#2CA02C',
                'Hargreaves': '#9467BD',
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

                ax1.plot(
                    df['Date'], plot_data_smooth,
                    color=colors[method], linewidth=2.5, alpha=0.9,
                    label=f'{method_names[method]}'
                )

            # Add location info to title
            location_desc = location_info.get('description', 'Selected weather data')
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
            ax1.legend(frameon=True, fancybox=False, shadow=False, loc='upper left', fontsize=10)
            ax1.tick_params(axis='x', rotation=45)

            # Method differences plot
            ax2.set_facecolor('#f8fffe')
            reference_method = 'PM'

            if reference_method in available_methods and len(available_methods) > 1:
                for method in available_methods:
                    if method != reference_method:
                        col = f'ET_{method}'
                        diff = (df[f'ET_{reference_method}'] - df[col]).abs()
                        if selected_unit == 'inches':
                            diff = diff.apply(
                                lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x
                            )

                        ax2.plot(
                            df['Date'], diff, color=colors[method], linewidth=2, alpha=0.7,
                            label=f'|{method_names[reference_method]} - {method_names[method]}|'
                        )

                ax2.set_title(
                    'Absolute Differences from Penman-Monteith (Reference Method)',
                    fontsize=14, fontweight='bold', color='#095256'
                )
                ax2.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                ax2.set_ylabel(
                    f'Difference ({unit_info["daily_label"]})',
                    fontsize=12, fontweight='600', color='#095256'
                )
                ax2.grid(True, alpha=0.3, color='#5AAA95')
                ax2.legend(frameon=True, fancybox=False, shadow=False, loc='upper left', fontsize=9)
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
        except Exception as plot_error:
            plt.close('all')
            print(f"Plot rendering fallback enabled: {plot_error}")
            plot_warning = "Using browser-rendered charts."
        
        # Prepare data for rendering with unit conversion (avoid slow iterrows)
        et_cols = [f'ET_{m}' for m in available_methods if f'ET_{m}' in df.columns]
        table_df = df[['Date'] + et_cols].copy()
        for col in et_cols:
            table_df[col] = pd.to_numeric(table_df[col], errors='coerce').fillna(0.0)
            if selected_unit == 'inches':
                table_df[col] = table_df[col].apply(
                    lambda v: convert_units(v, 'mm', 'inches') if v else 0.0
                )
            table_df[col] = table_df[col].apply(
                lambda v: round(float(v), unit_info['decimal_places']) if v else 0
            )
        et_data = table_df.to_dict('records')
    
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
        'plot_warning': plot_warning,
        'growing_season_plots': growing_season_plots,
        'selected_unit': selected_unit,
        'unit_info': unit_info,
        'acis_location': location_info,
        'available_methods': available_methods,
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
        
        # Get data from session.
        comparison_et_data_json = request.session.get('comparison_et_data')
        acis_data_json = request.session.get('acis_data')
        et_data_csv = request.session.get('et_data_csv')
        
        if not comparison_et_data_json and not acis_data_json and not et_data_csv:
            return JsonResponse({'error': 'No data in session'}, status=400)
        
        # Load data
        if comparison_et_data_json:
            df = pd.read_json(io.StringIO(comparison_et_data_json))
        elif acis_data_json:
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
            'PT': '#1F77B4',
            'PM': '#D62728',
            'Maule': '#2CA02C',
            'Hargreaves': '#9467BD',
        }
        
        method_names = {
            'PT': 'Priestley-Taylor',
            'PM': 'Penman-Monteith', 
            'Maule': 'Maulé',  
            'Hargreaves': 'Hargreaves-Samani',
        }
        
        # Plot each selected method
        for method in available_methods:
            col = f'ET_{method}'
            if col in df.columns:
                # Apply unit conversion if needed
                values = df[col].copy()
                if selected_unit == 'inches':
                    values = values.apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                
                ax1.plot(df['Date'], values, color=colors[method], linewidth=2, alpha=0.7, label=method_names[method])
        
        ax1.set_title('ET Method Comparison', fontsize=16, fontweight='bold', color='#095256')
        ax1.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
        ax1.set_ylabel(f'ET ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
        ax1.grid(True, alpha=0.3, color='#5AAA95')
        ax1.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=10)
        ax1.tick_params(axis='x', rotation=45)
        
        # Difference plot
        if len(available_methods) > 1:
            reference_method = 'PM' if 'PM' in available_methods else available_methods[0]
            
            for method in available_methods:
                if method != reference_method:
                    col = f'ET_{method}'
                    if col in df.columns and f'ET_{reference_method}' in df.columns:
                        diff = (df[f'ET_{reference_method}'] - df[col]).abs()
                        if selected_unit == 'inches':
                            diff = diff.apply(lambda x: convert_units(x, 'mm', 'inches') if not pd.isna(x) else x)
                        
                        ax2.plot(df['Date'], diff, color=colors.get(method, '#666'), linewidth=2, alpha=0.7, 
                                label=f'|{method_names[reference_method]} - {method_names[method]}|')
            
            ax2.set_title(f'Absolute Differences from {method_names[reference_method]} (Reference)', 
                         fontsize=14, fontweight='bold', color='#095256')
            ax2.set_xlabel('Date', fontsize=12, fontweight='600', color='#095256')
            ax2.set_ylabel(f'Difference ({unit_info["daily_label"]})', fontsize=12, fontweight='600', color='#095256')
            ax2.grid(True, alpha=0.3, color='#5AAA95')
            ax2.legend(frameon=True, fancybox=True, shadow=False, loc='upper left', fontsize=9)
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
        
        growing_season_plots = create_multi_method_growing_season_plots(
            df,
            selected_methods=available_methods,
            unit=selected_unit,
        )

        return JsonResponse({
            'plot_url': plot_url,
            'growing_season_plot_url': growing_season_plots.get('growing_season_analysis'),
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

def _resolve_city_lat_lon(city_name):
    if city_name in ALBERTA_LOCATIONS:
        loc = ALBERTA_LOCATIONS[city_name]
        return loc["lat"], loc["lon"]
    try:
        geo = geocode_location(city_name)
        return geo["latitude"], geo["longitude"]
    except Exception:
        # fallback near central Alberta
        return 53.0, -114.0

def _pt_daily_et_from_temperature(tmax, tmin, latitude, day_of_year):
    """
    Priestley-Taylor daily ET estimate using temperature-derived radiation.
    """
    tavg = (tmax + tmin) / 2.0
    ra = calculate_extraterrestrial_radiation(latitude, day_of_year)
    # Hargreaves-style radiation estimate used as input to PT net radiation.
    temp_range = max(tmax - tmin, 0.5)
    rs = 0.16 * np.sqrt(temp_range) * ra
    rn = net_radiation_estimate(rs, tmax, tmin, ra, RH=65.0, elevation=766)
    et_pt = priestley_taylor_ET(tavg, rn)
    return max(float(et_pt), 0.0) if not pd.isna(et_pt) else 0.0


def _pm_daily_et_from_temperature(tmax, tmin, latitude, day_of_year, rh=65.0, u2=None, rs=None, elevation=766):
    """
    Penman–Monteith (FAO-56) daily ET0. Prefer measured daily shortwave (Rs, MJ/m²/day)
    from Open-Meteo when *rs* is provided; otherwise fall back to Hargreaves-style Rs
    from temperature range and extraterrestrial radiation.

    u2 is 2 m wind (m/s) from ECCC/MSC and/or Open-Meteo wind_speed_10m_max.
    """
    tmax_c = max(float(tmax), 0.0)
    tmin_c = float(tmin)
    rh_use = 65.0
    if rh is not None and not pd.isna(rh):
        try:
            rh_use = float(rh)
        except (TypeError, ValueError):
            rh_use = 65.0
    u2_use = None
    if u2 is not None and not pd.isna(u2):
        try:
            u2_use = max(float(u2), 0.25)
        except (TypeError, ValueError):
            u2_use = None
    if u2_use is None:
        u2_use = 2.0
    ra = calculate_extraterrestrial_radiation(latitude, int(day_of_year))
    temp_range = max(tmax_c - tmin_c, 0.5)
    rs_use = None
    if rs is not None and not pd.isna(rs):
        try:
            rs_use = float(rs)
        except (TypeError, ValueError):
            rs_use = None
    if rs_use is None or rs_use <= 0:
        rs_use = 0.16 * np.sqrt(temp_range) * ra
    et0 = penman_monteith_ET(
        tmax_c,
        tmin_c,
        float(rh_use),
        float(u2_use),
        float(rs_use),
        float(ra),
        elevation=elevation,
    )
    return max(float(et0), 0.0) if not pd.isna(et0) else 0.0

def env_canada_forecast_view(request):
    """
    Standalone view to display Environment Canada precipitation forecast
    """
    from .environment_canada_scraper import fetch_env_canada_forecast, EnvironmentCanadaScraper
    
    error_message = None
    df_forecast = None
    city_name = 'Calgary'  # Default
    selected_days = 7  # Default
    crop_type = 'wheat'
    soil_type = 'loam'
    crop_options = [
        {"value": key, "label": key.replace("_", " ").title()}
        for key in sorted(CROP_GDD_PROFILES.keys())
    ]
    soil_options = [
        {"value": key, "label": key.replace("_", " ").title()}
        for key in sorted(SOIL_IRRIGATION_FACTORS.keys())
    ]

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
        crop_type = request.POST.get('crop_type', 'wheat').strip().lower()
        soil_type = request.POST.get('soil_type', 'loam').strip().lower()
        if crop_type not in CROP_GDD_PROFILES:
            crop_type = 'wheat'
        if soil_type not in SOIL_IRRIGATION_FACTORS:
            soil_type = 'loam'
        try:
            selected_days = int(request.POST.get('days', 7))
        except (TypeError, ValueError):
            selected_days = 7
        selected_days = max(3, min(selected_days, 30))
        
        try:
            df = fetch_env_canada_forecast(city_name, selected_days)
            lat, lon = _resolve_city_lat_lon(city_name)
            _ = lon
            df = merge_openmeteo_forecast_drivers(df, lat, lon)

            # Convert to records for template
            df_forecast = []
            for _, row in df.iterrows():
                temp_high = safe_temp_convert(row['Temp_High'])
                temp_low = safe_temp_convert(row['Temp_Low'])
                precip = safe_temp_convert(row['Precipitation_mm'])
                rh_pct = safe_temp_convert(row.get('RH_percent'))
                u2_ms = safe_temp_convert(row.get('u2_ms'))
                wind_kmh = safe_temp_convert(row.get('Wind_kmh_max'))
                rs_mj = safe_temp_convert(row.get('Rs_mjm2'))

                forecast_dict = {
                    'Date': row['Date'],
                    'Period': row['Period'],
                    'Temp_High': temp_high,
                    'Temp_Low': temp_low,
                    'RH_percent': rh_pct,
                    'u2_ms': u2_ms,
                    'Wind_kmh_max': wind_kmh,
                    'Rs_mjm2': rs_mj,
                    'Precipitation_mm': precip if precip is not None else 0.0,
                    'Forecast': str(row['Forecast']) if row['Forecast'] else ''
                }
                df_forecast.append(forecast_dict)
            
            # Penman–Monteith + GDD recommendation on current forecast.
            total_precip = sum([f['Precipitation_mm'] for f in df_forecast])
            estimated_et_total = 0.0
            cumulative_gdd = 0.0
            forecast_irrig_curve = []
            running_irrig = 0.0
            current_stage_label = "Early establishment"
            soil_factor = SOIL_IRRIGATION_FACTORS.get(soil_type, 1.0)

            for item in df_forecast:
                tmax = item['Temp_High'] if item['Temp_High'] is not None else item['Temp_Low']
                tmin = item['Temp_Low'] if item['Temp_Low'] is not None else item['Temp_High']
                if tmax is None or tmin is None:
                    continue
                d = pd.to_datetime(item["Date"])
                day_of_year = d.dayofyear
                et0 = _pm_daily_et_from_temperature(
                    float(tmax),
                    float(tmin),
                    lat,
                    day_of_year,
                    rh=item.get("RH_percent"),
                    u2=item.get("u2_ms"),
                    rs=item.get("Rs_mjm2"),
                )
                daily_gdd = calculate_daily_gdd(float(tmax), float(tmin))
                cumulative_gdd += daily_gdd
                stage_factor, current_stage_label = gdd_stage_factor(cumulative_gdd, crop_type)
                adjusted_et = et0 * stage_factor
                estimated_et_total += adjusted_et
                daily_irrig = max(adjusted_et - max(float(item["Precipitation_mm"]), 0.0), 0.0)
                running_irrig += daily_irrig
                forecast_irrig_curve.append(running_irrig)

            net_water_balance = total_precip - estimated_et_total
            irrigation_needed = running_irrig * soil_factor
            recommendation_level = (
                "low"
                if irrigation_needed < 10
                else "moderate"
                if irrigation_needed < 25
                else "high"
            )

            # 5-year historical baseline confidence band (sample areas around city).
            historical_confidence = build_historical_confidence(
                city_name,
                selected_days,
                crop_type,
                _resolve_city_lat_lon,
                _pm_daily_et_from_temperature,
            )
            forecast_curve_soil_adjusted = [v * soil_factor for v in forecast_irrig_curve]
            rec_chart_url = build_irrigation_confidence_plot(
                historical_confidence, forecast_curve_soil_adjusted
            )
            chart_available = bool(rec_chart_url)
            crop_label = next((item["label"] for item in crop_options if item["value"] == crop_type), "Wheat")
            soil_label = next((item["label"] for item in soil_options if item["value"] == soil_type), "Loam")

            context = {
                'city_name': city_name,
                'selected_days': selected_days,
                'show_extended_horizon_caveat': selected_days > 7,
                'df_forecast': df_forecast,
                'total_precip': total_precip,
                'estimated_et_total': estimated_et_total,
                'net_water_balance': net_water_balance,
                'irrigation_needed': irrigation_needed,
                'recommendation_level': recommendation_level,
                'gdd_total': cumulative_gdd,
                'gdd_stage': current_stage_label,
                'crop_type': crop_type,
                'soil_type': soil_type,
                'crop_label': crop_label,
                'soil_label': soil_label,
                'crop_options': crop_options,
                'soil_options': soil_options,
                'soil_factor': soil_factor,
                'historical_confidence': historical_confidence,
                'rec_chart_url': rec_chart_url,
                'chart_available': chart_available,
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
        'show_extended_horizon_caveat': False,
        'chart_available': False,
        'crop_type': crop_type,
        'soil_type': soil_type,
        'crop_label': next((item["label"] for item in crop_options if item["value"] == crop_type), "Wheat"),
        'soil_label': next((item["label"] for item in soil_options if item["value"] == soil_type), "Loam"),
        'crop_options': crop_options,
        'soil_options': soil_options,
        'available_cities': all_cities,
        'cities_by_region': cities_by_region,
    }
    
    return render(request, 'et/env_canada_forecast.html', context)

def aquacrop_simulation(request):
    """
    View for AquaCrop crop growth simulation
    """

    default_city = ""
    available_cities = sorted(ALBERTA_LOCATIONS.keys())

    context = {
        'crops': list(AquaCropSimulator.AVAILABLE_CROPS.keys()),
        'soil_types': list(AquaCropSimulator.SOIL_TYPES.keys()),
        'available_cities': available_cities,
        'selected_city': default_city,
        'selected_crop': '',
        'selected_soil': '',
        'selected_irrigation': 'full',
        'timestep': '',
        'start_date': '',
        'end_date': '',
        'irrigation_methods': [
            ('rainfed', 'Rainfed (No Irrigation)'),
            ('full', 'Full Irrigation (80% SMT)'),
            ('deficit', 'Deficit Irrigation (60% SMT)'),
        ],
    }

    def _maturity_warning(crop_name: str, end_date_str: str):
        thresholds = {"Wheat": "09-15", "Maize": "09-20"}
        crop_threshold = thresholds.get(crop_name)
        if not crop_threshold:
            return None
        end_ts = pd.to_datetime(end_date_str, errors="coerce")
        if pd.isna(end_ts):
            return None
        recommended_ts = pd.Timestamp(f"{int(end_ts.year)}-{crop_threshold}")
        if end_ts.normalize() < recommended_ts.normalize():
            recommended_date = recommended_ts.strftime("%Y-%m-%d")
            return (
                f"Warning: Your selected end date may not allow enough time for {crop_name} to reach maturity. "
                f"Consider extending to at least {recommended_date}. "
                "Actual maturity can still occur later depending on location weather and temperatures. "
                "Partial growth results will still be shown."
            )
        return None
    
    if request.method == 'POST':
        try:
            # Get form data
            timestep = request.POST.get('timestep', 'weekly')
            crop = request.POST.get('crop', 'Wheat')
            soil = request.POST.get('soil', 'Loam')
            irrigation = request.POST.get('irrigation', 'full')
            city_name = request.POST.get('city_name', default_city).strip()
            if city_name and city_name not in available_cities:
                city_name = default_city
            start_date = request.POST.get('start_date', '')
            end_date = request.POST.get('end_date', '')
            context['maturity_warning'] = _maturity_warning(crop, end_date)
            
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
            else:
                city_coords = ALBERTA_LOCATIONS.get(city_name)
                if not city_coords:
                    raise ValueError(f"No coordinates found for selected city: {city_name}")
                weather_df = build_aquacrop_weather_from_eccc(
                    latitude=city_coords['lat'],
                    longitude=city_coords['lon'],
                    start_date=start_date,
                    end_date=end_date,
                )
                weather_warnings = list(weather_df.attrs.get("warnings", [])) if hasattr(weather_df, "attrs") else []
                if weather_warnings:
                    context['warning_messages'] = weather_warnings
                weather_source_summary = (
                    weather_df.attrs.get("temperature_source_summary", {})
                    if hasattr(weather_df, "attrs")
                    else {}
                )
                if weather_source_summary:
                    context["temperature_source_summary"] = weather_source_summary

            # Run simulation
            results = run_aquacrop_simulation(
                crop=crop,
                soil=soil,
                start_date=start_date,
                end_date=end_date,
                irrigation=irrigation,
                weather_df=weather_df
            )

            # Weekly/biweekly aggregation
            if results.get('daily_output') is not None and len(results['daily_output']) > 0:
                daily_df = results['daily_output'].copy()
                # Add a Date column if not present (use day index)
                if 'Date' not in daily_df.columns:
                    start_dt = pd.to_datetime(start_date)
                    daily_df['Date'] = pd.date_range(start_dt, periods=len(daily_df), freq='D')
                
                resampled = aggregate_aquacrop_timeseries(daily_df, timestep)
                
                # Pick a meaningful column to plot — canopy_cover or biomass
                y_col = 'biomass' if 'biomass' in resampled.columns else resampled.select_dtypes('number').columns[0]
                ts_chart = plot_aquacrop_timeseries(
                    resampled, y_col=y_col,
                    title=f"{'Weekly' if timestep == 'weekly' else 'Biweekly'} Biomass Accumulation",
                    color='#087F8C',
                    timestep=timestep
                )
                context['resampled_chart'] = ts_chart
                resampled_table = resampled[['Period', y_col]].rename(columns={y_col: 'value'})
                context['resampled_data'] = resampled_table.to_dict('records')
                # Yield in t/ha using Harvest Index
                dry_biomass = results.get('yield_dry', results.get('yield_fresh', 0))
                context['yield_tha'] = compute_yield_tha(dry_biomass, crop)
                context['timestep'] = timestep
            
            # Add results to context
            context.update({
                'results': results,
                'selected_crop': crop,
                'selected_soil': soil,
                'selected_irrigation': irrigation,
                'selected_city': city_name,
                'start_date': start_date,
                'end_date': end_date,
                'has_results': True,
                'timestep': timestep,        # add this
            })
                        
        except Exception as e:
            print("[AQUACROP_DEBUG] aquacrop_simulation exception traceback:")
            print(traceback.format_exc())
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
            city_name = data.get('city_name', 'Calgary')
            city_coords = ALBERTA_LOCATIONS.get(city_name, ALBERTA_LOCATIONS.get('Calgary'))
            weather_df = build_aquacrop_weather_from_eccc(
                latitude=city_coords['lat'],
                longitude=city_coords['lon'],
                start_date=data.get('start_date', '2024/05/01'),
                end_date=data.get('end_date', '2024/09/01'),
            )
            
            results = run_aquacrop_simulation(
                crop=data.get('crop', 'Wheat'),
                soil=data.get('soil', 'Loam'),
                start_date=data.get('start_date', '2024/05/01'),
                end_date=data.get('end_date', '2024/09/01'),
                irrigation=data.get('irrigation', 'full'),
                weather_df=weather_df,
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
            print("[AQUACROP_DEBUG] aquacrop_api exception traceback:")
            print(traceback.format_exc())
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'POST request required'})





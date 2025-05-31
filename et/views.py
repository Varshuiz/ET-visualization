import pandas as pd
import io
from django.shortcuts import render
from .forms import UploadFileForm
from math import exp
import matplotlib.pyplot as plt
from io import BytesIO
import base64
from django.http import HttpResponse
import numpy as np
import requests
import xml.etree.ElementTree as ET

def saturation_vapor_pressure(T):
    """Compute saturation vapor pressure es from temperature T (°C)"""
    return 0.6108 * exp((17.27 * T) / (T + 237.3))

def delta_svp(T):
    """Slope of saturation vapor pressure curve (Δ) at temperature T in kPa/°C"""
    es = saturation_vapor_pressure(T)
    return 4098 * es / (T + 237.3)**2

def priestley_taylor_ET(Tavg, Rn, alpha=1.26, gamma=0.066, lambda_val=2.45):
    """Priestley–Taylor ET estimation"""
    if pd.isna(Tavg) or pd.isna(Rn):
        return np.nan
    delta = delta_svp(Tavg)
    return alpha * (delta / (delta + gamma)) * (Rn / lambda_val)


def index(request):
    et_data = None
    et_stats = None
    plot_url = None

    forecast_data = get_lethbridge_forecast()

    
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['file']
            try:
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
                    # If no date column, create a simple index-based date
                    df['Date'] = pd.date_range(start='2024-01-01', periods=len(df), freq='D')

                # Find temperature and radiation columns
                temp_cols = [col for col in df.columns if any(term in col.lower() for term in ['temp', 'air_temp', 'temperature'])]
                rad_cols = [col for col in df.columns if any(term in col.lower() for term in ['solar', 'rad', 'radiation'])]
                
                if temp_cols and rad_cols:
                    df['Tavg'] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
                    df['Rn'] = pd.to_numeric(df[rad_cols[0]], errors='coerce')
                else:
                    raise ValueError("Could not find temperature and solar radiation columns")

                # Compute ET
                df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rn']), axis=1)
                
                # Remove rows with NaN ET values
                df = df.dropna(subset=['ET'])
                
                if len(df) == 0:
                    raise ValueError("No valid ET values could be calculated")

                # Compute 5-day rolling average for smoothing
                df['ET_smooth'] = df['ET'].rolling(window=5, min_periods=1).mean()

                # Calculate statistics
                et_stats = {
                    'avg': df['ET'].mean(),
                    'max': df['ET'].max(),
                    'min': df['ET'].min(),
                    'std': df['ET'].std()
                }

                # Create the plot
                plt.figure(figsize=(12, 6))
                plt.style.use('default')  # Use default style
                
                # Set the background color
                plt.gca().set_facecolor('#f8fffe')
                
                # Plot the data
                plt.plot(df['Date'], df['ET'], 
                        label='Daily ET₀', color='#86A873', alpha=0.6, linewidth=1.5)
                plt.plot(df['Date'], df['ET_smooth'], 
                        label='5-day Rolling Average', color='#087F8C', linewidth=3)
                
                # Customize the plot
                plt.title('Evapotranspiration (ET₀) Over Time', 
                         fontsize=16, fontweight='bold', color='#095256', pad=20)
                plt.xlabel('Date', fontsize=12, fontweight='600', color='#095256')
                plt.ylabel('ET₀ (mm/day)', fontsize=12, fontweight='600', color='#095256')
                
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
                plt.close()  # Close the figure to free memory

                # Store CSV data in session for download
                request.session['et_data_csv'] = df[['Date', 'ET', 'ET_smooth']].to_csv(index=False)

                # Prepare data for rendering (convert dates to strings for JSON serialization)
                et_data = []
                for _, row in df.iterrows():
                    et_data.append({
                        'Date': row['Date'],
                        'ET': round(row['ET'], 2) if not pd.isna(row['ET']) else 0
                    })

            except Exception as e:
                print(f"File processing error: {e}")
                # Return error in context
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
}

    
    return render(request, 'et/index.html', context)

def download_et_csv(request):
    """Download the computed ET data as CSV"""
    csv_data = request.session.get('et_data_csv')
    if not csv_data:
        return HttpResponse("No ET data found in session. Please upload and process a file first.", 
                          status=404)

    response = HttpResponse(csv_data, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="computed_et_data.csv"'
    return response

# def get_lethbridge_forecast():
#     url = "https://dd.weather.gc.ca/citypage_weather/xml/AB/s0000403_e.xml"
#     response = requests.get(url)
#     root = ET.fromstring(response.content)

#     forecast_data = []
#     for forecast in root.findall(".//forecastGroup/forecast"):
#         date = forecast.findtext("dateTime[@name='startTime']/timeStamp")
#         summary = forecast.findtext("textSummary")
#         temp = forecast.findtext("temperatures/temperature[@class='high']")
#         if not temp:
#             temp = forecast.findtext("temperatures/temperature[@class='low']")
#         wind = forecast.findtext("winds/wind/speed")

#         forecast_data.append({
#             "date": date,
#             "summary": summary,
#             "temp_C": temp,
#             "wind_kmh": wind
#         })

#     return forecast_data

def get_lethbridge_forecast():
    url = "https://dd.weather.gc.ca/citypage_weather/xml/AB/s0000403_e.xml"
    response = requests.get(url)
    if response.status_code != 200:
        return None

    root = ET.fromstring(response.content)
    forecast_data = []

    for forecast in root.findall(".//forecastGroup/forecast"):
        period = forecast.findtext("period")
        temp = forecast.findtext("temperatures/temperature")
        wind = forecast.findtext("winds/wind/speed")
        humidex = forecast.findtext("humidex")

        forecast_data.append({
            "period": period,
            "temp_C": float(temp) if temp else None,
            "wind_kmh": float(wind) if wind else None,
            "humidex": float(humidex) if humidex else None
        })

    return forecast_data

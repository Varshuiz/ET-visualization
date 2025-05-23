import pandas as pd
import io
from django.shortcuts import render
from .forms import UploadFileForm
from math import exp

def saturation_vapor_pressure(T):
    """Compute saturation vapor pressure es from temperature T (°C)"""
    return 0.6108 * exp((17.27 * T) / (T + 237.3))

def delta_svp(T):
    """Slope of saturation vapor pressure curve (Δ) at temperature T in kPa/°C"""
    es = saturation_vapor_pressure(T)
    return 4098 * es / (T + 237.3)**2

def priestley_taylor_ET(Tavg, Rn, alpha=1.26, gamma=0.066, lambda_val=2.45):
    """Priestley–Taylor ET estimation"""
    delta = delta_svp(Tavg)
    return alpha * (delta / (delta + gamma)) * (Rn / lambda_val)

def index(request):
    et_data = None
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
                    df['Date'] = pd.NaT

                # Compute Tavg and ET
                df['Tavg'] = df['Air_Temp_Avg_C']
                df['Rn'] = df['Incoming_Solar_Rad_Total_MJm2']  # Proxy for net radiation
                df['ET'] = df.apply(lambda row: priestley_taylor_ET(row['Tavg'], row['Rn']), axis=1)

                # Prepare data for rendering
                et_data = df[['Date', 'ET']].round(2).to_dict(orient='records')

            except Exception as e:
                print("File processing error:", e)
                et_data = [{'Date': 'Error', 'ET': 'Could not process file'}]
    else:
        form = UploadFileForm()

    return render(request, 'et/index.html', {'form': form, 'et_data': et_data})

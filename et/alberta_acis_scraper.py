

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
import time
import tempfile
from datetime import datetime


class AlbertaACISScraper:
    """Web scraper for Alberta ACIS weather data"""
    
    STATION_MAPPING = {
        'Lethbridge': 'Lethbridge CDA',
        'Calgary': 'Calgary Int\'l A',
        'Edmonton': 'Edmonton Int\'l A',
        'Red Deer': 'Red Deer A',
        'Medicine Hat': 'Medicine Hat A',
        'Brooks': 'Brooks AGDM',
        'Taber': 'Taber AGDM',
        'Vauxhall': 'Vauxhall AGDM',
        'Grande Prairie': 'Grande Prairie A',
        'Fort McMurray': 'Fort McMurray A',
    }
    
    def __init__(self, headless=True):
        self.headless = headless
        self.driver = None
        
    def setup_driver(self):
        """Setup Chrome webdriver"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument('--headless')
        
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        
        # Unique user data directory
        user_data_dir = tempfile.mkdtemp()
        chrome_options.add_argument(f'--user-data-dir={user_data_dir}')
        
        # Set download directory
        download_dir = tempfile.gettempdir()
        prefs = {
            'download.default_directory': download_dir,
            'download.prompt_for_download': False,
        }
        chrome_options.add_experimental_option('prefs', prefs)
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        
        print("✓ Chrome driver initialized")
        
    def close(self):
        """Close the browser"""
        if self.driver:
            self.driver.quit()
            print("✓ Browser closed")
    
    def fetch_data(self, station_name, start_date, end_date):
        """Fetch weather data from Alberta ACIS"""
        
        try:
            print(f"\n🌐 Fetching data from Alberta ACIS...")
            print(f"   Station: {station_name}")
            print(f"   Date range: {start_date} to {end_date}")
            
            if not self.driver:
                self.setup_driver()
            
            url = "https://acis.alberta.ca/acis/weather-data-viewer.jsp"
            print(f"   Loading {url}...")
            self.driver.get(url)
            
            wait = WebDriverWait(self.driver, 20)
            
            # Wait for page to load
            print("   Waiting for page to load...")
            station_select = wait.until(
                EC.presence_of_element_located((By.ID, "acis_stations"))
            )
            time.sleep(3)
            
            # Step 1: Select station
            print("   Selecting station...")
            full_station_name = self.STATION_MAPPING.get(station_name, station_name)
            
            select = Select(station_select)
            station_found = False
            for option in select.options:
                option_text = option.text.strip()
                if full_station_name.upper() in option_text.upper() or \
                   station_name.upper() in option_text.upper():
                    option.click()
                    station_found = True
                    print(f"   ✓ Selected: {option_text}")
                    break
            
            if not station_found:
                raise ValueError(f"Station '{station_name}' not found")
            
            time.sleep(2)
            
            # Step 2: Select "Daily" interval
            print("   Setting interval to Daily...")
            interval_select = self.driver.find_element(By.ID, "intervalSelector")
            Select(interval_select).select_by_visible_text("Daily")
            time.sleep(1)
            
            # Step 3: Set date range using correct input IDs
            print("   Setting date range...")
            try:
                # Find date inputs by ID (they're type="text", not type="date"!)
                start_input = self.driver.find_element(By.ID, "calendarDataStart")
                end_input = self.driver.find_element(By.ID, "calendarDataEnd")
                
                # Clear and set start date
                start_input.click()
                time.sleep(0.3)
                start_input.clear()
                start_input.send_keys(start_date)
                
                # Clear and set end date
                end_input.click()
                time.sleep(0.3)
                end_input.clear()
                end_input.send_keys(end_date)
                
                time.sleep(1)
                print(f"   ✓ Dates set: {start_date} to {end_date}")
                
            except Exception as e:
                print(f"   ⚠ Error setting dates: {str(e)[:100]}")
                print(f"   Will use default dates")
            
            # Step 4: Select checkboxes using JavaScript (bypasses overlay)
            print("   Selecting data variables...")
            
            variables = {
                'cb_pop_at_max': 'Maximum Temperature',
                'cb_pop_at_min': 'Minimum Temperature',
                'cb_pop_pr_b': 'Precipitation',
                'cb_pop_et_std_grass': 'Reference Evapotranspiration',
                'cb_pop_hu_ave': 'Relative Humidity Average',
                'cb_pop_ws_inst': 'Wind Speed',
            }
            
            for checkbox_id, var_name in variables.items():
                try:
                    # Use JavaScript to check the checkbox
                    result = self.driver.execute_script(f"""
                        var checkbox = document.getElementById('{checkbox_id}');
                        if (checkbox) {{
                            checkbox.checked = true;
                            checkbox.onclick();  // Trigger the onclick event
                            return true;
                        }}
                        return false;
                    """)
                    
                    if result:
                        print(f"   ✓ Selected: {var_name}")
                    else:
                        print(f"   ⚠ Could not find: {var_name}")
                        
                    time.sleep(0.5)
                except Exception as e:
                    print(f"   ⚠ Error selecting {var_name}: {str(e)[:50]}")
            
            time.sleep(2)
            
            # Step 5: Click CSV button
            print("   Clicking CSV button...")
            
            try:
                csv_button = wait.until(
                    EC.element_to_be_clickable((By.ID, "btnCsv"))
                )
                csv_button.click()
                
                # Check for alert
                time.sleep(2)
                try:
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    print(f"   ⚠ Alert: {alert_text}")
                    alert.accept()
                    raise ValueError(f"CSV download failed: {alert_text}")
                except:
                    pass  # No alert means success
                
                print("   ✓ CSV download initiated")
                time.sleep(3)
                
                # Find and read downloaded CSV
                df = self._find_and_read_csv()
                
                if df is not None and len(df) > 0:
                    print(f"   ✓ Successfully read {len(df)} rows")
                    return df
                else:
                    raise ValueError("No data in CSV file")
                    
            except Exception as e:
                print(f"   CSV method failed: {str(e)[:100]}")
                raise
        
        except Exception as e:
            print(f"   ✗ Error: {e}")
            
            try:
                screenshot_path = "/tmp/alberta_acis_error.png"
                self.driver.save_screenshot(screenshot_path)
                print(f"   Screenshot: {screenshot_path}")
            except:
                pass
            
            raise ValueError(f"Failed to fetch data: {e}")
    
    def _find_and_read_csv(self):
        """Find and read downloaded CSV"""
        import os
        import glob
        
        possible_dirs = [
            tempfile.gettempdir(),
            os.path.expanduser('~/Downloads'),
            '/tmp',
        ]
        
        for directory in possible_dirs:
            if not os.path.exists(directory):
                continue
            
            # Look for CSV files
            csv_pattern = os.path.join(directory, '*.csv')
            csv_files = glob.glob(csv_pattern)
            
            if csv_files:
                # Get most recent
                csv_files.sort(key=os.path.getmtime, reverse=True)
                
                for csv_path in csv_files[:3]:  # Check 3 most recent
                    # Check if recent (within last 10 seconds)
                    if time.time() - os.path.getmtime(csv_path) < 10:
                        print(f"   Reading: {csv_path}")
                        try:
                            df = pd.read_csv(csv_path, encoding="latin-1")
                            df = self._clean_dataframe(df)
                            if len(df) > 0:
                                return df
                        except Exception as e:
                            print(f"   Error reading {csv_path}: {e}")
                            continue
        
        return None
    
    def _clean_dataframe(self, df):
        """Clean and standardize the DataFrame"""
        
        if df.empty:
            return df
        
        # Column mapping (case-insensitive)
        column_mapping = {
            'date (local standard time)': 'Date',
            'date': 'Date',
            'day': 'Date',
            'air temp. max. (°c)': 'Tmax',
            'maximum temperature': 'Tmax',
            'max temp': 'Tmax',
            'air temp. min. (°c)': 'Tmin',
            'minimum temperature': 'Tmin',
            'min temp': 'Tmin',
            'precip. (mm)': 'Precipitation',
            'precipitation': 'Precipitation',
            'et. std-grass (mm)': 'ET_ACIS',
            'reference evapotranspiration': 'ET_ACIS',
            'ref et': 'ET_ACIS',
            'evapotranspiration': 'ET_ACIS',
            'relative humidity avg. (%)': 'RH',
            'rel. humidity ave. (%)': 'RH',
            'relative humidity': 'RH',
            'humidity': 'RH',
            'wind speed 10 m avg. (km/h)': 'Wind_Speed_kmh',
            'wind speed inst. (km/h)': 'Wind_Speed_kmh',
            'wind speed': 'Wind_Speed_kmh',
        }
        
        # Normalize column names
        df.columns = df.columns.str.strip().str.lower()
        df = df.rename(columns=column_mapping)
        
        # Parse date
        if 'Date' in df.columns:
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        
        # Convert numeric columns
        for col in ['Tmax', 'Tmin', 'Precipitation', 'ET_ACIS', 'RH', 'Wind_Speed_kmh']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Convert wind speed from km/h to m/s, and from 10m to 2m height
        if 'Wind_Speed_kmh' in df.columns:
            # Convert km/h to m/s
            u10 = df['Wind_Speed_kmh'] / 3.6
            
            # Convert from 10m height to 2m height using logarithmic wind profile
            # u2 = u10 * (4.87 / ln(67.8*10 - 5.42))
            # Simplified: u2 ≈ u10 * 0.748
            df['Wind_Speed'] = u10 * 0.748
            df['u2'] = df['Wind_Speed']
        
        # Remove rows without date
        if 'Date' in df.columns:
            df = df.dropna(subset=['Date'])
            df = df.sort_values('Date').reset_index(drop=True)
        
        print(f"\n   📊 Columns: {df.columns.tolist()}")
        if 'ET_ACIS' in df.columns:
            et_count = df['ET_ACIS'].notna().sum()
            print(f"   ✓ Reference ET: {et_count}/{len(df)} values")
        else:
            print(f"   ⚠ No Reference ET column!")
        
        return df


def fetch_alberta_acis_data(station_name, start_date, end_date):
    """Convenience function to fetch data"""
    
    scraper = AlbertaACISScraper(headless=True)
    
    try:
        df = scraper.fetch_data(station_name, start_date, end_date)
        return df
    finally:
        scraper.close()


if __name__ == "__main__":
    print("Testing Alberta ACIS Scraper...")
    
    try:
        df = fetch_alberta_acis_data(
            station_name='Lethbridge',
            start_date='2024-01-01',
            end_date='2024-01-10'
        )
        
        print("\n✅ SUCCESS!")
        print(f"\nFirst rows:")
        print(df.head())
        
        if 'ET_ACIS' in df.columns:
            print(f"\nReference ET range: {df['ET_ACIS'].min():.2f} to {df['ET_ACIS'].max():.2f} mm/day")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")

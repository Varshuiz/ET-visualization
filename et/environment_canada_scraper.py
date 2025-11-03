"""
Environment Canada Weather Forecast Scraper - RSS Feed Version
Fetches precipitation forecasts using official Environment Canada RSS feeds
"""

import requests
import xml.etree.ElementTree as ET
import pandas as pd
import re
from datetime import datetime, timedelta


class EnvironmentCanadaScraper:
    """
    Scraper for Environment Canada weather forecasts using RSS feeds
    """
    
    # Major Alberta cities and their RSS codes
    # environment_canada_scraper.py - Update the LOCATION_CODES dictionary

class EnvironmentCanadaScraper:
    """
    Scraper for Environment Canada weather forecasts using RSS feeds
    """
    
    # Comprehensive list of Alberta cities with Environment Canada RSS codes
    LOCATION_CODES = {
        # Major Cities
        'Calgary': 'ab-52',
        'Edmonton': 'ab-50',
        'Lethbridge': 'ab-30',
        'Red Deer': 'ab-29',
        'Medicine Hat': 'ab-38',
        'Grande Prairie': 'ab-72',
        'Fort McMurray': 'ab-20',
        
        # Medium Cities
        'Airdrie': 'ab-1',
        'St. Albert': 'ab-43',
        'Spruce Grove': 'ab-44',
        'Lloydminster': 'ab-32',
        'Camrose': 'ab-10',
        'Brooks': 'ab-8',
        'Okotoks': 'ab-39',
        'Cochrane': 'ab-12',
        'Chestermere': 'ab-11',
        'Strathmore': 'ab-45',
        'Beaumont': 'ab-5',
        'Fort Saskatchewan': 'ab-19',
        'Cold Lake': 'ab-13',
        'High River': 'ab-25',
        'Wetaskiwin': 'ab-48',
        'Sylvan Lake': 'ab-46',
        'Leduc': 'ab-28',
        'Stony Plain': 'ab-45',
        
        # Mountain/Resort Towns
        'Banff': 'ab-94',
        'Jasper': 'ab-36',
        'Canmore': 'ab-9',
        
        # Northern Alberta
        'Peace River': 'ab-40',
        'Slave Lake': 'ab-42',
        'Whitecourt': 'ab-49',
        'Hinton': 'ab-26',
        'High Level': 'ab-24',
        'Fort Chipewyan': 'ab-18',
        'Rainbow Lake': 'ab-41',
        
        # Central Alberta
        'Drumheller': 'ab-15',
        'Olds': 'ab-39',
        'Innisfail': 'ab-27',
        'Ponoka': 'ab-40',
        'Lacombe': 'ab-27',
        'Rimbey': 'ab-41',
        'Rocky Mountain House': 'ab-42',
        
        # Southern Alberta
        'Taber': 'ab-47',
        'Vauxhall': 'ab-48',
        'Coaldale': 'ab-12',
        'Picture Butte': 'ab-40',
        'Vulcan': 'ab-48',
        'Claresholm': 'ab-12',
        'Pincher Creek': 'ab-40',
        'Cardston': 'ab-10',
        'Fort Macleod': 'ab-19',
        'Blairmore': 'ab-7',
        'Crowsnest Pass': 'ab-14',
        
        # Eastern Alberta
        'Vegreville': 'ab-47',
        'Vermilion': 'ab-48',
        'Wainwright': 'ab-48',
        'Provost': 'ab-41',
        'Coronation': 'ab-13',
        'Hanna': 'ab-23',
        'Oyen': 'ab-39',
        
        # West-Central Alberta
        'Edson': 'ab-16',
        'Drayton Valley': 'ab-15',
        'Mayerthorpe': 'ab-37',
        'Barrhead': 'ab-4',
        'Westlock': 'ab-49',
        'Athabasca': 'ab-3',
        
        # Northeast Alberta
        'Bonnyville': 'ab-7',
        'St. Paul': 'ab-43',
        'Lac La Biche': 'ab-27',
    }
    
    def get_location_code(self, city_name):
        """Get RSS feed code for a city"""
        
        # Try exact match
        if city_name in self.LOCATION_CODES:
            return self.LOCATION_CODES[city_name]
        
        # Try case-insensitive match
        for city, code in self.LOCATION_CODES.items():
            if city.upper() == city_name.upper():
                return code
        
        # Default to Calgary
        print(f"⚠ Location '{city_name}' not found, using Calgary")
        return self.LOCATION_CODES['Calgary']
    
    def fetch_forecast(self, city_name='Calgary', days=5):
        """
        Fetch weather forecast from Environment Canada RSS feed
        
        Parameters:
        - city_name: Name of the city (e.g., 'Calgary', 'Edmonton')
        - days: Number of forecast periods to fetch (default 5)
        
        Returns:
        - DataFrame with Date, Period, Temp_High, Temp_Low, Precipitation_mm, Forecast
        """
        
        try:
            print(f"\n🌐 Fetching forecast from Environment Canada RSS...")
            print(f"   Location: {city_name}")
            print(f"   Forecast periods: {days}")
            
            # Get location code
            location_code = self.get_location_code(city_name)
            
            # Environment Canada RSS feed URL
            rss_url = f"https://weather.gc.ca/rss/city/{location_code}_e.xml"
            print(f"   URL: {rss_url}")
            
            # Fetch RSS feed
            response = requests.get(rss_url, timeout=10)
            response.raise_for_status()
            
            # Parse XML
            root = ET.fromstring(response.content)
            
            # Namespace for Atom feed
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            # Find all forecast entries
            entries = root.findall('.//atom:entry', ns)
            
            print(f"   Found {len(entries)} forecast entries")
            
            forecast_data = []
            
            for entry in entries[:days]:
                title = entry.find('atom:title', ns)
                summary = entry.find('atom:summary', ns)
                
                if title is not None and summary is not None:
                    period = title.text
                    forecast = summary.text
                    
                    # Extract data
                    temp_high, temp_low = self._extract_temperatures(forecast)
                    precip = self._extract_precipitation(forecast)
                    
                    forecast_data.append({
                        'Period': period,
                        'Temp_High': temp_high,
                        'Temp_Low': temp_low,
                        'Precipitation_mm': precip,
                        'Forecast': forecast
                    })
                    
                    print(f"   ✓ {period}: {precip:.1f}mm precip")
            
            # Convert to DataFrame
            if forecast_data:
                df = pd.DataFrame(forecast_data)
                
                # Add dates (starting from today)
                today = datetime.now()
                dates = [today + timedelta(days=i) for i in range(len(df))]
                df['Date'] = dates
                
                # Reorder columns
                df = df[['Date', 'Period', 'Temp_High', 'Temp_Low', 'Precipitation_mm', 'Forecast']]
                
                print(f"\n   ✅ Successfully fetched {len(df)} forecast periods\n")
                
                return df
            else:
                raise ValueError("No forecast data found")
        
        except Exception as e:
            print(f"   ❌ Error: {e}\n")
            raise ValueError(f"Failed to fetch forecast: {e}")
        

    def debug_forecast(self, city_name='Calgary', days=5):
        """
        Debug function to see raw RSS feed data
        """
        try:
            location_code = self.get_location_code(city_name)
            rss_url = f"https://weather.gc.ca/rss/city/{location_code}_e.xml"
            
            print(f"\n{'='*80}")
            print(f"DEBUG: Fetching from {rss_url}")
            print(f"{'='*80}\n")
            
            response = requests.get(rss_url, timeout=10)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            entries = root.findall('.//atom:entry', ns)
            
            for i, entry in enumerate(entries[:days], 1):
                title = entry.find('atom:title', ns)
                summary = entry.find('atom:summary', ns)
                
                print(f"\n{'='*80}")
                print(f"ENTRY #{i}")
                print(f"{'='*80}")
                
                if title is not None:
                    print(f"TITLE: {title.text}")
                
                if summary is not None:
                    print(f"\nFULL TEXT:\n{summary.text}")
                
                # Try extraction
                if summary is not None:
                    temp_high, temp_low = self._extract_temperatures(summary.text)
                    precip = self._extract_precipitation(summary.text)
                    
                    print(f"\nEXTRACTED:")
                    print(f"  High: {temp_high}")
                    print(f"  Low: {temp_low}")
                    print(f"  Precipitation: {precip} mm")
                
            return True
            
        except Exception as e:
            print(f"Error: {e}")
            return False

    
    def _extract_precipitation(self, text):
        """Extract precipitation amount from forecast text"""
        
        if not text:
            return 0.0
        
        # Pattern 1: "X mm" or "X to Y mm"
        mm_pattern = r'(\d+(?:\.\d+)?)\s*(?:to\s*(\d+(?:\.\d+)?))?\s*mm'
        matches = re.findall(mm_pattern, text, re.IGNORECASE)
        
        if matches:
            amounts = []
            for match in matches:
                if match[1]:  # Range like "5 to 10"
                    amounts.append(float(match[1]))
                else:  # Single value
                    amounts.append(float(match[0]))
            return max(amounts) if amounts else 0.0
        
        # Pattern 2: Percentage of precipitation
        percent_pattern = r'(\d+)\s*percent'
        percent_matches = re.findall(percent_pattern, text, re.IGNORECASE)
        
        if percent_matches:
            prob = int(percent_matches[0])
            # Estimate amount based on probability
            if prob >= 80:
                return 10.0
            elif prob >= 60:
                return 5.0
            elif prob >= 40:
                return 2.0
            elif prob >= 20:
                return 0.5
        
        # Pattern 3: Keywords
        text_lower = text.lower()
        if any(word in text_lower for word in ['rain', 'showers', 'drizzle']):
            if 'heavy' in text_lower:
                return 15.0
            elif 'light' in text_lower:
                return 2.0
            else:
                return 5.0
        
        if 'snow' in text_lower:
            if 'heavy' in text_lower:
                return 20.0
            elif 'light' in text_lower:
                return 3.0
            else:
                return 8.0
        
        return 0.0
    
    def _extract_temperatures(self, text):
        """Extract high and low temperatures from forecast text"""
        
        if not text:
            return None, None
        
        temp_high = None
        temp_low = None
        
        text_lower = text.lower()
        
        # HIGH TEMPERATURE PATTERNS
        # Pattern 1: "High plus X"
        match = re.search(r'high\s+plus\s+(\d+)', text_lower)
        if match:
            temp_high = float(match.group(1))
        
        # Pattern 2: "High minus X"
        if temp_high is None:
            match = re.search(r'high\s+minus\s+(\d+)', text_lower)
            if match:
                temp_high = -float(match.group(1))
        
        # Pattern 3: "High X" (plain number)
        if temp_high is None:
            match = re.search(r'high\s+(\d+)', text_lower)
            if match:
                temp_high = float(match.group(1))
        
        # LOW TEMPERATURE PATTERNS
        # Pattern 1: "Low plus X"
        match = re.search(r'low\s+plus\s+(\d+)', text_lower)
        if match:
            temp_low = float(match.group(1))
        
        # Pattern 2: "Low minus X"
        if temp_low is None:
            match = re.search(r'low\s+minus\s+(\d+)', text_lower)
            if match:
                temp_low = -float(match.group(1))
        
        # Pattern 3: "Low X" (plain number - assume negative if no plus/minus)
        if temp_low is None:
            match = re.search(r'low\s+(\d+)', text_lower)
            if match:
                # When just "Low X" with no plus/minus, it's typically negative in Canadian winter
                # but we'll leave it positive and let context determine
                temp_low = float(match.group(1))
        
        return temp_high, temp_low

def fetch_env_canada_forecast(city_name='Calgary', days=5):
    """
    Convenience function to fetch Environment Canada forecast
    
    Parameters:
    - city_name: Name of Alberta city
    - days: Number of forecast periods (default 5)
    
    Returns:
    - DataFrame with forecast data including precipitation
    """
    
    scraper = EnvironmentCanadaScraper()
    return scraper.fetch_forecast(city_name, days)


def print_precipitation_forecast(city_name='Calgary', days=5):
    """
    Print precipitation forecast in a nice format
    """
    
    print(f"\n{'='*70}")
    print(f"🌧️  Environment Canada Precipitation Forecast")
    print(f"📍 {city_name}, Alberta")
    print(f"{'='*70}")
    
    try:
        df = fetch_env_canada_forecast(city_name, days)
        
        for idx, row in df.iterrows():
            date_str = row['Date'].strftime('%A, %B %d, %Y')
            period = row['Period']
            precip = row['Precipitation_mm']
            temp_high = row['Temp_High']
            temp_low = row['Temp_Low']
            
            print(f"\n📅 {date_str}")
            print(f"   {period}")
            
            if temp_high or temp_low:
                high_str = f"{temp_high:.0f}°C" if temp_high else "N/A"
                low_str = f"{temp_low:.0f}°C" if temp_low else "N/A"
                print(f"   🌡️  High: {high_str}  |  Low: {low_str}")
            
            print(f"   💧 Precipitation: {precip:.1f} mm")
            
            if len(row['Forecast']) > 0:
                forecast_short = row['Forecast'][:150] + "..." if len(row['Forecast']) > 150 else row['Forecast']
                print(f"   📝 {forecast_short}")
        
        total_precip = df['Precipitation_mm'].sum()
        print(f"\n{'='*70}")
        print(f"📊 Total Projected Precipitation: {total_precip:.1f} mm over {len(df)} periods")
        print(f"{'='*70}\n")
        
        return df
        
    except Exception as e:
        print(f"\n❌ Error fetching forecast: {e}\n")
        return None




    
if __name__ == "__main__":
    scraper = EnvironmentCanadaScraper()
    
    print("\n" + "="*80)
    print("TESTING CALGARY FORECAST")
    print("="*80)
    scraper.debug_forecast('Calgary', days=10)
    
    print("\n\n" + "="*80)
    print("TESTING LETHBRIDGE FORECAST")
    print("="*80)
    scraper.debug_forecast('Lethbridge', days=10)
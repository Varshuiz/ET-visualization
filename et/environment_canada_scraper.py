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
            
            print(f"   Found {len(entries)} total RSS entries")
            
            forecast_data = []
            
            # Filter to only actual forecast periods - get more than needed
            for entry in entries:
                title = entry.find('atom:title', ns)
                summary = entry.find('atom:summary', ns)
                
                if title is not None and summary is not None:
                    period = title.text
                    forecast = summary.text
                    
                    # Skip non-forecast entries
                    skip_keywords = [
                        'current conditions',
                        'observed at',
                        'warnings',
                        'watches',
                        'statements',
                        'advisories',
                        'ended:',
                        'weather shortcuts'
                    ]
                    
                    period_lower = period.lower()
                    if any(keyword in period_lower for keyword in skip_keywords):
                        print(f"   ⏭️  Skipping: {period}")
                        continue
                    
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
                
                # Group by day
                df = self._group_by_day(df, days)
                
                print(f"\n   ✅ Successfully fetched {len(df)} daily forecasts\n")
                
                return df
            else:
                raise ValueError("No forecast data found")
        
        except Exception as e:
            print(f"   ❌ Error: {e}\n")
            raise ValueError(f"Failed to fetch forecast: {e}")
    
    def _group_by_day(self, df, max_days=5):
        """
        Group day and night forecasts into single daily records
        High = daytime temp, Low = nighttime temp
        """
        today = datetime.now()
        daily_data = {}  # Use dict to group by day name
        
        for idx, row in df.iterrows():
            period = row['Period'].lower()
            
            # Determine if this is a night or day forecast
            is_night = 'night' in period or 'tonight' in period
            
            # Extract day name from period
            day_match = re.search(r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', period, re.IGNORECASE)
            
            # Handle "Tonight" separately
            if 'tonight' in period:
                day_name = 'Tonight'
            elif day_match:
                day_name = day_match.group(1).title()
            else:
                continue  # Skip if we can't identify the day
            
            # Initialize day if not exists
            if day_name not in daily_data:
                daily_data[day_name] = {
                    'day_name': day_name,
                    'temp_high': None,
                    'temp_low': None,
                    'precipitation': 0.0,
                    'forecast_parts': []
                }
            
            # Add data based on whether it's day or night
            if is_night:
                # Night forecast provides the LOW temperature
                if row['Temp_Low'] is not None:
                    daily_data[day_name]['temp_low'] = row['Temp_Low']
            else:
                # Day forecast provides the HIGH temperature
                if row['Temp_High'] is not None:
                    daily_data[day_name]['temp_high'] = row['Temp_High']
            
            # Accumulate precipitation from both day and night
            daily_data[day_name]['precipitation'] += row['Precipitation_mm']
            daily_data[day_name]['forecast_parts'].append(row['Forecast'])
        
        # Convert to list and limit to max_days
        daily_forecasts = list(daily_data.values())[:max_days]
        
        # Convert to DataFrame with dates
        result_data = []
        for i, day_data in enumerate(daily_forecasts):
            date = today + timedelta(days=i)
            
            # Combine forecast parts into one description
            combined_forecast = ' '.join(day_data['forecast_parts'])
            
            result_data.append({
                'Date': date,
                'Period': day_data['day_name'],
                'Temp_High': day_data['temp_high'],
                'Temp_Low': day_data['temp_low'],
                'Precipitation_mm': day_data['precipitation'],
                'Forecast': combined_forecast
            })
        
        return pd.DataFrame(result_data)
        

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
            
            for i, entry in enumerate(entries[:15], 1):  # Show more entries
                title = entry.find('atom:title', ns)
                summary = entry.find('atom:summary', ns)
                
                print(f"\n{'='*80}")
                print(f"ENTRY #{i}")
                print(f"{'='*80}")
                
                if title is not None:
                    print(f"TITLE: {title.text}")
                
                if summary is not None:
                    print(f"\nFULL TEXT:\n{summary.text[:200]}...")
                
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
        
        # Helper function to convert word numbers to float
        def word_to_number(word):
            word_map = {
                'zero': 0,
                'one': 1,
                'two': 2,
                'three': 3,
                'four': 4,
                'five': 5,
                'six': 6,
                'seven': 7,
                'eight': 8,
                'nine': 9,
                'ten': 10,
                'eleven': 11,
                'twelve': 12,
                'thirteen': 13,
                'fourteen': 14,
                'fifteen': 15,
                'sixteen': 16,
                'seventeen': 17,
                'eighteen': 18,
                'nineteen': 19,
                'twenty': 20,
                'thirty': 30,
                'forty': 40,
                'fifty': 50
            }
            return word_map.get(word, None)
        
        # HIGH TEMPERATURE PATTERNS
        # Pattern 1: "High zero"
        match = re.search(r'high\s+(zero|one|two|three|four|five|six|seven|eight|nine|ten)', text_lower)
        if match:
            temp_high = word_to_number(match.group(1))
        
        # Pattern 2: "High plus X"
        if temp_high is None:
            match = re.search(r'high\s+plus\s+(\d+)', text_lower)
            if match:
                temp_high = float(match.group(1))
        
        # Pattern 3: "High minus X"
        if temp_high is None:
            match = re.search(r'high\s+minus\s+(\d+)', text_lower)
            if match:
                temp_high = -float(match.group(1))
        
        # Pattern 4: "High X" (plain number)
        if temp_high is None:
            match = re.search(r'high\s+(\d+)', text_lower)
            if match:
                temp_high = float(match.group(1))
        
        # LOW TEMPERATURE PATTERNS
        # Pattern 1: "Low zero"
        match = re.search(r'low\s+(zero|one|two|three|four|five|six|seven|eight|nine|ten)', text_lower)
        if match:
            temp_low = word_to_number(match.group(1))
        
        # Pattern 2: "Low plus X"
        if temp_low is None:
            match = re.search(r'low\s+plus\s+(\d+)', text_lower)
            if match:
                temp_low = float(match.group(1))
        
        # Pattern 3: "Low minus X"
        if temp_low is None:
            match = re.search(r'low\s+minus\s+(\d+)', text_lower)
            if match:
                temp_low = -float(match.group(1))
        
        # Pattern 4: "Low X" (plain number)
        if temp_low is None:
            match = re.search(r'low\s+(\d+)', text_lower)
            if match:
                temp_low = float(match.group(1))
        
        # Pattern 5: "Temperature rising to minus X" or "Temperature rising to plus X"
        if temp_low is None:
            match = re.search(r'temperature\s+rising\s+to\s+minus\s+(\d+)', text_lower)
            if match:
                temp_low = -float(match.group(1))
        
        if temp_low is None:
            match = re.search(r'temperature\s+rising\s+to\s+plus\s+(\d+)', text_lower)
            if match:
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
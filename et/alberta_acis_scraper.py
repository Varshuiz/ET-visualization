import io
import re
from urllib.parse import urljoin

import pandas as pd
import requests


class AlbertaACISScraper:
    """HTTP client for Alberta ACIS weather data (no browser automation)."""
    
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
        'Medicine Lake Auto': 'Medicine Lake Auto',
    }
    
    def __init__(self, headless=True):
        self.headless = headless  # kept for backward compatibility
        self.base_url = "https://acis.alberta.ca/acis/weather-data-viewer.jsp"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        
    def setup_driver(self):
        """No-op kept for compatibility with old call sites."""
        return None
        
    def close(self):
        """Close HTTP session."""
        self.session.close()
    
    def fetch_data(self, station_name, start_date, end_date):
        """Fetch weather data from Alberta ACIS without Selenium."""
        try:
            print("\n🌐 Fetching data from Alberta ACIS via HTTP...")
            print(f"   Station: {station_name}")
            print(f"   Date range: {start_date} to {end_date}")

            full_station_name = self.STATION_MAPPING.get(station_name, station_name)

            page_html = self._get_viewer_html()
            station_value, resolved_station = self._resolve_station_value(
                page_html, full_station_name, station_name
            )
            if not station_value:
                raise ValueError(f"Station '{station_name}' not found in ACIS station list")
            print(f"   ✓ Selected: {resolved_station}")

            payload = self._build_payload(station_value, start_date, end_date)
            df = self._submit_for_data(payload)
            if df is None or df.empty:
                raise ValueError("ACIS returned no data for this query")

            print(f"   ✓ Successfully read {len(df)} rows")
            return df
        except Exception as e:
            print(f"   ✗ Error: {e}")
            raise ValueError(f"Failed to fetch data: {e}")

    def _get_viewer_html(self):
        response = self.session.get(self.base_url, timeout=45)
        response.raise_for_status()
        return response.text

    def _resolve_station_value(self, html, full_station_name, fallback_name):
        # Parse station options from the select list.
        # ACIS markup can vary: quoted value, unquoted value, or no value.
        options = re.findall(
            r'<option\b([^>]*)>(.*?)</option>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def normalize(text):
            return re.sub(r"\s+", " ", text or "").strip()

        for attrs, label_html in options:
            value_match = re.search(
                r'\bvalue\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
                attrs,
                flags=re.IGNORECASE,
            )
            value = ""
            if value_match:
                value = value_match.group(1) or value_match.group(2) or value_match.group(3) or ""

            label = normalize(re.sub(r"<[^>]+>", "", label_html))
            if not label:
                continue
            if full_station_name.lower() in label.lower() or fallback_name.lower() in label.lower():
                return (normalize(value) or label), label

        # Don't fail if station list cannot be parsed from HTML.
        # Use the mapped station name directly so the POST can still be attempted.
        fallback_station = full_station_name or fallback_name
        return normalize(fallback_station), normalize(fallback_station)

    def _build_payload(self, station_value, start_date, end_date):
        # Uses same input ids currently selected by Selenium flow.
        payload = {
            "acis_stations": station_value,
            "intervalSelector": "Daily",
            "calendarDataStart": start_date,
            "calendarDataEnd": end_date,
            "cb_pop_at_max": "on",
            "cb_pop_at_min": "on",
            "cb_pop_pr_b": "on",
            "cb_pop_et_std_grass": "on",
            "cb_pop_hu_ave": "on",
            "cb_pop_ws_inst": "on",
        }
        return payload

    def _submit_for_data(self, payload):
        # Try CSV/static-table actions first; ACIS may wire them with different submit names.
        action_variants = [
            {"btnCsv": "CSV"},
            {"btnCsv": "View CSV"},
            {"btnStaticTable": "Static Table"},
            {"btnTable": "Table"},
            {},
        ]

        for action_payload in action_variants:
            merged = dict(payload)
            merged.update(action_payload)
            try:
                response = self.session.post(self.base_url, data=merged, timeout=60)
                if response.status_code == 403 and "modern, standards-compliant web-browser" in response.text:
                    raise ValueError(
                        "ACIS blocked automated server requests (HTTP 403). "
                        "Their endpoint currently requires interactive browser access."
                    )
                response.raise_for_status()
                df = self._extract_dataframe_from_response(response)
                if df is not None and not df.empty:
                    return df
            except Exception:
                continue
        raise ValueError(
            "Could not retrieve ACIS data using HTTP form submission "
            "(likely blocked by ACIS anti-automation checks/captcha)."
        )

    def _extract_dataframe_from_response(self, response):
        content_type = (response.headers.get("Content-Type") or "").lower()
        text = response.text

        # 1) Direct CSV response
        if "text/csv" in content_type or "application/csv" in content_type:
            return self._read_csv_text(text)

        # 2) CSV-like body even if mislabeled
        if "date" in text.lower() and "," in text[:5000]:
            df = self._read_csv_text(text)
            if df is not None and not df.empty:
                return df

        # 3) Linked CSV in response HTML
        csv_links = re.findall(r'href=["\']([^"\']+\.csv[^"\']*)["\']', text, flags=re.IGNORECASE)
        for link in csv_links:
            try:
                csv_url = urljoin(self.base_url, link)
                csv_resp = self.session.get(csv_url, timeout=45)
                csv_resp.raise_for_status()
                df = self._read_csv_text(csv_resp.text)
                if df is not None and not df.empty:
                    return df
            except Exception:
                continue

        # 4) HTML table response
        try:
            tables = pd.read_html(io.StringIO(text))
        except Exception:
            tables = []
        for table in tables:
            cleaned = self._clean_dataframe(table.copy())
            if cleaned is not None and not cleaned.empty and "Date" in cleaned.columns:
                return cleaned
        return None

    def _read_csv_text(self, csv_text):
        try:
            df = pd.read_csv(io.StringIO(csv_text), encoding="latin-1")
            return self._clean_dataframe(df)
        except Exception:
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
    """Convenience function to fetch ACIS data without Selenium."""
    
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

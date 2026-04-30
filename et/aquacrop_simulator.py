"""
AquaCrop Integration Module - Following Official Documentation
Simulates crop growth, water use, and yield predictions
"""

from aquacrop import (
    AquaCropModel, 
    Soil, 
    Crop, 
    InitialWaterContent,
    IrrigationManagement
)
import pandas as pd
import numpy as np
import traceback


class AquaCropSimulator:
    """
    Wrapper for AquaCrop-OSPy following official documentation format
    """
    
    AVAILABLE_CROPS = {
        'Wheat': 'Wheat',
        'Maize': 'Maize',
        'Rice': 'Rice',
        'Cotton': 'Cotton',
        'Tomato': 'Tomato',
        'Potato': 'Potato',
        'Sunflower': 'Sunflower',
        'Soybean': 'Soybean',
        'Barley': 'Barley',
        'SugarBeet': 'SugarBeet',
    }
    
    SOIL_TYPES = {
        'Sandy Loam': 'SandyLoam',
        'Loam': 'Loam',
        'Clay Loam': 'ClayLoam',
        'Sandy Clay Loam': 'SandyClayLoam',
        'Silty Clay': 'SiltyClay',
        'Clay': 'Clay',
    }
    
    def __init__(self):
        self.model = None
        self.results = None
    
    def run_simulation(
        self,
        crop_name='Wheat',
        soil_type='Loam',
        start_date='2024/05/01',
        end_date='2024/09/01',
        weather_data=None,
        irrigation_method='rainfed',
        initial_water_content='FC',
    ):
        """
        Run AquaCrop simulation following official format
        """
        
        try:
            # Parse dates
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            
            # AquaCrop format: YYYY/MM/DD for simulation dates
            start_str = start_dt.strftime('%Y/%m/%d')
            end_str = end_dt.strftime('%Y/%m/%d')
            
            # Planting date: MM/DD (as per official documentation)
            planting_date = start_dt.strftime('%m/%d')
            
            print(f"\n{'='*70}")
            print("Running AquaCrop Simulation")
            print(f"{'='*70}")
            print(f"Crop: {crop_name}")
            print(f"Soil: {soil_type}")
            print(f"Period: {start_str} to {end_str}")
            print(f"Irrigation: {irrigation_method}")
            print(f"{'='*70}\n")
            
            # Weather data - MUST be provided from observed/forecast sources.
            if weather_data is None:
                raise ValueError(
                    "AquaCrop requires weather input data. Please upload a weather file "
                    "or use ECCC-backed auto-fetch in the simulation form."
                )
            weather_df = self._prepare_weather_data(weather_data)
            self._log_weather_diagnostics(weather_df, start_dt, end_dt)
            
            # Create components
            soil = Soil(soil_type=self.SOIL_TYPES.get(soil_type, 'Loam'))
            try:
                crop = Crop(crop_name, planting_date=planting_date)
                print(
                    "[AQUACROP_DEBUG] Crop parameters loaded successfully: "
                    f"crop_name={crop_name}, planting_date={planting_date}"
                )
            except Exception:
                print(
                    "[AQUACROP_DEBUG] Crop parameter load failed: "
                    f"crop_name={crop_name}, planting_date={planting_date}"
                )
                print(traceback.format_exc())
                raise
            
            if initial_water_content == 'FC':
                initial_wc = InitialWaterContent(value=['FC'])
            elif initial_water_content == 'WP':
                initial_wc = InitialWaterContent(value=['WP'])
            else:
                initial_wc = InitialWaterContent(value=[initial_water_content])
            
            # Irrigation
            if irrigation_method == 'rainfed':
                irrigation = IrrigationManagement(irrigation_method=0)
            elif irrigation_method == 'full':
                irrigation = IrrigationManagement(irrigation_method=1, SMT=[80] * 4)
            else:
                irrigation = IrrigationManagement(irrigation_method=1, SMT=[60] * 4)
            
            # Create and run model
            print(
                "[AQUACROP_DEBUG] AquaCropModel call params: "
                f"sim_start_time={start_str}, sim_end_time={end_str}, "
                f"crop={crop_name}, soil={self.SOIL_TYPES.get(soil_type, 'Loam')}, "
                f"irrigation_method={irrigation_method}, initial_water_content={initial_water_content}, "
                f"weather_rows={len(weather_df)}, weather_columns={list(weather_df.columns)}"
            )
            model = AquaCropModel(
                sim_start_time=start_str,
                sim_end_time=end_str,
                weather_df=weather_df,
                soil=soil,
                crop=crop,
                initial_water_content=initial_wc,
                irrigation_management=irrigation,
            )
            
            model.run_model(till_termination=True)
            
            # Process results
            results = self._process_results(model)
            
            print("\nSimulation Complete!")
            print(f"   Yield: {results['yield_fresh']:.1f} tonnes/ha")
            print(f"   Water Use: {results['total_et']:.1f} mm")
            print(f"   Water Productivity: {results['water_productivity']:.2f} kg/m³\n")
            
            self.model = model
            self.results = results
            
            return results
            
        except Exception as e:
            print(f"\nError: {e}\n")
            traceback.print_exc()
            raise

    def _log_weather_diagnostics(self, weather_df: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp):
        weather_copy = weather_df.copy()
        weather_copy["Date"] = pd.to_datetime(weather_copy["Date"], errors="coerce")
        nan_counts = weather_copy.isna().sum().to_dict()
        date_min = weather_copy["Date"].min() if "Date" in weather_copy.columns else pd.NaT
        date_max = weather_copy["Date"].max() if "Date" in weather_copy.columns else pd.NaT
        planting_before_weather = bool(pd.notna(date_min) and start_dt.normalize() < date_min.normalize())

        print("[AQUACROP_DEBUG] Weather dataframe columns:", list(weather_copy.columns))
        print("[AQUACROP_DEBUG] Weather dataframe rows:", len(weather_copy))
        print("[AQUACROP_DEBUG] Weather dataframe NaN counts:", nan_counts)
        print("[AQUACROP_DEBUG] Weather dataframe first date:", date_min)
        print("[AQUACROP_DEBUG] Weather dataframe last date:", date_max)
        print(
            "[AQUACROP_DEBUG] Planting before first weather row:",
            planting_before_weather,
            f"(planting={start_dt.normalize()}, first_weather={date_min.normalize() if pd.notna(date_min) else 'NaT'})",
        )
        print("[AQUACROP_DEBUG] Weather dataframe first 5 rows:")
        print(weather_copy.head(5).to_string(index=False))
        print("[AQUACROP_DEBUG] Weather dataframe last 5 rows:")
        print(weather_copy.tail(5).to_string(index=False))
    
    def _generate_sample_weather(self, start_date, end_date):
        """
        Generate weather in OFFICIAL AquaCrop format:
        DataFrame with columns: MinTemp, MaxTemp, Precipitation, ReferenceET, Date
        """
        dates = pd.date_range(start_date, end_date, freq='D')
        n = len(dates)
        
        # Seasonal patterns
        doy = dates.dayofyear.values
        base_temp = 15 + 10 * np.sin((doy - 100) * 2 * np.pi / 365)
        
        # Temperature
        max_temp = base_temp + np.random.normal(5, 2, n)
        min_temp = base_temp - np.random.normal(5, 2, n)
        
        # Precipitation
        precip = np.zeros(n)
        rain_days = np.random.choice(n, size=int(n * 0.3), replace=False)
        precip[rain_days] = np.random.exponential(8, len(rain_days))
        
        # Reference ET
        ref_et = 2 + 4 * np.sin((doy - 100) * 2 * np.pi / 365) + np.random.normal(0, 0.5, n)
        ref_et = np.clip(ref_et, 0.5, 8)
        
        # OFFICIAL FORMAT (order matters!)
        weather_df = pd.DataFrame({
            'MinTemp': np.clip(min_temp, -5, 30),
            'MaxTemp': np.clip(max_temp, 0, 40),
            'Precipitation': np.clip(precip, 0, 50),
            'ReferenceET': ref_et,
            'Date': dates,  # datetime objects as column
        })
        
        return weather_df
    
    def _prepare_weather_data(self, df):
        """Convert uploaded data to official format"""
        weather_df = pd.DataFrame()
        
        # Column mapping
        mapping = {
            'MinTemp': ['Min Temp (C)', 'Tmin', 'MinTemp', 'min_temp'],
            'MaxTemp': ['Max Temp (C)', 'Tmax', 'MaxTemp', 'max_temp'],
            'Precipitation': ['Precipitation (mm)', 'Precip', 'Precipitation', 'rain'],
            'ReferenceET': ['ET (mm)', 'ET0', 'ReferenceET', 'ETo'],
        }
        
        for target, options in mapping.items():
            found = False
            for opt in options:
                if opt in df.columns:
                    weather_df[target] = df[opt]
                    found = True
                    break
            if not found:
                raise ValueError(f"Missing required weather column for AquaCrop: {target}")
        
        # Date as datetime column
        if 'Date' not in df.columns:
            raise ValueError("Missing required weather column for AquaCrop: Date")
        weather_df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        if weather_df['Date'].isna().any():
            raise ValueError("Weather data contains invalid Date values")
        
        return weather_df
    
    def _process_results(self, model):
        """Extract results using official output format"""
        outputs = getattr(model, "_outputs", None) or getattr(model, "Outputs", None)
        if outputs is None:
            return {
                "yield_fresh": 0,
                "yield_dry": 0,
                "biomass": 0,
                "total_irrigation": 0,
                "total_et": 0,
                "transpiration": 0,
                "evaporation": 0,
                "total_rainfall": 0,
                "water_productivity": 0,
                "irrigation_efficiency": 0,
                "canopy_cover_max": 0,
                "growing_degree_days": 0,
                "daily_output": pd.DataFrame(),
                "water_flux": pd.DataFrame(),
                "reached_maturity": False,
                "partial_results": True,
                "result_note": "Simulation ended before crop maturity — showing partial growth results only.",
            }

        final_stats = getattr(outputs, "final_stats", pd.DataFrame())
        crop_growth = getattr(outputs, "crop_growth", pd.DataFrame())
        water_flux = getattr(outputs, "water_flux", pd.DataFrame())
        has_final_stats = len(final_stats) > 0
        
        last_season = final_stats.iloc[-1] if has_final_stats else None
        yield_col = next((col for col in final_stats.columns if 'dry yield' in col.lower()), None) if has_final_stats else None
        irrigation_col = (
            next((col for col in final_stats.columns if 'irrigation' in col.lower()), None)
            if has_final_stats
            else None
        )

        results = {
            'yield_fresh': last_season[yield_col] if has_final_stats and yield_col else 0,
            'yield_dry': last_season[yield_col] if has_final_stats and yield_col else 0,
            'biomass': crop_growth['biomass'].iloc[-1] if len(crop_growth) > 0 else 0,
            'total_irrigation': last_season[irrigation_col] if has_final_stats and irrigation_col else 0,
            'total_et': (water_flux['Tr'].sum() + water_flux['Es'].sum()) if len(water_flux) > 0 else 0,
            'transpiration': water_flux['Tr'].sum() if len(water_flux) > 0 else 0,
            'evaporation': water_flux['Es'].sum() if len(water_flux) > 0 else 0,
            'total_rainfall': water_flux['Infl'].sum() if len(water_flux) > 0 and 'Infl' in water_flux else 0,
            'water_productivity': 0,
            'irrigation_efficiency': 0,
            'canopy_cover_max': crop_growth['canopy_cover'].max() if len(crop_growth) > 0 else 0,
            'growing_degree_days': crop_growth['gdd_cum'].iloc[-1] if len(crop_growth) > 0 else 0,
            'daily_output': crop_growth,
            'water_flux': water_flux,
            'reached_maturity': bool(has_final_stats),
            'partial_results': not bool(has_final_stats),
            'result_note': (
                "Simulation ended before crop maturity — showing partial growth results only."
                if not has_final_stats
                else None
            ),
        }
        
        # Calculated metrics
        total_water = results['total_irrigation'] + results['total_rainfall']
        if total_water > 0:
            results['water_productivity'] = (results['yield_fresh'] * 1000) / total_water
        
        if results['total_irrigation'] > 0:
            results['irrigation_efficiency'] = (results['transpiration'] / results['total_irrigation']) * 100
        
        return results
    
    def get_growth_chart_data(self):
        """Chart data for growth visualization"""
        if not self.results:
            return None
        
        output = self.results['daily_output']
        if len(output) == 0:
            return None
        
        # Use days after planting (dap) or simple day numbers
        if 'dap' in output.columns:
            dates = [f"Day {int(d)}" for d in output['dap'].tolist()]
        else:
            dates = [f"Day {i+1}" for i in range(len(output))]
            
        return {
            'dates': dates,
            'canopy_cover': output['canopy_cover'].tolist() if 'canopy_cover' in output else [],
            'biomass': output['biomass'].tolist() if 'biomass' in output else [],
            'soil_water': output['Wr'].tolist() if 'Wr' in output else [],
        }
    
    def get_water_balance_data(self):
        """Chart data for water balance"""
        if not self.results:
            return None
        
        water_flux = self.results['water_flux']
        if len(water_flux) == 0:
            return None
        
        # Use days after planting (dap) or simple day numbers
        if 'dap' in water_flux.columns:
            dates = [f"Day {int(d)}" for d in water_flux['dap'].tolist()]
        else:
            dates = [f"Day {i+1}" for i in range(len(water_flux))]
            
        return {
            'dates': dates,
            'precipitation': water_flux['Infl'].tolist() if 'Infl' in water_flux else [],
            'irrigation': water_flux['IrrDay'].tolist() if 'IrrDay' in water_flux else [],
            'transpiration': water_flux['Tr'].tolist() if 'Tr' in water_flux else [],
            'evaporation': water_flux['Es'].tolist() if 'Es' in water_flux else [],
            'drainage': water_flux['DeepPerc'].tolist() if 'DeepPerc' in water_flux else [],
        }


def run_aquacrop_simulation(crop='Wheat', soil='Loam', start_date='2024/05/01',
                            end_date='2024/09/01', irrigation='rainfed', weather_df=None):
    """Convenience function"""
    simulator = AquaCropSimulator()
    results = simulator.run_simulation(
        crop_name=crop,
        soil_type=soil,
        start_date=start_date,
        end_date=end_date,
        irrigation_method=irrigation,
        weather_data=weather_df
    )
    
    results['growth_chart'] = simulator.get_growth_chart_data()
    results['water_balance_chart'] = simulator.get_water_balance_data()
    
    return results
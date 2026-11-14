from unittest.mock import patch

import pandas as pd
from django.test import TestCase, override_settings
from django.urls import reverse


class ETPlannerViewTests(TestCase):
    def _seed_acis_session(self):
        df = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2025-06-01", "2025-06-02", "2025-06-03"]),
                "Tmax": [24.0, 25.0, 23.0],
                "Tmin": [12.0, 11.0, 10.0],
                "RH": [60.0, 62.0, 58.0],
                "u2": [2.0, 2.1, 1.9],
                "Rs": [18.0, 17.5, 16.8],
                "Wind_Speed": [2.0, 2.1, 1.9],
                "Solar_Radiation": [18.0, 17.5, 16.8],
                "Precipitation": [0.0, 1.2, 0.0],
                "ET_PT": [4.0, 4.1, 3.8],
                "ET_PM": [4.2, 4.3, 4.0],
            }
        )
        session = self.client.session
        session["acis_data"] = df.to_json(date_format="iso")
        session["acis_location"] = {
            "description": "Calgary",
            "latitude": 51.0,
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
        }
        session.save()

    def test_weather_data_page_renders(self):
        response = self.client.get(reverse("et:acis_fetch"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prepare ET Inputs")
        self.assertContains(response, "Province")

    def test_et_results_requires_seeded_data(self):
        response = self.client.get(reverse("et:comparison_with_acis"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:acis_fetch"), response.url)

    def _seed_weather_only(self):
        df = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2025-06-01", "2025-06-02", "2025-06-03"]),
                "Tmax": [24.0, 25.0, 23.0],
                "Tmin": [12.0, 11.0, 10.0],
                "RH": [60.0, 62.0, 58.0],
                "Wind_Speed": [2.0, 2.1, 1.9],
                "Solar_Radiation": [18.0, 17.5, 16.8],
            }
        )
        session = self.client.session
        session["acis_data"] = df.to_json(date_format="iso")
        session["acis_location"] = {
            "description": "Calgary",
            "latitude": 51.0,
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
        }
        session.save()

    def _assert_trace_et_matches_results(self, unit):
        results = self.client.get(reverse("et:comparison_with_acis") + f"?unit={unit}")
        self.assertEqual(results.status_code, 200)
        et_data = results.context["et_data"]
        places = results.context["unit_info"]["decimal_places"]
        columns = {
            "pt": "ET_PT",
            "pm": "ET_PM",
            "maule": "ET_Maule",
            "hargreaves": "ET_Hargreaves",
        }
        for slug, column in columns.items():
            page = self.client.get(reverse("et:et_calculation_trace") + f"?method={slug}&unit={unit}")
            self.assertEqual(page.status_code, 200)
            rows = page.context["spec"]["rows"]
            self.assertEqual(len(rows), len(et_data))
            for record, row in zip(et_data, rows):
                expected = f"{float(record[column]):.{places}f}"
                self.assertEqual(row[-1], expected)
                self.assertContains(page, expected)

    def test_results_cards_link_to_formula_page(self):
        self._seed_acis_session()
        response = self.client.get(reverse("et:comparison_with_acis"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("View inputs &amp; formula", html)
        self.assertIn('href="/et/results/calculations/?method=pm&amp;unit=mm"', html)
        for slug in ("pt", "pm", "maule", "hargreaves"):
            self.assertIn(f"method={slug}", html)

    def test_formula_page_requires_session_weather(self):
        response = self.client.get(reverse("et:et_calculation_trace"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:acis_fetch"), response.url)

    def test_formula_page_et_matches_results_page(self):
        self._seed_weather_only()
        self._assert_trace_et_matches_results("mm")
        self._assert_trace_et_matches_results("inches")

    def test_formula_page_keeps_precomputed_et(self):
        self._seed_acis_session()
        results = self.client.get(reverse("et:comparison_with_acis"))
        page = self.client.get(reverse("et:et_calculation_trace") + "?method=pm")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'class="trace-tab active"')
        self.assertContains(page, "Penman-Monteith")
        for record, row in zip(results.context["et_data"], page.context["spec"]["rows"]):
            self.assertEqual(row[-1], f"{float(record['ET_PM']):.1f}")
        pt = self.client.get(reverse("et:et_calculation_trace") + "?method=pt")
        for record, row in zip(results.context["et_data"], pt.context["spec"]["rows"]):
            self.assertEqual(row[-1], f"{float(record['ET_PT']):.1f}")

    def test_formula_page_downloads_csv_and_zip(self):
        import io
        import zipfile

        self._seed_weather_only()
        csv_response = self.client.get(
            reverse("et:et_calculation_trace_download") + "?method=pm&unit=mm"
        )
        self.assertEqual(csv_response.status_code, 200)
        body = csv_response.content.decode("utf-8")
        self.assertIn("Tmax", body)
        self.assertIn("Latitude", body)
        self.assertIn("ET (mm/day)", body)

        zip_response = self.client.get(
            reverse("et:et_calculation_trace_download") + "?method=all&unit=inches"
        )
        self.assertEqual(zip_response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["hargreaves_inputs.csv", "maule_inputs.csv", "pm_inputs.csv", "pt_inputs.csv"],
            )
            pm_csv = archive.read("pm_inputs.csv").decode("utf-8")
            self.assertIn("ET (in/day)", pm_csv)

    def test_vector_parts_match_previous_expressions(self):
        import numpy as np

        from et.et_methods import priestley_taylor_parts_vec, penman_monteith_parts_vec

        tavg = np.array([18.0, 20.5], dtype=np.float32)
        rn = np.array([10.0, 12.0], dtype=np.float32)
        parts = priestley_taylor_parts_vec(tavg, rn)
        delta = 4098.0 * (0.6108 * np.exp((17.27 * tavg) / (tavg + 237.3))) / (tavg + 237.3) ** 2
        np.testing.assert_array_equal(parts["delta"], delta.astype(np.float32))

        tmax = np.array([24.0, 25.0], dtype=np.float32)
        tmin = np.array([12.0, 11.0], dtype=np.float32)
        rh = np.array([60.0, 62.0], dtype=np.float32)
        u2 = np.array([2.0, 2.1], dtype=np.float32)
        rs = np.array([18.0, 17.5], dtype=np.float32)
        ra = np.array([40.0, 40.2], dtype=np.float32)
        pm = penman_monteith_parts_vec(tmax, tmin, rh, u2, rs, ra, elevation=766)
        tmean = (tmax + tmin) / 2.0
        es_tmean = 0.6108 * np.exp((17.27 * tmean) / (tmean + 237.3))
        pm_delta = 4098.0 * es_tmean / (tmean + 237.3) ** 2
        np.testing.assert_array_equal(pm["delta"], pm_delta.astype(np.float32))

    def test_et_results_recomputes_pm_when_column_missing(self):
        """Session weather with PT only should still yield PM for comparison UI."""
        df = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2025-06-01", "2025-06-02"]),
                "Tmax": [22.0, 23.0],
                "Tmin": [10.0, 11.0],
                "RH": [60.0, 58.0],
                "Wind_Speed": [2.0, 2.0],
                "Solar_Radiation": [20.0, 19.0],
                "ET_PT": [3.5, 3.6],
            }
        )
        session = self.client.session
        session["acis_data"] = df.to_json(date_format="iso")
        session["acis_location"] = {
            "description": "Test",
            "latitude": 51.0,
            "start_date": "2025-06-01",
            "end_date": "2025-06-02",
        }
        session.save()
        response = self.client.get(reverse("et:comparison_with_acis"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("PM", response.context["available_methods"])

    @patch("et.views.get_lethbridge_forecast", return_value=[])
    @patch("et.environment_canada_scraper.fetch_env_canada_forecast")
    def test_et_results_renders_from_session_data(self, mock_fetch_ec, _mock_forecast):
        mock_fetch_ec.return_value = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-10"]),
                "Period": ["Today"],
                "Temp_High": [20.0],
                "Temp_Low": [9.0],
                "Precipitation_mm": [0.0],
                "Forecast": ["Sunny"],
            }
        )
        self._seed_acis_session()
        response = self.client.get(reverse("et:comparison_with_acis"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ET Results")
        self.assertContains(response, "Method Statistics")

    def test_forecast_page_get_renders_controls(self):
        response = self.client.get(reverse("et:env_canada_forecast"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crop type")
        self.assertContains(response, "Soil type")
        self.assertContains(response, "Province")
        self.assertContains(response, "British Columbia")

    def test_form_validation_assets_on_setup_forecast_and_aquacrop(self):
        for url_name in ("et:acis_fetch", "et:env_canada_forecast", "et:aquacrop_simulation"):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)
            self.assertContains(response, "form_validation.js")
            self.assertContains(response, "form_validation.css")
            self.assertContains(response, "novalidate")

    @patch("et.views.build_irrigation_confidence_plot", return_value="ZmFrZV9jaGFydA==")
    @patch(
        "et.views.build_historical_confidence",
        return_value={
            "days": [1, 2],
            "p10": [1.0, 2.0],
            "p50": [1.5, 2.5],
            "p90": [2.0, 3.0],
            "scenario_count": 3,
            "total_p10": 2.0,
            "total_p50": 2.5,
            "total_p90": 3.0,
        },
    )
    @patch("et.views.merge_openmeteo_forecast_drivers", side_effect=lambda df, lat, lon: df)
    @patch("et.environment_canada_scraper.fetch_env_canada_forecast")
    def test_forecast_post_uses_crop_and_soil_selection(
        self, mock_fetch_forecast, _mock_merge, _mock_hist, _mock_plot
    ):
        mock_fetch_forecast.return_value = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-10", "2026-05-11", "2026-05-12"]),
                "Period": ["Day 1", "Day 2", "Day 3"],
                "Temp_High": [22.0, 23.0, 24.0],
                "Temp_Low": [10.0, 11.0, 12.0],
                "Precipitation_mm": [1.0, 0.0, 0.5],
                "Forecast": ["Clear", "Sunny", "Cloudy"],
            }
        )
        response = self.client.post(
            reverse("et:env_canada_forecast"),
            {
                "city_name": "Calgary",
                "days": "7",
                "crop_type": "grain_corn",
                "soil_type": "sandy",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["crop_type"], "grain_corn")
        self.assertEqual(response.context["soil_type"], "sandy")
        self.assertContains(response, "Selected Crop")
        self.assertContains(response, "Selected Soil")
        self.assertContains(response, "ET flux")
        self.assertContains(response, "Forecast Results")
        self.assertContains(response, 'id="forecastResultsSection"')
        html = response.content.decode()
        marker_idx = html.find("<!-- forecast-results-start -->")
        hidden_idx = html.find('class="hidden space-y-6"')
        self.assertGreater(marker_idx, hidden_idx, "Results section must follow the setup form block")
        between = html[hidden_idx:marker_idx]
        self.assertGreaterEqual(
            between.count("</div>"),
            between.count("<div"),
            "Setup form wrapper must close before forecast results render",
        )

    def test_forecast_post_empty_data_shows_error(self):
        with patch("et.environment_canada_scraper.fetch_env_canada_forecast", return_value=pd.DataFrame()):
            response = self.client.post(
                reverse("et:env_canada_forecast"),
                {"city_name": "Calgary", "days": "7", "crop_type": "wheat", "soil_type": "loam"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forecast unavailable")
        self.assertNotContains(response, "Forecast Results")

    def test_update_comparison_plot_requires_session_data(self):
        response = self.client.get(
            reverse("et:update_comparison_plot"), {"methods": ["PT"], "unit": "mm"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_update_comparison_plot_returns_plot_with_session_data(self):
        self._seed_acis_session()
        response = self.client.get(
            reverse("et:update_comparison_plot"), {"methods": ["PT", "PM"], "unit": "mm"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("plot_url", payload)
        self.assertTrue(payload["plot_url"])

    def test_base_template_contains_loading_link_handler(self):
        response = self.client.get(reverse("et:acis_fetch"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '[data-loading-link="true"]')

    def test_weather_data_page_shows_backup_upload_section(self):
        response = self.client.get(reverse("et:acis_fetch"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Optional: upload your own weather file")

    def test_method_info_page_renders(self):
        response = self.client.get(reverse("et:method_info"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ET Method Guide")

    def test_comparison_alias_route_renders(self):
        response = self.client.get(reverse("et:comparison"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ET Results")

    @patch("et.views.get_lethbridge_forecast", return_value=[])
    @patch("et.environment_canada_scraper.fetch_env_canada_forecast", return_value=pd.DataFrame())
    def test_et_results_unit_toggle_mm_to_inches_keeps_working(self, _mock_ec, _mock_forecast):
        self._seed_acis_session()
        mm_response = self.client.get(reverse("et:comparison_with_acis"), {"unit": "mm"})
        self.assertEqual(mm_response.status_code, 200)

        inches_response = self.client.get(reverse("et:comparison_with_acis"), {"unit": "inches"})
        self.assertEqual(inches_response.status_code, 200)


class AquaCropHistoricalRangeTests(TestCase):
    def test_default_season_dates_may_to_september(self):
        from et.views import _aquacrop_current_year, _aquacrop_default_season_dates

        start, end = _aquacrop_default_season_dates()
        year = _aquacrop_current_year()
        self.assertEqual(start, f"{year}/05/01")
        self.assertEqual(end, f"{year}/09/30")

    def test_normalize_date_accepts_dashes_and_slashes(self):
        from et.views import _normalize_aquacrop_date_str

        self.assertEqual(_normalize_aquacrop_date_str("2026-05-01"), "2026/05/01")
        self.assertEqual(_normalize_aquacrop_date_str("2026/09/30"), "2026/09/30")

    def test_aquacrop_page_prefills_historical_custom_dates(self):
        response = self.client.get(reverse("et:aquacrop_simulation"))
        self.assertEqual(response.status_code, 200)
        from et.views import _aquacrop_default_season_dates

        start, end = _aquacrop_default_season_dates()
        self.assertContains(response, start)
        self.assertContains(response, end)
        self.assertContains(response, "Historical range")
        self.assertContains(response, "Your Season Data")
        self.assertContains(response, "Season data from")
        self.assertContains(response, "Update week rows")


class AquaCropSeasonDataTests(TestCase):
    def test_weekly_rows_may_through_september(self):
        from et.aquacrop_season_data import build_season_tables, weekly_period_starts
        import pandas as pd

        starts = weekly_period_starts(pd.Timestamp("2026-05-01"), pd.Timestamp("2026-09-30"))
        self.assertGreaterEqual(len(starts), 20)
        self.assertEqual(starts[0].strftime("%Y-%m-%d"), "2026-05-01")

        tables = build_season_tables(
            start_date="2026/05/01",
            end_date="2026/09/30",
            fetch_eccc=False,
        )
        self.assertEqual(len(tables["weather_rows"]), len(starts))
        self.assertIn("/05/01", tables["planting_date"])

    def test_planting_date_shifts_first_week_row(self):
        from et.aquacrop_season_data import build_season_tables

        tables = build_season_tables(
            start_date="2026/05/01",
            end_date="2026/06/30",
            planting_date="2026/05/15",
            fetch_eccc=False,
        )
        self.assertEqual(tables["weather_rows"][0]["week_start"], "2026-05-15")

    def test_aimm_runoff_below_25mm_is_zero(self):
        from et.aquacrop_season_data import aimm_weekly_runoff_mm

        self.assertEqual(aimm_weekly_runoff_mm(10, 20, 28), 0.0)

    def test_effective_irrigation_uses_efficiency(self):
        from et.aquacrop_season_data import effective_irrigation_mm

        self.assertEqual(effective_irrigation_mm(100, 81), 81.0)

    def test_merge_saved_season_table_normalizes_week_keys(self):
        from et.aquacrop_season_data import build_season_tables, merge_saved_season_table

        base = build_season_tables(
            start_date="2026/05/01",
            end_date="2026/06/15",
            fetch_eccc=False,
        )
        saved = {
            "management_rows": [
                {
                    "week_start": "2026/05/01",
                    "gross_irrigation": "25",
                    "effective_irrigation": 20.25,
                    "runoff": 0,
                }
            ],
            "weather_rows": [
                {
                    "week_start": "2026/05/01",
                    "tmax": "18",
                    "tmin": "5",
                    "precipitation": "10",
                    "reference_et": "21",
                }
            ],
        }
        merged = merge_saved_season_table(base, saved)
        self.assertTrue(merged.get("season_table_prefill_banner"))
        self.assertEqual(merged["management_rows"][0]["gross_irrigation"], "25")
        self.assertEqual(merged["weather_rows"][0]["tmax"], "18")


class AquaCropInitialWaterTests(TestCase):
    def _weather(self):
        import pandas as pd

        dates = pd.date_range("2024-05-01", "2024-05-20")
        return pd.DataFrame(
            {
                "MinTemp": 8.0,
                "MaxTemp": 20.0,
                "Precipitation": 0.0,
                "ReferenceET": 4.0,
                "Date": dates,
            }
        )

    def test_crop_condition_maps_to_initial_water_content(self):
        from aquacrop import AquaCropModel, Crop, InitialWaterContent, IrrigationManagement, Soil

        from et.aquacrop_simulator import run_aquacrop_simulation
        from et.crop_catalog import initial_water_for_crop_condition

        expected = {
            "optimal": ("Prop", "FC", "Field capacity (100% TAW)"),
            "mild_stress": ("Pct", 80.0, "80% of total available water"),
            "moderate_stress": ("Pct", 60.0, "60% of total available water"),
            "severe_stress": ("Pct", 40.0, "40% of total available water"),
            "wilting": ("Prop", "WP", "Wilting point (0% TAW)"),
            "": ("Prop", "FC", "Field capacity (100% TAW)"),
        }
        soil = Soil(soil_type="Loam")
        hyd = None
        for key, (wc_type, wc_value, label) in expected.items():
            spec = initial_water_for_crop_condition(key)
            self.assertEqual(spec["wc_type"], wc_type)
            self.assertEqual(spec["wc_value"], wc_value)
            self.assertEqual(spec["starting_soil_moisture"], label)
            initial_wc = InitialWaterContent(
                wc_type=spec["wc_type"],
                method="Layer",
                depth_layer=[1],
                value=[spec["wc_value"]],
            )
            model = AquaCropModel(
                sim_start_time="2024/05/01",
                sim_end_time="2024/05/20",
                weather_df=self._weather(),
                soil=soil,
                crop=Crop("Wheat", planting_date="05/01"),
                initial_water_content=initial_wc,
                irrigation_management=IrrigationManagement(irrigation_method=0),
            )
            model._initialize()
            th = float(model._init_cond.th[0])
            layer = model._param_struct.Soil.Hydrology.loc[1]
            if wc_type == "Prop" and wc_value == "FC":
                self.assertAlmostEqual(th, float(layer.th_fc), places=4)
            elif wc_type == "Prop" and wc_value == "WP":
                self.assertAlmostEqual(th, float(layer.th_wp), places=4)
            else:
                taw = float(layer.th_fc) - float(layer.th_wp)
                self.assertAlmostEqual(th, float(layer.th_wp) + (wc_value / 100.0) * taw, places=4)
            hyd = layer

        self.assertIsNotNone(hyd)
        results = run_aquacrop_simulation(
            crop="Wheat",
            soil="Loam",
            start_date="2024/05/01",
            end_date="2024/05/20",
            irrigation="rainfed",
            weather_df=self._weather(),
            crop_condition="moderate_stress",
        )
        self.assertEqual(results["starting_soil_moisture"], "60% of total available water")
        self.assertEqual(results["crop_condition"], "moderate_stress")


class AquaCropActualVsOptimalTests(TestCase):
    def test_expand_season_to_daily(self):
        from et.aquacrop_actual_vs_optimal import expand_season_to_daily

        weather = [
            {
                "week_start": "2026-05-01",
                "tmax": 20,
                "tmin": 5,
                "precipitation": 14,
                "reference_et": 21,
            }
        ]
        mgmt = [
            {
                "week_start": "2026-05-01",
                "gross_irrigation": 50,
                "effective_irrigation": 40.5,
                "soil_moisture": 28,
                "runoff": 0,
            }
        ]
        daily = expand_season_to_daily(weather, mgmt, "2026/05/01", "2026/05/07")
        self.assertEqual(len(daily), 7)
        self.assertAlmostEqual(daily["precipitation_mm"].sum(), 14.0, places=1)
        self.assertAlmostEqual(daily["irrigation_mm"].sum(), 40.5, places=1)


class AquaCropResultsTableTests(TestCase):
    def test_build_simulation_results_tables_weekly_sums_and_means(self):
        import pandas as pd
        from et.aquacrop_aggregation import build_simulation_results_tables

        n = 14
        results = {
            "daily_output": pd.DataFrame(
                {
                    "biomass": [1.0 + i * 0.1 for i in range(n)],
                }
            ),
            "water_flux": pd.DataFrame(
                {
                    "Tr": [1.0] * n,
                    "Es": [0.5] * n,
                    "Infl": [2.0] * n,
                    "IrrDay": [3.0] * n,
                    "Wr": [100.0 + i for i in range(n)],
                }
            ),
        }
        daily, weekly = build_simulation_results_tables(results, "2026/05/01")
        self.assertEqual(len(daily), 14)
        self.assertEqual(len(weekly), 2)
        self.assertAlmostEqual(weekly[0]["et_mm"], (1.5 * 7), places=1)
        self.assertAlmostEqual(weekly[0]["precipitation_mm"], 14.0, places=1)
        self.assertAlmostEqual(weekly[0]["irrigation_mm"], 21.0, places=1)

    def test_weekly_yield_comparison_optimal_and_actual(self):
        import pandas as pd
        from et.aquacrop_aggregation import build_weekly_yield_comparison

        n = 7
        optimal = pd.DataFrame({"biomass": [1.0 + i * 0.2 for i in range(n)]})
        actual = pd.DataFrame({"biomass": [0.5 + i * 0.1 for i in range(n)]})
        rows = build_weekly_yield_comparison(optimal, "Wheat", "2026/05/01", actual_daily_df=actual)
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["optimal_yield_tha"])
        self.assertIsNotNone(rows[0]["your_yield_tha"])
        self.assertLess(rows[0]["your_yield_tha"], rows[0]["optimal_yield_tha"])


class DashboardDeleteRunTests(TestCase):
    run_id = "00000000-0000-0000-0000-000000000099"

    def setUp(self):
        session = self.client.session
        session["supabase_user_id"] = "11111111-1111-1111-1111-111111111111"
        session.save()

    def test_delete_run_requires_post(self):
        url = reverse("et:delete_run", kwargs={"run_type": "et", "run_id": self.run_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    @patch("et.views_dashboard.delete_et_calculation", return_value=True)
    def test_delete_et_run_redirects_to_history(self, _mock_delete):
        url = reverse("et:delete_run", kwargs={"run_type": "et", "run_id": self.run_id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("#recent-history", response.url)

    def test_delete_invalid_run_type(self):
        url = reverse("et:delete_run", kwargs={"run_type": "invalid", "run_id": self.run_id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("#recent-history", response.url)


class LocationFieldResolutionTests(TestCase):
    def test_city_mode_resolves_alberta_coordinates(self):
        from et.location_services import resolve_saved_location_fields

        province, city, lat, lon = resolve_saved_location_fields(
            location_input_mode="city",
            province="Alberta",
            city="Calgary",
            latitude=None,
            longitude=None,
        )
        self.assertEqual(city, "Calgary")
        self.assertAlmostEqual(lat, 51.0447, places=3)
        self.assertAlmostEqual(lon, -114.0719, places=3)

    def test_coordinates_mode_formats_city_label(self):
        from et.location_services import resolve_saved_location_fields

        province, city, lat, lon = resolve_saved_location_fields(
            location_input_mode="coordinates",
            province="Alberta",
            city="",
            latitude=50.5644,
            longitude=-111.8986,
        )
        self.assertIn("50.5644", city)
        self.assertEqual(lat, 50.5644)
        self.assertEqual(lon, -111.8986)


class SupabaseErrorDetailTests(TestCase):
    def test_supabase_error_detail_extracts_postgres_message(self):
        from postgrest.exceptions import APIError

        from et.supabase_storage import _supabase_error_detail

        exc = APIError(
            {
                "message": 'column "soil_type" of relation "farms" does not exist',
                "code": "42703",
                "details": None,
                "hint": None,
            }
        )
        detail = _supabase_error_detail(exc)
        self.assertIn("42703", detail)
        self.assertIn("soil_type", detail)


class RootRedirectTests(TestCase):
    def test_root_redirects_anonymous_to_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:login"), response.url)

    def test_root_redirects_authenticated_to_dashboard(self):
        session = self.client.session
        session["supabase_user_id"] = "11111111-1111-1111-1111-111111111111"
        session.save()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:dashboard"), response.url)

    def test_et_index_redirects_anonymous_to_login(self):
        response = self.client.get("/et/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:login"), response.url)


class DashboardWelcomeTests(TestCase):
    def _sign_in(self, *, just_registered=False):
        session = self.client.session
        session["supabase_user_id"] = "11111111-1111-1111-1111-111111111111"
        if just_registered:
            session["just_registered"] = True
            session["just_registered_name"] = "Varsha Singh"
        session.save()

    @patch("et.views_dashboard.list_recent_forecast_runs", return_value=[])
    @patch("et.views_dashboard.list_recent_aquacrop_runs", return_value=[])
    @patch("et.views_dashboard.list_recent_et_calculations", return_value=[])
    @patch("et.views_dashboard.list_locations_for_user", return_value=[])
    @patch("et.views_dashboard.get_primary_location_for_user", return_value=None)
    @patch("et.views_dashboard.get_profile", return_value={"full_name": "Varsha Singh"})
    def test_new_account_shows_welcome_name_without_banner(self, *_mocks):
        self._sign_in(just_registered=True)
        response = self.client.get(reverse("et:dashboard"))
        self.assertContains(response, "Welcome, Varsha!")
        self.assertNotContains(response, "Account created")
        self.assertNotContains(response, "Welcome back, Varsha!")

        again = self.client.get(reverse("et:dashboard"))
        self.assertContains(again, "Welcome back, Varsha!")
        self.assertNotContains(again, "Welcome, Varsha!")


class AquacropSoilMatchTests(TestCase):
    def test_match_aquacrop_soil_normalizes_labels(self):
        from et.views import _match_aquacrop_soil

        self.assertEqual(_match_aquacrop_soil("loam"), "Loam")
        self.assertEqual(_match_aquacrop_soil("Sandy Loam"), "Sandy Loam")
        self.assertEqual(_match_aquacrop_soil("unknown"), "")


class AquacropRegionFieldsTests(TestCase):
    def test_resolve_region_fields_uses_alberta_cities(self):
        from et.location_services import resolve_aquacrop_region_fields, sorted_aquacrop_cities

        province, city, choices = resolve_aquacrop_region_fields(
            saved_province="Alberta",
            saved_city="Lethbridge",
        )
        self.assertEqual(province, "Alberta")
        self.assertEqual(city, "Lethbridge")
        self.assertIn("Calgary", choices)
        self.assertEqual(choices, sorted_aquacrop_cities())

    def test_unknown_province_defaults_to_alberta(self):
        from et.location_services import resolve_aquacrop_region_fields

        province, _city, choices = resolve_aquacrop_region_fields(saved_province="Ontario", saved_city="")
        self.assertEqual(province, "Alberta")
        self.assertTrue(choices)


class FarmProfileViewTests(TestCase):
    user_id = "11111111-1111-1111-1111-111111111111"
    loc_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    loc_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    def setUp(self):
        session = self.client.session
        session["supabase_user_id"] = self.user_id
        session.save()

    def _locations(self):
        return [
            {
                "id": self.loc_a,
                "farm_name": "North Field",
                "province": "Alberta",
                "city": "Calgary",
                "area_hectares": 40,
                "crop_type": "spring_wheat",
                "irrigation_type": "",
                "is_primary": True,
            },
        ]

    @patch("et.views_dashboard.get_profile", return_value=None)
    @patch("et.views_dashboard.list_locations_for_user")
    def test_profile_with_locations_defaults_to_add_form(self, mock_list, _mock_profile):
        mock_list.return_value = self._locations()
        response = self.client.get(reverse("et:farm_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add location")
        self.assertNotContains(response, 'value="North Field"', html=False)
        self.assertTrue(response.context["adding_new"])

    @patch("et.views_dashboard.get_profile", return_value=None)
    @patch("et.views_dashboard.list_locations_for_user")
    def test_profile_edit_location_prefills_form(self, mock_list, _mock_profile):
        mock_list.return_value = self._locations()
        response = self.client.get(reverse("et:farm_profile"), {"location": self.loc_a})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit location")
        self.assertContains(response, "North Field")
        self.assertEqual(response.context["edit_location_id"], self.loc_a)

    @patch("et.views_dashboard.log_feature_usage")
    @patch("et.views_dashboard.save_farm")
    @patch("et.views_dashboard.get_profile", return_value=None)
    @patch("et.views_dashboard.list_locations_for_user")
    def test_profile_post_without_location_id_inserts(self, mock_list, _mock_profile, mock_save, _mock_log):
        mock_list.return_value = self._locations()
        mock_save.return_value = (
            {
                "id": self.loc_b,
                "farm_name": "South Field",
                "province": "Alberta",
                "city": "Lethbridge",
                "crop_type": "barley",
                "is_primary": False,
            },
            None,
        )
        response = self.client.post(
            reverse("et:farm_profile"),
            {
                "action": "save",
                "location_id": "",
                "farm_name": "South Field",
                "province": "Alberta",
                "city": "Lethbridge",
                "area_hectares": "25",
                "crop_type": "barley",
                "soil_type": "Loam",
                "location_input_mode": "city",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"location={self.loc_b}", response.url)
        mock_save.assert_called_once()
        self.assertIsNone(mock_save.call_args.kwargs.get("farm_id"))
        self.assertIsNone(mock_save.call_args.kwargs.get("is_primary"))


class PasswordResetTests(TestCase):
    def test_login_page_links_to_forgot_password(self):
        response = self.client.get(reverse("et:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("et:forgot_password"))

    def test_login_page_forwards_recovery_hash_to_reset(self):
        response = self.client.get(reverse("et:login"))
        self.assertContains(response, '!== "recovery"')
        self.assertContains(response, reverse("et:reset_password"))

    def test_forgot_password_page_renders(self):
        response = self.client.get(reverse("et:forgot_password"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send reset link")

    def test_reset_password_page_renders(self):
        response = self.client.get(reverse("et:reset_password"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a new password")
        self.assertContains(response, "id_access_token")
        self.assertContains(response, "id_auth_code")
        self.assertContains(response, 'searchParams.get("code")')
        self.assertContains(response, "access_token")

    @patch("et.views_auth.supabase_configured", return_value=True)
    @patch("et.views_auth.get_anon_client")
    def test_forgot_password_requests_recovery_email(self, mock_get_client, _configured):
        response = self.client.post(
            reverse("et:forgot_password"),
            {"email": "Farmer@Example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "If an account exists for that email")
        mock_get_client.return_value.auth.reset_password_for_email.assert_called_once()
        email, options = mock_get_client.return_value.auth.reset_password_for_email.call_args.args
        self.assertEqual(email, "farmer@example.com")
        self.assertIn("/et/auth/reset-password/", options["redirect_to"])

    @patch("et.views_auth.supabase_configured", return_value=True)
    @patch("et.views_auth.get_anon_client")
    def test_forgot_password_hides_send_failures(self, mock_get_client, _configured):
        mock_get_client.return_value.auth.reset_password_for_email.side_effect = RuntimeError("down")
        response = self.client.post(
            reverse("et:forgot_password"),
            {"email": "farmer@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "send a reset email right now")
        self.assertNotContains(response, "If an account exists")

    @patch("et.views_auth.supabase_configured", return_value=True)
    def test_reset_password_rejects_missing_link(self, _configured):
        response = self.client.post(
            reverse("et:reset_password"),
            {"password": "newpassword1", "password_confirm": "newpassword1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or has expired")

    @patch("et.views_auth.supabase_configured", return_value=True)
    def test_reset_password_rejects_mismatch(self, _configured):
        response = self.client.post(
            reverse("et:reset_password"),
            {
                "password": "newpassword1",
                "password_confirm": "different1",
                "access_token": "header.payload.sig",
                "refresh_token": "refresh-token",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passwords do not match.")

    @patch("et.views_auth.supabase_configured", return_value=True)
    @patch("et.views_auth.create_anon_client")
    def test_reset_password_updates_and_signs_in(self, mock_create, _configured):
        from types import SimpleNamespace

        user = SimpleNamespace(id="11111111-1111-1111-1111-111111111111", email="farmer@example.com")
        session = SimpleNamespace(access_token="header.payload.sig", refresh_token="refresh-token", user=user)
        client = mock_create.return_value
        client.auth.set_session.return_value = SimpleNamespace(session=session, user=user)
        client.auth.update_user.return_value = SimpleNamespace(user=user)

        response = self.client.post(
            reverse("et:reset_password"),
            {
                "password": "newpassword1",
                "password_confirm": "newpassword1",
                "access_token": "header.payload.sig",
                "refresh_token": "refresh-token",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:dashboard"), response.url)
        client.auth.set_session.assert_called_once_with("header.payload.sig", "refresh-token")
        client.auth.update_user.assert_called_once_with({"password": "newpassword1"})
        self.assertEqual(self.client.session["supabase_user_id"], user.id)

    @override_settings(ALLOWED_HOSTS=["et-site.onrender.com", "localhost", "127.0.0.1"])
    @patch("et.views_auth.supabase_configured", return_value=True)
    @patch("et.views_auth.get_anon_client")
    def test_forgot_password_redirect_is_https_on_render(self, mock_get_client, _configured):
        response = self.client.post(
            reverse("et:forgot_password"),
            {"email": "farmer@example.com"},
            HTTP_HOST="et-site.onrender.com",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(response.status_code, 200)
        _email, options = mock_get_client.return_value.auth.reset_password_for_email.call_args.args
        self.assertEqual(
            options["redirect_to"],
            "https://et-site.onrender.com/et/auth/reset-password/",
        )
        self.assertNotIn("localhost", options["redirect_to"])
        self.assertNotIn("127.0.0.1", options["redirect_to"])

    @patch("et.views_auth.supabase_configured", return_value=True)
    def test_reset_password_rejects_short_password(self, _configured):
        response = self.client.post(
            reverse("et:reset_password"),
            {
                "password": "short",
                "password_confirm": "short",
                "access_token": "header.payload.sig",
                "refresh_token": "refresh-token",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password is too short")

    @patch("et.views_auth.supabase_configured", return_value=True)
    @patch("et.views_auth.create_anon_client")
    def test_reset_password_exchanges_pkce_code(self, mock_create, _configured):
        from types import SimpleNamespace

        user = SimpleNamespace(id="11111111-1111-1111-1111-111111111111", email="farmer@example.com")
        session = SimpleNamespace(access_token="header.payload.sig", refresh_token="refresh-token", user=user)
        client = mock_create.return_value
        client.auth.exchange_code_for_session.return_value = SimpleNamespace(session=session, user=user)
        client.auth.update_user.return_value = SimpleNamespace(user=user)

        response = self.client.post(
            reverse("et:reset_password"),
            {
                "password": "newpassword1",
                "password_confirm": "newpassword1",
                "auth_code": "recovery-code",
            },
        )
        self.assertEqual(response.status_code, 302)
        client.auth.exchange_code_for_session.assert_called_once_with({"auth_code": "recovery-code"})
        client.auth.set_session.assert_not_called()
        client.auth.update_user.assert_called_once_with({"password": "newpassword1"})

    def test_root_forwards_recovery_code_to_reset(self):
        response = self.client.get("/?code=abc123")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/et/auth/reset-password/", response.url)
        self.assertIn("code=abc123", response.url)

    def test_change_password_requires_login(self):
        response = self.client.get(reverse("et:change_password"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:login"), response.url)

    @patch("et.views_auth.supabase_configured", return_value=True)
    def test_change_password_page_renders_for_signed_in_user(self, _configured):
        session = self.client.session
        session["supabase_user_id"] = "11111111-1111-1111-1111-111111111111"
        session["supabase_access_token"] = "header.payload.sig"
        session["supabase_refresh_token"] = "refresh-token"
        session.save()
        response = self.client.get(reverse("et:change_password"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change password")

    @patch("et.views_auth.supabase_configured", return_value=True)
    def test_change_password_rejects_mismatch_and_short(self, _configured):
        session = self.client.session
        session["supabase_user_id"] = "11111111-1111-1111-1111-111111111111"
        session.save()
        mismatch = self.client.post(
            reverse("et:change_password"),
            {"password": "newpassword1", "password_confirm": "different1"},
        )
        self.assertContains(mismatch, "Passwords do not match.")
        short = self.client.post(
            reverse("et:change_password"),
            {"password": "short", "password_confirm": "short"},
        )
        self.assertContains(short, "Password is too short")

    @patch("et.views_auth.supabase_configured", return_value=True)
    @patch("et.views_auth.create_anon_client")
    def test_change_password_updates_user(self, mock_create, _configured):
        from types import SimpleNamespace

        user = SimpleNamespace(id="11111111-1111-1111-1111-111111111111", email="farmer@example.com")
        session = SimpleNamespace(access_token="header.payload.sig", refresh_token="refresh-token", user=user)
        client = mock_create.return_value
        client.auth.set_session.return_value = SimpleNamespace(session=session, user=user)
        client.auth.update_user.return_value = SimpleNamespace(user=user)

        store = self.client.session
        store["supabase_user_id"] = user.id
        store["supabase_user_email"] = user.email
        store["supabase_access_token"] = "header.payload.sig"
        store["supabase_refresh_token"] = "refresh-token"
        store.save()

        response = self.client.post(
            reverse("et:change_password"),
            {"password": "newpassword1", "password_confirm": "newpassword1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:dashboard"), response.url)
        client.auth.set_session.assert_called_once_with("header.payload.sig", "refresh-token")
        client.auth.update_user.assert_called_once_with({"password": "newpassword1"})

    @patch("et.views_auth.supabase_configured", return_value=True)
    def test_change_password_requires_session_tokens(self, _configured):
        session = self.client.session
        session["supabase_user_id"] = "11111111-1111-1111-1111-111111111111"
        session.save()
        response = self.client.post(
            reverse("et:change_password"),
            {"password": "newpassword1", "password_confirm": "newpassword1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your session has expired")

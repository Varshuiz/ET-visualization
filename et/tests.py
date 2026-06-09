from unittest.mock import patch

import pandas as pd
from django.test import TestCase
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
        mock_save.return_value = {
            "id": self.loc_b,
            "farm_name": "South Field",
            "province": "Alberta",
            "city": "Lethbridge",
            "crop_type": "barley",
            "is_primary": False,
        }
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
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"location={self.loc_b}", response.url)
        mock_save.assert_called_once()
        self.assertIsNone(mock_save.call_args.kwargs.get("farm_id"))
        self.assertIsNone(mock_save.call_args.kwargs.get("is_primary"))

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

    def test_et_results_requires_seeded_data(self):
        response = self.client.get(reverse("et:comparison_with_acis"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("et:acis_fetch"), response.url)

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
                "crop_type": "corn",
                "soil_type": "sandy",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["crop_type"], "corn")
        self.assertEqual(response.context["soil_type"], "sandy")
        self.assertContains(response, "Selected Crop")
        self.assertContains(response, "Selected Soil")

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
        self.assertContains(response, "Optional: Upload Your Own Weather File")

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

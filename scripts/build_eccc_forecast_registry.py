"""One-off script: fetch ECCC site_list_provinces_en.csv and write et/eccc_forecast_registry.py."""
import csv
import io

import requests


def main():
    r = requests.get(
        "https://dd.weather.gc.ca/today/citypage_weather/docs/site_list_provinces_en.csv",
        timeout=90,
    )
    r.encoding = "utf-8"
    buf = io.StringIO(r.text)
    next(buf)
    rows = list(csv.DictReader(buf))

    def parse_ll(s):
        s = s.strip().replace(" ", "")
        latp, lonp = s.split(",")
        lat = float(latp[:-1]) * (-1 if latp[-1] == "S" else 1)
        lon = float(lonp[:-1]) * (-1 if lonp[-1] == "W" else 1)
        return lat, lon

    def region_bc(lat, lon):
        if lat >= 55.0 or (lat >= 54.0 and lon <= -122.0):
            return "Northern BC"
        if lon <= -127.5:
            return "North & Central Coast"
        # Metro / Fraser Valley before Vancouver Island so mainland cities are not mis-tagged.
        if -123.45 <= lon <= -121.35 and 48.95 <= lat <= 49.55:
            return "Metro Vancouver & Fraser Valley"
        if 48.15 <= lat <= 50.85 and -124.8 <= lon <= -123.35:
            return "Vancouver Island"
        if -122.2 <= lon <= -118.8 and 49.0 <= lat <= 50.35:
            return "Okanagan & Thompson"
        if lat <= 51.2 and lon <= -115.8 and lon >= -119:
            return "Kootenays"
        if lat >= 51.5 and lon <= -119:
            return "Cariboo & Central Interior"
        return "Southern Interior & Southeast"

    def region_sk(lat, lon):
        if lat >= 54:
            return "Northern Saskatchewan"
        if lon <= -106:
            return "Western Saskatchewan"
        if lon >= -103:
            return "Eastern Saskatchewan"
        return "Central Saskatchewan"

    def region_mb(lat, lon):
        if lat >= 55:
            return "Northern Manitoba"
        if lon <= -99:
            return "Western Manitoba"
        if lon >= -96.5:
            return "Eastern Manitoba"
        return "Southern Manitoba"

    prov_map = {
        "BC": ("British Columbia", region_bc),
        "SK": ("Saskatchewan", region_sk),
        "MB": ("Manitoba", region_mb),
    }

    meta = []
    for row in rows:
        pc = row["Province Codes"]
        if pc not in prov_map:
            continue
        name = row["English Names"].strip()
        code = row["Codes"].strip()
        lat, lon = parse_ll(row["Latitude"] + "," + row["Longitude"])
        pname, regfn = prov_map[pc]
        reg = regfn(lat, lon)
        meta.append((pname, name, code, lat, lon, reg))

    lines = [
        '"""',
        "ECCC citypage forecast sites for BC, SK, and MB.",
        "Generated from dd.weather.gc.ca/today/citypage_weather/docs/site_list_provinces_en.csv",
        "(English names, province codes, coordinates). Site codes match MSC Datamart citypage XML.",
        '"""',
        "",
        "FORECAST_PROVINCE_ECC_CODE = {",
        '    "British Columbia": "BC",',
        '    "Saskatchewan": "SK",',
        '    "Manitoba": "MB",',
        "}",
        "",
        "# city_name -> { code, lat, lon, region }",
        "FORECAST_SITE_META_BY_PROVINCE = {",
    ]
    for pname in ["British Columbia", "Saskatchewan", "Manitoba"]:
        lines.append(f'    "{pname}": {{')
        for t in sorted([m for m in meta if m[0] == pname], key=lambda x: (x[5], x[1].lower())):
            _, city, code, lat, lon, reg = t
            city_esc = city.replace("\\", "\\\\").replace('"', '\\"')
            reg_esc = reg.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(
                f'        "{city_esc}": {{"code": "{code}", "lat": {lat}, "lon": {lon}, "region": "{reg_esc}"}},'
            )
        lines.append("    },")
    lines.append("}")
    lines.extend(
        [
            "",
            "",
            "def cities_by_region_for_province(province_display_name):",
            '    """Return region -> [cities] for templates / JSON."""',
            "    meta = FORECAST_SITE_META_BY_PROVINCE.get(province_display_name, {})",
            "    regions = {}",
            "    for city, d in sorted(meta.items(), key=lambda kv: kv[0].lower()):",
            '        regions.setdefault(d["region"], []).append(city)',
            "    for r in regions:",
            "        regions[r] = sorted(regions[r])",
            "    return dict(sorted(regions.items(), key=lambda kv: kv[0]))",
            "",
            "",
            "def all_cities_for_province(province_display_name):",
            "    return sorted(FORECAST_SITE_META_BY_PROVINCE.get(province_display_name, {}).keys())",
            "",
            "",
            "def get_site_code(province_display_name, city_name):",
            "    d = FORECAST_SITE_META_BY_PROVINCE.get(province_display_name, {}).get(city_name)",
            '    return d["code"] if d else None',
            "",
            "",
            "def get_lat_lon(province_display_name, city_name):",
            "    d = FORECAST_SITE_META_BY_PROVINCE.get(province_display_name, {}).get(city_name)",
            "    if not d:",
            "        return None, None",
            '    return float(d["lat"]), float(d["lon"])',
            "",
        ]
    )

    out_path = "et/eccc_forecast_registry.py"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Wrote", out_path, "lines", len(lines))


if __name__ == "__main__":
    main()

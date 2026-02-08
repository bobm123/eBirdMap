# eBird Alerts Map

Parse eBird Rare Bird Alert emails (.eml) and generate an interactive HTML map with color-coded pins for each sighting.

## Features

- Aggregates multiple alert emails into a single map
- Color-coded markers: bright red for newest sightings, darkening to blue-grey for oldest
- Grouped pins at shared locations to avoid overlap
- Interactive popups with species, observer, date, comments, and checklist links
- Filterable by state and date range
- Zero Python dependencies — uses only the standard library
- Map rendered with [Leaflet.js](https://leafletjs.com/) via CDN

## Usage

```
python ebird_map.py [path] [options]
```

**Arguments:**

| Argument | Description |
|---|---|
| `path` | Path to an `.eml` file or directory of `.eml` files (default: script directory) |
| `-o`, `--output` | Output HTML file path (default: `ebird_map.html` in the input directory) |
| `-s`, `--state` | Filter sightings to a state (e.g. `Maryland`, `Massachusetts`) |
| `-d`, `--days` | Include the latest N days of emails (default: latest day only) |
| `--no-open` | Don't open the map in the browser automatically |

**Examples:**

```bash
# Map from a single email
python ebird_map.py alerts/alert.eml

# All emails in a directory, last 7 days, Maryland only
python ebird_map.py alerts/ --days 7 --state Maryland

# 30-day view with custom output path
python ebird_map.py alerts/ --days 30 -o my_map.html
```

## Signing Up for eBird Alerts

1. Create a free account at [ebird.org](https://ebird.org) if you don't have one
2. Go to [ebird.org/alerts](https://ebird.org/alerts)
3. Browse available alerts by region (county, state, or country)
4. Click **Subscribe** next to the alert you want
5. Choose **daily** or **hourly** email frequency

**Alert types:**

- **Rare Bird Alerts** — reports of unusual species in a region (past 7 days)
- **ABA Rarities** — nationwide rarities (ABA Codes 3–5) from the US and Canada
- **Needs Alerts** — species you haven't yet reported to eBird for that region

Manage or unsubscribe from alerts at any time on your [My eBird](https://ebird.org/myebird) page.

## Setup

1. Save eBird alert emails as `.eml` files into a directory (e.g. `alerts/`)
2. Run the script
3. The map opens in your default browser

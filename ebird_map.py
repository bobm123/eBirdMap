"""
Parse an eBird Rare Bird Alert .eml file and generate an interactive HTML map
with pins for each sighting. Uses only Python standard library; the output HTML
uses Leaflet.js via CDN (no API key required).
"""

import argparse
import email
import email.policy
import glob
import json
import os
import re
import sys
import urllib.request
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta

# ── Parse .eml ──────────────────────────────────────────────────────────────

def find_emls(path=None):
    """Return a list of .eml file paths from a file, directory, or default."""
    if path and os.path.isfile(path):
        return [path]
    search_dir = path if path and os.path.isdir(path) else os.path.dirname(os.path.abspath(__file__))
    emls = sorted(glob.glob(os.path.join(search_dir, "*.eml")))
    if not emls:
        sys.exit(f"No .eml files found in {search_dir}")
    return emls


def parse_eml(eml_path):
    """Return (subject, body_text, send_date) from an .eml file."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain",))
    if body is None:
        sys.exit(f"No plain-text body found in {eml_path}")
    send_date = None
    if msg["date"]:
        send_date = msg["date"].datetime.date()
    return msg["subject"], body.get_content(), send_date


def parse_sightings(body):
    """Return list of dicts with species, count, location, lat, lon, date,
    observer, comments, checklist, confirmed."""
    sightings = []
    # Split on blank lines to get blocks, then look for map URLs
    blocks = re.split(r"\n(?=\S)", body)

    current = {}
    for line in body.splitlines():
        line = line.strip()

        # Species header: "Species Name (Scientific Name) (count) [CONFIRMED]"
        # Must not start with "- " (which indicates metadata lines like Reported, Map, etc.)
        m = re.match(r"^(?!- )(.+?)\s+\(([A-Z][a-z].*?)\)\s*(?:\((\d+)\))?\s*(CONFIRMED)?$", line)
        if m:
            if current.get("lat"):
                sightings.append(current)
            current = {
                "species": m.group(1),
                "scientific": m.group(2),
                "count": m.group(3) or "?",
                "confirmed": bool(m.group(4)),
                "location": "",
                "lat": None,
                "lon": None,
                "date": "",
                "observer": "",
                "comments": "",
                "checklist": "",
            }
            continue

        if not current:
            continue

        if line.startswith("- Reported "):
            rm = re.match(r"- Reported (.+?) by (.+)$", line)
            if rm:
                current["date"] = rm.group(1)
                current["observer"] = rm.group(2)
        elif line.startswith("- Map:"):
            coords = re.search(r"q=(-?\d+\.?\d*),(-?\d+\.?\d*)", line)
            if coords:
                current["lat"] = float(coords.group(1))
                current["lon"] = float(coords.group(2))
        elif line.startswith("- Checklist:"):
            current["checklist"] = line.split(":", 1)[-1].strip()
        elif line.startswith("- Comments:"):
            current["comments"] = line.split(":", 1)[-1].strip().strip('"')
        elif (not line.startswith("- Media:") and not line.startswith("- Map:")
              and not line.startswith("***") and not line.startswith("---")
              and current.get("lat") is None and line.startswith("- ")):
            # Location line (before Map line)
            loc = line.lstrip("- ").strip()
            if loc and not current["location"]:
                current["location"] = loc

    if current.get("lat"):
        sightings.append(current)

    return sightings


# ── Fetch from eBird API ──────────────────────────────────────────────────────

def fetch_sightings(region, api_key, back=7):
    """Fetch recent notable observations from the eBird API v2.

    Returns a list of sighting dicts in the same format as parse_sightings().
    """
    url = (
        f"https://api.ebird.org/v2/data/obs/{region}/recent/notable"
        f"?detail=full&back={back}"
    )
    req = urllib.request.Request(url, headers={"X-eBirdApiToken": api_key})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            sys.exit("Error: Invalid eBird API key.")
        elif e.code == 400:
            sys.exit(f"Error: Invalid region code '{region}'.")
        else:
            sys.exit(f"eBird API error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")

    if not data:
        sys.exit(f"No notable observations found for region '{region}' in the last {back} day(s).")

    sightings = []
    for obs in data:
        lat = obs.get("lat")
        lng = obs.get("lng")
        if lat is None or lng is None:
            continue

        reviewed = obs.get("obsReviewed", False)
        valid = obs.get("obsValid", False)
        confirmed = reviewed and valid

        # Format date to match .eml style: "Feb 05, 2026 15:08"
        obs_dt = obs.get("obsDt", "")
        date_str = ""
        if obs_dt:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(obs_dt, fmt)
                    date_str = dt.strftime("%b %d, %Y %H:%M")
                    break
                except ValueError:
                    continue

        sub_id = obs.get("subId", "")
        checklist_url = f"https://ebird.org/checklist/{sub_id}" if sub_id else ""

        sightings.append({
            "species": obs.get("comName", "Unknown"),
            "scientific": obs.get("sciName", ""),
            "count": str(obs.get("howMany", "?")) if obs.get("howMany") else "?",
            "confirmed": confirmed,
            "location": obs.get("locName", ""),
            "lat": float(lat),
            "lon": float(lng),
            "date": date_str,
            "observer": obs.get("userDisplayName", ""),
            "comments": "",
            "checklist": checklist_url,
        })

    return sightings


# ── Group by location to avoid overlapping pins ─────────────────────────────

def group_sightings(sightings):
    """Group sightings at the same coordinates into single pins."""
    groups = defaultdict(list)
    for s in sightings:
        key = (round(s["lat"], 5), round(s["lon"], 5))
        groups[key].append(s)
    return groups


# ── Generate HTML ────────────────────────────────────────────────────────────

def popup_html(group):
    """Build HTML popup for a group of sightings at one location."""
    loc = group[0]["location"]
    lines = [f"<b>{loc}</b><br>"]
    seen_species = set()
    for s in group:
        tag = f"{s['species']} ({s['count']})"
        if tag in seen_species:
            continue
        seen_species.add(tag)
        conf = ' ✓' if s['confirmed'] else ''
        lines.append(
            f"<b>{s['species']}</b> ×{s['count']}{conf}<br>"
            f"<i>{s['date']}</i> — {s['observer']}<br>"
        )
        if s["comments"]:
            lines.append(f"<span style='color:#555'>{s['comments']}</span><br>")
        if s["checklist"]:
            lines.append(f"<a href='{s['checklist']}' target='_blank'>Checklist</a><br>")
        lines.append("<hr style='margin:4px 0'>")
    return "".join(lines)


def escape_js(s):
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def age_color(age_fraction):
    """Return a hex color from bright red (newest, 0.0) to dark grey (oldest, 1.0)."""
    # Interpolate: #e74c3c (red) → #2c3e50 (dark blue-grey)
    r = int(231 + (44 - 231) * age_fraction)
    g = int(76 + (62 - 76) * age_fraction)
    b = int(60 + (80 - 60) * age_fraction)
    return f"#{r:02x}{g:02x}{b:02x}"


def parse_reported_date(date_str):
    """Parse a reported date like 'Feb 05, 2026 15:08' into a date object."""
    for fmt in ("%b %d, %Y %H:%M", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def generate_map(sightings, title, out_path, days=None):
    groups = group_sightings(sightings)

    all_lats = [s["lat"] for s in sightings]
    all_lons = [s["lon"] for s in sightings]
    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    # Compute date range for color scaling using each sighting's reported date
    reported_dates = [parse_reported_date(s["date"]) for s in sightings]
    reported_dates = [d for d in reported_dates if d]
    newest = max(reported_dates) if reported_dates else None
    oldest = min(reported_dates) if reported_dates else None
    if days and days > 1:
        date_span = days
    else:
        date_span = (newest - oldest).days if newest and oldest and newest != oldest else 1

    markers_js = []
    for (lat, lon), group in groups.items():
        popup = escape_js(popup_html(group))
        species_list = sorted({s["species"] for s in group})
        tooltip = escape_js(", ".join(species_list))
        # Use newest reported date in group for pin color
        group_dates = [parse_reported_date(s["date"]) for s in group]
        group_dates = [d for d in group_dates if d]
        group_newest = max(group_dates) if group_dates else newest
        age_frac = (newest - group_newest).days / date_span if newest else 0
        color = age_color(age_frac)
        markers_js.append(
            f"  L.circleMarker([{lat}, {lon}], "
            f"{{radius: 8, fillColor: '{color}', color: '#333', weight: 1, "
            f"opacity: 1, fillOpacity: 0.85}})"
            f".addTo(map)"
            f".bindPopup('{popup}', {{maxWidth: 350, maxHeight: 300}})"
            f".bindTooltip('{tooltip}');"
        )

    # Build legend from unique reported dates
    unique_dates = sorted({d for d in reported_dates}, reverse=True)
    legend_js = ""
    if len(unique_dates) > 1:
        legend_items = []
        for d in unique_dates:
            frac = (newest - d).days / date_span
            c = age_color(frac)
            legend_items.append(
                f"'<i style=\"background:{c}\"></i> {d}'"
            )
        legend_js = f"""
var legend = L.control({{position: 'bottomright'}});
legend.onAdd = function() {{
  var div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<b>Reported date</b><br>' + [{','.join(legend_items)}].join('<br>');
  return div;
}};
legend.addTo(map);"""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  body {{ margin: 0; }}
  #map {{ height: 100vh; }}
  h1 {{
    position: absolute; top: 10px; left: 60px; z-index: 1000;
    background: rgba(255,255,255,0.9); padding: 6px 14px;
    border-radius: 6px; font: 16px/1.3 sans-serif;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
  }}
  .legend {{
    background: rgba(255,255,255,0.9); padding: 8px 12px;
    border-radius: 6px; font: 13px/1.6 sans-serif;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
  }}
  .legend i {{
    display: inline-block; width: 14px; height: 14px;
    border-radius: 50%; margin-right: 6px; vertical-align: middle;
    border: 1px solid #333;
  }}
</style>
</head>
<body>
<h1>{title}<br><small>{len(sightings)} sightings &middot; {len(groups)} locations</small></h1>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map = L.map('map').setView([{center_lat}, {center_lon}], 8);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap contributors',
  maxZoom: 19
}}).addTo(map);
{chr(10).join(markers_js)}
{legend_js}
</script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive map from eBird rare bird alerts "
                    "(.eml files or the eBird API)."
    )
    parser.add_argument(
        "eml", nargs="?", default=None,
        help="Path to .eml file or directory of .eml files (defaults to script directory)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output HTML file path (defaults to ebird_map.html)",
    )
    parser.add_argument(
        "-s", "--state", default=None,
        help="Filter sightings to a specific state (e.g. Massachusetts, Maryland)",
    )
    parser.add_argument(
        "-d", "--days", type=int, default=None,
        help="Include emails from the latest N days (default: all emails in directory)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Don't open the map in the browser",
    )
    # eBird API arguments
    parser.add_argument(
        "--region", default=None,
        help="eBird region code (e.g. US-MA-001) — fetch notable sightings from API",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="eBird API key (or set EBIRD_API_KEY env var)",
    )
    parser.add_argument(
        "--back", type=int, default=7,
        help="Days to look back when using --region (1-30, default: 7)",
    )
    args = parser.parse_args()

    # ── API mode ──────────────────────────────────────────────────────────
    if args.region:
        api_key = args.api_key or os.environ.get("EBIRD_API_KEY")
        if not api_key:
            sys.exit("Error: --api-key or EBIRD_API_KEY env var required with --region.")

        back = max(1, min(30, args.back))
        print(f"Fetching notable observations for {args.region} (last {back} days)...")
        all_sightings = fetch_sightings(args.region, api_key, back)

        if args.state:
            filtered = [s for s in all_sightings if args.state.lower() in s["location"].lower()]
            print(f"Filtered to '{args.state}': {len(filtered)} of {len(all_sightings)} sightings")
            all_sightings = filtered

        if not all_sightings:
            sys.exit("No sightings with coordinates found.")

        species = sorted({s["species"] for s in all_sightings})
        print(f"Total: {len(all_sightings)} sightings — {len(species)} unique species:")
        for name in species:
            print(f"  • {name}")

        title = f"eBird Notable: {args.region}"
        out = args.output or os.path.join(os.getcwd(), "ebird_map.html")
        generate_map(all_sightings, title, out, days=back)
        print(f"Map written to: {out}")
        if not args.no_open:
            webbrowser.open(f"file:///{os.path.abspath(out).replace(os.sep, '/')}")
        return

    # ── .eml mode ─────────────────────────────────────────────────────────
    eml_paths = find_emls(args.eml)

    # Parse all emails and collect their dates
    parsed_emls = []
    for eml_path in eml_paths:
        subject, body, send_date = parse_eml(eml_path)
        sightings = parse_sightings(body)
        if sightings:
            parsed_emls.append((eml_path, subject, sightings, send_date))

    if not parsed_emls:
        sys.exit("No sightings with coordinates found in any .eml file.")

    # Filter by date: default depends on whether input is a single file or directory
    newest_date = max(e[3] for e in parsed_emls if e[3])
    if args.days is not None:
        cutoff = newest_date - timedelta(days=args.days - 1)
        parsed_emls = [e for e in parsed_emls if e[3] and e[3] >= cutoff]
        print(f"Showing {args.days} day(s) up to {newest_date} ({len(parsed_emls)} emails)")
    elif len(eml_paths) == 1:
        # Single file: just use it as-is
        print(f"Showing {parsed_emls[0][0]} ({newest_date})")
    else:
        # Multiple files (directory): include all by default
        oldest_date = min(e[3] for e in parsed_emls if e[3])
        if oldest_date == newest_date:
            print(f"Showing all {len(parsed_emls)} emails ({newest_date})")
        else:
            print(f"Showing all {len(parsed_emls)} emails ({oldest_date} to {newest_date})")

    all_sightings = []
    title = None
    for eml_path, subject, sightings, send_date in parsed_emls:
        print(f"  {os.path.basename(eml_path)}: {len(sightings)} sightings")
        for s in sightings:
            s["email_date"] = send_date
        all_sightings.extend(sightings)
        if title is None:
            title = subject

    if args.state:
        filtered = [s for s in all_sightings if args.state.lower() in s["location"].lower()]
        print(f"Filtered to '{args.state}': {len(filtered)} of {len(all_sightings)} sightings")
        all_sightings = filtered

    if not all_sightings:
        sys.exit("No sightings with coordinates found.")

    species = sorted({s["species"] for s in all_sightings})
    print(f"Total: {len(all_sightings)} sightings — {len(species)} unique species:")
    for name in species:
        print(f"  • {name}")

    input_path = args.eml or os.path.dirname(os.path.abspath(__file__))
    default_dir = input_path if os.path.isdir(input_path) else os.path.dirname(os.path.abspath(input_path))
    out = args.output or os.path.join(default_dir, "ebird_map.html")
    generate_map(all_sightings, title or "eBird Rare Bird Alert", out, days=args.days)
    print(f"Map written to: {out}")
    if not args.no_open:
        webbrowser.open(f"file:///{os.path.abspath(out).replace(os.sep, '/')}")


if __name__ == "__main__":
    main()

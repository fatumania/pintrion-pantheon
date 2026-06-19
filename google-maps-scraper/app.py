import os
import json
import re
import time
import threading
import socket
import urllib.parse
import uuid
import requests as http_requests
from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime

app = Flask(__name__)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
GEOCODING_URL = "https://nominatim.openstreetmap.org/search"

MAX_SESSIONS = 10

sessions = {}
sessions_lock = threading.Lock()


def create_session():
    sid = str(uuid.uuid4())[:8]
    with sessions_lock:
        sessions[sid] = {
            "id": sid,
            "scraping": False,
            "collecting_emails": False,
            "progress": {"current": 0, "total": 0, "status": "idle", "found": 0, "phase": ""},
            "email_progress": {"current": 0, "total": 0, "status": "idle"},
            "results": [],
            "log": [],
            "last_file": None,
            "last_csv": None,
            "created_at": datetime.now().isoformat(),
        }
    return sid


def get_session(sid, auto_create=False):
    with sessions_lock:
        if sid in sessions:
            return sessions[sid]
        if auto_create:
            sessions[sid] = {
                "id": sid,
                "scraping": False,
                "collecting_emails": False,
                "progress": {"current": 0, "total": 0, "status": "idle", "found": 0, "phase": ""},
                "email_progress": {"current": 0, "total": 0, "status": "idle"},
                "results": [],
                "log": [],
                "last_file": None,
                "last_csv": None,
                "created_at": datetime.now().isoformat(),
            }
            return sessions[sid]
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    with sessions_lock:
        result = []
        for sid, s in sessions.items():
            result.append({
                "id": sid,
                "scraping": s["scraping"],
                "status": s["progress"].get("status", "idle"),
                "found": s["progress"].get("found", 0),
                "created_at": s["created_at"],
            })
    return jsonify(result)


@app.route("/api/sessions", methods=["POST"])
def api_sessions_create():
    with sessions_lock:
        if len(sessions) >= MAX_SESSIONS:
            return jsonify({"error": f"Max {MAX_SESSIONS} sessions"}), 400
    sid = create_session()
    return jsonify({"ok": True, "id": sid})


@app.route("/api/sessions/<sid>", methods=["DELETE"])
def api_sessions_delete(sid):
    with sessions_lock:
        s = sessions.pop(sid, None)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


from cities_db import CITIES_DB
KNOWN_COUNTRIES_CITIES = CITIES_DB


def get_cities_for_country(page, country_code, country_name):
    known = KNOWN_COUNTRIES_CITIES.get(country_code.upper(), [])
    if known:
        return known

    cities = find_cities_via_google(page, country_name, max_cities=200)
    if cities:
        return cities

    try:
        r = http_requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": country_name, "format": "json", "limit": 1, "addressdetails": 1},
            headers={"User-Agent": "MapsScraper/5.0"},
            timeout=10,
        )
        data = r.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return [{"city": country_name, "lat": lat, "lon": lon, "search_all": True}]
    except Exception:
        pass

    return []


def get_city_coordinates(city, country=""):
    try:
        q = f"{city}, {country}" if country else city
        r = http_requests.get(
            GEOCODING_URL,
            params={"q": q, "format": "json", "limit": 1},
            headers={"User-Agent": "MapsScraper/5.0"},
            timeout=10,
        )
        data = r.json()
        if data:
            return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]), "display": data[0].get("display_name", q)}
    except Exception:
        pass
    return None


def find_cities_via_google(page, country_name, max_cities=100):
    cities = []
    try:
        search_url = f"https://www.google.com/maps/search/cities+in+{urllib.parse.quote(country_name)}"
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(4)

        try:
            consent = page.locator('button:has-text("Accept all"), button:has-text("Alle akzeptieren"), button:has-text("Accept")')
            if consent.count() > 0:
                consent.first.click(timeout=3000)
                time.sleep(2)
        except Exception:
            pass

        feed = page.locator('[role="feed"]')
        for _ in range(20):
            try:
                if feed.count() > 0:
                    feed.first.evaluate("el => el.scrollTop = el.scrollHeight")
                else:
                    page.mouse.wheel(0, 800)
                time.sleep(1.5)
            except Exception:
                break

        items = page.locator('.Nv2PK .fontHeadlineSmall, .Nv2PK a[href*="/maps/place/"]').all()
        for item in items[:max_cities]:
            try:
                text = item.inner_text(timeout=2000).strip()
                if text and len(text) > 1:
                    cities.append(text)
            except Exception:
                pass
    except Exception:
        pass
    return cities


def extract_emails_from_url(url):
    if not url:
        return []
    try:
        r = http_requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            allow_redirects=True,
            verify=False,
        )
        text = r.text[:200000]
        emails = list(set(EMAIL_REGEX.findall(text)))
        bad_ext = {".png", ".jpg", ".gif", ".svg", ".css", ".js", ".ico", ".webp", ".woff", ".woff2", ".ttf"}
        emails = [e for e in emails if not any(e.endswith(x) for x in bad_ext)]
        return emails[:5]
    except Exception:
        return []


def scrape_single_query(page, query, max_results=999999, seen_names=None):
    results = []
    if seen_names is None:
        seen_names = set()

    search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(4)

        try:
            consent = page.locator('button:has-text("Accept all"), button:has-text("Alle akzeptieren"), button:has-text("Accept")')
            if consent.count() > 0:
                consent.first.click(timeout=3000)
                time.sleep(2)
                page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                time.sleep(4)
        except Exception:
            pass

        prev_count = 0
        stale_rounds = 0
        for _ in range(80):
            try:
                feed = page.locator('[role="feed"]')
                if feed.count() > 0:
                    feed.first.evaluate("el => el.scrollTop = el.scrollHeight")
                else:
                    page.mouse.wheel(0, 1000)
                time.sleep(1.5)

                current_links = page.locator('a[href*="/maps/place/"]').count()
                if current_links == prev_count:
                    stale_rounds += 1
                    if stale_rounds >= 4:
                        break
                else:
                    stale_rounds = 0
                    prev_count = current_links

                if current_links >= max_results:
                    break
            except Exception:
                break

        links = page.locator('a[href*="/maps/place/"]').all()
        place_urls = []
        for link in links:
            try:
                href = link.get_attribute("href", timeout=2000)
                if href and "/maps/place/" in href and href not in place_urls:
                    place_urls.append(href)
            except Exception:
                pass

        for place_url in place_urls[:max_results]:
            try:
                page.goto(place_url, wait_until="domcontentloaded", timeout=15000)
                time.sleep(2)
                entry = extract_place_details(page)
                if entry and entry.get("name") and entry["name"] not in seen_names:
                    seen_names.add(entry["name"])
                    results.append(entry)
            except Exception:
                pass
    except Exception:
        pass

    return results


def scrape_with_radius(page, city, niche, radius_km, max_results=999999, seen_names=None):
    results = []
    if seen_names is None:
        seen_names = set()

    coords = get_city_coordinates(city)
    if not coords:
        return results

    lat, lon = coords["lat"], coords["lon"]
    radius_m = radius_km * 1000

    query = f"{niche} near {city}"
    search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(4)

        try:
            consent = page.locator('button:has-text("Accept all"), button:has-text("Alle akzeptieren"), button:has-text("Accept")')
            if consent.count() > 0:
                consent.first.click(timeout=3000)
                time.sleep(2)
        except Exception:
            pass

        if radius_km > 0:
            try:
                map_el = page.locator('[data-map]')
                if map_el.count() > 0:
                    zoom_level = max(10, 15 - int(radius_km / 10))
                    page.evaluate(f"""() => {{
                        const map = document.querySelector('[data-map]');
                        if (map) {{
                            const event = new MouseEvent('wheel', {{ deltaY: {(15 - zoom_level) * 100} }});
                            map.dispatchEvent(event);
                        }}
                    }}""")
                    time.sleep(2)
            except Exception:
                pass

        prev_count = 0
        stale_rounds = 0
        for _ in range(100):
            try:
                feed = page.locator('[role="feed"]')
                if feed.count() > 0:
                    feed.first.evaluate("el => el.scrollTop = el.scrollHeight")
                else:
                    page.mouse.wheel(0, 1000)
                time.sleep(1.5)

                current_links = page.locator('a[href*="/maps/place/"]').count()
                if current_links == prev_count:
                    stale_rounds += 1
                    if stale_rounds >= 4:
                        break
                else:
                    stale_rounds = 0
                    prev_count = current_links

                if current_links >= max_results:
                    break
            except Exception:
                break

        links = page.locator('a[href*="/maps/place/"]').all()
        place_urls = []
        for link in links:
            try:
                href = link.get_attribute("href", timeout=2000)
                if href and "/maps/place/" in href and href not in place_urls:
                    place_urls.append(href)
            except Exception:
                pass

        for place_url in place_urls[:max_results]:
            try:
                page.goto(place_url, wait_until="domcontentloaded", timeout=15000)
                time.sleep(2)
                entry = extract_place_details(page)
                if entry and entry.get("name") and entry["name"] not in seen_names:
                    entry["distance_from_center_km"] = calculate_distance(lat, lon, entry.get("lat", 0), entry.get("lng", 0))
                    if radius_km > 0 and entry.get("distance_from_center_km", 0) > radius_km:
                        continue
                    seen_names.add(entry["name"])
                    results.append(entry)
            except Exception:
                pass
    except Exception:
        pass

    return results


def calculate_distance(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
        if lat1 == 0 and lon1 == 0:
            return 0
        import math
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 1)
    except Exception:
        return 0


def extract_place_details(page):
    entry = {
        "name": "", "address": "", "phone": "", "website": "", "domain": "",
        "rating": 0, "reviews_count": 0, "hours": "",
        "google_url": page.url, "lat": "", "lng": "", "emails": [],
        "category": "", "city": "",
    }

    try:
        for sel in ["h1.DUwDvf", "h1"]:
            el = page.locator(sel)
            if el.count() > 0:
                text = el.first.inner_text(timeout=3000).strip()
                if text and text not in ("Ergebnisse", "Results", "Results"):
                    entry["name"] = text
                    break
    except Exception:
        pass

    if not entry["name"]:
        try:
            title = page.title()
            if " - " in title:
                entry["name"] = title.split(" - ")[0].strip()
        except Exception:
            pass

    try:
        for sel in ['button[data-item-id="address"] .Io6YTe', 'div[data-item-id="address"] .Io6YTe']:
            el = page.locator(sel)
            if el.count() > 0:
                entry["address"] = el.first.inner_text(timeout=3000).strip()
                break
    except Exception:
        pass

    try:
        for sel in ['button[data-item-id*="phone"] .Io6YTe', 'a[data-item-id*="phone"] .Io6YTe']:
            el = page.locator(sel)
            if el.count() > 0:
                entry["phone"] = el.first.inner_text(timeout=3000).strip()
                break
    except Exception:
        pass

    try:
        for sel in ['a[data-item-id="authority"]', 'a[aria-label*="Website"]', 'a[aria-label*="Website"]']:
            el = page.locator(sel)
            if el.count() > 0:
                href = el.first.get_attribute("href", timeout=3000) or ""
                entry["website"] = href
                if href:
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(href)
                        entry["domain"] = parsed.netloc or parsed.hostname or href
                    except Exception:
                        entry["domain"] = href
                break
    except Exception:
        pass

    try:
        for sel in ['div[role="img"][aria-label]', 'span[role="img"][aria-label]']:
            el = page.locator(sel)
            for i in range(el.count()):
                label = el.nth(i).get_attribute("aria-label", timeout=1000) or ""
                match = re.search(r"([\d.,]+)\s*(?:star|estrellas|sterne|étoile)", label, re.I)
                if match:
                    entry["rating"] = float(match.group(1).replace(",", "."))
                    break
            if entry["rating"]:
                break
    except Exception:
        pass

    try:
        el = page.locator('button[jsaction*="review"]')
        if el.count() > 0:
            text = el.first.inner_text(timeout=3000)
            nums = re.findall(r"\d+", text)
            if nums:
                entry["reviews_count"] = int(nums[0])
    except Exception:
        pass

    try:
        for sel in ['[data-item-id="oh"] .Io6YTe', 'div[aria-label*="hours"]', 'div[aria-label*="Offnungszeiten"]']:
            el = page.locator(sel)
            if el.count() > 0:
                entry["hours"] = el.first.inner_text(timeout=3000).strip()
                break
    except Exception:
        pass

    try:
        el = page.locator('button[data-item-id="category"] .Io6YTe')
        if el.count() > 0:
            entry["category"] = el.first.inner_text(timeout=3000).strip()
    except Exception:
        pass

    try:
        match = re.search(r"@([\d.-]+),([\d.-]+)", page.url)
        if match:
            entry["lat"] = match.group(1)
            entry["lng"] = match.group(2)
    except Exception:
        pass

    return entry


def scrape_country_mode(sid, location, niche, max_results, collect_emails):
    from playwright.sync_api import sync_playwright

    s = get_session(sid, auto_create=True)
    if not s:
        return

    s["progress"] = {
        "current": 0, "total": 0, "status": "Initializing...",
        "found": 0, "location": location, "niche": niche, "phase": "init",
    }
    s["results"] = []

    all_results = []
    seen_names = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        ])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        ctx.add_cookies([
            {"name": "CONSENT", "value": "YES+cb.20210328-17-p0.en+FX+299", "domain": ".google.com", "path": "/"},
            {"name": "SOCS", "value": "CAISEwgDEgk2NTQyNTUxODMaAmVuIAEaBgiA_ZyaBg", "domain": ".google.com", "path": "/"},
        ])
        page = ctx.new_page()

        country_code = ""
        country_name = location
        for code, name in [("US", "United States"), ("GB", "United Kingdom"), ("DE", "Germany"),
                           ("FR", "France"), ("ES", "Spain"), ("IT", "Italy"), ("PT", "Portugal"),
                           ("RO", "Romania"), ("PL", "Poland"), ("UA", "Ukraine"), ("RU", "Russia"),
                           ("MD", "Moldova"), ("TR", "Turkey"), ("AE", "UAE"), ("SA", "Saudi Arabia"),
                           ("CA", "Canada"), ("AU", "Australia"), ("BR", "Brazil"), ("MX", "Mexico"),
                           ("IN", "India"), ("CN", "China"), ("JP", "Japan"), ("KR", "South Korea"),
                           ("TH", "Thailand"), ("VN", "Vietnam"), ("PH", "Philippines"), ("ID", "Indonesia"),
                           ("MY", "Malaysia"), ("SG", "Singapore"), ("NZ", "New Zealand"), ("ZA", "South Africa"),
                           ("EG", "Egypt"), ("NG", "Nigeria"), ("KE", "Kenya"), ("GH", "Ghana"),
                           ("IL", "Israel"), ("SE", "Sweden"), ("NO", "Norway"), ("DK", "Denmark"),
                           ("FI", "Finland"), ("NL", "Netherlands"), ("BE", "Belgium"), ("AT", "Austria"),
                           ("CH", "Switzerland"), ("GR", "Greece"), ("CZ", "Czech Republic"),
                           ("HU", "Hungary"), ("SK", "Slovakia"), ("BG", "Bulgaria"), ("HR", "Croatia"),
                           ("RS", "Serbia"), ("BA", "Bosnia"), ("ME", "Montenegro"), ("MK", "North Macedonia"),
                           ("AL", "Albania"), ("XK", "Kosovo"), ("LT", "Lithuania"), ("LV", "Latvia"),
                           ("EE", "Estonia"), ("IS", "Iceland"), ("LU", "Luxembourg")]:
            if name.lower() in location.lower() or location.lower() in name.lower():
                country_code = code
                country_name = name
                break

        cities = get_cities_for_country(page, country_code, country_name)

        s["progress"]["phase"] = "scraping"
        s["progress"]["total"] = len(cities)
        s["log"].append(f"Found {len(cities)} cities in {country_name}")

        for city_i, city in enumerate(cities):
            if len(all_results) >= max_results:
                break

            s["progress"]["current"] = city_i + 1
            s["progress"]["status"] = f"City {city_i+1}/{len(cities)}: {city}"
            s["progress"]["found"] = len(all_results)

            remaining = max_results - len(all_results)
            query = f"{niche} in {city}"
            city_results = scrape_single_query(page, query, remaining, seen_names)

            for entry in city_results:
                entry["city"] = city
                all_results.append(entry)

            s["progress"]["found"] = len(all_results)
            s["results"] = all_results[-100:]
            s["log"].append(f"{city}: found {len(city_results)} (total: {len(all_results)})")

            if len(all_results) >= max_results:
                s["log"].append(f"Target reached: {len(all_results)}/{max_results}")
                break

        if len(all_results) < max_results:
            alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            for letter in alphabet:
                if len(all_results) >= max_results:
                    break
                query = f"{niche} near {location} {letter}*"
                s["progress"]["status"] = f"Alphabet scan: {letter}..."
                s["progress"]["found"] = len(all_results)
                extra = scrape_single_query(page, query, max_results - len(all_results), seen_names)
                for entry in extra:
                    entry["city"] = location
                    all_results.append(entry)
                if extra:
                    s["log"].append(f"Letter {letter}: found {len(extra)}")
                time.sleep(1)

        browser.close()

    if collect_emails and all_results:
        s["collecting_emails"] = True
        s["email_progress"] = {"current": 0, "total": len(all_results), "status": "Collecting emails..."}
        for i, entry in enumerate(all_results):
            s["email_progress"]["current"] = i + 1
            s["email_progress"]["status"] = f"Email {i+1}/{len(all_results)}: {entry.get('name', '')[:30]}"
            if entry.get("website"):
                entry["emails"] = extract_emails_from_url(entry["website"])
                time.sleep(0.5)
        s["collecting_emails"] = False
        s["email_progress"]["status"] = "Done"

    s["progress"]["status"] = "done"
    s["progress"]["phase"] = "done"
    s["progress"]["finished_at"] = datetime.now().isoformat()

    save_results(s, all_results, location, niche, sid)
    s["scraping"] = False


def scrape_city_mode(sid, location, niche, radius_km, max_results, collect_emails):
    from playwright.sync_api import sync_playwright

    s = get_session(sid, auto_create=True)
    if not s:
        return

    s["progress"] = {
        "current": 0, "total": 1, "status": f"Scraping {location} ({radius_km}km)...",
        "found": 0, "location": location, "niche": niche, "phase": "scraping",
    }
    s["results"] = []

    all_results = []
    seen_names = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        ])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        ctx.add_cookies([
            {"name": "CONSENT", "value": "YES+cb.20210328-17-p0.en+FX+299", "domain": ".google.com", "path": "/"},
            {"name": "SOCS", "value": "CAISEwgDEgk2NTQyNTUxODMaAmVuIAEaBgiA_ZyaBg", "domain": ".google.com", "path": "/"},
        ])
        page = ctx.new_page()

        coords = get_city_coordinates(location)
        if coords:
            s["log"].append(f"City coords: {coords['lat']}, {coords['lon']}")

        all_results = scrape_with_radius(page, location, niche, radius_km, max_results, seen_names)

        if len(all_results) < max_results:
            nearby_cities = []
            try:
                r = http_requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": location, "format": "json", "limit": 1, "addressdetails": 1},
                    headers={"User-Agent": "MapsScraper/5.0"},
                    timeout=10,
                )
                data = r.json()
                if data:
                    addr = data[0].get("address", {})
                    state = addr.get("state", "")
                    country = addr.get("country", "")
                    if state:
                        nearby_query = f"{niche} in {state}"
                        s["log"].append(f"Expanding to state: {state}")
                        extra = scrape_single_query(page, nearby_query, max_results - len(all_results), seen_names)
                        for entry in extra:
                            entry["city"] = state
                            all_results.append(entry)
            except Exception:
                pass

        browser.close()

    if collect_emails and all_results:
        s["collecting_emails"] = True
        s["email_progress"] = {"current": 0, "total": len(all_results), "status": "Collecting emails..."}
        for i, entry in enumerate(all_results):
            s["email_progress"]["current"] = i + 1
            s["email_progress"]["status"] = f"Email {i+1}/{len(all_results)}: {entry.get('name', '')[:30]}"
            if entry.get("website"):
                entry["emails"] = extract_emails_from_url(entry["website"])
                time.sleep(0.5)
        s["collecting_emails"] = False
        s["email_progress"]["status"] = "Done"

    s["progress"]["status"] = "done"
    s["progress"]["phase"] = "done"
    s["progress"]["finished_at"] = datetime.now().isoformat()

    save_results(s, all_results, location, niche, sid)
    s["scraping"] = False


def save_results(s, all_results, location, niche, sid):
    if all_results:
        safe_loc = "".join(c if c.isalnum() or c in "-_" else "_" for c in location)[:50]
        safe_niche = "".join(c if c.isalnum() or c in "-_" else "_" for c in niche)[:50]
        filename = f"gmaps_{safe_niche}_{safe_loc}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sid}.json"
        filepath = os.path.join(RESULTS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        s["last_file"] = filename

        csv_file = filepath.replace(".json", ".csv")
        with open(csv_file, "w", encoding="utf-8-sig") as f:
            f.write("Name;Address;City;Phone;Website;Domain;Email;Rating;Reviews;Category;Hours;Lat;Lng;URL\n")
            for r in all_results:
                emails = ", ".join(r.get("emails", []))
                f.write(f'{r.get("name","")};{r.get("address","")};{r.get("city","")};{r.get("phone","")};{r.get("website","")};{r.get("domain","")};{emails};{r.get("rating",0)};{r.get("reviews_count",0)};{r.get("category","")};{r.get("hours","")};{r.get("lat","")};{r.get("lng","")};{r.get("google_url","")}\n')
        s["last_csv"] = csv_file
        s["log"].append(f"Done: {len(all_results)} places saved")
    else:
        s["log"].append("No results found")


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data = request.get_json()
    sid = data.get("session_id")
    location = data.get("location", "").strip()
    niche = data.get("niche", "").strip()
    max_results = max(1, int(data.get("max_results", 100)))
    collect_emails = data.get("collect_emails", False)
    radius_km = float(data.get("radius_km", 0))
    mode = data.get("mode", "country")

    if not sid:
        return jsonify({"error": "session_id required"}), 400
    if not location:
        return jsonify({"error": "Enter location"}), 400
    if not niche:
        return jsonify({"error": "Enter niche"}), 400

    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    if s["scraping"]:
        return jsonify({"error": "Already running"}), 400

    s["scraping"] = True
    s["results"] = []
    s["log"] = []

    if mode == "city":
        t = threading.Thread(
            target=scrape_city_mode,
            args=(sid, location, niche, radius_km, max_results, collect_emails),
            daemon=True,
        )
    else:
        t = threading.Thread(
            target=scrape_country_mode,
            args=(sid, location, niche, max_results, collect_emails),
            daemon=True,
        )
    t.start()
    return jsonify({"ok": True, "session_id": sid, "location": location, "niche": niche, "mode": mode})


@app.route("/api/status/<sid>")
def api_status(sid):
    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({
        "progress": s["progress"],
        "email_progress": s["email_progress"],
        "collecting_emails": s["collecting_emails"],
    })


@app.route("/api/log/<sid>")
def api_log(sid):
    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify([])
    return jsonify(s["log"][-50:])


@app.route("/api/results/<sid>")
def api_results(sid):
    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify([])
    return jsonify(s["results"][-200:])


@app.route("/api/reset/<sid>", methods=["POST"])
def api_reset(sid):
    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    s["scraping"] = False
    s["collecting_emails"] = False
    s["progress"] = {"current": 0, "total": 0, "status": "idle", "found": 0, "phase": ""}
    s["email_progress"] = {"current": 0, "total": 0, "status": "idle"}
    s["results"] = []
    s["log"] = []
    return jsonify({"ok": True, "message": "Reset done"})


@app.route("/api/download/<filename>")
def api_download(filename):
    safe_name = os.path.basename(filename)
    fp = os.path.join(RESULTS_DIR, safe_name)
    if not os.path.abspath(fp).startswith(os.path.abspath(RESULTS_DIR)):
        return jsonify({"error": "Invalid path"}), 400
    if not os.path.exists(fp):
        return jsonify({"error": "File not found"}), 404
    return send_file(fp, as_attachment=True, download_name=safe_name)


@app.route("/api/download-latest/<sid>")
def api_download_latest(sid):
    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    last = s.get("last_file")
    if not last:
        return jsonify({"error": "No file"}), 404
    fp = os.path.join(RESULTS_DIR, last)
    if not os.path.exists(fp):
        return jsonify({"error": "File not found"}), 404
    return send_file(fp, as_attachment=True, download_name=last)


@app.route("/api/download-csv/<sid>")
def api_download_csv(sid):
    s = get_session(sid, auto_create=True)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    last = s.get("last_csv")
    if not last:
        return jsonify({"error": "No CSV"}), 404
    fp = os.path.join(RESULTS_DIR, last)
    if not os.path.exists(fp):
        return jsonify({"error": "File not found"}), 404
    return send_file(fp, as_attachment=True, download_name=os.path.basename(last))


@app.route("/api/files")
def api_files():
    files = []
    for f in sorted(os.listdir(RESULTS_DIR), reverse=True):
        if f.endswith(".json"):
            fp = os.path.join(RESULTS_DIR, f)
            with open(fp, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            files.append({"filename": f, "count": len(data)})
    return jsonify(files[:20])


def find_free_port(start=5558, end=5600):
    for port in range(start, end):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", port))
            s.close()
            return port
        except OSError:
            continue
    return start


if __name__ == "__main__":
    port = find_free_port()
    print("=" * 60)
    print("  Google Maps Scraper v5.0 - SMART")
    print(f"  Max sessions: {MAX_SESSIONS}")
    print(f"  Port: {port}")
    print(f"  Open: http://localhost:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)

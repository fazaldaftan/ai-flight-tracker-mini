# python
import math
import time
import requests
from typing import Optional, Dict, Any, List, Tuple

OSN_STATES = "https://opensky-network.org/api/states/all"
NOAA_METAR = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{icao}.TXT"
NOAA_TAF  = "https://tgftp.nws.noaa.gov/data/forecasts/taf/stations/{icao}.TXT"

def to_rad(d): return d * math.pi / 180.0
def to_deg(r): return r * 180.0 / math.pi

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    φ1, φ2 = to_rad(lat1), to_rad(lat2)
    dφ = to_rad(lat2 - lat1)
    dλ = to_rad(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def initial_bearing(lat1, lon1, lat2, lon2):
    φ1, φ2 = to_rad(lat1), to_rad(lat2)
    Δλ = to_rad(lon2 - lon1)
    y = math.sin(Δλ) * math.cos(φ2)
    x = math.cos(φ1)*math.sin(φ2) - math.sin(φ1)*math.cos(φ2)*math.cos(Δλ)
    θ = math.atan2(y, x)
    return (to_deg(θ) + 360) % 360

def get_opensky_states(bbox: Optional[Tuple[float,float,float,float]] = None) -> Dict[str, Any]:
    params = {}
    if bbox:
        params = {"lamin": bbox[0], "lomin": bbox[1], "lamax": bbox[2], "lomax": bbox[3]}
    r = requests.get(OSN_STATES, params=params, timeout=10, headers={"User-Agent": "ai-flight-tracker-mini"})
    r.raise_for_status()
    return r.json()

def parse_state(state_row: List[Any]) -> Dict[str, Any]:
    # https://opensky-network.org/apidoc/rest.html#response
    return {
        "icao24": state_row[0],
        "callsign": (state_row[1] or "").strip(),
        "origin_country": state_row[2],
        "time_position": state_row[3],
        "last_contact": state_row[4],
        "lon": state_row[5],
        "lat": state_row[6],
        "baro_altitude": state_row[7],
        "on_ground": state_row[8],
        "velocity": state_row[9],            # m/s
        "true_track": state_row[10],         # deg
        "vertical_rate": state_row[11],      # m/s
        "geo_altitude": state_row[13],       # meters
        "squawk": state_row[14],
        "spi": state_row[15],
        "position_source": state_row[16],
    }

def ms_to_kts(ms: Optional[float]) -> Optional[float]:
    if ms is None: return None
    return ms * 1.94384

def m_to_ft(m: Optional[float]) -> Optional[float]:
    if m is None: return None
    return m * 3.28084

def get_noaa_text(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "ai-flight-tracker-mini"})
        if r.status_code == 200 and len(r.text.strip()) > 0:
            return r.text
    except Exception:
        pass
    return None

def get_metar(icao: str) -> Optional[str]:
    return get_noaa_text(NOAA_METAR.format(icao=icao.upper()))

def get_taf(icao: str) -> Optional[str]:
    return get_noaa_text(NOAA_TAF.format(icao=icao.upper()))

def nice_heading(deg: Optional[float]) -> str:
    if deg is None: return "N/A"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    idx = int((deg % 360) / 22.5 + 0.5) % 16
    return f"{deg:.0f}° {dirs[idx]}"

def since(ts: Optional[int]) -> str:
    if not ts: return "N/A"
    delta = int(time.time() - ts)
    if delta < 60: return f"{delta}s ago"
    mins = delta // 60
    return f"{mins}m ago"

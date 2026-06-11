# app.py — AI Flight Tracker (Pro) with AI Commentary + Rotated Icons + Modern UI
import time
import math
import os
import io
import ssl
import urllib.request
import pandas as pd
import numpy as np
import streamlit as st
from streamlit_folium import st_folium
import folium
from keplergl import KeplerGl
import streamlit.components.v1 as components
import altair as alt
from typing import Optional, Tuple

from ops import (
    get_opensky_states, parse_state, ms_to_kts, m_to_ft,
    since, get_metar, get_taf, haversine_m
)

st.set_page_config(page_title="AI Flight Tracker (Pro)", layout="wide")

# ---------------- Airline Logos ----------------
AIRLINES = {
    "AAL": ("American Airlines", "https://upload.wikimedia.org/wikipedia/commons/c/cf/American_Airlines_logo_2013.svg"),
    "QTR": ("Qatar Airways", "https://upload.wikimedia.org/wikipedia/en/8/8e/Qatar_Airways_logo.svg"),
    "UAE": ("Emirates", "https://upload.wikimedia.org/wikipedia/en/d/dc/Emirates_logo.svg"),
    "ETD": ("Etihad Airways", "https://upload.wikimedia.org/wikipedia/en/1/1b/Etihad_Airways_Logo.svg"),
    "DLH": ("Lufthansa", "https://upload.wikimedia.org/wikipedia/commons/d/d5/Lufthansa_Logo_2018.svg"),
    "THY": ("Turkish Airlines", "https://upload.wikimedia.org/wikipedia/commons/8/8e/Turkish_Airlines_logo_2019.svg"),
}

# ---------------- Local Airport DB (keep it small & fast) ----------------
AIRPORTS = {
    "VABB": (19.0887, 72.8679),
    "VIDP": (28.5562, 77.1000),
    "OMDB": (25.2532, 55.3657),
    "OAKB": (34.5659, 69.2123),
    "KJFK": (40.6413, -73.7781),
    "KLAX": (33.9416, -118.4085),
    "EGLL": (51.4700, -0.4543),
    "LFPG": (49.0097, 2.5479),
    "EDDF": (50.0379, 8.5622),
    "RJTT": (35.5494, 139.7798),
}

# ---------------- Helpers ----------------
def alt_txt_m(f):
    v = f.get("geo_altitude")
    return "N/A" if v is None else f"{m_to_ft(v):.0f} ft"

def gs_txt_ms(f):
    v = f.get("velocity")
    return "N/A" if v is None else f"{ms_to_kts(v):.0f} kt"

def nearest_airport(lat: float, lon: float, airports=AIRPORTS) -> Tuple[str, float]:
    best_code, best_d = None, float("inf")
    for code, (alat, alon) in airports.items():
        d = haversine_m(lat, lon, alat, alon)
        if d < best_d:
            best_code, best_d = code, d
    return best_code or "N/A", best_d / 1000.0  # km

def trend(values, times):
    """Return simple slope per minute for last few points. values may contain None; ignore them."""
    pts = [(t, v) for t, v in zip(times, values) if v is not None]
    if len(pts) < 3: return 0.0
    t0 = pts[0][0]
    xs = np.array([p[0] - t0 for p in pts]) / 60.0  # minutes
    ys = np.array([p[1] for p in pts], dtype=float)
    if xs.ptp() == 0: return 0.0
    m = np.polyfit(xs, ys, 1)[0]
    return float(m)

def phase_from_trends(vs_fpm: float, gs_kts: Optional[float]) -> str:
    if vs_fpm > 500: return "Climb"
    if vs_fpm < -500: return "Descent"
    if gs_kts is not None and gs_kts > 250: return "Cruise"
    return "Level/Unknown"

def svg_plane_icon(heading_deg: float = 0.0, color: str = "#2563eb") -> str:
    """
    Lightweight SVG airplane marker rotated by heading.
    """
    return f"""
    <div style="transform: rotate({heading_deg:.0f}deg); transform-origin: center; width:20px; height:20px;">
      <svg viewBox="0 0 24 24" width="20" height="20">
        <path d="M11 2l1 6 8 4-8 4-1 6-2-6-6 4 3-8-3-8 6 4z" fill="{color}" fill-opacity="0.95" />
      </svg>
    </div>
    """

def add_rotated_marker(m, lat, lon, heading_deg, popup_html):
    icon_html = svg_plane_icon(heading_deg)
    folium.Marker(
        [lat, lon],
        popup=folium.Popup(popup_html, max_width=280),
        icon=folium.DivIcon(html=icon_html)
    ).add_to(m)

# ---------------- Session State ----------------
if "trails" not in st.session_state:
    st.session_state.trails = {}              # {icao24: [(lat,lon), ...]}
if "follow_icao24" not in st.session_state:
    st.session_state.follow_icao24 = None
if "telemetry" not in st.session_state:
    st.session_state.telemetry = {}           # {icao24: [(t, alt_ft, speed_kts), ...]}
if "map_view" not in st.session_state:
    st.session_state.map_view = {"center":[20.0, 0.0], "zoom":3}

# ---------------- Sidebar (modern, compact) ----------------
st.sidebar.title("✈ Flight Controls")
view_mode = st.sidebar.radio("View", ["2D Radar Map", "3D Globe"], index=0, horizontal=True)
callsign_filter = st.sidebar.text_input("Filter Callsign")
auto_follow = st.sidebar.toggle("Auto-Follow", value=True)

st.sidebar.subheader("📍 Jump to Airport")
airport_search = st.sidebar.text_input("ICAO (e.g., VABB, OMDB, KJFK)")
if airport_search:
    code = airport_search.strip().upper()
    if code in AIRPORTS:
        st.session_state.map_view = {"center": list(AIRPORTS[code]), "zoom": 7}
        st.sidebar.success(f"Centered on {code}")
    else:
        st.sidebar.error("Not in local list")

st.sidebar.markdown("---")
st.sidebar.caption("Tip: Click a plane in the **2D map** to start following it.")

# ---------------- Fetch flights ----------------
@st.cache_data(ttl=10, show_spinner=False)
def fetch():
    data = get_opensky_states(None)  # global
    rows = data.get("states") or []
    return [parse_state(r) for r in rows if r and r[5] and r[6]]

flights = fetch()
if callsign_filter:
    flights = [f for f in flights if callsign_filter.upper() in (f.get("callsign") or "")]

# ---------------- Update Telemetry (5 minutes, ~300 pts) ----------------
now_ts = time.time()
for f in flights:
    icao24 = f["icao24"]
    alt_ft = m_to_ft(f["geo_altitude"]) if f.get("geo_altitude") else None
    speed_kts = ms_to_kts(f["velocity"]) if f.get("velocity") else None
    hist = st.session_state.telemetry.get(icao24, [])
    hist.append((now_ts, alt_ft, speed_kts))
    st.session_state.telemetry[icao24] = hist[-300:]

# ---------------- 2D MODE ----------------
def map_2d():
    center = st.session_state.map_view["center"]
    zoom   = st.session_state.map_view["zoom"]

    # Auto-follow logic
    if auto_follow and st.session_state.follow_icao24:
        for f in flights:
            if f["icao24"] == st.session_state.follow_icao24:
                center = [f["lat"], f["lon"]]
                zoom = 6
                break

    m = folium.Map(location=center, zoom_start=zoom, tiles="cartodbpositron")

    for f in flights[:400]:
        lat, lon = f["lat"], f["lon"]
        ident = f["icao24"]

        # Airline
        calls = (f.get("callsign") or "").strip()
        airline, logo = AIRLINES.get(calls[:3], ("Unknown Airline", None))

        # Trail history
        trail = st.session_state.trails.get(ident, [])
        trail.append((lat, lon))
        trail = trail[-40:]
        st.session_state.trails[ident] = trail

        # Fading trail
        if len(trail) > 1:
            n = len(trail)
            for i in range(1, n):
                opacity = max(0.12, i/n)
                folium.PolyLine([trail[i-1], trail[i]], weight=3, opacity=opacity, color="#00A8FF").add_to(m)

        # Rotated icon + popup
        heading = f.get("true_track") or 0.0
        popup_html = (
            f"<div style='font-size:14px;'>"
            f"<b>{calls}</b><br><i>{airline}</i><br>"
            f"Alt: {alt_txt_m(f)} | GS: {gs_txt_ms(f)}<br>"
            f"Track: {heading:.0f}° | Last: {since(f.get('last_contact'))}"
            f"</div>"
        )
        add_rotated_marker(m, lat, lon, heading, popup_html)

        # Approx route (first seen -> current)
        if len(trail) >= 2:
            folium.PolyLine([trail[0], trail[-1]], weight=2, dash_array="6,6", color="#7E7E7E", opacity=0.6).add_to(m)

    out = st_folium(m, height=620, key="map2d", returned_objects=["last_object_clicked"])

    # Start following nearest clicked aircraft
    if out and out.get("last_object_clicked") and flights:
        latc, lonc = out["last_object_clicked"]["lat"], out["last_object_clicked"]["lng"]
        nearest = min(flights, key=lambda x: abs(x["lat"]-latc) + abs(x["lon"]-lonc))
        st.session_state.follow_icao24 = nearest["icao24"]

# ---------------- 3D MODE ----------------
def map_3d():
    df = pd.DataFrame([{
        "latitude": f["lat"],
        "longitude": f["lon"],
        "altitude_ft": m_to_ft(f["geo_altitude"]) if f.get("geo_altitude") else None,
        "callsign": f.get("callsign")
    } for f in flights])

    kmap = KeplerGl(height=620, data={"flights": df})
    components.html(kmap._repr_html_(), height=620)

# ---------------- AI Commentary (new) ----------------
def ai_commentary_card():
    st.markdown("### 🧠 AI Flight Commentary")
    if not st.session_state.follow_icao24:
        st.info("Select a flight on the 2D map to see live commentary.")
        return

    icao = st.session_state.follow_icao24
    # Find the current flight
    current = next((f for f in flights if f["icao24"] == icao), None)
    if not current:
        st.info("Selected flight not visible right now.")
        return

    calls = (current.get("callsign") or "").strip() or icao
    lat, lon = current["lat"], current["lon"]
    gs_kts = ms_to_kts(current.get("velocity")) if current.get("velocity") else None

    hist = st.session_state.telemetry.get(icao, [])
    if len(hist) >= 3:
        ts = [p[0] for p in hist[-40:]]
        alts = [p[1] for p in hist[-40:]]
        spds = [p[2] for p in hist[-40:]]
        vs_fpm = trend(alts, ts) if any(a is not None for a in alts) else 0.0
        ss_kts_per_min = trend(spds, ts) if any(s is not None for s in spds) else 0.0
    else:
        vs_fpm = 0.0
        ss_kts_per_min = 0.0

    phase = phase_from_trends(vs_fpm, gs_kts)
    near_ap, near_km = nearest_airport(lat, lon)
    # Approx origin from first trail point
    trail = st.session_state.trails.get(icao, [])
    origin_ap = None
    if trail:
        o_lat, o_lon = trail[0]
        origin_ap, o_km = nearest_airport(o_lat, o_lon)

    # Pretty KPIs
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Phase", phase)
    with c2:
        st.metric("Vert. speed", f"{vs_fpm:+.0f} fpm")
    with c3:
        st.metric("Speed trend", f"{ss_kts_per_min:+.1f} kt/min")

    # Narrative
    lines = []
    lines.append(f"**{calls}** is currently **{phase.lower()}**.")
    if gs_kts is not None:
        lines.append(f"Ground speed ~ **{gs_kts:.0f} kt**.")
    if abs(vs_fpm) > 100:
        dir_txt = "climbing" if vs_fpm > 0 else "descending"
        lines.append(f"It’s {dir_txt} at ~ **{abs(vs_fpm):.0f} fpm**.")
    if origin_ap:
        lines.append(f"Approx route observed: **{origin_ap} → near {near_ap}** ({near_km:.0f} km to {near_ap}).")
    else:
        lines.append(f"Nearest airport: **{near_ap}** (~{near_km:.0f} km).")

    st.markdown("> " + " ".join(lines))

# ---------------- FLIGHT DASHBOARD ----------------
def dashboard():
    st.markdown("### 📈 Live Flight Telemetry")
    if not st.session_state.follow_icao24:
        st.info("Click a plane in the 2D map to track flight details here.")
        return

    icao24 = st.session_state.follow_icao24
    data = st.session_state.telemetry.get(icao24, [])
    if len(data) < 3:
        st.info("Collecting telemetry…")
        return

    df = pd.DataFrame(data, columns=["t","alt_ft","speed_kts"])
    df["time_s"] = df["t"] - df["t"].iloc[0]

    # Compact, clean charts
    st.line_chart(df[["time_s","alt_ft"]].set_index("time_s"))
    st.line_chart(df[["time_s","speed_kts"]].set_index("time_s"))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Stop Following"):
            st.session_state.follow_icao24 = None
    with c2:
        code = st.text_input("METAR/TAF (ICAO)", "")
        if code:
            metar = get_metar(code)
            taf   = get_taf(code)
            if metar: st.code(metar.strip())
            if taf: st.code(taf.strip())

# ---------------- AIRPORT TRAFFIC (LOCAL LIST) ----------------
def airport_traffic_section():
    st.markdown("### 🗺 Airport Traffic (Local List)")
    colA, colB = st.columns(2)
    with colA:
        radius_km = st.slider("Radius (km)", 20, 300, 120, step=10)
    with colB:
        top_n = st.slider("Top N airports", 3, len(AIRPORTS), min(8, len(AIRPORTS)))

    rows = []
    for icao, (alat, alon) in AIRPORTS.items():
        cnt = 0
        for f in flights:
            d_km = haversine_m(alat, alon, f["lat"], f["lon"]) / 1000.0
            if d_km <= radius_km:
                cnt += 1
        rows.append({"airport": icao, "lat": alat, "lon": alon, "count": cnt})

    df_air = pd.DataFrame(rows).sort_values("count", ascending=False)

    # Bar
    bar = (
        alt.Chart(df_air.head(top_n))
        .mark_bar()
        .encode(
            x=alt.X("airport:N", title="Airport"),
            y=alt.Y("count:Q", title=f"Flights within {radius_km} km"),
            tooltip=["airport","count"]
        )
        .properties(height=220)
    )
    st.altair_chart(bar, use_container_width=True)

    # Scatter
    scatter = (
        alt.Chart(df_air)
        .mark_circle()
        .encode(
            x=alt.X("lon:Q", title="Longitude"),
            y=alt.Y("lat:Q", title="Latitude"),
            size=alt.Size("count:Q", legend=None, scale=alt.Scale(range=[0, 1000])),
            color=alt.Color("count:Q", title="Flights"),
            tooltip=["airport","count","lat","lon"]
        )
        .properties(height=320)
    )
    labels = (
        alt.Chart(df_air[df_air["count"] > 0])
        .mark_text(dy=-8, fontSize=11)
        .encode(x="lon:Q", y="lat:Q", text="airport:N")
    )
    st.altair_chart(scatter + labels, use_container_width=True)

# ---------------- Layout (modern & lightweight) ----------------
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem; padding-bottom: 1rem;}
      .stMetric {background: #f8fafc; border: 1px solid #eef2f7; border-radius: 10px; padding: 8px;}
      .stButton>button {border-radius:10px;}
    </style>
    """,
    unsafe_allow_html=True
)

st.title("AI Flight Tracker — Pro")

left, right = st.columns([2,1], vertical_alignment="top")

with left:
    if view_mode == "2D Radar Map":
        map_2d()
    else:
        map_3d()

with right:
    ai_commentary_card()
    st.markdown("---")
    dashboard()
    st.markdown("---")
    airport_traffic_section()

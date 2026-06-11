# AI Flight Tracker (Streamlit Mini Project)

Run:
1. python -m venv .venv && source .venv/bin/activate
2. pip install -r requirements.txt
3. streamlit run app.py

Features:
- Live flights from OpenSky (public REST)
- Map with trails, heading, and radar overlay
- Airport METAR/TAF lookup (NOAA)
- Auto-refresh

Notes:
- Free APIs have rate limits; reduce refresh rate or zoom into smaller area.
- Educational use only. Provide attribution to OpenSky and RainViewer.

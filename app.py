import streamlit as st
import pandas as pd
import requests
import urllib.parse
import os
import io
import gzip
import json
import time
from datetime import date, timedelta

st.set_page_config(page_title="Indian Stock Market Master Indices OHLC", layout="wide")

JSON_FILE = "upstox_all_indices.json"

# 1. Master Indices Cache / Auto-Updater Engine
def download_and_save_master_indices():
    """Downloads official Upstox complete master instruments and saves indexed tokens."""
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    res = requests.get(url, timeout=40)
    with gzip.open(io.BytesIO(res.content)) as f:
        df = pd.read_csv(f)
        
    df.columns = [c.strip().lower() for c in df.columns]
    key_col = 'instrument_key' if 'instrument_key' in df.columns else 'key'
    name_col = 'name' if 'name' in df.columns else df.columns[0]
    symbol_col = 'tradingsymbol' if 'tradingsymbol' in df.columns else ('symbol' if 'symbol' in df.columns else df.columns[0])

    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        if col in ['exchange', 'instrument_type', 'segment', 'instrument_key']:
            mask = mask | df[col].astype(str).str.contains('INDEX', case=False, na=False)

    indices_df = df[mask].copy()
    if key_col in df.columns:
        mask_key = df[key_col].astype(str).str.contains('_INDEX', case=False, na=False)
        indices_df = pd.concat([indices_df, df[mask_key]]).drop_duplicates()

    indices_catalog = {}
    for _, row in indices_df.iterrows():
        raw_name = str(row.get(name_col) or '').strip()
        raw_sym = str(row.get(symbol_col) or '').strip()
        raw_key = str(row.get(key_col) or '').strip()
        display_title = raw_name if raw_name and raw_name.lower() != 'nan' else raw_sym
        if display_title and raw_key and raw_key.lower() != 'nan':
            indices_catalog[display_title] = {"symbol": raw_sym, "instrument_key": raw_key}

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(indices_catalog, f, indent=4, ensure_ascii=False)
    return indices_catalog

@st.cache_data
def load_all_indices(force_refresh=False):
    """Loads indices from cache or downloads fresh file if older than 24 hours."""
    needs_download = False
    if force_refresh or not os.path.exists(JSON_FILE):
        needs_download = True
    else:
        if time.time() - os.path.getmtime(JSON_FILE) > 86400:
            needs_download = True

    if needs_download:
        try:
            return download_and_save_master_indices()
        except Exception:
            if os.path.exists(JSON_FILE):
                with open(JSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

INDICES_DATA = load_all_indices()

# 2. Dynamic Classification
def categorize_index(name, symbol):
    n = (name + " " + symbol).upper()
    if any(k in n for k in ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 200", "NIFTY 500", "MIDCAP", "SMALLCAP", "MICROCAP", "SENSEX", "TOTAL MKT"]):
        if not any(k in n for k in ["FINANCIAL", "HEALTHCARE", "IT"]):
            return "Broad Market"
    if any(k in n for k in ["EQUAL WGT", "EQL WGT", "VALUE", "ALPHA", "QUALITY", "LOW VOL", "HIGH BETA"]):
        return "Strategy"
    if any(k in n for k in ["COMMODITIES", "CPSE", "ENERGY", "HOUSING", "CONSUMPTION", "INFRA", "MOBILITY", "MNC", "PSE", "DEFENCE", "DIGITAL", "TOURISM"]):
        return "Thematic"
    return "Sectoral"

INDEX_ITEMS = {
    d: {"key": m["instrument_key"], "symbol": m["symbol"], "category": categorize_index(d, m["symbol"])}
    for d, m in INDICES_DATA.items()
}

# 3. Sidebar Controls
with st.sidebar:
    st.header("1. Parameters")
    all_cats = ["All Categories", "Sectoral", "Broad Market", "Thematic", "Strategy"]
    chosen_cat = st.selectbox("Category Filter", options=all_cats)

    available_list = sorted(list(INDEX_ITEMS.keys())) if chosen_cat == "All Categories" else sorted([k for k, v in INDEX_ITEMS.items() if v["category"] == chosen_cat])
    selected_index = st.selectbox("Select Index", options=available_list)
    timeframe = st.radio("Select Timeframe", options=["Daily", "Weekly"], horizontal=True)

    st.subheader("Date Range Selection")
    today = date.today()
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input("Start Date", value=today - timedelta(days=180), max_value=today)
    with col_d2:
        end_date = st.date_input("End Date", value=today, max_value=today)

    fetch_btn = st.button("Fetch Data", type="primary")

    st.divider()
    st.header("2. Master Registry Sync")
    if st.button("🔄 Sync Master Indices Now"):
        st.cache_data.clear()
        load_all_indices(force_refresh=True)
        st.success("Master indices successfully refreshed from Upstox!")
        st.rerun()

# 4. Upstox Historical Data Fetcher (URL Encoded)
def fetch_from_upstox(instrument_key, s_date, e_date):
    encoded_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/day/{e_date.strftime('%Y-%m-%d')}/{s_date.strftime('%Y-%m-%d')}"
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            candles = res.json().get("data", {}).get("candles", [])
            if candles:
                df = pd.DataFrame(candles, columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"])
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
                df = df[["Date", "Open", "High", "Low", "Close"]]
                for c in ["Open", "High", "Low", "Close"]:
                    df[c] = pd.to_numeric(df[c], errors='coerce').round(2)
                return df.sort_values(by="Date", ascending=True).reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()

# 5. True Trading-Week Resampling (Handles Monday/Friday Market Holidays)
def convert_to_weekly(daily_df):
    temp = daily_df.copy()
    temp["Date"] = pd.to_datetime(temp["Date"])
    temp = temp.sort_values(by="Date", ascending=True)
    iso = temp["Date"].dt.isocalendar()
    temp["Year"], temp["Week"] = iso.year, iso.week
    records = []
    for (_, _), group in temp.groupby(["Year", "Week"], sort=True):
        if not group.empty:
            records.append({
                "Date": group.iloc[-1]["Date"],
                "Open": group.iloc[0]["Open"],
                "High": group["High"].max(),
                "Low": group["Low"].min(),
                "Close": group.iloc[-1]["Close"]
            })
    return pd.DataFrame(records).sort_values(by="Date", ascending=True).reset_index(drop=True)

# 6. Main Application Interface
st.title("📈 Indian Stock Market Master Indices OHLC Explorer")
st.caption("Inspect verified daily and weekly OHLC market data across all NSE & BSE indices and download clean CSV reports.")

if (fetch_btn or selected_index) and INDEX_ITEMS:
    if start_date > end_date:
        st.error("Invalid Date Range: Start Date must be earlier than End Date.")
    else:
        selected_meta = INDEX_ITEMS[selected_index]
        with st.spinner(f"Fetching data for '{selected_index}' from Upstox..."):
            raw_df = fetch_from_upstox(selected_meta["key"], start_date, end_date)

        if raw_df.empty:
            st.warning("No data found for the selected date range.")
        else:
            display_df = convert_to_weekly(raw_df) if timeframe == "Weekly" else raw_df.copy()
            display_df["Date"] = display_df["Date"].dt.strftime('%Y-%m-%d')
            display_df = display_df.sort_values(by="Date", ascending=False).reset_index(drop=True)

            latest = display_df.iloc[0]
            prev = display_df.iloc[1] if len(display_df) > 1 else latest
            change = latest["Close"] - prev["Close"]
            pct = (change / prev["Close"]) * 100

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Latest Close", f"{latest['Close']:,.2f}", f"{change:+.2f} ({pct:+.2f}%)")
            c2.metric("Total Sessions", len(display_df))
            c3.metric("Timeframe", timeframe)
            c4.metric("Instrument Key", selected_meta["key"])

            st.divider()
            
            # Chart View
            chart_df = display_df.sort_values(by="Date").set_index("Date")
            st.subheader(f"{selected_index} ({timeframe}) Price Trend")
            st.line_chart(chart_df["Close"])

            # Data Table View
            st.subheader(f"{timeframe} OHLC Data Table")
            st.dataframe(display_df, use_container_width=True)

            # Clean Download Action
            st.divider()
            clean_filename = "".join([c for c in selected_index if c.isalnum() or c in " _-"]).replace(" ", "_")
            filename = f"{clean_filename}_{timeframe}_{start_date}_to_{end_date}.csv"
            csv_data = display_df.to_csv(index=False).encode('utf-8')
            
            st.download_button(
                label=f"📥 Download {selected_index} ({timeframe}) CSV",
                data=csv_data,
                file_name=filename,
                mime="text/csv",
                type="primary",
                use_container_width=True
            )
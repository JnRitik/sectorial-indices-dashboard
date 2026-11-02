import streamlit as st
import pandas as pd
import requests
import urllib.parse
import os
import io
import gzip
import json
import time
from datetime import date, timedelta, datetime
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import extra_streamlit_components as stx

st.set_page_config(page_title="Indian Stock Market Master Indices OHLC", layout="wide")

# Cookie Manager Setup
cookie_manager = stx.CookieManager()

JSON_FILE = "upstox_all_indices.json"
CLIENT_SECRET_FILE = "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]

# 1. Master Indices Cache / Auto-Updater
def download_and_save_master_indices():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    res = requests.get(url, timeout=40)
    with gzip.open(io.BytesIO(res.content)) as f:
        df = pd.read_csv(f)
    df.columns = [c.strip().lower() for c in df.columns]
    key_col = 'instrument_key' if 'instrument_key' in df.columns else 'key'
    name_col = 'name' if 'name' in df.columns else df.columns[0]
    symbol_col = 'tradingsymbol' if 'tradingsymbol' in df.columns else 'symbol'

    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        if col in ['exchange', 'instrument_type', 'segment', 'instrument_key']:
            mask = mask | df[col].astype(str).str.contains('INDEX', case=False, na=False)

    indices_df = df[mask].copy()
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
    if force_refresh or not os.path.exists(JSON_FILE) or (time.time() - os.path.getmtime(JSON_FILE) > 86400):
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

# 3. OAuth Flow Helpers (with 24h Persistent Cookie)
def get_redirect_uri():
    # Production Render URL ya local URL
    if "RENDER_EXTERNAL_URL" in os.environ:
        return os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
    return "http://localhost:8501"

def get_client_config():
    if "GOOGLE_OAUTH_CLIENT_CONFIG" in os.environ:
        return json.loads(os.environ["GOOGLE_OAUTH_CLIENT_CONFIG"])
    if os.path.exists(CLIENT_SECRET_FILE):
        with open(CLIENT_SECRET_FILE, "r") as f:
            return json.load(f)
    return None

def get_oauth_flow():
    cfg = get_client_config()
    if not cfg:
        return None
    redirect_uri = get_redirect_uri()
    flow = Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=redirect_uri)
    return flow

# Cookie / Session Recovery
user_token_json = cookie_manager.get(cookie="user_oauth_credentials")

if "user_credentials" not in st.session_state:
    if user_token_json:
        try:
            token_dict = json.loads(user_token_json)
            st.session_state["user_credentials"] = token_dict
        except Exception:
            st.session_state["user_credentials"] = None
    else:
        st.session_state["user_credentials"] = None

# OAuth Callback handling from query params
query_params = st.query_params
if "code" in query_params and not st.session_state.get("user_credentials"):
    code = query_params["code"]
    flow = get_oauth_flow()
    if flow:
        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            creds_data = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes
            }
            st.session_state["user_credentials"] = creds_data
            
            # Save in browser cookie with 24 hours expiry
            cookie_manager.set(
                "user_oauth_credentials",
                json.dumps(creds_data),
                expires_at=datetime.now() + timedelta(days=1)
            )
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Authentication exchange error: {e}")

# 4. Sidebar Interface
with st.sidebar:
    st.header("1. Parameters")
    all_cats = ["All Categories", "Sectoral", "Broad Market", "Thematic", "Strategy"]
    chosen_cat = st.selectbox("Category Filter", options=all_cats)

    available_list = sorted(list(INDEX_ITEMS.keys())) if chosen_cat == "All Categories" else sorted([k for k, v in INDEX_ITEMS.items() if v["category"] == chosen_cat])
    selected_index = st.selectbox("Select Index", options=available_list)
    timeframe = st.radio("Select Timeframe", options=["Daily", "Weekly"], horizontal=True)

    today = date.today()
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input("Start Date", value=today - timedelta(days=180), max_value=today)
    with col_d2:
        end_date = st.date_input("End Date", value=today, max_value=today)

    fetch_btn = st.button("Fetch Data", type="primary")

    st.divider()
    st.header("2. Google Account Link")
    if st.session_state.get("user_credentials"):
        st.success(" Connected to your Personal Google Account (Session active for 24h)")
        if st.button("🚪 Logout Google Account"):
            cookie_manager.delete("user_oauth_credentials")
            st.session_state["user_credentials"] = None
            st.rerun()
    else:
        st.info("Log in with Google to push sheets directly to YOUR Drive.")
        flow = get_oauth_flow()
        if flow:
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            st.markdown(f'<a href="{auth_url}" target="_self"><button style="background-color:#4285F4;color:white;padding:8px 16px;border:none;border-radius:4px;cursor:pointer;width:100%;">🔑 Sign In with Google</button></a>', unsafe_allow_html=True)
        else:
            st.warning("OAuth client secret missing.")

    st.divider()
    sheet_identifier = st.text_input("Spreadsheet Name or URL", value="NSE_Indices_Data")
    worksheet_name = st.text_input("Worksheet Tab Name", value=f"{selected_index[:20]}_{timeframe}")

# 5. Core Data Fetching & Resampling
def fetch_from_upstox(instrument_key, s_date, e_date):
    encoded_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/day/{e_date.strftime('%Y-%m-%d')}/{s_date.strftime('%Y-%m-%d')}"
    try:
        res = requests.get(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=15)
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

# 6. Push to User's Personal Google Sheet
def push_to_user_google_sheet(dataframe, sheet_target, ws_title, creds_dict):
    try:
        creds = Credentials(
            token=creds_dict["token"],
            refresh_token=creds_dict.get("refresh_token"),
            token_uri=creds_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=creds_dict.get("client_id"),
            client_secret=creds_dict.get("client_secret"),
            scopes=creds_dict.get("scopes", SCOPES)
        )
        gc = gspread.authorize(creds)
        target = sheet_target.strip()

        # Open existing sheet or create new in user's Drive
        try:
            sh = gc.open_by_url(target) if target.startswith("https://") else gc.open(target)
        except gspread.SpreadsheetNotFound:
            sh = gc.create(target)

        safe_tab = "".join([c for c in ws_title if c.isalnum() or c in " _-"])[:30]
        try:
            ws = sh.worksheet(safe_tab)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=safe_tab, rows=str(len(dataframe) + 50), cols="10")

        ws.clear()
        clean_df = dataframe.copy()
        clean_df["Date"] = clean_df["Date"].astype(str)
        data_to_write = [clean_df.columns.values.tolist()] + clean_df.values.tolist()
        ws.update(range_name='A1', values=data_to_write)
        return True, sh.url
    except Exception as e:
        return False, str(e)

# 7. Render Data & UI
st.title("📈 Indian Stock Market Master Indices OHLC Explorer")
st.caption("Inspect verified daily and weekly OHLC market data and export directly to YOUR personal Google Sheets.")

if (fetch_btn or selected_index) and INDEX_ITEMS:
    if start_date > end_date:
        st.error("Invalid Date Range: Start Date must be earlier than End Date.")
    else:
        selected_meta = INDEX_ITEMS[selected_index]
        with st.spinner(f"Loading '{selected_index}' from Upstox..."):
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
            chart_df = display_df.sort_values(by="Date").set_index("Date")
            st.line_chart(chart_df["Close"])
            st.dataframe(display_df, use_container_width=True)

            st.divider()
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"#### ⚡ Push to YOUR Google Sheets")
                if st.button("🚀 Push to Personal Google Sheet", type="primary"):
                    if not st.session_state.get("user_credentials"):
                        st.error("Please login with Google in the sidebar first!")
                    else:
                        with st.spinner("Writing data directly to your personal Google Drive..."):
                            success, result = push_to_user_google_sheet(
                                display_df, sheet_identifier, worksheet_name, st.session_state["user_credentials"]
                            )
                            if success:
                                st.success(f"Successfully pushed! [Open Your Google Sheet]({result})")
                            else:
                                st.error(f"Sync Error: {result}")

            with col_b:
                st.markdown("#### 📁 Local CSV Export")
                csv_data = display_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Download CSV", csv_data, f"{selected_index}_{timeframe}.csv", "text/csv")
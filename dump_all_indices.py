import gzip
import io
import json
import requests
import pandas as pd

print("1. Upstox complete master instrument file download ho rahi hai...")
url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
res = requests.get(url, timeout=40)

print("2. Decompress aur parse kiya ja raha hai...")
with gzip.open(io.BytesIO(res.content)) as f:
    df = pd.read_csv(f)

# Column names print karke verify karein
print(f"Columns available: {list(df.columns)}")

# Columns ko lower case karke normalize karein
df.columns = [c.strip().lower() for c in df.columns]

# Instrument key column dhundhein
key_col = 'instrument_key' if 'instrument_key' in df.columns else None
if not key_col:
    for c in df.columns:
        if 'key' in c:
            key_col = c
            break

# Name aur symbol columns
name_col = 'name' if 'name' in df.columns else df.columns[0]
symbol_col = 'tradingsymbol' if 'tradingsymbol' in df.columns else ('symbol' if 'symbol' in df.columns else df.columns[0])

# Index filter conditions check karein across available columns
mask = pd.Series(False, index=df.index)

for col in df.columns:
    if col in ['exchange', 'instrument_type', 'segment', 'exchange_token', 'instrument_key']:
        mask = mask | df[col].astype(str).str.contains('INDEX', case=False, na=False)

indices_df = df[mask].copy()

# Agar upar se direct index flag kam mile toh instrument_key me '_INDEX|' check karein
if key_col and key_col in df.columns:
    mask_key = df[key_col].astype(str).str.contains('_INDEX', case=False, na=False)
    indices_df = pd.concat([indices_df, df[mask_key]]).drop_duplicates()

indices_catalog = {}

for _, row in indices_df.iterrows():
    raw_name = str(row.get(name_col) or '').strip()
    raw_sym = str(row.get(symbol_col) or '').strip()
    raw_key = str(row.get(key_col) or '').strip() if key_col else ''

    # Clean display title
    display_title = raw_name if raw_name and raw_name.lower() != 'nan' else raw_sym
    
    # Sirf valid keys save karein
    if display_title and raw_key and raw_key.lower() != 'nan':
        indices_catalog[display_title] = {
            "symbol": raw_sym,
            "instrument_key": raw_key
        }

output_filename = "upstox_all_indices.json"
with open(output_filename, "w", encoding="utf-8") as f:
    json.dump(indices_catalog, f, indent=4, ensure_ascii=False)

print(f"3. Kaam complete! Total {len(indices_catalog)} indices extract ho gaye.")
print(f"File save ho gayi hai: '{output_filename}'")
import pandas as pd
from sqlalchemy import create_engine

# Updated connection string using the pooler endpoint and pooler username format
# Note: User format for poolers requires 'postgres.PROJECT_REF'
db_url = "postgresql://postgres.nspxumwsqewqfxuaepru:trendcast123A@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"

# Replace 'your_table_name' with your actual table name in Supabase
table_name = "view_timeseries"

print("Connecting and downloading data...")
engine = create_engine(db_url, connect_args={"options": "-c statement_timeout=1800000"})

chunksize = 100000
first = True

# Read via raw SQL query to ensure smooth streaming
query = f"SELECT * FROM {table_name}"

for chunk in pd.read_sql_query(query, engine, chunksize=chunksize):
    chunk.to_csv("exported_data.csv", mode="a" if not first else "w", index=False)
    first = False
    print("Chunk exported...")

print("Export Complete! Saved to D:\\DSEP\\datasets\\exported_data.csv")
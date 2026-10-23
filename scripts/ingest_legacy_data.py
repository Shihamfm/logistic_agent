from pathlib import Path
import pandas as pd
import urllib
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

#__file__ is scripts\ingest_legacy_data.py
script_dir = Path(__file__).resolve().parent # points to scripts folder
project_root = script_dir.parents[0] # climbs up 1 levels to project root folder

load_dotenv()

data_path = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"

db_host = os.getenv("SQL_SERVER_HOST")
db_port = os.getenv("SQL_SERVER_PORT")
db_username = os.getenv("SQL_ADMIN_USERNAME")
db_password = os.getenv("SQL_ADMIN_PASSWORD")

# Load the raw dataset
df = pd.read_csv(data_path)

# Map the columns to the new schema
schema_mapping = {
    'timestamp': 'TS_UTC',
    'vehicle_gps_latitude': 'V_LAT',
    'vehicle_gps_longitude': 'V_LON',
    'iot_temperature': 'IOT_TEMP_VAL_C',
    'cargo_condition_status': 'CGO_COND_CD',
    'risk_classification': 'RISK_CLS_TXT',
    'delay_probability': 'DELAY_PROB_DEC',
    'port_congestion_level': 'PRT_CNG_LVL',
    'route_risk_level': 'RT_RSK_IDX'
}

# Keep only the columns we mapped for this demo and renammed them
df_legacy = df[
    list(schema_mapping.keys())
].rename(columns=schema_mapping)

# Add an ingestion flag
df_legacy['SYS_INGEST_FLAG'] = 'Y'

# Connect to Docker MSSQL Server
# Use the pyodbc driver. (Ensure ODBC Driver 17/18 for SQL server installed on the OS)

connection_string = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={db_host},{db_port};"
    f"DATABASE=master;"
    f"UID={db_username};"
    f"PWD={db_password};"
    f"Encrypto=no;"
    f"TrustServerCertificate=yes;"
)

params = urllib.parse.quote_plus(connection_string)

engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}")

# Ingest large data into the table 
table_name= 'supply_chain_hist_raw_data'
df_legacy.to_sql(table_name, con=engine, if_exists='replace', index=False, schema='dbo')




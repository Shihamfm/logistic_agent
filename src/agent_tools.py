import os
import urllib
from pathlib import Path
import requests

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone
from langchain_core.tools import tool
from dotenv import load_dotenv

from sqlalchemy import create_engine, text

load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
EMBEDDINGS_MODEL_SETTING = os.getenv("Embeddings_model", "LOCAL").strip().upper()

#__file__ is src\tools\agent_tools.py
script_dir = Path(__file__).resolve().parent # points to scripts folder
project_root = script_dir.parents[0] # climbs up 1 levels to project root folder

db_host = os.getenv("SQL_SERVER_HOST", "local_host")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_AGENT_USERNAME", "USR_FDE_RO")
db_pw = os.getenv("SQL_AGENT_PASSWORD", "FDEAgentPassword2026")

if not PINECONE_API_KEY:
    raise ValueError("PINECONE_API_KEY is not set in the environment variables.")

if EMBEDDINGS_MODEL_SETTING == "OPENAI":
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
    INDEX_NAME = "fde-sop-openai-embeddings"
    TARGET_DIMENSION = 1536
else:
    local_model_target = os.getenv("Local_Embedding_Model", "sentence-transformers/all-MiniLM-L6-v2").strip()
    embeddings = HuggingFaceEmbeddings(
        model_name=local_model_target,
        model_kwargs={"device": "cpu"}  # Use CPU for inference
        )
    INDEX_NAME = "fde-sop-local-embeddings"
    TARGET_DIMENSION = 384

print(f"Connecting to {INDEX_NAME}")
pc = Pinecone(api_key=PINECONE_API_KEY)
index_client = pc.Index(INDEX_NAME)

vector_store = PineconeVectorStore(index=index_client, embedding=embeddings)
retreiver = vector_store.as_retriever(search_kwargs={"k":2})

@tool
def query_telemetry_db(sql_query: str) -> str:
    """
    Executes a SQL SELECT query against the FDE_VIEWS.VW_ACTIVE_FLEET view.
    Columns available:
    Timestamp, Latitude, Longitude, Current_Temperature_C, Cargo_Condition_Code,
    Risk_Classification, Delay_Probability, Port_Congestion_Level, Route_Risk_Index.
    Always write standard T-SQL queries.
    """
    connection_string = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={db_host},{db_port};"
    f"DATABASE=master;"
    f"UID={db_user};"
    f"PWD={db_pw};"
    f"Encrypto=no;"
    f"TrustServerCertificate=yes;"
    )
    params = urllib.parse.quote_plus(connection_string)

    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}")

    try:
        if not sql_query.strip().upper().startswith("SELECT"):
            return "SECURITY BLOCK: Only SELECT operations are authorized on this view"

        with engine.connect() as conn:
            cursor = conn.execute(text(sql_query))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(10)

            if not rows:
                return "No records matched the query criteria"

            formatted_output = f"COLUMNS: {', '.join(columns)}\n"
            for row in rows:
                formatted_output += str(tuple(row)) + "\n"

            return formatted_output
    except Exception as e:
        return f"Database Error: {str(e)}"


@tool
def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
    """
    Fetches real-time weather and corridor conditions from a live REST API for given GPS coordinates.
    Provides temperature, wind speed, and computed corridor congestion index.
    """
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
        response = requests.get(url)
        response.raise_for_status()

        payload = response.json().get("current_weather",{})
        temp = payload.get("temperature", "N/A")
        wind  = payload.get("windspeed", 0.0)

        congestion_index = 8.5 if wind > 10.0 else 2.5
        status_note = "High Transit Disruption" if wind > 10.0 else "Corridor Normal"

        return(
            f"--- LIVE CORRIDOR TELEMETRY ---\n"
            f"Target GPS: {latitude}, {longitude}\n"
            f"External Temp: {temp}°C | Wind Speed: {wind} km/h\n"
            f"Corridor Risk: {status_note} (Congestion Index: {congestion_index}/10)\n"
            f"-------------------------------"
        )
    except Exception as e:
        return f"Corridor API Communication failure: {str(e)}"


@tool
def search_compliance_sop(query: str) -> str:
    """
    Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.
    Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.
    """
    try:
        matched_docs = retreiver.invoke(query)
        if not matched_docs:
            return "No matching compliance clauses found"

        formatted_context = "\n\n".join(
            [f"[Source: {doc.metadata.get('source_file', 'SOP')} | Format: {doc.metadata.get('file_format', 'RAW')}]\n{doc.page_content}" for doc in matched_docs]                               
        )
        return f"--- COMPLIANCE SOP CONTEXT ---\n{formatted_context}\n------------------------------"
    except Exception as e:
        return f"Vector Store Retrieval Error: {str(e)}"


# # Local_Verification
# if __name__ == "__main__":
#     print("\n--- Testing Tool 1: SQL Telemetry View ---")
#     print(query_telemetry_db.invoke("SELECT TOP 2 Latitude, Longitude, Current_Temperature_C FROM FDE_VIEWS.VW_ACTIVE_FLEET"))
    
#     print("\n--- Testing Tool 2: Live Corridor API ---")
#     print(fetch_corridor_conditions.invoke({"latitude": 33.77, "longitude": -118.19}))
    
#     print("\n--- Testing Tool 3: Pinecone Vector Retrieval ---")
#     print(search_compliance_sop.invoke("What are the temperature rules for fresh perishables?"))



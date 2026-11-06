import os
import sys
import json
import urllib.parse
from pathlib import Path
from typing import Generator
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
import uvicorn

# ==========================================
# 1. IMMEDIATE PATH & ENVIRONMENT RESOLUTION
# ==========================================
BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")

# Import the compiled graph from src/
from src.orchestrator import fde_agent

# ==========================================
# 2. LOAD SYSTEM PROMPT
# ==========================================
PROMPT_PATH = BASE_DIR / "src" / "prompts" / "system_prompt.txt"
try:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_INSTRUCTION = f.read().strip()
except FileNotFoundError:
    print(f"Warning: Prompt file not found at {PROMPT_PATH}. Falling back to default.")
    SYSTEM_INSTRUCTION = "You are a Senior Logistics Incident Commander. Output using the standard 3-section format."

SYSTEM_PROMPT = SystemMessage(content=SYSTEM_INSTRUCTION)

# ==========================================
# 3. FASTAPI APP INITIALIZATION & CORS
# ==========================================
app = FastAPI(
    title="FDE Supply Chain Dispatch Console",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ==========================================
# 4. SQL CREDENTIALS & AUDIT LOGGING ENGINE
# ==========================================
db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_name = os.getenv("SQL_SERVER_DB", "master")
db_user = os.getenv("SQL_AGENT_USER", "USR_FDE_RO")
db_password = os.getenv("SQL_AGENT_PASSWORD")

connection_string = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={db_host},{db_port};"
    f"DATABASE={db_name};"
    f"UID={db_user};"
    f"PWD={db_password};"
    f"Encrypt=no;"
    f"TrustServerCertificate=yes;"
)

log_params = urllib.parse.quote_plus(connection_string)
log_engine = create_engine(f"mssql+pyodbc:///?odbc_connect={log_params}")

def write_audit_log(session_id: str, node_name: str, tool_name: str, content: str):
    """Silently writes agent execution traces to the SQL audit table."""
    try:
        with log_engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO FDE_VIEWS.AgentAuditLog (SessionID, NodeExecuted, ToolName, Content)
                    VALUES (:session_id, :node_name, :tool_name, :content)
                """),
                {
                    "session_id": session_id,
                    "node_name": node_name,
                    "tool_name": tool_name,
                    "content": content
                }
            )
            conn.commit()
    except Exception as e:
        print(f"Audit Log Failed (Silent): {e}")

# ==========================================
# 5. REQUEST SCHEMAS
# ==========================================
class ChatRequest(BaseModel):
    message: str
    thread_id: str

class AdminCredentials(BaseModel):
    username: str
    password: str

# ==========================================
# 6. ROUTES & ENDPOINTS
# ==========================================
@app.get("/")
def serve_home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )

@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """
    Executes the LangGraph agent and streams execution events, tool traces,
    and responses using Server-Sent Events (SSE).
    """
    thread_config = {"configurable": {"thread_id": req.thread_id}}

    # Inspect checkpoint memory: seed SystemMessage if this thread is new
    current_state = fde_agent.get_state(thread_config)
    existing_messages = current_state.values.get("messages", []) if current_state else []

    if not existing_messages:
        messages_to_send = [SYSTEM_PROMPT, HumanMessage(content=req.message)]
    else:
        messages_to_send = [HumanMessage(content=req.message)]

    def event_generator() -> Generator[str, None, None]:
        try:
            events = fde_agent.stream(
                {"messages": messages_to_send},
                config=thread_config,
                stream_mode="updates"
            )

            for event in events:
                for node_name, node_state in event.items():
                    if node_name == "reasoner":
                        latest_msg = node_state["messages"][-1]

                        # Intercept Tool Calls (Intent Recognition)
                        if hasattr(latest_msg, "tool_calls") and latest_msg.tool_calls:
                            for tc in latest_msg.tool_calls:
                                payload = {
                                    "type": "tool_input",
                                    "name": tc["name"],
                                    "args": tc["args"]
                                }
                                yield f"data: {json.dumps(payload)}\n\n"
                                write_audit_log(
                                    session_id=req.thread_id,
                                    node_name="reasoner",
                                    tool_name=tc["name"],
                                    content=json.dumps(tc["args"])
                                )

                        # Intercept Generated Text Content
                        if latest_msg.content:
                            payload = {
                                "type": "content",
                                "text": latest_msg.content
                            }
                            yield f"data: {json.dumps(payload)}\n\n"
                            write_audit_log(
                                session_id=req.thread_id,
                                node_name="reasoner_final",
                                tool_name="LLM Text Synthesis",
                                content=latest_msg.content
                            )

                    elif node_name == "tools":
                        for msg in node_state.get("messages", []):
                            if isinstance(msg, ToolMessage):
                                payload = {
                                    "type": "tool_output",
                                    "name": msg.name,
                                    "content": msg.content
                                }
                                yield f"data: {json.dumps(payload)}\n\n"
                                write_audit_log(
                                    session_id=req.thread_id,
                                    node_name="tools",
                                    tool_name=msg.name,
                                    content=msg.content
                                )

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as err:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(err)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/audit/logs")
def get_audit_logs(creds: AdminCredentials):
    """
    Validates admin credentials and fetches audit records directly from SQL Server.
    """
    expected_admin_user = os.getenv("SQL_ADMIN_USERNAME")
    expected_admin_pass = os.getenv("SQL_ADMIN_PASSWORD")

    if not expected_admin_user or not expected_admin_pass:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin credentials are not configured on the server."
        )

    if creds.username != expected_admin_user or creds.password != expected_admin_pass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Administrator Credentials."
        )

    try:
        admin_conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={db_host},{db_port};"
            f"DATABASE={db_name};"
            f"UID={expected_admin_user};"
            f"PWD={expected_admin_pass};"
            f"Encrypt=no;"
            f"TrustServerCertificate=yes;"
        )
        
        admin_params = urllib.parse.quote_plus(admin_conn_str)
        admin_engine = create_engine(f"mssql+pyodbc:///?odbc_connect={admin_params}")

        with admin_engine.connect() as conn:
            result = conn.execute(text("""
                SELECT LogID, Timestamp, SessionID, NodeExecuted, ToolName, Content
                FROM FDE_VIEWS.AgentAuditLog
                ORDER BY Timestamp DESC
            """))
            rows = [dict(row._mapping) for row in result]

            for row in rows:
                if "Timestamp" in row and row["Timestamp"] is not None:
                    row["Timestamp"] = str(row["Timestamp"])

            return {"logs": rows}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database Query Failed: {str(e)}"
        )

# ==========================================
# 7. APPLICATION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
import os
import sys
import json
import time
import logging
from typing import Optional, Dict, Any, List
import httpx
from fastapi import FastAPI, Request, Response, Form, File, UploadFile, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s"
)
logger = logging.getLogger("mcp_dashboard")

app = FastAPI(title="MCP Mission Control & Bucketloader Manager", version="1.0.0")

# Service Configuration
AGYC_MCP_URL = os.environ.get("AGYC_MCP_URL", "http://127.0.0.1:8001")
BUCKET_URL = os.environ.get("BUCKET_URL", "http://127.0.0.1:8090")

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)

def get_bucket_api_key() -> str:
    """Retrieve BUCKET_API_KEY from environment or bucketloader .env file."""
    key = os.environ.get("BUCKET_API_KEY")
    if key:
        return key.strip()
    
    candidates = [
        "/home/azureadmin/bucketloader/.env",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bucketloader", ".env"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("BUCKET_API_KEY=") and not line.startswith("#"):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
            except Exception:
                pass
    return ""

async def fetch_agyc_status() -> Dict[str, Any]:
    """Fetch live projects, tasks, and routes directly from agyc-mcp internal server."""
    result = {
        "projects": [],
        "tasks": [],
        "routes": [],
        "usage": {},
        "online": False
    }
    try:
        # Import directly if running on the same host for maximum performance
        sys.path.insert(0, "/home/azureadmin/agyc-mcp")
        try:
            from src.server import list_projects, list_tasks, list_routes, get_agyc_usage
            result["projects"] = list_projects()
            result["tasks"] = list_tasks(limit=30)
            result["routes"] = list_routes()
            try:
                result["usage"] = get_agyc_usage(timeout=5)
            except Exception:
                result["usage"] = {}
            result["online"] = True
            return result
        except Exception as e:
            logger.debug("Direct import fallback: %s", e)

        # Fallback to HTTP API
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{AGYC_MCP_URL}/")
            if resp.status_code == 200:
                result["online"] = True
    except Exception as e:
        logger.debug("agyc-mcp status fetch exception: %s", e)
    return result

async def fetch_bucket_health() -> Dict[str, Any]:
    """Fetch health and authentication status of Bucketloader service."""
    url = f"{BUCKET_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            return {"status": "error", "drive_authenticated": False, "http_status": resp.status_code}
    except Exception as e:
        return {"status": "error", "drive_authenticated": False, "error": str(e)}

async def fetch_bucket_files() -> List[Dict[str, Any]]:
    """Fetch stored files from Google Drive bucket."""
    api_key = get_bucket_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{BUCKET_URL}/files"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            return []
    except Exception as e:
        logger.warning("Failed to fetch bucket files: %s", e)
        return []

# ==============================================================================
# HTML Pages: Dashboard & Bucket Manager (Dual Routing for / and /mcpd)
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
@app.get("/mcpd", response_class=HTMLResponse)
@app.get("/mcpd/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Main MCP Control Center dashboard."""
    status_data = await fetch_agyc_status()
    bucket_data = await fetch_bucket_health()
    template = jinja_env.get_template("dashboard.html")
    html_content = template.render(
        status=status_data,
        bucket=bucket_data,
        request=request
    )
    return HTMLResponse(content=html_content)

@app.get("/bucket", response_class=HTMLResponse)
@app.get("/bucket/", response_class=HTMLResponse)
@app.get("/mcpd/bucket", response_class=HTMLResponse)
@app.get("/mcpd/bucket/", response_class=HTMLResponse)
async def bucket_manager_page(request: Request):
    """Interactive Bucketloader File Manager page."""
    bucket_data = await fetch_bucket_health()
    files = await fetch_bucket_files()
    template = jinja_env.get_template("bucket.html")
    html_content = template.render(
        bucket=bucket_data,
        files=files,
        request=request
    )
    return HTMLResponse(content=html_content)

# ==============================================================================
# JSON API Endpoints (Dual Routing for /api/... and /mcpd/api/...)
# ==============================================================================

@app.get("/api/status")
@app.get("/mcpd/api/status")
async def api_status():
    """Live JSON status for AJAX polling."""
    agyc = await fetch_agyc_status()
    bucket = await fetch_bucket_health()
    return {
        "agyc": agyc,
        "bucket": bucket,
        "timestamp": time.time()
    }

@app.post("/api/tasks/dispatch")
@app.post("/mcpd/api/tasks/dispatch")
async def api_dispatch_task(
    project_name: str = Form(...),
    prompt: str = Form(...),
    model: Optional[str] = Form(None),
    effort: Optional[str] = Form(None),
    timeout: int = Form(600),
):
    """Queues a new autonomous coding task in agyc-mcp."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import queue_task
    try:
        record = queue_task(
            project_name=project_name,
            task=prompt,
            model=model if model else None,
            effort=effort if effort else None,
            timeout=timeout
        )
        return {"success": True, "task": record}
    except Exception as e:
        logger.exception("Task dispatch error: %s", e)
        return {"success": False, "error": str(e)}

@app.post("/api/tasks/{task_id}/input")
@app.post("/mcpd/api/tasks/{task_id}/input")
async def api_send_task_input(
    task_id: str,
    input_text: str = Form(...),
    press_enter: bool = Form(True),
):
    """Sends stdin input directly to a running task shell."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import send_input_to_task
    try:
        res = send_input_to_task(task_id=task_id, input_text=input_text, press_enter=press_enter)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/tasks/{task_id}/cancel")
@app.post("/mcpd/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str):
    """Cancels a running or queued task."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import cancel_task
    try:
        res = cancel_task(task_id=task_id)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/tasks/{task_id}")
@app.get("/mcpd/api/tasks/{task_id}")
async def api_get_task_details(task_id: str):
    """Retrieves detailed logs and state for a specific task."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import get_task_status
    try:
        return get_task_status(task_id=task_id)
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/projects/commit")
@app.post("/mcpd/api/projects/commit")
async def api_project_commit(
    project_name: str = Form(...),
    message: Optional[str] = Form(None),
):
    """Manually triggers git commit and push for a project."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import git_commit_and_push
    try:
        res = git_commit_and_push(project_name=project_name, message=message if message else None)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/commands/run")
@app.post("/mcpd/api/commands/run")
async def api_run_command(
    command: str = Form(...),
    timeout: int = Form(30),
):
    """Executes an agyc slash command (e.g. /usage, /models)."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import run_agyc_command
    try:
        res = run_agyc_command(command=command, timeout=timeout)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}

# ==============================================================================
# Bucketloader Management APIs
# ==============================================================================

@app.get("/api/bucket/files")
@app.get("/mcpd/api/bucket/files")
async def api_bucket_files():
    """Lists files in the Google Drive bucket."""
    files = await fetch_bucket_files()
    return files

@app.post("/api/bucket/upload")
@app.post("/mcpd/api/bucket/upload")
async def api_bucket_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
):
    """Uploads a file directly into the Google Drive bucket."""
    api_key = get_bucket_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{BUCKET_URL}/upload"
    
    file_bytes = await file.read()
    files = {"file": (file.filename, file_bytes, file.content_type)}
    data = {"overwrite": str(overwrite).lower()}
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, files=files, data=data, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse({"error": resp.text}, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/bucket/save_text")
@app.post("/mcpd/api/bucket/save_text")
async def api_bucket_save_text(
    filename: str = Form(...),
    content: str = Form(...),
    overwrite: bool = Form(True),
):
    """Creates or overwrites a text/markdown/code file in the Google Drive bucket."""
    api_key = get_bucket_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{BUCKET_URL}/upload"
    
    content_bytes = content.encode("utf-8")
    files = {"file": (filename, content_bytes, "text/plain")}
    data = {"overwrite": str(overwrite).lower()}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, files=files, data=data, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse({"error": resp.text}, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/bucket/files/{file_id}/content")
@app.get("/mcpd/api/bucket/files/{file_id}/content")
async def api_bucket_file_content(file_id: str):
    """Retrieves file content for preview or text editor."""
    api_key = get_bucket_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{BUCKET_URL}/files/{file_id}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return JSONResponse({"error": resp.text}, status_code=resp.status_code)
            
            raw_bytes = resp.content
            try:
                text_content = raw_bytes.decode("utf-8")
                return {"type": "text", "content": text_content, "size": len(raw_bytes)}
            except UnicodeDecodeError:
                import base64
                b64 = base64.b64encode(raw_bytes).decode("ascii")
                mime = resp.headers.get("content-type", "application/octet-stream")
                return {"type": "binary", "mime": mime, "base64": b64, "size": len(raw_bytes)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/api/bucket/files/{file_id}")
@app.delete("/mcpd/api/bucket/files/{file_id}")
async def api_bucket_delete_file(file_id: str):
    """Deletes/trashes a file from the Google Drive bucket."""
    api_key = get_bucket_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{BUCKET_URL}/files/{file_id}"
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse({"error": resp.text}, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8096, reload=False)

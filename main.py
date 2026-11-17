import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import logging
import asyncio
import subprocess
from typing import Optional, Dict, Any, List, Tuple
import httpx
from fastapi import FastAPI, Request, Response, Form, File, UploadFile, HTTPException, Query, Header, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s"
)
logger = logging.getLogger("mcp_dashboard")

app = FastAPI(title="AGYC Dashboard & Bucketloader Manager", version="2.1.0")

# ==============================================================================
# Security & Authentication Configuration
# ==============================================================================

AUTH_USERNAME = os.environ.get("DASHBOARD_USERNAME", "korrectional")
AUTH_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Minegame.1")
SESSION_COOKIE_NAME = "agyc_session"
SESSION_MAX_AGE = 7 * 86400  # 7 days in seconds

SECRET_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")

def get_or_create_secret_key() -> str:
    env_key = os.environ.get("DASHBOARD_SECRET_KEY")
    if env_key:
        return env_key.strip()
    if os.path.exists(SECRET_KEY_PATH):
        try:
            with open(SECRET_KEY_PATH, "r", encoding="utf-8") as f:
                k = f.read().strip()
                if k:
                    return k
        except Exception:
            pass
    new_key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_PATH, "w", encoding="utf-8") as f:
            f.write(new_key)
    except Exception:
        pass
    return new_key

SECRET_KEY = get_or_create_secret_key()

class SecurityRateLimiter:
    """Anti-bruteforce progressive backoff and IP lockout protection."""
    MAX_ATTEMPTS = 5
    LOCKOUT_DURATION = 900  # 15 minutes in seconds

    def __init__(self):
        # ip -> {"attempts": int, "lockout_until": float, "last_attempt": float}
        self.records: Dict[str, Dict[str, Any]] = {}

    def get_client_ip(self, request: Request) -> str:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        x_real = request.headers.get("x-real-ip")
        if x_real:
            return x_real.strip()
        if request.client and request.client.host:
            return request.client.host
        return "127.0.0.1"

    def check_lockout(self, ip: str) -> Tuple[bool, int]:
        rec = self.records.get(ip)
        if not rec:
            return False, 0
        lockout_until = rec.get("lockout_until", 0)
        now = time.time()
        if now < lockout_until:
            remaining = int(lockout_until - now)
            return True, remaining
        if lockout_until > 0 and now >= lockout_until:
            self.records.pop(ip, None)
        return False, 0

    async def register_failure(self, ip: str) -> Tuple[int, int, float]:
        """
        Records a failed attempt. Calculates progressive exponential backoff.
        Returns (attempt_count, remaining_attempts_before_lockout, delay_seconds).
        """
        now = time.time()
        rec = self.records.get(ip)
        if not rec or (now - rec.get("last_attempt", 0) > 3600):
            rec = {"attempts": 1, "lockout_until": 0, "last_attempt": now}
        else:
            rec["attempts"] += 1
            rec["last_attempt"] = now

        count = rec["attempts"]
        delay = min(8.0, float(2 ** (count - 1)))

        if count >= self.MAX_ATTEMPTS:
            rec["lockout_until"] = now + self.LOCKOUT_DURATION
            remaining = 0
        else:
            remaining = self.MAX_ATTEMPTS - count

        self.records[ip] = rec
        if delay > 0:
            await asyncio.sleep(delay)

        return count, remaining, delay

    def register_success(self, ip: str):
        self.records.pop(ip, None)

security_limiter = SecurityRateLimiter()

def create_session_token(username: str) -> str:
    ts = str(int(time.time()))
    payload = f"{username}:{ts}"
    sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def verify_session_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    username, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > SESSION_MAX_AGE:
        return None
    payload = f"{username}:{ts_str}"
    expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected_sig):
        return None
    if not secrets.compare_digest(username, AUTH_USERNAME):
        return None
    return username

def normalize_mcpd_url(path: Optional[str]) -> str:
    """Ensures destination paths are correctly qualified under /mcpd/."""
    if not path or path.strip() in ("", "/", "/mcpd", "/mcpd/"):
        return "/mcpd/bucket"
    clean = path.strip()
    if clean.startswith("/bucket"):
        return f"/mcpd{clean}"
    if clean.startswith("/mcpd"):
        return clean
    if clean.startswith("/"):
        return f"/mcpd{clean}"
    return f"/mcpd/{clean}"

class AuthenticationRedirect(Exception):
    def __init__(self, redirect_url: str):
        self.redirect_url = redirect_url

@app.exception_handler(AuthenticationRedirect)
async def auth_redirect_handler(request: Request, exc: AuthenticationRedirect):
    return RedirectResponse(url=exc.redirect_url, status_code=status.HTTP_303_SEE_OTHER)

async def require_auth(request: Request) -> str:
    """Dependency that ensures user is authenticated, redirecting browser requests or returning 401."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = verify_session_token(token)
    if user:
        return user

    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        user = verify_session_token(bearer_token)
        if user:
            return user

    path = request.url.path
    is_api = (
        path.startswith("/api/")
        or path.startswith("/mcpd/api/")
        or "application/json" in request.headers.get("accept", "")
    )
    if is_api:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    target = normalize_mcpd_url(path)
    if request.url.query:
        target += f"?{request.url.query}"
    raise AuthenticationRedirect(f"/mcpd/login?next={target}")

# ==============================================================================
# Service Configuration
# ==============================================================================

AGYC_MCP_URL = os.environ.get("AGYC_MCP_URL", "http://127.0.0.1:8001")
BUCKET_URL = os.environ.get("BUCKET_URL", "http://127.0.0.1:8090")
MCP_PROJECTS_ROOT = os.environ.get("MCP_PROJECTS_ROOT", "/home/azureadmin/mcp/projects")

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

def _get_git_info(project_path: str) -> Dict[str, Any]:
    """Inspects git repository status for a given project directory."""
    info = {
        "branch": "none",
        "commit": "No commits",
        "dirty": False,
        "dirty_count": 0,
        "has_git": False
    }
    if not project_path or not os.path.exists(os.path.join(project_path, ".git")):
        return info
    
    info["has_git"] = True
    try:
        b_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path, capture_output=True, text=True, timeout=2)
        if b_res.returncode == 0 and b_res.stdout.strip():
            info["branch"] = b_res.stdout.strip()
        
        c_res = subprocess.run(["git", "log", "-1", "--format=%h - %s (%cr)"], cwd=project_path, capture_output=True, text=True, timeout=2)
        if c_res.returncode == 0 and c_res.stdout.strip():
            info["commit"] = c_res.stdout.strip()
        
        s_res = subprocess.run(["git", "status", "--porcelain"], cwd=project_path, capture_output=True, text=True, timeout=2)
        if s_res.returncode == 0:
            lines = [l for l in s_res.stdout.strip().splitlines() if l.strip()]
            info["dirty_count"] = len(lines)
            info["dirty"] = len(lines) > 0
    except Exception as e:
        logger.debug("Git info fetch error for %s: %s", project_path, e)
    return info

async def fetch_agyc_status() -> Dict[str, Any]:
    """Fetch live projects, tasks, and routes directly from agyc-mcp internal server."""
    result = {
        "projects": [],
        "managed_projects": [],
        "host_services": [],
        "tasks": [],
        "routes": [],
        "completion_sessions": [],
        "usage": {},
        "online": False
    }
    try:
        sys.path.insert(0, "/home/azureadmin/agyc-mcp")
        try:
            from src.server import list_projects, list_tasks, list_routes, list_completions
            
            raw_projs = list_projects()
            raw_tasks = list_tasks()
            raw_routes = list_routes()
            try:
                raw_completions = list_completions()
            except Exception:
                raw_completions = []
            
            result["tasks"] = raw_tasks
            result["routes"] = raw_routes
            result["completion_sessions"] = raw_completions
            result["online"] = True

            all_projs = []
            managed = []
            host = []

            for p in raw_projs:
                p_copy = dict(p)
                path = p.get("path", "")
                name = p.get("name", "")

                git_info = _get_git_info(path)
                p_copy.update(git_info)

                is_managed = (
                    not p.get("auto_discovered", False)
                    or path.startswith(MCP_PROJECTS_ROOT)
                    or name in ("bucketloader", "animation-renderer", "bucket-script", "mcp-dashboard")
                )
                p_copy["is_managed"] = is_managed

                all_projs.append(p_copy)
                if is_managed:
                    managed.append(p_copy)
                else:
                    host.append(p_copy)

            managed.sort(key=lambda x: x["name"])
            host.sort(key=lambda x: x["name"])

            result["projects"] = all_projs
            result["managed_projects"] = managed
            result["host_services"] = host

            # Auto-sync any completed/failed in-memory tasks to bucket archive
            for t in raw_tasks:
                st = t.get("status")
                tid = t.get("task_id") or t.get("id")
                if tid and st in ("completed", "failed", "cancelled", "timed_out"):
                    if tid not in task_db_store._synced_terminal_task_ids:
                        asyncio.create_task(task_db_store.sync_task(t))

            return result
        except Exception as e:
            logger.warning("Direct import exception: %s", e)

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
    """Fetch stored files from Google Drive bucket, including files in subfolders."""
    api_key = get_bucket_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    url = f"{BUCKET_URL}/files"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            items = resp.json()
            all_files = []
            for item in items:
                all_files.append(item)
                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    folder_name = item.get("name")
                    try:
                        sub_resp = await client.get(f"{BUCKET_URL}/files?subfolder={folder_name}", headers=headers)
                        if sub_resp.status_code == 200:
                            sub_items = sub_resp.json()
                            for sf in sub_items:
                                sf["subfolder"] = folder_name
                                sf["display_name"] = f"{folder_name}/{sf.get('name')}"
                                all_files.append(sf)
                    except Exception as se:
                        logger.debug("Failed to list subfolder %s: %s", folder_name, se)
            return all_files
    except Exception as e:
        logger.warning("Failed to fetch bucket files: %s", e)
        return []

# ==============================================================================
# Task History & Bucket Database Storage (mcp-database/)
# ==============================================================================

class TaskDatabaseStore:
    """
    Manages persistent task archive stored in Google Drive bucket under mcp-database/.
    Enforces bandwidth conservation by limiting queries to 5 items by default and
    caching retrieved lists in memory.
    """
    CACHE_TTL = 15.0  # seconds to cache past tasks in memory to prevent bandwidth & quota drain

    def __init__(self):
        self._cached_tasks: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._lock = asyncio.Lock()
        self._synced_terminal_task_ids = set()

    async def fetch_past_tasks(self, limit: int = 5, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Retrieves the most recent past tasks from mcp-database/tasks.json in the bucket.
        Capped to `limit` (default 5) to minimize bandwidth.
        """
        now = time.time()
        if not force_refresh and self._cached_tasks and (now - self._last_fetch_time < self.CACHE_TTL):
            return self._cached_tasks[:limit]

        async with self._lock:
            if not force_refresh and self._cached_tasks and (time.time() - self._last_fetch_time < self.CACHE_TTL):
                return self._cached_tasks[:limit]

            url = f"{BUCKET_URL}/raw/mcp-database/tasks.json"
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            data.sort(
                                key=lambda t: t.get("completed_at") or t.get("created_at") or "",
                                reverse=True
                            )
                            self._cached_tasks = data
                            self._last_fetch_time = time.time()
                            for t in self._cached_tasks:
                                tid = t.get("task_id")
                                if tid:
                                    self._synced_terminal_task_ids.add(tid)
                            return self._cached_tasks[:limit]
            except Exception as e:
                logger.debug("Failed to fetch past tasks from bucket: %s", e)

            return self._cached_tasks[:limit]

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single task detail, checking memory cache first, then bucketloader."""
        for t in self._cached_tasks:
            if t.get("task_id") == task_id:
                return t

        # Try individual task file from bucket
        api_key = get_bucket_api_key()
        headers = {"X-API-Key": api_key} if api_key else {}
        url = f"{BUCKET_URL}/raw/mcp-database/{task_id}.json"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug("Error fetching individual task %s: %s", task_id, e)

        # Fallback to refreshing aggregate archive
        tasks = await self.fetch_past_tasks(limit=1000, force_refresh=True)
        for t in tasks:
            if t.get("task_id") == task_id:
                return t
        return None

    async def sync_task(self, task_dict: Dict[str, Any]) -> bool:
        """
        Persists a terminal task record into:
        1. Aggregated mcp-database/tasks.json
        2. Individual mcp-database/{task_id}.json
        """
        task_id = task_dict.get("task_id")
        if not task_id:
            return False

        api_key = get_bucket_api_key()
        headers = {"X-API-Key": api_key} if api_key else {}

        try:
            current_tasks = await self.fetch_past_tasks(limit=1000, force_refresh=True)
            tasks_list = list(current_tasks)

            idx = next((i for i, t in enumerate(tasks_list) if t.get("task_id") == task_id), None)
            if idx is not None:
                tasks_list[idx] = task_dict
            else:
                tasks_list.insert(0, task_dict)

            tasks_list.sort(
                key=lambda t: t.get("completed_at") or t.get("created_at") or "",
                reverse=True
            )

            # Upload aggregate
            agg_bytes = json.dumps(tasks_list, indent=2).encode("utf-8")
            files_agg = {"file": ("tasks.json", agg_bytes, "application/json")}
            data_agg = {"overwrite": "true", "subfolder": "mcp-database"}

            async with httpx.AsyncClient(timeout=15.0) as client:
                r1 = await client.post(f"{BUCKET_URL}/upload", files=files_agg, data=data_agg, headers=headers)

                # Upload individual task record
                ind_bytes = json.dumps(task_dict, indent=2).encode("utf-8")
                files_ind = {"file": (f"{task_id}.json", ind_bytes, "application/json")}
                data_ind = {"overwrite": "true", "subfolder": "mcp-database"}
                r2 = await client.post(f"{BUCKET_URL}/upload", files=files_ind, data=data_ind, headers=headers)

                if r1.status_code == 200:
                    self._cached_tasks = tasks_list
                    self._last_fetch_time = time.time()
                    self._synced_terminal_task_ids.add(task_id)
                    logger.info("Synced task %s to bucket mcp-database/", task_id)
                    return True
                else:
                    logger.warning("Bucket upload returned status %d: %s", r1.status_code, r1.text)
                    return False
        except Exception as e:
            logger.error("Exception syncing task %s to bucket: %s", task_id, e)
            return False

task_db_store = TaskDatabaseStore()

# ==============================================================================
# Authentication Routes (Login & Logout)
# ==============================================================================

@app.get("/login", response_class=HTMLResponse)
@app.get("/mcpd/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = Query(None)):
    """Renders authentication page."""
    next_dest = normalize_mcpd_url(next) if next else "/mcpd/bucket"
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if verify_session_token(token):
        return RedirectResponse(url=next_dest, status_code=status.HTTP_303_SEE_OTHER)

    ip = security_limiter.get_client_ip(request)
    is_locked, remaining = security_limiter.check_lockout(ip)
    error_msg = None
    if is_locked:
        mins = remaining // 60
        secs = remaining % 60
        error_msg = f"SECURITY LOCKOUT: Too many failed login attempts from {ip}. Please retry in {mins}m {secs}s."

    template = jinja_env.get_template("login.html")
    content = template.render(
        request=request,
        error=error_msg,
        next_url=next_dest,
        action_url="/mcpd/login"
    )
    return HTMLResponse(content=content)

@app.post("/login")
@app.post("/mcpd/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = Form(None),
):
    """Processes login credentials with anti-bruteforce progressive delay and IP lockout."""
    next_dest = normalize_mcpd_url(next) if next else "/mcpd/bucket"
    ip = security_limiter.get_client_ip(request)
    is_locked, remaining = security_limiter.check_lockout(ip)
    if is_locked:
        mins = remaining // 60
        secs = remaining % 60
        template = jinja_env.get_template("login.html")
        content = template.render(
            request=request,
            error=f"SECURITY LOCKOUT: IP {ip} temporarily locked. Retry in {mins}m {secs}s.",
            next_url=next_dest,
            action_url="/mcpd/login"
        )
        return HTMLResponse(content=content, status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    # Constant-time credential verification
    user_ok = secrets.compare_digest(username.strip(), AUTH_USERNAME)
    pass_ok = secrets.compare_digest(password.strip(), AUTH_PASSWORD)

    if not (user_ok and pass_ok):
        count, remaining_attempts, delay = await security_limiter.register_failure(ip)
        if remaining_attempts <= 0:
            error_msg = f"ACCESS DENIED: Maximum login attempts exceeded. IP {ip} locked for 15 minutes."
            resp_code = status.HTTP_429_TOO_MANY_REQUESTS
        else:
            error_msg = f"ACCESS DENIED: Invalid identity or passkey. {remaining_attempts} attempt(s) remaining before IP lockout."
            resp_code = status.HTTP_401_UNAUTHORIZED

        template = jinja_env.get_template("login.html")
        content = template.render(
            request=request,
            error=error_msg,
            next_url=next_dest,
            action_url="/mcpd/login"
        )
        return HTMLResponse(content=content, status_code=resp_code)

    # Success: reset lockout counters
    security_limiter.register_success(ip)
    token = create_session_token(AUTH_USERNAME)

    response = RedirectResponse(url=next_dest, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/"
    )
    return response

@app.get("/logout")
@app.get("/mcpd/logout")
async def logout_action():
    """Clears authentication session."""
    response = RedirectResponse(url="/mcpd/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response

# ==============================================================================
# HTML Pages: Dashboard & Bucket Manager (Dual Routing for / and /mcpd)
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
@app.get("/mcpd", response_class=HTMLResponse)
@app.get("/mcpd/", response_class=HTMLResponse)
async def dashboard_page(request: Request, user: str = Depends(require_auth)):
    """Main AGYC Control Center dashboard (requires authentication)."""
    status_data = await fetch_agyc_status()
    bucket_data = await fetch_bucket_health()
    past_tasks = await task_db_store.fetch_past_tasks(limit=5)
    template = jinja_env.get_template("dashboard.html")
    html_content = template.render(
        status=status_data,
        bucket=bucket_data,
        past_tasks=past_tasks,
        request=request,
        user=user
    )
    return HTMLResponse(content=html_content)

@app.get("/bucket", response_class=HTMLResponse)
@app.get("/bucket/", response_class=HTMLResponse)
@app.get("/mcpd/bucket", response_class=HTMLResponse)
@app.get("/mcpd/bucket/", response_class=HTMLResponse)
async def bucket_manager_page(request: Request, user: str = Depends(require_auth)):
    """Interactive Bucketloader File Manager page (requires authentication)."""
    bucket_data = await fetch_bucket_health()
    files = await fetch_bucket_files()
    template = jinja_env.get_template("bucket.html")
    html_content = template.render(
        bucket=bucket_data,
        files=files,
        request=request,
        user=user
    )
    return HTMLResponse(content=html_content)

# ==============================================================================
# JSON API Endpoints (Dual Routing for /api/... and /mcpd/api/...)
# ==============================================================================

@app.get("/api/status")
@app.get("/mcpd/api/status")
async def api_status(user: str = Depends(require_auth)):
    """Live JSON status for AJAX polling."""
    status_data = await fetch_agyc_status()
    return status_data

@app.post("/api/tasks/dispatch")
@app.post("/mcpd/api/tasks/dispatch")
async def api_dispatch_task(
    project_name: str = Form(...),
    prompt: str = Form(...),
    model: Optional[str] = Form(None),
    effort: Optional[str] = Form(None),
    user: str = Depends(require_auth),
):
    """Dispatches a new task into the background execution queue."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import queue_task
    try:
        res = queue_task(
            project_name=project_name,
            task=prompt,
            model=model.strip() if model and model.strip() else None,
            effort=effort.strip() if effort and effort.strip() else None,
        )
        if isinstance(res, dict):
            if "error" in res and not res.get("task_id"):
                return {"success": False, "error": res["error"]}
            res_dict = dict(res)
            res_dict["success"] = True
            return res_dict
        return {"success": True, "result": res}
    except Exception as e:
        logger.error("Task dispatch error: %s", e)
        return {"success": False, "error": str(e)}

@app.post("/api/tasks/{task_id}/input")
@app.post("/mcpd/api/tasks/{task_id}/input")
async def api_send_task_input(
    task_id: str,
    user_input: str = Form(...),
    user: str = Depends(require_auth),
):
    """Sends interactive reply to a waiting subagent."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import send_input_to_task
    try:
        res = send_input_to_task(task_id=task_id, input_text=user_input)
        if isinstance(res, dict):
            res_dict = dict(res)
            res_dict["success"] = "error" not in res_dict
            return res_dict
        return {"success": True, "result": res}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/tasks/{task_id}/cancel")
@app.post("/mcpd/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str, user: str = Depends(require_auth)):
    """Cancels a running or queued task."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import cancel_task
    try:
        res = cancel_task(task_id=task_id)
        if isinstance(res, dict):
            res_dict = dict(res)
            res_dict["success"] = res_dict.get("cancelled", True)
            return res_dict
        return {"success": True, "result": res}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/tasks/past")
@app.get("/mcpd/api/tasks/past")
async def api_past_tasks(
    limit: int = Query(5, ge=1, le=50),
    force: bool = Query(False),
    user: str = Depends(require_auth),
):
    """Returns the most recent past tasks from Google Drive bucket (mcp-database/tasks.json). Limit defaults to 5."""
    tasks = await task_db_store.fetch_past_tasks(limit=limit, force_refresh=force)
    return {"tasks": tasks, "count": len(tasks), "limit": limit}

@app.get("/api/tasks/past/{task_id}")
@app.get("/mcpd/api/tasks/past/{task_id}")
async def api_past_task_detail(task_id: str, user: str = Depends(require_auth)):
    """Returns the detailed record of a single past task from Google Drive mcp-database."""
    task = await task_db_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in archive")
    return task

@app.get("/api/tasks/{task_id}")
@app.get("/mcpd/api/tasks/{task_id}")
async def api_get_task_details(task_id: str, user: str = Depends(require_auth)):
    """Retrieves detailed logs and state for a specific task."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import get_task_status
    try:
        res = get_task_status(task_id=task_id)
        if isinstance(res, dict) and not res.get("error"):
            return res
    except Exception:
        pass

    archived = await task_db_store.get_task(task_id)
    if archived:
        return archived
    return {"error": f"Task {task_id} not found"}

# ==============================================================================
# Autonomous Completion Loop Endpoints (4-Hour Self-Healing Development)
# ==============================================================================

@app.post("/api/complete")
@app.post("/mcpd/api/complete")
async def api_start_completion(
    project_name: str = Form(...),
    goal: str = Form(...),
    duration_hours: float = Form(4.0),
    model: Optional[str] = Form(None),
    effort: Optional[str] = Form(None),
    user: str = Depends(require_auth),
):
    """Starts an autonomous 4-hour completion loop with automated testing and TODO.md roadmap."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import complete
    try:
        res = complete(
            project_name=project_name,
            goal=goal,
            duration_hours=duration_hours,
            model=model.strip() if model and model.strip() else None,
            effort=effort.strip() if effort and effort.strip() else None,
        )
        if isinstance(res, dict):
            if "error" in res and not res.get("session_id"):
                return {"success": False, "error": res["error"]}
            res_dict = dict(res)
            res_dict["success"] = True
            return res_dict
        return {"success": True, "result": res}
    except Exception as e:
        logger.error("Complete dispatch error: %s", e)
        return {"success": False, "error": str(e)}

@app.get("/api/complete")
@app.get("/mcpd/api/complete")
async def api_list_completions(
    project_name: Optional[str] = Query(None),
    user: str = Depends(require_auth),
):
    """Lists all active and past completion loops."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import list_completions
    try:
        sessions = list_completions(project_name=project_name)
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        logger.error("List completions error: %s", e)
        return {"sessions": [], "error": str(e)}

@app.get("/api/complete/{session_id}")
@app.get("/mcpd/api/complete/{session_id}")
async def api_get_completion(session_id: str, user: str = Depends(require_auth)):
    """Retrieves real-time status of a completion session."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import get_completion_status
    try:
        res = get_completion_status(session_id=session_id)
        if "error" in res:
            raise HTTPException(status_code=404, detail=res["error"])
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get completion status error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/complete/{session_id}/cancel")
@app.post("/mcpd/api/complete/{session_id}/cancel")
async def api_cancel_completion(session_id: str, user: str = Depends(require_auth)):
    """Cancels an active 4-hour completion loop."""
    sys.path.insert(0, "/home/azureadmin/agyc-mcp")
    from src.server import cancel_completion
    try:
        res = cancel_completion(session_id=session_id)
        return res
    except Exception as e:
        logger.error("Cancel completion error: %s", e)
        return {"success": False, "error": str(e)}

@app.post("/api/projects/commit")
@app.post("/mcpd/api/projects/commit")
async def api_project_commit(
    project_name: str = Form(...),
    message: Optional[str] = Form(None),
    user: str = Depends(require_auth),
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
    user: str = Depends(require_auth),
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
# Bucketloader Management & Authenticated Download APIs
# ==============================================================================

@app.get("/api/bucket/files")
@app.get("/mcpd/api/bucket/files")
async def api_bucket_files(user: str = Depends(require_auth)):
    """Lists files in the Google Drive bucket (requires authentication)."""
    files = await fetch_bucket_files()
    return files

@app.get("/api/bucket/files/{file_id}/download")
@app.get("/mcpd/api/bucket/files/{file_id}/download")
@app.get("/bucket/download/{file_id}")
@app.get("/mcpd/bucket/download/{file_id}")
async def api_bucket_file_download(
    file_id: str,
    request: Request,
    filename: Optional[str] = Query(None),
    user: str = Depends(require_auth),
):
    """
    Secure authenticated file download endpoint.
    Streams binary/text file directly with Content-Disposition: attachment header.
    """
    api_key = get_bucket_api_key()
    url = f"{BUCKET_URL}/files/{file_id}?download=1"

    req_headers = {}
    if api_key:
        req_headers["X-API-Key"] = api_key
    range_header = request.headers.get("range")
    if range_header:
        req_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=180.0)
    try:
        req = client.build_request("GET", url, headers=req_headers)
        resp = await client.send(req, stream=True)

        if resp.status_code not in (200, 206):
            body = await resp.aread()
            await resp.aclose()
            await client.aclose()
            return JSONResponse({"error": "Failed to stream file from bucket", "status_code": resp.status_code, "detail": body.decode("utf-8", errors="ignore")}, status_code=resp.status_code)

        async def stream_generator():
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        response_headers = {}
        for h in ("content-type", "content-length", "accept-ranges", "content-range"):
            val = resp.headers.get(h)
            if val:
                response_headers[h] = val

        disposition = resp.headers.get("content-disposition")
        if disposition and "attachment" in disposition.lower():
            response_headers["content-disposition"] = disposition
        elif filename:
            clean_name = os.path.basename(filename).replace('"', '\\"')
            response_headers["content-disposition"] = f'attachment; filename="{clean_name}"'
        else:
            response_headers["content-disposition"] = f'attachment; filename="bucket_file_{file_id}"'

        return StreamingResponse(
            stream_generator(),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
            headers=response_headers,
        )
    except Exception as e:
        await client.aclose()
        logger.error("Download streaming error for file %s: %s", file_id, e)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/bucket/raw/{filepath:path}/download")
@app.get("/mcpd/api/bucket/raw/{filepath:path}/download")
@app.get("/bucket/raw/{filepath:path}/download")
@app.get("/mcpd/bucket/raw/{filepath:path}/download")
async def api_bucket_raw_file_download(
    filepath: str,
    request: Request,
    user: str = Depends(require_auth),
):
    """
    Secure authenticated file download endpoint by relative filepath.
    Streams file directly with Content-Disposition: attachment header.
    """
    api_key = get_bucket_api_key()
    clean_path = filepath.strip("/")
    url = f"{BUCKET_URL}/raw/{clean_path}?download=1"

    req_headers = {}
    if api_key:
        req_headers["X-API-Key"] = api_key
    range_header = request.headers.get("range")
    if range_header:
        req_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=180.0)
    try:
        req = client.build_request("GET", url, headers=req_headers)
        resp = await client.send(req, stream=True)

        if resp.status_code not in (200, 206):
            body = await resp.aread()
            await resp.aclose()
            await client.aclose()
            return JSONResponse({"error": f"Failed to retrieve file '{clean_path}'", "status_code": resp.status_code, "detail": body.decode("utf-8", errors="ignore")}, status_code=resp.status_code)

        async def stream_generator():
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        response_headers = {}
        for h in ("content-type", "content-length", "accept-ranges", "content-range"):
            val = resp.headers.get(h)
            if val:
                response_headers[h] = val

        base_name = os.path.basename(clean_path).replace('"', '\\"')
        response_headers["content-disposition"] = f'attachment; filename="{base_name}"'

        return StreamingResponse(
            stream_generator(),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
            headers=response_headers,
        )
    except Exception as e:
        await client.aclose()
        logger.error("Raw download streaming error for %s: %s", filepath, e)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/bucket/upload")
@app.post("/mcpd/api/bucket/upload")
async def api_bucket_upload(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    user: str = Depends(require_auth),
):
    """Uploads a file directly into the Google Drive bucket (requires authentication)."""
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
    user: str = Depends(require_auth),
):
    """Creates or overwrites a text/markdown/code file in the Google Drive bucket (requires authentication)."""
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
async def api_bucket_file_content(file_id: str, user: str = Depends(require_auth)):
    """Retrieves file content for preview or text editor (requires authentication)."""
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
async def api_bucket_delete_file(file_id: str, user: str = Depends(require_auth)):
    """Deletes/trashes a file from the Google Drive bucket (requires authentication)."""
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

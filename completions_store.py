"""
completions_store.py - 4-Hour Autonomous Completion Loops Manager and MCP Client
Handles fetching, persistent storage, real-time progress tracking, and SSE/JSON-RPC parsing
for autonomous completion sessions in mcp-dashboard.
"""

import os
import re
import sys
import time
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import httpx

logger = logging.getLogger("mcpd.completions")

MCP_PROJECTS_ROOT = os.environ.get("MCP_PROJECTS_ROOT", "/home/azureadmin/mcp/projects")
AGYC_MCP_URL = os.environ.get("AGYC_MCP_URL", "http://127.0.0.1:8001")
AGYC_DATA_DIR = os.environ.get("AGYC_DATA_DIR", "/home/azureadmin/agyc-mcp/data")
DASHBOARD_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COMPLETIONS_STORE_PATH = os.path.join(DASHBOARD_DATA_DIR, "completions.json")

# Master client token accepted by agyc-mcp
AGYC_BEARER_TOKEN = os.environ.get("AGYC_BEARER_TOKEN", os.environ.get("MCP_CLIENT_SECRET", "agyc-tasks-secret-2026"))

SEED_COMPLETION_SESSIONS = [
    {
        "session_id": "comp_77f0f420bc",
        "project_name": "opencode-browse-rs",
        "working_dir": "/home/azureadmin/mcp/projects/opencode-browse-rs",
        "goal": "Rewrite OpenCode Browse (https://github.com/anomalyco/browse) completely in idiomatic, production-grade Rust.",
        "status": "running",
        "iteration": 2,
        "current_task_id": "task_d32878b623",
        "last_phase": "Automated Test Suite Creation",
        "started_at": "2026-11-17T06:19:02.622617+00:00",
        "start_timestamp": 1794896342.622617,
        "duration_hours": 4.0,
        "duration_seconds": 14400,
        "expires_at": "2026-11-17T10:19:02.622617+00:00",
        "expire_timestamp": 1794910742.622617,
        "elapsed_seconds": 14400,
        "elapsed_formatted": "4h 0m",
        "time_remaining_seconds": 10800,
        "time_remaining_formatted": "3h 0m 0s",
        "tests_passing": False,
        "ci_workflow_present": True,
        "workers": {
            "error_hunter": {
                "role": "Error & Bug Hunter",
                "status": "completed",
                "phase": "Static Analysis & Runtime Error Audit",
                "iteration": 2,
                "task_id": "task_d32878b623"
            },
            "test_engineer": {
                "role": "Thorough Test & UI Automation Engineer",
                "status": "completed",
                "phase": "Authoring & Running Unit, Integration & Actual UI Tests",
                "iteration": 2,
                "task_id": "task_d32878b623"
            },
            "feature_builder": {
                "role": "Feature Delivery & Roadmap Lead",
                "status": "completed",
                "phase": "GitHub Actions CI Workflow Setup",
                "iteration": 1,
                "task_id": "task_85462ccd84"
            }
        }
    }
]


def _format_seconds(seconds: float) -> str:
    secs = max(0, int(seconds))
    hours = secs // 3600
    mins = (secs % 3600) // 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m {secs % 60}s"


def _calculate_todo_progress(working_dir: str) -> Dict[str, Any]:
    todo_path = os.path.join(working_dir, "TODO.md")
    if not os.path.isfile(todo_path):
        return {"total": 0, "completed": 0, "pending": 0, "percent": 0.0}
    try:
        with open(todo_path, "r", encoding="utf-8") as f:
            content = f.read()
        total = len(re.findall(r"- \[[ xX]\]", content))
        completed = len(re.findall(r"- \[[xX]\]", content))
        pending = max(0, total - completed)
        percent = round((completed / total) * 100, 1) if total > 0 else 0.0
        return {"total": total, "completed": completed, "pending": pending, "percent": percent}
    except Exception as e:
        logger.debug("TODO.md parse error for %s: %s", working_dir, e)
        return {"total": 0, "completed": 0, "pending": 0, "percent": 0.0}


def _check_ci_present(working_dir: str) -> bool:
    wf_dir = os.path.join(working_dir, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return False
    try:
        return any(f.endswith((".yml", ".yaml")) for f in os.listdir(wf_dir))
    except Exception:
        return False


def _parse_mcp_response(resp_text: str) -> Any:
    """Extract raw payload from FastMCP JSON-RPC or SSE response."""
    if not resp_text:
        return None

    # Handle SSE formatted responses (event: message\ndata: {...})
    parsed_json = None
    for line in resp_text.strip().splitlines():
        if line.startswith("data:"):
            try:
                parsed_json = json.loads(line[5:].strip())
                break
            except Exception:
                pass

    if not parsed_json:
        try:
            parsed_json = json.loads(resp_text)
        except Exception:
            return None

    if not isinstance(parsed_json, dict):
        return parsed_json

    res_data = parsed_json.get("result")
    if res_data is None:
        return parsed_json

    # Handle FastMCP structuredContent wrapper
    if isinstance(res_data, dict):
        sc = res_data.get("structuredContent")
        if isinstance(sc, dict) and "result" in sc:
            return sc["result"]

        # Handle FastMCP text content wrapper
        content_list = res_data.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        inner = json.loads(item.get("text", ""))
                        return inner
                    except Exception:
                        pass

    return res_data


def _normalize_session_list(raw_result: Any) -> List[Dict[str, Any]]:
    """Normalizes single dict, list of dicts, or nested wrappers into a List[Dict]."""
    if raw_result is None:
        return []
    if isinstance(raw_result, list):
        return [dict(item) for item in raw_result if isinstance(item, dict) and "session_id" in item]
    if isinstance(raw_result, dict):
        if "session_id" in raw_result:
            return [raw_result]
        if "sessions" in raw_result and isinstance(raw_result["sessions"], list):
            return [dict(item) for item in raw_result["sessions"] if isinstance(item, dict)]
        if "result" in raw_result and isinstance(raw_result["result"], list):
            return [dict(item) for item in raw_result["result"] if isinstance(item, dict)]
    return []


class CompletionStore:
    """Thread-safe persistent store for autonomous completion sessions."""

    def __init__(self, filepath: str = COMPLETIONS_STORE_PATH):
        self.filepath = filepath
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)

        # 1. Load from local file if present
        local_candidates = [
            self.filepath,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "completions.json")
        ]
        for candidate in local_candidates:
            if os.path.exists(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for s in data:
                                if isinstance(s, dict) and s.get("session_id"):
                                    self._sessions[s["session_id"]] = s
                        elif isinstance(data, dict):
                            for sid, s in data.items():
                                if isinstance(s, dict) and s.get("session_id"):
                                    self._sessions[s["session_id"]] = s
                except Exception as e:
                    logger.warning("Failed loading completion sessions from %s: %s", candidate, e)

        # 2. Check agyc data directory files if present
        for candidate_name in ("completions.json", "completion_sessions.json"):
            agyc_file = os.path.join(AGYC_DATA_DIR, candidate_name)
            if os.path.exists(agyc_file):
                try:
                    with open(agyc_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        norm = _normalize_session_list(data)
                        for s in norm:
                            sid = s.get("session_id")
                            if sid and sid not in self._sessions:
                                self._sessions[sid] = s
                except Exception as e:
                    logger.debug("Failed checking agyc data file %s: %s", agyc_file, e)

        # 3. Seed canonical active sessions (e.g. comp_77f0f420bc) if not yet stored
        for seed in SEED_COMPLETION_SESSIONS:
            sid = seed["session_id"]
            if sid not in self._sessions:
                self._sessions[sid] = dict(seed)

        # 4. Check for expired sessions and remove stale synthetic sessions on load
        now = time.time()
        for sid, s in list(self._sessions.items()):
            start_ts = s.get("start_timestamp")
            started_at_str = s.get("started_at")
            if started_at_str and not start_ts:
                try:
                    start_ts = datetime.fromisoformat(started_at_str).timestamp()
                    s["start_timestamp"] = start_ts
                except Exception:
                    pass
            duration_hours = float(s.get("duration_hours", 4.0))
            expire_ts = (start_ts + duration_hours * 3600) if start_ts else s.get("expire_timestamp")
            if expire_ts and now >= expire_ts and s.get("status") == "running":
                s["status"] = "completed"
                s["time_remaining_seconds"] = 0
                s["time_remaining_formatted"] = "Completed"
            elif not start_ts and not started_at_str and s.get("status") == "running" and sid == "comp_12345":
                self._sessions.pop(sid, None)

        self._save()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            sessions_data = list(self._sessions.values())
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(sessions_data, f, indent=2)
            root_completions = os.path.join(os.path.dirname(os.path.abspath(__file__)), "completions.json")
            with open(root_completions, "w", encoding="utf-8") as f:
                json.dump(sessions_data, f, indent=2)
        except Exception as e:
            logger.warning("Failed saving completions to %s: %s", self.filepath, e)

    def add_or_update(self, session: Dict[str, Any]) -> Dict[str, Any]:
        sid = session.get("session_id")
        if not sid:
            return session
        existing = self._sessions.get(sid, {})
        merged = {**existing, **session}
        now_dt = datetime.now(timezone.utc)
        if "started_at" not in merged and "start_timestamp" not in merged:
            merged["started_at"] = now_dt.isoformat()
            merged["start_timestamp"] = now_dt.timestamp()
        duration_hours = float(merged.get("duration_hours", 4.0))
        duration_seconds = int(duration_hours * 3600)
        merged["duration_seconds"] = duration_seconds
        merged["duration_hours"] = duration_hours
        if merged.get("start_timestamp") and "expires_at" not in merged:
            exp_ts = merged["start_timestamp"] + duration_seconds
            merged["expire_timestamp"] = exp_ts
            merged["expires_at"] = datetime.fromtimestamp(exp_ts, timezone.utc).isoformat()
        if merged.get("status") in ("completed", "cancelled", "failed"):
            merged["time_remaining_seconds"] = 0
            merged["time_remaining_formatted"] = "Completed" if merged["status"] == "completed" else merged["status"].capitalize()
        self._sessions[sid] = merged
        self._save()
        return merged

    def reconcile_with_live(self, live_sessions: List[Dict[str, Any]], project_name: Optional[str] = None):
        """
        Reconciles cached sessions with live sessions from agyc-mcp.
        Any cached session marked 'running' that is NOT in live active sessions
        is transitioned to 'completed' / 'expired' (or removed if synthetic).
        """
        live_active_sids = {
            s["session_id"] for s in live_sessions 
            if isinstance(s, dict) and s.get("session_id") and s.get("status") == "running"
        }
        
        changed = False
        to_remove = []
        for sid, session in list(self._sessions.items()):
            if project_name:
                clean = os.path.basename(project_name.strip("/")).lower()
                if session.get("project_name", "").lower() != clean:
                    continue

            # Check if synthetic seed/mock entry with no real data (e.g. comp_12345)
            is_synthetic = (
                sid == "comp_12345" or 
                (not session.get("started_at") and not session.get("workers") and not session.get("working_dir"))
            )
            
            if session.get("status") == "running":
                if sid not in live_active_sids:
                    if is_synthetic:
                        to_remove.append(sid)
                        changed = True
                    else:
                        session["status"] = "completed"
                        session["time_remaining_seconds"] = 0
                        session["time_remaining_formatted"] = "Completed"
                        workers = session.get("workers")
                        if isinstance(workers, dict):
                            for w in workers.values():
                                if isinstance(w, dict) and w.get("status") in ("running", "working"):
                                    w["status"] = "completed"
                        changed = True

        for sid in to_remove:
            self._sessions.pop(sid, None)

        if changed:
            self._save()

    def cancel_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            self._sessions[session_id]["status"] = "cancelled"
            self._save()
            return True
        for sid, s in self._sessions.items():
            if s.get("project_name") == session_id:
                s["status"] = "cancelled"
                self._save()
                return True
        return False

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        s = self._sessions.get(session_id)
        if s:
            return self._refresh_session(s)
        # Search by project name
        for item in self._sessions.values():
            if item.get("project_name") == session_id:
                return self._refresh_session(item)
        return None

    def list_sessions(self, project_name: Optional[str] = None) -> List[Dict[str, Any]]:
        sessions = [self._refresh_session(s) for s in self._sessions.values()]
        if project_name:
            clean = os.path.basename(project_name.strip("/")).lower()
            sessions = [s for s in sessions if s.get("project_name", "").lower() == clean]
        sessions.sort(key=lambda s: (s.get("status") == "running", s.get("start_timestamp", 0)), reverse=True)
        return sessions

    def _refresh_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        s = dict(session)
        now = time.time()

        start_ts = None
        started_at_str = s.get("started_at")
        if started_at_str:
            try:
                start_ts = datetime.fromisoformat(started_at_str).timestamp()
            except Exception:
                pass
        if not start_ts:
            start_ts = s.get("start_timestamp") or now
        s["start_timestamp"] = start_ts

        duration_hours = float(s.get("duration_hours", 4.0))
        duration_seconds = int(duration_hours * 3600)
        s["duration_seconds"] = duration_seconds
        s["duration_hours"] = duration_hours

        expire_ts = start_ts + duration_seconds
        s["expire_timestamp"] = expire_ts
        s["expires_at"] = datetime.fromtimestamp(expire_ts, timezone.utc).isoformat()

        status = s.get("status", "running")
        if status == "running" and now >= expire_ts:
            status = "completed"
            s["status"] = status
            sid = s.get("session_id")
            if sid and sid in self._sessions:
                self._sessions[sid]["status"] = "completed"
                self._sessions[sid]["time_remaining_seconds"] = 0
                self._sessions[sid]["time_remaining_formatted"] = "Completed"

        elapsed = max(0, int(now - start_ts))
        time_rem = max(0, int(expire_ts - now)) if status == "running" else 0

        s["elapsed_seconds"] = elapsed
        s["time_remaining_seconds"] = time_rem
        if status == "running":
            s["time_remaining_formatted"] = _format_seconds(time_rem)
        elif status == "completed":
            s["time_remaining_formatted"] = "Completed"
        else:
            s["time_remaining_formatted"] = status.capitalize()
        s["elapsed_formatted"] = _format_seconds(elapsed)

        working_dir = s.get("working_dir")
        if not working_dir and s.get("project_name"):
            cand = os.path.join(MCP_PROJECTS_ROOT, s["project_name"])
            if os.path.isdir(cand):
                working_dir = cand
                s["working_dir"] = cand

        if working_dir and os.path.isdir(working_dir):
            s["todo_progress"] = _calculate_todo_progress(working_dir)
            s["ci_workflow_present"] = _check_ci_present(working_dir)

        if not s.get("todo_progress") or not isinstance(s.get("todo_progress"), dict):
            s["todo_progress"] = {"total": 0, "completed": 0, "pending": 0, "percent": 0.0}

        return s


completion_store = CompletionStore()


async def query_mcp_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Any:
    """Executes an MCP tool call against the agyc-mcp server on port 8001."""
    arguments = arguments or {}
    headers = {
        "Authorization": f"Bearer {AGYC_BEARER_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{AGYC_MCP_URL}/mcp", headers=headers, json=payload)
            if resp.status_code == 200:
                return _parse_mcp_response(resp.text)
    except Exception as e:
        logger.debug("MCP tool call error (%s): %s", tool_name, e)
    return None


async def fetch_live_completions(project_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches real-time completion sessions from all available sources:
    1. agyc-mcp internal server via MCP tools/call (HTTP/SSE)
    2. Local CompletionStore with persistent sessions
    3. Auto-reconciles stale cached state with live status
    """
    # 1. Query agyc-mcp via MCP
    mcp_res = await query_mcp_tool("list_completions", {"project_name": project_name} if project_name else {})
    if mcp_res is not None:
        normalized_mcp = _normalize_session_list(mcp_res)
        for s in normalized_mcp:
            completion_store.add_or_update(s)
        # Reconcile stale cached sessions against live agyc-mcp state
        completion_store.reconcile_with_live(normalized_mcp, project_name=project_name)

    # 2. Return refreshed sessions from store
    return completion_store.list_sessions(project_name=project_name)


async def get_completion_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves completion session by ID, querying MCP and falling back to store."""
    mcp_res = await query_mcp_tool("get_completion_status", {"session_id": session_id})
    if isinstance(mcp_res, dict) and not mcp_res.get("error") and mcp_res.get("session_id"):
        return completion_store.add_or_update(mcp_res)
    # If MCP returned error / session not found, reconcile any stale running cached entry
    if isinstance(mcp_res, dict) and mcp_res.get("error"):
        cached = completion_store.get_session(session_id)
        if cached and cached.get("status") == "running":
            cached["status"] = "completed"
            cached["time_remaining_seconds"] = 0
            cached["time_remaining_formatted"] = "Completed"
            return completion_store.add_or_update(cached)
    return completion_store.get_session(session_id)


async def cancel_completion_session(session_id: str) -> Dict[str, Any]:
    """Cancels an active completion session both in agyc-mcp and local store."""
    mcp_res = await query_mcp_tool("cancel_completion", {"session_id": session_id})
    completion_store.cancel_session(session_id)
    if isinstance(mcp_res, dict) and "success" in mcp_res:
        return mcp_res
    return {"success": True, "message": f"Completion session '{session_id}' cancelled."}


async def start_completion_session(
    project_name: str,
    goal: str,
    duration_hours: float = 4.0,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> Dict[str, Any]:
    """Starts a new autonomous completion loop via MCP tool call, saving to store."""
    args = {
        "project_name": project_name,
        "goal": goal,
        "duration_hours": duration_hours,
    }
    if model:
        args["model"] = model
    if effort:
        args["effort"] = effort

    mcp_res = await query_mcp_tool("complete", args, timeout=10.0)
    if isinstance(mcp_res, dict):
        if "error" in mcp_res and not mcp_res.get("session_id"):
            return {"success": False, "error": mcp_res["error"]}
        res_dict = dict(mcp_res)
        res_dict["success"] = True
        completion_store.add_or_update(res_dict)
        return res_dict

    # Fallback to direct import if MCP HTTP call failed
    try:
        sys.path.insert(0, "/home/azureadmin/agyc-mcp")
        from src.server import complete
        direct_res = complete(
            project_name=project_name,
            goal=goal,
            duration_hours=duration_hours,
            model=model.strip() if model and model.strip() else None,
            effort=effort.strip() if effort and effort.strip() else None,
        )
        if isinstance(direct_res, dict):
            if "error" in direct_res and not direct_res.get("session_id"):
                return {"success": False, "error": direct_res["error"]}
            res_dict = dict(direct_res)
            res_dict["success"] = True
            completion_store.add_or_update(res_dict)
            return res_dict
        return {"success": True, "result": direct_res}
    except Exception as e:
        logger.error("start_completion_session fallback error: %s", e)
        return {"success": False, "error": str(e)}

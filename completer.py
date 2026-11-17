"""
Autonomous Completion Engine for agyc-mcp.

Implements the 4-hour self-healing completion loop:
- Launches agyc with project goal
- Periodically checks execution status every 5 minutes
- Uses TODO.md for task tracking and progress measurement
- Writes tests, fixes bugs, adds missing features, and writes GitHub Actions CI workflows
- Enforces regular git commits
- Runs for up to 4 hours to deliver a 100% complete, error-free application
"""

import os
import sys
import time
import json
import logging
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("agyc.completer")

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

INITIAL_TODO_TEMPLATE = """# Project Completion Roadmap: {project_name}

## Objective
{goal}

## 1. Core Architecture & Components
- [ ] Initialize project structure, configuration, and dependencies
- [ ] Implement core domain models, routes, or business logic
- [ ] Implement primary user/client interfaces (CLI, API, or Web)

## 2. Automated Test Suite
- [ ] Comprehensive unit tests for core modules
- [ ] Integration tests covering full request/data lifecycles
- [ ] Edge cases, boundary values, and error-handling tests
- [ ] Verify test suite passes with 100% success rate (zero errors)

## 3. GitHub Actions CI/CD Workflows
- [ ] Create `.github/workflows/ci.yml` workflow file
- [ ] Configure automated test run, linting, and build steps
- [ ] Verify workflow YAML syntax and step validity

## 4. Edge Cases, Bug Audits & Polish
- [ ] Audit for runtime errors, unhandled exceptions, and edge cases
- [ ] Security hardening, input sanitization, and graceful degradation
- [ ] Comprehensive README.md with architecture, setup, and usage examples
- [ ] Clean git commit history and push to remote repository
"""

class CompletionSession:
    """Represents an autonomous completion session running for up to 4 hours."""

    def __init__(
        self,
        session_id: str,
        project_name: str,
        working_dir: str,
        goal: str,
        duration_hours: float = 4.0,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        check_interval_seconds: int = 300,  # 5 minutes
    ):
        self.session_id = session_id
        self.project_name = project_name
        self.working_dir = working_dir
        self.goal = goal
        self.duration_seconds = max(60, int(duration_hours * 3600))
        self.model = model
        self.effort = effort
        self.check_interval_seconds = max(10, check_interval_seconds)

        self.start_timestamp = time.time()
        self.expire_timestamp = self.start_timestamp + self.duration_seconds
        self.started_at = _utc_now_iso()
        self.expires_at = datetime.fromtimestamp(self.expire_timestamp, timezone.utc).isoformat()

        self.status = "running"  # running, completed, cancelled, expired, error
        self.iteration = 0
        self.current_task_id: Optional[str] = None
        self.last_phase = "Initializing"
        self.last_check_at: Optional[str] = None
        self.last_test_output: Optional[str] = None
        self.tests_passing: Optional[bool] = None
        self.ci_workflow_present: bool = False
        self.todo_progress: Dict[str, Any] = {"total": 0, "completed": 0, "pending": 0, "percent": 0}
        self.history: List[Dict[str, Any]] = []
        self.error: Optional[str] = None

        # Simultaneous Workers & Actual UI Test Tracking
        self.workers: Dict[str, Dict[str, Any]] = {
            "error_hunter": {
                "role": "Error & Bug Hunter",
                "task_id": None,
                "status": "idle",
                "phase": "Static Analysis & Runtime Error Audit",
                "iteration": 0,
                "last_dispatched_at": None,
            },
            "test_engineer": {
                "role": "Thorough Test & UI Automation Engineer",
                "task_id": None,
                "status": "idle",
                "phase": "Authoring & Running Unit, Integration & Actual UI Tests",
                "iteration": 0,
                "last_dispatched_at": None,
            },
            "feature_builder": {
                "role": "Feature Delivery & Roadmap Lead",
                "task_id": None,
                "status": "idle",
                "phase": "Feature Implementation from TODO",
                "iteration": 0,
                "last_dispatched_at": None,
            },
        }
        self.ui_tests_detected: bool = False
        self.ui_tests_passing: Optional[bool] = None
        self.ui_tests_runner: Optional[str] = None
        self.ui_tests_output: Optional[str] = None

        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def time_remaining_seconds(self) -> int:
        rem = int(self.expire_timestamp - time.time())
        return max(0, rem)

    @property
    def elapsed_seconds(self) -> int:
        return int(time.time() - self.start_timestamp)

    def cancel(self):
        with self._lock:
            if self.status == "running":
                self.status = "cancelled"
            self._stop_event.set()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "project_name": self.project_name,
                "working_dir": self.working_dir,
                "goal": self.goal,
                "status": self.status,
                "iteration": self.iteration,
                "current_task_id": self.current_task_id,
                "last_phase": self.last_phase,
                "started_at": self.started_at,
                "expires_at": self.expires_at,
                "elapsed_seconds": self.elapsed_seconds,
                "elapsed_formatted": self._format_duration(self.elapsed_seconds),
                "time_remaining_seconds": self.time_remaining_seconds,
                "time_remaining_formatted": self._format_duration(self.time_remaining_seconds),
                "duration_hours": round(self.duration_seconds / 3600, 2),
                "check_interval_seconds": self.check_interval_seconds,
                "last_check_at": self.last_check_at,
                "tests_passing": self.tests_passing,
                "ci_workflow_present": self.ci_workflow_present,
                "ui_tests_detected": self.ui_tests_detected,
                "ui_tests_passing": self.ui_tests_passing,
                "ui_tests_runner": self.ui_tests_runner,
                "ui_tests_output": (self.ui_tests_output[:1000] if self.ui_tests_output else None),
                "workers": {k: dict(v) for k, v in self.workers.items()},
                "active_workers_count": sum(1 for w in self.workers.values() if w.get("status") in ("pending", "running")),
                "todo_progress": self.todo_progress,
                "history_count": len(self.history),
                "error": self.error,
            }

    @staticmethod
    def _format_duration(seconds: int) -> str:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours}h {mins}m {secs}s"
        return f"{mins}m {secs}s"


class CompletionLoopManager:
    """Orchestrates autonomous multi-turn 4-hour completion sessions."""

    def __init__(self, task_manager=None, project_manager=None):
        self.task_manager = task_manager
        self.project_manager = project_manager
        self.sessions: Dict[str, CompletionSession] = {}
        self.threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start_completion(
        self,
        project_name: str,
        goal: str,
        duration_hours: float = 4.0,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        check_interval_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Launches an autonomous completion loop for a project."""
        clean_name = os.path.basename(project_name.strip()).lower()
        if not clean_name:
            return {"error": "Project name cannot be empty."}

        # Resolve working directory
        working_dir = None
        if self.project_manager:
            try:
                if hasattr(self.project_manager, "get_project"):
                    proj = self.project_manager.get_project(clean_name)
                    if isinstance(proj, dict) and isinstance(proj.get("path"), str):
                        working_dir = proj.get("path")
            except Exception:
                pass
            if not working_dir:
                try:
                    p = self.project_manager.get_project_path(clean_name)
                    if isinstance(p, str) and os.path.isdir(p):
                        working_dir = p
                except Exception:
                    pass

        if not working_dir:
            try:
                from .project_store import DEFAULT_PROJECTS_DIR
                candidate = os.path.join(DEFAULT_PROJECTS_DIR, clean_name)
                if os.path.isdir(candidate):
                    working_dir = candidate
            except Exception:
                pass

        if not working_dir or not os.path.isdir(working_dir):
            return {"error": f"Project directory for '{clean_name}' not found."}

        with self._lock:
            # Check for existing active session for this project
            for s in self.sessions.values():
                if s.project_name == clean_name and s.status == "running":
                    logger.info("Found existing running completion session %s for %s", s.session_id, clean_name)
                    return s.to_dict()

            session_id = f"comp_{secrets.token_hex(5)}"
            session = CompletionSession(
                session_id=session_id,
                project_name=clean_name,
                working_dir=working_dir,
                goal=goal.strip() if goal else f"Build a complete, error-free application for {clean_name}",
                duration_hours=duration_hours,
                model=model,
                effort=effort,
                check_interval_seconds=check_interval_seconds,
            )
            self.sessions[session_id] = session

            th = threading.Thread(
                target=self._run_loop,
                args=(session,),
                daemon=True,
                name=f"Completer-{session_id}"
            )
            self.threads[session_id] = th
            th.start()
            logger.info("Started 4-hour completion loop session %s for %s", session_id, clean_name)
            return session.to_dict()

    def get_session(self, identifier: str) -> Optional[Any]:
        """Finds session by session_id or project_name."""
        with self._lock:
            if identifier in self.sessions:
                return self.sessions[identifier]
            clean = os.path.basename(identifier.strip()).lower()
            for s in reversed(list(self.sessions.values())):
                if s.project_name == clean:
                    return s
            completions_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "completions.json")
            if os.path.isfile(completions_file):
                try:
                    with open(completions_file, "r", encoding="utf-8") as f:
                        file_data = json.load(f)
                        if isinstance(file_data, list):
                            for item in file_data:
                                if isinstance(item, dict):
                                    if item.get("session_id") == identifier or item.get("project_name") == clean:
                                        return item
                except Exception:
                    pass
        return None

    def cancel_session(self, identifier: str) -> Dict[str, Any]:
        """Gracefully terminates a running completion loop."""
        session = self.get_session(identifier)
        if not session:
            return {"success": False, "error": f"No completion session found matching '{identifier}'"}
        if hasattr(session, "cancel"):
            session.cancel()
            s_dict = session.to_dict()
        else:
            session["status"] = "cancelled"
            s_dict = session
        completions_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "completions.json")
        if os.path.isfile(completions_file):
            try:
                with open(completions_file, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                if isinstance(file_data, list):
                    for item in file_data:
                        if isinstance(item, dict) and item.get("session_id") == s_dict.get("session_id"):
                            item["status"] = "cancelled"
                    with open(completions_file, "w", encoding="utf-8") as f:
                        json.dump(file_data, f, indent=2)
            except Exception:
                pass
        sid = s_dict.get("session_id", identifier)
        logger.info("Cancelled completion session %s", sid)
        return {"success": True, "message": f"Session {sid} cancelled.", "session": s_dict}

    def list_sessions(self, project_name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Lists completion sessions sorted by start time descending."""
        with self._lock:
            sessions_list = list(self.sessions.values())
            completions_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "completions.json")
            if os.path.isfile(completions_file):
                try:
                    with open(completions_file, "r", encoding="utf-8") as f:
                        file_data = json.load(f)
                        if isinstance(file_data, list):
                            existing_ids = {s.session_id for s in sessions_list}
                            for item in file_data:
                                if isinstance(item, dict) and item.get("session_id") not in existing_ids:
                                    sessions_list.append(item)
                except Exception:
                    pass

            if project_name:
                clean_name = os.path.basename(project_name.strip("/")).lower()
                sessions_list = [
                    s for s in sessions_list
                    if (s.project_name if hasattr(s, "project_name") else str(s.get("project_name", "")).lower()) == clean_name
                ]

            def sort_key(s):
                if hasattr(s, "start_timestamp"):
                    return s.start_timestamp
                return s.get("start_timestamp") or 0

            sessions_list.sort(key=sort_key, reverse=True)
            return [s.to_dict() if hasattr(s, "to_dict") else s for s in sessions_list[:limit]]

    # ==========================================================================
    # Project Inspection Helpers
    # ==========================================================================

    def _ensure_and_inspect_todo(self, working_dir: str, project_name: str, goal: str) -> Dict[str, Any]:
        """Ensures TODO.md exists and parses task progress."""
        todo_path = os.path.join(working_dir, "TODO.md")
        if not os.path.exists(todo_path):
            try:
                content = INITIAL_TODO_TEMPLATE.format(project_name=project_name, goal=goal)
                with open(todo_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info("Created initial TODO.md in %s", working_dir)
            except Exception as e:
                logger.warning("Error creating TODO.md: %s", e)

        total, completed, pending = 0, 0, 0
        pending_items = []
        try:
            with open(todo_path, "r", encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if line_s.startswith("- [x]") or line_s.startswith("- [X]"):
                        completed += 1
                        total += 1
                    elif line_s.startswith("- [ ]"):
                        pending += 1
                        total += 1
                        pending_items.append(line_s[5:].strip())
        except Exception as e:
            logger.debug("Error reading TODO.md: %s", e)

        percent = round((completed / total * 100), 1) if total > 0 else 0.0
        return {
            "total": total,
            "completed": completed,
            "pending": pending,
            "percent": percent,
            "pending_items": pending_items,
        }

    def _run_project_tests(self, working_dir: str) -> Tuple[bool, str]:
        """Executes project unit/integration tests non-interactively to check pass/fail status."""
        test_commands = []

        # Check for Python
        if any(os.path.exists(os.path.join(working_dir, f)) for f in ("pytest.ini", "pyproject.toml", "requirements.txt", "setup.py")) or os.path.isdir(os.path.join(working_dir, "tests")):
            venv_pytest = os.path.join(working_dir, ".venv", "bin", "pytest")
            if os.path.exists(venv_pytest):
                test_commands.append([venv_pytest, "-q"])
            else:
                test_commands.append(["python3", "-m", "unittest", "discover", "-s", "tests"])

        # Check for Node
        if os.path.exists(os.path.join(working_dir, "package.json")):
            test_commands.append(["npm", "test", "--", "--passWithNoTests"])

        # Check for Rust
        if os.path.exists(os.path.join(working_dir, "Cargo.toml")):
            test_commands.append(["cargo", "test"])

        # Check for Go
        if os.path.exists(os.path.join(working_dir, "go.mod")):
            test_commands.append(["go", "test", "./..."])

        if not test_commands:
            has_tests = False
            for root, _, files in os.walk(working_dir):
                if any("test" in f.lower() for f in files):
                    has_tests = True
                    break
            if not has_tests:
                return False, "NO_TESTS_FOUND: No test files or testing configuration detected in the project yet."

            return False, "NO_TEST_RUNNER: Test files found but no standard runner (pytest/npm/cargo/go) configured."

        outputs = []
        all_passed = True
        for cmd in test_commands:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60
                )
                output_str = "Command: " + " ".join(cmd) + "\nExit Code: " + str(proc.returncode) + "\n" + proc.stdout + "\n" + proc.stderr
                outputs.append(output_str.strip())
                if proc.returncode != 0:
                    all_passed = False
            except Exception as e:
                all_passed = False
                outputs.append("Command " + " ".join(cmd) + " execution error: " + str(e))

        return all_passed, "\n---\n".join(outputs)

    def _check_github_ci_workflow(self, working_dir: str) -> Tuple[bool, str]:
        """Checks for GitHub Actions workflow files under .github/workflows/."""
        wf_dir = os.path.join(working_dir, ".github", "workflows")
        if not os.path.isdir(wf_dir):
            return False, "MISSING_DIR: .github/workflows/ directory does not exist."

        yaml_files = [f for f in os.listdir(wf_dir) if f.endswith(".yml") or f.endswith(".yaml")]
        if not yaml_files:
            return False, "NO_WORKFLOWS: .github/workflows/ directory exists but contains no .yml/.yaml workflow files."

        return True, "PRESENT: Found workflow files: " + ", ".join(yaml_files)

    def _run_ui_tests(self, working_dir: str) -> Tuple[bool, bool, str, str]:
        """Detects and executes actual UI tests (Playwright, Cypress, Puppeteer, or CLI PTY simulation).
        Returns: (detected: bool, passed: bool, runner_name: str, output: str)
        """
        # 1. Check for Playwright
        has_playwright = any(
            os.path.exists(os.path.join(working_dir, f))
            for f in ("playwright.config.ts", "playwright.config.js")
        )
        if not has_playwright:
            pkg_path = os.path.join(working_dir, "package.json")
            if os.path.exists(pkg_path):
                try:
                    with open(pkg_path, "r", encoding="utf-8") as pf:
                        pkg_data = json.load(pf)
                        scripts = pkg_data.get("scripts", {})
                        if any("playwright" in s.lower() or "ui" in k.lower() or "e2e" in k.lower() for k, s in scripts.items()):
                            has_playwright = True
                except Exception:
                    pass

        if has_playwright:
            try:
                proc = subprocess.run(
                    ["npx", "playwright", "test"],
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=90
                )
                output = f"Playwright UI Tests (Exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
                return True, proc.returncode == 0, "playwright", output.strip()
            except Exception as e:
                return True, False, "playwright", f"Playwright test error: {e}"

        # 2. Check for Cypress
        has_cypress = any(
            os.path.exists(os.path.join(working_dir, f))
            for f in ("cypress.config.js", "cypress.config.ts", "cypress.json")
        ) or os.path.isdir(os.path.join(working_dir, "cypress"))
        if has_cypress:
            try:
                proc = subprocess.run(
                    ["npx", "cypress", "run"],
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=90
                )
                output = f"Cypress UI Tests (Exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
                return True, proc.returncode == 0, "cypress", output.strip()
            except Exception as e:
                return True, False, "cypress", f"Cypress test error: {e}"

        # 3. Check for Python UI / E2E / Interactive CLI tests
        ui_test_files = []
        tests_dir = os.path.join(working_dir, "tests")
        if os.path.isdir(tests_dir):
            for root, _, files in os.walk(tests_dir):
                for f in files:
                    if f.endswith(".py") and any(k in f.lower() for k in ("ui", "e2e", "browser", "interactive", "terminal", "playwright", "tui")):
                        ui_test_files.append(os.path.join(root, f))

        if ui_test_files:
            venv_pytest = os.path.join(working_dir, ".venv", "bin", "pytest")
            cmd = [venv_pytest if os.path.exists(venv_pytest) else "pytest", "-q"] + ui_test_files[:5]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                output = f"Python UI/Interactive Tests (Exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
                return True, proc.returncode == 0, "pytest-ui", output.strip()
            except Exception as e:
                return True, False, "pytest-ui", f"UI test error: {e}"

        return False, False, "none", "NO_UI_TESTS_DETECTED: No Playwright, Cypress, or interactive CLI/UI test suites found yet."

    # ==========================================================================
    # Autonomous Completion Loop (Simultaneous Multi-Instance Workers)
    # ==========================================================================

    def _run_loop(self, session: CompletionSession):
        """Background daemon executing the 4-hour completion loop with simultaneous worker instances."""
        logger.info("Entering 4-hour completion loop for %s with simultaneous workers (expires: %s)", session.project_name, session.expires_at)

        while not session._stop_event.is_set():
            now = time.time()
            if now >= session.expire_timestamp:
                logger.info("Completion session %s reached 4-hour limit. Concluding.", session.session_id)
                session.status = "completed"
                break

            session.last_check_at = _utc_now_iso()

            # 1. Inspect project state
            todo_data = self._ensure_and_inspect_todo(session.working_dir, session.project_name, session.goal)
            session.todo_progress = {
                "total": todo_data.get("total", 0),
                "completed": todo_data.get("completed", 0),
                "pending": todo_data.get("pending", 0),
                "percent": todo_data.get("percent", 0.0)
            }

            tests_ok, test_output = self._run_project_tests(session.working_dir)
            session.tests_passing = tests_ok
            session.last_test_output = test_output

            ui_detected, ui_ok, ui_runner, ui_output = self._run_ui_tests(session.working_dir)
            session.ui_tests_detected = ui_detected
            session.ui_tests_passing = ui_ok if ui_detected else None
            session.ui_tests_runner = ui_runner
            session.ui_tests_output = ui_output

            ci_ok, ci_info = self._check_github_ci_workflow(session.working_dir)
            session.ci_workflow_present = ci_ok

            # If all items are completed, unit tests pass, actual UI tests pass, and CI exists:
            if todo_data.get("pending", 0) == 0 and tests_ok and ui_ok and ci_ok:
                logger.info("Project %s is 100%% complete with all tests (including UI) passing and CI configured!", session.project_name)
                session.status = "completed"
                session.last_phase = "Completed - All Tests & UI Tests Passing with Zero Errors"
                break

            # 2. Check and dispatch simultaneous worker roles
            roles = [
                ("error_hunter", "Error & Bug Hunter"),
                ("test_engineer", "Thorough Test & UI Automation Engineer"),
                ("feature_builder", "Feature Delivery & Roadmap Lead"),
            ]

            any_dispatched = False
            for role_key, role_name in roles:
                worker = session.workers[role_key]
                current_tid = worker.get("task_id")
                is_running = False

                if current_tid and self.task_manager:
                    t = self.task_manager.get_task(current_tid)
                    if t and t.status in ("pending", "running"):
                        is_running = True
                        worker["status"] = t.status
                    elif t:
                        worker["status"] = t.status

                if not is_running:
                    phase, prompt = self._formulate_worker_prompt(
                        role=role_key,
                        session=session,
                        todo_data=todo_data,
                        tests_ok=tests_ok,
                        test_output=test_output,
                        ui_detected=ui_detected,
                        ui_ok=ui_ok,
                        ui_output=ui_output,
                        ci_ok=ci_ok,
                        ci_info=ci_info
                    )

                    worker["iteration"] = worker.get("iteration", 0) + 1
                    worker["phase"] = phase

                    if self.task_manager:
                        try:
                            task_dict = self.task_manager.queue_task(
                                prompt=prompt,
                                project_name=session.project_name,
                                model=session.model,
                                effort=session.effort or "high",
                                timeout=900,  # 15 minutes max per turn
                                allow_concurrent=True,
                                max_concurrent=4
                            )
                            worker["task_id"] = task_dict.get("task_id")
                            worker["status"] = "running"
                            worker["last_dispatched_at"] = _utc_now_iso()
                            session.current_task_id = worker["task_id"]
                            session.last_phase = f"{role_name}: {phase}"
                            any_dispatched = True

                            session.history.append({
                                "role": role_key,
                                "iteration": worker["iteration"],
                                "task_id": worker["task_id"],
                                "phase": phase,
                                "dispatched_at": _utc_now_iso(),
                                "todo_percent": todo_data.get("percent", 0.0),
                                "tests_passing": tests_ok,
                                "ui_tests_passing": ui_ok if ui_detected else None
                            })
                            logger.info("Dispatched simultaneous worker %s (#%d: %s) task %s for %s",
                                        role_key, worker["iteration"], phase, worker["task_id"], session.project_name)
                        except Exception as ex:
                            logger.error("Failed to queue worker %s for %s: %s", role_key, session.project_name, ex)
                            session.error = str(ex)

            # 3. Sleep interval: check every 20s if workers are active, up to check_interval_seconds
            rem = session.time_remaining_seconds
            active_any = any(w.get("status") in ("pending", "running") for w in session.workers.values())
            poll_interval = 20.0 if active_any else min(300.0, float(session.check_interval_seconds))
            sleep_time = min(poll_interval, rem if rem > 0 else poll_interval)
            session._stop_event.wait(timeout=sleep_time)

        if session.status == "running":
            session.status = "completed" if time.time() >= session.expire_timestamp else "cancelled"
        logger.info("Exited completion loop for %s with status %s", session.project_name, session.status)

    def _formulate_worker_prompt(
        self,
        role: str,
        session: CompletionSession,
        todo_data: Dict[str, Any],
        tests_ok: bool,
        test_output: str,
        ui_detected: bool,
        ui_ok: bool,
        ui_output: str,
        ci_ok: bool,
        ci_info: str
    ) -> Tuple[str, str]:
        """Formulates targeted directives for each simultaneous worker instance."""
        time_rem = session._format_duration(session.time_remaining_seconds)
        pending_items = todo_data.get("pending_items", [])

        if role == "error_hunter":
            phase = "Bug Fixing & Error Auditing"
            prompt = f"""[AUTONOMOUS COMPLETION LOOP - INSTANCE 1: ERROR & BUG HUNTER | Time Remaining: {time_rem}]
PRIMARY APPLICATION GOAL: {session.goal}
YOUR ROLE: Dedicated Error & Bug Hunter / Static & Concurrency Auditor

MISSION:
Deeply audit, locate, and fix ALL errors, crashes, edge cases, and runtime flaws across the entire project.

CURRENT SYSTEM HEALTH:
- Automated Tests Passing: {tests_ok}
- Latest Test Runner Output:
```
{test_output[:2500]}
```

REQUIRED ACTIONS:
1. Deeply inspect all codebase files for:
   - Syntax errors, type mismatches, and undefined variables or imports.
   - Unhandled exceptions, missing null/None checks, and malformed inputs.
   - Resource leaks (unclosed sockets, files, DB connections, or orphaned child processes).
   - Concurrency race conditions, deadlocks, and async/await pitfalls.
2. If tests failed above, resolve the root cause of every test failure immediately.
3. Fix all identified bugs directly in the code with clean, idiomatic solutions.
4. Run the test suite in your workspace to verify that zero errors or warnings remain.
5. Record fixes in `TODO.md` under a `## Bug Fixes & Resiliency` section.
6. Stage and commit your work:
   `git add . && git commit -m "fix(audit): resolve runtime bugs, edge cases, and harden error handling" && git push` (if remote is configured).
"""
            return phase, prompt

        elif role == "test_engineer":
            phase = "Thorough Test Suite & Actual UI Tests"
            prompt = f"""[AUTONOMOUS COMPLETION LOOP - INSTANCE 2: THOROUGH TEST & UI AUTOMATION ENGINEER | Time Remaining: {time_rem}]
PRIMARY APPLICATION GOAL: {session.goal}
YOUR ROLE: Lead Test Automation Engineer (Unit, Integration, and ACTUAL UI TESTS)

CRITICAL REQUIREMENT - THOROUGH TESTS INCLUDING ACTUAL UI TESTS:
You must write and execute thorough, production-grade automated tests. This MUST include ACTUAL UI TESTS:

- FOR WEB APPLICATIONS:
  1. Author real browser-based UI automation tests using Playwright, Cypress, Puppeteer, or Selenium.
  2. Test actual DOM interactions:
     - Page navigation, button clicks, form inputs, modal dialogs, tab switching, and dropdowns.
     - Verify visual/responsive behavior across viewport sizes.
     - Validate that ZERO unhandled exceptions or console errors occur in the browser (`page.on('console', ...)` / `page.on('pageerror', ...)`).
  3. Ensure headless execution (`npx playwright test` / `pytest -m ui`) runs cleanly and non-interactively in headless CI mode.

- FOR CLI / TERMINAL APPLICATIONS:
  1. Author real interactive terminal tests (using `pexpect`, `ptyprocess`, or stdin simulation subprocess harnesses).
  2. Simulate real keystrokes, arrow keys, interactive menus, enter keys, Ctrl+C / escape sequences.
  3. Assert ANSI terminal screen outputs, rendered borders, and exit codes.

- FOR BACKEND / API / FULL-STACK:
  1. End-to-end user scenario tests with realistic client sessions, authentication tokens, rate-limits, and fault injection.

CURRENT TEST STATUS:
- Unit/Integration Tests: {"PASSING" if tests_ok else "FAILING / MISSING"}
- Actual UI Tests Detected: {"YES (Passing: " + str(ui_ok) + ")" if ui_detected else "NO (MUST WRITE ACTUAL UI TESTS)"}
- UI Test Output:
```
{ui_output[:2500]}
```

REQUIRED ACTIONS:
1. If UI tests are missing or failing, author real, robust UI test files (e.g. `tests/test_ui.py`, `tests/ui/`, or Playwright/Cypress specs).
2. Execute the tests locally in your workspace to confirm a 100% pass rate with zero errors.
3. Update `TODO.md` to mark all test milestones as `- [x]`.
4. Stage and commit your work:
   `git add . && git commit -m "test(ui): implement thorough automated UI and integration test suite" && git push` (if remote is configured).
"""
            return phase, prompt

        else: # feature_builder
            if not ci_ok:
                phase = "GitHub Actions CI Workflow Setup"
                prompt = f"""[AUTONOMOUS COMPLETION LOOP - INSTANCE 3: FEATURE DELIVERY LEAD | Time Remaining: {time_rem}]
PRIMARY APPLICATION GOAL: {session.goal}
YOUR ROLE: Feature Implementation & Roadmap Lead
CURRENT PHASE: {phase}

CRITICAL REQUIREMENT:
The project is missing GitHub Actions CI workflow configuration (`.github/workflows/ci.yml`).

REQUIRED ACTIONS:
1. Create `.github/workflows/ci.yml` with automated test, lint, and build jobs for every push and pull request.
2. Include steps to run unit tests and actual UI tests.
3. Validate YAML syntax.
4. Mark CI workflow milestone in `TODO.md` as `- [x]`.
5. Stage and commit:
   `git add . && git commit -m "ci: add GitHub Actions workflow tests (.github/workflows/ci.yml)" && git push` (if remote is configured).
"""
                return phase, prompt

            if pending_items:
                next_item = pending_items[0]
                phase = f"Feature Delivery: {next_item[:30]}"
                items_preview = "\n".join(f"- {it}" for it in pending_items[:5])
                prompt = f"""[AUTONOMOUS COMPLETION LOOP - INSTANCE 3: FEATURE DELIVERY LEAD | Time Remaining: {time_rem}]
PRIMARY APPLICATION GOAL: {session.goal}
YOUR ROLE: Feature Implementation & Roadmap Lead
CURRENT MILESTONE: {next_item}

REMAINING TODO ITEMS ({len(pending_items)} pending):
{items_preview}

REQUIRED ACTIONS:
1. Implement the feature: "{next_item}".
2. Integrate cleanly with existing modules and verify zero compilation/runtime regressions.
3. Update `TODO.md`: mark this milestone as `- [x]`.
4. Stage and commit:
   `git add . && git commit -m "feat: implement {next_item[:40]}" && git push` (if remote is configured).
"""
                return phase, prompt

            # Priority: Zero-Error Hardening & Documentation Polish
            phase = "Zero-Error Hardening & Polish"
            prompt = f"""[AUTONOMOUS COMPLETION LOOP - INSTANCE 3: FEATURE DELIVERY LEAD | Time Remaining: {time_rem}]
PRIMARY APPLICATION GOAL: {session.goal}
YOUR ROLE: Feature Implementation & Roadmap Lead
CURRENT PHASE: {phase}

STATUS:
All current TODO milestones are marked complete and tests are passing.

REQUIRED ACTIONS:
1. Polish documentation in `README.md` with complete architecture diagrams, usage guides, and CLI/API examples.
2. Perform final pass on error handling, user ergonomics, and graceful shutdown handling.
3. Update `TODO.md` to confirm project is 100% complete with 0 errors.
4. Stage and commit:
   `git add . && git commit -m "docs: finalize complete application documentation and zero-error polish" && git push` (if remote is configured).
"""
            return phase, prompt

    def _formulate_turn_prompt(
        self,
        session: CompletionSession,
        todo_data: Dict[str, Any],
        tests_ok: bool,
        test_output: str,
        ci_ok: bool,
        ci_info: str
    ) -> Tuple[str, str]:
        """Backward-compatible turn prompt generator."""
        if not tests_ok and "NO_TESTS" not in test_output:
            return self._formulate_worker_prompt(
                role="error_hunter",
                session=session,
                todo_data=todo_data,
                tests_ok=tests_ok,
                test_output=test_output,
                ui_detected=False,
                ui_ok=False,
                ui_output="",
                ci_ok=ci_ok,
                ci_info=ci_info
            )
        elif not ci_ok:
            return self._formulate_worker_prompt(
                role="feature_builder",
                session=session,
                todo_data=todo_data,
                tests_ok=tests_ok,
                test_output=test_output,
                ui_detected=False,
                ui_ok=False,
                ui_output="",
                ci_ok=False,
                ci_info=ci_info
            )
        elif not tests_ok and "NO_TESTS" in test_output:
            phase, prompt = self._formulate_worker_prompt(
                role="test_engineer",
                session=session,
                todo_data=todo_data,
                tests_ok=tests_ok,
                test_output=test_output,
                ui_detected=False,
                ui_ok=False,
                ui_output="",
                ci_ok=ci_ok,
                ci_info=ci_info
            )
            return "Test Suite Creation & Actual UI Tests", prompt
        elif todo_data.get("pending_items"):
            return self._formulate_worker_prompt(
                role="feature_builder",
                session=session,
                todo_data=todo_data,
                tests_ok=tests_ok,
                test_output=test_output,
                ui_detected=False,
                ui_ok=False,
                ui_output="",
                ci_ok=True,
                ci_info=ci_info
            )
        else:
            return self._formulate_worker_prompt(
                role="feature_builder",
                session=session,
                todo_data=todo_data,
                tests_ok=tests_ok,
                test_output=test_output,
                ui_detected=False,
                ui_ok=False,
                ui_output="",
                ci_ok=True,
                ci_info=ci_info
            )

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock
try:
    from completer import CompletionSession, CompletionLoopManager, INITIAL_TODO_TEMPLATE
except ImportError:
    from src.completer import CompletionSession, CompletionLoopManager, INITIAL_TODO_TEMPLATE

class TestCompleter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.mock_tm = MagicMock()
        self.mock_pm = MagicMock()
        self.mock_pm.get_project.return_value = {"name": "test-proj", "path": self.temp_dir}
        self.manager = CompletionLoopManager(task_manager=self.mock_tm, project_manager=self.mock_pm)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_init_and_properties(self):
        session = CompletionSession(
            session_id="comp_12345",
            project_name="test-proj",
            working_dir=self.temp_dir,
            goal="Build a complete REST API",
            duration_hours=4.0
        )
        self.assertEqual(session.duration_seconds, 14400)
        self.assertTrue(session.time_remaining_seconds > 14390)
        self.assertEqual(session.status, "running")
        
        d = session.to_dict()
        self.assertEqual(d["session_id"], "comp_12345")
        self.assertEqual(d["project_name"], "test-proj")
        self.assertEqual(d["goal"], "Build a complete REST API")
        self.assertEqual(d["duration_hours"], 4.0)
        self.assertIn("h", d["time_remaining_formatted"])

        session.cancel()
        self.assertEqual(session.status, "cancelled")
        self.assertTrue(session._stop_event.is_set())

    def test_ensure_and_inspect_todo(self):
        todo_data = self.manager._ensure_and_inspect_todo(
            working_dir=self.temp_dir,
            project_name="test-proj",
            goal="Build snake game"
        )
        todo_path = os.path.join(self.temp_dir, "TODO.md")
        self.assertTrue(os.path.exists(todo_path))
        self.assertTrue(todo_data["total"] > 5)
        self.assertEqual(todo_data["completed"], 0)
        self.assertEqual(todo_data["percent"], 0.0)

        # Mark two items complete
        with open(todo_path, "a", encoding="utf-8") as f:
            f.write("\n- [x] Initialized project structure\n- [x] Wrote core loop\n")

        updated = self.manager._ensure_and_inspect_todo(
            working_dir=self.temp_dir,
            project_name="test-proj",
            goal="Build snake game"
        )
        self.assertEqual(updated["completed"], 2)
        self.assertTrue(updated["percent"] > 0)

    def test_check_github_ci_workflow(self):
        ok, msg = self.manager._check_github_ci_workflow(self.temp_dir)
        self.assertFalse(ok)
        self.assertIn("MISSING_DIR", msg)

        wf_dir = os.path.join(self.temp_dir, ".github", "workflows")
        os.makedirs(wf_dir, exist_ok=True)
        ok2, msg2 = self.manager._check_github_ci_workflow(self.temp_dir)
        self.assertFalse(ok2)
        self.assertIn("NO_WORKFLOWS", msg2)

        with open(os.path.join(wf_dir, "ci.yml"), "w") as f:
            f.write("name: CI\n")

        ok3, msg3 = self.manager._check_github_ci_workflow(self.temp_dir)
        self.assertTrue(ok3)
        self.assertIn("PRESENT", msg3)

    def test_formulate_turn_prompts(self):
        session = CompletionSession(
            session_id="comp_test",
            project_name="test-proj",
            working_dir=self.temp_dir,
            goal="Complete app",
            duration_hours=4.0
        )
        
        # Priority 1: Failing tests
        phase1, prompt1 = self.manager._formulate_turn_prompt(
            session=session,
            todo_data={"pending_items": ["item1"], "total": 5, "completed": 0, "pending": 5, "percent": 0.0},
            tests_ok=False,
            test_output="AssertionError: 2 != 3",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Bug Fixing", phase1)
        self.assertIn("AssertionError", prompt1)
        self.assertIn("git commit -m", prompt1)

        # Priority 2: Missing CI
        phase2, prompt2 = self.manager._formulate_turn_prompt(
            session=session,
            todo_data={"pending_items": ["item1"], "total": 5, "completed": 1, "pending": 4, "percent": 20.0},
            tests_ok=True,
            test_output="OK",
            ci_ok=False,
            ci_info="MISSING"
        )
        self.assertIn("GitHub Actions", phase2)
        self.assertIn(".github/workflows/ci.yml", prompt2)

        # Priority 3: Missing Tests
        phase3, prompt3 = self.manager._formulate_turn_prompt(
            session=session,
            todo_data={"pending_items": ["item1"], "total": 5, "completed": 1, "pending": 4, "percent": 20.0},
            tests_ok=False,
            test_output="NO_TESTS_FOUND: No test files detected",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Test Suite Creation", phase3)

        # Priority 4: Feature delivery
        phase4, prompt4 = self.manager._formulate_turn_prompt(
            session=session,
            todo_data={"pending_items": ["Implement JWT auth"], "total": 5, "completed": 3, "pending": 1, "percent": 60.0},
            tests_ok=True,
            test_output="OK",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Feature Delivery", phase4)
        self.assertIn("Implement JWT auth", prompt4)

        # Priority 5: All done -> Zero error polish
        phase5, prompt5 = self.manager._formulate_turn_prompt(
            session=session,
            todo_data={"pending_items": [], "total": 5, "completed": 5, "pending": 0, "percent": 100.0},
            tests_ok=True,
            test_output="OK",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Zero-Error Hardening", phase5)

    def test_manager_start_and_cancel(self):
        res = self.manager.start_completion(
            project_name="test-proj",
            goal="Build complete app",
            duration_hours=4.0
        )
        self.assertIn("session_id", res)
        sid = res["session_id"]
        
        session = self.manager.get_session(sid)
        self.assertIsNotNone(session)
        self.assertEqual(session.status, "running")

        # Duplicate start returns existing running session
        res2 = self.manager.start_completion(
            project_name="test-proj",
            goal="Build complete app"
        )
        self.assertEqual(res2["session_id"], sid)

        # Cancel
        c_res = self.manager.cancel_session(sid)
        self.assertTrue(c_res["success"])
        self.assertEqual(session.status, "cancelled")

        # List sessions
        sessions = self.manager.list_sessions()
        self.assertTrue(len(sessions) >= 1)
        self.assertEqual(sessions[0]["session_id"], sid)

    def test_simultaneous_workers_initialization_and_serialization(self):
        session = CompletionSession(
            session_id="comp_multi",
            project_name="test-proj",
            working_dir=self.temp_dir,
            goal="Build high quality SaaS with UI",
            duration_hours=4.0
        )
        # Verify 3 simultaneous worker slots
        self.assertIn("error_hunter", session.workers)
        self.assertIn("test_engineer", session.workers)
        self.assertIn("feature_builder", session.workers)

        d = session.to_dict()
        self.assertIn("workers", d)
        self.assertEqual(len(d["workers"]), 3)
        self.assertIn("error_hunter", d["workers"])
        self.assertIn("test_engineer", d["workers"])
        self.assertIn("feature_builder", d["workers"])
        self.assertIn("ui_tests_detected", d)
        self.assertIn("ui_tests_passing", d)

    def test_worker_prompt_formulation(self):
        session = CompletionSession(
            session_id="comp_prompts",
            project_name="test-proj",
            working_dir=self.temp_dir,
            goal="Build high quality SaaS with UI",
            duration_hours=4.0
        )
        todo_data = {"pending_items": ["Add billing portal"], "total": 5, "completed": 2, "pending": 3, "percent": 40.0}

        # Error Hunter
        phase_eh, prompt_eh = self.manager._formulate_worker_prompt(
            role="error_hunter",
            session=session,
            todo_data=todo_data,
            tests_ok=False,
            test_output="NullPointerException in auth.py",
            ui_detected=False,
            ui_ok=False,
            ui_output="",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Bug Fixing", phase_eh)
        self.assertIn("Error & Bug Hunter", prompt_eh)
        self.assertIn("NullPointerException", prompt_eh)

        # Test Engineer - Must mandate actual UI tests
        phase_te, prompt_te = self.manager._formulate_worker_prompt(
            role="test_engineer",
            session=session,
            todo_data=todo_data,
            tests_ok=True,
            test_output="OK",
            ui_detected=False,
            ui_ok=False,
            ui_output="NO_UI_TESTS_DETECTED",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Actual UI Tests", phase_te)
        self.assertIn("ACTUAL UI TESTS", prompt_te)
        self.assertIn("Playwright", prompt_te)
        self.assertIn("terminal", prompt_te)

        # Feature Builder
        phase_fb, prompt_fb = self.manager._formulate_worker_prompt(
            role="feature_builder",
            session=session,
            todo_data=todo_data,
            tests_ok=True,
            test_output="OK",
            ui_detected=True,
            ui_ok=True,
            ui_output="3 passed",
            ci_ok=True,
            ci_info="OK"
        )
        self.assertIn("Feature Delivery", phase_fb)
        self.assertIn("Add billing portal", prompt_fb)

    def test_ui_tests_detection(self):
        # 1. Initially no UI tests
        detected, passed, runner, out = self.manager._run_ui_tests(self.temp_dir)
        self.assertFalse(detected)
        self.assertEqual(runner, "none")

        # 2. Add a Playwright config
        pw_file = os.path.join(self.temp_dir, "playwright.config.ts")
        with open(pw_file, "w") as f:
            f.write("// playwright config\n")

        # Playwright runner should be detected (even if npx fails to execute without node)
        detected2, passed2, runner2, out2 = self.manager._run_ui_tests(self.temp_dir)
        self.assertTrue(detected2)
        self.assertEqual(runner2, "playwright")

if __name__ == "__main__":
    unittest.main()

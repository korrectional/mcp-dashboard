import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import main

@pytest.fixture
def mock_agyc_server():
    server = MagicMock()
    with patch.dict("sys.modules", {"src.server": server}):
        yield server

class TestAgycApiStatus:
    @pytest.mark.parametrize("route", ["/api/status", "/mcpd/api/status"])
    def test_status_unauthenticated_returns_401(self, client, route):
        response = client.get(route)
        assert response.status_code == 401
        assert response.json().get("detail") == "Authentication required"

    @pytest.mark.parametrize("route", ["/api/status", "/mcpd/api/status"])
    def test_status_authenticated_returns_data(self, auth_client, route):
        mock_data = {
            "projects": [{"name": "proj1"}],
            "managed_projects": [{"name": "proj1"}],
            "host_services": [],
            "tasks": [{"id": "t1"}],
            "routes": [{"path": "/r1"}],
            "online": True
        }
        with patch("main.fetch_agyc_status", new_callable=AsyncMock, return_value=mock_data):
            response = auth_client.get(route)
            assert response.status_code == 200
            assert response.json() == mock_data


class TestTaskDispatchApi:
    @pytest.mark.parametrize("route", ["/api/tasks/dispatch", "/mcpd/api/tasks/dispatch"])
    def test_dispatch_unauthenticated(self, client, route):
        response = client.post(route, data={"project_name": "p1", "prompt": "test"})
        assert response.status_code == 401

    def test_dispatch_success_minimal_params(self, auth_client, mock_agyc_server):
        mock_agyc_server.queue_task.return_value = {"task_id": "task_101", "status": "queued"}
        response = auth_client.post("/api/tasks/dispatch", data={
            "project_name": "mcp-dashboard",
            "prompt": "run tests"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["task_id"] == "task_101"
        mock_agyc_server.queue_task.assert_called_once_with(
            project_name="mcp-dashboard",
            task="run tests",
            model=None,
            effort=None
        )

    def test_dispatch_with_model_and_effort(self, auth_client, mock_agyc_server):
        mock_agyc_server.queue_task.return_value = {"task_id": "task_102"}
        response = auth_client.post("/mcpd/api/tasks/dispatch", data={
            "project_name": "animation-renderer",
            "prompt": "render scene",
            "model": "gemini-2.5-pro",
            "effort": "high"
        })
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_agyc_server.queue_task.assert_called_once_with(
            project_name="animation-renderer",
            task="render scene",
            model="gemini-2.5-pro",
            effort="high"
        )

    def test_dispatch_with_error_in_result(self, auth_client, mock_agyc_server):
        mock_agyc_server.queue_task.return_value = {"error": "Project not found"}
        response = auth_client.post("/api/tasks/dispatch", data={
            "project_name": "invalid-project",
            "prompt": "do something"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Project not found"

    def test_dispatch_with_non_dict_result(self, auth_client, mock_agyc_server):
        mock_agyc_server.queue_task.return_value = "Task queued successfully"
        response = auth_client.post("/api/tasks/dispatch", data={
            "project_name": "p1",
            "prompt": "p"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["result"] == "Task queued successfully"

    def test_dispatch_exception_handling(self, auth_client, mock_agyc_server):
        mock_agyc_server.queue_task.side_effect = RuntimeError("Dispatch pipeline failure")
        response = auth_client.post("/api/tasks/dispatch", data={
            "project_name": "p1",
            "prompt": "p"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Dispatch pipeline failure" in data["error"]


class TestTaskInteractiveInputApi:
    @pytest.mark.parametrize("route", ["/api/tasks/t123/input", "/mcpd/api/tasks/t123/input"])
    def test_task_input_unauthenticated(self, client, route):
        response = client.post(route, data={"user_input": "yes"})
        assert response.status_code == 401

    def test_task_input_success_dict(self, auth_client, mock_agyc_server):
        mock_agyc_server.send_input_to_task.return_value = {"delivered": True}
        response = auth_client.post("/api/tasks/task_42/input", data={"user_input": "approve"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["delivered"] is True
        mock_agyc_server.send_input_to_task.assert_called_once_with(task_id="task_42", input_text="approve")

    def test_task_input_error_dict(self, auth_client, mock_agyc_server):
        mock_agyc_server.send_input_to_task.return_value = {"error": "Task not awaiting input"}
        response = auth_client.post("/api/tasks/task_42/input", data={"user_input": "approve"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_task_input_non_dict_result(self, auth_client, mock_agyc_server):
        mock_agyc_server.send_input_to_task.return_value = "sent"
        response = auth_client.post("/api/tasks/task_42/input", data={"user_input": "ok"})
        assert response.status_code == 200
        assert response.json() == {"success": True, "result": "sent"}

    def test_task_input_exception_handling(self, auth_client, mock_agyc_server):
        mock_agyc_server.send_input_to_task.side_effect = Exception("Input pipe broken")
        response = auth_client.post("/api/tasks/task_42/input", data={"user_input": "ok"})
        assert response.status_code == 200
        assert response.json() == {"success": False, "error": "Input pipe broken"}


class TestTaskCancelAndStatusApi:
    @pytest.mark.parametrize("route", ["/api/tasks/t10/cancel", "/mcpd/api/tasks/t10/cancel"])
    def test_cancel_unauthenticated(self, client, route):
        response = client.post(route)
        assert response.status_code == 401

    def test_cancel_success(self, auth_client, mock_agyc_server):
        mock_agyc_server.cancel_task.return_value = {"task_id": "t10", "cancelled": True}
        response = auth_client.post("/api/tasks/t10/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_cancel_non_dict_result(self, auth_client, mock_agyc_server):
        mock_agyc_server.cancel_task.return_value = "Task t10 killed"
        response = auth_client.post("/api/tasks/t10/cancel")
        assert response.status_code == 200
        assert response.json() == {"success": True, "result": "Task t10 killed"}

    def test_cancel_exception(self, auth_client, mock_agyc_server):
        mock_agyc_server.cancel_task.side_effect = RuntimeError("Could not cancel")
        response = auth_client.post("/api/tasks/t10/cancel")
        assert response.status_code == 200
        assert response.json() == {"success": False, "error": "Could not cancel"}

    @pytest.mark.parametrize("route", ["/api/tasks/t10", "/mcpd/api/tasks/t10"])
    def test_task_details_unauthenticated(self, client, route):
        response = client.get(route)
        assert response.status_code == 401

    def test_task_details_success(self, auth_client, mock_agyc_server):
        mock_agyc_server.get_task_status.return_value = {"task_id": "t10", "state": "running", "logs": ["started"]}
        response = auth_client.get("/api/tasks/t10")
        assert response.status_code == 200
        assert response.json()["state"] == "running"

    def test_task_details_exception(self, auth_client, mock_agyc_server):
        mock_agyc_server.get_task_status.side_effect = KeyError("Task not found")
        response = auth_client.get("/api/tasks/t999")
        assert response.status_code == 200
        assert "error" in response.json()


class TestGitCommitAndCommandRunApi:
    @pytest.mark.parametrize("route", ["/api/projects/commit", "/mcpd/api/projects/commit"])
    def test_project_commit_unauthenticated(self, client, route):
        response = client.post(route, data={"project_name": "p1"})
        assert response.status_code == 401

    def test_project_commit_success(self, auth_client, mock_agyc_server):
        mock_agyc_server.git_commit_and_push.return_value = {"success": True, "commit": "a1b2c3d"}
        response = auth_client.post("/api/projects/commit", data={
            "project_name": "mcp-dashboard",
            "message": "feat: new feature"
        })
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_agyc_server.git_commit_and_push.assert_called_once_with(
            project_name="mcp-dashboard",
            message="feat: new feature"
        )

    def test_project_commit_without_message(self, auth_client, mock_agyc_server):
        mock_agyc_server.git_commit_and_push.return_value = {"success": True}
        response = auth_client.post("/mcpd/api/projects/commit", data={"project_name": "p1"})
        assert response.status_code == 200
        mock_agyc_server.git_commit_and_push.assert_called_once_with(project_name="p1", message=None)

    def test_project_commit_exception(self, auth_client, mock_agyc_server):
        mock_agyc_server.git_commit_and_push.side_effect = Exception("Git push rejected")
        response = auth_client.post("/api/projects/commit", data={"project_name": "p1"})
        assert response.status_code == 200
        assert response.json() == {"success": False, "error": "Git push rejected"}

    @pytest.mark.parametrize("route", ["/api/commands/run", "/mcpd/api/commands/run"])
    def test_command_run_unauthenticated(self, client, route):
        response = client.post(route, data={"command": "/usage"})
        assert response.status_code == 401

    def test_command_run_success(self, auth_client, mock_agyc_server):
        mock_agyc_server.run_agyc_command.return_value = {"output": "Quota: 95% available"}
        response = auth_client.post("/api/commands/run", data={"command": "/usage", "timeout": "45"})
        assert response.status_code == 200
        assert response.json()["output"] == "Quota: 95% available"
        mock_agyc_server.run_agyc_command.assert_called_once_with(command="/usage", timeout=45)

    def test_command_run_exception(self, auth_client, mock_agyc_server):
        mock_agyc_server.run_agyc_command.side_effect = TimeoutError("Command timed out")
        response = auth_client.post("/api/commands/run", data={"command": "/slow"})
        assert response.status_code == 200
        assert response.json() == {"success": False, "error": "Command timed out"}

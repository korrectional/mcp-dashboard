# AGYC // DASHBOARD & BUCKETLOADER MANAGER

A control panel and file manager interface for **Google Antigravity CLI (AGYC)** and the **Bucketloader Google Drive Object Storage** engine, deployed under `https://kanevskyy.dev/mcpd/`.

Styled with the **Neo Kinpaku** aesthetic inspired by [pbakaus/impeccable](https://github.com/pbakaus/impeccable).

---

## 1. System Architecture

```
[ Browser / Operator ]
        │  HTTPS (443)
        ▼
   [ Cloudflare ]
        │  HTTPS
        ▼
 [ Nginx (Reverse Proxy) ]
        │  /mcpd/ -> http://127.0.0.1:8096/
        │  /bucket/ -> http://127.0.0.1:8090/
        ▼
 [ AGYC Dashboard Service (FastAPI :8096) ]
   ├── Authentication & Rate Limiter (Anti-Bruteforce IP Lockout)
   ├── AGYC MCP Service Interface (Projects, Tasks, Dispatch, Commits)
   └── Bucketloader Secure Proxy (Authenticated Downloads, Uploads, Edits)
```

### Routing Endpoints
| Route | Access | Description |
| :--- | :--- | :--- |
| `/mcpd/` (or `/`) | Authenticated | Main AGYC Dashboard (telemetry, managed projects, host infrastructure, runner) |
| `/mcpd/bucket` (or `/bucket`) | Authenticated | Interactive Bucketloader file manager & editor |
| `/mcpd/login` (or `/login`) | Public | Operator authentication portal with anti-bruteforce security |
| `/mcpd/logout` (or `/logout`) | Authenticated | Destroys active session cookie |
| `/mcpd/api/bucket/files/{file_id}/download` | Authenticated | Streams file attachment with `Content-Disposition: attachment` |
| `/mcpd/api/bucket/raw/{filepath}/download` | Authenticated | Streams file attachment by relative filepath |
| `/mcpd/api/bucket/upload` | Authenticated | Multi-part file upload directly into Google Drive bucket |
| `/mcpd/api/bucket/save_text` | Authenticated | Creates or overwrites text/markdown/code files in bucket |
| `/mcpd/api/bucket/files/{file_id}/content` | Authenticated | Fetches file text or base64 preview for modal editor |
| `/mcpd/api/bucket/files/{file_id}` (DELETE) | Authenticated | Deletes/trashes a file from Google Drive |
| `/mcpd/api/status` | Authenticated | Real-time project telemetry, tasks, and system routes |
| `/mcpd/api/tasks/dispatch` | Authenticated | Dispatches background task prompt to Antigravity CLI |
| `/mcpd/api/tasks/past` | Authenticated | Retrieves past tasks from `mcp-database/tasks.json` (default: 5 tasks) |
| `/mcpd/api/tasks/past/{task_id}` | Authenticated | Fetches full detailed JSON record of a past task from `mcp-database/` |
| `/mcpd/api/projects/commit` | Authenticated | Triggers one-click checkpoint commit and git push |
| `/mcpd/api/commands/run` | Authenticated | Executes slash commands (e.g. `/usage`, `/models`, `/status`) |

---

## 2. Authentication & Security Engine

### Operator Credentials
- **Username**: `korrectional`
- **Password**: `Minegame.1`

### Security Safeguards
1. **Anti-Bruteforce Exponential Backoff**:
   - Progressive server-side latency delay applied after each failed login attempt:
     - 1st failed attempt: 1.0s delay
     - 2nd failed attempt: 2.0s delay
     - 3rd failed attempt: 4.0s delay
     - 4th failed attempt: 8.0s delay
2. **IP Lockout Enforcement**:
   - 5 failed login attempts within 1 hour triggers a strict **15-minute IP lockout** (HTTP `429 Too Many Requests`).
   - Client IP is extracted from proxy headers (`X-Forwarded-For`, `X-Real-IP`, or socket host).
3. **Constant-Time Verification**:
   - Identity and passkey matching uses `secrets.compare_digest` / `hmac.compare_digest` to prevent timing side-channel attacks.
4. **Cryptographic Session Tokens**:
   - Session tokens are signed using HMAC-SHA256 with a unique server secret key stored in `.secret_key`.
   - Cookie configuration:
     - `agyc_session`: `HttpOnly=True`, `Secure=True`, `SameSite=Lax`, `Path=/`, `Max-Age=7 days`.
5. **Dual Protection Layer**:
   - Web browser requests to protected routes redirect cleanly via HTTP `303 See Other` to `/mcpd/login?next=...`.
   - API / AJAX requests receive HTTP `401 Unauthorized` with JSON error payloads.

---

## 3. Bucketloader Download & File Operations

### Authenticated Download Features
- **Direct Attachment Streaming**:
  - The endpoint `/mcpd/api/bucket/files/{file_id}/download` proxies chunks directly from the Google Drive storage daemon (:8090) while enforcing valid session authentication.
  - Generates RFC-compliant `Content-Disposition: attachment; filename="{filename}"` headers ensuring browsers download files directly to disk rather than playing them inline.
  - Supports HTTP Range requests (`Accept-Ranges: bytes`) for resumable downloads.
- **Recursive Subfolder Discovery**:
  - Automatically indexes both root items and nested subfolders (e.g., `renders/cli_sample.mp4`, `renders/kinetic_telemetry_waveform.mp4`).
  - Displays hierarchical paths directly in the table.
- **Multi-Mode Actions**:
  - **Download ↓**: Saves the file attachment locally.
  - **Stream ↗**: Opens browser media viewer/direct streaming preview.
  - **Edit**: Opens text/code modal editor for real-time file updates.
  - **Delete**: Trashes file from Google Drive storage.

---

## 4. Past Tasks Archive & Bucket Database (`mcp-database/`)

### Google Drive Bucket Database Architecture
Past tasks executed by the AGYC engine (via web dashboard dispatch or host CLI) are automatically persisted into Google Drive object storage under the `mcp-database/` folder:

- **Aggregated History Archive**:
  - `mcp-database/tasks.json`: A single JSON array storing past tasks sorted in descending chronological order by completion time.
- **Granular Task Records**:
  - `mcp-database/{task_id}.json`: Dedicated JSON record storing full prompt, terminal output (`stdout`/`stderr`), exit code, duration, checkpoints, and execution timings.

### Bandwidth Conservation Strategy
To prevent high data transfer and protect Google Drive API quotas:
1. **Paging / Slicing Limit**: The dashboard and API query endpoints only retrieve the **last 5 tasks** by default (`limit=5`).
2. **In-Memory Cache (TTL 15s)**: Dashboard page reloads and polling checks reuse cached in-memory task lists rather than continuously querying Google Drive.
3. **On-Demand Log Retrieval**: Full terminal logs and output strings are fetched asynchronously only when an operator clicks **"View Output"** for a specific task.
4. **Direct Raw Download**: Operators can download individual JSON files or the full aggregated database with dedicated attachment download links.

---

## 5. Autonomous 4-Hour Completion Loop (`complete`)

### Autonomous Continuous Engineering Architecture
The `complete` command implements a self-healing, long-running (4-hour) continuous development cycle that drives projects from inception or partial progress to a 100% complete application with zero runtime errors, automated test coverage, and GitHub Actions CI.

```
                  [ complete / complete_project ]
                                │
                                ▼
         ┌──────────────────────────────────────────────┐
         │ 4-Hour Autonomous Completion Session (14400s) │
         └──────────────────────┬───────────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │ Every 5 Minutes: Cron Inspection Loop │
            └───────────────────┬───────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
  [ Automated Tests ]       [ CI Workflow ]         [ TODO.md ]
  Python: pytest            .github/workflows/      Structured roadmap
  Node: npm test            ci.yml verification     Completed vs Pending
  Rust: cargo test          Multi-step runners      Progress percentage
  Go: go test               Coverage reporting
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │ Formulate Targeted Sub-Turn Directives│
            │ Phase 1: Bug Fixing & Test Resolution │
            │ Phase 2: GitHub Actions CI Setup      │
            │ Phase 3: Automated Test Creation      │
            │ Phase 4: Feature Implementation       │
            │ Phase 5: Zero-Error Hardening         │
            └───────────────────┬───────────────────┘
                                │
                                ▼
            [ Agent Self-Commit via Prompt Directives ]
            git add . && git commit -m "..." && git push
```

### Key Engineering Features
1. **5-Minute Health & Status Loop**:
   - Runs a background thread monitoring `agyc` execution. If a subtask finishes, crashes, or pauses, the engine inspects repository state immediately and dispatches the next turn.
2. **Dynamic `TODO.md` Roadmap Generation**:
   - If `TODO.md` is absent, the engine scans the workspace, analyzes existing code and the user's primary goal, and creates a comprehensive milestone roadmap.
   - Automatically tracks pending (`- [ ]`) versus completed (`- [x]`) items and computes progress percentages.
3. **Multi-Language Automated Test Suites**:
   - Native auto-detection and execution for:
     - Python: `pytest` / `python -m unittest`
     - Node.js: `npm test`
     - Rust: `cargo test`
     - Go: `go test ./...`
   - Test failures and stack traces are captured and injected directly into the next turn's prompt for automated self-healing.
4. **GitHub Actions CI Workflow Generation**:
   - Verifies presence of `.github/workflows/ci.yml`. If missing, schedules a high-priority subtask instructing the agent to author a production-grade CI pipeline.
5. **Milestone Self-Commitments**:
   - The agent stages and commits its own work after completing each milestone (`git add . && git commit -m "..." && git push`). No blind host auto-commit timer is run.
6. **4-Hour Autonomous Lifespan**:
   - The loop maintains execution for up to 4.0 hours (14,400 seconds) or until all milestones are resolved and tests pass with 0 errors.

### MCP Tools & REST API Endpoints
| MCP Tool | HTTP Endpoint | Description |
| :--- | :--- | :--- |
| `complete` | `POST /mcpd/api/complete` | Launches a 4-hour autonomous completion loop |
| `complete_project` | `POST /mcpd/api/complete` | Alias for `complete` |
| `get_completion_status` | `GET /mcpd/api/complete/{id}` | Real-time status, countdown, TODO %, and test output |
| `cancel_completion` | `POST /mcpd/api/complete/{id}/cancel` | Gracefully cancels an active completion session |
| `list_completions` | `GET /mcpd/api/complete` | Lists active and past completion sessions |

---

## 6. Deployment & Maintenance

The service runs under systemd on the host VM:
```bash
sudo systemctl status mcp-dashboard.service
sudo systemctl restart mcp-dashboard.service
```

Logs can be viewed via journalctl:
```bash
journalctl -u mcp-dashboard.service -f -n 50
```

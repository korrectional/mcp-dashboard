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

## 4. Deployment & Maintenance

The service runs under systemd on the host VM:
```bash
sudo systemctl status mcp-dashboard.service
sudo systemctl restart mcp-dashboard.service
```

Logs can be viewed via journalctl:
```bash
journalctl -u mcp-dashboard.service -f -n 50
```

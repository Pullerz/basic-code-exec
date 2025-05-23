# Stateless Python REPL API Service

A stateless, session-based Python REPL API built with FastAPI, providing code evaluation, file management, and safe command execution in sandboxed directories.

This service is ideal for powering online code runners, grading and assessment systems, educational platforms, and interactive coding tutorials—anywhere you need isolated, programmatic execution of user code with robust safety and file handling.

---

## Features

- **Session Management:** Each session has its own isolated temporary directory, identified by a session ID (UUID).
- **Code Execution:** Evaluate Python code against test cases with entry-point specification, input/output validation, and per-case isolation.
- **Command Execution:** Run shell commands within the session's sandboxed directory.
- **File Management:** Read, write, rename, and delete files safely within the session directory.
- **Session Forking:** Duplicate all files and state in a session to a new session ID.
- **Security Precautions:**
  - All activities confined to per-session sandbox directories.
  - Strict path traversal protection.
  - Resource limits (CPU, memory, wall-clock time) enforced for code execution.
- **Intended Use Cases:** Online code runners, auto-graders, educational coding platforms, secure code sandboxes for learning and assessment.

---

## API Endpoints

| Method | Endpoint        | Description                                                                 | Request / Response (Summary)                |
|--------|----------------|-----------------------------------------------------------------------------|---------------------------------------------|
| GET    | `/`            | Health check (returns `"ping"`)                                             | —                                           |
| POST   | `/evaluate_code` | Evaluate code with test cases in session sandbox                           | `{id, code, entry_point, io_cases}` → `{passed, case_results, error}` |
| POST   | `/run`         | Run shell command in session directory                                      | `{id, cmd}` → `{stdout, stderr, id}`        |
| GET    | `/read_file`   | Read file contents from session directory                                   | `id`, `rel_path` (query) → `{content, id, rel_path}` |
| POST   | `/write_file`  | Write file in session directory (creates directories as needed)              | `{id, rel_path, content}` → `{success, id, rel_path}` |
| POST   | `/delete_file` | Delete file from session directory                                          | `{id, rel_path}` → `{success, id, rel_path}`|
| POST   | `/rename_file` | Rename (move) file within session directory                                 | `{id, old_path, new_path}` → `{success, id, old_path, new_path}` |
| POST   | `/fork_session`| Duplicate all files in a session to a new session ID                        | `{id}` → `{new_id}`                         |

- **Sessions:** To start, generate a UUID as your session ID (or use the Python client helper).
- **All file and command operations are restricted to that session's sandbox.**

---

## Setup Instructions

### Local Development

1. **Install Python 3.11+**
2. **Install dependencies:**
    ```bash
    pip install -r container-requirements.txt
    ```
3. **Run the service:**
    ```bash
    uvicorn main:app --reload
    ```

### Docker

1. **Build the Docker image:**
    ```bash
    docker build -t repl-api .
    ```
2. **Run the container:**
    ```bash
    docker run -p 6000:6000 repl-api
    ```
    - The service will be available at `http://localhost:6000`

---

## Usage Examples

### 1. Create a Session (touch file to create workspace)

```bash
SESSION_ID=$(uuidgen)
curl -X POST http://localhost:6000/write_file \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$SESSION_ID\", \"rel_path\": \"example.py\", \"content\": \"print('Hello, World!')\"}"
```

### 2. Run a Command

```bash
curl -X POST http://localhost:6000/run \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$SESSION_ID\", \"cmd\": \"python example.py\"}"
```

### 3. Read a File

```bash
curl "http://localhost:6000/read_file?id=$SESSION_ID&rel_path=example.py"
```

### 4. Evaluate Code with Test Cases

```bash
curl -X POST http://localhost:6000/evaluate_code \
  -H "Content-Type: application/json" \
  -d '{
        "id": "'"$SESSION_ID"'",
        "code": "def add(a, b):\n    return a + b",
        "entry_point": "add",
        "io_cases": [
          {"input": "a=1, b=2", "output": "3"},
          {"input": "a=5, b=5", "output": "10"}
        ]
      }'
```

### 5. Fork a Session

```bash
curl -X POST http://localhost:6000/fork_session \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$SESSION_ID\"}"
```

---

### Python Async Client Usage

A fully async Python client is available in [`repl_client.py`](./repl_client.py):

```python
import asyncio
from repl_client import ReplClient

async def main():
    async with ReplClient("http://localhost:6000") as client:
        session_id = await client.create_session()
        await client.write_file(session_id, "example.py", "print('Hello, REPL!')")
        result = await client.run(session_id, "python example.py")
        print(result.stdout, result.stderr)
        # Evaluate code with test cases
        eval_result = await client.evaluate_code(
            session_id,
            code="def add(a, b): return a + b",
            entry_point="add",
            io_cases=[{"input": "a=2, b=3", "output": "5"}]
        )
        print(eval_result.passed, eval_result.case_results)
        # Clean up
        await client.delete_file(session_id, "example.py")

asyncio.run(main())
```
**Install dependencies for the client:**
```bash
pip install httpx pydantic
```

---

## Dependencies

- **Python packages** (see `container-requirements.txt`):
  - `fastapi`
  - `uvicorn`
  - `pydantic`
  - `httpx` (for client)
  - `starlette`
- **System requirements:**
  - **Python 3.11+**
  - **SWI-Prolog** (`swipl`) — installed in Docker image for full compatibility
- **Other:** The API is stateless between sessions, but files are stored in `/tmp/genie_repl` during a session's lifetime.

---

## Security Notes

- **Sandboxed Execution:** Each session operates in its own directory under `/tmp/genie_repl`.
- **Path Traversal Protection:** All file operations validate and normalize paths to prevent escaping the session sandbox.
- **Resource Limits:** Code evaluation is sandboxed with strict limits on CPU time, memory usage, and is run in a subprocess pool for isolation.
- **Production Recommendations:**
  - Deploy behind an application firewall.
  - Run the service in a container or VM with resource quotas.
  - Use regular cleanup for `/tmp/genie_repl` as needed.
  - Disable debug mode and restrict network access if exposed publicly.

---

## Contributing

Contributions are welcome! Please open an issue or pull request to contribute improvements, new features, or bug fixes.

---

## License

[Specify your license here, e.g., MIT, Apache 2.0, etc.]
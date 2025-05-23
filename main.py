import os
import resource
import shutil
import subprocess
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.responses import JSONResponse

app = FastAPI(title="Stateless Python REPL API", debug=True)

TMP_ROOT = "/tmp/genie_repl"


class ForkSessionRequest(BaseModel):
    id: str


class ForkSessionResponse(BaseModel):
    new_id: str


class RunCommandRequest(BaseModel):
    id: str
    cmd: str


class EvaluateCodeRequest(BaseModel):
    id: str
    code: str
    entry_point: str
    io_cases: list


class EvaluateCodeResponse(BaseModel):
    passed: bool
    case_results: list
    error: str = None


class WriteFileRequest(BaseModel):
    id: str
    rel_path: str  # relative file path within session directory (may include subdirs)
    content: str


class RenameFileRequest(BaseModel):
    id: str
    old_path: str
    new_path: str


class DeleteFileRequest(BaseModel):
    id: str
    rel_path: str


def safe_dirname(session_id: str) -> str:
    # Prevent path traversal
    session_id = session_id.replace("/", "_").replace("..", "__")
    return os.path.join(TMP_ROOT, session_id)


@app.post("/fork_session")
def fork_session(req: ForkSessionRequest):
    """Fork an existing session: duplicates all files/directories, returns new session id."""
    old_id = req.id
    old_dir = safe_dirname(old_id)
    if not os.path.isdir(old_dir):
        raise HTTPException(status_code=404, detail="Session to fork not found")
    new_id = str(uuid.uuid4())
    new_dir = safe_dirname(new_id)
    try:
        shutil.copytree(old_dir, new_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fork session: {e}")
    return JSONResponse({"new_id": new_id})


def safe_rel_path(rel_path: str) -> str:
    # For nested writes/reads: normalize, prevent absolute or parent traversal
    rel_path = os.path.normpath(rel_path)
    if rel_path.startswith("../") or rel_path.startswith("/"):
        raise ValueError("Invalid path")
    if ".." in rel_path.split(os.path.sep):
        raise ValueError("Invalid path: Parent traversal not allowed")
    return rel_path


@app.get("/")
def ping():
    """Health check endpoint."""
    return "ping"


@app.post("/evaluate_code")
async def evaluate_code(req: EvaluateCodeRequest):
    try:
        all_passed, case_results = evaluate_cases(
            code=req.code,
            entry_point=req.entry_point,
            io_cases=req.io_cases,
            # you can override timeout / mem here if you like:
            # timeout=2.0, mem_limit_mb=256, cpu_seconds=2
        )
    except Exception as e:
        traceback.print_exc()
        raise e
    return {
        "passed": all_passed,
        "case_results": case_results,
        "error": None,  # evaluator_refactor already folds per-case errors in
    }


def _run_single_case(
    code: str,
    entry_point: str,
    case: Dict[str, str],
    mem_limit_mb: int,
    cpu_seconds: int,
) -> Dict[str, Any]:
    """Executes one IO-case in an isolated process and *returns* a result dict."""
    try:
        # ------------------------------------------------------------------- #
        # -- sandbox this very process --
        # ------------------------------------------------------------------- #
        soft, hard = mem_limit_mb * 1024 * 1024, mem_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (int(soft), int(hard)))
        resource.setrlimit(
            resource.RLIMIT_CPU, (int(cpu_seconds), int(cpu_seconds) + 1)
        )

        # -- run the user’s code ------------------------------------------- #
        exec_globals: Dict[str, Any] = {}
        exec(code, exec_globals)  # may raise

        # Build the arg-list exactly as before ----------------------------- #
        import inspect
        import re
        from ast import literal_eval

        def _parse_kw(input_str: str) -> Dict[str, Any]:
            pattern = r"(\w+)\s*=\s*(.+?)(?=,\s*\w+\s*=|$)"
            out = {}
            for m in re.finditer(pattern, input_str):
                k, v = m.group(1).strip(), m.group(2).strip()
                try:
                    out[k] = literal_eval(v)
                except Exception:
                    pass
            return out

        def _parse_list(input_str: str):
            try:
                return [literal_eval(input_str.strip())]
            except Exception:
                return []

        params = _parse_kw(case["input"])
        list_params = _parse_list(case["input"]) if not params else []

        func = eval(entry_point, exec_globals)
        arg_spec = inspect.getfullargspec(func)
        arg_names = (
            arg_spec.args[1:]
            if arg_spec.args and arg_spec.args[0] == "self"
            else arg_spec.args
        )
        args = [params[a] for a in arg_names] if params else list_params
        got = func(*args) if params else func(*args)

        try:
            expected = literal_eval(case["output"])
        except Exception:
            expected = eval(case["output"], exec_globals)

        return {
            "passed": got == expected,
            "got": got,
            "expected": expected,
            "error": None,
        }

    except Exception as e:
        return {
            "passed": False,
            "got": None,
            "expected": case.get("output"),
            "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        }


def evaluate_cases(
    code: str,
    entry_point: str,
    io_cases: List[Dict[str, str]],
    *,
    timeout: float = 5.0,  # per-case timeout (seconds)
    mem_limit_mb: int = 2048,
    cpu_seconds: int = 4,
    max_workers: int | None = None,  # NEW: pool size (defaults to CPU count)
) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Run *one* function implementation against *many* IO cases in parallel.

    Returns
    -------
    all_passed : bool
        True only if **every** case passed.
    case_results : list[dict]
        Per-case dicts with ``passed | got | expected | error`` keys.
    """
    max_workers = max_workers or int(os.getenv("REPL_MAX_WORKERS", os.cpu_count() or 4))

    case_results: List[Dict[str, Any]] = []
    all_passed = True

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                _run_single_case, code, entry_point, case, mem_limit_mb, cpu_seconds
            )
            for case in io_cases
        ]

        for idx, (case, fut) in enumerate(zip(io_cases, futures)):
            try:
                result = fut.result(timeout=timeout)
            except TimeoutError:
                fut.cancel()
                result = {
                    "passed": False,
                    "got": None,
                    "expected": case.get("output"),
                    "error": "Timeout",
                }
            except Exception as e:
                # Shouldn’t normally reach here – _run_single_case already catches,
                # but keep a belt-and-braces handler.
                result = {
                    "passed": False,
                    "got": None,
                    "expected": case.get("output"),
                    "error": f"{type(e).__name__}: {e}",
                }

            result["case"] = idx
            result["input"] = case.get("input")
            case_results.append(result)
            all_passed &= result["passed"]

    return all_passed, case_results


@app.post("/run")
def run_command(req: RunCommandRequest):
    """Run the provided command in a per-session tmp dir and return stdout and stderr.
    The user is expected to have already written any required files via /write_file.
    """
    session_id = req.id
    cmd = req.cmd
    workdir = safe_dirname(session_id)
    if not os.path.isdir(workdir):
        raise HTTPException(status_code=400, detail="Session does not exist")
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            cwd=workdir,
            timeout=30,
            check=False,  # We'll handle errors
            text=True,
        )
        return JSONResponse(
            {"stdout": proc.stdout, "stderr": proc.stderr, "id": session_id}
        )
    except Exception as e:
        import traceback

        tb = traceback.format_exc()
        return JSONResponse(
            {
                "stdout": "",
                "stderr": f"Exception running command: {e}\n{tb}",
                "id": session_id,
            },
            status_code=500,
        )


@app.get("/read_file")
def read_file(id: str, rel_path: str):
    """Read a file's contents from the temporary FS for a given session."""
    try:
        workdir = safe_dirname(id)
        rel_path = safe_rel_path(rel_path)
        file_path = os.path.join(workdir, rel_path)
        # Ensure file_path is under workdir
        if not os.path.abspath(file_path).startswith(os.path.abspath(workdir)):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="File does not exist")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content, "id": id, "rel_path": rel_path}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/write_file")
def write_file(req: WriteFileRequest):
    """Write content to a file in the session temp FS, creating directories as needed."""
    try:
        workdir = safe_dirname(req.id)
        rel_path = safe_rel_path(req.rel_path)
        target_path = os.path.join(workdir, rel_path)
        # Secure: prevent writes outside session dir
        abs_workdir = os.path.abspath(workdir)
        abs_target = os.path.abspath(target_path)
        if not abs_target.startswith(abs_workdir):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        target_dir = os.path.dirname(abs_target)
        os.makedirs(target_dir, exist_ok=True)
        with open(abs_target, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"success": True, "id": req.id, "rel_path": req.rel_path}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete_file")
def delete_file(req: DeleteFileRequest):
    """Delete a file from the session temporary FS."""
    try:
        workdir = safe_dirname(req.id)
        rel_path = safe_rel_path(req.rel_path)
        target_path = os.path.join(workdir, rel_path)
        abs_workdir = os.path.abspath(workdir)
        abs_target = os.path.abspath(target_path)
        if not abs_target.startswith(abs_workdir):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        if not os.path.isfile(abs_target):
            raise HTTPException(status_code=404, detail="File does not exist")
        os.remove(abs_target)
        return {"success": True, "id": req.id, "rel_path": req.rel_path}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rename_file")
def rename_file(req: RenameFileRequest):
    """Rename (move) a file within the session temporary FS."""
    try:
        workdir = safe_dirname(req.id)
        old_rel = safe_rel_path(req.old_path)
        new_rel = safe_rel_path(req.new_path)

        old_abs = os.path.join(workdir, old_rel)
        new_abs = os.path.join(workdir, new_rel)
        abs_workdir = os.path.abspath(workdir)

        if not os.path.abspath(old_abs).startswith(abs_workdir) or not os.path.abspath(
            new_abs
        ).startswith(abs_workdir):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        if not os.path.isfile(old_abs):
            raise HTTPException(status_code=404, detail="Old file does not exist")
        target_dir = os.path.dirname(new_abs)
        os.makedirs(target_dir, exist_ok=True)
        os.rename(old_abs, new_abs)
        return {
            "success": True,
            "id": req.id,
            "old_path": req.old_path,
            "new_path": req.new_path,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

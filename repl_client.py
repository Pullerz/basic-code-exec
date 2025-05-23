import traceback
import uuid
from typing import Any, Optional

import httpcore
import httpx
from pydantic import BaseModel, ValidationError


class RunCommandRequest(BaseModel):
    id: str
    cmd: str


class ScriptResponse(BaseModel):
    stdout: str
    stderr: str
    id: str


class WriteFileRequest(BaseModel):
    id: str
    rel_path: str
    content: str


class WriteFileResponse(BaseModel):
    success: bool
    id: str
    rel_path: str


class ReadFileResponse(BaseModel):
    content: str
    id: str
    rel_path: str


class DeleteFileRequest(BaseModel):
    id: str
    rel_path: str


class DeleteFileResponse(BaseModel):
    success: bool
    id: str
    rel_path: str


class RenameFileRequest(BaseModel):
    id: str
    old_path: str
    new_path: str


class RenameFileResponse(BaseModel):
    success: bool
    id: str
    old_path: str
    new_path: str


class ReplServerError(Exception):
    """Custom exception for REPL server errors."""

    def __init__(
        self, message: str, status_code: Optional[int] = None, details: Any = None
    ):
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(f"{message} (status_code={status_code}, details={details})")


class ForkSessionResponse(BaseModel):
    new_id: str


class EvaluateCodeRequest(BaseModel):
    id: str
    code: str
    entry_point: str
    io_cases: list


class EvaluateCodeResponse(BaseModel):
    passed: bool
    case_results: list
    error: Optional[str] = None


class ReplClient:
    """
    Usage:
    import asyncio
    async def main():
        async with ReplClient("http://localhost:6000") as client:
            session_id = await client.create_session()
            await client.write_file(session_id, "example.py", "print('Hello, REPL!')")
            result = await client.run(session_id, "python example.py")
            await client.delete_file(session_id, "example.py")
            await client.rename_file(session_id, "foo.txt", "bar.txt")
            # New: code evaluation
            eval_result = await client.evaluate_code(session_id, code, entry_point, io_cases)
            print(result.stdout, result.stderr, eval_result.passed)
    asyncio.run(main())
    """

    def __init__(self, base_url: str = "http://localhost:6000", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout=timeout))

    async def fork_session(self, id: str) -> str:
        """Fork a session by duplicating all files, returning the new session id."""
        payload = {"id": id}
        try:
            resp = await self._client.post(
                f"{self.base_url}/fork_session", json=payload
            )
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text
                raise ReplServerError(
                    "Failed to fork session",
                    status_code=resp.status_code,
                    details=detail,
                )
            data = resp.json()
            result = ForkSessionResponse(**data)
            return result.new_id
        except ValidationError as ve:
            raise ReplServerError(
                "Invalid response structure from /fork_session", details=str(ve)
            )
        except Exception as e:
            raise ReplServerError("Error during /fork_session request", details=str(e))

    async def ping(self) -> bool:
        try:
            resp = await self._client.get(f"{self.base_url}/")
            resp.raise_for_status()
            return resp.text.strip() == "ping"
        except Exception as e:
            raise ReplServerError("Unable to ping REPL server", details=str(e))

    async def create_session(self) -> str:
        # Generates a UUID suitable for a session id and ensures the workdir is created via write_file.
        session_id = str(uuid.uuid4())
        # Touch a file to create the session
        await self.write_file(session_id, "placeholder.txt", "")
        return session_id

    async def run(self, id: str, cmd: str) -> ScriptResponse:
        payload = RunCommandRequest(id=id, cmd=cmd).model_dump()
        try:
            resp = await self._client.post(f"{self.base_url}/run", json=payload)
            if resp.status_code != 200:
                # Attempt to parse error details
                stderr = ""
                try:
                    stderr = resp.json().get("stderr") or resp.json().get("detail", "")
                except Exception:
                    stderr = resp.text
                raise ReplServerError(
                    "Failed to execute command",
                    status_code=resp.status_code,
                    details=stderr,
                )
            data = resp.json()
            return ScriptResponse(**data)
        except ValidationError as ve:
            raise ReplServerError(
                "Invalid response structure from /run", details=str(ve)
            )
        except httpcore.ReadTimeout as rte:
            raise ReplServerError(
                "Clientside timeout while waiting for REPL server response",
                status_code=408,
                details=str(rte),
            )
        except ReplServerError as rse:
            raise rse
        except Exception as e:
            raise ReplServerError("Error during /run request", details=str(e))

    async def write_file(
        self, id: str, rel_path: str, content: str
    ) -> WriteFileResponse:
        payload = WriteFileRequest(id=id, rel_path=rel_path, content=content).model_dump()
        try:
            resp = await self._client.post(f"{self.base_url}/write_file", json=payload)
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text
                raise ReplServerError(
                    "Failed to write file", status_code=resp.status_code, details=detail
                )
            data = resp.json()
            return WriteFileResponse(**data)
        except ValidationError as ve:
            raise ReplServerError(
                "Invalid response structure from /write_file", details=str(ve)
            )
        except Exception as e:
            print("Error during /write_file request", e)
            traceback.print_exc()
            raise ReplServerError(
                "Error during /write_file request",
                details=f"{str(e)}\n\nId: {id}\nPath: {rel_path}\nContent: {content}",
            )

    async def read_file(self, id: str, rel_path: str) -> ReadFileResponse:
        params = {"id": id, "rel_path": rel_path}
        try:
            resp = await self._client.get(f"{self.base_url}/read_file", params=params)
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text
                raise ReplServerError(
                    "Failed to read file", status_code=resp.status_code, details=detail
                )
            data = resp.json()
            return ReadFileResponse(**data)
        except ValidationError as ve:
            raise ReplServerError(
                "Invalid response structure from /read_file", details=str(ve)
            )
        except Exception as e:
            raise ReplServerError("Error during /read_file request", details=str(e))

    async def delete_file(self, id: str, rel_path: str) -> DeleteFileResponse:
        payload = DeleteFileRequest(id=id, rel_path=rel_path).model_dump()
        try:
            resp = await self._client.post(f"{self.base_url}/delete_file", json=payload)
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text
                raise ReplServerError(
                    "Failed to delete file",
                    status_code=resp.status_code,
                    details=detail,
                )
            data = resp.json()
            return DeleteFileResponse(**data)
        except ValidationError as ve:
            raise ReplServerError(
                "Invalid response structure from /delete_file", details=str(ve)
            )
        except Exception as e:
            raise ReplServerError("Error during /delete_file request", details=str(e))

    async def rename_file(
        self, id: str, old_path: str, new_path: str
    ) -> RenameFileResponse:
        payload = RenameFileRequest(id=id, old_path=old_path, new_path=new_path).model_dump()
        try:
            resp = await self._client.post(f"{self.base_url}/rename_file", json=payload)
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = resp.text
                raise ReplServerError(
                    "Failed to rename file",
                    status_code=resp.status_code,
                    details=detail,
                )
            data = resp.json()
            return RenameFileResponse(**data)
        except ValidationError as ve:
            raise ReplServerError(
                "Invalid response structure from /rename_file", details=str(ve)
            )
        except Exception as e:
            raise ReplServerError("Error during /rename_file request", details=str(e))

    async def evaluate_code(
        self,
        id: str,
        code: str,
        entry_point: str,
        io_cases: list,
        timeout: float = None,
    ) -> EvaluateCodeResponse:
        """
        Evaluate code for given session id, code, entry_point, and io_cases.
        Timeout is handled via the HTTP request.
        """
        payload = EvaluateCodeRequest(
            id=id, code=code, entry_point=entry_point, io_cases=io_cases
        ).model_dump()
        # Use the passed timeout (if any), else default to self.timeout
        client_timeout = timeout if timeout is not None else self.timeout
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=client_timeout)
        ) as client:
            try:
                resp = await client.post(f"{self.base_url}/evaluate_code", json=payload)
                if resp.status_code != 200:
                    try:
                        detail = resp.json().get("detail", "")
                    except Exception:
                        detail = resp.text
                    raise ReplServerError(
                        "Failed to evaluate code",
                        status_code=resp.status_code,
                        details=detail,
                    )
                data = resp.json()
                return EvaluateCodeResponse(**data)
            except ValidationError as ve:
                raise ReplServerError(
                    "Invalid response structure from /evaluate_code", details=str(ve)
                )
            except httpcore.ReadTimeout as rte:
                raise ReplServerError(
                    "Clientside timeout while waiting for REPL server response",
                    status_code=408,
                    details=str(rte),
                )
            except Exception as e:
                stack = traceback.format_exc()
                raise ReplServerError(
                    "Error during /evaluate_code request",
                    details=f"{str(e)}\n\n{stack}",
                )

    async def aclose(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

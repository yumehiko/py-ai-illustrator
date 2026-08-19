"""Versioned contract and AppleScript bridge for the direct native runtime."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

NATIVE_COMPILE_CONTRACT = "py-ai-illustrator.native-compile"
NATIVE_RESULT_CONTRACT = "py-ai-illustrator.native-compile-result"
NATIVE_CONTRACT_VERSION = 1
NATIVE_COMPILE_OPERATION = "compile"
NATIVE_REQUEST_FILENAME = "py-ai-native-request.json"
NATIVE_RUNTIME_FILENAME = "py-ai-native-runtime.jsx"
NATIVE_RUNTIME_RESOURCE = "runtime/direct_native.jsx"
NATIVE_REQUIRED_CHECKS = (
    "structure_and_order",
    "stable_identity",
    "geometry_and_style",
    "linked_resources",
    "native_editability",
    "pdf_compatible_ai",
)


class NativeContractError(ValueError):
    """Raised when the Illustrator runtime crosses an invalid contract boundary."""


@dataclass(frozen=True, slots=True)
class NativeCompileRequest:
    """The complete, JSON-serializable input sent to the Illustrator runtime."""

    document: dict[str, object]
    destination: str

    def __post_init__(self) -> None:
        if not isinstance(self.document, dict):
            raise NativeContractError("Native compile document must be an object")
        if not isinstance(self.destination, str) or not self.destination:
            raise NativeContractError("Native compile destination must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": NATIVE_COMPILE_CONTRACT,
            "version": NATIVE_CONTRACT_VERSION,
            "operation": NATIVE_COMPILE_OPERATION,
            "destination": self.destination,
            "document": self.document,
        }


def serialize_native_compile_request(request: NativeCompileRequest) -> str:
    """Serialize a request using strict JSON semantics at the runtime boundary."""

    try:
        return json.dumps(
            request.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise NativeContractError(f"Could not serialize native compile request: {error}") from error


def parse_native_compile_result(stdout: str) -> dict[str, Any]:
    """Parse and validate one versioned result returned by ExtendScript."""

    response = stdout.strip()
    if not response:
        raise NativeContractError("Illustrator returned an empty response")
    try:
        result = json.loads(response, parse_constant=_reject_non_finite_json)
    except (TypeError, ValueError) as error:
        raise NativeContractError("Illustrator returned a non-JSON response") from error
    if not isinstance(result, dict):
        raise NativeContractError("Illustrator result contract must be an object")
    if result.get("contract") != NATIVE_RESULT_CONTRACT:
        raise NativeContractError("Illustrator returned an unsupported result contract")
    if type(result.get("version")) is not int or result["version"] != NATIVE_CONTRACT_VERSION:
        raise NativeContractError("Illustrator returned an unsupported result contract version")
    if result.get("operation") != NATIVE_COMPILE_OPERATION:
        raise NativeContractError("Illustrator returned an unsupported result operation")
    if type(result.get("ok")) is not bool:
        raise NativeContractError("Illustrator result contract must contain a boolean 'ok'")
    if result["ok"]:
        checks = result.get("checks")
        if not isinstance(checks, dict):
            raise NativeContractError("Successful Illustrator result must contain checks")
        missing = [name for name in NATIVE_REQUIRED_CHECKS if name not in checks]
        if missing:
            raise NativeContractError(
                "Successful Illustrator result is missing required checks: "
                + ", ".join(missing)
            )
        non_boolean = [name for name in NATIVE_REQUIRED_CHECKS if type(checks[name]) is not bool]
        if non_boolean:
            raise NativeContractError(
                "Successful Illustrator result checks must be boolean: "
                + ", ".join(non_boolean)
            )
        failed = [name for name in NATIVE_REQUIRED_CHECKS if checks[name] is not True]
        if failed:
            raise NativeContractError(
                "Successful Illustrator result contains failed checks: "
                + ", ".join(failed)
            )
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is not supported")


def load_native_runtime_source() -> str:
    """Load the project-owned JSX runtime without embedding it in Python."""

    return (
        resources.files("py_ai_illustrator")
        .joinpath(NATIVE_RUNTIME_RESOURCE)
        .read_text(encoding="utf-8")
    )


ScriptExecutor = Callable[..., CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class NativeRuntimeBridge:
    """Place the request/runtime in a temporary directory and invoke Illustrator."""

    runtime_loader: Callable[[], str] = load_native_runtime_source

    def execute(
        self,
        request: NativeCompileRequest,
        directory: str | Path,
        *,
        timeout: float,
        application_name: str,
        runtime_source: str | None = None,
        script_executor: ScriptExecutor | None = None,
    ) -> CompletedProcess[str]:
        runtime_directory = Path(directory)
        runtime_directory.mkdir(parents=True, exist_ok=True)
        request_path = runtime_directory / NATIVE_REQUEST_FILENAME
        request_path.write_text(
            serialize_native_compile_request(request),
            encoding="utf-8",
            newline="\n",
        )
        runtime_source = self.runtime_loader() if runtime_source is None else runtime_source
        if script_executor is None:
            from ._illustrator_bridge import execute_javascript

            script_executor = execute_javascript
        return script_executor(
            runtime_source,
            runtime_directory,
            timeout=timeout,
            application_name=application_name,
            script_name=NATIVE_RUNTIME_FILENAME,
        )

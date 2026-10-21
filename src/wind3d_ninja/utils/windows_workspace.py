from __future__ import annotations

import ctypes
from dataclasses import fields, is_dataclass, replace
from functools import wraps
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, TypeVar


_T = TypeVar("_T")
_PRIVATE_WORKSPACE_SDDL = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
_MAX_WORKSPACE_ROOT_LENGTH = 25
_MAX_OUTPUT_DIRECTORY_LENGTH = 50


def _is_solver_safe(path: Path) -> bool:
    value = str(path)
    return value.isascii() and not any(character.isspace() for character in value)


def _windows_workspace_root() -> tuple[Path, bool]:
    public = Path(os.environ.get("PUBLIC", "C:/Users/Public"))
    candidates = (
        *((Path(value), False) for value in (os.environ.get("WIND3D_WORK_ROOT"),) if value),
        (Path(os.environ.get("ProgramData", "C:/ProgramData")), True),
        (public / "Documents", True),
        (Path(tempfile.gettempdir()), False),
    )
    for candidate, private in candidates:
        try:
            candidate = candidate.expanduser().resolve()
            if not _is_solver_safe(candidate) or len(str(candidate)) > _MAX_WORKSPACE_ROOT_LENGTH:
                continue
            candidate.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=candidate, prefix="wind3d-check-", delete=True):
                pass
            return candidate, private
        except OSError:
            continue
    raise OSError(
        "No writable short ASCII Windows workspace is available. Set WIND3D_WORK_ROOT "
        "to a writable path of at most 25 characters without spaces or non-ASCII characters."
    )


def _make_workspace() -> Path:
    root, needs_private_acl = _windows_workspace_root()
    workspace = Path(tempfile.mkdtemp(prefix="w3-", dir=root))
    if needs_private_acl:
        try:
            _set_private_workspace_acl(workspace)
        except BaseException:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
    return workspace


def _set_private_workspace_acl(path: Path) -> None:
    if sys.platform != "win32":
        return
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_ulong()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        _PRIVATE_WORKSPACE_SDDL, 1, ctypes.byref(descriptor), ctypes.byref(descriptor_size)
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "ConvertStringSecurityDescriptorToSecurityDescriptorW failed")
    try:
        security_information = 0x00000004 | 0x80000000
        if not advapi32.SetFileSecurityW(str(path), security_information, descriptor):
            error = ctypes.get_last_error()
            raise OSError(error, "SetFileSecurityW failed")
    finally:
        if kernel32.LocalFree(descriptor):
            error = ctypes.get_last_error()
            raise OSError(error, "LocalFree failed")


def _replace_path_text(value: str, staged_output: Path, final_output: Path) -> str:
    staged = str(staged_output)
    final = str(final_output)
    alternatives = ((staged, final), (staged.replace("\\", "/"), final.replace("\\", "/")))
    for source, destination in alternatives:
        value = value.replace(source, destination)
    return value


def _rewrite_json_string(value: str, staged_output: Path, final_output: Path) -> str:
    rewritten = _replace_path_text(value, staged_output, final_output)
    try:
        nested = json.loads(rewritten)
    except json.JSONDecodeError:
        return rewritten
    if not isinstance(nested, (dict, list)):
        return rewritten
    nested_rewritten = _rewrite_json_value(nested, staged_output, final_output)
    if nested_rewritten == nested:
        return rewritten
    return json.dumps(nested_rewritten, ensure_ascii=False, indent=2)


def _remap_value(value: Any, staged_output: Path, final_output: Path) -> Any:
    if isinstance(value, Path):
        try:
            return final_output / value.resolve().relative_to(staged_output.resolve())
        except ValueError:
            return value
    if isinstance(value, str):
        return _replace_path_text(value, staged_output, final_output)
    if isinstance(value, tuple):
        return tuple(_remap_value(item, staged_output, final_output) for item in value)
    if isinstance(value, list):
        return [_remap_value(item, staged_output, final_output) for item in value]
    if isinstance(value, dict):
        return {
            _remap_value(key, staged_output, final_output): _remap_value(item, staged_output, final_output)
            for key, item in value.items()
        }
    if is_dataclass(value) and not isinstance(value, type):
        return replace(
            value,
            **{
                field.name: _remap_value(getattr(value, field.name), staged_output, final_output)
                for field in fields(value)
            },
        )
    return value


def _rewrite_json_value(value: Any, staged_output: Path, final_output: Path) -> Any:
    if isinstance(value, str):
        return _rewrite_json_string(value, staged_output, final_output)
    if isinstance(value, list):
        return [_rewrite_json_value(item, staged_output, final_output) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_json_value(item, staged_output, final_output) for key, item in value.items()}
    return value


def _rewrite_exported_references(output_tree: Path, staged_output: Path, final_output: Path) -> None:
    for path in output_tree.rglob("*.json"):
        try:
            original = path.read_text(encoding="utf-8")
            parsed = json.loads(original)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        rewritten = _rewrite_json_value(parsed, staged_output, final_output)
        if rewritten != parsed:
            path.write_text(json.dumps(rewritten, ensure_ascii=False, indent=2), encoding="utf-8")
    for suffix in ("*.cfg", "*.csv"):
        for path in output_tree.rglob(suffix):
            try:
                original = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rewritten = _replace_path_text(original, staged_output, final_output)
            if rewritten != original:
                path.write_text(rewritten, encoding="utf-8")
    try:
        import netCDF4
    except ImportError:
        return
    for path in output_tree.rglob("*.nc"):
        try:
            with netCDF4.Dataset(path, "a") as dataset:
                for name in dataset.ncattrs():
                    value = dataset.getncattr(name)
                    if isinstance(value, str):
                        rewritten = _rewrite_json_string(value, staged_output, final_output)
                        if rewritten != value:
                            dataset.setncattr(name, rewritten)
        except OSError:
            continue


def _copy_staged_output(staged_output: Path, final_output: Path) -> None:
    if staged_output.exists():
        _rewrite_exported_references(staged_output, staged_output, final_output)
        final_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged_output, final_output, dirs_exist_ok=True, copy_function=shutil.copy2)


def _remap_exception(error: BaseException, staged_output: Path, final_output: Path) -> None:
    error.args = tuple(_remap_value(value, staged_output, final_output) for value in error.args)


def _preserve_workspace(error: BaseException, workspace: Path) -> None:
    location = f"Temporary results preserved at: {workspace}"
    error.args = (f"{error}\n{location}",)
    if hasattr(error, "add_note"):
        error.add_note(location)


def windows_safe_output(
    function: Callable[..., _T],
) -> Callable[..., _T]:
    @wraps(function)
    def wrapped(self: Any, input_dir: Path, output_dir: Path, *args: Any, **kwargs: Any) -> _T:
        final_output = Path(output_dir).expanduser().resolve()
        if sys.platform != "win32" or (
            _is_solver_safe(final_output) and len(str(final_output)) <= _MAX_OUTPUT_DIRECTORY_LENGTH
        ):
            return function(self, input_dir, output_dir, *args, **kwargs)

        workspace = _make_workspace()
        staged_output = workspace / "o"
        result: _T | None = None
        failure: BaseException | None = None
        copy_failure: BaseException | None = None
        try:
            if final_output.exists():
                shutil.copytree(final_output, staged_output, copy_function=shutil.copy2)
            result = function(self, input_dir, staged_output, *args, **kwargs)
        except BaseException as error:
            failure = error
        try:
            _copy_staged_output(staged_output, final_output)
        except BaseException as error:
            copy_failure = error
        if copy_failure is None:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            _preserve_workspace(failure or copy_failure, workspace)
            raise (failure or copy_failure).with_traceback((failure or copy_failure).__traceback__)
        if failure is not None:
            _remap_exception(failure, staged_output, final_output)
            raise failure.with_traceback(failure.__traceback__)
        return _remap_value(result, staged_output, final_output)

    return wrapped


def solver_safe_dem(dem: Path, output_dir: Path) -> Path:
    source = Path(dem)
    if sys.platform != "win32" or _is_solver_safe(source.expanduser().resolve()):
        return source
    destination = Path(output_dir).expanduser().resolve() / "terrain" / "solver_dem.tif"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination

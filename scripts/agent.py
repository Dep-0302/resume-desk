#!/usr/bin/env python3
"""Install and operate the classic ResumeDesk floating panel for an Agent.

The controller deliberately has no model, network, credential, startup-item, or
security-setting behaviour.  It only builds a disposable source snapshot,
installs a versioned classic panel bundle, and calls the recovery backend that
is embedded in that installed bundle.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Iterator, Mapping, Optional, Tuple, Union


SCHEMA_VERSION = 1
DEFAULT_INSTALL_ROOT = Path.home() / "Library" / "Application Support" / "ResumeDesk"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
SYSTEM_PYTHON = Path("/usr/bin/python3")
APP_NAME = "断点复原浮窗.app"
APP_BACKEND = Path("Contents") / "Resources" / "Recovery" / "recovery.py"
RECEIPT_NAME = "install.json"
RELEASES_NAME = "releases"
REVIEW_DIR_NAME = "AgentReview"
INSTALL_GUIDE = "INSTALL_AGENT.md"
PROTOCOL_GUIDE = Path("docs") / "AGENT_PROTOCOL.md"
MAX_JSON_BYTES = 4 * 1024 * 1024
REQUIRED_CODEX_COLUMNS = {
    "id", "title", "cwd", "rollout_path", "archived", "source", "updated_at", "first_user_message",
}
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?$")


class ControllerError(RuntimeError):
    code = "controller_error"
    exit_code = 2


class PermissionDenied(ControllerError):
    code = "permission_denied"
    exit_code = 3


class IncompatibleSource(ControllerError):
    code = "incompatible_source"


class VersionConflict(ControllerError):
    code = "version_conflict"


class NotReady(ControllerError):
    code = "not_ready"


class UsageError(ControllerError):
    code = "usage"


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def repo_root() -> Path:
    """The source checkout root when this controller is run from scripts/."""
    return Path(__file__).resolve().parent.parent


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sanitise_error(value: Any, limit: int = 600) -> str:
    text = str(value or "")
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[credential hidden]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]{16,}", "[credential hidden]", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:limit]


def json_output(payload: Mapping[str, Any]) -> None:
    print(json.dumps({"schema_version": SCHEMA_VERSION, **payload}, ensure_ascii=False, sort_keys=True))


def path_from_argument(value: Union[str, Path]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).absolute()
    # Canonicalise a user-selected root once (macOS itself exposes /var through
    # a system link).  Controller-created descendants are still checked below
    # so no managed file can escape that selected root later.
    return path.resolve(strict=False)


def assert_no_symlink_components(path: Path) -> None:
    """Do not allow controller-owned paths to escape through a symlink."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise PermissionDenied(f"无法访问路径组件：{current}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ControllerError(f"控制器不接受含软链接的路径：{current}")


def ensure_directory(path: Path, *, mode: int = 0o700) -> Path:
    path = path_from_argument(path)
    assert_no_symlink_components(path)
    try:
        if path.exists():
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ControllerError(f"预期目录但发现其他对象：{path}")
        else:
            path.mkdir(parents=True, mode=mode)
            os.chmod(path, mode)
    except PermissionError as exc:
        raise PermissionDenied(f"没有创建或访问目录的权限：{path}") from exc
    return path


def require_regular_file(path: Path, *, label: str = "文件") -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ControllerError(f"缺少{label}：{path}") from exc
    except PermissionError as exc:
        raise PermissionDenied(f"没有读取{label}的权限：{path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ControllerError(f"{label}必须是普通文件：{path}")
    return path


def require_system_python() -> Path:
    """`/usr/bin/python3` is a fixed system entrypoint and may itself be linked."""
    try:
        if not SYSTEM_PYTHON.is_file() or not os.access(SYSTEM_PYTHON, os.X_OK):
            raise ControllerError("缺少可执行的 /usr/bin/python3")
    except PermissionError as exc:
        raise PermissionDenied("没有执行系统 Python 的权限") from exc
    return SYSTEM_PYTHON


def contained_path(root: Path, candidate: Path) -> Path:
    root = root.resolve(strict=False)
    candidate = candidate.resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ControllerError("路径不能跳出指定目录")
    return candidate


def safe_child(parent: Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ControllerError("非法的内部文件名")
    return contained_path(parent, parent / name)


def read_json(path: Path, *, label: str = "JSON") -> dict[str, Any]:
    require_regular_file(path, label=label)
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ControllerError(f"{label}过大")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except PermissionError as exc:
        raise PermissionDenied(f"没有读取{label}的权限：{path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerError(f"{label}不是合法 JSON：{path}") from exc
    if not isinstance(payload, dict):
        raise ControllerError(f"{label}根节点必须是对象：{path}")
    return payload


def atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    ensure_directory(path.parent, mode=0o700)
    if path.exists():
        require_regular_file(path, label="控制器文件")
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
    except PermissionError as exc:
        raise PermissionDenied(f"没有写入控制器文件的权限：{path}") from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def atomic_write_new_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    if path.exists():
        raise ControllerError(f"拒绝覆盖已有 Agent 审核文件：{path}")
    atomic_write_json(path, payload, mode=mode)


def tree_digest(directory: Path) -> str:
    """Hash a release tree, rejecting links and special files along the way."""
    if not directory.is_dir() or directory.is_symlink():
        raise ControllerError(f"发布目录不合法：{directory}")
    digest = hashlib.sha256()
    files: list[Path] = []
    for item in directory.rglob("*"):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ControllerError(f"发布目录不能包含软链接：{item}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ControllerError(f"发布目录包含不支持的对象：{item}")
        files.append(item)
    for item in sorted(files, key=lambda entry: entry.relative_to(directory).as_posix()):
        relative = item.relative_to(directory).as_posix().encode("utf-8")
        digest.update(relative + b"\0")
        digest.update(str(stat.S_IMODE(item.stat().st_mode)).encode("ascii") + b"\0")
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def source_version(source_root: Path) -> str:
    version_path = source_root / "VERSION"
    require_regular_file(version_path, label="VERSION")
    version = version_path.read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        raise IncompatibleSource("VERSION 不是可安全安装的版本号")
    return version


def paths_overlap(first: Path, second: Path) -> bool:
    first = first.resolve(strict=False)
    second = second.resolve(strict=False)
    return first == second or first in second.parents or second in first.parents


def reject_source_overlap(source_root: Path, install_root: Path, state_override: Optional[str]) -> None:
    source_root = source_root.resolve(strict=False)
    install_root = path_from_argument(install_root)
    if paths_overlap(source_root, install_root):
        raise ControllerError("安装根不能是源码目录或其子目录")
    if state_override is not None and paths_overlap(source_root, path_from_argument(state_override)):
        raise ControllerError("状态目录不能位于源码目录内")


def validate_backend_source(backend: Path) -> None:
    require_regular_file(backend, label="恢复后端")
    try:
        source = backend.read_text(encoding="utf-8")
        ast.parse(source, filename=str(backend))
        # Unlike `python -m py_compile`, compile() never writes a __pycache__
        # entry beside an installed application or into a user cache.
        compile(source, str(backend), "exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise IncompatibleSource("恢复后端无法作为 Python 源码加载") from exc
    required = ("agent-export", "agent-apply", "agent-status")
    absent = [command for command in required if command not in source]
    if absent:
        raise IncompatibleSource("恢复后端缺少 Agent 接入命令：" + ", ".join(absent))


def validate_install_guides(source_root: Path) -> None:
    require_regular_file(source_root / INSTALL_GUIDE, label="Agent 安装指引")
    require_regular_file(source_root / PROTOCOL_GUIDE, label="Agent 协议指引")


def build_prerequisites() -> dict[str, str]:
    system = platform.system()
    version_text = platform.mac_ver()[0]
    try:
        version_parts = tuple(int(part) for part in version_text.split(".")[:2])
    except ValueError:
        version_parts = ()
    if system != "Darwin" or not version_parts or version_parts < (14,):
        raise ControllerError("安装构建需要 macOS 14 或更高版本")
    require_system_python()
    xcrun = shutil.which("xcrun")
    if not xcrun:
        raise ControllerError("缺少 xcrun；请安装 Apple Command Line Tools")
    result = subprocess.run([xcrun, "--find", "swiftc"], text=True, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise ControllerError("xcrun 找不到 swiftc；请安装 Apple Command Line Tools")
    return {"swiftc": result.stdout.strip()}


def copy_source_snapshot(source_root: Path, destination: Path) -> None:
    require_regular_file(source_root / "build.sh", label="构建脚本")
    validate_backend_source(source_root / "Recovery" / "recovery.py")
    validate_install_guides(source_root)
    try:
        shutil.copytree(
            source_root,
            destination,
            ignore=shutil.ignore_patterns(".git", ".agents", ".codex", "dist", "__pycache__", "*.pyc", ".DS_Store"),
        )
    except PermissionError as exc:
        raise PermissionDenied("没有读取源码以创建临时构建快照的权限") from exc


def run_build(build_root: Path) -> None:
    result = subprocess.run(
        ["zsh", str(build_root / "build.sh")],
        cwd=build_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = sanitise_error(result.stderr or result.stdout)
        raise ControllerError(f"构建经典浮窗失败：{detail or '构建脚本返回非零'}")


@dataclass(frozen=True)
class Installation:
    root: Path
    release: Path
    version: str
    app: Path
    backend: Path
    config: dict[str, Any]
    receipt: dict[str, Any]


def installation_root(value: Optional[Union[str, Path]]) -> Path:
    return ensure_directory(path_from_argument(value or DEFAULT_INSTALL_ROOT), mode=0o700)


def existing_installation_root(value: Optional[Union[str, Path]]) -> Path:
    root = path_from_argument(value or DEFAULT_INSTALL_ROOT)
    assert_no_symlink_components(root)
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise ControllerError("尚未安装 ResumeDesk；请先运行 install")
    return root


def default_state_dir(root: Path) -> Path:
    return safe_child(root, "Recovery")


def valid_version(value: Any) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ControllerError("安装配置中的版本号不合法")
    return value


def load_existing_config(root: Path) -> Optional[dict[str, Any]]:
    """The one install receipt is also the atomic active-version configuration."""
    path = safe_child(root, RECEIPT_NAME)
    if not path.exists():
        return None
    config = read_json(path, label="安装回执")
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ControllerError("安装回执版本不兼容")
    if "state_dir" in config:
        value = config["state_dir"]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ControllerError("安装回执中的状态目录不合法")
    if "active_version" in config:
        valid_version(config["active_version"])
    return config


def load_installation(root: Path) -> Installation:
    config = load_existing_config(root)
    if not config or "active_version" not in config:
        raise ControllerError("尚未安装 ResumeDesk；请先运行 install")
    receipt = config
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise ControllerError("安装回执版本不兼容")
    version = valid_version(config["active_version"])
    if receipt.get("release_version") != version:
        raise ControllerError("安装回执中的活动版本不一致")
    releases = ensure_directory(safe_child(root, RELEASES_NAME), mode=0o700)
    release = safe_child(releases, version)
    if not release.is_dir() or release.is_symlink():
        raise ControllerError("当前版本的安装目录不存在或不安全")
    expected_hash = receipt.get("release_hash")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ControllerError("安装回执缺少发布哈希")
    if tree_digest(release) != expected_hash:
        raise ControllerError("当前安装内容与安装回执不匹配；拒绝继续使用")
    app = safe_child(release, APP_NAME)
    if not app.is_dir() or app.is_symlink():
        raise ControllerError("当前安装缺少经典浮窗 App")
    backend = app / APP_BACKEND
    validate_backend_source(backend)
    require_regular_file(safe_child(release, "agent.py"), label="已安装控制器")
    require_regular_file(safe_child(release, "USAGE.txt"), label="已安装使用指引")
    require_regular_file(safe_child(release, INSTALL_GUIDE), label="已安装 Agent 指引")
    require_regular_file(safe_child(safe_child(release, "docs"), "AGENT_PROTOCOL.md"), label="已安装 Agent 协议")
    return Installation(root=root, release=release, version=version, app=app, backend=backend,
                        config=config, receipt=receipt)


@contextmanager
def installation_lock(root: Path) -> Iterator[None]:
    """Serialise install/receipt changes without using an external service."""
    import fcntl

    lock_path = safe_child(root, ".agent-install.lock")
    if lock_path.exists():
        require_regular_file(lock_path, label="安装锁")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except PermissionError as exc:
        raise PermissionDenied("没有创建安装锁的权限") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


USAGE_TEXT = """ResumeDesk classic floating-panel Agent controller

This installed copy operates only this release's 断点复原浮窗.app and its embedded
Recovery/recovery.py.  It does not install a login item, change security
settings, sign an app, or contact a model/API.

Use prepare --allow-read to create a private review packet.  An Agent must fill
the matching response template from actual evidence, then apply --file ...
--allow-read.  A template starts with every decision set to unknown and is not
evidence that the panel is ready.  Run verify before launch.

For the complete installed workflow and response contract, read INSTALL_AGENT.md
and docs/AGENT_PROTOCOL.md beside this controller.  They remain available after
the source checkout has been moved or removed.
"""


def build_release_stage(source_root: Path, stage_parent: Path) -> tuple[Path, str, str]:
    """Build in a disposable copy; return (staged release, version, digest)."""
    version = source_version(source_root)
    temporary = Path(tempfile.mkdtemp(prefix=".agent-build-", dir=stage_parent))
    try:
        snapshot = temporary / "source"
        copy_source_snapshot(source_root, snapshot)
        run_build(snapshot)
        app_source = snapshot / "dist" / APP_NAME
        if not app_source.is_dir() or app_source.is_symlink():
            raise ControllerError("构建没有产出唯一的经典浮窗 App")
        validate_backend_source(app_source / APP_BACKEND)
        staged_release = temporary / "release"
        staged_release.mkdir(mode=0o700)
        shutil.copytree(app_source, staged_release / APP_NAME)
        shutil.copy2(Path(__file__).resolve(), staged_release / "agent.py")
        os.chmod(staged_release / "agent.py", 0o755)
        shutil.copy2(snapshot / INSTALL_GUIDE, staged_release / INSTALL_GUIDE)
        installed_docs = staged_release / "docs"
        installed_docs.mkdir(mode=0o700)
        shutil.copy2(snapshot / PROTOCOL_GUIDE, installed_docs / "AGENT_PROTOCOL.md")
        usage = staged_release / "USAGE.txt"
        usage.write_text(USAGE_TEXT, encoding="utf-8")
        os.chmod(usage, 0o600)
        return staged_release, version, tree_digest(staged_release)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def configured_state_dir(root: Path, config: Optional[Mapping[str, Any]], override: Optional[str]) -> Path:
    if override is not None:
        state = path_from_argument(override)
    elif config and isinstance(config.get("state_dir"), str):
        state = path_from_argument(config["state_dir"])
    else:
        state = default_state_dir(root)
    return ensure_directory(state, mode=0o700)


def install_release(source_root: Path, root: Path, state_override: Optional[str]) -> dict[str, Any]:
    """Install one immutable release while leaving all old releases and state intact."""
    build_prerequisites()
    # Refuse an old source tree before creating any install-root files.
    reject_source_overlap(source_root, root, state_override)
    source_version(source_root)
    validate_backend_source(source_root / "Recovery" / "recovery.py")
    validate_install_guides(source_root)
    root = installation_root(root)
    existing_config = load_existing_config(root)
    state = configured_state_dir(root, existing_config, state_override)
    releases = ensure_directory(safe_child(root, RELEASES_NAME), mode=0o700)
    with installation_lock(root):
        staged_release, version, release_hash = build_release_stage(source_root, root)
        target = safe_child(releases, version)
        try:
            if target.exists():
                if not target.is_dir() or target.is_symlink():
                    raise VersionConflict("同版本安装位置不是安全目录")
                existing_hash = tree_digest(target)
                if existing_hash != release_hash:
                    raise VersionConflict("同版本已有不同内容；拒绝覆盖已安装版本")
                shutil.rmtree(staged_release.parent, ignore_errors=True)
                installed = False
            else:
                os.replace(staged_release, target)
                # The source snapshot is still beside the moved release.  It
                # is temporary build output only and must not remain under the
                # user installation root after a successful install.
                shutil.rmtree(staged_release.parent, ignore_errors=True)
                installed = True
            # This single document is both the receipt and the active-version
            # pointer.  A failed write therefore leaves the prior install
            # entirely usable instead of splitting config from receipt.
            receipt = {
                "schema_version": SCHEMA_VERSION,
                "active_version": version,
                "release_version": version,
                "release_hash": release_hash,
                "state_dir": str(state),
                "installed_at": utc_now(),
                "updated_at": utc_now(),
                "app": f"{RELEASES_NAME}/{version}/{APP_NAME}",
                "controller": f"{RELEASES_NAME}/{version}/agent.py",
            }
            atomic_write_json(safe_child(root, RECEIPT_NAME), receipt)
        finally:
            if staged_release.parent.exists():
                shutil.rmtree(staged_release.parent, ignore_errors=True)
    return {
        "ok": True,
        "command": "install",
        "installed": installed,
        "version": version,
        "install_root": str(root),
        "release": str(target),
        "installed_helper": str(safe_child(target, "agent.py")),
        "installed_app": str(safe_child(target, APP_NAME)),
        "installed_guide": str(safe_child(target, INSTALL_GUIDE)),
        "installed_protocol": str(target / PROTOCOL_GUIDE),
        "state_dir": str(state),
        "next_step": "运行 prepare --allow-read；模板中的 unknown 不能视为已就绪。",
    }


def run_backend(installation: Installation, state_dir: Path, command: str,
                arguments: Optional[list[str]] = None) -> dict[str, Any]:
    require_system_python()
    args = [str(SYSTEM_PYTHON), "-B", str(installation.backend), "--state-dir", str(state_dir), command]
    args.extend(arguments or [])
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = sanitise_error(result.stderr or result.stdout)
        raise ControllerError(f"恢复后端 {command} 失败：{detail or '返回非零'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ControllerError(f"恢复后端 {command} 没有返回合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ControllerError(f"恢复后端 {command} 返回格式不合法")
    return payload


def codex_home(value: Optional[str]) -> Path:
    return path_from_argument(value or DEFAULT_CODEX_HOME)


def check_codex_database(home: Path) -> dict[str, Any]:
    database = home / "state_5.sqlite"
    try:
        require_regular_file(database, label="Codex 索引数据库")
        if not os.access(database, os.R_OK):
            return {"ok": False, "reason": "permission_denied"}
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            connection.execute("PRAGMA query_only=ON")
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='threads'"
            ).fetchone()
            if not table:
                return {"ok": False, "reason": "schema_missing_threads"}
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
        finally:
            connection.close()
    except PermissionDenied:
        return {"ok": False, "reason": "permission_denied"}
    except ControllerError:
        return {"ok": False, "reason": "missing_index_database"}
    except sqlite3.Error:
        return {"ok": False, "reason": "schema_unreadable"}
    missing = sorted(REQUIRED_CODEX_COLUMNS - columns)
    if missing:
        return {"ok": False, "reason": "schema_missing_columns", "missing_columns": missing}
    return {"ok": True, "schema": "threads"}


def doctor(codex_home_path: Path) -> dict[str, Any]:
    mac_version = platform.mac_ver()[0]
    try:
        version = tuple(int(part) for part in mac_version.split(".")[:2])
    except ValueError:
        version = ()
    mac_ok = platform.system() == "Darwin" and bool(version) and version >= (14,)
    python_ok = SYSTEM_PYTHON.is_file() and os.access(SYSTEM_PYTHON, os.X_OK)
    xcrun = shutil.which("xcrun")
    swiftc_ok = False
    if xcrun:
        probe = subprocess.run([xcrun, "--find", "swiftc"], text=True, capture_output=True, check=False)
        swiftc_ok = probe.returncode == 0 and bool(probe.stdout.strip())
    codex = check_codex_database(codex_home_path)
    desktop_candidates = [
        Path("/Applications/Codex.app"),
        Path.home() / "Applications" / "Codex.app",
    ]
    desktop_present = any(path.is_dir() and not path.is_symlink() for path in desktop_candidates)
    checks = {
        "macos_14": {"ok": mac_ok, "version": mac_version or None},
        "system_python": {"ok": python_ok, "path": str(SYSTEM_PYTHON)},
        "swiftc": {"ok": swiftc_ok},
        "codex_desktop_index": codex,
    }
    missing = [name for name, check in checks.items() if not check["ok"]]
    warnings: list[str] = []
    if codex.get("ok") and not desktop_present:
        warnings.append("可读取 Codex 本地索引，但未在常见位置发现 Codex.app；这不满足浮窗显示的桌面 App 生命周期条件。")
    return {
        "ok": not missing,
        "command": "doctor",
        "checks": checks,
        "missing": missing,
        "warnings": warnings,
        "note": "只检查 Codex 本地索引存在和 schema；不会读取聊天内容。Codex CLI 可读记录不代表 Codex 桌面 App 正在运行。",
    }


def agent_review_directory(state_dir: Path) -> Path:
    return ensure_directory(safe_child(state_dir, REVIEW_DIR_NAME), mode=0o700)


def token_file_stem(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise ControllerError("后端没有返回合法 batch_token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def prepare(installation: Installation, state_dir: Path, home: Path, allow_read: bool,
            excludes: list[str]) -> dict[str, Any]:
    if not allow_read:
        raise PermissionDenied("prepare 需要明确的 --allow-read；未读取 Codex 数据")
    args = ["--home", str(home), "--allow-read"]
    for item in excludes:
        args.extend(["--exclude-id", item])
    packet = run_backend(installation, state_dir, "agent-export", args)
    if packet.get("schema_version") != SCHEMA_VERSION or not isinstance(packet.get("coverage"), dict):
        raise ControllerError("agent-export 返回的 schema 或 coverage 不兼容")
    items = packet.get("items")
    if not isinstance(items, list):
        raise ControllerError("agent-export 返回的 items 不合法")
    token = packet.get("batch_token")
    stem = token_file_stem(token)
    template_decisions = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("fingerprint"), str):
            raise ControllerError("agent-export 项目缺少 id 或 fingerprint")
        template_decisions.append({
            "id": item["id"],
            "fingerprint": item["fingerprint"],
            "decision": "unknown",
            "reason": "尚未核对；不能据此宣称已就绪。",
            "evidence_quote": "",
        })
    review_dir = agent_review_directory(state_dir)
    packet_path = safe_child(review_dir, f"packet-{stem}.json")
    template_path = safe_child(review_dir, f"response-{stem}.json")
    template = {"schema_version": SCHEMA_VERSION, "batch_token": token, "decisions": template_decisions}
    atomic_write_new_json(packet_path, packet)
    try:
        atomic_write_new_json(template_path, template)
    except Exception:
        # The packet remains protected evidence.  Do not delete it on a template
        # error, and never replace an existing artifact.
        raise
    return {
        "ok": True,
        "command": "prepare",
        "packet": str(packet_path),
        "response_template": str(template_path),
        "packet_path": str(packet_path),
        "response_path": str(template_path),
        "batch_token": token,
        "coverage": packet["coverage"],
        "item_count": len(items),
        "next_step": "逐项根据原始证据填写模板，再运行 apply --file <模板路径> --allow-read。",
    }


def apply_review(installation: Installation, state_dir: Path, response_file: Path, allow_read: bool) -> dict[str, Any]:
    if not allow_read:
        raise PermissionDenied("apply 需要明确的 --allow-read；未读取或写入审核结果")
    response_file = path_from_argument(response_file)
    require_regular_file(response_file, label="Agent 审核响应")
    if response_file.stat().st_size > MAX_JSON_BYTES:
        raise ControllerError("Agent 审核响应过大")
    result = run_backend(installation, state_dir, "agent-apply", [str(response_file), "--allow-read"])
    if result.get("schema_version") != SCHEMA_VERSION or not isinstance(result.get("applied"), dict):
        raise ControllerError("agent-apply 返回格式不兼容")
    return {
        "ok": True,
        "command": "apply",
        "batch_token": result.get("batch_token"),
        "applied": result["applied"],
        "coverage": result.get("coverage"),
        "panel_count": result.get("panel_count"),
        "status": result.get("status"),
        "next_step": "运行 verify；apply 成功本身不等于浮窗已显示或已就绪。",
    }


def count_panel_items(panel: Mapping[str, Any]) -> int:
    projects = panel.get("projects")
    if not isinstance(projects, list):
        raise ControllerError("panel-read 返回的 projects 不合法")
    total = 0
    for project in projects:
        if not isinstance(project, dict) or not isinstance(project.get("items"), list):
            raise ControllerError("panel-read 返回的项目不合法")
        total += len(project["items"])
    return total


def verify_installation(root: Path, state_override: Optional[str]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    try:
        installation = load_installation(root)
        state_dir = configured_state_dir(root, installation.config, state_override)
    except ControllerError as exc:
        return {
            "ok": False, "ready": False, "command": "verify",
            "errors": [{"code": exc.code, "message": sanitise_error(exc)}],
        }

    source_ok = True
    try:
        validate_backend_source(installation.backend)
    except ControllerError as exc:
        source_ok = False
        errors.append({"code": exc.code, "message": sanitise_error(exc)})

    status: Optional[dict[str, Any]] = None
    panel: Optional[dict[str, Any]] = None
    if source_ok:
        try:
            status = run_backend(installation, state_dir, "agent-status")
            if status.get("schema_version") != SCHEMA_VERSION:
                raise ControllerError("agent-status schema 不兼容")
            panel = run_backend(installation, state_dir, "panel-read")
            if panel.get("schema_version") != SCHEMA_VERSION or not isinstance(panel.get("revision"), str):
                raise ControllerError("panel-read schema 不兼容")
            panel_items = count_panel_items(panel)
            if isinstance(status.get("panel_count"), int) and status["panel_count"] != panel_items:
                raise ControllerError("agent-status 与 panel-read 的项目数不一致")
        except ControllerError as exc:
            errors.append({"code": exc.code, "message": sanitise_error(exc)})

    ready = False
    partial_eligible = False
    safe_status: Optional[dict[str, Any]] = None
    safe_panel: Optional[dict[str, Any]] = None
    source_note: Optional[str] = None
    partial_note: Optional[str] = None
    if status is not None and panel is not None:
        last_review = status.get("last_review")
        stage = status.get("stage")
        coverage = status.get("coverage")
        ready = (
            stage == "ready"
            and isinstance(coverage, dict)
            and isinstance(last_review, dict)
            and bool(last_review.get("batch_token"))
            and last_review.get("coverage_complete") is True
            and last_review.get("unknown_count") == 0
        )
        safe_status = {key: status.get(key) for key in (
            "stage", "coverage", "reviewed_count", "panel_count", "last_review", "next_step", "notice",
            "has_pending_items", "source_home", "source_kind",
        )}
        safe_panel = {"revision": panel.get("revision"), "project_count": len(panel.get("projects", [])),
                      "item_count": count_panel_items(panel)}
        if not ready:
            errors.append({"code": "not_ready", "message": "尚无最近一次完整的真实审核；安装或模板 unknown 都不能视为 ready。"})
        source_kind = status.get("source_kind")
        source_home = status.get("source_home")
        last_source_home = last_review.get("source_home") if isinstance(last_review, dict) else None
        last_source_kind = last_review.get("source_kind") if isinstance(last_review, dict) else None
        source_valid = (
            source_kind in ("default_codex", "custom_codex")
            and isinstance(source_home, str) and Path(source_home).is_absolute()
            and source_home == last_source_home and source_kind == last_source_kind
        )
        completed_batch = (
            isinstance(last_review, dict)
            and isinstance(last_review.get("batch_token"), str) and bool(last_review["batch_token"])
            and isinstance(last_review.get("applied_at"), str) and bool(last_review["applied_at"])
            and isinstance(last_review.get("reviewed_count"), int)
            and isinstance(last_review.get("unknown_count"), int)
            and isinstance(last_review.get("coverage_complete"), bool)
        )
        if stage in ("ready", "needs_attention"):
            if (source_kind not in ("default_codex", "custom_codex")
                    or not source_valid):
                ready = False
                errors.append({"code": "source_provenance_missing",
                               "message": "已审核状态的来源标记必须是绝对路径，并与最近审核一致。"})
            elif source_kind == "custom_codex":
                source_note = "本次核对使用明确指定的自定义 Codex 来源；不能据此宣称已覆盖默认个人 Codex 历史。"
            else:
                source_note = "本次核对来源标记为默认 Codex 目录；仍以 coverage 的实际范围为准。"
        else:
            source_note = "尚未形成可启动浮窗的完整审核来源；当前状态不会宣称已覆盖任何个人历史。"

        partial_eligible = (
            stage == "needs_attention"
            and isinstance(coverage, dict)
            and completed_batch
            and source_valid
            and safe_panel["item_count"] > 0
            and all(error["code"] == "not_ready" for error in errors)
        )
        if partial_eligible:
            unknown_count = last_review["unknown_count"]
            gaps = coverage.get("gaps")
            gap_count = len(gaps) if isinstance(gaps, list) else None
            partial_note = (
                "已应用当前批次，但仍有 " + str(unknown_count) + " 条 unknown"
                + (" 和 " + str(gap_count) + " 个 coverage 缺口" if gap_count is not None else "")
                + "；仅可显示已核实的浮窗条目，不代表完整接入。"
            )

    # A transport/schema/panel mismatch must never leave a true ready flag for
    # command_result or launch to trust.
    ready = ready and not errors

    return {
        "ok": not errors,
        "ready": ready,
        "partial_eligible": partial_eligible,
        "command": "verify",
        "version": installation.version,
        "state_dir": str(state_dir),
        "source_schema": {"ok": source_ok, "required_commands": ["agent-export", "agent-apply", "agent-status"]},
        "status": safe_status,
        "panel": safe_panel,
        "source": ({"source_home": status.get("source_home"), "source_kind": status.get("source_kind")}
                   if status is not None else None),
        "source_note": source_note,
        "partial_note": partial_note,
        "errors": errors,
    }


def diagnostics_path_for(state_dir: Path, requested: Optional[str]) -> Path:
    directory = agent_review_directory(state_dir)
    if requested is not None:
        candidate = contained_path(state_dir, path_from_argument(requested))
        if candidate.parent != directory and directory not in candidate.parents:
            raise ControllerError("diagnostics 路径必须位于状态目录的 AgentReview 内")
    else:
        candidate = safe_child(directory, f"launch-{uuid.uuid4().hex}.json")
    if candidate.exists():
        raise ControllerError("拒绝覆盖已有 diagnostics 文件")
    return candidate


def run_open(arguments: list[str]) -> None:
    result = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ControllerError("无法提交启动请求：" + sanitise_error(result.stderr or result.stdout))


def observed_diagnostics(path: Path, expected_revision: str, expected_version: str,
                         wait_seconds: float) -> Tuple[bool, str, Optional[int]]:
    deadline = time.monotonic() + wait_seconds
    last_reason = "尚未收到浮窗 diagnostics；可能仍在等待 Codex 桌面 App 或图形会话。"
    process_id: Optional[int] = None
    while True:
        if path.exists():
            try:
                data = read_json(path, label="浮窗 diagnostics")
                if isinstance(data.get("process_id"), int):
                    process_id = data["process_id"]
                valid = (
                    data.get("test_mode") is False
                    and data.get("backend_loaded") is True
                    and data.get("visible") is True
                    and data.get("backend_revision") == expected_revision
                    and data.get("app_version") == expected_version
                )
                if valid:
                    return True, "已收到与当前 panel-read 一致的浮窗显示 diagnostics。", process_id
                last_reason = "收到 diagnostics，但尚未证明当前浮窗已显示并加载同一后端版本。"
            except ControllerError:
                last_reason = "收到的 diagnostics 不合法，未能证明浮窗已显示。"
        if time.monotonic() >= deadline:
            return False, last_reason, process_id
        time.sleep(0.1)


def launch(installation: Installation, state_dir: Path, codex_bundle_id: str,
           requested_diagnostics: Optional[str], wait_seconds: float,
           allow_partial: bool = False) -> dict[str, Any]:
    if not codex_bundle_id:
        raise UsageError("--codex-bundle-id 不能为空")
    verification = verify_installation(installation.root, str(state_dir))
    review_ready = bool(verification.get("ready"))
    partial_review = False
    if not review_ready:
        if allow_partial and verification.get("partial_eligible"):
            partial_review = True
        elif allow_partial:
            raise NotReady("--allow-partial 仅允许已应用当前批次、来源标记有效且仍有可显示项目的 needs_attention 状态")
        else:
            raise NotReady("launch 需要 verify ready；安装成功或 open 返回成功都不能替代真实审核")
    panel = verification.get("panel") or {}
    revision = panel.get("revision")
    if not isinstance(revision, str):
        raise NotReady("verify 没有可用的 panel revision")
    diagnostics = diagnostics_path_for(state_dir, requested_diagnostics)
    command = [
        "/usr/bin/open", "-n", str(installation.app), "--args",
        "--state-dir", str(state_dir),
        "--codex-bundle-id", codex_bundle_id,
        "--diagnostics-path", str(diagnostics),
    ]
    run_open(command)
    display_verified, display_note, process_id = observed_diagnostics(
        diagnostics, revision, installation.version, wait_seconds,
    )
    return {
        "ok": True,
        "command": "launch",
        "launch_requested": True,
        "bundle": str(installation.app),
        "state_dir": str(state_dir),
        "codex_bundle_id": codex_bundle_id,
        "diagnostics_path": str(diagnostics),
        "display_verified": display_verified,
        "display_note": display_note,
        "process_id": process_id,
        "source": verification.get("source"),
        "review_ready": review_ready,
        "partial_review": partial_review,
        "coverage": (verification.get("status") or {}).get("coverage"),
        "partial_note": verification.get("partial_note") if partial_review else None,
        "note": "open 成功只代表已提交启动请求；安装和实际界面显示是两份独立证据。",
    }


def add_common_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--install-root")
    command.add_argument("--state-dir")
    command.add_argument("--codex-home")


def build_parser() -> JSONArgumentParser:
    parser = JSONArgumentParser(prog="agent.py", description="ResumeDesk classic panel Agent controller")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=JSONArgumentParser)
    for name in ("doctor", "install", "prepare", "apply", "verify", "launch"):
        command = sub.add_parser(name)
        add_common_arguments(command)
        if name == "prepare":
            command.add_argument("--allow-read", action="store_true")
            command.add_argument("--exclude-id", action="append", default=[])
        elif name == "apply":
            command.add_argument("--file", required=True)
            command.add_argument("--allow-read", action="store_true")
        elif name == "launch":
            command.add_argument("--codex-bundle-id", default="com.openai.codex")
            command.add_argument("--diagnostics-path")
            command.add_argument("--diagnostics-wait-seconds", type=float, default=4.0)
            command.add_argument("--allow-partial", action="store_true")
    return parser


def command_result(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "doctor":
        result = doctor(codex_home(args.codex_home))
        return result, 0 if result["ok"] else 2
    if args.command == "install":
        return install_release(repo_root(), path_from_argument(args.install_root or DEFAULT_INSTALL_ROOT), args.state_dir), 0
    root = existing_installation_root(args.install_root)
    if args.command == "verify":
        result = verify_installation(root, args.state_dir)
        return result, 0 if result["ready"] else 2
    installation = load_installation(root)
    state_dir = configured_state_dir(root, installation.config, args.state_dir)
    if args.command == "prepare":
        return prepare(installation, state_dir, codex_home(args.codex_home), args.allow_read, args.exclude_id), 0
    if args.command == "apply":
        return apply_review(installation, state_dir, Path(args.file), args.allow_read), 0
    if args.command == "launch":
        if not 0 <= args.diagnostics_wait_seconds <= 10:
            raise UsageError("--diagnostics-wait-seconds 必须在 0 到 10 之间")
        return launch(installation, state_dir, args.codex_bundle_id, args.diagnostics_path,
                      args.diagnostics_wait_seconds, args.allow_partial), 0
    raise UsageError("未知命令")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    command = "unknown"
    try:
        args = parser.parse_args(argv)
        command = args.command
        result, status = command_result(args)
        json_output(result)
        return status
    except ControllerError as exc:
        json_output({"ok": False, "command": command, "error": {"code": exc.code, "message": sanitise_error(exc)}})
        return exc.exit_code
    except Exception as exc:  # keep unexpected failures structured and bounded
        json_output({"ok": False, "command": command,
                     "error": {"code": "internal_error", "message": sanitise_error(exc)}})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Engine settings and engine-owned state persistence.

Two separate files live under the engine data dir (default ``~/PodcastReader``,
override via ``PODCAST_READER_DATA_DIR``):

- ``engine-state.json`` (mode 0600) — engine-owned ``{port, token}``; never
  exposed for writing via the API.
- ``settings.json`` — user settings, readable and writable via
  ``GET/PUT /v1/settings``.

All writes are atomic (temp file + ``os.replace``) under a module-level lock.

On Windows, where POSIX modes do not protect file contents, secret-bearing
writes install and verify a protected DACL granting access only to the file
owner and LocalSystem.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import sys
import threading
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from podcast_reader.providers import canonicalize_custom_providers
from podcast_reader.types import EngineSettings

if TYPE_CHECKING:
    from collections.abc import Callable

if sys.platform == "win32":  # pragma: no cover - imported only on Windows
    import msvcrt
else:
    msvcrt = None

logger = logging.getLogger(__name__)

ENGINE_STATE_FILE = "engine-state.json"
SETTINGS_FILE = "settings.json"

#: The chapter_model literal Phase 1 persisted explicitly (Anthropic was the
#: only provider then). Kept as a historical constant — it must keep matching
#: old files even if the anthropic registry default changes later.
_PHASE1_CHAPTER_MODEL = "claude-haiku-4-5-20251001"

_WRITE_LOCK = threading.Lock()


class EngineState(TypedDict):
    """Engine-owned state: the bound port and the API bearer token."""

    port: int
    token: str


def data_dir_path() -> Path:
    """Resolve the engine data directory path without creating it.

    For read-only consumers (the whisper worker's DLL-path prep, ytdlp's
    managed-copy residence gate) that must not leave a ``~/PodcastReader``
    behind as a side effect. Default is ``~/PodcastReader``;
    ``PODCAST_READER_DATA_DIR`` overrides.
    """
    env = os.environ.get("PODCAST_READER_DATA_DIR")
    return Path(env).expanduser() if env else Path.home() / "PodcastReader"


def data_dir() -> Path:
    """Resolve (and create) the engine data directory."""
    base = data_dir_path()
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_engine_state(base: Path) -> EngineState:
    """Load engine state, creating it (port 0, fresh token, mode 0600) on first run.

    A pre-existing file with permissive mode bits is re-hardened to 0600 on
    load (POSIX only): the file holds the bearer token, so a one-time loose
    copy (restored backup, manual edit) must not stay world-readable forever.
    """
    path = base / ENGINE_STATE_FILE
    if path.exists():
        _ensure_owner_only(path)
        return cast("EngineState", json.loads(path.read_text()))
    state = EngineState(port=0, token=secrets.token_urlsafe(32))
    save_engine_state(base, state)
    return state


def save_engine_state(base: Path, state: EngineState) -> None:
    """Persist engine state atomically with owner-only permissions."""
    atomic_write_json(base / ENGINE_STATE_FILE, state, mode=0o600)


def load_settings(base: Path) -> EngineSettings:
    """Load user settings, falling back to defaults without persisting them.

    Loaded values are merged over :func:`default_settings`, so a settings file
    persisted by an earlier version (lacking newer fields) upgrades cleanly —
    no job may fail because the file predates a release.

    A Phase 1 file (no ``chapter_provider``) carrying exactly the Phase 1
    default ``chapter_model`` has its model normalized to ``""`` ("provider
    default") during the merge: that value was installed by us, not chosen by
    the user, and a later provider switch must not send an Anthropic model id
    to another provider. Any other persisted model is a deliberate user
    choice and is preserved verbatim.

    A malformed settings file is quarantined to ``settings.json.corrupt``
    (with a warning, mirroring the job-journal handling) and defaults are
    returned — bad settings must never prevent the engine from serving.
    """
    path = base / SETTINGS_FILE
    if not path.exists():
        return default_settings(base)
    try:
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise TypeError("settings must be a JSON object")
    except (OSError, ValueError, TypeError) as exc:
        _quarantine(path, exc)
        return default_settings(base)
    try:
        if (
            "chapter_provider" not in loaded
            and loaded.get("chapter_model") == _PHASE1_CHAPTER_MODEL
        ):
            loaded = {**loaded, "chapter_model": ""}
        merged = {**default_settings(base), **loaded}
        merged["custom_providers"] = canonicalize_custom_providers(merged["custom_providers"])
    except (KeyError, TypeError, ValueError) as exc:
        _quarantine(path, exc)
        return default_settings(base)
    return cast("EngineSettings", merged)


def save_settings(base: Path, settings: EngineSettings) -> None:
    """Persist user settings atomically."""
    canonical = dict(settings)
    canonical["custom_providers"] = canonicalize_custom_providers(
        canonical.get("custom_providers", [])
    )
    atomic_write_json(base / SETTINGS_FILE, canonical)


def default_settings(base: Path) -> EngineSettings:
    """Default settings, mirroring the CLI's environment-variable defaults."""
    return EngineSettings(
        whisper_model="large-v3",
        whisper_lang="en",
        whisper_device="cuda",
        sentences=5,
        library_dir=str(base / "library"),
        chapter_model="",  # "" means: the chapter provider's default model
        chapter_provider="anthropic",
        custom_provider_url="",
        custom_providers=[],
        diarize=False,
        caption_cleanup=False,
        media_cache_max_bytes=5 * 1024**3,  # 5 GiB LRU cap for the lazy media cache
    )


def token_fingerprint(token: str) -> str:
    """Non-reversible token identifier safe to publish in the discovery file."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def engine_version() -> str:
    """Installed package version, or a dev placeholder when not installed."""
    try:
        return metadata.version("podcast-reader")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


def atomic_write_json(path: Path, payload: object, *, mode: int | None = None) -> None:
    """Write *payload* as JSON via :func:`atomic_write_text`."""
    atomic_write_text(path, json.dumps(payload, indent=2), mode=mode)


def atomic_write_text(path: Path, data: str, *, mode: int | None = None) -> None:
    """Write *data* via temp file + ``os.replace`` under the module lock.

    With *mode*, the temp file is created with that mode applied at ``os.open``
    time (``O_CREAT | O_EXCL``), so the payload is never readable by other
    users, even transiently. Public so secret-bearing writers outside this
    module (the cookie-jar store) share one secure-write implementation.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _WRITE_LOCK:
        try:
            if mode is None:
                tmp.write_text(data)
            else:
                _secure_write_text(tmp, data, mode)
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if mode is not None and sys.platform == "win32":  # pragma: no cover - Windows CI
            try:
                verify_windows_private_file(path)
            except Exception:
                # Never leave a secret at a destination whose final ACL could
                # not be proved after replacement.
                path.unlink(missing_ok=True)
                raise


def ensure_owner_only_dir(path: Path) -> None:
    """Re-harden an existing secret-bearing directory to 0700 (POSIX only).

    ``mkdir(mode=0o700, exist_ok=True)`` applies the mode only on creation —
    a directory that already existed with loose permissions keeps them. Public
    for the same reason as :func:`atomic_write_text`: secret-bearing writers
    outside this module (the cookie-jar store) share one hardening
    implementation. No-op on Windows (see module docstring — ACLs carry the
    protection there).
    """
    if os.name != "posix":  # pragma: no cover — exercised on Windows only
        return
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        path.chmod(0o700)


def _ensure_owner_only(path: Path) -> None:
    """Re-harden an existing secret file on the current platform."""
    if sys.platform == "win32":  # pragma: no cover - Windows CI/frozen smoke
        ensure_windows_private_file(path)
        return
    if os.name != "posix":
        return
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.chmod(0o600)


def _quarantine(path: Path, exc: Exception) -> None:
    """Move a corrupt file aside as ``<name>.corrupt``, logging either way."""
    corrupt = path.with_name(path.name + ".corrupt")
    try:
        path.replace(corrupt)
    except OSError as rename_exc:
        logger.warning(
            "%s unreadable (%s); quarantine rename failed (%s); using defaults",
            path.name,
            exc,
            rename_exc,
        )
        return
    logger.warning(
        "%s unreadable; quarantined to %s and using defaults: %s", path.name, corrupt, exc
    )


def _secure_write_text(path: Path, data: str, mode: int) -> None:
    """Create *path* carrying *mode* from the moment it exists, then write *data*."""
    path.unlink(missing_ok=True)  # a temp file left by a crash would trip O_EXCL
    if sys.platform == "win32":  # pragma: no cover - Windows CI/frozen smoke
        fd = _windows_create_private_fd(path)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        protected_fd = fd
        fd = -1
        _write_text_to_fd(protected_fd, data)
    finally:
        if fd >= 0:
            os.close(fd)


def _write_text_to_fd(fd: int, data: str) -> None:
    """Consume an already-protected descriptor and write the secret payload."""
    with os.fdopen(fd, "w") as fh:
        fh.write(data)


def _windows_security_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """Load the Windows ACL APIs lazily so non-Windows imports stay portable."""
    loader = ctypes.WinDLL  # type: ignore[attr-defined]
    advapi32 = loader("advapi32", use_last_error=True)
    kernel32 = loader("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return advapi32, kernel32


def _windows_error(message: str, code: int | None = None) -> OSError:
    error = ctypes.get_last_error() if code is None else code  # type: ignore[attr-defined]
    detail = ctypes.FormatError(error)  # type: ignore[attr-defined]
    return OSError(error, f"{message}: {detail}")


def _windows_current_user_sid() -> str:
    """Return the current process token's user SID in canonical string form."""
    advapi32, kernel32 = _windows_security_libraries()
    token = ctypes.c_void_p()
    open_token = advapi32.OpenProcessToken
    open_token.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    open_token.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    if not open_token(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise _windows_error("could not open the current user token")
    try:
        get_token = advapi32.GetTokenInformation
        get_token.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_token.restype = ctypes.c_int
        size = ctypes.c_uint32()
        get_token(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise _windows_error("could not size the current user SID")
        token_user = ctypes.create_string_buffer(size.value)
        if not get_token(token, 1, token_user, size.value, ctypes.byref(size)):
            raise _windows_error("could not read the current user SID")
        sid = ctypes.c_void_p.from_buffer(token_user).value
        rendered = ctypes.c_void_p()
        convert_sid = advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        convert_sid.restype = ctypes.c_int
        if not convert_sid(sid, ctypes.byref(rendered)):
            raise _windows_error("could not render the current user SID")
        try:
            return ctypes.wstring_at(rendered)
        finally:
            kernel32.LocalFree(rendered)
    finally:
        kernel32.CloseHandle(token)


def _windows_private_sddl(current_user_sid: str) -> str:
    """Describe the exact owner and DACL required for a Windows secret file."""
    return f"O:{current_user_sid}D:P(A;;FA;;;SY)(A;;FA;;;{current_user_sid})"


def _windows_sddl_principal_equals_sid(principal: str, expected_sid: str) -> bool:
    """Resolve an SDDL alias/numeric principal and compare its SID exactly."""
    advapi32, kernel32 = _windows_security_libraries()
    actual = ctypes.c_void_p()
    expected = ctypes.c_void_p()
    convert = advapi32.ConvertStringSidToSidW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    convert.restype = ctypes.c_int
    if not convert(principal, ctypes.byref(actual)):
        raise _windows_error("could not resolve a rendered DACL principal")
    try:
        if not convert(expected_sid, ctypes.byref(expected)):
            raise _windows_error("could not resolve the expected DACL principal")
        equal_sid = advapi32.EqualSid
        equal_sid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        equal_sid.restype = ctypes.c_int
        return bool(equal_sid(actual, expected))
    finally:
        if expected.value:
            kernel32.LocalFree(expected)
        kernel32.LocalFree(actual)


def _windows_dacl_principals_are_exact(
    principals: set[str], current_user_sid: str, equals_sid: Callable[[str, str], bool]
) -> bool:
    """Require one TokenUser ACE and one SYSTEM ACE after resolving aliases."""
    user_matches = sum(equals_sid(principal, current_user_sid) for principal in principals)
    system_matches = sum(equals_sid(principal, "S-1-5-18") for principal in principals)
    return len(principals) == 2 and user_matches == 1 and system_matches == 1


def _windows_private_descriptor() -> tuple[ctypes.CDLL, ctypes.CDLL, ctypes.c_void_p]:
    """Allocate the protected current-user+SYSTEM security descriptor."""
    advapi32, kernel32 = _windows_security_libraries()
    current_user_sid = _windows_current_user_sid()
    descriptor = ctypes.c_void_p()
    size = ctypes.c_uint32()
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert.restype = ctypes.c_int
    sddl = _windows_private_sddl(current_user_sid)
    if not convert(sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)):
        raise _windows_error("could not construct private engine-state DACL")
    return advapi32, kernel32, descriptor


def _windows_create_private_fd(path: Path) -> int:
    """Atomically create a no-sharing file with its protected DACL already set."""

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("security_descriptor", ctypes.c_void_p),
            ("inherit_handle", ctypes.c_int),
        ]

    assert msvcrt is not None
    _advapi32, kernel32, descriptor = _windows_private_descriptor()
    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, 0)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(SecurityAttributes),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = ctypes.c_void_p(-1).value
    try:
        handle = create_file(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise _windows_error("could not create protected engine-state temp file")
        _verify_windows_private_handle(ctypes.c_void_p(handle))
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        handle = ctypes.c_void_p(-1).value
        return fd
    finally:
        if handle != ctypes.c_void_p(-1).value:
            kernel32.CloseHandle(handle)
        kernel32.LocalFree(descriptor)


def ensure_windows_private_file(path: Path) -> None:
    """Install a protected owner+SYSTEM-only DACL on a Windows secret file."""
    if sys.platform != "win32":
        return
    advapi32, kernel32 = _windows_security_libraries()
    security_descriptor = ctypes.c_void_p()
    owner_sid = ctypes.c_void_p()
    size = ctypes.c_uint32()
    current_user_sid = _windows_current_user_sid()
    private_dacl_sddl = f"D:P(A;;FA;;;SY)(A;;FA;;;{current_user_sid})"
    convert_sid = advapi32.ConvertStringSidToSidW
    convert_sid.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    convert_sid.restype = ctypes.c_int
    if not convert_sid(current_user_sid, ctypes.byref(owner_sid)):
        raise _windows_error("could not construct the current user SID")
    try:
        convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        convert.restype = ctypes.c_int
        if not convert(private_dacl_sddl, 1, ctypes.byref(security_descriptor), ctypes.byref(size)):
            raise _windows_error("could not construct private engine-state DACL")
        try:
            dacl_present = ctypes.c_int()
            dacl_defaulted = ctypes.c_int()
            dacl = ctypes.c_void_p()
            get_dacl = advapi32.GetSecurityDescriptorDacl
            get_dacl.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_int),
            ]
            get_dacl.restype = ctypes.c_int
            if (
                not get_dacl(
                    security_descriptor,
                    ctypes.byref(dacl_present),
                    ctypes.byref(dacl),
                    ctypes.byref(dacl_defaulted),
                )
                or not dacl_present.value
            ):
                raise _windows_error("could not read constructed private engine-state DACL")
            set_security = advapi32.SetNamedSecurityInfoW
            set_security.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_int,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            set_security.restype = ctypes.c_uint32
            result = set_security(str(path), 1, 0x80000005, owner_sid, None, dacl, None)
            if result:
                raise _windows_error("could not apply private engine-state DACL", result)
        finally:
            kernel32.LocalFree(security_descriptor)
    finally:
        kernel32.LocalFree(owner_sid)
    verify_windows_private_file(path)


def _validate_windows_private_descriptor(
    advapi32: ctypes.CDLL,
    kernel32: ctypes.CDLL,
    security_descriptor: ctypes.c_void_p,
    owner: ctypes.c_void_p,
) -> None:
    """Validate owner and the exact protected DACL in a retrieved descriptor."""
    rendered = ctypes.c_void_p()
    rendered_owner = ctypes.c_void_p()
    length = ctypes.c_uint32()
    try:
        convert = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        convert.restype = ctypes.c_int
        if not convert(
            security_descriptor, 1, 0x00000004, ctypes.byref(rendered), ctypes.byref(length)
        ):
            raise _windows_error("could not render private engine-state DACL")
        sddl = ctypes.wstring_at(rendered)
        convert_owner = advapi32.ConvertSidToStringSidW
        convert_owner.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        convert_owner.restype = ctypes.c_int
        if not convert_owner(owner, ctypes.byref(rendered_owner)):
            raise _windows_error("could not render the engine-state owner SID")
        owner_sddl = ctypes.wstring_at(rendered_owner)
    finally:
        if rendered_owner.value:
            kernel32.LocalFree(rendered_owner)
        if rendered.value:
            kernel32.LocalFree(rendered)
    principals = set(re.findall(r"\(A;;FA;;;([^)]+)\)", sddl))
    current_user_sid = _windows_current_user_sid()
    if (
        owner_sddl != current_user_sid
        or not sddl.startswith("D:P")
        or not _windows_dacl_principals_are_exact(
            principals, current_user_sid, _windows_sddl_principal_equals_sid
        )
        or sddl.count("(") != 2
    ):
        raise PermissionError(f"engine-state DACL is not restricted to owner and SYSTEM: {sddl}")


def _verify_windows_private_handle(handle: ctypes.c_void_p) -> None:
    """Verify the live no-sharing handle before any secret bytes are written."""
    advapi32, kernel32 = _windows_security_libraries()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    get_security = advapi32.GetSecurityInfo
    get_security.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = ctypes.c_uint32
    result = get_security(
        handle,
        1,
        0x00000005,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if result:
        raise _windows_error("could not inspect protected engine-state handle", result)
    try:
        _validate_windows_private_descriptor(advapi32, kernel32, security_descriptor, owner)
    finally:
        kernel32.LocalFree(security_descriptor)


def verify_windows_private_file(path: Path) -> None:
    """Fail unless a Windows file has exactly the protected owner+SYSTEM DACL."""
    if sys.platform != "win32":
        return
    advapi32, kernel32 = _windows_security_libraries()
    dacl = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    get_security = advapi32.GetNamedSecurityInfoW
    get_security.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = ctypes.c_uint32
    result = get_security(
        str(path),
        1,
        0x00000005,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if result:
        raise _windows_error("could not inspect private engine-state DACL", result)
    try:
        _validate_windows_private_descriptor(advapi32, kernel32, security_descriptor, owner)
    finally:
        kernel32.LocalFree(security_descriptor)

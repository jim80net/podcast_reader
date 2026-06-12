"""Built-in pack registry: data plus pure functions, evaluable without network.

The registry is authoritative for what can be installed (per design decision
3): per pack — id, kind, platform gate, download spec (exact URLs with
per-file sha256 and size), component versions, the compat range this engine
build requires, and license notices. A registry entry whose artifact is not
yet published carries ``files: None`` (per S5): such a pack is unpublished —
not installable, reported ``unavailable`` by ``GET /v1/packs``.

A successful install writes ``pack-manifest.json`` into the pack directory
*last*; a pack directory without a valid manifest is by definition not
installed (atomic-by-construction install). Startup validation checks the
manifest against :func:`compat_error` (pack schema + component pairings) and
:func:`files_error` (existence + recorded size only — no hashing, per S8).

Pin provenance (2026-06-12):

- NVIDIA wheels: PyPI JSON API (``https://pypi.org/pypi/<pkg>/<ver>/json``),
  win_amd64 wheels for the versions the spike's ctranslate2 4.8.0 pin matrix
  requires (cuDNN 9.x + CUDA 12 cuBLAS; spike §"Version pin matrix").
- Whisper models: Hugging Face ``Systran/faster-whisper-*`` snapshots at the
  exact revisions below; LFS sha256s from the HF tree API, non-LFS files
  hashed from the downloaded bytes at those revisions.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

# pydantic (FastAPI response models) requires typing_extensions.TypedDict on
# Python < 3.12; typing.TypedDict raises PydanticUserError there.
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from pathlib import Path

PACK_SCHEMA = 1
MANIFEST_FILE = "pack-manifest.json"

PackKind = Literal["runtime", "model", "worker"]
PackState = Literal[
    "not-installed",
    "resumable",
    "installing",
    "installed",
    "incompatible",
    "failed",
    "unavailable",
]


class PackFilePin(TypedDict):
    """One pinned download: install-relative path, exact URL, sha256, size."""

    path: str
    url: str
    sha256: str
    size: int


class LicenseNotice(TypedDict):
    """An attribution surfaced in Settings via the installed manifest."""

    name: str
    text: str


class PackEntry(TypedDict):
    """One registry pack.

    ``platforms`` lists the supporting ``sys.platform`` values (``None`` = all
    platforms). ``files: None`` marks the entry unpublished (per S5).
    ``extract_wheels`` selects the CUDA install step: download the pinned
    wheels, extract the complete ``nvidia/*/bin/*.dll`` set, delete archives.
    ``compat`` maps component name -> required major version — what THIS
    engine build requires, checked against installed manifests at startup.
    """

    id: str
    kind: PackKind
    display_name: str
    platforms: list[str] | None
    install_dir: str
    extract_wheels: bool
    files: list[PackFilePin] | None
    version: str
    component_versions: dict[str, str]
    compat: dict[str, str]
    licenses: list[LicenseNotice]


class ManifestFile(TypedDict):
    """One installed file as recorded at install time."""

    path: str
    sha256: str
    size: int


class PackManifest(TypedDict):
    """``pack-manifest.json`` — written last; records what is on disk."""

    pack_schema: int
    id: str
    version: str
    component_versions: dict[str, str]
    files: list[ManifestFile]
    licenses: list[LicenseNotice]


class PackProgress(TypedDict):
    """Download progress for an installing pack."""

    bytes: int
    total: int


class HardwareInfo(TypedDict):
    """Detected hardware reported with the pack listing."""

    platform: str
    nvidia_gpu: bool
    gpu_names: list[str]


class PackStatus(TypedDict):
    """One pack in the ``GET /v1/packs`` listing.

    ``licenses`` carries the attribution notices Settings renders (task 8.1):
    the installed manifest's notices when the pack is on disk (what was
    actually installed), the registry's otherwise — engine-authoritative
    either way.
    """

    id: str
    kind: PackKind
    display_name: str
    size: int
    state: PackState
    recommended: bool
    installed_version: str | None
    progress: PackProgress | None
    error: PackInstallError | None
    licenses: list[LicenseNotice]


class PackInstallError(TypedDict):
    """Structured pack error (mirrors the JobError shape, minus hint)."""

    code: str
    message: str


class PacksResponse(TypedDict):
    """Body of ``GET /v1/packs``: hardware block + per-pack statuses."""

    hardware: HardwareInfo
    packs: list[PackStatus]


_MIT_MODEL_LICENSE = LicenseNotice(
    name="MIT (Systran faster-whisper model conversion of OpenAI Whisper)",
    text=(
        "Model weights converted and published by Systran under the MIT "
        "license (https://huggingface.co/Systran); original Whisper models "
        "by OpenAI, also MIT-licensed."
    ),
)

_NVIDIA_LICENSES = [
    LicenseNotice(
        name="NVIDIA cuBLAS",
        text=(
            "This product uses NVIDIA cuBLAS runtime libraries, downloaded "
            "unmodified from NVIDIA's official PyPI distribution "
            "(nvidia-cublas-cu12) and installed into an application-private "
            "directory under the NVIDIA CUDA Toolkit End User License "
            "Agreement (attribution clause). cuBLAS is (c) NVIDIA Corporation."
        ),
    ),
    LicenseNotice(
        name="NVIDIA cuDNN",
        text=(
            "This product uses NVIDIA cuDNN runtime libraries, downloaded "
            "unmodified from NVIDIA's official PyPI distribution "
            "(nvidia-cudnn-cu12) and installed into an application-private "
            "directory under the NVIDIA cuDNN End User License Agreement. "
            "cuDNN source-code notice: portions of cuDNN incorporate "
            "third-party open-source software; see the cuDNN EULA "
            "(https://docs.nvidia.com/deeplearning/cudnn/sla/) for the "
            "complete notices. cuDNN is (c) NVIDIA Corporation."
        ),
    ),
]


def _hf_pin(model: str, revision: str, path: str, sha256: str, size: int) -> PackFilePin:
    return PackFilePin(
        path=path,
        url=f"https://huggingface.co/Systran/faster-whisper-{model}/resolve/{revision}/{path}",
        sha256=sha256,
        size=size,
    )


def _model_pack(model: str, revision: str, files: list[PackFilePin]) -> PackEntry:
    return PackEntry(
        id=f"model-{model}",
        kind="model",
        display_name=f"Whisper {model} model",
        platforms=None,
        install_dir=f"models/{model}",
        extract_wheels=False,
        files=files,
        version=revision,
        component_versions={"model_revision": revision},
        compat={},  # model dirs are format-stable across the shipped ct2 range
        licenses=[_MIT_MODEL_LICENSE],
    )


_TINY_REV = "d90ca5fe260221311c53c58e660288d3deb8d356"
_SMALL_REV = "536b0662742c02347bc0e980a01041f333bce120"
_MEDIUM_REV = "08e178d48790749d25932bbc082711ddcfdfbc4f"
_LARGE_V3_REV = "edaa852ec7e145841d8ffdb056a99866b5f0a478"

#: tokenizer.json / vocabulary.txt are byte-identical across tiny/small/medium
#: at the pinned revisions (verified by hashing the downloaded files).
_SHARED_TOKENIZER_SHA = "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"
_SHARED_VOCABULARY_SHA = "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"

REGISTRY: dict[str, PackEntry] = {
    "cuda-runtime": PackEntry(
        id="cuda-runtime",
        kind="runtime",
        display_name="NVIDIA CUDA runtime (cuBLAS + cuDNN 9)",
        platforms=["win32"],  # Windows-only per the parent design
        install_dir="runtime",
        extract_wheels=True,
        files=[
            PackFilePin(
                path="nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
                url=(
                    "https://files.pythonhosted.org/packages/20/e2/"
                    "fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
                    "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"
                ),
                sha256="623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
                size=553162896,
            ),
            PackFilePin(
                path="nvidia_cudnn_cu12-9.23.1.3-py3-none-win_amd64.whl",
                url=(
                    "https://files.pythonhosted.org/packages/75/ec/"
                    "62b56fc5e8219a268c6f62c4e9fb1369ebec049512328e650d1a9a28bcc8/"
                    "nvidia_cudnn_cu12-9.23.1.3-py3-none-win_amd64.whl"
                ),
                sha256="b874af5bfab5e1010ae88bfead14bf8e9da6b20283582288f1c05f056090a398",
                size=689996767,
            ),
        ],
        version="1",
        component_versions={"cublas": "12.9.2.10", "cudnn": "9.23.1.3"},
        # ctranslate2 4.8.0 (the frozen build): cuDNN 9.x only since 4.5.0,
        # cuBLAS for CUDA 12 (spike pin matrix; faster-whisper README).
        compat={"cudnn": "9", "cublas": "12"},
        licenses=list(_NVIDIA_LICENSES),
    ),
    "model-tiny": _model_pack(
        "tiny",
        _TINY_REV,
        [
            _hf_pin(
                "tiny",
                _TINY_REV,
                "model.bin",
                "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
                75538270,
            ),
            _hf_pin(
                "tiny",
                _TINY_REV,
                "config.json",
                "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
                2249,
            ),
            _hf_pin("tiny", _TINY_REV, "tokenizer.json", _SHARED_TOKENIZER_SHA, 2203239),
            _hf_pin("tiny", _TINY_REV, "vocabulary.txt", _SHARED_VOCABULARY_SHA, 459861),
        ],
    ),
    "model-small": _model_pack(
        "small",
        _SMALL_REV,
        [
            _hf_pin(
                "small",
                _SMALL_REV,
                "model.bin",
                "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
                483546902,
            ),
            _hf_pin(
                "small",
                _SMALL_REV,
                "config.json",
                "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
                2370,
            ),
            _hf_pin("small", _SMALL_REV, "tokenizer.json", _SHARED_TOKENIZER_SHA, 2203239),
            _hf_pin("small", _SMALL_REV, "vocabulary.txt", _SHARED_VOCABULARY_SHA, 459861),
        ],
    ),
    "model-medium": _model_pack(
        "medium",
        _MEDIUM_REV,
        [
            _hf_pin(
                "medium",
                _MEDIUM_REV,
                "model.bin",
                "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
                1527906378,
            ),
            _hf_pin(
                "medium",
                _MEDIUM_REV,
                "config.json",
                "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
                2257,
            ),
            _hf_pin("medium", _MEDIUM_REV, "tokenizer.json", _SHARED_TOKENIZER_SHA, 2203239),
            _hf_pin("medium", _MEDIUM_REV, "vocabulary.txt", _SHARED_VOCABULARY_SHA, 459861),
        ],
    ),
    "model-large-v3": _model_pack(
        "large-v3",
        _LARGE_V3_REV,
        [
            _hf_pin(
                "large-v3",
                _LARGE_V3_REV,
                "model.bin",
                "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
                3087284237,
            ),
            _hf_pin(
                "large-v3",
                _LARGE_V3_REV,
                "config.json",
                "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
                2394,
            ),
            _hf_pin(
                "large-v3",
                _LARGE_V3_REV,
                "preprocessor_config.json",
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                340,
            ),
            _hf_pin(
                "large-v3",
                _LARGE_V3_REV,
                "tokenizer.json",
                "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
                2480617,
            ),
            _hf_pin(
                "large-v3",
                _LARGE_V3_REV,
                "vocabulary.json",
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
                1068114,
            ),
        ],
    ),
    "diarization": PackEntry(
        id="diarization",
        kind="worker",
        display_name="Speaker diarization worker",
        platforms=None,
        install_dir="workers/diarization",
        extract_wheels=False,
        # Unpublished (per S5): no download spec until the release pipeline
        # publishes a pack-diarization-v* artifact (task 7.5). Everything
        # else reflects the stack the 5.1 freeze smoke proved
        # (packaging/DIARIZATION_SMOKE.md): 7.5 only flips `files`.
        files=None,
        version="1",
        component_versions={
            # The argv/turns.json contract the engine's diarize step speaks.
            "worker_contract": "1",
            "pyannote_audio": "4.0.4",
            "torch": "2.12.0+cpu",
        },
        compat={"worker_contract": "1"},
        licenses=[
            LicenseNotice(
                name="MIT (pyannote.audio)",
                text=(
                    "Speaker diarization powered by pyannote.audio "
                    "(https://github.com/pyannote/pyannote-audio), MIT-licensed; "
                    "pipeline models by pyannoteAI/CNRS, MIT-licensed."
                ),
            ),
            LicenseNotice(
                name="BSD-3-Clause (PyTorch)",
                text=(
                    "This pack bundles the CPU build of PyTorch "
                    "(https://pytorch.org), BSD-3-Clause licensed, "
                    "(c) PyTorch contributors."
                ),
            ),
        ],
    ),
}


def is_published(entry: PackEntry) -> bool:
    """True when the entry carries a download spec (per S5)."""
    return entry["files"] is not None


def platform_supported(entry: PackEntry, platform: str) -> bool:
    """True when *entry* supports ``sys.platform`` value *platform*."""
    platforms = entry["platforms"]
    return platforms is None or platform in platforms


def pack_total_size(entry: PackEntry) -> int:
    """Total download size in bytes (0 for unpublished entries)."""
    files = entry["files"]
    if files is None:
        return 0
    return sum(pin["size"] for pin in files)


def pack_dir(base: Path, entry: PackEntry) -> Path:
    """The pack's install directory under the engine data dir."""
    return base.joinpath(*entry["install_dir"].split("/"))


def manifest_path(pack_dir_path: Path) -> Path:
    """Location of ``pack-manifest.json`` inside a pack directory."""
    return pack_dir_path / MANIFEST_FILE


def read_manifest(pack_dir_path: Path) -> PackManifest | None:
    """The pack's installed manifest, or ``None`` when absent or invalid.

    A pack directory without a valid manifest is by definition not installed
    (install writes the manifest last; uninstall deletes it first, per S1) —
    so unreadable or wrong-shaped manifests read as "not installed" rather
    than raising.
    """
    path = manifest_path(pack_dir_path)
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict) or "pack_schema" not in loaded or "files" not in loaded:
        return None
    manifest: PackManifest = loaded  # type: ignore[assignment]
    return manifest


def compat_error(entry: PackEntry, manifest: PackManifest) -> str | None:
    """Why *manifest* falls outside this engine's compat range, or ``None``.

    Checks the pack schema version and the component pairings the registry
    requires (e.g. ctranslate2 4.8 <-> cuDNN 9): each ``compat`` component's
    installed major version must equal the required major.
    """
    if manifest["pack_schema"] != PACK_SCHEMA:
        return (
            f"manifest pack_schema {manifest['pack_schema']} is not the "
            f"supported schema {PACK_SCHEMA}"
        )
    for component, required_major in entry["compat"].items():
        installed = manifest["component_versions"].get(component)
        if installed is None or installed.split(".")[0] != required_major:
            return (
                f"component {component} version {installed!r} is outside the "
                f"required major version {required_major} for this engine build"
            )
    return None


def files_error(pack_dir_path: Path, manifest: PackManifest) -> str | None:
    """Why the manifest-listed files fail the integrity check, or ``None``.

    Existence + recorded size only — no content hashing (per S8): hashing
    gigabytes at every startup is not worth catching bit rot the size check
    misses.
    """
    for recorded in manifest["files"]:
        path = pack_dir_path / recorded["path"]
        if not path.is_file():
            return f"missing file: {recorded['path']}"
        actual = path.stat().st_size
        if actual != recorded["size"]:
            return (
                f"size mismatch for {recorded['path']}: "
                f"recorded {recorded['size']} bytes, found {actual}"
            )
    return None

"""Tests for podcast_reader.engine.packs (registry data + pure functions).

Spec: pack-management "Built-in pack registry" — the registry is data plus
pure functions, evaluable without network access; every test here runs
offline against the shipped pins.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.packs import (
    CUDA_DLL_STEMS,
    MANIFEST_FILE,
    PACK_SCHEMA,
    REGISTRY,
    ManifestFile,
    PackManifest,
    compat_error,
    files_error,
    is_published,
    manifest_path,
    pack_dir,
    pack_files_error,
    pack_total_size,
    platform_supported,
    read_manifest,
)
from podcast_reader.engine.settings import atomic_write_json

if TYPE_CHECKING:
    from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HF_REVISION_RE = re.compile(r"/resolve/[0-9a-f]{40}/")

_MODEL_IDS = ["model-tiny", "model-small", "model-medium", "model-large-v3"]


def _manifest(
    *,
    pack_id: str = "cuda-runtime",
    schema: int = PACK_SCHEMA,
    component_versions: dict[str, str] | None = None,
    files: list[ManifestFile] | None = None,
) -> PackManifest:
    return PackManifest(
        pack_schema=schema,
        id=pack_id,
        version="1",
        component_versions=(
            component_versions
            if component_versions is not None
            else {"cudnn": "9.23.1.3", "cublas": "12.9.2.10"}
        ),
        files=files if files is not None else [],
        licenses=[],
    )


class TestRegistryData:
    """Spec scenario: Registry lists all packs."""

    def test_registry_lists_all_packs(self) -> None:
        assert set(REGISTRY) == {"cuda-runtime", *_MODEL_IDS, "diarization"}

    def test_entry_ids_match_their_keys(self) -> None:
        for pack_id, entry in REGISTRY.items():
            assert entry["id"] == pack_id

    def test_published_packs_carry_pinned_urls_and_sha256(self) -> None:
        for entry in REGISTRY.values():
            files = entry["files"]
            if files is None:
                continue
            assert files, f"published pack {entry['id']} has an empty download spec"
            for pin in files:
                assert pin["url"].startswith("https://")
                assert _SHA256_RE.fullmatch(pin["sha256"]), pin
                assert pin["size"] > 0

    def test_model_packs_pin_exact_hf_revisions(self) -> None:
        """Spec scenario: Revision pinned (runtime-packs) — registry side."""
        for model_id in _MODEL_IDS:
            entry = REGISTRY[model_id]
            files = entry["files"]
            assert files is not None
            revisions = set()
            for pin in files:
                match = _HF_REVISION_RE.search(pin["url"])
                assert match is not None, f"{model_id} URL not revision-pinned: {pin['url']}"
                revisions.add(match.group(0))
            assert len(revisions) == 1  # one snapshot, not a mix of revisions

    def test_model_packs_contain_offline_load_set(self) -> None:
        """Every file faster-whisper needs to load offline is pinned."""
        for model_id in _MODEL_IDS:
            files = REGISTRY[model_id]["files"]
            assert files is not None
            names = {pin["path"] for pin in files}
            assert {"model.bin", "config.json", "tokenizer.json"} <= names
            assert names & {"vocabulary.txt", "vocabulary.json"}

    def test_cuda_pack_is_windows_only_wheel_extraction(self) -> None:
        entry = REGISTRY["cuda-runtime"]
        assert entry["platforms"] == ["win32"]
        assert entry["extract_wheels"] is True
        files = entry["files"]
        assert files is not None
        assert {pin["path"] for pin in files} == {
            "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
            "nvidia_cudnn_cu12-9.23.1.3-py3-none-win_amd64.whl",
        }
        for pin in files:
            assert pin["url"].startswith("https://files.pythonhosted.org/")

    def test_cuda_pack_compat_encodes_ctranslate2_pin_matrix(self) -> None:
        """ctranslate2 4.8.0 pairs strictly with CUDA 12 cuBLAS + cuDNN 9."""
        entry = REGISTRY["cuda-runtime"]
        assert entry["compat"] == {"cudnn": "9", "cublas": "12"}
        assert entry["component_versions"]["cudnn"].startswith("9.")
        assert entry["component_versions"]["cublas"].startswith("12.")

    def test_cuda_pack_carries_nvidia_notices(self) -> None:
        names = " ".join(notice["name"] for notice in REGISTRY["cuda-runtime"]["licenses"])
        assert "cuBLAS" in names
        assert "cuDNN" in names

    def test_diarization_is_unpublished(self) -> None:
        """Spec scenario: Unpublished pack is not installable (per S5) —
        registry side: no download spec until 7.5 publishes an artifact."""
        entry = REGISTRY["diarization"]
        assert entry["files"] is None
        assert is_published(entry) is False

    def test_diarization_carries_the_smoked_component_pins(self) -> None:
        """Task 5.1's GO freeze fixes the shipped stack: the entry carries the
        real component/compat shape behind the unpublished flag, so 7.5 only
        flips `files` to the published pins."""
        entry = REGISTRY["diarization"]
        assert entry["install_dir"] == "workers/diarization"
        assert entry["component_versions"]["pyannote_audio"] == "4.0.4"
        assert entry["component_versions"]["torch"].startswith("2.12")
        assert entry["component_versions"]["worker_contract"] == "1"
        # startup validation has something real to check once installed
        assert entry["compat"] == {"worker_contract": "1"}
        names = " ".join(notice["name"] for notice in entry["licenses"])
        assert "pyannote" in names
        assert "PyTorch" in names

    def test_all_other_packs_are_published(self) -> None:
        for pack_id, entry in REGISTRY.items():
            if pack_id != "diarization":
                assert is_published(entry), pack_id

    def test_pin_sha256s_are_unique_within_each_pack(self) -> None:
        """T5: staging partials are named by sha256 (per S2), so duplicate
        shas within one pack would collide on the same staging file."""
        for pack_id, entry in REGISTRY.items():
            files = entry["files"]
            if files is None:
                continue
            shas = [pin["sha256"] for pin in files]
            assert len(shas) == len(set(shas)), f"duplicate pin sha256 in pack {pack_id}"

    def test_pack_total_size_sums_pins(self) -> None:
        tiny = REGISTRY["model-tiny"]
        files = tiny["files"]
        assert files is not None
        assert pack_total_size(tiny) == sum(pin["size"] for pin in files)
        assert pack_total_size(REGISTRY["diarization"]) == 0


class TestPlatformGate:
    """Spec scenario: Platform-gated pack excluded."""

    def test_cuda_supported_only_on_windows(self) -> None:
        entry = REGISTRY["cuda-runtime"]
        assert platform_supported(entry, "win32") is True
        assert platform_supported(entry, "darwin") is False
        assert platform_supported(entry, "linux") is False

    def test_ungated_packs_supported_everywhere(self) -> None:
        for platform in ("win32", "darwin", "linux"):
            assert platform_supported(REGISTRY["model-tiny"], platform) is True


class TestPackDir:
    def test_model_pack_installs_under_models_name(self, tmp_path: Path) -> None:
        """The whisper worker maps model name -> <data_dir>/models/<name>."""
        assert pack_dir(tmp_path, REGISTRY["model-large-v3"]) == tmp_path / "models" / "large-v3"

    def test_cuda_pack_installs_under_runtime(self, tmp_path: Path) -> None:
        assert pack_dir(tmp_path, REGISTRY["cuda-runtime"]) == tmp_path / "runtime"

    def test_manifest_path_is_inside_pack_dir(self, tmp_path: Path) -> None:
        assert manifest_path(tmp_path / "runtime") == tmp_path / "runtime" / MANIFEST_FILE


class TestCompat:
    """Spec: Startup compatibility validation — compat-range half."""

    def test_matching_manifest_passes(self) -> None:
        assert compat_error(REGISTRY["cuda-runtime"], _manifest()) is None

    def test_schema_mismatch_is_incompatible(self) -> None:
        error = compat_error(REGISTRY["cuda-runtime"], _manifest(schema=PACK_SCHEMA + 1))
        assert error is not None
        assert "pack_schema" in error

    def test_component_major_outside_range_is_incompatible(self) -> None:
        """Spec scenario: App update moves the compat range — a cuDNN 8
        manifest fails the engine's cuDNN 9 requirement."""
        manifest = _manifest(component_versions={"cudnn": "8.9.7", "cublas": "12.9.2.10"})
        error = compat_error(REGISTRY["cuda-runtime"], manifest)
        assert error is not None
        assert "cudnn" in error

    def test_missing_component_is_incompatible(self) -> None:
        manifest = _manifest(component_versions={"cublas": "12.9.2.10"})
        error = compat_error(REGISTRY["cuda-runtime"], manifest)
        assert error is not None
        assert "cudnn" in error

    def test_model_pack_compat_is_schema_only(self) -> None:
        manifest = _manifest(pack_id="model-tiny", component_versions={})
        assert compat_error(REGISTRY["model-tiny"], manifest) is None


class TestFilesValidation:
    """Spec: Startup compatibility validation — existence + size, no hashing
    (per S8)."""

    def _seed(self, pack_dir_path: Path) -> PackManifest:
        pack_dir_path.mkdir(parents=True, exist_ok=True)
        (pack_dir_path / "model.bin").write_bytes(b"weights")
        (pack_dir_path / "config.json").write_bytes(b"{}")
        return _manifest(
            files=[
                ManifestFile(path="model.bin", sha256="0" * 64, size=7),
                ManifestFile(path="config.json", sha256="1" * 64, size=2),
            ]
        )

    def test_all_files_present_with_recorded_sizes_pass(self, tmp_path: Path) -> None:
        manifest = self._seed(tmp_path / "pack")
        assert files_error(tmp_path / "pack", manifest) is None

    def test_missing_file_named_in_error(self, tmp_path: Path) -> None:
        """Spec scenario: Missing or truncated pack file detected at startup."""
        manifest = self._seed(tmp_path / "pack")
        (tmp_path / "pack" / "model.bin").unlink()
        error = files_error(tmp_path / "pack", manifest)
        assert error is not None
        assert "model.bin" in error

    def test_truncated_file_named_in_error(self, tmp_path: Path) -> None:
        manifest = self._seed(tmp_path / "pack")
        (tmp_path / "pack" / "model.bin").write_bytes(b"wei")
        error = files_error(tmp_path / "pack", manifest)
        assert error is not None
        assert "model.bin" in error

    def test_partial_cuda_manifest_fails_required_dll_set(self, tmp_path: Path) -> None:
        target = tmp_path / "runtime"
        target.mkdir()
        (target / "cudnn64_9.dll").write_bytes(b"dll")
        manifest = _manifest(files=[ManifestFile(path="cudnn64_9.dll", sha256="0" * 64, size=3)])

        assert files_error(target, manifest) is None
        error = pack_files_error(REGISTRY["cuda-runtime"], target, manifest)
        assert error is not None
        assert "cublas64" in error

    def test_complete_cuda_manifest_passes_required_dll_set(self, tmp_path: Path) -> None:
        target = tmp_path / "runtime"
        target.mkdir()
        recorded: list[ManifestFile] = []
        for stem in CUDA_DLL_STEMS:
            suffix = "12" if stem.startswith("cublas") else "9"
            name = f"{stem}_{suffix}.dll"
            (target / name).write_bytes(b"dll")
            recorded.append(ManifestFile(path=name, sha256="0" * 64, size=3))

        assert pack_files_error(REGISTRY["cuda-runtime"], target, _manifest(files=recorded)) is None


class TestManifestIO:
    def test_read_manifest_missing_returns_none(self, tmp_path: Path) -> None:
        assert read_manifest(tmp_path) is None

    def test_manifest_roundtrip(self, tmp_path: Path) -> None:
        manifest = _manifest()
        atomic_write_json(manifest_path(tmp_path), manifest)
        assert read_manifest(tmp_path) == manifest

    def test_corrupt_manifest_reads_as_not_installed(self, tmp_path: Path) -> None:
        """A pack dir without a VALID manifest is by definition not installed."""
        manifest_path(tmp_path).write_text("{not json")
        assert read_manifest(tmp_path) is None

    def test_wrong_shape_manifest_reads_as_not_installed(self, tmp_path: Path) -> None:
        manifest_path(tmp_path).write_text('["a", "list"]')
        assert read_manifest(tmp_path) is None


class TestManifestShape:
    """T2: corrupt-but-parseable manifests read as not installed.

    The engine dereferences ``version`` (status), ``component_versions``
    (compat, splitting each version string), ``files`` with per-file
    path/sha256/size (integrity + uninstall), and ``licenses`` (status) —
    so :func:`read_manifest` vouches for those shapes rather than letting a
    garbage manifest crash startup validation or status derivation.
    """

    @staticmethod
    def _write(tmp_path: Path, payload: object) -> None:
        manifest_path(tmp_path).write_text(json.dumps(payload))

    @pytest.mark.parametrize("missing", ["pack_schema", "version", "component_versions", "files"])
    def test_missing_required_field_reads_as_not_installed(
        self, tmp_path: Path, missing: str
    ) -> None:
        payload = dict(_manifest())
        del payload[missing]
        self._write(tmp_path, payload)
        assert read_manifest(tmp_path) is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("pack_schema", "1"),
            ("version", 2),
            ("component_versions", "9.1"),
            ("component_versions", {"cudnn": 9}),
            ("files", "not-a-list"),
            ("files", [["path", "sha", "size"]]),
            ("files", [{"sha256": "0" * 64, "size": 7}]),
            ("files", [{"path": "f", "size": 7}]),
            ("files", [{"path": "f", "sha256": "0" * 64}]),
            ("files", [{"path": 1, "sha256": "0" * 64, "size": 7}]),
            ("files", [{"path": "f", "sha256": 0, "size": 7}]),
            ("files", [{"path": "f", "sha256": "0" * 64, "size": "7"}]),
            ("licenses", "MIT"),
            ("licenses", ["MIT"]),
            ("licenses", [{}]),
            ("licenses", [{"name": "MIT"}]),
            ("licenses", [{"text": "..."}]),
            ("licenses", [{"name": 1, "text": "..."}]),
            ("licenses", [{"name": "MIT", "text": None}]),
        ],
    )
    def test_wrong_typed_field_reads_as_not_installed(
        self, tmp_path: Path, field: str, value: object
    ) -> None:
        payload = dict(_manifest())
        payload[field] = value
        self._write(tmp_path, payload)
        assert read_manifest(tmp_path) is None

    def test_well_shaped_manifest_with_files_reads_back(self, tmp_path: Path) -> None:
        manifest = _manifest(files=[ManifestFile(path="model.bin", sha256="0" * 64, size=7)])
        self._write(tmp_path, manifest)
        assert read_manifest(tmp_path) == manifest

    def test_well_shaped_license_notices_read_back(self, tmp_path: Path) -> None:
        payload = dict(_manifest())
        payload["licenses"] = [{"name": "MIT", "text": "Permission is hereby granted..."}]
        self._write(tmp_path, payload)
        assert read_manifest(tmp_path) == payload

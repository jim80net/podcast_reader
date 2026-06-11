# tool-resolution

## ADDED Requirements

### Requirement: Freeze-aware tool resolution
`resolve_tool` SHALL resolve **external** tool names (yt-dlp, ffmpeg, whisper-ctranslate2) in this precedence order: (1) the user-data tools directory — supplied as an explicit `tools_dir` parameter, defaulting to the `PODCAST_READER_TOOLS_DIR` environment variable when set, (2) under `sys.frozen`, the frozen bundle's `tools/` directory; otherwise the directory containing `sys.executable` (current behavior), (3) bare name for PATH lookup. Under `sys.frozen`, `Path(sys.executable).parent` SHALL NOT be searched for **external** console scripts (none exist at bundle root in a frozen app).

#### Scenario: User-data copy wins
- **WHEN** a tool exists in both the user-data tools directory and the bundle/interpreter directory
- **THEN** the user-data path is returned

#### Scenario: Frozen bundle resolution
- **WHEN** running frozen (`sys.frozen` set) and the tool exists in the bundle tools directory
- **THEN** the bundle path is returned and the interpreter directory is not consulted

#### Scenario: Unfrozen behavior preserved
- **WHEN** running unfrozen with the tool present next to `sys.executable`
- **THEN** that sibling path is returned (existing behavior)

#### Scenario: PATH fallback preserved
- **WHEN** the tool exists in none of the preferred directories
- **THEN** the bare tool name is returned for PATH lookup

### Requirement: Bundled worker resolution
Bundled worker executables (e.g. `whisper-worker`) are a distinct class from external tools: in a frozen onedir bundle they live exactly at `Path(sys.executable).parent` (spike evidence: both entry points sit at bundle root sharing `_internal/`). `resolve_bundled_worker(name)` SHALL return the sibling executable path when running frozen and the worker exists, and `None` otherwise (unfrozen runs have no bundled workers; callers fall back to external tool resolution).

#### Scenario: Frozen bundle resolves sibling worker
- **WHEN** running frozen and `whisper-worker` exists next to the executable
- **THEN** `resolve_bundled_worker("whisper-worker")` returns that sibling path

#### Scenario: Unfrozen returns None
- **WHEN** running unfrozen
- **THEN** `resolve_bundled_worker("whisper-worker")` returns `None`

#### Scenario: Frozen but worker absent returns None
- **WHEN** running frozen and no such worker exists at bundle root
- **THEN** `resolve_bundled_worker` returns `None`

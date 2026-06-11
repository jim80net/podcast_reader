## 1. Create pyproject.toml and package skeleton

- [x] 1.1 Write pyproject.toml with hatchling, core deps (anthropic, youtube-transcript-api, yt-dlp), optional extras (whisper, diarization, dev), console script entry point, mypy/ruff/pytest config
- [x] 1.2 Create src/podcast_reader/__init__.py
- [x] 1.3 Run `uv sync --dev` and verify package resolves
- [x] 1.4 Commit

## 2. Move youtube.py (from youtube_transcript.py)

- [x] 2.1 Copy tests, update imports
- [x] 2.2 Create src/podcast_reader/youtube.py (remove main/argparse, add types, drop .title sidecar logic)
- [x] 2.3 Run tests, ruff, mypy — all clean
- [x] 2.4 Commit

## 3. Move chapters.py (from generate_chapters.py)

- [x] 3.1 Copy tests, update imports
- [x] 3.2 Create src/podcast_reader/chapters.py (remove main, add types, keep SYSTEM_PROMPT and logic)
- [x] 3.3 Run tests + tooling checks
- [x] 3.4 Commit

## 4. Move html.py (from json_to_html.py)

- [x] 4.1 Copy tests, update imports
- [x] 4.2 Create src/podcast_reader/html.py (remove main/argparse/json/Path, add future annotations + full types; preserve all CSS/JS/render logic verbatim)
- [x] 4.3 Run tests + tooling
- [x] 4.4 Commit

## 5. Create ytdlp.py (new module)

- [x] 5.1 Write unit tests for build_*_args, fetch_title, download_audio (with subprocess mocks + error cases)
- [x] 5.2 Implement the module (build args, run subprocess, handle auth hint, return Path)
- [x] 5.3 Run tests + ruff + mypy
- [x] 5.4 Commit

## 6. Create transcribe.py (new module)

- [x] 6.1 Write unit tests for build_whisper_args and transcribe (mocks)
- [x] 6.2 Implement the thin wrapper around whisper-ctranslate2
- [x] 6.3 Run tests + tooling
- [x] 6.4 Commit

## 7. Create cli.py (main entry point) + full integration

- [x] 7.1 Write tests for classify_input (YouTube / URL / local file cases)
- [x] 7.2 Implement cli.py: argparse, env var reading, WSL path helper, _run_pipeline orchestration that calls the right modules, caching, chapter generation (if key present), HTML output
- [x] 7.3 Wire everything; add --output-dir, --model flags matching prior behavior
- [x] 7.4 Run full test suite (unit + any available integration)
- [x] 7.5 Run ruff + mypy on entire src/
- [x] 7.6 Delete old loose files (transcribe.sh, *_transcript.py, generate_chapters.py, json_to_html.py, requirements.txt)
- [x] 7.7 Update README.md, CLAUDE.md, AGENTS.md with new usage and structure
- [x] 7.8 Manual smoke test on local file + YouTube + a public X post (if credentials allow)
- [x] 7.9 Commit

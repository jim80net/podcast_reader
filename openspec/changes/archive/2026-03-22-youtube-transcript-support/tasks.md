## 1. Add dependencies

- [x] 1.1 Add youtube-transcript-api and pytest to requirements.txt (or pyproject.toml in later packaging change)
- [x] 1.2 Install dependencies with uv
- [x] 1.3 Verify imports work
- [x] 1.4 Commit

## 2. Create youtube_transcript.py with URL parsing and transcript fetching

- [x] 2.1 Create tests directory and write failing tests for video ID extraction
- [x] 2.2 Run tests to verify they fail
- [x] 2.3 Implement extract_video_id
- [x] 2.4 Run tests to verify they pass
- [x] 2.5 Write failing tests for transcript conversion (snippets_to_whisper_segments)
- [x] 2.6 Run tests to verify new tests fail
- [x] 2.7 Implement snippets_to_whisper_segments
- [x] 2.8 Run all tests to verify they pass
- [x] 2.9 Implement fetch_transcript, fetch_video_title, and main
- [x] 2.10 Commit

## 3. Integrate YouTube support into transcribe.sh (later cli.py)

- [x] 3.1 Add *.title to .gitignore
- [x] 3.2 Rewrite input resolution section of transcribe.sh with YouTube branch
- [x] 3.3 Pass --source flag to json_to_html.py
- [x] 3.4 Commit

## 4. Update json_to_html.py to accept --source flag

- [x] 4.1 Add --source argument to argparse
- [x] 4.2 Pass source through to build_html
- [x] 4.3 Update build_html signature and meta line / footer
- [x] 4.4 Run existing tests to ensure nothing breaks
- [x] 4.5 Commit

## 5. Update documentation

- [x] 5.1 Update README.md with YouTube usage example, file table entry, and pipeline note
- [x] 5.2 Update CLAUDE.md with the same changes
- [x] 5.3 Commit

## 6. End-to-end manual test

- [x] 6.1 Run the full pipeline on a YouTube video (jNQXAC9IVRw "Me at the zoo") and verify JSON, HTML, and source attribution
- [x] 6.2 Verify existing audio file pipeline still works

# transcript-library

## ADDED Requirements

### Requirement: Managed library directory
The engine SHALL store job artifacts under a configurable library directory (default `~/PodcastReader/`), with each source's artifacts grouped under a directory derived from the source identity.

#### Scenario: Artifacts land in the library
- **WHEN** an engine job completes
- **THEN** its JSON, optional chapters, and HTML artifacts exist under the library directory keyed by source identity, not under the process working directory

### Requirement: Source-identity keying
Library entries SHALL be keyed by source identity — a hash of the canonical URL for remote sources, or a content hash for local files — so distinct sources with identical filenames cannot collide.

#### Scenario: Same-named local files do not collide
- **WHEN** two different local files both named `episode.mp3` are processed
- **THEN** they produce two distinct library entries with distinct artifact directories

#### Scenario: Same URL reuses the entry
- **WHEN** the same URL is submitted twice
- **THEN** the second job reuses the first entry's cached artifacts

### Requirement: Atomic, single-writer index
The engine SHALL be the sole writer of the library index, and every index write SHALL be atomic (write temp file, then rename). The CLI one-shot mode SHALL NOT write to the index.

#### Scenario: Index intact after crash during write
- **WHEN** the engine is killed during an index write
- **THEN** the previous index version is still readable on next start

### Requirement: Staged artifact writes
Engine pipeline steps SHALL produce artifacts in a staging location and move them into the library entry atomically on step completion, so a crash mid-write never leaves a torn artifact in the entry directory.

#### Scenario: Crash mid-transcription leaves no torn artifact
- **WHEN** the engine is killed while a step is writing an artifact
- **THEN** the library entry contains either the complete artifact or none, never a partial file

### Requirement: Cache re-validation
A cached artifact SHALL only be treated as a cache hit if it validates (JSON parses; HTML is non-empty). Invalid artifacts SHALL be discarded and regenerated rather than served or crashing the job.

#### Scenario: Truncated JSON is a cache miss
- **WHEN** a job runs for a source whose cached transcript JSON is corrupt
- **THEN** the artifact is regenerated and the job completes successfully

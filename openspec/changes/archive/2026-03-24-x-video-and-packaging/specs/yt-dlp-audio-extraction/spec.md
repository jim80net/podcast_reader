## ADDED Requirements

### Requirement: Audio extraction from any yt-dlp supported URL
The system SHALL support downloading/extracting audio as mp3 from X/Twitter, Vimeo, TikTok, direct audio links, and any other platform supported by yt-dlp.

#### Scenario: X/Twitter status URL
- **WHEN** user provides a https://x.com/user/status/123456 URL
- **THEN** yt-dlp extracts the audio to an mp3 file named after the platform ID

#### Scenario: Cookies for authenticated content
- **WHEN** `YT_DLP_COOKIES` env var points to a cookies file
- **THEN** yt-dlp is invoked with `--cookies` and can access private/age-restricted media

#### Scenario: Title fetching
- **WHEN** a URL is provided without an explicit title
- **THEN** `yt-dlp --print title` is used to obtain a reasonable default title

### Requirement: yt-dlp failures produce actionable errors
On yt-dlp failure, the CLI SHALL surface the stderr and, for auth errors, suggest the cookies configuration.

#### Scenario: Login required error
- **WHEN** yt-dlp reports "login required" or similar
- **THEN** the error message includes a hint about setting YT_DLP_COOKIES

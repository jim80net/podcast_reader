$ErrorActionPreference = "Stop"

$dataDir = if ($env:PODCAST_READER_DATA_DIR) {
    [System.IO.Path]::GetFullPath($env:PODCAST_READER_DATA_DIR)
} else {
    Join-Path $HOME "PodcastReader"
}
$settingsPath = Join-Path $dataDir "settings.json"
$libraryDir = Join-Path $dataDir "library"
if (Test-Path -LiteralPath $settingsPath) {
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    if ($settings.library_dir) {
        $libraryDir = [string]$settings.library_dir
    }
}
$indexPath = Join-Path $libraryDir "library.json"
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "Podcast Reader library index was not found. Open the installed app once, then retry."
}

$entries = @(Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json)
$measurements = @(
    foreach ($entry in $entries) {
        $artifact = [string]$entry.html_path
        if ($artifact -and (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            [pscustomobject]@{ exists = $true; bytes = (Get-Item -LiteralPath $artifact).Length }
        } else {
            [pscustomobject]@{ exists = $false; bytes = 0 }
        }
    }
)
$sizes = @($measurements | Where-Object exists | ForEach-Object bytes | Sort-Object)

function Get-Percentile([long[]]$Values, [double]$Percentile) {
    if ($Values.Count -eq 0) { return 0 }
    $index = [Math]::Ceiling($Percentile * $Values.Count) - 1
    return $Values[[Math]::Max(0, $index)]
}

$maxArtifacts = 500
$maxArtifactBytes = 2MB
$maxTotalBytes = 32MB
$visited = 0
$accepted = 0
$scanBytes = [long]0
$truncated = $false
for ($index = $measurements.Count - 1; $index -ge 0; $index--) {
    if ($visited -ge $maxArtifacts) {
        $truncated = $true
        break
    }
    $visited++
    $item = $measurements[$index]
    if (-not $item.exists -or $item.bytes -gt $maxArtifactBytes) { continue }
    if ($item.bytes -gt ($maxTotalBytes - $scanBytes)) {
        $truncated = $true
        break
    }
    $accepted++
    $scanBytes += $item.bytes
}
if ($visited -lt $measurements.Count) { $truncated = $true }

[pscustomobject]@{
    schema = "podcast-reader-search-capacity-v1"
    indexed_artifacts = $entries.Count
    existing_artifacts = $sizes.Count
    missing_artifacts = @($measurements | Where-Object { -not $_.exists }).Count
    total_html_bytes = [long](($sizes | Measure-Object -Sum).Sum)
    median_html_bytes = [long](Get-Percentile $sizes 0.50)
    p95_html_bytes = [long](Get-Percentile $sizes 0.95)
    max_html_bytes = [long](Get-Percentile $sizes 1.00)
    artifacts_over_2_mib = @($sizes | Where-Object { $_ -gt $maxArtifactBytes }).Count
    simulated_newest_first = [pscustomobject]@{
        visited = $visited
        accepted = $accepted
        bytes = $scanBytes
        partial = $truncated
    }
    shipped_limits = [pscustomobject]@{
        artifacts = $maxArtifacts
        bytes_per_artifact = $maxArtifactBytes
        aggregate_bytes = $maxTotalBytes
        wall_clock_seconds = 1.5
    }
} | ConvertTo-Json -Depth 3

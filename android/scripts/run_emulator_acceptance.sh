#!/usr/bin/env bash
set -euo pipefail

android_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
evidence_dir="${1:?usage: run_emulator_acceptance.sh EVIDENCE_DIRECTORY}"
mkdir -p "$evidence_dir"

cd "$android_dir"
adb logcat -c
./gradlew --no-daemon --stacktrace connectedDebugAndroidTest

adb logcat -d -v threadtime > "$evidence_dir/logcat.txt"
adb shell dumpsys activity activities > "$evidence_dir/activity.txt"
adb shell dumpsys window windows > "$evidence_dir/windows.txt"
adb shell dumpsys clipboard > "$evidence_dir/clipboard.txt" 2>&1 || true
adb exec-out screencap -p > "$evidence_dir/screenshot.png"
adb exec-out run-as net.jim80.podcastreader tar -cf - . > "$evidence_dir/package-data.tar"

scan_roots=(
    "$evidence_dir"
    "$android_dir/app/build/outputs/androidTest-results/connected"
    "$android_dir/app/build/reports/androidTests/connected"
)
markers=(
    "K4_ENGINE_BEARER_7f4d1a9c2e6b"
    "K4_ENGINE_BEARER_"
    "K4_DEVICE_CODE_9b3e7c1a5d8f"
    "K4_DEVICE_CODE_"
    "K4_PREMIUM_ACCESS_2c8e4a6f1d7b"
    "K4_PREMIUM_ACCESS_"
    "K4_PREMIUM_REFRESH_6d1f9a3c7e2b"
    "K4_PREMIUM_REFRESH_"
)

for marker in "${markers[@]}"; do
    if grep --binary-files=text --recursive --files-with-matches --fixed-strings \
        -- "$marker" "${scan_roots[@]}" > /dev/null; then
        echo "::error::Android K4 sweep found a synthetic secret marker in captured evidence"
        exit 1
    fi
done

printf 'Android K4 sweep passed: %s full/prefix markers absent from device evidence\n' "${#markers[@]}"

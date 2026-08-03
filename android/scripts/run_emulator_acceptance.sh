#!/usr/bin/env bash
set -euo pipefail

android_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
evidence_dir="$(realpath -m "${1:?usage: run_emulator_acceptance.sh EVIDENCE_DIRECTORY}")"
case "$evidence_dir" in
    "$android_dir/build/acceptance/"*) ;;
    *) echo "refusing evidence directory outside android/build/acceptance" >&2; exit 2 ;;
esac
mkdir -p "$evidence_dir"
rm -f -- "$evidence_dir/SAFE_TO_UPLOAD"

cd "$android_dir"
adb logcat -c
set +e
./gradlew --no-daemon --stacktrace connectedDebugAndroidTest
gradle_status=$?
set -e

collection_status=0
adb logcat -d -v threadtime > "$evidence_dir/logcat.txt" || collection_status=1
adb shell dumpsys activity activities > "$evidence_dir/activity.txt" || collection_status=1
adb shell dumpsys window windows > "$evidence_dir/windows.txt" || collection_status=1
adb shell dumpsys clipboard > "$evidence_dir/clipboard.txt" 2>&1 || true
adb exec-out screencap -p > "$evidence_dir/screenshot.png" || collection_status=1
adb exec-out run-as net.jim80.podcastreader tar -cf - . > "$evidence_dir/package-data.tar" || collection_status=1

scan_roots=(
    "$evidence_dir"
    "$android_dir/app/build/outputs/androidTest-results/connected"
    "$android_dir/app/build/reports/androidTests/connected"
)
upload_roots=("${scan_roots[@]}")

purge_upload_roots() {
    local root
    for root in "${upload_roots[@]}"; do
        case "$root" in
            "$android_dir/build/acceptance/"*|\
            "$android_dir/app/build/outputs/androidTest-results/connected"|\
            "$android_dir/app/build/reports/androidTests/connected")
                chmod -R u+w -- "$root" 2>/dev/null || true
                rm -rf -- "$root"
                ;;
            *)
                echo "::error::Refusing to purge an unbounded Android evidence path"
                return 1
                ;;
        esac
    done
    for root in "${upload_roots[@]}"; do
        if [[ -e "$root" ]]; then
            echo "::error::Android evidence quarantine could not remove an upload root"
            return 1
        fi
    done
}

marker_source="$android_dir/app/src/androidTest/java/net/jim80/podcastreader/acceptance/AcceptanceSecrets.kt"
set +e
marker_output="$(python3 "$android_dir/scripts/extract_android_k4_markers.py" "$marker_source")"
marker_status=$?
set -e
if [[ $marker_status -ne 0 ]]; then
    purge_upload_roots
    echo "::error::Android K4 sweep could not construct its marker list"
    exit 1
fi
mapfile -t markers <<< "$marker_output"

for marker in "${markers[@]}"; do
    existing_scan_roots=()
    for root in "${scan_roots[@]}"; do
        [[ -e "$root" ]] && existing_scan_roots+=("$root")
    done
    [[ ${#existing_scan_roots[@]} -gt 0 ]] || continue
    set +e
    grep --binary-files=text --recursive --files-with-matches --fixed-strings \
        -- "$marker" "${existing_scan_roots[@]}" > /dev/null
    grep_status=$?
    set -e
    if [[ $grep_status -eq 0 ]]; then
        purge_upload_roots
        echo "::error::Android K4 sweep quarantined evidence containing a secret marker"
        exit 1
    elif [[ $grep_status -gt 1 ]]; then
        collection_status=1
    fi
done

if [[ $collection_status -ne 0 ]]; then
    purge_upload_roots
    echo "::error::Android acceptance evidence was incomplete or could not be fully swept"
    if [[ $gradle_status -ne 0 ]]; then
        exit "$gradle_status"
    fi
    exit "$collection_status"
fi

printf 'Android K4 sweep passed: %s full/prefix markers absent from device evidence\n' "${#markers[@]}"
: > "$evidence_dir/SAFE_TO_UPLOAD"
if [[ $gradle_status -ne 0 ]]; then
    exit "$gradle_status"
fi
exit "$collection_status"

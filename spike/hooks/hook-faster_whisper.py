"""PyInstaller hook for faster_whisper (none ships in pyinstaller-hooks-contrib 2026.6).

faster_whisper bundles the Silero VAD model as package data
(`faster_whisper/assets/silero_vad_v6.onnx`); without collecting it the
frozen worker crashes as soon as vad_filter or default VAD paths touch it.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("faster_whisper")

"""PyInstaller hook for ctranslate2 (none ships in pyinstaller-hooks-contrib 2026.6).

Why this is needed:
- Linux wheels put libctranslate2/libgomp in a sibling `ctranslate2.libs/`
  directory (auditwheel layout). PyInstaller's ldd-based binary analysis of
  `ctranslate2/_ext.*.so` normally finds them, but it relocates them to the
  bundle root; collecting them explicitly keeps the RPATH-relative layout the
  extension expects ($ORIGIN/../ctranslate2.libs).
- Windows wheels put `ctranslate2.dll` (and on CUDA-enabled loads, expect
  cublas/cudnn resolvable via the DLL search path) inside the package dir;
  `ctranslate2/__init__.py` calls os.add_dll_directory(package_dir) and
  ctypes.CDLL on every *.dll there, so the DLLs MUST stay inside the
  `ctranslate2/` directory in the frozen bundle — collect_dynamic_libs
  preserves that package-relative placement.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

binaries = collect_dynamic_libs("ctranslate2")
datas = collect_data_files("ctranslate2")

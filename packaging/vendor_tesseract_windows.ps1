# Vendors a copy of a Chocolatey-installed Tesseract install, trimmed to
# tesseract.exe's real dependency closure + English tessdata, into
# packaging/vendor/windows/tesseract/, for PyInstaller to bundle as
# resources/tesseract/ (see qurio-aug.spec and qurio_aug/tesseract_setup.py).
# Run this before `pyinstaller packaging/qurio-aug.spec`.
#
# Requires: choco install tesseract -y, and a Python with pefile on PATH
# (already guaranteed by this point -- pefile is a hard PyInstaller
# dependency, and PyInstaller is a documented prerequisite for this whole
# packaging step; see README's "Building the binary yourself").
#
# Used to just copy the *entire* Tesseract-OCR install directory rather
# than hand-picking DLLs, matching the reasoning that unlike the macOS
# vendoring script (which uses dylibbundler to compute the exact
# dependency closure automatically), there was no way to verify from
# macOS which subset of DLLs were actually load-bearing -- copying
# everything was the defensively-correct choice given zero Windows test
# access. Confirmed on real Windows hardware: trim_tesseract_windows.py's
# PE-import-table walk (same idea as dylibbundler, just for PE instead of
# Mach-O) finds tesseract.exe's real closure cleanly, excluding ~110MB of
# a ~229MB raw install -- a dozen model-training tools this project never
# invokes (text2image.exe, lstmtraining.exe, mftraining.exe, etc.) plus
# the large ICU/Pango/Cairo stack only *those* tools need, not OCR itself.
# If choco's tesseract package ever breaks/drifts, the documented
# fallback is installing via the official UB-Mannheim installer instead
# and pointing $TesseractInstallDir at wherever that puts it.
$ErrorActionPreference = "Stop"

$TesseractInstallDir = "C:\Program Files\Tesseract-OCR"
$OutDir = Join-Path $PSScriptRoot "vendor\windows\tesseract"
$RawDir = Join-Path $PSScriptRoot "vendor\windows\_tesseract_raw"

if (-not (Test-Path $TesseractInstallDir)) {
    throw "$TesseractInstallDir not found -- run 'choco install tesseract -y' first"
}

if (Test-Path $OutDir) {
    Remove-Item -Recurse -Force $OutDir
}
if (Test-Path $RawDir) {
    Remove-Item -Recurse -Force $RawDir
}
New-Item -ItemType Directory -Force -Path $RawDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Copy-Item -Path (Join-Path $TesseractInstallDir "*") -Destination $RawDir -Recurse -Force

# Prefer an activated venv's own python over a bare "python" on PATH --
# pefile (trim_tesseract_windows.py's only dependency) is only guaranteed
# to be present in whichever environment PyInstaller itself was installed
# into, which may not be what "python" resolves to on PATH generally.
$PythonExe = if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV "Scripts\python.exe" } else { "python" }
& $PythonExe (Join-Path $PSScriptRoot "trim_tesseract_windows.py") $RawDir $OutDir
if ($LASTEXITCODE -ne 0) {
    throw "trim_tesseract_windows.py failed"
}

# tessdata isn't a dependency tesseract.exe imports at link time (it's
# data, loaded by path at runtime), so the dependency walk above never
# sees it -- copied separately here, same as before, keeping only
# English to match the macOS vendoring script's scope (no language
# selection UI exists in this project, so shipping every language is
# pure bloat).
Copy-Item -Path (Join-Path $RawDir "tessdata") -Destination $OutDir -Recurse -Force
Remove-Item -Recurse -Force $RawDir

$TessdataDir = Join-Path $OutDir "tessdata"
Get-ChildItem $TessdataDir -Filter "*.traineddata" | Where-Object { $_.Name -ne "eng.traineddata" } | Remove-Item -Force

Write-Host "vendored tesseract -> $OutDir"
& (Join-Path $OutDir "tesseract.exe") --version

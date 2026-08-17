# Vendors a copy of a Chocolatey-installed Tesseract install (binary +
# every adjacent DLL + English tessdata) into
# packaging/vendor/windows/tesseract/, for PyInstaller to bundle as
# resources/tesseract/ (see qurio-aug.spec and qurio_aug/tesseract_setup.py).
# Run this before `pyinstaller packaging/qurio-aug.spec`.
#
# Requires: choco install tesseract -y
#
# Unlike the macOS vendoring script (which uses dylibbundler to copy only
# the exact dependency closure), this copies the *entire* Tesseract-OCR
# install directory rather than hand-picking DLLs -- there is no way to
# verify from macOS which subset of DLLs (particularly VC++ redistributable
# -adjacent ones) are actually load-bearing, so copying everything is the
# defensively-correct choice given zero Windows test access for this
# project. If choco's tesseract package ever breaks/drifts, the documented
# fallback is installing via the official UB-Mannheim installer instead
# and pointing $TesseractInstallDir at wherever that puts it.
$ErrorActionPreference = "Stop"

$TesseractInstallDir = "C:\Program Files\Tesseract-OCR"
$OutDir = Join-Path $PSScriptRoot "vendor\windows\tesseract"

if (-not (Test-Path $TesseractInstallDir)) {
    throw "$TesseractInstallDir not found -- run 'choco install tesseract -y' first"
}

if (Test-Path $OutDir) {
    Remove-Item -Recurse -Force $OutDir
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Copy-Item -Path (Join-Path $TesseractInstallDir "*") -Destination $OutDir -Recurse -Force

# Chocolatey's tesseract package ships tessdata already; keep only English
# to match the macOS vendoring script's scope (no language selection UI
# exists in this project, so shipping every language is pure bloat).
$TessdataDir = Join-Path $OutDir "tessdata"
if (Test-Path $TessdataDir) {
    Get-ChildItem $TessdataDir -Filter "*.traineddata" | Where-Object { $_.Name -ne "eng.traineddata" } | Remove-Item -Force
}

Write-Host "vendored tesseract -> $OutDir"
& (Join-Path $OutDir "tesseract.exe") --version

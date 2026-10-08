$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    Copy-Item -LiteralPath README.md -Destination assets/README.md -Force
    Copy-Item -LiteralPath THIRD_PARTY_NOTICES.md -Destination assets/THIRD_PARTY_NOTICES.md -Force
    $assetPath = Join-Path $PSScriptRoot 'assets'
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name NationPythonQCSetup --distpath dist --workpath build --specpath . --paths src --add-data "$assetPath;assets" --hidden-import win32timezone src/app.py
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    Get-FileHash -LiteralPath dist/NationPythonQCSetup.exe -Algorithm SHA256 | Format-List
} finally { Pop-Location }

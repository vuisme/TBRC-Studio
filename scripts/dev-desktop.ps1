$ErrorActionPreference = "Stop"

$vsDevCmd = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if (-not (Test-Path -LiteralPath $vsDevCmd)) {
  throw "Visual Studio Build Tools launcher not found: $vsDevCmd"
}

$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
$env:TAURI_SKIP_BACKEND = "1"

& cmd.exe /d /s /c "call `"$vsDevCmd`" -arch=x64 && bun run --cwd frontend desktop"
exit $LASTEXITCODE

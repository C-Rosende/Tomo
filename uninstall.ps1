param([switch]$DeleteModels)
$ErrorActionPreference = 'Stop'
$regPath = 'HKCU:\Software\Mozilla\NativeMessagingHosts\local.tomo'
if (Test-Path $regPath) { Remove-Item -Force $regPath }
foreach ($vendor in 'Google\Chrome', 'Microsoft\Edge', 'BraveSoftware\Brave-Browser') {
    Remove-Item -Force "HKCU:\Software\$vendor\NativeMessagingHosts\local.tomo" -ErrorAction SilentlyContinue
}
$dst = Join-Path $env:LOCALAPPDATA 'Tomo'
if (Test-Path $dst) { [System.IO.Directory]::Delete($dst, $true) }
if ($DeleteModels) {
    $hub = Join-Path $env:USERPROFILE '.cache\huggingface\hub'
    Get-ChildItem $hub -Directory -Filter 'models--Systran--faster-whisper-*' -ErrorAction SilentlyContinue |
        ForEach-Object { [System.IO.Directory]::Delete($_.FullName, $true); Write-Host "Model removed: $($_.Name)" }
    Get-ChildItem $hub -Directory -Filter 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo' -ErrorAction SilentlyContinue |
        ForEach-Object { [System.IO.Directory]::Delete($_.FullName, $true); Write-Host "Model removed: $($_.Name)" }
}
Write-Host 'Tomo helper removed. Remove the browser extension if it is still installed.'

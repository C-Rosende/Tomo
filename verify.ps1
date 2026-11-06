$ErrorActionPreference = 'Stop'
$dst = Join-Path $env:LOCALAPPDATA 'Tomo'
$ok = $true
function Check([string]$name, [bool]$pass, [string]$detail = '') {
    Write-Host ("[{0}] {1} {2}" -f $(if ($pass) { 'OK' } else { 'FAIL' }), $name, $detail) -ForegroundColor $(if ($pass) { 'Green' } else { 'Red' })
    if (-not $pass) { $script:ok = $false }
}
if (-not (Test-Path (Join-Path $dst 'install.json'))) {
    Write-Host 'The Tomo helper is not installed. Follow the install steps at https://github.com/C-Rosende/Tomo#-install' -ForegroundColor Red
    exit 1
}
$info = Get-Content (Join-Path $dst 'install.json') -Raw | ConvertFrom-Json
foreach ($p in $info.files.PSObject.Properties) {
    $path = Join-Path $dst $p.Name
    $hash = if (Test-Path $path) { (Get-FileHash $path -Algorithm SHA256).Hash } else { '' }
    Check "Integrity $($p.Name)" ($hash -eq $p.Value)
}
$config = Get-Content (Join-Path $dst 'config.json') -Raw | ConvertFrom-Json
foreach ($tool in 'ffmpeg', 'ffprobe', 'deno', 'node') {
    $path = $config.$tool
    if ($path) { Check "Tool $tool" (Test-Path -LiteralPath $path -PathType Leaf) $path }
}
if (-not $config.ffmpeg) { Write-Warning 'FFmpeg is not configured: merging and conversion are unavailable.' }
$regItem = Get-Item 'HKCU:\Software\Mozilla\NativeMessagingHosts\local.tomo' -ErrorAction SilentlyContinue
$reg = if ($regItem) { $regItem.GetValue('') } else { '' }
Check 'Native Messaging registration' ($reg -eq (Join-Path $dst 'native-host.json')) $reg
$manifest = Get-Content (Join-Path $dst 'native-host.json') -Raw | ConvertFrom-Json
Check 'Allowed extension ID' ($manifest.allowed_extensions -contains 'tomo@tomo.app')
if ($info.files.PSObject.Properties.Name -contains 'native-host-chromium.json') {
    $chromium = Get-Content (Join-Path $dst 'native-host-chromium.json') -Raw | ConvertFrom-Json
    $expectedOrigins = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'chromium-ids.txt') |
        ForEach-Object { ($_ -replace '#.*', '').Trim() } | Where-Object { $_ -match '^[a-p]{32}$' } |
        ForEach-Object { "chrome-extension://$_/" } | Sort-Object -Unique)
    $actualOrigins = @($chromium.allowed_origins | Sort-Object -Unique)
    Check 'Allowed Chrome origins' ($expectedOrigins.Count -gt 0 -and
        $actualOrigins.Count -eq $expectedOrigins.Count -and
        -not (Compare-Object $expectedOrigins $actualOrigins))
    foreach ($vendor in 'Google\Chrome', 'Microsoft\Edge', 'BraveSoftware\Brave-Browser') {
        $item = Get-Item "HKCU:\Software\$vendor\NativeMessagingHosts\local.tomo" -ErrorAction SilentlyContinue
        Check "Registration $vendor" ($item -and $item.GetValue('') -eq (Join-Path $dst 'native-host-chromium.json'))
    }
}
$py = Join-Path $dst '.venv\Scripts\python.exe'
$code = @'
import json, struct, subprocess, sys
m = json.dumps({"action": "ping"}).encode()
p = subprocess.run([sys.executable, "-I", sys.argv[1]], input=struct.pack("<I", len(m)) + m, capture_output=True, timeout=60)
o = p.stdout; r = []
while len(o) >= 4:
    n = struct.unpack("<I", o[:4])[0]; r.append(json.loads(o[4:4 + n])); o = o[4 + n:]
print(json.dumps(r[-1] if r else {}))
'@
$tmp = Join-Path $env:TEMP "vct-verify-$PID.py"
[System.IO.File]::WriteAllText($tmp, $code, (New-Object System.Text.UTF8Encoding($false)))
try { $ping = & $py -I $tmp (Join-Path $dst 'host.py') | ConvertFrom-Json } finally { [System.IO.File]::Delete($tmp) }
Check 'Helper responds' ($ping.status -eq 'done') "v$($ping.version), yt-dlp $($ping.ytdlp), ASR $($ping.asr), FFmpeg $($ping.ffmpeg)"
if (-not $ok) { exit 1 }

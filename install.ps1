param(
    [switch]$WithASR,
    [switch]$Deps,
    [string]$Python,
    [switch]$Metrics
)
$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$dst = Join-Path $env:LOCALAPPDATA 'Tomo'
$hostName = 'local.tomo'
$extensionId = 'tomo@tomo.app'
$files = @('host.py', 'sysdep.py', 'ai.py', 'locales.json', 'host.bat', 'requirements.lock', 'requirements-asr.lock')

function Get-PyVersion([string]$exe) {
    if (-not $exe -or $exe -like '*\WindowsApps\*' -or -not (Test-Path $exe)) { return $null }
    try {
        $v = & $exe -I -c 'import sys; print(*sys.version_info[:2], sep=chr(46))' 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return [version]($v | Select-Object -Last 1) }
    } catch { }
    return $null
}

function Find-Python {
    if ($Python) {
        if ((Get-PyVersion $Python) -ge [version]'3.12') { return $Python }
        throw "The given Python is invalid or older than 3.12: $Python"
    }
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '-3.12', '-3.13', '-3') {
            try { $candidates.Add((& py $v -c 'import sys; print(sys.executable)' 2>$null | Select-Object -Last 1)) } catch { }
        }
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        foreach ($v in '3.12', '3.13') {
            try { $candidates.Add((& uv python find $v 2>$null | Select-Object -Last 1)) } catch { }
        }
    }
    foreach ($name in 'python', 'python3') {
        Get-Command $name -All -ErrorAction SilentlyContinue | ForEach-Object { $candidates.Add($_.Source) }
    }
    $valid = foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        $ver = Get-PyVersion $c
        if ($ver -ge [version]'3.12') { [pscustomobject]@{ Path = $c; Version = $ver; Rank = [int]($ver -gt [version]'3.13') } }
    }
    $best = $valid | Sort-Object Rank, @{ e = { $_.Version }; Descending = $true } | Select-Object -First 1
    if (-not $best -and $Deps) {
        winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
        $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (Test-Path $py) { return $py }
    }
    if (-not $best) { throw 'Python 3.12+ is required (for example: winget install -e --id Python.Python.3.12). Install it and run this again.' }
    return $best.Path
}

function Find-Tool([string]$name, [string]$wingetId) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    $pkgs = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $pkgs) {
        $hit = Get-ChildItem $pkgs -Directory -Filter "$wingetId*" -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ChildItem $_.FullName -Recurse -Filter "$name.exe" -ErrorAction SilentlyContinue } |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Invoke-SelfTest([string]$py, [string]$hostPy) {
    $test = @'
import json, struct, subprocess, sys
msg = json.dumps({"action": "ping"}).encode()
p = subprocess.run([sys.executable, "-I", sys.argv[1]], input=struct.pack("<I", len(msg)) + msg,
                   capture_output=True, timeout=120)
out, msgs = p.stdout, []
while len(out) >= 4:
    n = struct.unpack("<I", out[:4])[0]; msgs.append(json.loads(out[4:4 + n])); out = out[4 + n:]
done = [m for m in msgs if m.get("status") == "done"]
print(json.dumps(done[-1] if done else {"error": p.stderr.decode(errors="replace")[-300:]}))
'@
    $tmp = Join-Path $env:TEMP "vct-selftest-$PID.py"
    [System.IO.File]::WriteAllText($tmp, $test, (New-Object System.Text.UTF8Encoding($false)))
    try { return (& $py -I $tmp $hostPy | Select-Object -Last 1 | ConvertFrom-Json) }
    finally { [System.IO.File]::Delete($tmp) }
}

$ffmpeg = Find-Tool 'ffmpeg' 'Gyan.FFmpeg'
$deno = Find-Tool 'deno' 'DenoLand.Deno'
$node = (Get-Command node -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if ($Deps) {
    if (-not $ffmpeg) { winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements; $ffmpeg = Find-Tool 'ffmpeg' 'Gyan.FFmpeg' }
    if (-not $deno -and -not $node) { winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements; $deno = Find-Tool 'deno' 'DenoLand.Deno' }
    if (-not $ffmpeg) { throw 'FFmpeg could not be installed (needed to merge audio and video and to convert). Install it with: winget install -e --id Gyan.FFmpeg and run this again.' }
}

$python = Find-Python
Write-Host "Python base: $python"

New-Item -ItemType Directory -Force -Path $dst | Out-Null
$backup = Join-Path $dst 'backup'
$hadPrevious = Test-Path (Join-Path $dst 'host.py')
if ($hadPrevious) {
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    foreach ($f in $files + @('config.json')) { Copy-Item -Force (Join-Path $dst $f) (Join-Path $backup $f) -ErrorAction SilentlyContinue }
}
trap {
    if ($hadPrevious -and (Test-Path $backup)) {
        foreach ($f in $files + @('config.json')) { Copy-Item -Force (Join-Path $backup $f) (Join-Path $dst $f) -ErrorAction SilentlyContinue }
    }
    break
}
foreach ($f in $files) { Copy-Item -Force (Join-Path $src "backend\$f") (Join-Path $dst $f) }

$venvPython = Join-Path $dst '.venv\Scripts\python.exe'
if (-not (Get-PyVersion $venvPython)) {
    if (Test-Path (Join-Path $dst '.venv')) { [System.IO.Directory]::Delete((Join-Path $dst '.venv'), $true) }
    & $python -m venv (Join-Path $dst '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
$pip = @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--upgrade', '--require-hashes', '--only-binary=:all:')
$pinnedYtdlp = [version]'2026.10.26'
$installedYtdlp = & $venvPython -I -c 'import importlib.metadata as m; print(m.version("yt-dlp") if m.packages_distributions().get("yt_dlp") else "0.0")'
if ([version]$installedYtdlp -gt $pinnedYtdlp) { throw "Refusing to downgrade yt-dlp $installedYtdlp to $pinnedYtdlp." }
Write-Host 'Installing yt-dlp and its official dependencies...'
& $venvPython @pip -r (Join-Path $src 'backend\requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Could not install yt-dlp.' }
$hasAsr = (& $venvPython -I -c 'import importlib.util as u; print(bool(u.find_spec(''faster_whisper'')))') -eq 'True'
if ($WithASR -or $hasAsr) {
    Write-Host 'Installing local transcription (faster-whisper, CPU)...'
    $installedAsr = & $venvPython -I -c 'import importlib.metadata as m; print(m.version("faster-whisper") if m.packages_distributions().get("faster_whisper") else "0.0")'
    if ([version]$installedAsr -gt [version]'1.2.1') { throw "Refusing to downgrade faster-whisper $installedAsr to 1.2.1." }
    & $venvPython @pip -r (Join-Path $src 'backend\requirements-asr.lock')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install faster-whisper.' }
}

$config = [ordered]@{}
$previousConfig = Join-Path $dst 'config.json'
$keepMetrics = (Test-Path $previousConfig) -and ((Get-Content $previousConfig -Raw | ConvertFrom-Json).metrics -eq $true)
if ($Metrics -or $keepMetrics) { $config.metrics = $true }
if ($ffmpeg) {
    $config.ffmpeg = $ffmpeg
    $probe = Join-Path (Split-Path $ffmpeg) 'ffprobe.exe'
    if (Test-Path $probe) { $config.ffprobe = $probe }
}
if ($deno) { $config.deno = $deno } elseif ($node) { $config.node = $node }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $dst 'config.json'), ($config | ConvertTo-Json), $utf8)

$result = Invoke-SelfTest $venvPython (Join-Path $dst 'host.py')
if (-not $result -or $result.status -ne 'done' -or $result.protocol -ne 2) {
    if ($hadPrevious) {
        foreach ($f in $files + @('config.json')) { Copy-Item -Force (Join-Path $backup $f) (Join-Path $dst $f) -ErrorAction SilentlyContinue }
        throw "Self-test failed; the previous version was restored. Details: $($result | ConvertTo-Json -Compress)"
    }
    throw "Self-test failed: $($result | ConvertTo-Json -Compress)"
}

$manifest = [ordered]@{
    name = $hostName
    description = 'Tomo local helper (downloads and transcription)'
    path = (Join-Path $dst 'host.bat')
    type = 'stdio'
    allowed_extensions = @($extensionId)
}
$manifestPath = Join-Path $dst 'native-host.json'
[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5), $utf8)
$regPath = "HKCU:\Software\Mozilla\NativeMessagingHosts\$hostName"
New-Item -Path $regPath -Force | Out-Null
Set-Item -Path $regPath -Value $manifestPath

$chromiumIds = @(Get-Content (Join-Path $src 'chromium-ids.txt') -ErrorAction SilentlyContinue |
    ForEach-Object { ($_ -replace '#.*', '').Trim() } | Where-Object { $_ -match '^[a-p]{32}$' })
$chromiumManifest = Join-Path $dst 'native-host-chromium.json'
$chromiumRoots = 'Google\Chrome', 'Microsoft\Edge', 'BraveSoftware\Brave-Browser'
if ($chromiumIds.Count) {
    $chromium = [ordered]@{
        name = $hostName
        description = $manifest.description
        path = $manifest.path
        type = 'stdio'
        allowed_origins = @($chromiumIds | ForEach-Object { "chrome-extension://$_/" })
    }
    [System.IO.File]::WriteAllText($chromiumManifest, ($chromium | ConvertTo-Json -Depth 5), $utf8)
    foreach ($vendor in $chromiumRoots) {
        $key = "HKCU:\Software\$vendor\NativeMessagingHosts\$hostName"
        New-Item -Path $key -Force | Out-Null
        Set-Item -Path $key -Value $chromiumManifest
    }
} else {
    Remove-Item -Force $chromiumManifest -ErrorAction SilentlyContinue
    foreach ($vendor in $chromiumRoots) { Remove-Item -Force "HKCU:\Software\$vendor\NativeMessagingHosts\$hostName" -ErrorAction SilentlyContinue }
}
$venvHome = (Get-Content (Join-Path $dst '.venv\pyvenv.cfg') | Where-Object { $_ -match '^home\s*=' }) -replace '^home\s*=\s*', ''
$integrity = [ordered]@{ version = $result.version; installed = (Get-Date).ToString('s'); python = $venvHome; files = [ordered]@{} }
foreach ($f in $files + @('config.json', 'native-host.json') + @(if ($chromiumIds.Count) { 'native-host-chromium.json' })) { $integrity.files[$f] = (Get-FileHash (Join-Path $dst $f) -Algorithm SHA256).Hash }
[System.IO.File]::WriteAllText((Join-Path $dst 'install.json'), ($integrity | ConvertTo-Json -Depth 4), $utf8)

Write-Host "`nTomo helper $($result.version) installed in $dst" -ForegroundColor Green
Write-Host "yt-dlp $($result.ytdlp), local ASR: $(if ($result.asr) { "yes ($($result.faster_whisper))" } else { 'no (use -WithASR)' })"
if (-not $ffmpeg) { Write-Warning 'FFmpeg is missing: run this script with -Deps or: winget install -e --id Gyan.FFmpeg' }
if (-not $deno -and -not $node) { Write-Warning 'No Deno/Node: YouTube may fail. Use -Deps or: winget install -e --id DenoLand.Deno' }
if (Test-Path $backup) { [System.IO.Directory]::Delete($backup, $true) }
Write-Host 'Done. Go back to your browser and open Tomo (click its icon); it will find the helper by itself.' -ForegroundColor Green
if ($chromiumIds.Count) { Write-Host "Chrome, Edge and Brave: helper registered for $($chromiumIds.Count) extension(s)." }

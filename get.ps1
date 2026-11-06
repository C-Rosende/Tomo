param([switch]$WithASR)
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$releaseTag = 'v0.7.12'
$minisignPublicKey = 'REPLACE_WITH_MINISIGN_PUBLIC_KEY'
if ($minisignPublicKey -like 'REPLACE_*') { throw 'Release public key not configured; installation refused.' }
$minisign = Get-Command minisign -ErrorAction SilentlyContinue
if (-not $minisign) { throw 'minisign is required to verify this release.' }
$work = Join-Path ([IO.Path]::GetTempPath()) ("tomo-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work 'tomo.zip'
    $release = Invoke-RestMethod "https://api.github.com/repos/C-Rosende/Tomo/releases/tags/$releaseTag" -UseBasicParsing
    $tag = $release.tag_name
    if ($tag -ne $releaseTag) { throw 'The pinned release could not be verified.' }
    $asset = $release.assets | Where-Object { $_.name -eq "tomo-source-$($tag.Substring(1)).zip" } | Select-Object -First 1
    $sums = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1
    $signature = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS.minisig' } | Select-Object -First 1
    if (-not $asset -or -not $sums -or -not $signature) { throw 'The pinned release is incomplete. Try again later.' }
    $sumsFile = Join-Path $work 'SHA256SUMS'
    $signatureFile = Join-Path $work 'SHA256SUMS.minisig'
    Invoke-WebRequest $sums.browser_download_url -OutFile $sumsFile -UseBasicParsing
    Invoke-WebRequest $signature.browser_download_url -OutFile $signatureFile -UseBasicParsing
    & $minisign.Source -Vm $sumsFile -x $signatureFile -P $minisignPublicKey
    if ($LASTEXITCODE -ne 0) { throw 'Release signature is invalid. Nothing was installed.' }
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
    $expected = ((Get-Content $sumsFile) |
        Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset.name) + '$') } | Select-Object -First 1) -split '\s+' | Select-Object -First 1
    if (-not $expected -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne $expected.ToUpper()) {
        throw 'The downloaded file does not match its checksum. Nothing was installed.'
    }
    Expand-Archive $zip -DestinationPath $work -Force
    $root = Get-ChildItem $work -Directory | Select-Object -First 1
    $installArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root.FullName 'install.ps1'), '-Deps')
    if ($WithASR) { $installArgs += '-WithASR' }
    & (Get-Process -Id $PID).Path @installArgs
    if ($LASTEXITCODE -ne 0) { throw 'The installation did not finish. Read the message above, fix it and run the command again.' }
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    $ErrorActionPreference = $previousErrorAction
}

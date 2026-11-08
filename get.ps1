param([switch]$WithASR, [string]$VerifyFile, [string]$SignatureFile, [string]$PublicKey, [string]$WorkRoot)
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$releaseTag = 'v0.8.1'
$minisignPublicKey = 'RWQXJrNEc6cEHhGs904MZvkENTW2X08w1vRovftHHpo41XQeto/MRc+V'
$minisignArchiveUrl = 'https://github.com/jedisct1/minisign/releases/download/0.12/minisign-0.12-win64.zip'
$minisignArchiveSha256 = '37B600344E20C19314B2E82813DB2BFDCC408B77B876F7727889DBD46D539479'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Tomo requires 64-bit Windows. Nothing was downloaded or installed.' }
$work = Join-Path $(if ($WorkRoot) { $WorkRoot } else { [IO.Path]::GetTempPath() }) ("tomo-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $minisignArchive = Join-Path $work 'minisign.zip'
    Invoke-WebRequest $minisignArchiveUrl -OutFile $minisignArchive -UseBasicParsing
    if ((Get-FileHash $minisignArchive -Algorithm SHA256).Hash -ne $minisignArchiveSha256) {
        throw 'The signature verifier does not match its pinned checksum. Nothing was installed.'
    }
    Expand-Archive $minisignArchive -DestinationPath $work
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
    $minisign = Join-Path $work "minisign-win64/$arch/minisign.exe"
    if (-not (Test-Path $minisign)) { throw 'The signature verifier is unavailable. Nothing was installed.' }
    if ($VerifyFile) {
        if (-not $SignatureFile -or -not $PublicKey) { throw 'Signature file and public key are required.' }
        & $minisign -Vm $VerifyFile -x $SignatureFile -P $PublicKey
        if ($LASTEXITCODE -ne 0) { throw 'Release signature is invalid. Nothing was installed.' }
        return
    }
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
    & $minisign -Vm $sumsFile -x $signatureFile -P $minisignPublicKey
    if ($LASTEXITCODE -ne 0) { throw 'Release signature is invalid. Nothing was installed.' }
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
    $expected = ((Get-Content $sumsFile) |
        Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset.name) + '$') } | Select-Object -First 1) -split '\s+' | Select-Object -First 1
    if (-not $expected -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne $expected.ToUpper()) {
        throw 'The downloaded file does not match its checksum. Nothing was installed.'
    }
    Expand-Archive $zip -DestinationPath $work -Force
    $root = Get-ChildItem $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'install.ps1') } | Select-Object -First 1
    if (-not $root) { throw 'The release archive does not contain the installer. Nothing was installed.' }
    $installArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root.FullName 'install.ps1'), '-Deps')
    if ($WithASR) { $installArgs += '-WithASR' }
    & (Get-Process -Id $PID).Path @installArgs
    if ($LASTEXITCODE -ne 0) { throw 'The installation did not finish. Read the message above, fix it and run the command again.' }
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    $ErrorActionPreference = $previousErrorAction
}

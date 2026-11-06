param(
    [Parameter(Mandatory=$true)][string]$Sums,
    [Parameter(Mandatory=$true)][string]$PrivateKey
)
$ErrorActionPreference = 'Stop'
$sumsPath = (Resolve-Path -LiteralPath $Sums).Path
$keyPath = (Resolve-Path -LiteralPath $PrivateKey).Path
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($keyPath.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The private signing key must be kept outside the repository.'
}
$minisign = Get-Command minisign -ErrorAction Stop
& $minisign.Source -S -s $keyPath -m $sumsPath -x ($sumsPath + '.minisig')
if ($LASTEXITCODE -ne 0) { throw 'Release signing failed.' }

#requires -Version 5.1
<#
.SYNOPSIS
    Deploy the Moonwing manager from a Windows checkout via WSL (rsync + SSH).

.DESCRIPTION
    1. rsync → RemoteInstallPath
    2. pip install -e .
    3. alembic upgrade head (unless skipped)
    4. systemctl restart (unless skipped)
    5. health + alembic current

    Set $env:SSHPASS before calling or pass -EscrowFile + -EscrowSecretsKey to read `.secrets.<key>.password`.

.EXAMPLE
    $env:SSHPASS = '<password>'
    .\deploy\scripts\deploy-manager.ps1 -HostIp 10.0.0.5 -RemoteInstallPath /opt/moonwing/app

.EXAMPLE
    .\deploy\scripts\deploy-manager.ps1 -HostIp mgr.internal `
        -EscrowFile escrow.json -EscrowSecretsKey prod/manager_ssh
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$HostIp,

    [string]$User = 'moonwing',

    [string]$RemoteInstallPath = '/opt/moonwing/app',

    [string]$EscrowFile = '',

    [string]$EscrowSecretsKey = '',

    [switch]$SkipMigrations,

    [switch]$SkipRestart
)

$ErrorActionPreference = 'Stop'

function Assert-IfaceToken([string]$Value, [string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Name is empty."
    }
    if ($Value.Contains("'")) {
        throw "$Name cannot contain apostrophes (`'`)."
    }
}

function BashSingleQuote([string]$Value) {
    Assert-IfaceToken $Value 'bash literal'
    return "'" + $Value + "'"
}

Assert-IfaceToken $HostIp 'HostIp'
Assert-IfaceToken $User 'User'
Assert-IfaceToken $RemoteInstallPath 'RemoteInstallPath'
if (-not $RemoteInstallPath.StartsWith('/')) {
    throw 'RemoteInstallPath must be a POSIX absolute path such as /opt/moonwing/app.'
}

if ($EscrowFile -and -not $EscrowSecretsKey) {
    throw 'Use -EscrowSecretsKey when supplying -EscrowFile.'
}

$escrowPathResolved = ''
if (-not [string]::IsNullOrWhiteSpace($EscrowFile)) {
    $escrowPathResolved = (Resolve-Path -LiteralPath $EscrowFile).Path
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Write-Host "[deploy-manager] checkout: $repoRoot"

$wslSrc = (& wsl wslpath -a ($repoRoot.Path -replace '\\', '/')) -replace "`r", ''
Assert-IfaceToken $wslSrc 'WSL path'
Write-Host "[deploy-manager] wsl source: $wslSrc"

$passwordFromEscrow = $null
if ($escrowPathResolved) {
    $doc = Get-Content -LiteralPath $escrowPathResolved -Raw | ConvertFrom-Json
    if (-not ($doc.PSObject.Properties.Name -contains 'secrets')) {
        throw 'Escrow export must expose a `"secrets`" map.'
    }
    $needle = $doc.secrets.PSObject.Properties | Where-Object { $_.Name -eq $EscrowSecretsKey }
    if (-not $needle) {
        throw ('Secret key `' + $EscrowSecretsKey + '` missing under `"secrets`".')
    }
    $node = $needle.Value
    if ($node.password) {
        $passwordFromEscrow = [string]$node.password
    }
    elseif (($User -eq 'root') -and $node.PSObject.Properties.Name -contains 'root_password') {
        $passwordFromEscrow = [string]$node.root_password
    }
    if (-not $passwordFromEscrow) {
        throw 'Escrow entry must expose password (or root_password when User=root).'
    }
}

$effectivePw = $env:SSHPASS
if (-not $effectivePw) {
    $effectivePw = $passwordFromEscrow
}
if (-not $effectivePw) {
    throw 'Need SSHPASS in the environment, or escrow JSON credentials.'
}

$qRoot = BashSingleQuote $RemoteInstallPath
$qVenv = BashSingleQuote ($RemoteInstallPath + '/venv')
$qPy = BashSingleQuote ($RemoteInstallPath + '/src')
$qIni = BashSingleQuote ($RemoteInstallPath + '/alembic.ini')

$srcTrail = (($wslSrc.TrimEnd('/') + '/'))
$rDestTrail = (($RemoteInstallPath.TrimEnd('/') + '/'))
$rsyncRemote = "{0}@{1}:{2}" -f $User, $HostIp, $rDestTrail

$rsyncExtras = '--exclude=.git/ --exclude=.merge-staging/ --exclude=.pytest_cache/ --exclude=__pycache__/ --exclude=*.pyc --exclude=*.bak --exclude=*.bak-* --exclude=.env --exclude=venv/ --exclude=build/ --exclude=dist/ --exclude=*.egg-info/'

Assert-IfaceToken $srcTrail 'rsync src'
Assert-IfaceToken $rsyncRemote 'rsync dest'

function Invoke-WslRemoteBash([string]$ScriptFragment, [string]$Label) {
    Write-Host "[deploy-manager] $Label"
    $bytes = [Text.Encoding]::UTF8.GetBytes($ScriptFragment)
    $payload = [Convert]::ToBase64String($bytes)
    & wsl sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "${User}@${HostIp}" `
        bash -lc "echo '$payload' | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) {
        throw ("{0} exited {1}" -f $Label, $LASTEXITCODE)
    }
}

$oldPw = $env:SSHPASS
$oldEnv = $env:WSLENV
try {
    $env:SSHPASS = $effectivePw
    $env:WSLENV = if ($env:WSLENV) { "$($env:WSLENV):SSHPASS/u" } else { 'SSHPASS/u' }

    Write-Host ("[deploy-manager] step 1/5 rsync → {0}" -f $rsyncRemote)
    $rsyncInner = "$(BashSingleQuote $srcTrail) $(BashSingleQuote $rsyncRemote)"
    $bashRsync = @"
set -euo pipefail
sshpass -e rsync -az --delete -e 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' $rsyncExtras $rsyncInner
"@

    & wsl bash -lc $bashRsync
    if ($LASTEXITCODE -ne 0) { throw "step 1/5 rsync failed ($LASTEXITCODE)" }

    $pipFragment = ("cd $qRoot && $qVenv/bin/pip install -e . --quiet" + "`n")
    Invoke-WslRemoteBash $pipFragment.Trim() 'step 2/5 pip install -e .'

    if (-not $SkipMigrations) {
        $alembicFragment = "cd $qRoot && PYTHONPATH=$qPy $qVenv/bin/alembic -c alembic.ini upgrade head"
        Invoke-WslRemoteBash $alembicFragment 'step 3/5 alembic upgrade head'
    }
    else {
        Write-Host '[deploy-manager] step 3/5 skipping alembic'
    }

    if (-not $SkipRestart) {
        Invoke-WslRemoteBash 'sudo -n systemctl restart moonwing-api.service moonwing-worker.service' 'step 4/5 systemd restart'
    }
    else {
        Write-Host '[deploy-manager] step 4/5 skipping restart'
    }

    $tail = ('systemctl is-active moonwing-api.service moonwing-worker.service ' +
             '&& curl -fsS http://127.0.0.1:8000/health && echo && ' +
             "PYTHONPATH=$qPy $qVenv/bin/alembic -c $qIni current")
    Invoke-WslRemoteBash $tail 'step 5/5 health snapshot'

    Write-Host '[deploy-manager] DONE'
}
finally {
    $env:SSHPASS = $oldPw
    $env:WSLENV = $oldEnv
}

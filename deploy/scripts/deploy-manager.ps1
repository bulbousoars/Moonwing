#requires -Version 5.1
<#
.SYNOPSIS
    Deploy the Moonwing manager (API + worker) to VM 215 from a Windows checkout.

.DESCRIPTION
    Replaces the legacy "scp into venv site-packages" workflow. Performs the
    deploy as a single repo-driven pipeline:

      1. rsync the local checkout to /mnt/storage/moonwing/app on the VM,
         excluding .git, .env, build artifacts, and pytest caches
      2. pip install -e . in the production venv (picks up new deps)
      3. alembic upgrade head
      4. systemctl restart moonwing-api moonwing-worker
      5. health check + alembic current

    Uses WSL + sshpass + the same OpenBao offline escrow pattern as
    C:\Users\danie\homelab_ssh.ps1 so it works without configuring
    additional credentials.

.PARAMETER HostIp
    Target host. Defaults to 192.168.1.215 (secops).

.PARAMETER User
    SSH user. Defaults to dduggan.

.PARAMETER SkipMigrations
    Skip the alembic upgrade step.

.PARAMETER SkipRestart
    Skip restarting moonwing-api and moonwing-worker.

.EXAMPLE
    .\deploy\scripts\deploy-manager.ps1
    Deploy current checkout to secops with migrations and restart.

.EXAMPLE
    .\deploy\scripts\deploy-manager.ps1 -SkipMigrations
    Deploy without running alembic.
#>
param(
    [string]$HostIp = "192.168.1.215",
    [string]$User = "dduggan",
    [switch]$SkipMigrations,
    [switch]$SkipRestart
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Write-Host "[deploy-manager] repo root: $repoRoot"

# Translate Windows path to WSL path (D:\Projects\Moonwing -> /mnt/d/Projects/Moonwing)
$wslSrc = (& wsl wslpath -a ($repoRoot.Path -replace '\\','/')) -replace "`r",""
Write-Host "[deploy-manager] wsl src: $wslSrc"

# Pull SSH password from OpenBao offline escrow (same pattern as homelab_ssh.ps1)
$escrow = Get-Content -Raw -Path "$env:USERPROFILE\OpenBao-Offline-Escrow\2026-04-27-openbao-offline-escrow.json" | ConvertFrom-Json
$secretData = $escrow.secrets.'secret/homelab/vm-ssh'
$password = if ($User -eq "root") { $secretData.root_password_secops } else { $secretData.password }
if (-not $password) { $password = $secretData.password }
if (-not $password) { throw "VM SSH credential was empty or missing" }

$oldPass = $env:SSHPASS
$oldWslenv = $env:WSLENV
try {
    $env:SSHPASS = $password
    $env:WSLENV = if ($env:WSLENV) { "$($env:WSLENV):SSHPASS/u" } else { "SSHPASS/u" }

    Write-Host "[deploy-manager] step 1/5: rsync source -> $HostIp"
    $rsyncCmd = @(
        "sshpass -e rsync -az --delete",
        "-e 'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'",
        "--exclude='.git/'",
        "--exclude='.merge-staging/'",
        "--exclude='.pytest_cache/'",
        "--exclude='__pycache__/'",
        "--exclude='*.pyc'",
        "--exclude='*.bak'",
        "--exclude='*.bak-*'",
        "--exclude='.env'",
        "--exclude='venv/'",
        "--exclude='build/'",
        "--exclude='dist/'",
        "--exclude='*.egg-info/'",
        "$wslSrc/",
        "${User}@${HostIp}:/mnt/storage/moonwing/app/"
    ) -join ' '
    & wsl bash -lc $rsyncCmd
    if ($LASTEXITCODE -ne 0) { throw "rsync failed (exit $LASTEXITCODE)" }

    function Invoke-Remote {
        param([string]$Cmd, [string]$Label)
        Write-Host "[deploy-manager] $Label"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Cmd))
        & wsl sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$User@$HostIp" "echo $encoded | base64 -d | bash"
        if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
    }

    Invoke-Remote 'cd /mnt/storage/moonwing/app && /mnt/storage/moonwing/app/venv/bin/pip install -e . --quiet' "step 2/5: pip install -e ."

    if (-not $SkipMigrations) {
        Invoke-Remote 'cd /mnt/storage/moonwing/app && PYTHONPATH=/mnt/storage/moonwing/app/src /mnt/storage/moonwing/app/venv/bin/alembic -c alembic.ini upgrade head' "step 3/5: alembic upgrade head"
    } else {
        Write-Host "[deploy-manager] step 3/5: SKIPPED (alembic)"
    }

    if (-not $SkipRestart) {
        Invoke-Remote 'sudo -n systemctl restart moonwing-api.service moonwing-worker.service' "step 4/5: systemctl restart api+worker"
    } else {
        Write-Host "[deploy-manager] step 4/5: SKIPPED (restart)"
    }

    Invoke-Remote 'systemctl is-active moonwing-api.service moonwing-worker.service && curl -fsS http://127.0.0.1:8000/health && echo && PYTHONPATH=/mnt/storage/moonwing/app/src /mnt/storage/moonwing/app/venv/bin/alembic -c /mnt/storage/moonwing/app/alembic.ini current' "step 5/5: health + alembic current"

    Write-Host "[deploy-manager] OK"
}
finally {
    $env:SSHPASS = $oldPass
    $env:WSLENV = $oldWslenv
}

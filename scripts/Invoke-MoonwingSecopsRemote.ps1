#requires -Version 5.1
<#
.SYNOPSIS
  OpenBao SSH to secops (192.168.1.215) and run deploy/secops/on-host.sh.

.DESCRIPTION
  Uses New-AgentSshSession.ps1 (cursor-admin cert). No password SSH.
  Moonwing checkout default: /opt/moonwing/Moonwing

.EXAMPLE
  .\scripts\Invoke-MoonwingSecopsRemote.ps1
  .\scripts\Invoke-MoonwingSecopsRemote.ps1 -RemoteGitRoot /opt/moonwing/Moonwing -Admin
#>
param(
    [string]$HostName = '192.168.1.215',
    [string]$RemoteGitRoot = '/opt/moonwing/Moonwing',
    [ValidateSet('codex', 'claude', 'gemini', 'cursor', 'human')]
    [string]$Agent = 'cursor',
    [switch]$Admin,
    [string]$OpenBaoSessionScript = 'C:\Users\danie\Scripts\homelab\New-AgentSshSession.ps1',
    [switch]$SkipGitPull
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $OpenBaoSessionScript)) {
    throw "OpenBao session script not found: $OpenBaoSessionScript"
}

$session = & $OpenBaoSessionScript -Agent $Agent -HostName $HostName -Admin:$Admin
$sshConfig = Join-Path $session.session_dir 'ssh_config'
Write-Host "[Invoke-MoonwingSecopsRemote] $($session.principal) -> $HostName ($($session.ttl))"

$repoEsc = $RemoteGitRoot -replace "'", "'\''"
$gitBlock = if ($SkipGitPull) {
    'echo "[remote] SkipGitPull"'
} else {
    @"
sudo -n git -C '$repoEsc' update-index --no-skip-worktree docker-compose.yml 2>/dev/null || true
sudo -n git -C '$repoEsc' fetch origin main
sudo -n git -C '$repoEsc' reset --hard origin/main
"@
}

$remoteBash = @"
set -euo pipefail
$gitBlock
cd '$repoEsc'
chmod +x deploy/secops/on-host.sh
./deploy/secops/on-host.sh
"@

$remoteBash = ($remoteBash -replace "`r`n", "`n") -replace "`r", "`n"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteBash))

try {
    Write-Host "[Invoke-MoonwingSecopsRemote] running on-host.sh ..."
    ssh -F $sshConfig -o ConnectTimeout=30 -o BatchMode=yes $HostName "echo $b64 | base64 -d | bash"
} finally {
    Write-Host "[Invoke-MoonwingSecopsRemote] cleanup: $($session.cleanup_command)"
    Invoke-Expression $session.cleanup_command
}

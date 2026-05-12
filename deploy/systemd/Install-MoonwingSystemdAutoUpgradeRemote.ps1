#requires -Version 5.1
<#
.SYNOPSIS
  SSH (OpenBao broker config) to a Moonwing host, git pull, install systemd auto-upgrade timer.

.DESCRIPTION
  Pair with New-AgentSshSession.ps1 (use **-Admin** if the remote user needs passwordless sudo for
  `git pull` and `install-on-host.sh`). Pass the same **HostName** you used for the broker session.

  Example (daily timer only):
    $s = & "C:\path\to\New-AgentSshSession.ps1" -Agent cursor -HostName moonwing.example.org -Admin
    & "$PSScriptRoot\Install-MoonwingSystemdAutoUpgradeRemote.ps1" -SshConfig (Join-Path $s.session_dir 'ssh_config') -TargetHost moonwing.example.org

  Example (pull again ~10 min after each upgrade — active dev):
    ... same $s ... -AutoUpgradeIntervalMinutes 10 -GitBranch main

.NOTES
  Requires: OpenSSH client, Python 3 on the remote (used by install-on-host.sh for unit templating).
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$SshConfig,

    [Parameter(Mandatory = $true)]
    [string]$TargetHost,

    [string]$RepoPath = '/opt/moonwing/Moonwing',

    [string]$GitRemote = 'origin',

    [string]$GitBranch = 'main',

    [int]$GitTimeoutSec = 300,

    [ValidateRange(0, 1440)]
    [int]$AutoUpgradeIntervalMinutes = 0,

    [switch]$SkipGitPull
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SshConfig)) {
    throw "SshConfig not found: $SshConfig"
}

$repoEsc = $RepoPath -replace "'", "'\''"
$gitTimeout = $GitTimeoutSec
$gitFetch = if ($SkipGitPull) {
    'echo "[remote] SkipGitPull: not running git pull"'
} else {
    @"
export GIT_TERMINAL_PROMPT=0
if [[ ! -w "`$REPO/.git" ]]; then
  if command -v timeout >/dev/null 2>&1; then
    timeout $gitTimeout sudo -n git -C "`$REPO" fetch $GitRemote
    timeout $gitTimeout sudo -n git -C "`$REPO" pull --ff-only $GitRemote $GitBranch
  else
    sudo -n git -C "`$REPO" fetch $GitRemote && sudo -n git -C "`$REPO" pull --ff-only $GitRemote $GitBranch
  fi
else
  if command -v timeout >/dev/null 2>&1; then
    timeout $gitTimeout git -C "`$REPO" fetch $GitRemote
    timeout $gitTimeout git -C "`$REPO" pull --ff-only $GitRemote $GitBranch
  else
    git -C "`$REPO" fetch $GitRemote && git -C "`$REPO" pull --ff-only $GitRemote $GitBranch
  fi
fi
"@
}

$remoteBash = @"
set -euo pipefail
REPO='$repoEsc'
cd "`$REPO"
git config --global --add safe.directory "`$REPO" 2>/dev/null || true
$gitFetch
$(if ($AutoUpgradeIntervalMinutes -gt 0) {
    "sudo env MOONWING_AUTO_UPGRADE_INTERVAL_MIN=$AutoUpgradeIntervalMinutes bash deploy/systemd/install-on-host.sh"
} else {
    "sudo bash deploy/systemd/install-on-host.sh"
})
"@

$remoteBash = ($remoteBash -replace "`r`n", "`n") -replace "`r", "`n"

$bytes = [Text.Encoding]::UTF8.GetBytes($remoteBash)
$b64 = [Convert]::ToBase64String($bytes)

Write-Host "[Install-MoonwingSystemdAutoUpgradeRemote] ssh -F $SshConfig $TargetHost ..."
ssh -F $SshConfig -o ConnectTimeout=30 -o ServerAliveInterval=20 -o BatchMode=yes $TargetHost "echo $b64 | base64 -d | bash"

#requires -Version 5.1
<#
.SYNOPSIS
  SSH (OpenBao broker config) to a Moonwing host, git pull, install systemd auto-upgrade timer.

.DESCRIPTION
  Pair with New-AgentSshSession.ps1 (use **-Admin** if the remote user needs passwordless sudo for
  `git pull` and `install-on-host.sh`). Pass the same **HostName** you used for the broker session.

  Example:
    $s = & "$env:USERPROFILE\Scripts\homelab\New-AgentSshSession.ps1" -Agent cursor -HostName 192.168.1.215 -Admin
    & "$PSScriptRoot\Install-MoonwingSystemdAutoUpgradeRemote.ps1" -SshConfig (Join-Path $s.session_dir 'ssh_config') -TargetHost 192.168.1.215

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

    [switch]$SkipGitPull
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SshConfig)) {
    throw "SshConfig not found: $SshConfig"
}

$repoEsc = $RepoPath -replace "'", "'\''"
$gitFetch = if ($SkipGitPull) {
    'echo "[remote] SkipGitPull: not running git pull"'
} else {
    "git -C `"`$REPO`" fetch $GitRemote && git -C `"`$REPO`" pull --ff-only $GitRemote $GitBranch"
}

$remoteBash = @"
set -euo pipefail
REPO='$repoEsc'
cd "`$REPO"
$gitFetch
sudo bash "`$REPO/deploy/systemd/install-on-host.sh"
"@

$bytes = [Text.Encoding]::UTF8.GetBytes($remoteBash)
$b64 = [Convert]::ToBase64String($bytes)

Write-Host "[Install-MoonwingSystemdAutoUpgradeRemote] ssh -F $SshConfig $TargetHost ..."
ssh -F $SshConfig $TargetHost "echo $b64 | base64 -d | bash"

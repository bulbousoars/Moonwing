#requires -Version 5.1
<#
.SYNOPSIS
  SSH to a Linux host and run scripts/moonwing-upgrade.sh in your Moonwing git checkout.

.DESCRIPTION
  For Docker Compose installs on the server (see README). Requires OpenSSH client (ssh).
  Authentication: use ssh-agent, an SSH key, or your configured default — interactive passwords are not scripted.

.EXAMPLE
  .\scripts\Invoke-MoonwingUpgradeRemote.ps1 -HostName moonwing.internal -RemoteGitRoot /opt/moonwing/Moonwing

.EXAMPLE
  .\scripts\Invoke-MoonwingUpgradeRemote.ps1 -HostName 192.168.1.50 -User dduggan -RemoteGitRoot /opt/moonwing/Moonwing
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,

    [string]$User = '',

    [Parameter(Mandatory = $true)]
    [string]$RemoteGitRoot
)

$ErrorActionPreference = 'Stop'

$bashSingleQuotedPath = "'" + ($RemoteGitRoot -replace "'", "'\''") + "'"
$target = if ([string]::IsNullOrWhiteSpace($User)) { $HostName } else { "${User}@${HostName}" }

$remoteBash = @"
set -euo pipefail
cd $bashSingleQuotedPath
chmod +x scripts/moonwing-upgrade.sh scripts/moonwing-up.sh 2>/dev/null || true
./scripts/moonwing-upgrade.sh
"@

$bytes = [Text.Encoding]::UTF8.GetBytes($remoteBash)
$b64 = [Convert]::ToBase64String($bytes)

Write-Host "[Invoke-MoonwingUpgradeRemote] ssh $target (upgrade in $RemoteGitRoot) ..."
ssh $target "echo $b64 | base64 -d | bash"

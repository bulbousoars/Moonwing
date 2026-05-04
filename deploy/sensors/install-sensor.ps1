#requires -RunAsAdministrator
<#
 Moonwing endpoint sensor installer (Windows) — shipped in-repo.

 Requires a stable manager URL (DNS or IP: https://moonwing.example.com) and an
 enrollment token from Management → Sensors → Install.

 Operational twin of UI-generated installers; keep aligned with
 src/moonwing/services/sensor_installer.py (Windows template).
#>

param(
    [Parameter(Mandatory = $false)]
    [string] $ManagerUrl,

    [Parameter(Mandatory = $false)]
    [string] $EnrollmentToken,

    [string] $SensorVersion = '0.1.0'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-ManagerUrlString([string] $Explicit) {
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        return $Explicit.Trim()
    }
    if (-not [string]::IsNullOrWhiteSpace($env:MOONWING_MANAGER_URL)) {
        return $env:MOONWING_MANAGER_URL.Trim()
    }
    return ''
}

function Resolve-EnrollmentTokenString([string] $Explicit) {
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        return $Explicit.Trim()
    }
    if (-not [string]::IsNullOrWhiteSpace($env:MOONWING_ENROLLMENT_TOKEN)) {
        return $env:MOONWING_ENROLLMENT_TOKEN.Trim()
    }
    return ''
}

$ManagerUrl = (Resolve-ManagerUrlString $ManagerUrl).TrimEnd('/')
$EnrollmentToken = Resolve-EnrollmentTokenString $EnrollmentToken

if ([string]::IsNullOrWhiteSpace($ManagerUrl) -or [string]::IsNullOrWhiteSpace($EnrollmentToken)) {
    Write-Error 'Set MOONWING_MANAGER_URL and MOONWING_ENROLLMENT_TOKEN environment variables or pass -ManagerUrl and -EnrollmentToken. See deploy/sensors/README.md.'
}

if ($ManagerUrl -notmatch '^(https?://)') {
    Write-Error 'Manager URL must start with http:// or https://'
}

$InstallDir = 'C:\ProgramData\Moonwing\sensor'
$ConfigFile = Join-Path $InstallDir 'sensor.env'
$AgentScript = Join-Path $InstallDir 'moonwing-sensor.ps1'

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

$Hostname = [System.Net.Dns]::GetHostName()
try {
    $OsName = (Get-CimInstance Win32_OperatingSystem).Caption
} catch {
    $OsName = 'Windows'
}

Write-Host "[moonwing-sensor] enrolling with $ManagerUrl"

$EnrollPayload = @{
    enrollment_token = $EnrollmentToken
    hostname         = $Hostname
    platform         = 'windows'
    os_name          = $OsName
    sensor_version   = $SensorVersion
    labels           = @()
} | ConvertTo-Json -Compress

$EnrollResponse = Invoke-RestMethod `
    -Method Post `
    -Uri "$ManagerUrl/api/sensors/enroll" `
    -ContentType 'application/json' `
    -Body $EnrollPayload

$SensorId    = $EnrollResponse.sensor_id
$SensorToken = $EnrollResponse.token

if (-not $SensorId -or -not $SensorToken) {
    throw "Enrollment failed: $($EnrollResponse | ConvertTo-Json -Depth 4)"
}

@(
    "MOONWING_MANAGER_URL=$ManagerUrl",
    "MOONWING_SENSOR_ID=$SensorId",
    "MOONWING_SENSOR_TOKEN=$SensorToken",
    "MOONWING_SENSOR_VERSION=$SensorVersion"
) | Set-Content -Path $ConfigFile -Encoding ASCII

$AgentBody = @'
$ErrorActionPreference = 'Continue'
$ConfigFile = 'C:\ProgramData\Moonwing\sensor\sensor.env'

$cfg = @{}
foreach ($line in (Get-Content $ConfigFile)) {
    if ($line -match '^(MOONWING_[A-Z_]+)=(.*)$') {
        $cfg[$Matches[1]] = $Matches[2]
    }
}

$ManagerUrl   = $cfg['MOONWING_MANAGER_URL']
$SensorId     = $cfg['MOONWING_SENSOR_ID']
$SensorToken  = $cfg['MOONWING_SENSOR_TOKEN']
$SensorVersion = $cfg['MOONWING_SENSOR_VERSION']

$Inventory = @{
    hostname = [System.Net.Dns]::GetHostName()
    os_name  = (Get-CimInstance Win32_OperatingSystem).Caption
    kernel   = [System.Environment]::OSVersion.Version.ToString()
    machine  = [System.Environment]::OSVersion.Platform.ToString()
}

$Payload = @{
    inventory      = $Inventory
    network        = @{}
    sensor_version = $SensorVersion
} | ConvertTo-Json -Compress

try {
    Invoke-RestMethod `
        -Method Post `
        -Uri "$ManagerUrl/api/sensors/$SensorId/heartbeat" `
        -Headers @{ Authorization = "Bearer $SensorToken" } `
        -ContentType 'application/json' `
        -Body $Payload | Out-Null
} catch {
    Write-EventLog -LogName Application -Source 'Moonwing Sensor' `
        -EntryType Warning -EventId 1001 `
        -Message "Heartbeat failed: $($_.Exception.Message)" -ErrorAction SilentlyContinue
}
'@

Set-Content -Path $AgentScript -Value $AgentBody -Encoding UTF8

if (-not [System.Diagnostics.EventLog]::SourceExists('Moonwing Sensor')) {
    [System.Diagnostics.EventLog]::CreateEventSource('Moonwing Sensor', 'Application')
}

$TaskName = 'Moonwing Sensor Heartbeat'
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$AgentScript`""
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration ([System.TimeSpan]::MaxValue)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action `
    -Trigger $Trigger -Settings $Settings -RunLevel Highest `
    -User 'SYSTEM' | Out-Null

Write-Host "[moonwing-sensor] installed. sensor_id=$SensorId"
Write-Host "[moonwing-sensor] heartbeat task: $TaskName"

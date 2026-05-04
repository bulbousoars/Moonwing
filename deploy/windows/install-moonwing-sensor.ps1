param(
    [Parameter(Mandatory = $true)]
    [string]$ServerUrl,

    [Parameter(Mandatory = $true)]
    [string]$EnrollmentToken,

    [string]$InstallDir = "$env:ProgramFiles\Moonwing\Sensor",
    [string]$ConfigDir = "$env:ProgramData\Moonwing",
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

$source = Join-Path $PSScriptRoot "..\..\src\moonwing_sensor"
$packageDest = Join-Path $InstallDir "moonwing_sensor"
Copy-Item -Recurse -Force -Path $source -Destination $packageDest

$python = (Get-Command python.exe -ErrorAction Stop).Source
$env:PYTHONPATH = $InstallDir
& $python -m moonwing_sensor.agent enroll --server $ServerUrl --token $EnrollmentToken --config (Join-Path $ConfigDir "sensor.json")

$serviceName = "MoonwingSensor"
$arguments = "-m moonwing_sensor.agent run --config `"$ConfigDir\sensor.json`" --interval $IntervalSeconds"
$binaryPath = "`"$python`" $arguments"

if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    sc.exe config $serviceName binPath= $binaryPath | Out-Null
} else {
    New-Service -Name $serviceName -DisplayName "Moonwing Endpoint Sensor" -BinaryPathName $binaryPath -StartupType Automatic
}

Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName" -Name Environment -Value @("PYTHONPATH=$InstallDir")
Start-Service -Name $serviceName

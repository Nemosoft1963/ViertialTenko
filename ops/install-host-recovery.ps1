[CmdletBinding()]
param([string]$ProjectRoot=(Split-Path -Parent $PSScriptRoot),[ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")][string]$WeeklyRestartTime="03:00",[switch]$EnableTasks)
$ErrorActionPreference="Stop";$script=Join-Path $ProjectRoot "ops\host-watchdog.ps1"
if(!(Test-Path -LiteralPath $script)){throw "Watchdog script was not found: $script"}
if(!$EnableTasks){Write-Host "Validation mode: no tasks were registered. Use -EnableTasks to register.";exit 0}
$exe="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
function Act($mode){$arg='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Mode {1} -ProjectRoot "{2}"'-f$script,$mode,$ProjectRoot;New-ScheduledTaskAction -Execute $exe -Argument $arg}
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$watch=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
$startup=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name);$startup.Delay="PT30S"
$hm=$WeeklyRestartTime.Split(":");$weekly=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At ([datetime]::Today.AddHours([int]$hm[0]).AddMinutes([int]$hm[1]))
Register-ScheduledTask -TaskName "ViertialTenko-Watchdog" -Action (Act "Watchdog") -Trigger $watch -Settings $settings -Principal $principal -Force|Out-Null
Register-ScheduledTask -TaskName "ViertialTenko-Startup" -Action (Act "Startup") -Trigger $startup -Settings $settings -Principal $principal -Force|Out-Null
Register-ScheduledTask -TaskName "ViertialTenko-WeeklyRestart" -Action (Act "WeeklyRestart") -Trigger $weekly -Settings $settings -Principal $principal -Force|Out-Null
Get-ScheduledTask -TaskName "ViertialTenko-*"|Select-Object TaskName,State

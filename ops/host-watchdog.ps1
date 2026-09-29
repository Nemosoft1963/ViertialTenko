[CmdletBinding()]
param(
 [ValidateSet("Watchdog","Startup","WeeklyRestart")][string]$Mode="Watchdog",
 [string]$ProjectRoot=(Split-Path -Parent $PSScriptRoot),
 [string]$HealthUrl="http://127.0.0.1:8080/api/health",
 [int]$FailureThreshold=3,[int]$RecoveryCooldownMinutes=15
)
$ErrorActionPreference="Stop"
$stateDir=Join-Path $env:LOCALAPPDATA "ViertialTenkoS2\watchdog"
$stateFile=Join-Path $stateDir "state.json"
$logFile=Join-Path $stateDir "watchdog.log"
$composeFile=Join-Path $ProjectRoot "docker-compose.yml"
function Log($level,$message){New-Item -ItemType Directory -Path $stateDir -Force|Out-Null;Add-Content -LiteralPath $logFile -Encoding UTF8 -Value ("{0} [{1}] [{2}] {3}"-f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"),$level,$Mode,$message)}
function Default-State{[ordered]@{docker_failures=0;api_failures=0;last_check=$null;last_recovery=$null;last_result="unknown";recovery_count=0}}
function Read-State{if(!(Test-Path -LiteralPath $stateFile)){return Default-State};try{Get-Content -LiteralPath $stateFile -Raw|ConvertFrom-Json}catch{return Default-State}}
function Save-State($s){New-Item -ItemType Directory -Path $stateDir -Force|Out-Null;$t=$stateFile+"."+[guid]::NewGuid().ToString("N")+".tmp";$s|ConvertTo-Json|Set-Content -LiteralPath $t -Encoding UTF8;Move-Item -LiteralPath $t -Destination $stateFile -Force}
function Docker-Ok{try{$v=& docker info --format "{{.ServerVersion}}" 2>$null;return($LASTEXITCODE -eq 0 -and $v)}catch{return $false}}
function Api-Ok{try{return((Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 8).ok -eq $true)}catch{return $false}}
function Start-Docker{if(Docker-Ok){return $true};$exe=Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe";if(!(Test-Path -LiteralPath $exe)){Log "ERROR" "Docker Desktop.exe was not found";return $false};Start-Process -FilePath $exe -WindowStyle Hidden;foreach($i in 1..36){Start-Sleep 5;if(Docker-Ok){return $true}};return $false}
function Start-Tenko{& docker compose --project-directory $ProjectRoot -f $composeFile --profile openwebui up -d;if($LASTEXITCODE -ne 0){return $false};foreach($i in 1..36){if(Api-Ok){return $true};Start-Sleep 5};return $false}
function Recover{if(!(Start-Docker)){return $false};return Start-Tenko}
function Active-Checkin{$items=@(Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/checkins" -TimeoutSec 10);return(@($items|Where-Object{$_.status -eq "in_progress"}).Count -gt 0)}
$made=$false;$mutex=New-Object System.Threading.Mutex($true,"Local\ViertialTenkoHostWatchdog",[ref]$made);if(!$made){exit 0}
try{
 if($Mode -eq "WeeklyRestart"){
  if(!(Docker-Ok)-or!(Api-Ok)){Log "WARN" "Weekly restart skipped because health is unknown";exit 0}
  try{if(Active-Checkin){Log "INFO" "Weekly restart skipped because a check-in is active";exit 0}}catch{Log "WARN" "Weekly restart skipped because check-in state is unavailable";exit 0}
  Log "INFO" "Forced Docker backend restart is disabled in this safe version";exit 0
 }
 if($Mode -eq "Startup"){if(Recover){exit 0}else{exit 1}}
 $s=Read-State;$d=Docker-Ok;$a=$false;if($d){$a=Api-Ok}
 $s.docker_failures=if($d){0}else{[int]$s.docker_failures+1};$s.api_failures=if($a){0}else{[int]$s.api_failures+1};$s.last_check=(Get-Date).ToString("o");$s.last_result=if($d-and$a){"healthy"}else{"unhealthy"}
 if($d-and$a){Save-State $s;exit 0};Log "WARN" ("Unhealthy Docker={0} API={1} DockerFailures={2} ApiFailures={3}"-f$d,$a,$s.docker_failures,$s.api_failures)
 if([int]$s.docker_failures-lt$FailureThreshold-and[int]$s.api_failures-lt$FailureThreshold){Save-State $s;exit 0}
 if($s.last_recovery){try{if(((Get-Date)-[datetime]::Parse([string]$s.last_recovery)).TotalMinutes-lt$RecoveryCooldownMinutes){Save-State $s;exit 0}}catch{}}
 $s.last_recovery=(Get-Date).ToString("o");$s.recovery_count=[int]$s.recovery_count+1;Save-State $s
 if(Recover){$s.docker_failures=0;$s.api_failures=0;$s.last_result="recovered";Save-State $s;exit 0};$s.last_result="recovery_failed";Save-State $s;exit 1
}catch{Log "ERROR" $_.Exception.Message;exit 1}finally{try{$mutex.ReleaseMutex()}catch{};$mutex.Dispose()}

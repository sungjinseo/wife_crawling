# Windows 작업 스케줄러에 네이버 정기 수집 등록 (PowerShell 에서 실행)
#   예) .\register_task.ps1            (기본 매일 10:00)
#       .\register_task.ps1 -Time 07:30 -Distro Ubuntu -LinuxUser greatseo -Project /mnt/d/workspace/getting-home
#   지우기) Unregister-ScheduledTask -TaskName getting-home-naver -Confirm:$false
param(
  [string]$Time = "10:00",                                   # 매일 실행 시각 (출근 후)
  [string]$Project = "/mnt/d/workspace/getting-home",        # WSL 안에서 본 프로젝트 경로
  [string]$Distro = "",                                      # 비우면 기본 WSL 배포판
  [string]$LinuxUser = ""                                    # 비우면 기본 사용자
)
$wslArgs = @()
if ($Distro)    { $wslArgs += "-d $Distro" }
if ($LinuxUser) { $wslArgs += "-u $LinuxUser" }
$wslArgs += "-- bash -lc '$Project/tools/run_naver.sh'"
# conhost --headless: 실행할 때 검은 창이 뜨지 않게 (Windows 10 21H2 이상)
$action   = New-ScheduledTaskAction -Execute "conhost.exe" -Argument ("--headless wsl.exe " + ($wslArgs -join " "))
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName "getting-home-naver" -Action $action -Trigger $trigger -Settings $settings `
  -Description "분당 브리핑: 네이버 매물 수집 후 서버 업로드 (tools/run_naver.sh)" -Force | Out-Null
Write-Host "등록 완료: 매일 $Time 실행 (PC 가 꺼져 있었으면 켜진 뒤 바로 실행)"
Write-Host "지금 한 번 실행: Start-ScheduledTask -TaskName getting-home-naver"
Write-Host "로그: $Project/data/logs/naver_날짜.log"

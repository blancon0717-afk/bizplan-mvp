# start_backend.ps1 — 백엔드 단일 인스턴스 실행 스크립트
# 실행: .\start_backend.ps1

$DIR     = Split-Path -Parent $MyInvocation.MyCommand.Path
$PYTHON  = "$DIR\.venv\Scripts\python.exe"
$PID_FILE = "$DIR\logs\backend.pid"
$LOG_FILE = "$DIR\logs\backend.log"
$PORT    = 8000

# 1. 포트 8000 점유 프로세스 모두 종료
Write-Host "[1] 포트 $PORT 점유 프로세스 확인 중..."

$portPids = netstat -ano 2>$null |
    Select-String ":$PORT\s" |
    ForEach-Object { ($_ -split '\s+')[-1] } |
    Sort-Object -Unique |
    Where-Object { $_ -match '^\d+$' -and $_ -ne '0' }

foreach ($p in $portPids) {
    taskkill /F /PID $p 2>$null | Out-Null
    Write-Host "    -> PID $p 종료"
}

# 기존 PID 파일 프로세스도 종료
if (Test-Path $PID_FILE) {
    $oldPid = (Get-Content $PID_FILE -ErrorAction SilentlyContinue).Trim()
    if ($oldPid -match '^\d+$') {
        taskkill /F /PID $oldPid 2>$null | Out-Null
        Write-Host "    -> 이전 PID $oldPid 종료"
    }
    Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

# 2. 포트 해제 확인
$stillUsed = netstat -ano 2>$null | Select-String ":$PORT\s"
if ($stillUsed) {
    Write-Host "[!] 포트 $PORT 가 아직 사용 중입니다. 수동으로 종료 후 재시도하세요."
    exit 1
}
Write-Host "[2] 포트 $PORT 해제 확인 완료"

# 3. uvicorn 단일 인스턴스 시작
Write-Host "[3] 백엔드 시작 중..."
$proc = Start-Process `
    -FilePath $PYTHON `
    -ArgumentList "-m uvicorn main:app --host 0.0.0.0 --port $PORT" `
    -WorkingDirectory "$DIR\backend" `
    -RedirectStandardOutput $LOG_FILE `
    -WindowStyle Hidden `
    -PassThru

# 4. 기동 확인 (최대 15초 대기) 후 실제 listening PID를 파일에 저장
Write-Host "[4] 기동 확인 중..."
$ready = $false
for ($i = 0; $i -lt 3; $i++) {
    Start-Sleep -Seconds 5
    try {
        $res = Invoke-WebRequest -Uri "http://localhost:$PORT/health" -UseBasicParsing -TimeoutSec 3

        # netstat에서 실제 listening PID 추출
        $listeningPid = netstat -ano 2>$null |
            Select-String "0.0.0.0:$PORT\s+0.0.0.0:0\s+LISTENING" |
            ForEach-Object { ($_ -split '\s+')[-1] } |
            Select-Object -First 1

        $listeningPid | Out-File -FilePath $PID_FILE -Encoding utf8 -NoNewline
        Write-Host "[OK] 백엔드 정상 기동 | PID: $listeningPid | 응답: $($res.Content)"
        Write-Host "     PID 저장 -> $PID_FILE"
        $ready = $true
        break
    } catch {
        Write-Host "     대기 중... ($($($i+1)*5)초)"
    }
}

if (-not $ready) {
    Write-Host "[!] 백엔드 응답 없음. 로그 확인:"
    Get-Content $LOG_FILE -Tail 15
    powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass `
        -File "$env:USERPROFILE\.claude\notify.ps1" `
        -Title "백엔드 시작 실패" -Message "로그를 확인해주세요"
    exit 1
}

# 정상 기동 완료 토스트 알림
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass `
    -File "$env:USERPROFILE\.claude\notify.ps1" `
    -Title "백엔드 시작 완료" -Message "포트 $PORT 에서 정상 기동됨"

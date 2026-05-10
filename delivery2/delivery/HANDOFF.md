# 개발자 인수인계 가이드

**project99 — 사업계획서 자동 평가 시스템**
작성: 블랜 / 2026-04-29

---

## 1. 이 시스템이 하는 일

```
PDF 또는 텍스트
    ↓
Claude Haiku로 50개 루브릭 피처 자동 판정
    ↓
Lasso 로지스틱 회귀로 합격 확률 산출
    ↓
개선 피드백 리포트 생성 (reports/score_YYYYMMDD_HHMMSS.md)
```

현재 완성된 기능:
- `score_plan.py` — 사업계획서 PDF/텍스트 → 합격 확률 + 피드백 (3단계 완료)
- `main.py` — 노션 DB에서 PDF를 가져와 전체 파이프라인 재실행 (데이터 갱신용)

---

## 2. 폴더 구조

```
project99/
├── .env                    ← 비밀키 (절대 공유/커밋 금지)
├── .env.example            ← 환경변수 템플릿 (이 파일 참고해서 .env 만들 것)
├── requirements.txt        ← Python 패키지 목록
│
├── score_plan.py           ← ★ 핵심 진입점: 사업계획서 평가 CLI
├── main.py                 ← 노션 → PDF 추출 → 피처 저장 파이프라인
├── notion_client.py        ← 노션 API 클라이언트
├── claude_structurer.py    ← Claude Haiku 루브릭 판정
├── pdf_extractor.py        ← PDF 텍스트 추출 (pdfplumber)
├── regression_summary.py   ← 회귀 분석 + rubric_weights JSON 생성
├── rubric_rebuild.py       ← 루브릭 스키마 재구성
│
├── data/
│   ├── rubric_weights_7types.json  ← 7개 주요 프로그램 모델 가중치 (AUC 0.678)
│   ├── rubric_weights_all.json     ← 전체 프로그램 모델 가중치 (AUC 0.640)
│   ├── rubric_results_FINAL.csv    ← 학습 데이터 (815건)
│   ├── feedback_raw.json           ← 심사위원 피드백 123건
│   └── presentation_qa_matched.json ← 발표 Q&A 508건
│
└── reports/                ← 자동 생성되는 평가 리포트 저장 폴더
```

---

## 3. 환경 설정

### 3-1. Python 환경

```bash
# Python 3.10+ 필요
python --version

# 가상환경 생성 (권장)
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# 패키지 설치
pip install -r requirements.txt
```

### 3-2. .env 파일 생성

`.env.example`을 복사해 `.env`로 저장 후 실제 키 입력:

```bash
copy .env.example .env
# 이후 .env를 텍스트 에디터로 열어 각 항목 입력
```

필수 항목:

| 변수 | 설명 | 발급처 |
|---|---|---|
| `NOTION_TOKEN` | 노션 인테그레이션 토큰 | https://www.notion.so/my-integrations |
| `NOTION_DB_ID` | 사업계획서 DB ID | 노션 DB URL에서 추출 |
| `ANTHROPIC_API_KEY` | Claude API 키 | https://console.anthropic.com/settings/keys |

선택 항목 (구글 시트 연동 시):

| 변수 | 설명 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GCP 서비스 계정 JSON 경로 |
| `GOOGLE_SHEET_ID` | 결과 저장할 시트 ID |
| `GOOGLE_SHEET_NAME` | 시트 탭 이름 |

### 3-3. 노션 DB 인테그레이션 연결

노션 DB에 인테그레이션을 연결해야 API 접근이 가능합니다:

1. https://www.notion.so/my-integrations 에서 인테그레이션 생성
2. 생성된 `secret_xxx...` 토큰을 `NOTION_TOKEN`에 입력
3. **노션 DB 페이지** 오른쪽 상단 `...` → `연결` → 생성한 인테그레이션 추가
4. DB URL에서 ID 추출: `notion.so/workspace/[여기가 DB_ID]?v=...`

---

## 4. 즉시 실행 테스트

환경 설정 완료 후 아래 명령으로 동작 확인:

```bash
# PDF 평가
python score_plan.py plan.pdf --program 예비창업패키지

# 텍스트로 테스트
python score_plan.py --text "AI 기반 헬스케어 플랫폼. 국내 시장 규모 5조원..." --program 초기창업패키지

# 7개 주요 프로그램 전용 모드 (더 정확한 모델)
python score_plan.py plan.pdf --program 예비창업패키지 --mode 7types
```

성공 시 콘솔에 합격 확률과 피드백 출력 + `reports/score_YYYYMMDD_HHMMSS.md` 저장.

---

## 5. 모델 재학습 (데이터 갱신 시)

노션 DB에 새 사업계획서가 추가되면 아래 순서로 재학습:

```bash
# 1단계: 노션 → PDF 추출 → 피처 판정 → rubric_results_FINAL.csv 갱신
python main.py

# 2단계: 회귀 분석 → 가중치 JSON 갱신
python regression_summary.py --mode 7types
python regression_summary.py --mode all
```

> AUC가 이전보다 유의미하게 낮아지면 (0.05 이상 하락) 새 데이터 품질 확인 필요.
> 현재 기준: 7types AUC 0.678 / all AUC 0.640

---

## 6. 노션 DB 자동 연동

노션 DB가 업데이트될 때마다 자동으로 데이터를 갱신하는 방법입니다.

### 방법 A — Windows 작업 스케줄러 (권장)

추가 설치 없이 Windows 내장 기능으로 매일 자동 실행.

#### 배치 스크립트 생성

`project99\` 안에 `sync_and_retrain.bat` 파일을 만들고 아래 내용 작성
(경로는 실제 설치 경로로 수정):

```bat
@echo off
cd /d C:\[실제설치경로]\project99
call venv\Scripts\activate

echo [%DATE% %TIME%] 동기화 시작 >> logs\sync.log

python main.py >> logs\sync.log 2>&1
python regression_summary.py --mode 7types >> logs\sync.log 2>&1
python regression_summary.py --mode all >> logs\sync.log 2>&1

echo [%DATE% %TIME%] 완료 >> logs\sync.log
```

`logs\` 폴더 미리 생성:
```bat
mkdir C:\[실제설치경로]\project99\logs
```

#### 작업 스케줄러 등록 (PowerShell, 관리자 권한)

```powershell
$bat = "C:\[실제설치경로]\project99\sync_and_retrain.bat"

$action   = New-ScheduledTaskAction -Execute $bat
$trigger  = New-ScheduledTaskTrigger -Daily -At "03:00AM"
$settings = New-ScheduledTaskSettingsSet `
                -RunOnlyIfNetworkAvailable `
                -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "project99-sync" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest
```

확인 및 즉시 테스트:
```powershell
Get-ScheduledTask -TaskName "project99-sync"
Start-ScheduledTask -TaskName "project99-sync"
```

실행 로그는 `logs\sync.log`에 쌓입니다.

### 방법 B — Make / Zapier (실시간, 선택)

노션은 공식 Webhook을 지원하지 않으므로, 실시간 감지가 필요하면:

1. **Make(구 Integromat)** 또는 **Zapier**에서 노션 DB 변경 트리거 설정
2. 변경 발생 시 로컬 ngrok 터널 또는 배포된 서버 API 호출
3. 해당 엔드포인트에서 `main.py` + `regression_summary.py` 실행

일반적인 경우 방법 A로 충분합니다.

---

## 7. 주요 파라미터

### score_plan.py

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `--program` | 필수 | 지원 사업명 (예비창업패키지, 초기창업패키지 등) |
| `--mode` | `7types` | `7types` (AUC 0.678) / `all` (AUC 0.640) |
| `--organizer` | None | 주관기관명 (지역 일치 피처 판정에 활용) |
| `--no-save` | False | 리포트 파일 저장 안 함 |
| `--text` | None | PDF 대신 텍스트 직접 입력 |

### main.py 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `SAMPLE_SIZE` | `0` | 0=전체, N=최근 N건만 처리 |
| `CONCURRENCY` | `3` | API 병렬 요청 수 |

---

## 8. 모델 구조 (요약)

```
피처 50개 (0/1 이진) → Lasso 로지스틱 회귀 → 합격 확률
                         ↑
              rubric_weights_7types.json 가중치 (lasso_strength, direction)
```

점수 계산:
```
intercept = log(서류합격률 / (1 - 서류합격률))
log_odds  = intercept + Σ(lasso_strength × direction × 피처값)
확률       = 1 / (1 + exp(-log_odds))
```

---

## 9. 알려진 제약

- `rubric_weights_*.json` 내 NaN 값은 정상 (비유의 피처). `_load_json_with_nan()` 함수로 처리됨.
- 암호화된 PDF는 pdfplumber 추출 실패 → 해당 건 자동 skip.
- `SAMPLE_SIZE=0` 전체 실행 시 815건 기준 약 2~3시간 소요 + API 비용 발생.
- AUC 0.678 — 예측 참고용. 합격 보장 아님.

---

## 10. 다음 개발 단계 (4단계)

사업계획서 **초안 자동 작성** 기능 추가 예정:

```
아이템 요약 + 팀 정보 입력
    ↓
Claude로 8대 섹션 초안 생성
    ↓
score_plan.py 자동 채점
    ↓
약점 섹션 재생성 반복 (목표 합격 확률 달성까지)
```

참고: `delivery/guides/01_사업계획서_작성가이드.md`

---

문의: blancon0717@gmail.com

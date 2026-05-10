# scripts/ 디렉토리

이 디렉토리에는 데이터 전처리 및 유지보수용 일회성 스크립트가 담겨 있습니다.
백엔드 서버나 앱에서 자동으로 호출되지 않으며, 필요할 때 수동으로 실행합니다.

---

## merge_programs.py

### 용도

여러 소스에서 수집한 지원사업 공고 CSV를 하나로 병합하여
`data/programs/support_programs.csv`를 갱신합니다.

- 기존 `support_programs.csv`의 `지역`, `program_code`, `설명`, `상태` 메타데이터 보존
- 신규 공고 CSV에서 `name`, `연차`, `특화분야`, `지원시기`, `최대지원금액` 정보 업데이트
- 지역·상태 정보는 규칙 기반으로 자동 추론 (기존 메타 우선)

### 실행 시점

**반기 1회 권장** — 통상 3월·9월, 주요 지원사업 공고 갱신 시기 전후

다음 경우에도 수동 실행:
- 신규 지원사업 공고가 대량 추가된 경우
- 기존 공고의 지원 시기·금액 정보가 업데이트된 경우

### 실행 방법

```bash
cd bizplan-mvp
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 1. 새 공고 CSV를 프로젝트 루트에 위치
# 2. merge_programs.py 상단 NEW_CSV 경로를 새 파일명으로 수정
# 3. 실행
python scripts/merge_programs.py
```

실행 후 `data/programs/support_programs.csv` 상단 주석의 날짜와
아래 업데이트 이력 표를 함께 갱신하세요.

### 데이터 출처

| 소스 | 설명 | 비고 |
|------|------|------|
| 중소벤처기업부 K-Startup | k-startup.go.kr 공고 목록 | 주요 출처 |
| 창업진흥원 공고 | 사업별 개별 공고 페이지 | 세부 내용 확인용 |
| 주요 지원사업 공고 모음.csv | Notion DB에서 export한 내부 큐레이션 목록 | 현재 사용 중인 입력 파일 |

### 마지막 업데이트 날짜

| 날짜 | 업데이트 내용 | 담당자 |
|------|-------------|--------|
| 2026-05-07 | 초기 병합 완료, 89개 프로그램 수록 | 진석 |

> 업데이트 시 이 표에 행을 추가하고,
> `data/programs/support_programs.csv` 상단 `# Last updated:` 주석도 함께 수정하세요.

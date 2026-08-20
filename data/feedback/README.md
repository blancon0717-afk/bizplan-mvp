# 블랜 팀장 피드백 데이터셋

> 팀장(손명훈)이 팀원에게 준 사업계획서 작성 피드백을 Before/After 구조로 정리한 데이터입니다.
> AI 어시스턴트에 컨텍스트로 붙여넣어 바로 활용 가능합니다.

---

## 파일 목록

| 파일 | 설명 | 항목 수 |
|------|------|---------|
| `사업계획서_피드백.json` | 실제 클라이언트 사업계획서 피드백 (Before/After + OCR) | 39개 |
| `공통_피드백.json` | 모든 문서에 공통 적용되는 기본 피드백 | 9개 |
| `sample.json` | 각 파일 샘플 1개씩 (구조 파악용) | - |

---

## 데이터 구조

### 사업계획서_피드백.json

```json
{
  "category": "사업계획서",
  "project": "2025_클라이언트명_담당컨설턴트명",
  "round": "1차 피드백",
  "feedback_text": "팀장이 남긴 피드백 텍스트",
  "before_images": [
    {
      "url": "S3 이미지 URL (만료됨 — 아래 주의사항 참고)",
      "ocr": "이미지에서 추출한 텍스트 전체 (Claude Vision OCR)"
    }
  ],
  "after_text": "반영 후 변경 내용 설명",
  "after_images": [
    {
      "url": "S3 이미지 URL (만료됨)",
      "ocr": "반영 후 이미지에서 추출한 텍스트"
    }
  ]
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `category` | string | 항상 `"사업계획서"` |
| `project` | string | `연도_클라이언트명_담당자명` 패턴 |
| `round` | string | `"1차 피드백"`, `"2차 피드백"` 등 |
| `feedback_text` | string | 팀장 코멘트. 빈 문자열일 수 있음 |
| `before_images` | array | 피드백 전 캡쳐본. 0개 이상 |
| `before_images[].url` | string | S3 signed URL — **만료됨** |
| `before_images[].ocr` | string | OCR 추출 텍스트. **핵심 활용 필드** |
| `after_text` | string | 반영 설명. 없으면 빈 문자열 |
| `after_images` | array | 반영 후 캡쳐본. 0개 이상 |
| `after_images[].ocr` | string | OCR 추출 텍스트. **핵심 활용 필드** |

---

### 공통_피드백.json

```json
{
  "category": "공통 피드백",
  "feedback_text": "모든 문서에 공통 적용되는 피드백",
  "children": ["하위 예시나 보충 설명"]
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `category` | string | 항상 `"공통 피드백"` |
| `feedback_text` | string | 피드백 본문 |
| `children` | array\<string\> | 보충 설명. 없으면 빈 배열 |

---

## 주의사항

### 이미지 URL 만료
`url` 필드의 S3 signed URL은 수집 시점(2026-05-06)에서 1시간 후 만료됩니다.
이미지 원본이 필요하면 Notion API로 재수집해야 합니다.
**`ocr` 필드의 텍스트는 영구 유효합니다.**

### OCR 신뢰도
Claude Haiku Vision으로 추출했습니다.
- 전반적으로 높은 정확도
- 표(Table)는 마크다운 형태로 변환됨
- 이미지 내 메모/댓글도 함께 추출됨 (노션 코멘트 포함)
- 흐릿한 이미지에서 오인식 가능

### 빈 필드 처리
- `feedback_text == ""` → 이미지만 있는 피드백
- `after_text == ""` → 반영 설명 없이 이미지만 있음
- `before_images == []` → 텍스트 피드백만 존재

### 시각적 피드백 (붉은 네모 등 이미지 직접 표기)
`round` 필드가 `"3페이지"` 등 차수가 아닌 페이지 번호인 경우, 텍스트 코멘트 없이
이미지에 붉은 네모 등으로 직접 표기한 피드백입니다.
- `feedback_text`, `after_text`, `after_images` 모두 빈 값
- OCR은 문서 본문 텍스트만 추출하므로 **어떤 부분을 지적했는지 정보가 소실됨**
- 이미지 URL도 만료되어 원본 확인 불가
- **지침화/학습 데이터로 활용 불가** — 현재 데이터셋에 2건 포함

---

## 활용 방법

### 기본 로드

```python
import json

sabup = json.load(open("사업계획서_피드백.json", encoding="utf-8"))
common = json.load(open("공통_피드백.json", encoding="utf-8"))
```

### 필터링

```python
# 특정 프로젝트 피드백
project_items = [x for x in sabup if "클라이언트명" in x["project"]]

# Before/After가 모두 있는 완전한 사례만
complete = [x for x in sabup
            if x["before_images"] and x["after_images"] and x["after_text"]]

# 키워드 검색 (피드백 텍스트 + OCR 모두)
keyword = "표"
matched = [x for x in sabup
           if keyword in x["feedback_text"]
           or any(keyword in img["ocr"] for img in x["before_images"])]
```

### AI 학습 데이터(instruction-response 쌍)로 변환

```python
pairs = []
for item in sabup:
    if item["before_images"] and item["after_text"]:
        pairs.append({
            "instruction": item["before_images"][0]["ocr"],
            "feedback": item["feedback_text"],
            "improved": item["after_text"]
        })
```

### AI 프롬프트 컨텍스트로 바로 사용

```python
# 공통 피드백을 시스템 프롬프트에 주입
rules = "\n".join(
    f"- {x['feedback_text']}" + (
        "\n" + "\n".join(f"  - {c}" for c in x["children"]) if x["children"] else ""
    )
    for x in common
)

system_prompt = f"""당신은 사업계획서 피드백 전문가입니다.
아래 공통 피드백 규칙을 항상 적용하세요:

{rules}
"""
```

---

## 데이터 현황

- 수집 일시: 2026-05-06
- 사업계획서 프로젝트 수: 38개 (2024~2026년)
- 총 피드백 아이템: 39개
- 공통 피드백: 9개
- OCR 엔진: Claude Haiku Vision (claude-haiku-4-5-20251001)
- 원본 소스: Notion 블랜 내부 피드백 DB

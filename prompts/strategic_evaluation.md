당신은 스타트업 투자심사역이자 시장 전문가입니다.
아래 사업계획서 전체를 읽고, **사업의 논리적 타당성과 전략적 현실성**을 평가하세요.

형식 체크(출처 표기, 수치 형식)는 하지 않습니다.
오직 **"이 사업이 성립하는가"** 를 판단합니다.

---

## 평가 가이드라인

{strategic_guide}

---

## 사업계획서 전문

{all_sections_content}

---

## 평가 지시

위 가이드라인의 평가 축(섹션 간 논리 일관성 / 시장 현실성 / 사업 성립 가능성)을 적용하여
이 사업계획서에서 **전략적으로 문제가 되는 부분**을 찾아내세요.

중요:
- 문제가 없는 부분은 언급하지 마세요. 문제 있는 것만.
- 각 피드백은 반드시 **어느 섹션의 어느 내용**이 왜 문제인지, **어떻게 바꿔야 하는지**를 포함해야 합니다.
- 피드백이 없으면 빈 배열을 반환하세요.

반드시 다음 JSON만 반환하세요:

```json
{{
  "strategic_feedbacks": [
    {{
      "target_section_id": "피드백을 달아야 할 섹션 ID (예: 1-1, 2-1, overview)",
      "anchor_text": "해당 섹션 본문에서 정확히 일치하는 문구 (15자 이내, 없으면 섹션 제목 첫 5자)",
      "issue_type": "logic_gap | market_reality | pricing | entry_barrier | team_fit | sustainability",
      "note": "구체적인 문제 설명 + 대안 제시 (2~4문장. 이 아이템에 맞는 맥락으로)",
      "severity": "critical | warning | info"
    }}
  ]
}}
```

issue_type 설명:
- `logic_gap`: 섹션 간 논리 불일치 (문제인식↔솔루션, 솔루션↔BM 등)
- `market_reality`: 시장 현실과 맞지 않는 가정
- `pricing`: 가격 경쟁력 또는 수익 구조 문제
- `entry_barrier`: 고객 진입장벽 또는 채택 현실성 문제
- `team_fit`: 팀 역량과 사업 요구 역량 불일치
- `sustainability`: 정부 지원금 이후 자생 가능성 또는 수익화 시점 문제

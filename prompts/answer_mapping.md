# 답변-섹션 매핑 요청

## 작성 대상 섹션
**[{section_id}] {section_title}**
카테고리: {section_category}
태그: {section_tags}
요구사항: {section_instructions}

---

## 전체 인터뷰 답변 목록

아래는 사용자의 모든 인터뷰 답변입니다. 각 답변에 고유 ID (qid)가 있습니다.

{all_answers_block}

---

## 작업 지시

위 답변들 중 **"{section_title}" 섹션 작성에 사용할 수 있는 답변**을 선별하세요.

선별 기준:
1. 섹션 태그와 의미적으로 연관되는 답변
2. 섹션 요구사항에 직접 기여하는 답변
3. 배경 정보로 유용한 답변 (간접적)

반드시 JSON 형식으로 응답:
```json
{
  "primary_qids": ["가장 직접적으로 섹션에 쓰일 답변 ID들"],
  "supporting_qids": ["배경/보강으로 쓰일 답변 ID들"],
  "reasoning": "선별 근거 간단히"
}
```

다른 설명 금지, JSON만.

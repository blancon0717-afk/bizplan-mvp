# 프레임워크 초안 작성 요청

## 작성할 섹션
**[{section_id}] {section_title}**
(상위 섹션: {parent_title})

---

## 적용할 작성 방법론 (DRAFT_WRITING_GUIDE)

{skills_block}

---

## AI가 인터뷰 답변을 요약·정제한 컨텍스트 (원문 그대로가 아닌 AI 재구성 텍스트임. 이 블록에서 온 내용은 반드시 llm_inferred로 분류할 것)

{answers_block}

---

## 작성 지시

위의 컨텍스트와 DRAFT_WRITING_GUIDE의 해당 섹션 기준에 맞게 **{section_title}** 섹션의 초안을 작성하세요.
{today_date_note}
특별 주의:
1. 답변이 없거나 부족해도 반드시 초안을 작성하되, 아이템/서비스 설명을 기반으로 추론하세요.
   추론 부분은 반드시 severity=critical 인라인 메모로 'AI 추론 내용입니다. 실제 정보로 교체해주세요'를 달아주세요.
   **content_segments를 빈 배열로 반환하는 것은 절대 금지입니다.**

2. 수치 및 출처 처리 규칙: `[출처 필요]` `[추정값]` `[수치 필요]` 태그를 본문에 절대 삽입하지 말 것.

3. **inline_suggestions anchor_text 작성 규칙 (절대 준수)**:
   - anchor_text는 반드시 content_segments의 text 안에 글자 하나도 다르지 않게 그대로 존재하는 문구여야 함
   - anchor_text 길이는 10~25자 이내로 제한
   - 제목(■로 시작하는 줄)에서 추출 금지

4. 출처 표기 규칙: 외부 데이터(통계청, 시장조사기관 등)를 인용할 때만 표기. 자사가 직접 작성한 것을 출처로 표기하는 것 절대 금지.

반드시 JSON 형식으로만 응답하세요. 다른 설명 금지.

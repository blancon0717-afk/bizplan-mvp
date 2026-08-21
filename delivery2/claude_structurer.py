from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
SCHEMA_PATH = Path(__file__).parent / "rubric_schema.json"

# Haiku 4.5 가격 (USD per 1M tokens)
PRICE_INPUT_PER_M = 1.0
PRICE_OUTPUT_PER_M = 5.0

# 트랙 B로 분리되어 노션 메타에서 직접 계산하는 피처. Haiku에 묻지 않음.
META_HANDLED = {"지방지원 여부"}

# 피처별 PDF 검색 가이드 — 어디를 보고 판정해야 하는지 Haiku에게 알려줌.
SECTION_HINTS: dict[str, str] = {
    "소재지+주관기관 일치 여부": '회사 주소가 주관기관 운영 지역과 같은 광역(시/도)에 속하면 Y. 주관기관: "{주관기관}"',
    "아이템특이성": "기존 시장에 없는 차별점이 본문에 명시되어 있으면 Y (강조/굵은 글씨/문구 \"기존과 차별화\" 등).",
    "여성 대표 여부": "대표자가 여성으로 명시되어 있거나 여성기업 인증/여성 대표 표기가 있으면 Y.",
    "청년 여부(만 39세 이하)": "대표자 생년월일이 1986년 이후이거나 '청년창업', '만 39세 이하' 명시 있으면 Y.",
    "대표자 해당 아이템 산업 경험/기술 보유 여부": "대표자 이력 섹션에 해당 산업 종사 경력/관련 학위가 명시되면 Y.",
    "대표자 사업 경험 여부 (매출 1억 이상)": "대표자가 이전 사업에서 매출 1억 이상 달성 명시한 경우만 Y.",
    "아이템 관련 경력 증빙": "대표자/팀의 관련 경력이 구체적 회사명·직위·기간으로 명시되면 Y.",
    "개발인력(sw) 보유(팀원 기준)": "팀 구성 표/조직도에서 개발자(SW) 인원수. 재직/채용완료만 집계, '채용 예정'은 제외. 본문에 명시 없으면 0.",
    "제조 전문 인력 (기술자) 보유(팀원 기준)": "팀 구성에서 제조/기술자 인원수.",
    "R&D 인력 보유(팀원 기준)": "팀 구성에서 연구개발 인력 인원수.",
    "영업 & 유통 마케팅 인력 보유 (팀원 기준)": "팀 구성에서 영업/유통/마케팅 담당 인원수. 재직/채용완료만 집계, '채용 예정'은 제외.",
    "디자인 인력 보유 (팀원 기준)": "팀 구성에서 디자인 담당 인원수.",
    "해당 시장 전문가(팀원 기준)": "자문위원/멘토 중 해당 시장 전문가 수.",
    "개발(S/W) 네트워크 보유": "'네트워크 현황', '협력사', '파트너' 섹션에 외부 개발 협력처 명시되면 Y.",
    "제조 전문 네트워크 (기술자) 보유": "외부 제조사·기술협력처 명시되면 Y.",
    "R&D 인력 or 자체 학습 데이터 네트워크 보유": "외부 R&D 협력 기관/학교 또는 자체 학습 데이터 보유 명시되면 Y.",
    "영업 및 마케팅 네트워크 보유": "외부 마케팅·유통·판매 협력처가 구체적 실명(회사/기관명)으로 명시되면 Y. 일반적 계획 서술만 있으면 N.",
    "디자인 네트워크 보유": "외부 디자인 협력처 명시되면 Y.",
    "해당 시장 전문 자문 네트워크 보유 위원": "자문위원/멘토단 명단/표가 있으면 Y.",
    "수요처 네트워크 보유": "잠재 수요처/고객사 명단·로고가 명시되면 Y.",
    "필수 재료 공급처 네트워크 보유": "원재료 공급처/Supplier가 명시되면 Y.",
    "실제 제조 OR 서비스 사진 보유 여부": "제품/서비스 실물 사진 또는 이미지 캡션 명시되면 Y.",
    "실제 실험 사진 보유 여부": "실험·테스트 사진 또는 데이터 이미지 명시되면 Y.",
    "FGI": "FGI(Focus Group Interview)/심층 인터뷰 실시 명시되면 Y.",
    "MVP 테스트 실행 여부": "MVP/프로토타입 제작 또는 시범 운영 명시되면 Y.",
    "자체 생산 시설 보유": "자체 공장/생산시설 보유 명시되면 Y.",
    "수요처 확보 여부": "구매 의향서/계약 등으로 수요처 확보 명시되면 Y.",
    "해외 진출 가능성": "해외 진출 계획/수출 계획/해외 파트너 명시되면 Y.",
    "아이템 관련 특허 보유 개수 (등록 기준)": "등록 완료된 특허 개수만 정수로. 출원중은 제외.",
    "아이템관련 특허 출원 여부 (출원상태 YNLY)": "출원중인 특허가 1건이라도 있으면 Y.",
    "아이템 관련 인증 & 디자인 특허 & 상표 등록 개수 -> 기업 인증 아님 주의": "디자인특허+상표등록+제품인증 합계. 기업인증(벤처/이노비즈)은 제외.",
    "비즈니스 모델 다양화": "수익 모델(BM)이 몇 가지 명시됐는지 정수.",
    "구체적 마케팅 협력처 보유 여부 (마케팅이 바로 가능한 수준 셋팅 완료)": "협력처/채널 실명과 셋팅 완료 상태가 함께 명시된 경우만 Y. 계획·예정 단계는 N.",
    "투자의향서 확보 여부": "투자의향서/LOI 명시되면 Y.",
    "견적서 보유 개수": "확보한 견적서 개수.",
    "기대출 및 보증 금액 (최근 1년)": "최근 1년 내 대출·보증 금액(원 단위 정수). 명시 없으면 0.",
    "MOU 보유 개수": "체결 완료된 MOU·업무협약·파트너쉽·입점 협약의 개수. '체결 예정/논의 중/제휴 추진'은 제외. '제휴 완료'는 협약 체결 근거가 함께 있을 때만 포함.",
    "데이터 보유 여부 (고객 및 아이템 관련) -> AI/빅데이터 가공용 Yr 잠재고객 데이터 등": "자체 보유 고객·아이템·학습 데이터 명시되면 Y.",
    "대표자/팀원 관련 자격증서": "대표자/팀원 자격증·면허 명시되면 Y.",
    "납품 확정 계약서 보유 여부": "확정 납품 계약서 명시되면 Y.",
    "구매 의향서 보유": "구매의향서/구매확약서 명시되면 Y.",
    "기술임치": "기술임치제도 등록 명시되면 Y.",
    "기술이전협약서": "기술이전 협약서 명시되면 Y.",
    "기타 기업 인증 개수": "벤처기업·이노비즈·메인비즈 등 기업인증 개수.",
    "수상이력": "공모전/대회 수상 이력 명시되면 Y.",
    "직전 매출 (수출 제외)": "최근 회계연도 매출액(원 단위 정수). 예: 5억 → 500000000.",
    "고용": "현재 정규직/계약직 직원 수(명).",
    "직전 수출": "직전 연도 수출액(원 단위 정수).",
    "누적 투자": "지금까지 누적 투자유치 금액(원 단위 정수).",
    "총매출 (직전매출 + 직전 수출) -> 자동 계산 수정금지": "직전매출+직전수출 합산(원 단위 정수). 명시 없으면 0.",
}


SYSTEM = """너는 한국 정부지원사업 사업계획서(PDF)를 객관적 루브릭으로 평가하는 심사 보조 AI다.

절대 원칙:
1. **본문에 명확히 기술된 것만** 근거로 판정. 추측·상상 금지.
2. 본문 근거가 약하거나 애매하면 N / 0 (보수적 판정).
3. 출력은 유효한 JSON 객체 하나. 그 외 어떤 설명도 금지.
4. Y/N 타입은 정확히 "Y" 또는 "N" 문자열로, 숫자 타입은 **정수**로 응답.
5. **숫자 단위는 반드시 따른다**: 금액=원(예: 5억→500000000), 인원=명, 개수=정수개.
6. 각 피처별 가이드(어디를 보고 무엇을 찾는지)를 따른다.
7. **'예정·계획·추진 중·논의 중' 상태는 보유로 인정하지 않는다** (피처 가이드가 계획 인정을 명시한 경우만 예외)."""

USER_TMPL = """다음 피처들을 PDF 사업계획서 본문 근거로 판정하라.

이 사업계획서의 주관기관: {주관기관}

<피처 (총 {n}개) — [type] "name" — 판정 가이드>
{schema}

<추가 출력>
"특이성_키워드": 사업계획서가 강조하는 차별점/특이성을 보여주는 핵심 키워드 3~5개를 string 배열로.
                예시 형식: ["AI 기반 진단", "자체 알고리즘", "특허 기술"]

<사업계획서 원문>
{text}

출력: 모든 피처 + 특이성_키워드 를 포함한 단일 JSON 객체."""


def _load_schema() -> list[dict]:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [f for f in data["features"] if f["type"] != "empty"]


def _compact_schema(features: list[dict], 주관기관: str | None) -> str:
    lines = []
    for i, f in enumerate(features, 1):
        if f["name"] in META_HANDLED:
            continue
        t = "Y/N" if f["type"] == "yn" else "숫자"
        hint = SECTION_HINTS.get(f["name"], "")
        if "{주관기관}" in hint:
            hint = hint.format(주관기관=주관기관 or "(미상)")
        lines.append(f'{i}. [{t}] "{f["name"]}" — {hint}')
    return "\n".join(lines)


class Structurer:
    def __init__(
        self,
        api_key: str,
        model: str = MODEL,
        max_input_chars: int = 45_000,
        max_tokens: int = 2200,
        thinking: dict | None = None,
    ):
        # thinking 기본 활성 모델(Sonnet 5 등)은 max_tokens를 사고에 소모해 JSON이 잘린다.
        # 그런 모델엔 thinking={"type": "disabled"} + 여유 max_tokens를 넘길 것.
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_input_chars = max_input_chars
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.features = _load_schema()
        self.haiku_features = [f for f in self.features if f["name"] not in META_HANDLED]
        # Cumulative usage tracker (thread-safe enough for our additive use)
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    @property
    def cost_usd(self) -> float:
        return self.input_tokens / 1_000_000 * PRICE_INPUT_PER_M + self.output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M

    def usage_str(self) -> str:
        return (
            f"calls={self.calls} in={self.input_tokens:,} out={self.output_tokens:,} "
            f"cost=${self.cost_usd:.3f}"
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15), reraise=True)
    def structure(self, text: str, 주관기관: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return self._empty()
        snippet = text[: self.max_input_chars]
        schema_str = _compact_schema(self.features, 주관기관)
        extra: dict[str, Any] = {}
        if self.thinking is not None:
            extra["thinking"] = self.thinking
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            **extra,
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": USER_TMPL.format(
                    schema=schema_str,
                    n=len(self.haiku_features),
                    text=snippet,
                    주관기관=주관기관 or "(미상)",
                ),
            }],
        )
        # Accumulate usage
        self.calls += 1
        self.input_tokens += msg.usage.input_tokens
        self.output_tokens += msg.usage.output_tokens
        # 잘린 응답은 데이터로 취급하지 않는다 — 기본값(N/0)으로 위장되면
        # 오염 행과 정상 행을 사후 구분할 수 없다 (agent-design §1).
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(
                f"응답 잘림(max_tokens={self.max_tokens}, model={self.model}) — "
                "max_tokens 상향 또는 thinking 비활성 후 재시도"
            )
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        parsed = _parse_json(raw)
        if not parsed:
            raise RuntimeError(f"루브릭 JSON 파싱 실패 (raw len={len(raw)})")
        return self._normalize(parsed)

    def _empty(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.haiku_features:
            out[f["name"]] = "N" if f["type"] == "yn" else 0
        out["특이성_키워드"] = []
        return out

    def _normalize(self, parsed: dict) -> dict[str, Any]:
        # Haiku가 키를 멋대로 정규화하므로 (예: "1_소재지_주관기관_일치_여부"),
        # 양쪽 키를 같은 방식으로 정규화한 뒤 매칭한다.
        parsed_norm = {_norm_key(k): v for k, v in parsed.items()}
        out: dict[str, Any] = {}
        for f in self.haiku_features:
            name = f["name"]
            v = parsed_norm.get(_norm_key(name))
            if f["type"] == "yn":
                if isinstance(v, str) and v.strip().upper() in ("Y", "N", "O"):
                    out[name] = "Y" if v.strip().upper() in ("Y", "O") else "N"
                elif isinstance(v, bool):
                    out[name] = "Y" if v else "N"
                else:
                    out[name] = "N"
            else:
                if isinstance(v, (int, float)):
                    out[name] = int(v)
                elif isinstance(v, str):
                    m = re.search(r"-?\d+", v.replace(",", ""))
                    out[name] = int(m.group()) if m else 0
                else:
                    out[name] = 0
        # 특이성 키워드 — 정규화 키로도 시도
        kw = parsed.get("특이성_키워드") or parsed_norm.get(_norm_key("특이성_키워드")) or []
        if isinstance(kw, str):
            kw = [s.strip() for s in re.split(r"[,;|]", kw) if s.strip()]
        elif not isinstance(kw, list):
            kw = []
        out["특이성_키워드"] = [str(x) for x in kw][:8]
        return out


def _norm_key(s: str) -> str:
    """피처명 정규화: 번호 prefix + 모든 공백/특수문자 제거 + lowercase."""
    s = re.sub(r"^\d+[_\.\-:]?\s*", "", s)
    s = re.sub(r"[\s_+\-()/&,.\[\]:;!?'\"]", "", s)
    return s.lower()


def _parse_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    log.warning("failed to parse rubric JSON (len=%d)", len(raw))
    return {}

"""shared/llm.py — Claude LLM 단일 진입점 (claude-code-sdk 기반).

Max 구독 사용 — 외부 API 비용 0.

사용:
    from shared.llm import invoke_text
    text = invoke_text("writer", "프롬프트")

    from shared.llm import chat
    llm = chat("router").bind_tools(my_tools)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


# LangChain 프로바이더 감지용 센티넬 — 실제 API 호출 금지
# SDK subprocess 에는 별도로 "" 오버라이드해서 OAuth 모드 강제 (아래 _run_sdk_sync 참조)
os.environ.setdefault("ANTHROPIC_API_KEY", "max-subscription-no-api-cost")

# 외부 텔레메트리 전송 차단 (OpenTelemetry 계열 라이브러리 공통)
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# ★ 사용자 박제 2026-06-07 — Claude CLI 잔존 흔적 일소.
# 모듈 import 시 단 1회 message_parser monkey-patch (rate_limit_event 등 미지 type 흡수)
# + PATH 보장 (/opt/homebrew/bin 자동 prepend). 데몬·cron 환경에서도 안전.
from shared import claude_sdk_compat as _sdk_compat  # noqa: E402,F401


# ── 모델 표준 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelSpec:
    alias: str           # 자비스 alias ("writer", "router" 등)
    model_id: str        # Claude 모델 ID
    max_tokens: int
    temperature: float
    description: str = ""
    # ★ 배경(비긴급) 작업용 alias 인가 — 발행창에서는 한도를 글 작성에 양보하고 *보류* 된다.
    #   (사용자 박제 2026-07-25: "03·09·02·06·08 이 도는 동안 LLM 은 오로지 글 작성에만")
    #   여기 선언 한 곳에서 `_BG_ALIASES` 를 파생 — 별도 목록을 두면 alias 추가 시 드리프트.
    background: bool = False
    # ★ 프롬프트 캐시를 쓸 것인가 (ERRORS [541], 사용자 판단 2026-07-27).
    #   배수는 `_CACHE_WRITE_MULT` / `_CACHE_READ_MULT` **단독** — 여기에 숫자를 적지 말 것
    #   (2026-07-27 에 쓰기 배수가 1.25→2.0 으로 정정됐고, 그때 이 줄의 사본이 낡았다).
    #   한 번 비싸게 쓰고 여러 번 싸게 읽어야 이득이며, **읽지 못하면 프리미엄을 그냥 버린다.**
    #   손익분기(재사용 배수)는 `cache_selfcheck()` 가 위 두 상수에서 파생해 판정한다.
    #   7일 실측 근거는 아래 MODELS 각 항목 주석에 박제. 판정이 낡으면 `cache_selfcheck()` 가 알린다.
    cache: bool = True


# ★ 모델 계층 — 사용자 박제 2026-07-06 (ADR 017): Sonnet 5 단일 모델 통일 (ADR 015 폐지).
#   alias→model_id 매핑은 이 MODELS dict 가 시스템 전체의 유일 소스 — 다른 곳은 전부 파생.
# 자비스 모델 카탈로그 — 한 곳에서 관리
MODELS: dict[str, ModelSpec] = {
    # ══ writer 계열 — `writer_{long|short}_{용도}` (사용자 박제 2026-07-26) ══════
    #
    #   ★ 왜 바꿨나: 종전 `writer` / `writer_fast` 두 개뿐이었고, ADR 017(모델 단일 통일)
    #     이후엔 **모델·토큰·온도가 완전히 동일**해 `_fast` 라는 이름이 거짓이 됐다
    #     (실측: model_id·max_tokens·temperature 3개 전부 같음). 대시보드 "실시간 호출
    #     내역"에 `writer` 만 줄줄이 찍혀 *무슨 작업 중인지 구분이 안 됐다*.
    #
    #   ★ 이름 규칙: `long`=긴 생성물(본문·인포그래픽), `short`=짧은 조각(제목·색상·판정).
    #     길이는 *실제 max_tokens* 로 뒷받침한다 — 이름과 값이 어긋나면 그게 또 거짓말이다.
    #
    #   ★ ① 단일 진입점: alias→스펙은 이 dict 가 유일 소스. `_BG_ALIASES`·
    #     `_PUBLISH_ESSENTIAL_CAP`·구 alias 별칭까지 **전부 여기서 파생**한다.
    #   ★ ② 동적 설계: 별도 목록을 두지 않는다. alias 를 추가하면 파생물이 자동으로 따라온다.
    #   ★ ③ 모든 곳: 경제·테마 × 네이버·티스토리 4조합이 같은 alias 를 쓴다
    #     (분기는 alias 가 아니라 프롬프트에 있다).
    #
    #   구 `writer`/`writer_fast` 는 아래에서 **별칭으로 생존** — 과거 DB 기록(writer 99건·
    #   writer_fast 150건)은 그 시점의 사실이므로 개변하지 않는다(사용자 판단 2026-07-26).

    # ── long — 긴 생성물 ────────────────────────────────────────
    "writer_long_body": ModelSpec(
        alias="writer_long_body",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.4,
        description="블로그 본문 대본 — 도입부·섹션·감성문단·면책 (헌법 규정 준수)",
        # ★ 캐시 끔 — 7일 실측 **재사용 0.00배** (생성 191,224 / 읽기 0).
        #   ⚠️ 종전 사유("12분 간격이라 TTL 5분을 넘긴다")는 **오진이었다** (ERRORS [542]):
        #     TTL 프로브 결과 **13분 25초 뒤에도 회수**된다 → 12분 간격은 TTL *안*이다.
        #     진짜 원인은 **블록 전체가 바이트 일치하지 않는 것**. 부분 프리픽스 재사용은
        #     일어나지 않는다(앞 7,666자 동일 + 꼬리 1줄만 다른 3회 연속 → read 전부 0).
        #     그래서 NV/TS 의 system 이 문체 한 줄·학습지침 때문에 달라지는 동안은
        #     TTL 을 어떻게 잡아도 회수가 안 된다.
        #   ★ system 은 이제 플랫폼 무관이다(`draft_writer.build_platform_block`, md5 일치 확인).
        #     그런데도 **켜지 않는 이유는 따로 있다 — 호출 횟수가 모자란다**:
        #       손익분기 재사용 = (2.0-1)/(1-0.1) = 1.111배 → 같은 프리픽스를 **2.11회** 이상
        #       호출해야 이득인데, 발행쌍은 **정확히 2회**다 (NV→TS, 실측 3쌍 전부 재시도 0).
        #     실데이터 검산: 캐시 ON 191,632 (07-26 create 47,942+47,874 ×2.0)
        #                  vs 캐시 OFF 101,378 (07-27 input 50,950+50,428 ×1.0)
        #                  vs 동일화 후 ON 107,285 (계산) → **여전히 OFF 가 5.8% 싸다.**
        #   → 켜는 조건은 "system 동일" 이 아니라 **"같은 프리픽스 3회 이상"** 이다.
        #     발행 구조가 바뀌어 호출이 3회 이상이 되면(폴백 3-call 상시화·재시도 상례화 등)
        #     그때 `cache_selfcheck()` 가 [C1] 로 알린다. 그 신호를 보고 켤 것.
        cache=False,
    ),
    "writer_long_infographic": ModelSpec(
        alias="writer_long_infographic",
        model_id="claude-sonnet-5",
        max_tokens=11000,   # ★ HTML 인포그래픽은 통짜 출력이라 본문보다 크다
        temperature=0.4,
        description="인포그래픽·SVG HTML 통짜 생성 (가장 긴 출력)",
    ),
    "writer_long_chat": ModelSpec(
        alias="writer_long_chat",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.4,
        description="사용자 자유 문장 응답 (텔레그램 — 사용자 대기 중이라 배경 아님)",
    ),
    "writer_long_learn": ModelSpec(
        alias="writer_long_learn",
        model_id="claude-sonnet-5",
        max_tokens=3500,
        temperature=0.3,
        description="디자인·SEO 학습 (레퍼런스 분석 → 지침 도출)",
        background=True,   # 배경 작업 — 발행창에서 보류
    ),

    # ── short — 짧은 조각 ───────────────────────────────────────
    "writer_short_title": ModelSpec(
        alias="writer_short_title",
        model_id="claude-sonnet-5",
        max_tokens=200,
        temperature=0.7,
        description="제목·소제목·썸네일 문구 (한 줄~두 줄)",
    ),
    "writer_short_cta": ModelSpec(
        alias="writer_short_cta",
        model_id="claude-sonnet-5",
        max_tokens=120,
        temperature=0.9,   # ★ 매번 달라야 하는 문구라 온도가 높다 (헌법 제1-B조 동적 생성)
        description="CTA·맺음 한 줄 (고정 풀 금지 — 매번 새로 생성)",
    ),
    "writer_short_visual": ModelSpec(
        alias="writer_short_visual",
        model_id="claude-sonnet-5",
        max_tokens=4000,   # ★ SVG 조각이 여기 포함돼 short 중에선 크다
        temperature=0.8,
        description="차트 색상·스타일 스펙·SVG 조각 (시각 요소)",
    ),
    "writer_short_analysis": ModelSpec(
        alias="writer_short_analysis",
        model_id="claude-sonnet-5",
        max_tokens=1600,
        temperature=0.2,   # ★ 판정은 흔들리면 안 되므로 온도가 낮다
        description="분석·판정·번역 — 품질 제안·섹터 분류·수치 대조·프롬프트 번역",
        # ★ 캐시 끔 (ERRORS [541]) — 7일 실측 **재사용 0.08배** (생성 20,187 / 읽기 1,696).
        #   호출 묶음이 **3시간 간격**이라 TTL 5분 안에 재호출이 거의 없다.
        #   주간 약 3,520 토큰 절감. (간격이 좁아지면 selfcheck 가 되돌리라고 알린다)
        cache=False,
    ),

    # ── 구 alias (하위호환) — 본문은 위 신규를 가리키는 얇은 별칭 ──
    #   ★ 지우지 않는 이유: 외부 문서(BLOG_SUPREME_LAW·ADR)와 과거 DB 기록이 이 이름을
    #     참조한다. 이름만 남기고 **스펙은 신규에서 파생**해 두 벌 관리를 만들지 않는다.
    "writer": ModelSpec(
        alias="writer",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.4,
        description="[구] writer_long_body 로 대체됨 — 하위호환 별칭",
    ),
    "writer_fast": ModelSpec(
        alias="writer_fast",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.4,
        description="[구] writer_short_* 로 분화됨 — 하위호환 별칭",
    ),
    "router": ModelSpec(
        alias="router",
        model_id="claude-sonnet-5",
        max_tokens=1000,
        temperature=0.0,
        description="마스터 라우터 — 인텐트 분류·도메인 매칭 (Sonnet 5)",
    ),

    # ★ 테마 종목 폴백 (ERRORS [540]) — 종전엔 `router` alias 를 빌려 쓰고 있었다.
    #   `router` 는 "마스터 라우터 인텐트 분류" 인데 실제로는 JARVIS09 가 네이버 테마에서
    #   종목·티커를 못 찾았을 때의 **LLM 폴백**이었다 — 이름이 하는 일과 달라 대시보드가 거짓말을 했다.
    "collect_theme_fallback": ModelSpec(
        alias="collect_theme_fallback",
        model_id="claude-sonnet-5",
        max_tokens=1000,
        temperature=0.6,   # 후보를 넓게 뽑아야 하므로 라우팅(0.0)보다 높다
        description="테마 종목·티커 폴백 — 카탈로그에서 못 찾을 때 (JARVIS09)",
    ),

    # ══ analyzer 계열 — `analyzer_{용도}` (사용자 박제 2026-07-27, ERRORS [540]) ══
    #
    #   ★ 왜 쪼갰나: `analyzer` 하나가 **5개 모듈 13곳**에서 전혀 다른 일을 하고 있었다 —
    #     일일 리뷰 · 차트 데이터 판정 · 근거팩 추출 · 이미지 스펙 · 수집 설계 · 글종류 판정.
    #     대시보드에 `analyzer` 만 찍히니 **무슨 작업이 토큰을 쓰는지 구분이 안 됐다.**
    #     writer 계열을 8개로 쪼갠 것과 같은 이유·같은 방식.
    #   ★ ② 동적 설계: alias 를 여기 추가하면 `_BG_ALIASES`·`_PUBLISH_ESSENTIAL_CAP` 등
    #     파생물이 자동으로 따라온다. 별도 목록을 만들지 않는다.
    #   ★ max_tokens·temperature 를 용도에 맞춘다 — 이름만 나누고 값이 같으면 그게 또
    #     `writer_fast` 같은 거짓말이 된다(ERRORS 직전 교훈).
    "analyzer_quality": ModelSpec(
        alias="analyzer_quality",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.2,
        description="발행 글 품질 분석·일일 리뷰 — 개선 제안 도출",
    ),
    "analyzer_chart": ModelSpec(
        alias="analyzer_chart",
        model_id="claude-sonnet-5",
        max_tokens=1600,
        temperature=0.0,   # 차트 수치 판정 — 흔들리면 안 된다
        description="차트 데이터 판정·단위·랭크 (JARVIS09 chart_data)",
    ),
    "analyzer_evidence": ModelSpec(
        alias="analyzer_evidence",
        model_id="claude-sonnet-5",
        max_tokens=6000,   # 전 문서 단일 호출 (ERRORS [374])
        temperature=0.2,
        description="근거팩 추출 — 수집 문서에서 fact·수치 뽑기",
    ),
    "analyzer_imagespec": ModelSpec(
        alias="analyzer_imagespec",
        model_id="claude-sonnet-5",
        max_tokens=3000,
        temperature=0.3,
        description="이미지 스펙·SVG 코드 생성 (JARVIS06)",
    ),
    "analyzer_plan": ModelSpec(
        alias="analyzer_plan",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.2,
        description="수집 설계 — 리서치 플랜 (ADR 012, 수집 품질의 조타수)",
    ),
    "analyzer_posttype": ModelSpec(
        alias="analyzer_posttype",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.5,
        description="글종류별 섹션 구성 판정 (post_type_specs)",
    ),
    "analyzer": ModelSpec(
        alias="analyzer",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.2,
        description="[구] analyzer_* 로 분화됨 — 하위호환 별칭",
    ),
    "coder": ModelSpec(
        alias="coder",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.1,
        description="코드 수정·patch 생성·자가수정 (Sonnet 5 — 오류 수정 전용)",
        background=True,   # 배경 작업 — 발행창에서 보류
    ),
    "guardian": ModelSpec(
        alias="guardian",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.1,
        description="JARVIS07 오류 분석·패치 생성 (Sonnet 5)",
        background=True,   # 배경 작업 — 발행창에서 보류
    ),
    "architect": ModelSpec(
        alias="architect",
        model_id="claude-sonnet-5",
        max_tokens=10000,
        temperature=0.3,
        description="ARCHITECT 새 에이전트·시스템 설계 (Sonnet 5)",
        background=True,   # 배경 작업 — 발행창에서 보류
    ),
    "diagnostic": ModelSpec(
        alias="diagnostic",
        model_id="claude-sonnet-5",
        max_tokens=6000,
        temperature=0.2,
        description="복잡 multi-cause traceback 진단·근본 원인 추론 (Sonnet 5)",
        background=True,   # 배경 작업 — 발행창에서 보류
    ),
    "learn_eval": ModelSpec(
        alias="learn_eval",
        model_id="claude-sonnet-5",
        max_tokens=4000,
        temperature=0.1,
        description="learned_patterns 등록 게이트 — patch 안전성·정확성·재사용 가치 채점 (Sonnet 5)",
        background=True,   # 배경 작업 — 발행창에서 보류
    ),
    "fact_judge": ModelSpec(
        alias="fact_judge",
        model_id="claude-sonnet-5",
        max_tokens=4000,
        temperature=0.0,
        description="발행 전 사실성 검수 — claim 추출·출처 대조 판정 (Sonnet 5, temp 0 결정성 우선)",
    ),
    "engagement_judge": ModelSpec(
        alias="engagement_judge",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.2,
        description="발행 전 유익성·매력도 채점 — 독자 흡인력 judge (Sonnet 5)",
    ),
}

# 전 모듈 alias→model_id 단일 참조 — MODELS 에서 파생 (중복 리터럴 매핑 금지)
_ALIAS_MODEL: dict[str, str] = {alias: spec.model_id for alias, spec in MODELS.items()}
_DEFAULT_MODEL_ID = MODELS["writer"].model_id


# ════════════════════════════════════════════════════════════════
# ★ 표시용 모델 라벨 — 단일 진실 소스 파생 (SSOT, 사용자 박제 2026-07-04)
# ════════════════════════════════════════════════════════════════
# 웹 대시보드(hub.py)·텔레그램·문서가 모델명을 *하드코딩하지 말고* 이 함수로 파생.
# MODELS 한 곳만 바꾸면 모든 표시가 자동으로 따라온다 (2중·3중 수정 불필요).
# 하드코딩 금지는 precommit `ssot` 카테고리가 강제.

def pretty_model_id(model_id: str) -> str:
    """모델 ID → 사람이 읽는 라벨.

    'claude-sonnet-5'  → 'Sonnet 5'
    """
    s = (model_id or "").replace("claude-", "")
    parts = [p for p in s.split("-") if p]
    if not parts:
        return model_id or "?"
    family = parts[0].capitalize()
    ver: list[str] = []
    for p in parts[1:]:
        if p.isdigit() and len(p) <= 2:   # 버전 조각만 (긴 날짜 접미사 제외)
            ver.append(p)
        else:
            break
    return (f"{family} {'.'.join(ver)}" if ver else family).strip()


def model_label(alias: str) -> str:
    """alias(writer/guardian/…) → 사람이 읽는 모델명. MODELS 에서 파생.

    코드가 모델을 바꾸면 이 라벨을 쓰는 모든 표시(웹·텔레그램)가 자동 갱신된다.
    표시 코드에 모델명 리터럴을 직접 쓰지 말고 이 함수를 호출할 것.
    """
    return pretty_model_id(_ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID))


def model_id(alias: str = "writer") -> str:
    """alias → 실제 모델 ID. **모델 ID 리터럴의 유일한 소유자는 이 모듈**.

    다른 파일에서 모델 ID 문자열을 직접 쓰면 모델 교체 시 그 사본이 그대로 남아
    폐기된 모델을 계속 가리킨다(② 동적 설계 / '복사본을 진실로 믿지 말 것').
    반드시 이 함수로 파생할 것 — precommit `model` 카테고리가 강제한다.
    """
    return _ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID)


def live_model_ids() -> set[str]:
    """현재 살아있는 모델 ID 전부 — 검사·표시가 목록을 박지 않도록 런타임 파생."""
    return set(_ALIAS_MODEL.values())


def get_spec(alias: str) -> ModelSpec:
    """alias 로 ModelSpec 조회."""
    if alias not in MODELS:
        raise KeyError(f"model alias '{alias}' 미등록. {list(MODELS.keys())}")
    return MODELS[alias]


def chat(alias: str = "writer", **overrides) -> Any:
    """LangChain BaseChatModel 호환 어댑터 반환.

    `.invoke(messages)` / `with_structured_output(Pydantic)` / `bind_tools(tools)`
    모두 Claude Code 위에 구현. LangGraph StateGraph 노드로 사용 가능.
    """
    try:
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa
    except ImportError:
        return None
    spec = get_spec(alias)
    return ClaudeSDKChatModel(
        alias=alias,
        model_id=spec.model_id,
        max_tokens=overrides.get("max_tokens", spec.max_tokens),
        temperature=overrides.get("temperature", spec.temperature),
    )


def is_langchain_available() -> bool:
    """LangChain core 사용 가능 여부."""
    try:
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa
        return True
    except ImportError:
        return False


def _build_claude_sdk_chat_model():
    """LangChain 호환 chat model 클래스 lazy 빌드."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field
    import json as _json
    import re as _re
    import uuid as _uuid

    class ClaudeSDKChatModel(BaseChatModel):
        """LangChain BaseChatModel 호환 — claude-code-sdk 위 구현.

        지원: .invoke / with_structured_output / bind_tools
        """
        alias: str = Field(default="writer")
        model_id: str = Field(default=_DEFAULT_MODEL_ID)
        max_tokens: int = Field(default=4000)
        temperature: float = Field(default=0.7)
        bound_tools: Optional[list] = Field(default=None)

        @property
        def _llm_type(self) -> str:
            return "claude-sdk"

        def _sdk_model(self) -> str:
            return _ALIAS_MODEL.get(self.alias, _DEFAULT_MODEL_ID)

        @staticmethod
        def _messages_to_prompt(messages) -> tuple[str, str]:
            """LangChain BaseMessage 리스트 → (system_prompt, user_prompt)."""
            sys_parts, user_parts = [], []
            for m in messages:
                content = getattr(m, "content", str(m))
                # content 가 list 일 때 (tool result 블록·멀티모달) → 텍스트 추출
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", str(b)) if isinstance(b, dict) else str(b)
                        for b in content
                    )
                content = str(content)
                if isinstance(m, SystemMessage):
                    sys_parts.append(content)
                else:
                    user_parts.append(content)
            return "\n\n".join(sys_parts), "\n\n".join(user_parts)

        def _tool_schema_injection(self) -> str:
            """bind_tools 결과 → prompt 안에 tool 스키마 주입."""
            if not self.bound_tools:
                return ""
            specs = []
            for t in self.bound_tools:
                name = getattr(t, "name", str(t))
                desc = getattr(t, "description", "")
                # args schema (LangChain Tool 의 args_schema 또는 .args)
                args = ""
                if hasattr(t, "args_schema") and t.args_schema:
                    try:
                        args = _json.dumps(
                            t.args_schema.model_json_schema().get("properties", {}),
                            ensure_ascii=False,
                        )[:400]
                    except Exception:
                        pass
                specs.append(f"- {name}: {desc[:200]} | args={args}")
            schema_block = "\n".join(specs)
            return (
                "\n\n[사용 가능한 도구]\n" + schema_block +
                '\n\n도구 호출이 필요하면 *마지막 줄* 에 JSON 으로:\n'
                '{"tool_calls": [{"name": "도구명", "args": {...}}]}\n'
                "도구 없이 답변 가능하면 평문으로만."
            )

        @staticmethod
        def _parse_tool_calls(text: str):
            """응답 text 끝의 JSON tool_calls 블록 추출."""
            # 마지막 { ... } JSON 블록
            m = _re.search(r'\{[^{}]*"tool_calls"\s*:\s*\[[^\]]*\][^{}]*\}\s*$',
                           text, _re.DOTALL)
            if not m:
                return None, text
            try:
                obj = _json.loads(m.group())
                tcs = obj.get("tool_calls", [])
                normalized = [
                    {"name": tc.get("name", ""),
                     "args": tc.get("args", {}) or {},
                     "id":   tc.get("id", str(_uuid.uuid4())[:8])}
                    for tc in tcs
                ]
                # 본문에서 JSON 부분 제거
                stripped = text[:m.start()].rstrip()
                return normalized, stripped
            except Exception:
                return None, text

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            system_prompt, user_prompt = self._messages_to_prompt(messages)
            tool_block = self._tool_schema_injection()
            if tool_block:
                system_prompt = (system_prompt + tool_block).strip()
            # ★ 단일 진입점 경유 (ERRORS [543]) — 종전엔 `_run_sdk_sync` 를 **직접** 불러
            #   alias 귀속(_bind_alias) · alias별 캐시정책(_sdk_env) · 회로차단 · 재시도 ·
            #   발행창 배경보류 를 **전부 우회**했다. ERRORS [474]([540]) 와 같은 병 —
            #   "한 통로에만 걸면 나머지로 샌다".
            #   timeout 은 명시하지 않는다 → `invoke_text_result` 의 저장소 표준값을 상속
            #   (종전 300s 는 `_run_sdk_sync` 의 raw 기본값이 우연히 걸린 것이지 정책이 아니었다).
            response_text, _ok = invoke_text_result(
                self.alias, user_prompt, system=system_prompt,
            )
            response_text = response_text or ""
            tool_calls, content = self._parse_tool_calls(response_text)
            msg = AIMessage(content=content, tool_calls=tool_calls or [])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        def bind_tools(self, tools, **kwargs):
            """LangChain bind_tools — tool 리스트를 모델 상태에 박제."""
            return self.copy(update={"bound_tools": list(tools)})

    return ClaudeSDKChatModel


# ClaudeSDKChatModel 은 langchain_core import 시점에 클래스 정의
try:
    ClaudeSDKChatModel = _build_claude_sdk_chat_model()
except ImportError:
    ClaudeSDKChatModel = None  # langchain_core 미설치 환경 — chat() 가 None 반환


# ── 직접 호출 헬퍼 ────────────────────────────────────────────
# (alias→model_id 는 모듈 상단 _ALIAS_MODEL 단일 소스 — 여기 재정의 금지)


# ── LLM 호출 실패 근본 차단 (사용자 박제 2026-07-02) ──────────────────
#  ① embedded null byte: 수집 데이터(뉴스·웹)의 널바이트·제어문자가 프롬프트에 섞이면
#     claude CLI subprocess spawn 이 ValueError("embedded null byte") 로 크래시 → 사전 제거.
#  ② Max 구독 burst 초과: 발행이 claude CLI 를 동시에 여러 개(차트 4-way 등) spawn 하면
#     Max 구독 동시성 한도 초과 → CLI 가 모델 미호출(num_turns=0) 로 빈 응답 → 폴백.
#     단일 진입점에서 프로세스 전역 세마포어로 spawn 을 직렬화 → 각 호출이 실제 성공.
import re as _re_ctrl
import threading as _threading
import time as _time          # 발행창 만료 판정 (불균형 복구)
import time as _time_pace

_CTRL_RE = _re_ctrl.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_prompt(s: str) -> str:
    """CLI subprocess spawn 안전 — 널바이트·제어문자 제거 (탭·개행·복귀는 보존)."""
    if not s:
        return s
    return _CTRL_RE.sub("", s)


# 동시 claude CLI spawn 상한 (기본 1 = 완전 직렬 — Max burst 안전). env 로 튜닝.
# ★ 토큰 계측용 alias 전파 (ERRORS [456]) — _run_sdk_sync 는 model 만 받으므로
#   "어느 용도(alias)가 얼마나 썼는지" 를 귀속하려면 호출 문맥이 필요하다.
import contextvars as _contextvars
import sys as _sys
_CURRENT_ALIAS: "_contextvars.ContextVar[str]" = _contextvars.ContextVar("llm_alias", default="")


def _bind_alias(alias: str) -> None:
    """★ alias 귀속 단일 진입점 — **모든 LLM 통로가 이걸 거친다** (ERRORS [540], 2026-07-27).

    ★ 왜 만들었나: 종전엔 `invoke_text_result` **한 곳에서만** `_CURRENT_ALIAS.set()` 을 했다.
      `invoke_vision` 은 `alias` 를 인자로 받아놓고 컨텍스트에 넣지 않아, 비전 호출이 전부
      폴백 라벨 `"vision"` 으로 찍혔다 — **MODELS 에 없는 가짜 alias 가 DB 에 쌓였다**.
      실측(2026-07-27): `design_learner` 의 `invoke_vision("writer_long_learn", …)` 9회가
      전부 `alias="vision"`(캐시읽기 349,701)으로 기록돼 **세분화가 이 통로에서만 무력**했다.

    ★ CLAUDE.md 가 박제한 같은 병의 재발이다 —
      *"실례 [474]: 발행 우선 규칙을 `invoke_text` 에만 걸어 `run_sdk_query` 로 우회됨"*.
      한 통로에만 걸면 나머지로 샌다. → **귀속을 이 함수 하나로 모으고 모든 통로가 호출**한다.

    ★ 왜 컨텍스트매니저가 아닌가: `contextvars` 는 스레드/태스크 로컬이라 `set` 만으로도
      이 호출 문맥에 갇힌다. 게다가 호출부(`invoke_text_result`)는 긴 재시도 루프에
      중간 return 이 많아 `with` 로 감싸면 구조가 크게 흔들린다. 다음 진입점이 다시 덮으므로
      누수 위험도 없다 — **비어 있는 경우만 문제이고 그건 `alias_selfcheck()` 가 감시한다.**
    """
    _CURRENT_ALIAS.set(alias or "")


def _raw_sdk_callers() -> list[tuple[str, int, bool]]:
    """원시 SDK(`_run_sdk_sync`/`_invoke_sdk_vision`)를 *직접* 부르는 함수를 **소스에서 파생**.

    ★ 왜 손목록을 버렸나 (ERRORS [543]): 종전 검사는 통로 이름 두 개
      (`invoke_text_result`·`invoke_vision`)를 코드에 **박아뒀다**. 그 목록이
      `_generate`(LangChain 어댑터) 경로를 **놓쳤고**,
      두 어댑터는 alias 귀속·캐시정책·회로차단·재시도를 통째로 우회한 채 돌고 있었다.
      *목록을 손으로 관리하는 검사는 반드시 낡는다* — 새 통로가 생기면 목록도
      같이 고쳐야 하는데, 고치는 사람은 검사가 있는 줄도 모른다.

    ★ 중첩 함수 처리: 호출은 **자기 몸통 것만** 센다. 그래야 팩토리
      (`_build_claude_sdk_chat_model`)가 안에 든 `_generate` 때문에 오탐되지 않고,
      `_generate` 자신이 독립 통로로 잡힌다.

    반환: [(함수명, 줄번호, `_bind_alias` 를 거치는가)]
    """
    import ast as _ast
    import inspect as _insp

    def _own_calls(fnode) -> set:
        out, stack = set(), list(fnode.body)
        while stack:
            n = stack.pop()
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda)):
                continue                      # 중첩 함수는 *그 자체가* 별도 통로
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name):
                out.add(n.func.id)
            stack.extend(_ast.iter_child_nodes(n))
        return out

    _RAW = {"_run_sdk_sync", "_invoke_sdk_vision"}
    found: list[tuple[str, int, bool]] = []
    tree = _ast.parse(_insp.getsource(_sys.modules[__name__]))
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        calls = _own_calls(node)
        if calls & _RAW:
            found.append((node.name, node.lineno, "_bind_alias" in calls))
    return found


def alias_selfcheck() -> list[str]:
    """★ alias 귀속이 *실제로 되고 있는지* 동작으로 확인 (ERRORS [540]).

    이 결함은 예외를 안 던지고 **계측 라벨만 조용히 틀어진다** — 그래서 감시가 필요하다.
    """
    issues: list[str] = []
    try:
        # 원시 SDK 를 *직접* 부르는 함수는 반드시 alias 를 묶어야 한다.
        for name, lineno, bound in _raw_sdk_callers():
            if not bound:
                issues.append(f"[A1] {name}(:{lineno}) 가 원시 SDK 를 직접 부르면서 "
                              f"_bind_alias 를 안 거침 — alias 라벨·캐시정책 유실")
        # MODELS 에 없는 alias 가 DB 에 쌓이고 있지 않은가
        from shared.db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT alias, COUNT(*) FROM llm_token_usage "
                "WHERE ts >= datetime('now','localtime','-1 day') GROUP BY alias"
            ).fetchall()
        for a, n in rows:
            if a and a not in MODELS:
                issues.append(f"[A2] MODELS 에 없는 alias 가 기록됨: {a!r} ({n}건)")
            if not a:
                issues.append(f"[A3] alias 빈 값으로 기록됨 ({n}건) — 귀속 누락 통로 존재")
    except Exception as e:  # noqa: BLE001
        issues.append(f"[A0] alias_selfcheck 실패: {type(e).__name__}: {e}")
    return issues


_LLM_MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "1") or "1"))
_LLM_SPAWN_SEM = _threading.BoundedSemaphore(_LLM_MAX_CONCURRENCY)
# spawn 간 최소 간격(초) — 기본 0(off). rate-limit 잦으면 0.5~1 로 상향.
_LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL_SEC", "0") or "0")
_LLM_PACE_LOCK = _threading.Lock()
_LLM_LAST_SPAWN = [0.0]
_LLM_SEM_POLL_SEC = 15.0  # 세마포어 대기 중 heartbeat 주기 (watchdog freeze_sec=300 보다 충분히 작게)


# ── 크로스 프로세스 LLM 직렬화 잠금 ─────────────────────────────────────────
# daemon 과 수동 실행(--tistory-only 등)은 별개 프로세스 → 각자 독립된 BoundedSemaphore.
# 두 프로세스가 동시에 claude CLI 를 spawn 하면 Max 구독 포화 → SDK hang(0응답) 원인.
# fcntl advisory lock: POSIX 보장 + 프로세스 종료 시 자동 해제(교착 위험 0).
_LLM_PROC_LOCK_PATH = Path(
    os.environ.get("JARVIS_DB_PATH", str(Path.home() / ".jarvis" / "jarvis.sqlite"))
).parent / "llm_exec.lock"
_llm_proc_fd: list = [None]
_llm_proc_fd_lock = _threading.Lock()

# ★ 락 획득 대기 상한 (P2-a 사용자 박제 2026-07-18) — LLM timeout(최대 300s)만큼 기다리다
#   사망하지 말고, 짧게(45s) 시도 후 실패하면 lock_contention 으로 defer. 락 경합은 rate-limit
#   스로틀이 아니므로 회로차단기를 오염시키지 않는다(hung 오분류 차단).
_LOCK_ACQUIRE_MAX_WAIT = float(os.getenv("LLM_LOCK_ACQUIRE_MAX_WAIT", "45") or "45")


def _proc_lock_acquire(timeout: float | None = None) -> bool:
    """크로스 프로세스 배타 잠금 — 다른 JARVIS 프로세스가 CLI 사용 중이면 폴링 대기.

    ★ timeout 상한 (ERRORS [439] 후속 — 사용자 박제 2026-07-16): 무제한 폴링은
    다른 프로세스가 잠금을 오래 점유할 때 harness 액션 데드라인(블로그 발행=30분)을
    조용히 관통한다 — 스텝 *내부* 블로킹이라 협조적 wd.check() 가 못 잡고, 백그라운드
    감시 스레드의 '데드라인 초과(블로킹)' 로만 뒤늦게 걸린다. timeout 지정 시 그 안에서
    포기하고 False 반환 → 호출자가 SDK hang 과 동일하게 취급(재시도/회로차단기 경유).
    """
    import fcntl as _fcntl, time as _t
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd
    except Exception:
        def _wd(): pass
    if _llm_proc_fd[0] is None:
        with _llm_proc_fd_lock:
            if _llm_proc_fd[0] is None:
                _LLM_PROC_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
                _llm_proc_fd[0] = open(_LLM_PROC_LOCK_PATH, "w")
    _fd = _llm_proc_fd[0]
    _wd()
    _waited = 0.0
    while True:
        try:
            _fcntl.flock(_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except OSError:
            _wd()
            if timeout is not None and _waited >= timeout:
                return False
            _t.sleep(_LLM_SEM_POLL_SEC)
            _waited += _LLM_SEM_POLL_SEC


def _proc_lock_release() -> None:
    """크로스 프로세스 잠금 해제."""
    import fcntl as _fcntl
    if _llm_proc_fd[0] is not None:
        try:
            _fcntl.flock(_llm_proc_fd[0], _fcntl.LOCK_UN)
        except Exception:
            pass


# ── 발행 기간 LLM 우선권 ──────────────────────────────────────────────────────
# mark_publishing(True) 동안 background alias(guardian 등)의 timeout 을 90s 로 단축,
# retries=1 → 세마포어 장기 점유로 발행 파이프라인을 최대 300s 블로킹하던 사고 방지.
_PUBLISHING_ACTIVE = _threading.Event()
_PUBLISHING_DEPTH = 0                        # 중첩 표시 참조수 (잡 래퍼 + 내부 run())
_PUBLISHING_DEPTH_LOCK = _threading.Lock()
_PUBLISHING_SINCE = 0.0                      # 창이 열린 시각 (불균형 만료 판정용)


def _reset_publishing_state() -> None:
    """발행창 강제 해제 — 불균형(mark_publishing(False) 누락) 복구용."""
    global _PUBLISHING_DEPTH, _PUBLISHING_SINCE
    with _PUBLISHING_DEPTH_LOCK:
        _PUBLISHING_DEPTH = 0
        _PUBLISHING_SINCE = 0.0
    _PUBLISHING_ACTIVE.clear()
    try:
        _PUBLISHING_MARK_PATH.unlink(missing_ok=True)
    except Exception:
        pass

# ★ 현재 스레드의 스케줄 잡 문맥 (2026-07-25) — JARVIS04 잡 래퍼가 설정.
#   왜 필요한가: 배경 잡 중 *글 작성 alias* 를 쓰는 것이 있다(daily_review=analyzer,
#   design_learn=writer). alias 만 보면 이들을 못 거르고, 그렇다고 analyzer/writer 를 막으면
#   09 수집·06 이미지가 죽는다. "누가 부르는가"(잡 문맥)로 갈라야 정확하다.
_JOB_CTX = _threading.local()


def mark_job_context(job_id: str = "", pipeline: bool = False) -> None:
    """현재 스레드가 실행 중인 스케줄 잡 표시 — JARVIS04 `job_llm_priority.gate()` 전용.
    job_id="" 로 호출하면 해제(스레드풀 재사용 대비 반드시 finally 에서 해제)."""
    _JOB_CTX.job_id = job_id or ""
    _JOB_CTX.pipeline = bool(pipeline)


def current_job_is_background() -> bool:
    """지금이 *파이프라인이 아닌* 스케줄 잡 안인가 (발행창에서 양보 대상)."""
    return bool(getattr(_JOB_CTX, "job_id", "")) and not getattr(_JOB_CTX, "pipeline", False)


def current_job_id() -> str:
    """지금 실행 중인 스케줄 잡 ID (잡 밖이면 "").

    ★ 왜 필요한가: 발행 콜백이 *자기 잡 ID 를 코드에 박지 않고* 알아내기 위해서다.
      박아두면 잡 ID 사본이 생겨, JARVIS04 에서 ID 를 바꿔도 여기만 옛 값을 가리킨다(② 동적 설계).
      문맥은 `job_llm_priority` 의 게이트가 잡 진입 시 심어 둔다.
    """
    return getattr(_JOB_CTX, "job_id", "") or ""


def defer_reason(alias: str = "", background: bool | None = None) -> str:
    """★ LLM 착수 보류 사유 — **모든 통로의 단일 판정 함수** (2026-07-25).

    LLM 은 네 개의 문으로 나간다: `invoke_text` · `invoke_vision` · `_run_sdk_sync`(chat) ·
    `run_sdk_query`(Claude Code SDK). 종전엔 `invoke_text` 한 곳만 막혀 있어
    GUARDIAN auto_repair(run_sdk_query, timeout 1200s)·design_learner(invoke_vision)가
    발행창에서도 그대로 나갔다. 판정을 여기 한 곳에 모아 네 문이 같은 답을 쓰게 한다.

    배경 여부 판정 (우선순위):
      ① background 인자가 명시되면 그대로 (호출자가 아는 것이 가장 정확)
      ② alias 가 MODELS 에서 background=True 로 선언됐으면 배경
      ③ 파이프라인이 아닌 *스케줄 잡* 문맥 안이면 배경 (alias 로 못 거르는 잡용)
    잡이 아닌 문맥(텔레그램 사용자 명령·사용자 수동 실행)은 ②만 걸린다 — 사용자 작업은 막지 않는다.

    반환: 보류 사유 문자열(발행 중/보호 구간) 또는 "" (진행해도 됨).
    """
    if background is None:
        background = (alias in _BG_ALIASES) or current_job_is_background()
    if not background:
        return ""
    try:
        return bg_defer_reason()
    except Exception:
        return ""
# ★ MODELS 선언에서 파생 (사본 금지 — 2026-07-25). 종전엔 여기 손으로 4개를 적어둬서
#   `coder`(architect 코드생성)가 누락돼 발행창에도 그대로 나갔다.
_BG_ALIASES = frozenset(a for a, s in MODELS.items() if getattr(s, "background", False))

# ★ 발행창 essential 재시도 캡 대상 (P-C 사용자 박제 2026-07-18) — 본문 생성·발행전 검증 호출.
#   스로틀/SDK 스톨 시 재시도 증폭(913s) 차단용 retries=1. analyzer(추출)는 제외(선계산으로 이전).
_PUBLISH_ESSENTIAL_CAP = frozenset(
    a.strip() for a in
    (os.getenv("LLM_PUBLISH_ESSENTIAL_CAP", "writer,fact_judge,engagement_judge") or "").split(",")
    if a.strip()
)


# ★ 발행 중 표시 — **프로세스 경계를 넘는다** (ERRORS [474] — 2026-07-22)
#   `_PUBLISHING_ACTIVE` 는 threading.Event 라 *같은 프로세스 안에서만* 유효하다.
#   그런데 경제 브리핑은 `economic_poster.py` **별도 subprocess** 로 돌고(테마는 데몬
#   in-process), GUARDIAN 은 데몬 프로세스에 있다 → 경제 발행 중에는 데몬이
#   `is_publishing()=False` 로 보고 자가수리를 시작해 버렸다.
#   (2026-07-22 07:24 실사고가 정확히 이 경우 — '모든 글에 적용' 이 안 된 상태였다.)
#   → 파일 표식으로 프로세스 간 공유. 크래시로 표식이 남아도 stale 판정으로 자동 무효화.
_PUBLISHING_MARK_PATH = _LLM_PROC_LOCK_PATH.parent / "publishing.active"


def _publishing_stale_sec(default: float = 4800.0) -> float:
    """발행 표식의 유효 시간 — 블로그 액션 데드라인에서 파생 (하드코딩 금지).

    발행은 플랫폼별로 액션 데드라인(기본 40분)을 갖고 2개 플랫폼이 직렬로 돈다.
    그 2배를 넘겨도 표식이 살아 있으면 정리 실패로 보고 무시한다(영구 차단 방지).
    """
    try:
        from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC as _D
        return float(_D) * 2
    except Exception:
        return default


def mark_publishing(active: bool) -> None:
    """발행 파이프라인 시작/종료 신호 — background alias LLM 호출 자동 강등.

    ★ in-process Event + 파일 표식을 *함께* 갱신 → 다른 프로세스(데몬 GUARDIAN)도 인지.
    """
    # ★ 중첩 안전 refcount (2026-07-25): 잡 래퍼(JARVIS04)가 파이프라인 잡 전체를 감싸고
    #   그 안에서 economic_poster.run()/run_all_themes() 가 또 표시한다. 참조수 없이 bool 로
    #   다루면 *안쪽이 끝날 때 바깥 창까지 꺼져* 발행 후반부가 무방비가 된다.
    global _PUBLISHING_DEPTH, _PUBLISHING_SINCE
    with _PUBLISHING_DEPTH_LOCK:
        if active:
            if _PUBLISHING_DEPTH == 0:
                _PUBLISHING_SINCE = _time.time()   # 0→1 시각 기록 (불균형 만료 판정)
            _PUBLISHING_DEPTH += 1
        else:
            _PUBLISHING_DEPTH = max(0, _PUBLISHING_DEPTH - 1)
            if _PUBLISHING_DEPTH == 0:
                _PUBLISHING_SINCE = 0.0
        active = _PUBLISHING_DEPTH > 0      # 실제 창 상태 = 참조수 > 0
    if active:
        _PUBLISHING_ACTIVE.set()
    else:
        _PUBLISHING_ACTIVE.clear()
    try:
        if active:
            from datetime import datetime as _dt_p
            _PUBLISHING_MARK_PATH.parent.mkdir(parents=True, exist_ok=True)
            _PUBLISHING_MARK_PATH.write_text(
                _dt_p.now().isoformat(timespec="seconds"), encoding="utf-8")
        else:
            _PUBLISHING_MARK_PATH.unlink(missing_ok=True)
    except Exception:
        pass   # 표식 실패는 치명적이지 않다 — in-process Event 는 그대로 동작


def _publishing_mark_active() -> bool:
    """다른 프로세스가 발행 중인지 — 파일 표식 확인 (stale 자동 무시)."""
    try:
        if not _PUBLISHING_MARK_PATH.exists():
            return False
        from datetime import datetime as _dt_p
        _raw = _PUBLISHING_MARK_PATH.read_text(encoding="utf-8").strip()
        _started = _dt_p.fromisoformat(_raw)
        _age = (_dt_p.now() - _started).total_seconds()
        if _age > _publishing_stale_sec():
            # ★ 자가 치유 (2026-07-25): 무시만 하지 말고 *지운다*.
            #   watchdog os._exit(75)·proc.kill() 로 죽은 발행은 표식을 남기는데, 종전엔
            #   지우는 주체가 없어 재부팅 후에도 파일이 남아 최대 80분간 배경 LLM 이 전면 보류됐다.
            try:
                _PUBLISHING_MARK_PATH.unlink(missing_ok=True)
                import logging as _lg0
                _lg0.getLogger("jarvis.llm").warning(
                    f"🧹 발행 표식이 {_age/60:.0f}분째 남아 있어 제거 (비정상 종료 잔재)")
            except Exception:
                pass
            return False
        return True
    except Exception:
        return False


# ── 발행창 보호 구간 (사용자 승인 2026-07-20 — 제안 ③) ─────────────────
#
# 배경: 새벽 심층감사·상시 잡이 한도를 쓴 뒤 발행창에서 LLM 이 스로틀되면 발행이
#   차단된다. 발행 *직전* 일정 시간 동안 background alias 를 아예 막아 한도를
#   발행에 몰아준다.
#
# ★ 동적 설계: 발행 시각을 하드코딩하지 않는다. JARVIS04 DEFAULT_JOBS 의 실제
#   cron(hour/minute)에서 도출 → 사용자가 발행 시각을 바꾸면 보호 구간이 자동으로
#   따라 이동한다. (2026-07-20 '복사본을 진실로 믿지 말 것' 원칙)
_PROTECT_MIN = int(os.getenv("LLM_PUBLISH_PROTECT_MIN", "90") or "90")   # 발행 前 보호 분
# 스로틀 시 재시도 생략 (제안 ① — 킬스위치 0 으로 종전 동작 복귀)
_THROTTLE_NO_RETRY = (os.getenv("LLM_THROTTLE_NO_RETRY", "1") or "1") != "0"


def _max_retries() -> int:
    """LLM 계층 재시도 상한 — harness.DEFAULT_MAX_ATTEMPTS(SSOT)에서 파생.

    ★ 하드코딩 금지: 상한은 한 곳(harness)에서만 정의한다. import 실패 시에만
      보수적으로 2 로 폴백(종전 3 이 아니라 2 — 사용자 박제 2026-07-21).
    """
    try:
        from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS
        return max(1, int(DEFAULT_MAX_ATTEMPTS))
    except Exception:
        return 2

# ── SDK cwd 격리 (제안 ② — 2026-07-20, 전수감사 확정) ──────────────────
#
# 문제: ClaudeCodeOptions 에 cwd 를 주지 않으면 spawn 된 claude CLI 가 데몬의
#   cwd(= 저장소 루트)를 물려받아 **CLAUDE.md + @import 5개(≈96KB / 약 48,940 토큰)**
#   를 프로젝트 메모리로 자동 로드한다. 내용은 매 호출 동일한데, CLI 가
#   [CLAUDE.md + 우리 프롬프트] 를 *하나의 트레일링 캐시 블록* 에 넣기 때문에
#   프롬프트가 바뀔 때마다 블록 전체가 무효화 → 48,940 토큰이 매번 *재기록*
#   (쓰기 프리미엄 `_CACHE_WRITE_MULT`)된다. 읽기(`_CACHE_READ_MULT`)로 재사용되지 못한다.
#
# 실측(자연실험): cwd=저장소 → cache_create ≈ 49,000 / cwd=빈 임시폴더 → ≈ 890.
#   회귀 cache_create = 48,940 + 0.80×프롬프트글자수. 고정 낭비가 캐시쓰기의 85~98%.
#
# 해결: 전용 빈 작업 디렉터리를 cwd 로 준다. 이 호출들은 프로젝트 헌법이 필요 없다
#   — 작성 규칙은 law_enforcer.build_writing_rules_block() 으로 *프롬프트에 명시 주입*
#   되는 것이 이 저장소의 설계다(자동 로드에 의존하지 않는다).
#
# ★ 동적: 경로를 박지 않고 DB 경로에서 도출. 킬스위치 LLM_ISOLATE_CWD=0.
_ISOLATE_CWD = (os.getenv("LLM_ISOLATE_CWD", "1") or "1") != "0"

# ── SDK 프리픽스 축소 2종 (2026-07-26, A/B 검증 후 적용) ────────────────────
#   `_ISOLATE_CWD` 가 CLAUDE.md 자동 로드(48,940 토큰)를 걷어낸 뒤에도, 그 아래
#   **Claude Code CLI 자체의 시스템 프롬프트 + 도구/MCP 스키마** 가 그대로 남아 있었다.
#   같은 병의 2차 발현 — 실측 호출당 31,468 토큰 중 31,241 이 우리 프롬프트가 아니었다.
#   두 노브를 분리해 둔 이유: 성격이 다르다. SPLIT_SYSTEM 은 *자리 이동*(내용 불변,
#   위험 0), TEXT_NO_TOOLS 는 *기능 차단*(A/B 로 품질 확인 후 적용).
_SPLIT_SYSTEM   = (os.getenv("LLM_SPLIT_SYSTEM", "1") or "1") != "0"
_TEXT_NO_TOOLS  = (os.getenv("LLM_TEXT_NO_TOOLS", "1") or "1") != "0"


def _llm_scratch_dir() -> str | None:
    """CLAUDE.md 가 없는 전용 cwd. 실패하면 None(종전 동작)."""
    if not _ISOLATE_CWD:
        return None
    try:
        from shared.db import DB_PATH
        d = Path(DB_PATH).parent / "llm_cwd"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    except Exception:
        return None


# 저장소를 벗어나면 프로젝트 .claude/settings.json 도 함께 잃는다.
# 거기서 설정하던 값은 env 로 직접 전달해 유지한다.
_SDK_BASE_ENV = {"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}


def _sdk_env(alias: str = "") -> dict:
    """SDK 에 넘길 env — alias 의 캐시 정책까지 반영 (ERRORS [541], ① 단일 진입점).

    ★ 왜 헬퍼인가: env 를 조립하는 곳이 텍스트(`_run_sdk_sync`)·비전(`_invoke_sdk_vision`)
      **두 곳**이다. 각자 조립하면 한쪽만 정책이 걸려 또 통로별로 샌다 —
      바로 앞 사고(ERRORS [540] alias 귀속 누락)와 **같은 형태의 병**이다.

    ★ 캐시를 끄는 판정 근거: 배수는 `_CACHE_WRITE_MULT` / `_CACHE_READ_MULT` 단독 (숫자 복사 금지).
      재사용이 손익분기 미만이면 **쓰기 프리미엄만 내고 회수를 못 한다**(순손실).
      판정은 `MODELS[alias].cache` 가 소유하고 여기선 집행만 한다(② 동적 설계).

    무배포 되돌리기: `LLM_CACHE_POLICY=0` → 정책 무시(전부 SDK 기본값 = 캐시 켬).
    """
    env = dict(_SDK_BASE_ENV)
    if (os.getenv("LLM_CACHE_POLICY", "1") or "1") == "0":
        return env
    spec = MODELS.get(alias)
    if spec is not None and not getattr(spec, "cache", True):
        # CLI 가 읽는 공식 스위치. 모델별 변수(_SONNET 등)도 있으나 우리는 단일 모델이라 전역으로 충분.
        env["DISABLE_PROMPT_CACHING"] = "1"
    return env


# 캐시 정책 적용 시각 — 이 이후 데이터만 [C2] 판정에 쓴다 (과거 데이터 오탐 방지).
#   ★ 비교는 반드시 `datetime(?)` 경유 (ERRORS [542]): ts 는 SQLite `datetime('now','localtime')`
#     가 쓰므로 **공백 구분자**인데 이 상수는 ISO 'T' 였다 → 문자열 비교가 항상 0행 → [C2] 가
#     *무증상으로 죽어 있었다*. 포맷을 손으로 맞추는 대신 SQLite 가 양쪽을 정규화하게 둔다
#     (원칙② — 저장 포맷의 사본을 코드에 두지 말 것).
_CACHE_POLICY_SINCE = "2026-07-27T14:30:00"

# ★ 캐시 쓰기 프리미엄 — 이 값의 **단일 진실 소스** (원칙①).
#   종전 1.25(5분 TTL 가정)는 **실측으로 반증**됐다 (ERRORS [542]):
#     ① 청구액 역산 — `llm_token_usage.cost_usd` 617건을 입력/출력/캐시읽기/캐시쓰기 4단가로
#        동시 회귀(R²=0.994) → 캐시쓰기 6.17 $/MTok. 나머지 3개가 정가와 일치하므로 신뢰 가능.
#        가설 검정: 1.25 가정 시 총액 오차 19.96% / 2.0 가정 시 4.53%.
#     ② TTL 프로브 — 동일 system 을 **13분 25초** 간격으로 2회 발사해 2회차 `cache_read` 회수 확인.
#        5분 TTL 이면 불가능한 값이다.
#   → 손익분기가 재사용 0.28배에서 **1.11배**로 올라간다. 판정이 통째로 바뀌므로 상수화한다.
_CACHE_WRITE_MULT = 2.0
_CACHE_READ_MULT = 0.1


def cache_selfcheck() -> list[str]:
    """★ 캐시 정책이 *실측과 맞는지* 감시 (ERRORS [541]).

    판정을 손으로 박아두면 호출 패턴이 바뀌었을 때 낡는다 — 그때 **조용히 손해**만 난다.
    최근 7일 실사용에서 정책과 어긋나면 알린다(끈 게 이득이 됐거나, 켠 게 손해가 됐거나).
    """
    issues: list[str] = []
    try:
        from shared.db import get_db
        with get_db() as conn:      # ★ 커넥션은 이 블록 안에서만 (auto-close 규약)
            rows = conn.execute(
                "SELECT alias, SUM(cache_create), SUM(cache_read) FROM llm_token_usage "
                "WHERE ts >= datetime('now','localtime','-7 day') AND alias<>'' "
                "GROUP BY alias").fetchall()
            recent = dict(conn.execute(
                # ★ datetime(?) 경유 필수 — 생 문자열 비교는 구분자가 어긋나 0행이 된다 (위 상수 주석)
                "SELECT alias, COALESCE(SUM(cache_create),0) FROM llm_token_usage "
                "WHERE ts >= datetime(?) AND alias<>'' GROUP BY alias",
                (_CACHE_POLICY_SINCE,)).fetchall())
        for a, cc, cr in rows:
            spec = MODELS.get(a)
            if spec is None:
                continue
            cc, cr = int(cc or 0), int(cr or 0)
            if cc < 5000:
                continue                      # 표본 부족 — 판정 보류
            ratio = cr / cc
            on_cost = _CACHE_WRITE_MULT * cc + _CACHE_READ_MULT * cr
            off_cost = 1.0 * (cc + cr)
            if spec.cache and on_cost > off_cost:
                issues.append(f"[C1] {a}: 재사용 {ratio:.2f}배 — 캐시가 순손실"
                              f"({on_cost - off_cost:+,.0f} 토큰/7일). cache=False 검토")
        for a, n in recent.items():
            spec = MODELS.get(a)
            # ★ 정책 적용 *이후* 구간만 본다 — 과거 데이터로 오탐을 내면 감시를 못 믿게 된다.
            if spec is not None and not spec.cache and int(n or 0) > 0:
                issues.append(f"[C2] {a}: cache=False 인데 정책 적용 후에도 캐시생성 "
                              f"{int(n):,} 발생 — 통로 누락 의심")
    except Exception as e:  # noqa: BLE001
        issues.append(f"[C0] cache_selfcheck 실패: {type(e).__name__}: {e}")
    return issues


# ── 본문 생성 timeout 단일 진입점 (ERRORS [460] — 2026-07-20) ──────────
#
# 사고: 테마 티스토리 발행이 6/6 실패. 로그의 "인프라 스로틀" 은 *오분류* 였고
#   실제 원인은 `SDK timeout 300s — 수집된 응답: 0개`. 네이버는 27,657 토큰을
#   292.1초에 생성해 300초를 *간신히* 통과했고, 대등한 분량의 티스토리는 벽을 넘었다.
#   실측 생성 속도 ≈ 88 토큰/초 → 27.6K 토큰에 약 314초가 필요한데 상한이 300초였다.
#
# 왜 하드코딩이 문제였나: draft_writer.py 6곳에 `timeout=300` 이 박혀 있어
#   분량 정책이 늘어도 시간 예산이 따라오지 않았다. 값을 코드에 복사해둔 전형.
#
# ★ 동적: 플랫폼 액션 데드라인(watchdog SSOT)에서 도출한다. 재시도 상한(3회)이
#   데드라인을 넘지 않도록 1/4 로 제한 → 데드라인이 바뀌면 자동 추종.
def writer_timeout() -> int:
    """본문 생성 LLM 호출 상한(초). 액션 데드라인에서 도출 — 하드코딩 금지."""
    env = os.getenv("LLM_WRITER_TIMEOUT_SEC")
    if env:
        try:
            return max(60, int(env))
        except ValueError:
            pass
    try:
        from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC as _d
    except Exception:
        _d = 2400
    # 3회 재시도 + 수집·이미지·발행 단계 몫을 남기고 1/4 배정
    return max(300, min(900, int(_d / 4)))
_protect_cache: list = [0.0, ()]     # (계산시각, ((hour,minute), ...))
_PROTECT_TTL = 600.0


def _publish_times() -> tuple:
    """발행 잡의 (시,분) 목록 — DEFAULT_JOBS 에서 실시간 도출. 실패 시 빈 튜플."""
    import time as _t
    now = _t.time()
    if _protect_cache[1] and now - _protect_cache[0] < _PROTECT_TTL:
        return _protect_cache[1]
    times: tuple = ()
    try:
        # ★ 발행 잡 판별은 JARVIS04 단일 소스에서 파생 (2026-07-25). 종전엔 여기에
        #   "run_self_repair_then_" 문자열이 *복사* 돼 있어 job_llm_priority 와 2벌이었다.
        from JARVIS04_SCHEDULER.job_llm_priority import publish_cron_times
        times = publish_cron_times()
    except Exception:
        pass
    _protect_cache[0], _protect_cache[1] = now, tuple(sorted(set(times)))
    return _protect_cache[1]


def in_publish_protection() -> bool:
    """지금이 발행 직전 보호 구간인가 (발행 시각 前 _PROTECT_MIN 분)."""
    if _PROTECT_MIN <= 0:
        return False
    times = _publish_times()
    if not times:
        return False
    from datetime import datetime as _dt
    now = _dt.now()
    cur = now.hour * 60 + now.minute
    for h, m in times:
        start = (h * 60 + m - _PROTECT_MIN) % (24 * 60)
        end = h * 60 + m
        if start <= end:
            if start <= cur < end:
                return True
        else:                      # 자정 넘김
            if cur >= start or cur < end:
                return True
    return False


def is_publishing() -> bool:
    """현재 발행 파이프라인 실행 중인지 — **프로세스 경계 넘어서** 판정 (ERRORS [474]).

    in-process Event(테마=데몬 내부) + 파일 표식(경제=별도 subprocess) 둘 다 확인.
    한쪽만 보면 '모든 글에 적용' 이 안 된다.
    """
    if _PUBLISHING_ACTIVE.is_set():
        # ★ 균형 안 맞은 표시로 인한 *영구 블랙아웃* 방지 (2026-07-25).
        #   mark_publishing(True) 후 예외로 (False) 가 누락되면 참조수가 영영 안 내려간다
        #   → 배경 LLM 이 데몬 재시작 전까지 전면 보류. 파일 표식과 같은 만료 규칙을 적용한다.
        _since = globals().get("_PUBLISHING_SINCE") or 0.0
        if _since and (_time.time() - _since) > _publishing_stale_sec():
            import logging as _lg1
            _lg1.getLogger("jarvis.llm").warning(
                f"🧹 발행창이 {(_time.time()-_since)/60:.0f}분째 열려 있어 강제 해제 "
                f"(mark_publishing 불균형 — 영구 블랙아웃 방지)")
            _reset_publishing_state()
        else:
            return True
    return _publishing_mark_active()


def bg_defer_reason() -> str:
    """배경(비긴급) LLM 작업을 지금 *미뤄야* 하는 사유 — 빈 문자열이면 진행해도 됨.

    ★ ERRORS [474] (사용자 지시 2026-07-22) — 발행 우선.
      `invoke_text` 는 이미 발행 前 보호(차단) + 발행 中 강등(timeout 90s)을 하지만,
      GUARDIAN 자가수리는 `run_sdk_query` 라는 *다른 통로* 로 나가 셋 다 적용받지 않았다.
      게다가 Tier-2 는 한 세션이 10분 이상이라 '90초로 단축' 은 의미가 없다 —
      발행 중에는 *아예 하지 않는* 것이 맞다.
      실제 사고: 2026-07-22 07:24, 티스토리 발행(07:33 종료)이 도는 중에 GUARDIAN 이
      LLM 을 점유해 발행 파이프라인이 lock_contention 으로 밀리고 품질 게이트가 스킵됐다.
    """
    try:
        if is_publishing():
            return "발행 진행 중"
        if in_publish_protection():
            return f"발행 前 {_PROTECT_MIN}분 보호 구간"
    except Exception:
        pass
    return ""


def _retry_sweep_sec(default: float = 600.0) -> float:
    """배경 작업이 '다음 기회' 를 얻기까지의 간격 — DEFAULT_JOBS 에서 파생 (하드코딩 금지)."""
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        for j in DEFAULT_JOBS:
            if j.get("id") == "j07_retry_pending":
                kw = j.get("kwargs") or {}
                return float(kw.get("minutes", 10)) * 60 + float(kw.get("seconds", 0))
    except Exception:
        pass
    return default


def bg_sem_wait_max() -> float:
    """배경(비긴급) 작업의 LLM 순번 대기 상한 — **동적 파생** (ERRORS [474]).

    ★ 왜 이 값인가: 배경 작업은 포기해도 `j07_retry_pending` 이 곧 다시 집어간다.
      즉 *다음 기회가 오는 간격* 보다 오래 줄 서는 것은 무의미하다 —
      기다리느니 양보하고 다음 차례에 하는 편이 발행에도 좋고 자기 자신에게도 낫다.
      스윕 주기의 1/4 을 상한으로 둔다(10분 주기 → 150초).
      하드코딩 금지 — 스윕 주기를 바꾸면 이 값이 따라온다.
    무배포 조정: `LLM_BG_SEM_WAIT_MAX`(초).
    """
    _env = os.getenv("LLM_BG_SEM_WAIT_MAX")
    if _env:
        try:
            return max(1.0, float(_env))
        except Exception:
            pass
    return max(30.0, _retry_sweep_sec() / 4.0)


def _acquire_llm_sem(timeout: float | None = None) -> bool:
    """★ 전역 LLM 세마포어 획득 — 대기 중에도 워치독 진행 신호 전송 (freeze 오탐 방지).

    다른 에이전트(GUARDIAN 심층감사·WRITER 장문 생성 등)가 슬롯을 오래 점유해도
    대기 자체는 정상 흐름이다. plain `with _LLM_SPAWN_SEM:` 은 대기 구간에 beat가
    없어 워치독이 300초 무진전으로 오판해 강제 종료(os._exit 75)하는 사고 원인이었다.
    호출 후 (True 반환 시) 반드시 `try/finally: _LLM_SPAWN_SEM.release()` 로 짝 맞출 것.

    ★ timeout (ERRORS [474] — 2026-07-22):
      None = 무한 대기(기존 동작 — 발행 등 긴급 경로).
      값 지정 시 초과하면 **False 반환** 하고 포기 → 호출자가 defer.
      종전엔 상한이 없어, 대기 중에도 beat 를 보내니 워치독도 정상으로 판단했다.
      실측: GUARDIAN Tier-2 세션 4966초(82.8분) 중 실작업은 630초 상한이고
      나머지 70분+ 가 *줄 서 있던 시간* 이었다.

    Returns: True = 획득(release 의무) / False = timeout 포기(release 하지 말 것)
    """
    try:
        from JARVIS00_INFRA.watchdog import beat as _beat
    except Exception:
        def _beat() -> None: pass
    _beat()
    _waited = 0.0
    while not _LLM_SPAWN_SEM.acquire(timeout=_LLM_SEM_POLL_SEC):
        _beat()   # ★ 세마포어 대기 중에도 진행 신호 — freeze-kill 오탐 방지
        _waited += _LLM_SEM_POLL_SEC
        if timeout is not None and _waited >= timeout:
            import logging as _lg_sem
            _lg_sem.getLogger("jarvis.llm").info(
                f"⏭ LLM 순번 대기 {_waited:.0f}s 초과 — 배경 작업 포기(defer). "
                f"긴급 경로에 차선을 양보한다."
            )
            return False
    return True


# ★ Rate-limit 회로 차단기 (ERRORS [288] — 2026-07-03)
# 연속 *진짜 스로틀* N회 시 open → 비필수 호출은 즉시 "" 반환 (재시도 0)
# 쿨다운 후 probe 1회(1샷) 허용 → 성공 시 close.
_CIRCUIT_THRESHOLD = int(os.getenv("LLM_CIRCUIT_THRESHOLD", "3") or "3")
_CIRCUIT_COOLDOWN_SEC = float(os.getenv("LLM_CIRCUIT_COOLDOWN_SEC", "90") or "90")
# 필수 alias 면제 셋 — open 중에도 1회 실시도 허용 (대본 본문·사실성 게이트가 "" 즉사
# → 발행 통째 실패로 번지는 것 방지). 장식성 호출(번역·라벨·태그)만 즉시 폴백.
_CIRCUIT_EXEMPT_ALIASES = {
    a.strip() for a in
    (os.getenv("LLM_CIRCUIT_EXEMPT", "writer,fact_judge,engagement_judge,analyzer") or "").split(",")
    if a.strip()
}
_circuit_lock = _threading.Lock()
_circuit_consecutive_throttles = [0]
_circuit_open_since = [0.0]  # monotonic timestamp; 0 = closed
# ★ 직전 _run_sdk_sync 호출의 스로틀 여부 (스레드별) — CLI 부재·auth 오류 등
# 비스로틀 빈 응답으로 회로가 열리는 오탐 방지 (결함 b)
_LAST_CALL = _threading.local()


def _pace_spawn() -> None:
    """직전 spawn 과 최소 간격 유지 (burst rate-limit 완충). 기본 off."""
    if _LLM_MIN_INTERVAL <= 0:
        return
    with _LLM_PACE_LOCK:
        _now = _time_pace.monotonic()
        _wait = _LLM_LAST_SPAWN[0] + _LLM_MIN_INTERVAL - _now
        if _wait > 0:
            _time_pace.sleep(_wait)
            _now = _time_pace.monotonic()
        _LLM_LAST_SPAWN[0] = _now


def _run_sdk_sync(
    prompt: str,
    model: str = _DEFAULT_MODEL_ID,
    system: str = "",
    timeout: int = 300,
) -> str:
    """claude-code-sdk 동기 래퍼 — 응답 수집 후 ProcessError/MessageParseError 무시.

    ★ anyio.fail_after(timeout) 는 SDK subprocess 전송이 블로킹(비-yield) I/O로 멈추면
    인터럽트를 못 걸 수 있다 — google_collector._bounded() 가 pytrends 에 대해 이미 고친
    것과 동일한 클래스의 버그(레이더 수집이 메시지 0건인 채 300초+를 통째로 블로킹해
    watchdog freeze 880s 로 감지된 사고). ThreadPoolExecutor + fut.result(timeout=) 로
    호출 자체에 강한 벽시계 상한을 걸고, 대기 중에도 주기적으로 beat() 해 오탐/무한
    블로킹을 동시에 방지한다.
    """
    import anyio
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import MessageParseError, ProcessError

    # ★ cwd 격리 — 저장소 밖 전용 폴더. CLAUDE.md 자동 로드(48,940 토큰/호출) 차단.
    _opts_kw: dict = {"model": model, "env": _sdk_env(_CURRENT_ALIAS.get())}
    _scratch = _llm_scratch_dir()
    if _scratch:
        _opts_kw["cwd"] = _scratch

    # ── ② system 은 *system 자리* 로 (2026-07-26, 실측 −27%) ──────────────────
    #   종전엔 `f"{system}\n\n{prompt}"` 로 **사용자 메시지에 병합** 했다. 그러면 매 호출
    #   불변인 블록(헌법·루브릭·역할)이 프롬프트와 한 덩어리가 되어, 프롬프트가 한 글자만
    #   달라도 캐시 블록 전체가 무효화된다 — `writer` 의 캐시 재사용이 **1.0x**(쓰기 2.27M ≈
    #   읽기 2.17M)였던 이유다. 캐시 쓰기 프리미엄(`_CACHE_WRITE_MULT`) 탓에 *절감이 아니라 순손실*.
    #   ⚠️ 종전 이 자리에 "자리만 옮기니 품질 위험이 없다" 고 적혀 있었으나 **근거가 없다**
    #     (ERRORS [542]): 그 A/B(ERRORS.md:220-292)는 조건이 A(현행) vs D(신안) 둘뿐이고
    #     `disallowed_tools=["*"]` 와 **합본**으로 측정됐으며, 개선 원인도 도구 차단으로 귀속했다.
    #     즉 **이 이동 단독의 품질 근거는 측정된 적이 없다.** 방향도 중요하다 — user→system 은
    #     지시 권위가 *올라가는* 쪽이라 무해했을 수 있으나, 그 역(system→user)은 다른 문제다.
    #     프롬프트 조각을 옮길 때 "자리만 이동이라 안전"을 **근거 없이 재사용하지 말 것**.
    #   ★ ReAct 경로 보존: `router.py` 는 도구 스키마를 `system` 에 주입한 뒤 응답 텍스트에서
    #     tool_calls 를 파싱한다. 호출자의 `system` 을 *그대로* 넘기므로 그 규약이 유지된다.
    #     (호출자 system 을 다른 것으로 *대체* 하면 tool_calls 가 조용히 사라진다 — 금지.)
    #   무배포 되돌리기: `LLM_SPLIT_SYSTEM=0`
    full_prompt = prompt
    if system:
        if _SPLIT_SYSTEM:
            _opts_kw["system_prompt"] = system
        else:
            full_prompt = f"{system}\n\n{prompt}".strip()
    full_prompt = _sanitize_prompt(full_prompt)   # ★ embedded null byte 크래시 차단

    # ── ① 도구 정의 제거 (2026-07-26, 실측 31,468 → 227 토큰/호출) ──────────────
    #   ★ 근거 — 이 함수는 응답에서 **`TextBlock` 만 수집** 한다. 즉 모델이 도구를 써도
    #     그 결과는 *버려진다*. 그런데 도구 왕복(멀티턴)은 호출의 11% 인데 토큰의 **36%**
    #     (12.0M/33.2M)를 먹는다. 쓰지도 않는 기능에 토큰 1/3 을 태우고 있었다.
    #   ★ 와일드카드인 이유(② 동적 설계): 이름 목록을 박으면 **MCP 도구를 놓친다**.
    #     실측 — 명시 목록 11종은 13,914 토큰까지만 줄었고(사용자 커넥터 Notion·Drive 스키마가
    #     남았다), `["*"]` 는 227 까지 내려갔다. 목록을 유지보수할 필요도 없다.
    #   ★ 부수 효과(보안): 데몬 LLM 세션이 사용자 전역 MCP 커넥터를 상속해 실제로
    #     Notion·Google Drive 를 검색한 기록이 트랜스크립트에 있었다. 이 차단이 그것도 닫는다.
    #   ★ 적용 범위: **이 함수(invoke_text 경로)만**. 도구가 *필요한* 두 경로는 건드리지 않는다
    #     — `_invoke_sdk_vision`(이미지 Read 필수) · `run_sdk_query`(auto_repair 자가수정).
    #   무배포 되돌리기: `LLM_TEXT_NO_TOOLS=0`
    if _TEXT_NO_TOOLS:
        _opts_kw["disallowed_tools"] = ["*"]

    options = ClaudeCodeOptions(**_opts_kw)
    parts: list[str] = []
    throttled = {"v": False}
    hung = {"v": False}
    truncated = {"v": False}   # ★ 우리 데드라인이 스트림을 끊었는데 부분출력 존재 = 인프라 절단
    # ★ 토큰 계측 (ERRORS [456]): ResultMessage 의 usage/cost 를 박제해 사용량 가시화.
    #   종전엔 num_turns 만 보고 나머지를 버려 "언제 얼마나 썼는지" 를 알 수 없었다.
    _meter = {"usage": None, "cost": 0.0, "dur": 0, "turns": 0}

    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        _wd_beat = lambda: None

    async def _collect():
        nonlocal parts
        _wd_beat()
        with anyio.fail_after(timeout):
            async for msg in query(prompt=full_prompt, options=options):
                _wd_beat()   # ★ 메시지 수신 = 진행 신호 (SDK 살아있음 — 워치독 오탐 freeze-kill 방지)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
                # ★ Max 구독 burst 스로틀 감지 (사용자 박제 2026-07-01): rate-limit 시 CLI 는
                #   모델을 호출하지 않고 ResultMessage(num_turns=0, duration_api_ms=0, success)만
                #   흘려 *빈 응답* 을 낸다(예외 아님). 조용한 degrade 방지 위해 플래그로 표식.
                elif type(msg).__name__ == "ResultMessage":
                    if getattr(msg, "num_turns", 1) == 0:
                        throttled["v"] = True
                    # 계측 — 성공·스로틀 무관하게 항상 수집 (스로틀도 데이터)
                    _meter["usage"] = getattr(msg, "usage", None)
                    _meter["cost"]  = getattr(msg, "total_cost_usd", 0.0) or 0.0
                    _meter["dur"]   = getattr(msg, "duration_ms", 0) or 0
                    _meter["turns"] = getattr(msg, "num_turns", 0) or 0
                # ※ rate_limit_event 박제는 claude_sdk_compat._patched 가 *모든 경로*
                #   공통으로 수행한다. 여기서 또 기록하면 같은 이벤트가 2건으로
                #   중복 적재되므로(화면에 2줄) 의도적으로 하지 않는다.

    def _run_blocking() -> None:
        # ★ 이벤트 루프 오염 방지 (ERRORS [443] — 사용자 박제 2026-07-16):
        #   anyio.run() 완료 후 스레드의 이벤트 루프가 closed 상태로 남는다.
        #   ThreadPoolExecutor 가 스레드를 재사용하면 다음 anyio.run() 이 닫힌 루프를 만나
        #   "Loop is closed" 경고 → SDK 0 응답 → 또 300s 낭비(경제 발행 hang 연쇄 사고 근본 원인).
        #   매 호출마다 새 이벤트 루프 강제 설정 → 재사용 오염 제거.
        import asyncio as _aio
        _aio.set_event_loop(_aio.new_event_loop())
        try:
            anyio.run(_collect)
        except (MessageParseError, ProcessError):
            pass  # rate_limit_event 또는 프로세스 종료 — 응답은 이미 수집됨
        except TimeoutError:
            import logging as _logging
            _logging.getLogger("jarvis.llm").warning(f"SDK timeout {timeout}s — 수집된 응답: {len(parts)}개")
            if not parts:
                hung["v"] = True       # 0개 hang → 회로차단기 신호
            else:
                truncated["v"] = True  # ★ 부분출력 + 우리 데드라인 절단 = 인프라 스로틀(콘텐츠 결함 아님)
        except Exception as e:
            if not parts:
                import logging as _logging
                _logging.getLogger("jarvis.llm").warning(f"SDK 오류: {e}")

    # ★ 프로세스 전역 세마포어 — claude CLI 동시 spawn 직렬화 (Max burst 초과 방지)
    _pace_spawn()
    _acquire_llm_sem()
    try:
        # ★ P2-a (사용자 박제 2026-07-18): 락 대기를 짧게(45s) 캡 — LLM timeout(300s)만큼 기다리다
        #   사망하지 말 것. 락 획득 실패는 rate-limit 스로틀이 아니라 *경합* 이므로 hung(회로차단기
        #   신호)로 오분류하지 말고 lock_contention 으로 분리 → 회로 무오염 + harness defer.
        _lock_wait = min(timeout, _LOCK_ACQUIRE_MAX_WAIT)
        if not _proc_lock_acquire(timeout=_lock_wait):
            import logging as _logging
            _logging.getLogger("jarvis.llm").warning(
                f"크로스 프로세스 잠금 {_lock_wait:.0f}s 대기 초과 — lock_contention (회로 무오염, defer 위임)"
            )
            _LAST_CALL.throttled = False
            _LAST_CALL.hung = False           # ★ hung 아님 — 회로차단기 카운트 제외 (락 경합≠스로틀)
            _LAST_CALL.truncated = False
            _LAST_CALL.lock_contention = True
            return ""
        try:
            exe = ThreadPoolExecutor(max_workers=1)
            try:
                fut = exe.submit(_run_blocking)
                wall_deadline = timeout + 30.0   # anyio 내부 타임아웃 위 안전 마진
                waited = 0.0
                poll = 15.0                      # watchdog freeze_sec(300s) 보다 충분히 작게
                while True:
                    try:
                        fut.result(timeout=min(poll, max(0.1, wall_deadline - waited)))
                        break
                    except _FutTimeout:
                        waited += poll
                        _wd_beat()   # ★ 벽시계 대기 중에도 진행 신호 — freeze 오탐 방지
                        if waited >= wall_deadline:
                            import logging as _logging
                            _logging.getLogger("jarvis.llm").warning(
                                f"SDK 벽시계 상한 {wall_deadline:.0f}s 초과 — 강제 포기(수집 {len(parts)}개)"
                            )
                            if parts:
                                truncated["v"] = True  # ★ 부분출력 + 벽시계 절단 = 인프라 스로틀
                            else:
                                hung["v"] = True       # ★ 0개 = hang (기존 무신호 구멍 보강)
                            break
            finally:
                exe.shutdown(wait=False)   # 내부 스레드 leak 가능 — 메인 흐름 비블로킹 우선(_bounded() 와 동일 정책)
        finally:
            _proc_lock_release()
    finally:
        _LLM_SPAWN_SEM.release()
    _was_throttled = bool(throttled["v"] and not parts)
    _was_hung = bool(hung["v"] and not parts)
    _was_truncated = bool(truncated["v"] and parts)  # ★ parts>0 일 때만 (정의상 데드라인 절단)
    _LAST_CALL.throttled = _was_throttled
    _LAST_CALL.hung = _was_hung  # ★ hang(TimeoutError+0parts) 도 회로차단기 신호로 전달
    _LAST_CALL.truncated = _was_truncated  # ★ 절단(부분출력+데드라인) — 인프라 스로틀 신호
    if _was_throttled:
        import logging as _logging
        _logging.getLogger("jarvis.llm").debug("rate-limit 스로틀 (num_turns=0) — 재시도/폴백")
    # ★ 토큰 계측 박제 (ERRORS [456]) — 실패해도 본류를 막지 않는다.
    try:
        from shared.token_usage import record_call
        record_call(
            alias=_CURRENT_ALIAS.get() or "", model=model,
            usage=_meter["usage"], cost_usd=_meter["cost"],
            duration_ms=_meter["dur"], num_turns=_meter["turns"],
            ok=bool(parts), source="daemon",
        )
    except Exception:
        pass
    return "".join(parts)


def _invoke_sdk_vision(prompt: str, model: str, image_paths: list,
                       timeout: int = 180, cwd: str | None = None) -> str:
    """★ 비전(이미지 입력) SDK 호출 (사용자 박제 2026-07-05) — Read 도구로 이미지 파일 분석.

    invoke_text 는 텍스트 전용이라 이미지를 못 본다. SDK 가 구동하는 claude 에이전트에
    allowed_tools=['Read'] 를 주면 이미지 파일을 읽어 분석한다. 인포그래픽 디자인 학습 등
    실이미지 세밀 분석에 사용. permission_mode=bypassPermissions (Read 는 읽기전용, 안전).
    """
    import anyio
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import MessageParseError, ProcessError

    imgs = "\n".join(f"- {p}" for p in image_paths)
    full = _sanitize_prompt(f"다음 이미지 파일들을 Read 도구로 열어서 직접 보고 분석하라:\n{imgs}\n\n{prompt}")
    # ★ cwd 격리 + 이미지 접근 유지 (제안 ②): 호출자(design_learner)가 넘긴 cwd 는
    #   Read 도구가 이미지 파일에 닿게 하려는 것이다 — 그냥 교체하면 이미지를 못 읽는다.
    #   cwd 는 저장소 밖 전용 폴더로 두고(CLAUDE.md 자동 로드 차단), 원래 경로는
    #   add_dirs 로 작업 범위에 추가해 접근을 보존한다.
    #   (이미지 폴더도 저장소 안이라 종전엔 vision 호출도 매번 ~49k 를 재기록했다.)
    _v_kw: dict = {
        "model": model, "allowed_tools": ["Read"],
        "permission_mode": "bypassPermissions", "max_turns": 6,
        "env": _sdk_env(_CURRENT_ALIAS.get()),
    }
    _v_scratch = _llm_scratch_dir()
    if _v_scratch:
        _v_kw["cwd"] = _v_scratch
        if cwd:
            _v_kw["add_dirs"] = [cwd]
    else:
        _v_kw["cwd"] = cwd
    options = ClaudeCodeOptions(**_v_kw)
    parts: list[str] = []

    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        _wd_beat = lambda: None

    # ★ 비전도 계측한다 (2026-07-26) — 종전 이 경로는 `record_call` 이 없어 장부에 0이었다.
    #   design_learner 의 레퍼런스 비전 판정이 여기로 돈다. ③ 모든 통로 적용.
    _vmeter = {"usage": None, "cost": 0.0, "dur": 0, "turns": 0}

    async def _collect():
        _wd_beat()
        with anyio.fail_after(timeout):
            async for msg in query(prompt=full, options=options):
                _wd_beat()   # ★ 메시지 수신 = 진행 신호 (SDK 살아있음 — 워치독 오탐 freeze-kill 방지)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
                elif type(msg).__name__ == "ResultMessage":
                    _vmeter["usage"] = getattr(msg, "usage", None)
                    _vmeter["cost"]  = float(getattr(msg, "total_cost_usd", 0) or 0)
                    _vmeter["dur"]   = int(getattr(msg, "duration_ms", 0) or 0)
                    _vmeter["turns"] = int(getattr(msg, "num_turns", 0) or 0)

    def _record_vision() -> None:
        try:
            from shared.token_usage import record_call
            # ★ 폴백을 "vision" 으로 두지 않는다 (ERRORS [540]) — MODELS 에 없는 이름이
            #   진짜 alias 인 척 DB 에 쌓여 세분화를 무력화했다. 비어 있으면 *비어 있음이
            #   드러나야* 고칠 수 있다. 통로 구분은 `source="vision"` 이 이미 담당한다.
            record_call(alias=_CURRENT_ALIAS.get() or "", model=model,
                        usage=_vmeter["usage"], cost_usd=_vmeter["cost"],
                        duration_ms=_vmeter["dur"], num_turns=_vmeter["turns"],
                        ok=bool(parts), source="vision")
        except Exception:                                   # noqa: BLE001
            pass

    _pace_spawn()
    _acquire_llm_sem()
    try:
        # ★ P2-a: 락 대기 짧게 캡 — vision 은 회로 미참여라 신호 불필요, 대기 낭비만 제거
        _lock_wait = min(timeout, _LOCK_ACQUIRE_MAX_WAIT)
        if not _proc_lock_acquire(timeout=_lock_wait):
            print(f"  ⚠️ vision 크로스 프로세스 잠금 {_lock_wait:.0f}s 대기 초과 — 포기")
            return ""
        try:
            try:
                anyio.run(_collect)
            except (MessageParseError, ProcessError):
                pass
            except TimeoutError:
                print(f"  ⚠️ vision SDK timeout {timeout}s — 수집 {len(parts)}개")
            except Exception as e:
                if not parts:
                    print(f"  ❌ vision SDK 오류: {e}")
        finally:
            _proc_lock_release()
    finally:
        _LLM_SPAWN_SEM.release()
    _record_vision()
    return "".join(parts)


def invoke_vision(alias: str, prompt: str, image_paths: list,
                  timeout: int = 180, cwd: str | None = None) -> str:
    """이미지 입력 LLM 단일 진입점 (SDK Read 도구). 텍스트 결과 반환. 실패/미가용 시 ""."""
    if not image_paths:
        return ""
    # ★ 발행창 보류 — 네 통로 공통 판정 (2026-07-25). 종전엔 이 문이 무방비라
    #   design_learner(job j06_design_learn, 05:00)의 비전 호출이 발행 보호구간을 그대로 통과했다.
    _v_why = defer_reason(alias)
    if _v_why:
        import logging as _lgv
        _lgv.getLogger("jarvis.llm").info(
            f"🛡 {_v_why} — 배경 vision 호출({alias}) 보류 (한도를 글 작성에 우선 배정)")
        return ""
    model = _ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID)
    try:
        # ★ alias 귀속 (ERRORS [540]) — 종전엔 이 통로가 빠져 비전 호출이 전부
        #   가짜 alias "vision" 으로 찍혔다. 텍스트 통로와 **같은 헬퍼**를 쓴다(① 단일 진입점).
        _bind_alias(alias)
        return _invoke_sdk_vision(prompt, model, [str(p) for p in image_paths],
                                      timeout=timeout, cwd=cwd)
    except Exception as e:
        print(f"  ❌ invoke_vision 오류: {e}")
        return ""


def _circuit_record_throttle() -> None:
    """rate-limit 빈 응답 → 연속 카운터 증가, 임계 초과 시 회로 open."""
    with _circuit_lock:
        _circuit_consecutive_throttles[0] += 1
        if (_circuit_consecutive_throttles[0] >= _CIRCUIT_THRESHOLD
                and _circuit_open_since[0] == 0.0):
            import time as _tm
            _circuit_open_since[0] = _tm.monotonic()
            print(f"  🔴 [LLM] rate-limit 회로 차단 — 연속 {_circuit_consecutive_throttles[0]}회 throttle, "
                  f"{_CIRCUIT_COOLDOWN_SEC}s 쿨다운")


def _circuit_record_success() -> None:
    """정상 응답 → 회로 즉시 close."""
    with _circuit_lock:
        _circuit_consecutive_throttles[0] = 0
        _circuit_open_since[0] = 0.0


def _circuit_gate() -> str:
    """회로 상태 조회 + probe 획득 (★ 상태 전이 있음 — 순수 술어 아님).

    반환: 'closed' 정상 / 'open' 차단(비필수 호출 즉시 폴백) /
          'probe' 쿨다운 경과 — 이 호출 1회를 1샷 실시도로 허용 (open_since 리셋
          → 다음 probe 는 다시 쿨다운 후. 락 직렬화로 probe 폭주 없음).
    """
    with _circuit_lock:
        if _circuit_open_since[0] == 0.0:
            return "closed"
        import time as _tm
        elapsed = _tm.monotonic() - _circuit_open_since[0]
        if elapsed >= _CIRCUIT_COOLDOWN_SEC:
            _circuit_open_since[0] = _tm.monotonic()
            return "probe"
        return "open"


def last_call_infra_incomplete() -> bool:
    """직전 invoke_text 호출이 *인프라 사유*(스로틀 빈응답/hang/데드라인 절단)로 미완결이었는지.

    ★ _LAST_CALL 은 thread-local — invoke_text 를 호출한 *동일 스레드* 에서 *반환 직후* 읽어야
      유효. 다른(워커) 스레드에서 관측하려면 circuit_is_open() 사용.
    콘텐츠 결함(정상 완료인데 짧음·빈약)과 인프라 스로틀을 호출자가 구분하는 유일한 신호원.
    """
    return bool(
        getattr(_LAST_CALL, "throttled", False)
        or getattr(_LAST_CALL, "hung", False)
        or getattr(_LAST_CALL, "truncated", False)
        or getattr(_LAST_CALL, "lock_contention", False)   # ★ P2-a: 락 경합도 미완결(defer 대상, 회로 무오염)
    )


def last_call_infra_reason() -> str:
    """직전 미완결의 *구체적 사유*. 오진 방지용 (ERRORS [460]).

    ★ 왜 필요한가: 종전엔 스로틀·타임아웃·절단·락경합을 전부 "인프라 스로틀" 한 덩어리로
      표기했다. 2026-07-20 테마 티스토리 6/6 실패의 실제 원인은 *timeout 300s 초과*
      (생성 88토큰/초 × 27.6K 토큰 ≈ 314초)였는데 로그·텔레그램이 "스로틀" 이라고만
      말해 한도·rate-limit 쪽으로 진단이 한참 헤맸다. 사유를 분리해 표기한다.
    """
    if getattr(_LAST_CALL, "hung", False):
        return "timeout"            # 상한 내 완료 실패, 부분출력 0 — 시간 예산 부족 신호
    if getattr(_LAST_CALL, "truncated", False):
        return "truncated"          # 부분출력 + 데드라인 절단
    if getattr(_LAST_CALL, "throttled", False):
        return "throttle"           # num_turns=0 — 서버가 모델 호출 자체를 거절
    if getattr(_LAST_CALL, "lock_contention", False):
        return "lock_contention"    # 크로스 프로세스 락 경합
    return ""


_INFRA_REASON_LABEL = {
    "timeout":         "생성 시간 초과 — 분량 대비 timeout 부족(시간 예산 재검토)",
    "truncated":       "생성 절단 — 데드라인이 스트림을 끊음",
    "throttle":        "인프라 스로틀 — 서버가 호출 거절(한도/rate-limit)",
    "lock_contention": "락 경합 — 다른 호출이 점유 중",
}


def infra_reason_label(reason: str = "") -> str:
    """사유 코드 → 사람이 읽는 설명. 미상이면 종전 표기 유지."""
    return _INFRA_REASON_LABEL.get(
        reason or last_call_infra_reason(),
        "인프라 미완결 — 일시적(다음 시도/회차 재개)")


# ── infra_throttle 오류코드 단일 진입점 (ERRORS [460]) ─────────────────
#   판정 4곳·소비 2곳이 각자 문자열을 만들고 파싱하면 또 갈라진다.
#   생성·판별·라벨링을 여기 3함수로 고정한다.
_INFRA_ERR_PREFIX = "infra_throttle"


def make_infra_error(reason: str = "") -> str:
    """미완결 오류코드 생성 — `infra_throttle:<사유>`. 호출자는 문자열 조립 금지."""
    return f"{_INFRA_ERR_PREFIX}:{reason or last_call_infra_reason() or 'unknown'}"


def is_infra_error(err: str | None) -> bool:
    """오류코드가 인프라 미완결인가 (사유 유무 무관)."""
    return bool(err) and str(err).startswith(_INFRA_ERR_PREFIX)


def describe_infra_error(err: str | None) -> str:
    """오류코드 → 사람이 읽는 사유 설명. 사유가 없으면 일반 표기."""
    s = str(err or "")
    reason = s.split(":", 1)[1] if ":" in s else ""
    return infra_reason_label(reason)


def circuit_is_open() -> bool:
    """rate-limit 회로차단기 open 여부 — 순수 read-only peek (probe 전이·상태변이 없음).

    ★ 프로세스 전역 상태 — parallel 워커 스레드에서도 관측 가능(thread-local 아님).
      probe 를 소비하는 _circuit_gate() 와 달리 회로 계정을 오염시키지 않는다.
    """
    with _circuit_lock:
        return _circuit_open_since[0] != 0.0


def invoke_text(alias: str, prompt: str, system: str = "", timeout: int = 180,
                _retries: int = 4, _essential: bool = False,
                _nonessential: bool = False, **overrides) -> str:
    """Claude Code SDK 호출 단일 진입점 — 본문(text)만 반환 (하위호환 유지).

    ★ 2026-07-25 ①단일 진입점: 구현 본체는 `invoke_text_result` 하나뿐이고 이 함수는
      그 위에 얹힌 얇은 어댑터다. 시그니처·반환·동작은 종전과 **완전히 동일** —
      호출자(수백 곳) 무영향. "모델이 판정을 못 했다"를 구분해야 하는 *판정형* 호출만
      `invoke_text_result` 를 쓴다 (빈 문자열과 판정불가가 구분되지 않는 게 결함이었다).
    """
    text, _ok = invoke_text_result(
        alias, prompt, system=system, timeout=timeout, _retries=_retries,
        _essential=_essential, _nonessential=_nonessential, **overrides)
    return text


def invoke_text_result(alias: str, prompt: str, system: str = "", timeout: int = 180,
                       _retries: int = 4, _essential: bool = False,
                       _nonessential: bool = False, **overrides) -> tuple[str, bool]:
    """Claude Code SDK 호출 단일 진입점 — `(text, ok)` 반환.

    ★ ok 의 의미 (에이전트 간 계약 — 2026-07-25):
        ok=True  : 모델이 실제로 답했다. text 는 그 답(비어있지 않음).
        ok=False : **모델이 판정을 못 했다.** 회로 open·발행창 보호·SDK 실패·빈 응답·
                   절단(truncated) 전부 여기. text 는 "" 이거나 절단된 부분출력이다.

      종전 `invoke_text` 는 이 셋(정상 빈 답변 / 회로 open 즉시 폴백 / SDK 실패)을
      **모두 ""** 로 뭉개서 반환했다. 예외도 없어 GUARDIAN 기록도 로그도 남지 않았고,
      품질 게이트들이 *판정을 한 번도 안 하고* "통과" 시키는 implicit error 의 진원지였다.
      판정형 호출(사실성·매력도·진실성 감사)은 반드시 이 함수로 ok 를 확인할 것.

    모든 alias — Sonnet 5 단일 모델 (ADR 017, 사용자 박제 2026-07-06 — ADR 015 폐지).

    모든 alias — Sonnet 5 단일 모델 (ADR 017, 사용자 박제 2026-07-06 — ADR 015 폐지).

    ★ rate-limit 재시도 (사용자 박제 2026-07-01): 빈 응답이면 지수 백오프+지터로
      재시도. ★ 회로 차단기 (ERRORS [288] — 2026-07-03): 연속 *진짜 스로틀* ≥3 회 시
      쿨다운 동안 비필수 호출 즉시 "" 폴백. 필수 alias(_CIRCUIT_EXEMPT_ALIASES)와
      probe 는 1샷 실시도. ★ 데드라인 강등: JARVIS_LLM_DEADLINE_TS(epoch) 잔여 <10분
      이면 재시도 1회·백오프 0 — 발행(Layer 4) 시간 보호.

    ★ _nonessential (사용자 박제 2026-07-05, ERRORS [368]): *비필수* 호출(자기비평·
      매력도·번역·썸네일 등 — 폴백이 있어 없어도 발행되는 것). 스로틀(회로 open/probe)
      감지 시 *SDK 호출 없이 즉시 "" 반환* — timeout·재시도로 임계경로를 막지 않는다.
      회로 정상일 때도 1회·시간상자(≤45초). 필수 alias 면제(writer 등)보다 *우선* 적용.
    """
    import time as _t, random as _r

    # ★ 스레드로컬 위생 (truncated 신호 도입): 진입 시 1회 리셋해 early-return
    #   (회로 open 폴백 등) 경로에서 이전 호출의 인프라 플래그가 새는 것을 방지.
    _LAST_CALL.throttled = False
    _LAST_CALL.hung = False
    _LAST_CALL.truncated = False
    _LAST_CALL.lock_contention = False   # ★ P2-a: 락 경합 신호도 진입 리셋

    # ★ 재시도 최대 3회 상한 (사용자 박제 2026-07-06): 어떤 재시도도 3회 초과 금지.
    #   기본 _retries=4 → 실효 3으로 캡. deadline/_nonessential/probe/open 강등은 더 낮춤.
    # ★ 재시도 상한 SSOT 파생 (사용자 박제 2026-07-21): harness.DEFAULT_MAX_ATTEMPTS
    #   하나로 LLM 계층·harness 계층을 동시에 통제한다. 종전엔 여기 `3` 이 박혀 있어
    #   상한을 바꾸려면 두 곳을 따로 고쳐야 했고, 두 층의 곱(최악 증폭)이 통제 불능이었다.
    retries = max(1, min(_max_retries(), _retries))
    backoff = True
    # ★ 토큰 계측 귀속 (ERRORS [456]) — 헬퍼 경유 (ERRORS [540]: 통로별 누락 차단).
    #   ★ 여기서 `with` 를 쓰지 않는 이유: 이 함수는 아래 긴 재시도 루프 전체에서 alias 가
    #     유지돼야 하고 중간에 return 하는 경로가 많다. contextvar 는 스레드/태스크 로컬이라
    #     set 만으로도 이 호출 문맥에 갇힌다. (중첩이 실제로 생기는 곳은 vision 통로 뿐)
    _bind_alias(alias)

    # ★ 글로벌 데드라인 강등 — 발행 파이프라인(economic_poster 등)이 설정
    try:
        _dl = float(os.environ.get("JARVIS_LLM_DEADLINE_TS", "0") or "0")
        _rem = _dl - _t.time()
        # ★ stale carryover 차단 (사용자 박제 2026-07-18): 발행 액션 데드라인 임박(잔여<600s) 시에만
        #   강등하되, 이미 1시간 이상 지난 데드라인은 이전 발행의 잔재(예: 06:30 경제 값이 pop 안 돼
        #   21:00 테마에서 관측 → 모든 테마 호출 상시 강등)이므로 무시. 활성 액션의 정상 overrun
        #   (잔여 0~-3600s)은 여전히 강등해 발행창 밖 호출만 부당 강등에서 제외한다.
        if _dl and -3600 < _rem < 600:
            retries, backoff = 1, False
    except Exception:
        pass

    # ★ 발행 중 background alias 자동 강등 (2026-07-15):
    #   동일 프로세스(daemon) 안에서 guardian 이 세마포어를 timeout=300s 로 점유해
    #   발행 파이프라인을 최대 300s 차단하던 사고 방지.
    #   mark_publishing(True) → 모든 BG alias 호출을 timeout ≤90s·retries=1 로 단축.
    # ★ 발행창 보호 구간 (사용자 승인 2026-07-20 — 제안 ③): 발행 시각 前
    #   _PROTECT_MIN 분 동안 background alias 를 *아예 차단* 해 한도를 발행에 몰아준다.
    #   종전엔 발행이 *시작된 뒤*(mark_publishing) 강등만 했으므로, 발행 직전에
    #   심층감사·학습이 한도를 태워버리는 것을 막지 못했다.
    #   보호 시각은 DEFAULT_JOBS cron 에서 도출 — 하드코딩 없음.
    # ★★ 배경 alias 는 발행창에서 *보류* — 강등 아님 (사용자 박제 2026-07-25).
    #   "03·09·02·06·08 이 도는 동안 LLM 은 오로지 글 작성에만 쓰인다."
    #   ① 판정은 `bg_defer_reason()` 단일 소스 — 발행 中 + 발행 前 보호구간을 한 번에 답한다.
    #   ② 종전 결함(같은 병 3번째): 여기서 `_PUBLISHING_ACTIVE`(threading.Event)만 봤다.
    #      경제 브리핑은 **subprocess** 라 데몬의 Event 는 꺼져 있어(ERRORS [474] 와 동일)
    #      경제 발행 내내 데몬 쪽 배경 LLM 이 차단도 강등도 안 된 채 한도를 먹었다.
    #      `bg_defer_reason()` 은 파일 표식까지 보므로 프로세스 경계를 넘는다.
    #   ③ '90초로 강등' 은 의미가 없었다 — Tier-2 한 세션이 10분 이상이라 강등해도 차선을 문다.
    #   ④ alias 로는 못 거르는 배경 잡이 있다 — daily_review 는 `analyzer`, design_learn 은
    #      `writer` 를 쓴다(둘 다 글 작성 alias). 그렇다고 그 alias 를 막으면 09 수집·06 이미지가
    #      죽는다. 그래서 *잡 문맥* 으로 함께 판정한다: 파이프라인이 아닌 스케줄 잡이면 보류.
    #      잡이 아닌 문맥(텔레그램 사용자 명령·수동 실행)은 job_id 가 없어 여기 안 걸린다.
    _bg_why = defer_reason(alias)          # ★ 네 통로 공통 단일 판정
    if _bg_why:
        import logging as _lg
        _who = getattr(_JOB_CTX, "job_id", "") or f"alias:{alias}"
        _lg.getLogger("jarvis.llm").info(
            f"🛡 {_bg_why} — 배경 작업 '{_who}'({alias}) 보류 (한도를 글 작성에 우선 배정)"
        )
        return "", False      # ★ 모델 미호출 — 호출자는 다음 기회에 재시도
    # ★ P-C 발행창 essential 재시도 캡 (사용자 박제 2026-07-18): writer(본문 생성)·fact_judge·
    #   engagement_judge(발행 전 검증)는 필수라 timeout 은 유지하되, 스로틀/SDK 스톨 시 재시도로
    #   913s(최대 3×300+백오프)로 증폭되는 것을 차단 — retries=1. 스로틀·스톨 창에서 같은 창
    #   재발사는 무의미하므로 1회 후 defer 가 정상경로다. analyzer(fact·chart 추출)는 품질 보존 위해
    #   강등 제외 — 추출은 선계산(06:00/20:30 저부하 창)으로 발행창 밖 이전됨.
    elif alias in _PUBLISH_ESSENTIAL_CAP and is_publishing():
        retries = min(retries, 1)
        backoff = False

    # ★ 회로 차단기 게이트 (_essential=True 는 호출 단위 필수 면제 —
    #   설계 planner 등 품질 조타수 호출이 스로틀 중에도 1회 실시도, ERRORS [300])
    _gate = _circuit_gate()
    # ★ 비필수 호출 — 스로틀 시 임계경로 블로킹 절대 금지 (ERRORS [368]). 필수 면제보다 우선.
    if _nonessential:
        if _gate in ("open", "probe"):
            # 스로틀 중 — SDK 미호출·즉시 폴백. ok=False 로 *판정 불가* 를 명시한다.
            return "", False
        retries, backoff = 1, False        # 정상일 때도 1샷
        timeout = min(timeout, 90)         # 시간 상자 — 최악 90초 (max_tokens≤700 안에 완료)
    elif _gate == "open":
        if _essential or alias in _CIRCUIT_EXEMPT_ALIASES:
            retries, backoff = 1, False   # 필수 호출 — open 중에도 1회 실시도
        else:
            print("  ⏳ [LLM] 회로 차단 중 — 즉시 폴백 (재시도 생략)")
            return "", False           # ★ 모델 미호출 — 판정 불가
    elif _gate == "probe":
        retries, backoff = 1, False       # probe 는 1샷 — 최악 1 spawn 만 소모

    model = _ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID)
    result = ""
    throttled_seen = False
    hung_seen = False
    truncated_seen = False
    for _attempt in range(retries):
        # ★ 전역 하트비트 (사용자 박제 2026-07-06): LLM 호출 = 진행 신호 → freeze 워치독
        #   이 오래 걸리는 정상 LLM 작업을 멈춤으로 오탐하지 않도록 매 시도마다 beat.
        try:
            from JARVIS00_INFRA.watchdog import beat as _wd_beat
            _wd_beat()
        except Exception:
            pass
        try:
            _LAST_CALL.throttled = False
            _LAST_CALL.hung = False
            _LAST_CALL.truncated = False
            _LAST_CALL.lock_contention = False
            result = _run_sdk_sync(prompt, model=model, system=system, timeout=timeout) or ""
        except Exception:
            result = ""
        _truncated = getattr(_LAST_CALL, "truncated", False)
        if result.strip() and not _truncated:
            _circuit_record_success()
            return result, True        # ★ 유일한 ok=True 경로 — 모델이 실제로 답했다
        # ★ 절단(우리 데드라인이 스트림을 끊음 + 부분출력) = 인프라 스로틀 — 성공 처리·회로 리셋
        #   금지. 빈 응답과 동급으로 재시도 루프에 흘리고, 소진 후 best-effort 로 절단본 반환.
        if _truncated:
            truncated_seen = True
        if getattr(_LAST_CALL, "throttled", False):
            throttled_seen = True
            # ★ 스로틀 = 재시도 금지 (사용자 승인 2026-07-20 — 제안 ①)
            #   num_turns=0 은 *모델을 아예 호출하지 않았다* 는 신호(한도/스로틀).
            #   같은 창에서 즉시 재발사해도 같은 결과이고, 한도가 없을 때 한도를 더
            #   태운다. LLM 재시도(최대 3) × harness max_attempts(3) = 최악 9배 증폭의
            #   진원지. 여기서 끊고 상위(harness)의 defer 에 위임한다.
            #   킬스위치: LLM_THROTTLE_NO_RETRY=0
            if _THROTTLE_NO_RETRY and _attempt < retries - 1:
                import logging as _lg
                _lg.getLogger("jarvis.llm").info(
                    f"⏭ 스로틀 감지 — 재시도 생략 후 defer (alias={alias}, "
                    f"시도 {_attempt + 1}/{retries}). 같은 창 재발사는 한도만 소모."
                )
                break
        if getattr(_LAST_CALL, "hung", False):
            hung_seen = True
        if backoff and _attempt < retries - 1:
            _t.sleep(min(30.0, 4 * (2 ** _attempt)) + _r.uniform(0, 1.5))
    # 모든 재시도 실패 — 진짜 스로틀(ResultMessage) OR SDK hang(TimeoutError+0parts) 모두
    # 회로차단기 카운트. CLI 부재·auth 빠른 실패는 hung=False라 오탐 없음.
    if throttled_seen or hung_seen or truncated_seen:
        _circuit_record_throttle()
    # ★ 루프를 끝까지 돈 = 성공 분기를 한 번도 못 탔다 → 판정 불가.
    #   text 는 "" 이거나 *절단된 부분출력* (best-effort 로 계속 반환 — 하위호환).
    return result, False


# ── 진단 ──────────────────────────────────────────────────────

# ── public ────────────────────────────────────────────────────

__all__ = [
    "ModelSpec", "MODELS", "get_spec",
    "model_id", "model_label", "pretty_model_id", "live_model_ids",
    "chat", "invoke_text", "invoke_text_result", "invoke_vision",
    "last_call_infra_incomplete", "circuit_is_open",
    "is_langchain_available",
]

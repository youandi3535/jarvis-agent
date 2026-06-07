"""JARVIS06_IMAGE/image_agent.py — 이미지 생성 에이전트 단일 진입점.

폴백 체인: Nanobana(Gemini) → Pollinations.ai  (★ 사용자 박제 2026-06-07 — ERRORS [263]:
Bing / HuggingFace 전멸 → 완전 삭제)
SVG 차트: Claude SVG Provider (LLM 동적 생성, 고정 템플릿 금지)
버스 연동: image.request 이벤트 수신 → image.response 발행
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

_ROOT      = Path(__file__).resolve().parents[1]
OUTPUT_DIR = _ROOT / "JARVIS06_IMAGE" / "output"


# ── 공개 API ─────────────────────────────────────────────────

def generate_photo(prompt_ko: str, out_dir: Path | None = None,
                   width: int = 1024, height: int = 1024,
                   seed: int | None = None,
                   prompt_en: str | None = None) -> Path:
    """사진 이미지 생성.

    폴백 체인: Nanobana(Gemini) → Pollinations.ai
    (★ Bing / HuggingFace 완전 삭제 — ERRORS [263] 박제 2026-06-07)

    Args:
        prompt_ko:  한국어 이미지 프롬프트 (자동 영어 번역)
        out_dir:    저장 디렉토리 (None = 기본 OUTPUT_DIR)
        width:      이미지 너비 힌트 (프로바이더가 지원하는 경우)
        height:     이미지 높이 힌트
        seed:       재현 가능한 시드 (프로바이더가 지원하는 경우)
        prompt_en:  이미 영어로 된 프롬프트 (지정 시 번역 생략 — LLM이 직접 생성한 경우)

    Returns:
        생성된 이미지 파일 경로.
    Raises:
        RuntimeError: 모든 백엔드 실패 시.
    """
    from JARVIS06_IMAGE.prompt_translator import translate
    from JARVIS06_IMAGE.providers.pollinations_provider import PollinationsProvider

    dest = Path(out_dir) if out_dir else OUTPUT_DIR
    if not prompt_en:
        prompt_en = translate(prompt_ko)

    # 마크다운 헤더·레이블 제거 (LLM이 "# Image Prompt\n\n..." 형태로 반환하는 경우)
    import re as _re_clean
    prompt_en = _re_clean.sub(r'^#+\s*[^\n]*\n+', '', (prompt_en or '').strip(), flags=_re_clean.MULTILINE)
    prompt_en = _re_clean.sub(r'^\*{1,2}[A-Za-z ]+\*{1,2}:?\s*', '', prompt_en.strip())
    prompt_en = prompt_en.strip().strip('"').strip("'").strip()
    if not prompt_en:
        prompt_en = translate(prompt_ko) or prompt_ko

    log.info(f"[J06] 사진 생성: '{prompt_ko[:40]}' → '{prompt_en[:60]}'")

    # ★ 사용자 박제 2026-06-07 (ERRORS [263]) — Bing / HuggingFace 완전 삭제.
    # Bing 쿠키 무한 만료 + HuggingFace DNS 차단·hf-inference 미지원 → 전멸.
    # 단일 폴백: Pollinations.ai (키 불필요)
    log.info("[J06] Pollinations.ai 호출")
    kw_args: dict = {}
    if seed is not None:
        kw_args["seed"] = seed
    return PollinationsProvider().generate(
        prompt_en, dest, width=width, height=height, **kw_args
    )


def generate_chart(data: dict[str, Any], chart_type: str, title: str,
                   out_dir: Path | None = None,
                   width: int = 800, height: int = 500) -> Path:
    """SVG/PNG 차트 생성 (Claude LLM 동적 생성).

    chart_type: bar | line | pie | radar | table | custom
    """
    from JARVIS06_IMAGE.providers.claude_svg_provider import ClaudeSVGProvider
    dest = Path(out_dir) if out_dir else OUTPUT_DIR
    log.info(f"[J06] 차트 생성: type={chart_type} title={title[:40]}")
    return ClaudeSVGProvider().generate(data, chart_type, title, dest, width, height)


def generate_infographic(
    section_text: str,
    keyword: str,
    out_path: Path | str,
    sector: str = "",
    section_title: str = "",
) -> Path:
    """블로그 섹션 텍스트 → matplotlib 인포그래픽 이미지 생성.

    image_spec.generate_image_spec() 으로 설계서 생성 후
    render_from_spec() (matplotlib 1순위) 으로 렌더링.

    Args:
        section_text:  섹션 전체 본문
        keyword:       블로그 키워드 / 테마
        out_path:      저장 경로 (.jpg / .png)
        sector:        섹터 (선택)
        section_title: 소제목 (선택)

    Returns:
        생성된 이미지 Path.
    Raises:
        RuntimeError: 렌더링 완전 실패 시.
    """
    from JARVIS06_IMAGE.image_spec import generate_image_spec, render_from_spec
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"[J06] 인포그래픽 생성: keyword={keyword[:30]} → {dest.name}")
    spec = generate_image_spec(
        section_text=section_text,
        keyword=keyword,
        sector=sector,
        section_title=section_title,
    )
    return render_from_spec(spec, dest)


def generate_thumbnail(title: str, keyword: str, sector: str = "",
                       platform: str = "naver", out_dir: Path | None = None,
                       body_text: str = "") -> str:
    """썸네일 생성 → 파일 경로 반환 (thumbnail_maker 위임)."""
    from JARVIS06_IMAGE.thumbnail_maker import create_thumbnail
    import time as _t
    dest_dir = Path(out_dir) if out_dir else OUTPUT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 이전 썸네일 삭제 — 새 생성 전 폴더의 기존 thumbnail_*.png 제거
    for old in dest_dir.glob("thumbnail_*.png"):
        try:
            old.unlink()
        except Exception:
            pass

    safe_kw = "".join(c for c in keyword[:20] if c.isalnum() or c in "_-") or "thumb"
    _ts = int(_t.time()) % 100000
    out_file = str(dest_dir / f"thumbnail_{safe_kw}_{_ts}.png")
    return create_thumbnail(theme=keyword, title=title, output_path=out_file,
                            body_text=body_text, platform=platform)


# ── 데몬 등록 진입점 ─────────────────────────────────────────

def register(scheduler, bus) -> None:
    """데몬 부팅 시 자동 등록: capability + bus 구독."""
    _register_capability()
    _subscribe_bus(bus)
    log.info("✅ JARVIS06_IMAGE 등록 완료")


def _status_section() -> str:
    return "🖼️ *JARVIS06 IMAGE* — 이미지 생성 대기 중"


def _register_capability() -> None:
    try:
        from shared.capabilities import declare
        declare(
            agent_id   = "jarvis06_image",
            domain     = "image",
            intents    = ["image.generate.photo", "image.generate.chart",
                          "image.generate.thumbnail"],
            tools      = [],
            requires_approval = ["image.generate.photo"],
            cost_class = "low",
            description= "이미지 생성 에이전트 — Pollinations(사진), Claude SVG(차트), 썸네일 (★ Bing/HF 폐기 2026-06-07)",
            tags       = ["image", "chart", "thumbnail", "svg", "pollinations"],
            help_section=(
                "🖼️ *이미지 생성 (JARVIS06)*\n"
                "슬래시 명령어 없음 — 자유 문장으로 요청\n"
                "예: 특징주 썸네일 만들어줘 / 환율 차트 그려줘"
            ),
            status_fn=_status_section,
        )
    except Exception as e:
        log.warning(f"⚠️ jarvis06_image capability 등록 실패: {e}")
        _g_report("image", e, module=__name__)


def _subscribe_bus(bus) -> None:
    """shared.bus 의 image.request 이벤트 구독."""
    try:
        bus.subscribe("image.request", _handle_bus_request)
        log.info("[J06] bus 'image.request' 구독 완료")
    except Exception as e:
        log.warning(f"[J06] bus 구독 실패 (무시): {e}")
        _g_report("image", e, module=__name__)


def _handle_bus_request(event: dict, source: str = "") -> None:
    """image.request 버스 이벤트 핸들러.

    bus.subscribe 는 handler(payload, source) 2인자로 호출 — source 인자 추가 (ERRORS [111] 동일 패턴).
    event: {"type": "photo"|"chart"|"thumbnail", "params": {...}, "reply_to": str}
    """
    req_type = event.get("type", "photo")
    params   = event.get("params", {})
    reply_to = event.get("reply_to", "image.response")
    try:
        if req_type == "photo":
            path = generate_photo(
                prompt_ko=params.get("prompt", ""),
                out_dir=params.get("out_dir"),
            )
        elif req_type == "chart":
            path = generate_chart(
                data      = params.get("data", {}),
                chart_type= params.get("chart_type", "bar"),
                title     = params.get("title", ""),
                out_dir   = params.get("out_dir"),
            )
        elif req_type == "thumbnail":
            path_str = generate_thumbnail(
                title   = params.get("title", ""),
                keyword = params.get("keyword", ""),
                sector  = params.get("sector", ""),
                platform= params.get("platform", "naver"),
                out_dir = params.get("out_dir"),
            )
            path = Path(path_str)
        else:
            raise ValueError(f"알 수 없는 요청 유형: {req_type}")

        try:
            from shared.bus import publish
            publish(reply_to, {"ok": True, "path": str(path), "type": req_type})
        except Exception:
            pass

    except Exception as e:
        log.error(f"[J06] image.request 처리 실패: {e}")
        _g_report("image", e, module=__name__)
        try:
            from shared.bus import publish
            publish(reply_to, {"ok": False, "error": str(e), "type": req_type})
        except Exception:
            pass


def handle_safe_intent(intent: str, params: dict | None = None) -> bool:
    """SAFE image 인텐트 처리."""
    return False


__all__ = [
    "generate_photo", "generate_chart", "generate_thumbnail",
    "generate_infographic",
    "process_draft",          # ★ 대본+수집자료 → 완성 블록 (JARVIS08 발행 준비)
    "register", "handle_safe_intent",
]


def process_draft(draft_html: str, theme: str, sector: str,
                  stocks_data: dict, collection_docs: list | None,
                  platform: str, out_dir) -> dict:
    """대본 HTML + JARVIS09 수집 자료 → 완성 블록. draft_processor 위임."""
    from JARVIS06_IMAGE.draft_processor import process_draft as _proc
    return _proc(
        draft_html=draft_html, theme=theme, sector=sector,
        stocks_data=stocks_data, collection_docs=collection_docs,
        platform=platform, out_dir=out_dir,
    )

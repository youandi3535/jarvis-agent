"""JARVIS06_IMAGE/image_agent.py — 이미지 생성 에이전트 단일 진입점.

이미지 생성: Cloudflare Workers AI 단독 (무료 티어)  (★ 사용자 결정 2026-08-05:
Bing / HuggingFace 전멸 → 완전 삭제)
버스 연동: image.request 이벤트 수신 → image.response 발행 (photo · thumbnail)
"""
from __future__ import annotations

import logging
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# ★ 에이전트 식별자 단일 소유 — capability 선언과 버스 응답 source 가 같은 값을 쓴다.
AGENT_ID = "jarvis06_image"

_ROOT      = Path(__file__).resolve().parents[1]
OUTPUT_DIR = _ROOT / "JARVIS06_IMAGE" / "output"




# ── 공개 API ─────────────────────────────────────────────────

def _verify_image_file(path) -> str:
    """생성 이미지 유효성 (0바이트·손상). "" 통과 / 사유 반환. 검증 인프라 미가용 시 통과."""
    try:
        from JARVIS00_INFRA.verification import is_valid_image_file
        return is_valid_image_file(path)
    except Exception:
        return ""


# ★ 검증 레지스트리 등록 (2026-07-02): 이미지 산출물 파일 유효성 체크포인트.
try:
    from JARVIS00_INFRA.verification import register_check as _reg_img_check

    @_reg_img_check("generate_photo", "이미지 파일 유효", severity="block")
    def _chk_photo_file(output, ctx):
        return _verify_image_file(output)

    @_reg_img_check("generate_infographic", "이미지 파일 유효", severity="block")
    def _chk_infg_file(output, ctx):
        return _verify_image_file(output)
except Exception:
    pass


def generate_photo(prompt_ko: str, out_dir: Path | None = None,
                   width: int = 1024, height: int = 1024,
                   seed: int | None = None,
                   prompt_en: str | None = None) -> Path:
    """사진 이미지 생성.

    프로바이더: Cloudflare Workers AI 단독 (Pollinations 유료화로 2026-08-05 삭제)
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

    # ★ 이미지 생성 = Cloudflare Workers AI 단독 (2026-08-05 사용자 결정 — ERRORS [574])
    #
    #   Pollinations 는 2026-08-05 07:36 부터 402 `Insufficient balance` 로 전멸했다.
    #   라이브 확인: 이미지 모델 **39개 전부 유료**, 키 없는 익명 티어는 401.
    #   Gemini(나노바나나)도 공식 가격표가 이미지 모델 전부 `Free Tier: Not available`.
    #   → 사용자 결정 "유료는 금액이 작아도 안 쓴다" 에 따라 **완전 삭제**.
    #
    #   Cloudflare: 무료 10,000 neuron/일 · Flux-1-Schnell 57.6 neuron/장 = 하루 약 173장.
    #   실측 3.0초 · 주제 정확. 우리가 쓰는 건 하루 4~10장이라 여유 17배.
    #
    #   ★ 프로바이더를 **하나만** 둔다. 둘이면 한쪽만 고치는 사고가 난다 —
    #     실제로 이 교체 중에 `thumbnail_maker` 를 빠뜨려 ③원칙을 어겼고,
    #     썸네일 경로를 *직접 돌려봐서야* 발견했다. 하나면 갈라질 수 없다.
    #   ★ 서킷 브레이커·전역 쿨다운은 함께 삭제했다 — Pollinations 의 IP 레벨 큐 제한
    #     (ERRORS [267][270]) 때문에 있던 것이고, Cloudflare 는 계정 단위 쿼터라 불필요하다.
    #     남겨두면 "왜 있는지 모르는 코드" 가 되어 다음 사람을 헷갈린다.
    _cf_err = ""
    try:
        from JARVIS06_IMAGE.providers.cloudflare_provider import CloudflareProvider
        _kw = {"seed": seed} if seed is not None else {}
        result = CloudflareProvider().generate(
            prompt_en, dest, width=width, height=height, **_kw)
    except Exception as _cfe:
        _cf_err = str(_cfe)
        log.warning(f"[J06] Cloudflare 이미지 생성 실패: {_cfe}")
        result = None

    # ★ Pollinations 완전 삭제 (2026-08-05 사용자 결정 — ERRORS [574])
    #   이미지 모델 39개 전부 유료화(402). "유료는 금액이 작아도 안 쓴다."
    #   프로바이더를 **하나만** 둔다 — 둘이면 한쪽만 고치는 사고가 다시 난다
    #   (실제로 이 교체 중에 `thumbnail_maker` 를 빠뜨려 ③원칙을 어겼다).
    if result is None:
        raise RuntimeError(_cf_err or "이미지 생성 실패 — Cloudflare 자격증명/한도 확인")

    _iv = _verify_image_file(result)
    if _iv:
        log.warning(f"[J06] 생성 이미지 검증 실패({_iv}) → 실패 처리(스킵/폴백)")
        raise RuntimeError(f"이미지 검증 실패: {_iv}")
    return result


# ── generate_chart — ★ 삭제 (사용자 박제 2026-08-10) ──────────────────────
#   `ClaudeSVGProvider().generate(...)` 를 직접 불러 픽셀을 만들어 반환했다.
#   (그 프로바이더 자체도 2026-08-10 삭제 — 호출자가 0곳이 된 고아였다)
#   초크포인트(`infographic_engine._emit` → `certify_image`)를 지나지 않으므로
#   **검증도 provenance 등록도 없는 차트**가 나올 수 있었다. 본문 호출자는 0곳인데
#   `shared.bus` 의 `image.request(type='chart')` 와 패키지 `__init__` 공개 export 로
#   외부 도달이 가능해, 게이트를 세우는 대신 도달 경로째 지웠다 (①원칙: 사본은 지운다).
#   수치 차트가 필요하면 초크포인트를 직접 부를 것 — 실데이터 dataset 이 입력이다:
#       from JARVIS06_IMAGE.infographic_engine import generate_infographic


# ── generate_infographic — ★ 삭제 (사용자 박제 2026-08-10) ─────────────────
#   여기 있던 `generate_infographic` 은 초크포인트
#   (`infographic_engine.generate_infographic` → `_emit` → `certify_image`) 와
#   **이름이 같은데** 본문은 `generate_image_spec` → `render_from_spec` 이라
#   `_emit` 을 지나지 않는 완전한 우회로였다. 이름이 같으므로 다음 작업자는
#   "초크포인트를 지난다" 고 믿는다 — 가장 나쁜 종류의 사본이다.
#   저장소 전역 호출자 0곳(실측)이라 위임이 아니라 삭제했다.
#   인포그래픽이 필요하면 초크포인트를 직접 부를 것:
#     from JARVIS06_IMAGE.infographic_engine import generate_infographic


def generate_thumbnail(title: str, keyword: str, sector: str = "",
                       platform: str = "naver", out_dir: Path | None = None,
                       body_text: str = "", tag_line: str = "") -> str:
    """썸네일 생성 → 파일 경로 반환 (thumbnail_maker 위임).

    tag_line: 썸네일 하단 카테고리 라벨 (예: '경제 브리핑' / '테마 분석'). 미지정 시 keyword.
    """
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
                            body_text=body_text, platform=platform,
                            tag_line=(tag_line or sector))


# ── 데몬 등록 진입점 ─────────────────────────────────────────

def register(scheduler, bus) -> None:
    """데몬 부팅 시 자동 등록: capability + bus 구독."""
    _register_capability()
    _subscribe_bus(bus)
    log.info("✅ JARVIS06_IMAGE 등록 완료")


def _status_section() -> str:
    lines = ["🖼️ *JARVIS06 IMAGE* — 이미지 생성 에이전트"]
    try:
        out_dir = OUTPUT_DIR
        if out_dir.exists():
            files = [f for f in out_dir.iterdir()
                     if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg")]
            total = len(files)
            png_cnt = sum(1 for f in files if f.suffix.lower() == ".png")
            svg_cnt = sum(1 for f in files if f.suffix.lower() == ".svg")
            lines.append(f"📊 생성 이미지: 총 {total}개  (PNG {png_cnt} · SVG {svg_cnt})")
        else:
            lines.append("📊 output 디렉토리 없음")
    except Exception as _e:
        lines.append(f"📊 통계 조회 실패: {_e}")
    # 프로바이더 상태 — **자격증명 유무를 실제로 확인** 한다(있다고 가정하지 않는다)
    try:
        from JARVIS06_IMAGE.providers.cloudflare_provider import provider_available as _cfa
        lines.append("✅ Cloudflare Workers AI 가용 (무료 티어, 단일 프로바이더)" if _cfa()
                     else "❌ Cloudflare 자격증명 없음 — .env 의 CLOUDFLARE_ACCOUNT_ID/API_TOKEN 확인")
    except Exception as _e2:
        lines.append(f"❌ 이미지 프로바이더 상태 확인 실패: {_e2}")
    lines.append("📁 출력: JARVIS06_IMAGE/output/")
    return "\n".join(lines)


def _register_capability() -> None:
    try:
        from shared.capabilities import declare
        declare(
            agent_id   = AGENT_ID,
            domain     = "image",
            # ★ "image.generate.chart" 제거 (2026-08-10) — generate_chart 삭제와 동시.
            #   선언만 남기면 처리기 없는 인텐트가 되어 "된다" 는 거짓 신호가 된다.
            intents    = ["image.generate.photo", "image.generate.thumbnail"],
            tools      = [],
            requires_approval = ["image.generate.photo"],
            cost_class = "low",
            description= "이미지 생성 에이전트 — Cloudflare Workers AI(사진), 썸네일",
            tags       = ["image", "thumbnail", "cloudflare"],
            help_section=(
                "🖼️ *이미지 생성 (JARVIS06)*\n"
                "슬래시 명령어 없음 — 자유 문장으로 요청\n"
                "예: 특징주 썸네일 만들어줘"
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


def _reply(reply_to: str, payload: dict) -> None:
    """image.request 응답 송출 — ★ 이 파일의 유일한 응답 경로.

    ★ 시그니처 불일치 + 침묵 삼킴 (사용자 박제 2026-08-10 최종리뷰 #4):
      `shared.bus.publish(event_type, source, payload=None)` 인데 종전엔
      `publish(reply_to, {...})` 로 **payload 를 source 자리에** 넘겼다. 그러면
      ① 실제 payload 는 `{}` 로 비고 ② dict 를 source 컬럼에 넣으니 sqlite 가
      `InterfaceError` 를 던지는데 ③ 바로 뒤 `except Exception: pass` 가 그것을 삼켜,
      요청자는 `image.response` 를 **영영 받지 못한 채** 아무 흔적도 남지 않았다.
      이제 위치인자를 맞추고, 실패는 삼키지 말고 GUARDIAN 에 박제한다.
      source 는 capability 선언(`_register_capability`)과 같은 에이전트 식별자다.
    """
    try:
        from shared.bus import publish
        publish(reply_to, AGENT_ID, payload)
    except Exception as e:
        log.error(f"[J06] image.response 송출 실패({reply_to}): {e}")
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
        # ★ type == "chart" 분기 삭제 (2026-08-10) — generate_chart 와 함께.
        #   검증을 지나지 않는 차트가 버스로 만들어질 수 있던 유일한 외부 도달 경로였다.
        #   알 수 없는 유형으로 떨어져 ValueError → reply {"ok": False} 로 나간다.
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

        _reply(reply_to, {"ok": True, "path": str(path), "type": req_type})

    except Exception as e:
        log.error(f"[J06] image.request 처리 실패: {e}")
        _g_report("image", e, module=__name__)
        _reply(reply_to, {"ok": False, "error": str(e), "type": req_type})


def handle_safe_intent(intent: str, params: dict | None = None) -> bool:
    """SAFE image 인텐트 처리."""
    return False


__all__ = [
    "generate_photo", "generate_thumbnail",
    "process_draft",          # ★ 대본+수집자료 → 완성 블록 (JARVIS08 발행 준비)
    "register", "handle_safe_intent",
]


def process_draft(*args, **kwargs) -> dict:
    """대본 HTML + CollectedData → 완성 블록. draft_processor 위임 (v2 — Step 6).
    브리지 호환을 위해 인자 그대로 전달 (신·구 시그니처 모두 수용)."""
    from JARVIS06_IMAGE.draft_processor import process_draft as _proc
    return _proc(*args, **kwargs)

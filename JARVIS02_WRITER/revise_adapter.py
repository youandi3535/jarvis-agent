"""
JARVIS02 — 재발행 어댑터
승인된 분석 결과(post_analysis.status='approved')를 받아
각 플랫폼에 수정·재발행한다.

실행:
  python revise_adapter.py <analysis_id>   # 특정 ID만
  python revise_adapter.py --all           # 승인 대기 전체 처리
  python revise_adapter.py --watch         # 폴링 데몬

루프 가드:
  is_revised=1 인 글은 분석 대상에서 제외되므로 무한 루프 없음.
  네이버는 글 당 1회 수정만 허용 (가드 내장).
"""
from __future__ import annotations

import sys
import os
import json
import time
import re
import requests
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

try:
    from JARVIS02_WRITER.length_manager import MAX_P_SENTS as _MAX_P
    from JARVIS02_WRITER.length_manager import build_length_phrase as _len_phrase
except ImportError:
    from length_manager import MAX_P_SENTS as _MAX_P  # 동일 폴더 직접 실행 시
    from length_manager import build_length_phrase as _len_phrase
from dotenv import load_dotenv

BASE_DIR    = Path(__file__).parent
JARVIS_ROOT = BASE_DIR.parent
sys.path.insert(0, str(JARVIS_ROOT))

load_dotenv(JARVIS_ROOT / ".env")

from shared import db
from shared.bus import on_post_revised

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 수정 패치 적용 — HTML에 개선안 반영
# ─────────────────────────────────────────────────────────────


def _pick_cta(platform: str, fallback: str = "") -> str:
    """플랫폼별 CTA 문구 LLM 동적 생성 — 매번 다른 표현."""
    from shared.llm import invoke_text as _llm
    _ctx = {
        "naver":   "네이버 블로그. 이웃신청·공감 유도. 해요체.",
        "tistory": "티스토리 블로그. 공감 버튼 클릭 유도. 합니다체.",
    }.get(platform, "블로그. 독자 반응 유도.")
    return _llm(
        "writer_fast",
        f"{_ctx} 독자에게 자연스러운 CTA {_len_phrase(1, _MAX_P)} 작성. 이모지 없이. 문장만 출력.",
        max_tokens=60, temperature=0.8
    ) or fallback or "읽어주셔서 감사합니다."


def _apply_patch(original_html: str, title: str, suggestions: list,
                 platform: str = "") -> tuple[str, str]:
    """suggestions를 HTML·제목에 적용. (new_title, new_html) 반환."""
    new_title = title
    new_html  = original_html or ""

    for s in suggestions:
        stype    = s.get("type", "")
        before_t = s.get("before", "")
        after_t  = s.get("after", "")

        if not after_t:
            continue

        if stype == "title":
            new_title = after_t[:100]

        elif stype == "cta":
            cta_text = _pick_cta(platform, after_t)
            cta_html = (
                f'<p style="text-align:center;margin-top:30px;padding:15px;'
                f'background:#f8f9fa;border-radius:8px;">'
                f'<strong>{cta_text}</strong></p>'
            )
            if "</body>" in new_html:
                new_html = new_html.replace("</body>", cta_html + "</body>")
            else:
                new_html += cta_html

        elif stype in ("intro", "readability", "keyword", "seo", "structure"):
            if before_t and before_t in new_html:
                new_html = new_html.replace(before_t, after_t, 1)

    return new_title, new_html


# ─────────────────────────────────────────────────────────────
# 플랫폼별 재발행
# ─────────────────────────────────────────────────────────────

def _extract_tistory_post_id(url: str) -> str:
    """티스토리 URL → post_id. 패턴: https://blog.tistory.com/123 또는 /entry/제목 (entry 는 ID 없음)."""
    if not url:
        return ""
    m = re.search(r'/(\d+)(?:[/?#]|$)', url)
    return m.group(1) if m else ""


def _extract_naver_log_no(url: str) -> str:
    """네이버 URL → logNo. 패턴: ?logNo=12345 또는 /blog_id/12345."""
    if not url:
        return ""
    m = re.search(r'logNo=(\d+)', url) or re.search(r'/(\d+)(?:[/?#]|$)', url)
    return m.group(1) if m else ""


def _revise_tistory(record: dict, new_title: str, new_html: str) -> bool:
    """티스토리 — 검증된 post_to_tistory() 흐름 그대로 재사용 (edit_post_id 매개변수)."""
    url = record.get("url", "")
    post_id = _extract_tistory_post_id(url)
    if not post_id:
        print(f"  ⚠️ 티스토리 post_id 추출 실패 (url={url[:80]})")
        return False

    try:
        from JARVIS08_PUBLISH.platforms import post_to_tistory

        # blocks 형태로 전달하면 분리 입력 (text 한 덩어리). 이미지 없는 단일 HTML 블록.
        # html_content 인수에 new_html 통째로 — _inject_html_block 으로 들어감.
        blocks = [("html", new_html)]
        ok = post_to_tistory(
            title=new_title,
            html_content=new_html,
            blocks=blocks,
            category="주식 - 테마분류",
            tags=None,
            edit_post_id=post_id,
        )
        if ok:
            print(f"  ✅ 티스토리 수정 완료 (post_id={post_id})")
        else:
            print(f"  ❌ 티스토리 수정 실패 (post_id={post_id})")
        return bool(ok)
    except Exception as e:
        print(f"  ❌ 티스토리 수정 오류: {e}")
        _g_report("writer", e, module=__name__)
        return False


def _revise_naver(record: dict, new_title: str, new_html: str) -> bool:
    """네이버 — 1회 한도 가드 + 검증된 post_to_naver() 흐름 재사용 (edit_log_no 매개변수)."""
    if record.get("is_revised", 0):
        print("  ⚠️ 네이버: 이미 수정됨 (1회 한도)")
        return False

    url = record.get("url", "")
    log_no = _extract_naver_log_no(url)
    if not log_no:
        print(f"  ⚠️ 네이버 logNo 추출 실패 (url={url[:80]})")
        return False

    try:
        from JARVIS08_PUBLISH.platforms import post_to_naver

        blocks = [("text", new_html)]  # 네이버는 텍스트 모드, html_to_naver_text 가 자동 변환
        ok = post_to_naver(
            title=new_title,
            html_content=new_html,
            blocks=blocks,
            category="주식 - 테마분류",
            tags=None,
            edit_log_no=log_no,
        )
        if ok:
            print(f"  ✅ 네이버 수정 완료 (logNo={log_no})")
        else:
            print(f"  ❌ 네이버 수정 실패 (logNo={log_no})")
        return bool(ok)
    except Exception as e:
        print(f"  ❌ 네이버 수정 오류: {e}")
        _g_report("writer", e, module=__name__)
        return False


# ─────────────────────────────────────────────────────────────
# 메인 처리
# ─────────────────────────────────────────────────────────────

PLATFORM_REVISERS = {
    "naver":    _revise_naver,
    "tistory":  _revise_tistory,
}


def process_one(record: dict) -> bool:
    """승인된 글 1개 재발행 — ★ 하네스 5-Layer 게이트 (수정→기록→누적→순환) 2026-05-18.

    Layer 1: 전제조건 없음 (platform 검사만 선행)
    Layer 2: ① 패치 적용 (apply_patch → new_title, new_html)
    Layer 3: 검증 (로그인·HTML 품질·빈 헤더) + 즉시 수정 훅 (빈 헤더 제거)
    Layer 4: 송출 (platform reviser → db.mark_revised → TG)
    max_attempts=3 — 송출 실패 시 검증 순환 재진입.
    """
    # ★ P1-③ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): harness ImportError fallback 제거.
    # 이전: harness 미가용 시 _process_one_legacy() 직접 실행 (검증 0회 우회).
    # 현재: ImportError 시 차단 + 텔레그램 알림 + False 반환.
    try:
        from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue as _Issue
    except ImportError as _ie:
        _g_report("writer", _ie, module=__name__)
        try:
            _tg(f"🚨 [revise_adapter] harness ImportError — 재발행 차단 (id={record.get('id')})\n사유: {_ie}")
        except Exception:
            pass
        return False

    aid      = record["id"]
    platform = record["platform"]
    theme    = record["theme"]
    title    = record.get("title") or theme
    url      = record.get("url", "")

    patch       = json.loads(record.get("revision_patch") or "{}")
    suggestions = patch.get("suggestions", [])
    orig_html   = record.get("original_html") or ""

    print(f"\n🔧 재발행 중: [{platform}] {title} (id={aid})")

    reviser = PLATFORM_REVISERS.get(platform)
    if not reviser:
        print(f"  ❌ 지원하지 않는 플랫폼: {platform}")
        return False

    # ── [L2] ① 패치 적용 단계 ─────────────────────────────────────
    @action_step(name="① 패치 적용")
    def _step_apply(state: dict):
        # ★ 결정론적 step — 한 번 패치(+fix)한 HTML은 재실행 시 재사용.
        # fix 훅이 state["new_html"]을 in-place 수정 후 sentinel 셋 → 재실행 시 no-op.
        if state.get("__patch_applied__"):
            return {}  # fix된 상태 유지 — 덮어쓰기 금지
        nt, nh = _apply_patch(orig_html, title, suggestions, platform)
        print(f"  제목: {title} → {nt}")
        state["__patch_applied__"] = True  # sentinel (merged에 포함됨)
        return {"new_title": nt, "new_html": nh}

    # ── [L3] 순수 검증 ─────────────────────────────────────────────
    def _verify(state: dict) -> list:
        """★ 순수 검증만 — 수정 로직은 _fix 훅이 담당."""
        issues = []
        # 로그인 상태 점검 (쿠키 만료 선제 방지)
        try:
            from JARVIS08_PUBLISH.credentials.login_manager import auto_refresh_if_needed
            auto_refresh_if_needed()
        except Exception as e:
            issues.append(_Issue(step="① 패치 적용", kind="login_check",
                                 detail=f"로그인 갱신 실패: {e}"))
        # HTML 품질 검증
        nh = state.get("new_html", "")
        nt = state.get("new_title", "")
        if not nt.strip():
            issues.append(_Issue(step="① 패치 적용", kind="draft_quality",
                                 detail="제목이 비어 있음"))
        if not nh or len(nh) < 100:
            issues.append(_Issue(step="① 패치 적용", kind="draft_quality",
                                 detail=f"HTML 본문이 너무 짧음 ({len(nh)}자)"))
        # 빈 헤더 검출
        empty_hdrs = re.findall(r'<(h[1-6])[^>]*>\s*</\1>', nh)
        for tag in empty_hdrs:
            issues.append(_Issue(step="① 패치 적용", kind="draft_quality",
                                 detail=f"빈 헤더 발견: <{tag}>"))
        return issues

    # ── [L3] 즉시 수정 훅 — 수정→기록→누적→순환 ──────────────────
    def _fix(state: dict, issues: list) -> tuple:
        """★ 즉시 수정 + harness가 GUARDIAN 학습 자동 박제."""
        fixed, unfixed = [], []
        nh = state.get("new_html", "")
        for iss in issues:
            if iss.kind == "draft_quality" and "빈 헤더" in iss.detail:
                new_nh = re.sub(r'<(h[1-6])[^>]*>\s*</\1>', '', nh)
                if new_nh != nh:
                    state["new_html"] = new_nh
                    nh = new_nh
                    fixed.append(iss)
                else:
                    unfixed.append(iss)
            else:
                unfixed.append(iss)
        return fixed, unfixed

    # ── [L4] 송출 — 외부 도달까지 포함 ───────────────────────────
    def _send(state: dict):
        """실패 시 raise → 검증 순환 재진입 (송출 후 실패 개념 없음)."""
        new_title = state["new_title"]
        new_html  = state["new_html"]
        ok = reviser(record, new_title, new_html)
        if not ok:
            raise RuntimeError(f"[{platform}] reviser returned False — 외부 발행 실패")
        db.mark_revised(aid)
        on_post_revised(aid, platform, theme, url)
        _tg(
            f"✅ *[{platform.upper()}] {theme}* 수정 완료!\n"
            f"📝 {new_title}\n🔗 {url}"
        )

    action_def = ActionDefinition(
        name=f"revise-{platform}-{aid}",
        steps=[_step_apply],
        verify=_verify,
        fix=_fix,    # ★ "수정→기록→누적→순환" 전체 에이전트 디폴트 (사용자 박제 2026-05-18)
        send=_send,
        max_attempts=3,
    )
    result = run_action(action_def)

    if not result.delivered:
        _tg(
            f"❌ *[{platform.upper()}] {theme}* 수정 실패 — 수동 확인 필요.\n"
            f"📝 {title}\n🔗 {url}\n"
            f"사유: {(result.escalation_reason or '하네스 검증 한도 초과')[:200]}"
        )
    return result.delivered


def _process_one_legacy(record: dict) -> bool:
    """harness 미가용 시 직접 실행 (backward-compat fallback)."""
    aid      = record["id"]
    platform = record["platform"]
    theme    = record["theme"]
    title    = record.get("title") or theme
    url      = record.get("url", "")
    patch       = json.loads(record.get("revision_patch") or "{}")
    suggestions = patch.get("suggestions", [])
    orig_html   = record.get("original_html") or ""
    print(f"\n🔧 재발행 중 (legacy): [{platform}] {title} (id={aid})")
    new_title, new_html = _apply_patch(orig_html, title, suggestions, platform)
    reviser = PLATFORM_REVISERS.get(platform)
    if not reviser:
        return False
    try:
        ok = reviser(record, new_title, new_html)
    except Exception as e:
        ok = False
        _g_report("writer", e, module=__name__)
    if ok:
        db.mark_revised(aid)
        on_post_revised(aid, platform, theme, url)
        _tg(f"✅ *[{platform.upper()}] {theme}* 수정 완료!\n📝 {new_title}\n🔗 {url}")
    else:
        _tg(f"❌ *[{platform.upper()}] {theme}* 수정 실패\n📝 {new_title}\n🔗 {url}")
    return ok


def run_approved():
    """승인 대기 중인 글 전체 처리."""
    records = db.get_approved_for_revision()
    if not records:
        print("재발행 대기 글 없음.")
        return 0
    cnt = 0
    for r in records:
        if process_one(r):
            cnt += 1
    return cnt


def run_watch(interval: int = 30):
    print(f"🔧 재발행 데몬 시작 (폴링 {interval}s)")
    while True:
        try:
            run_approved()
        except Exception as e:
            print(f"⚠️ 재발행 루프 오류: {e}")
            _g_report("writer", e, module=__name__)
        time.sleep(interval)


if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    args = sys.argv[1:]
    if "--watch" in args:
        run_watch(int(os.getenv("REVISE_POLL_SEC", "30")))
    elif "--all" in args:
        n = run_approved()
        print(f"\n✅ 재발행 완료: {n}개")
    else:
        # 특정 analysis_id
        aid = next((a for a in args if a.isdigit()), None)
        if aid:
            record = db.get_analysis_by_id(int(aid))
            if record and record.get("status") == "approved":
                process_one(record)
            else:
                print(f"ID {aid}: 승인 상태가 아니거나 없음 (status={record.get('status') if record else 'N/A'})")
        else:
            n = run_approved()
            print(f"\n✅ 재발행 완료: {n}개")

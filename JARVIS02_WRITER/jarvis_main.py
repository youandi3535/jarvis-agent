"""
jarvis_main.py v4
Market Signal | 메인 오케스트레이터
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 원고 1번만 생성 → 3개 플랫폼 순서대로 발행 (토큰 절약)
- 플랫폼별 성공/실패를 result_{theme}.json 으로 저장
- scheduler.py가 결과 파일을 읽어 실패 플랫폼만 재시도

사용법:
  python jarvis_main.py 반도체              # 전체 실행
  python jarvis_main.py 반도체 --naver-only # 네이버만 (캐시 원고 사용)
  python jarvis_main.py 반도체 --tistory-only # 티스토리만 (캐시 원고 사용)
"""

import os
import sys
import re
import json
import time
import subprocess
import requests
import glob
import shutil
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# ★ sys.path 보정 (직접 실행 vs 데몬 모듈 로드 양쪽 호환)
# scheduler.py 가 subprocess 로 'python jarvis_main.py 키워드 --scheduled' 호출 시
# 'JARVIS02_WRITER' 패키지가 sys.path 에 없어서 import 실패 → 반드시 *모든 JARVIS 절대 import 보다 먼저* 보정.
_JARVIS_ROOT = Path(__file__).parent.parent
if str(_JARVIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_JARVIS_ROOT))


# 실시간 출력 (VS Code 터미널용) — 두 가지 방법 모두 적용
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

os.environ.setdefault('PYTHONUNBUFFERED', '1')
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

load_dotenv()

# JARVIS 공유 모듈 (bus → 분석 루프 트리거) — sys.path 는 위에서 이미 보정됨
try:
    from shared.bus import on_post_published_detail as _emit_published
    _BUS_OK = True
except ImportError:
    _BUS_OK = False

# ── 현재 날짜 (LLM 프롬프트 주입용) ─────────────────────────────────────────
_TODAY_STR = datetime.now().strftime("%Y년 %m월 %d일")

# ── 글자수 관리: length_manager 단일 진입점. 한도·cap·경고는 거기만 수정 ──
try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L  # 같은 폴더 직접 실행 시

# ── SEO 기준: seo_standards 단일 진입점 ─────────────────────────────────────
try:
    from JARVIS02_WRITER.seo_standards import build_platform_seo_section
except ImportError:
    try:
        from seo_standards import build_platform_seo_section
    except ImportError:
        def build_platform_seo_section(active_pfxs, theme=""):  # noqa: E306
            return ""


# 발행 직후 품질 분석기 즉시 트리거
_ANALYZER_SCRIPT = _JARVIS_ROOT / "JARVIS03_RADAR" / "post_quality_analyzer.py"


BASE_DIR  = Path(__file__).parent
LOGS_DIR  = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def tg(msg: str):
    """텔레그램 알림 전송 (실패해도 무시)"""
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass



def make_section_title_image(title: str, save_path: str, level: int = 2, number: int = 0) -> bool:
    """소제목 배너 이미지 생성 — JARVIS06_IMAGE.section_title 위임."""
    from JARVIS06_IMAGE.section_title import make_section_title_image as _make
    return _make(title, save_path, level=level, number=number)



# ══════════════════════════════════════════
#  결과 파일 저장/로드
# ══════════════════════════════════════════

def get_result_path(theme: str) -> Path:
    safe = theme.replace("/", "_").replace(" ", "_")
    return LOGS_DIR / f"result_{safe}.json"



def enforce_text_between_images(blocks: list, source: str = "") -> list:
    """★ 글+이미지 규정 강제 (제4조) — 연속 content 이미지를 *재배치*로 분리.

    ★ 사용자 박제 2026-06-29 — "다 만들고 band-aid"가 아니라 *실제 교정*:
      연속되는 content 이미지를 빈 텍스트로 메우지 않고, 다음 본문 단락 뒤로 *옮겨서*
      텍스트가 이미지 사이에 오게 한다(이미지-텍스트-이미지). 옮길 본문이 끝까지 없을
      때만 최후수단으로 스페이서 + 경고(진짜 불가능한 경우만).

    소제목/배너/썸네일 이미지(heading_img)는 연속 판정에서 제외.
    ★ ERRORS [170]: divider/spacer 로는 연속을 회피할 수 없음 — 배치 플래그 유지.
    """
    def _is_heading_img(btype, bdata) -> bool:
        if btype in ('heading_h2', 'heading_h3', 'heading'):
            return True
        if btype == 'image':
            fname = str(bdata)
            return ('heading_' in fname or 'economic_h2_' in fname
                    or 'section_title' in fname or 'thumbnail_' in fname)
        return False

    def _is_text_gap(btype, bdata) -> bool:
        """이미지를 뒤에 붙일 수 있는 '본문' 블록 (텍스트가 이미지 앞에 오게)."""
        return btype not in ('image', 'divider', 'spacer',
                             'heading', 'heading_h2', 'heading_h3')

    result: list = []
    deferred: list = []          # 연속이라 재배치 대기 중인 content 이미지
    last_content_img = False      # 직전 emit 블록이 content 이미지인가

    def _emit(b) -> None:
        nonlocal last_content_img
        result.append(b)
        bt = b[0]
        if bt == 'image' and not _is_heading_img(bt, b[1]):
            last_content_img = True
        elif bt in ('divider', 'spacer'):
            pass  # 유지 — 스페이서는 연속 회피 못함 (ERRORS [170])
        else:
            last_content_img = False

    for btype, bdata in blocks:
        if btype == 'image' and not _is_heading_img(btype, bdata):
            if last_content_img:
                deferred.append((btype, bdata))   # 연속 → 다음 본문 뒤로 재배치
            else:
                _emit((btype, bdata))
        else:
            _emit((btype, bdata))
            # 본문 블록 뒤 → 대기 이미지 1개 재배치 (이제 앞에 텍스트가 있음)
            if deferred and _is_text_gap(btype, bdata):
                _emit(deferred.pop(0))

    # 남은 대기 이미지: 더 옮길 본문이 없음 → 최후수단(스페이서) + 진짜 위반 경고
    if deferred:
        msg = (f"⚠️ [글+이미지 규정 위반] {source} — 이미지 {len(deferred)}개 재배치 불가"
               f"(뒤따르는 본문 단락 부족). 스페이서로 분리.")
        print(msg)
        try:
            from shared.notify import send_tg
            send_tg(msg)
        except Exception:
            pass
        for img in deferred:
            if last_content_img:
                result.append(('text', '<p style="margin:4px 0;">&nbsp;</p>'))
            _emit(img)

    return result


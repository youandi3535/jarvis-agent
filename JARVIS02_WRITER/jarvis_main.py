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

# 이제 절대 import 가능
from JARVIS02_WRITER.collect_theme import generate_report

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

def _cap_content(text, max_korean=_L.MAX_KOREAN, context="theme"):
    """legacy alias → length_manager.compress."""
    return _L.compress(text, context=context, max_korean=max_korean)

def _kor_count(text):
    """legacy alias → length_manager.count."""
    return _L.count(text)

def _emit_length_check(theme: str, platform: str, report: str) -> None:
    """legacy alias → length_manager.warn_length."""
    _L.warn_length(theme, platform, report or "")

# 발행 직후 품질 분석기 즉시 트리거
_ANALYZER_SCRIPT = _JARVIS_ROOT / "JARVIS03_RADAR" / "post_quality_analyzer.py"

def _trigger_analysis(analysis_id: int):
    """발행 직후 분석기를 비동기로 즉시 실행."""
    if analysis_id and _ANALYZER_SCRIPT.exists():
        try:
            subprocess.Popen(
                [sys.executable, str(_ANALYZER_SCRIPT), str(analysis_id)],
                cwd=str(_ANALYZER_SCRIPT.parent),
            )
            print(f"  🔍 품질 분석 트리거: analysis_id={analysis_id}")
        except Exception as _e:
            print(f"  ⚠️ 분석 트리거 실패 (무시): {_e}")
            _g_report("writer", _e, module=__name__)

BASE_DIR  = Path(__file__).parent
LOGS_DIR  = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def tg(msg: str):
    """텔레그램 알림 전송 (실패해도 무시)"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def generate_intro_outro(theme: str) -> tuple[str, str]:
    """Claude Code SDK로 도입부·마무리 문단 생성 (length_manager.INTRO_THEME_TARGET 이내, 캐주얼 톤)."""
    from shared.llm import invoke_text as _inv_cli

    prompt = f"""주식 테마 '{theme}'에 대한 블로그 포스팅용 도입부와 마무리 문단을 작성해줘.

오늘 날짜: {_TODAY_STR} — 연도·날짜 언급 시 반드시 이 날짜 기준으로 작성. 다른 연도 사용 금지.

조건:
- 각각 {_L.build_length_phrase(_L.INTRO_THEME_TARGET_SENTS)} 이내
- 개인 투자자 시각으로 자연스럽고 캐주얼하게 (해요체)
- 매번 조금씩 다른 표현과 구성으로 (템플릿 느낌 X)
- 도입부: 이 테마에 관심 갖게 된 계기나 요즘 흐름 언급
- 마무리: 투자 시 참고할 점이나 개인 의견으로 마무리 (투자 권유 X)
- "구독", "공감", "좋아요" 언급 절대 금지

아래 형식으로만 답변해:
[도입부]
(내용)
[마무리]
(내용)"""

    try:
        from shared.personas import get as _persona
        _sys = _persona("jarvis02_writer")
        _full = f"{_sys}\n\n{prompt}".strip() if _sys else prompt
        text = (_inv_cli("writer", _full, timeout=120) or "").strip()
        intro = text.split("[마무리]")[0].replace("[도입부]", "").strip()
        outro = text.split("[마무리]")[1].strip() if "[마무리]" in text else ""
        print(f"  ✅ 도입부/마무리 생성 완료 (도입부 {len(intro)}자 / 마무리 {len(outro)}자)")
        return intro, outro
    except Exception as e:
        print(f"  ⚠️ 도입부/마무리 생성 실패: {e}")
        _g_report("writer", e, module=__name__)
        tg(f"❌ [{theme}] Claude API 도입부/마무리 생성 실패\n{str(e)[:150]}")
        return "", ""


def generate_blog_article(theme: str, report_html: str) -> str:
    """
    CrewAI 리포트를 source 로, length_manager 정책(한글 TARGET_LOW~MAX_KOREAN)
    범위 안에서 Claude API 가 처음부터 완성된 블로그 원고를 직접 생성.
    해요체, 단락 구분은 \\n\\n, HTML 태그 없는 순수 텍스트 반환.
    """
    from shared.llm import invoke_text as _inv_cli

    # HTML 태그 제거해서 핵심 텍스트 데이터 추출
    raw = re.sub(r'<[^>]+>', ' ', report_html)
    raw = re.sub(r'[^\n]*(?:현재가|시가총액|PER|ROE|영업이익률|순이익|매출액?)[^\n]*[\d,]+[^\n]*', ' ', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()
    source = raw[:5000]  # 핵심 데이터 5000자 (섹션 구성에 충분)

    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_block
    _supreme_b = _law_block()

    prompt = f"""{_supreme_b}
'{theme}' 테마 주식 블로그 원고를 작성해줘.

오늘 날짜: {_TODAY_STR} — 연도·날짜 언급 시 반드시 이 날짜 기준으로 작성. 다른 연도 사용 금지.

참고 데이터 (종목명·수치·이슈 등 이 내용을 바탕으로 작성):
{source}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_L.build_prompt_length_block()}

[필수 규칙] (헌법 제3조 이하 전체 적용)
1. 모든 본문: 친근한 해요체(~해요, ~이에요, ~있어요)
2. 투자 주의사항 단락만 ~했습니다/~입니다 형태로 마무리
3. 단락 구분: 빈 줄 하나(\\n\\n), HTML 태그 없음
4. 한 단락 최대 2문장
5. 구성 순서 (★ 반드시 위 [분량 규정] 합계 {_L.build_length_phrase(_L.TARGET_SENTENCES)} 안에서 배분):
   ① 테마 소개 및 최근 흐름 — 정확히 {_L.build_length_phrase(_L.INTRO_SENTS_MIN, _L.INTRO_SENTS_MAX)}
   ② 주요 종목 통합 분석 — 정확히 섹션당 {_L.build_length_phrase(_L.SEC_SENTS_MIN, _L.SEC_SENTS_MAX)} (대장주 중심, 전체 종목을 2개 단락으로 묶어 작성. 종목별 단락 분리 금지)
   ③ 투자 시 참고사항 — 정확히 섹션당 {_L.build_length_phrase(_L.SEC_SENTS_MIN, _L.SEC_SENTS_MAX)}
   ④ 투자 주의사항 (~했습니다 체) — 정확히 {_L.build_length_phrase(_L.OUTRO_SENTS)}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
텍스트만 출력 (제목, 번호, 부가 설명 없이)."""

    try:
        from shared.personas import get as _persona
        _sys = _persona("jarvis02_writer")
        _full = f"{_sys}\n\n{prompt}".strip() if _sys else prompt
        article = (_inv_cli("writer", _full, timeout=300) or "").strip()
        kor = _L.count(article)
        print(f"  ✅ 블로그 원고 생성 완료: 한글 {kor:,}자")
        return article
    except Exception as e:
        print(f"  ⚠️ 블로그 원고 생성 실패: {e}")
        _g_report("writer", e, module=__name__)
        tg(f"❌ [{theme}] Claude API 블로그 원고 생성 실패 → 발행 불가\n{str(e)[:150]}")
        return ""


def generate_triple_articles(theme: str, report_html: str, text_slot_headings: list = None,
                             platforms: list = None) -> dict:
    """
    요청된 플랫폼 원고를 소제목 섹션별로 1회 API 호출로 생성.
    platforms: ['naver', 'tistory'] 중 요청된 것만 전달. None이면 전체 2개.
    text_slot_headings: 각 텍스트 슬롯 위의 소제목 목록 (prepare_images에서 추출)
    Returns: {
        "naver":   {...},
        "tistory": {...}
    }
    """
    from shared.llm import invoke_text as _inv_cli

    # 요청 플랫폼 정규화
    _ALL_PFX = [("NAVER", "네이버"), ("TISTORY", "티스토리")]
    if platforms is None:
        platforms = ['naver', 'tistory']
    active_pfxs = [(pfx, name) for pfx, name in _ALL_PFX if pfx.lower() in platforms]

    raw = re.sub(r'<[^>]+>', ' ', report_html)
    raw = re.sub(r'\s+', ' ', raw).strip()
    source = raw[:6000]

    headings = text_slot_headings or []
    n = len(headings) if headings else 4  # 헤딩 없으면 4섹션 기본값

    # 섹션당 문장 수 — 총 TARGET_SENTENCES 를 n섹션에 균등 배분 (length_block과 일치 보장)
    _STOCK_KW = re.compile(r'대장주|부대장주|주요 종목|대표 종목|핵심 종목|관련 종목|종목별|수혜주|대표주')

    # body 문장수를 n으로 나눠 각 섹션별 문장수 배열 생성 (합계 항상 body)
    _body_sents = _L.TARGET_SENTENCES - _L.INTRO_SENTS_MIN - _L.OUTRO_SENTS  # 24 (최소값 기준)
    _base_s     = max(2, _body_sents // n)
    _rem_s      = _body_sents - _base_s * n   # 나머지 → 앞 섹션에 1씩 배분
    _sec_counts = [_base_s + (1 if i < _rem_s else 0) for i in range(n)]

    def _sents_for_sec(i: int, h: str = '') -> int:
        """섹션 i의 문장수: TARGET_SENTENCES 를 n섹션에 균등 배분한 값. 합계=TARGET_SENTENCES 보장."""
        return _sec_counts[i] if i < len(_sec_counts) else _base_s

    if headings:
        def _slot_desc(i, h):
            sents = _sents_for_sec(i, h)
            if not h:
                return f'  S{i+1}: (일반) — 정확히 {sents}문장. 각 문장: 핵심 1개, 마침표 완결.'
            if h.startswith('[차트/표 해석]'):
                topic = h.replace('[차트/표 해석]', '').strip()
                return f'  S{i+1}: [차트해석] 《{topic}》 — 정확히 {sents}문장. 수치·순위 1개 이상 포함. 마침표 완결.'
            else:
                return f'  S{i+1}: 《{h}》 — 정확히 {sents}문장. 핵심만. 마침표 완결.'

        headings_desc = '\n'.join(_slot_desc(i, h) for i, h in enumerate(headings))
        total_sents = sum(_sents_for_sec(i, h) for i, h in enumerate(headings))
        _tot = _L.INTRO_SENTS_MIN + total_sents + _L.OUTRO_SENTS
        section_rule = (
            f'[섹션 구조]\n'
            f'규칙: 각 섹션은 지정된 문장 수만큼만 작성. 더 많이 쓰지 말 것. 각 문장은 반드시 마침표(.)로 끝낼 것.\n'
            f'총 문장 수: 도입부 {_L.INTRO_SENTS}문장 + 섹션 {total_sents}문장 + 마무리 {_L.OUTRO_SENTS}문장 = {_tot}문장\n\n'
            f'{headings_desc}\n\n'
            f'[섹션 내용 기준]\n'
            f'- 《소제목》 주제만 다룰 것. 다른 섹션 내용 혼재 금지.\n'
            f'- [차트해석]: 참고 데이터에서 수치·순위 구체 언급.\n'
            f'- 종목 섹션: 해당 종목만. 기술·경쟁력·최근 이슈 중심. 재무 수치(매출·PER·ROE 등) 언급 금지.\n'
        )
    else:
        # 섹션 수를 모를 때: 4섹션 기본값 (headings가 없을 때는 n=4로 설정됨)
        _s = _sec_counts[0] if _sec_counts else 6  # 기본값 = (30-3-2)/4 = 6.25 → 6
        _tot_fb = _L.INTRO_SENTS_MIN + sum(_sec_counts[:4]) + _L.OUTRO_SENTS
        section_rule = (
            f'[섹션 구조]\n'
            f'규칙: 각 섹션은 지정된 문장 수만큼만 작성. 각 문장은 반드시 마침표(.)로 끝낼 것.\n'
            f'총 문장 수: 도입부 {_L.INTRO_SENTS}문장 + 섹션 {_tot_fb - _L.INTRO_SENTS_MIN - _L.OUTRO_SENTS}문장 + 마무리 {_L.OUTRO_SENTS}문장 = {_tot_fb}문장\n\n'
            f'  S1: 테마 소개 및 최근 흐름 — 정확히 {_sec_counts[0] if len(_sec_counts)>0 else _s}문장. 마침표 완결.\n'
            f'  S2: 주요 종목별 분석 — 정확히 {_sec_counts[1] if len(_sec_counts)>1 else _s}문장. 마침표 완결.\n'
            f'  S3: 투자 시 참고사항 — 정확히 {_sec_counts[2] if len(_sec_counts)>2 else _s}문장. 마침표 완결.\n'
            f'  S4: 투자 주의사항 (~습니다 체) — 정확히 {_sec_counts[3] if len(_sec_counts)>3 else _s}문장. 마침표 완결.\n'
        )

    def _sfmt(prefix):
        return '\n'.join(f'==={prefix}_S{i+1}===\n(내용)' for i in range(n))

    # 요청 플랫폼별 스타일 규칙
    _STYLES = {
        "NAVER":   "일반 투자자 대상. 친근한 해요체(~해요/~이에요). 쉬운 비유·설명.",
        "TISTORY": "트렌드 민감 독자. 간결하고 세련된 문체. 핵심 인사이트 중심.",
    }
    _INTRO_RULES = {
        "NAVER":   f"개인 투자자가 관심 갖게 된 계기나 최근 이슈 (해요체, {_L.build_length_phrase(_L.INTRO_THEME_TARGET_SENTS)} 전후)",
        "TISTORY": f"첫 문장에 '{theme}' 또는 '{theme} 관련주' 키워드를 자연스럽게 포함. 트렌드 관점의 간결하고 세련된 문체(~습니다), {_L.build_length_phrase(_L.INTRO_THEME_TARGET_SENTS)} 전후",
    }
    # ★ outro 끝에 *플랫폼 톤에 맞는* 면책 1문장 자유 작성 — 하드코딩 금지 (CLAUDE.md)
    # 의미: "본 글은 정보 제공·참고용이며 투자 권유 아니다. 모든 판단/책임은 본인."
    # 표현: 매번 다르게. 어제·다른 플랫폼 글과 동일 표현 금지.
    _DISC_PHRASING = (
        f"마지막 문장에 *반드시* 의미상 다음을 포함하는 면책 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} 자유롭게 작성: "
        "본 글은 정보 제공·참고 목적이며 투자 권유가 아니다. "
        "최종 판단·손익은 투자자 본인 책임. "
        "★ 매번 다른 표현 사용 — 같은 단어 조합 금지. "
        "★ 다른 플랫폼·어제 글과 동일 표현 금지."
    )
    _OUTRO_RULES = {
        "NAVER":   f"해요체(~해요). 응원과 공감이 담긴 {_L.build_length_phrase(_L.OUTRO_TARGET_SENTS)} 감성글. {_DISC_PHRASING} 면책 문장은 해요체 톤 유지.",
        "TISTORY": f"간결하고 세련된 문체. 핵심 인사이트를 담은 {_L.build_length_phrase(_L.OUTRO_TARGET_SENTS)} 감성글. {_DISC_PHRASING} 면책 문장은 본문과 동일 톤 유지.",
    }

    style_lines     = '\n'.join(f'- {name}: {_STYLES[pfx]}'       for pfx, name in active_pfxs)
    intro_rules     = '\n'.join(f'- {name} 도입부: {_INTRO_RULES[pfx]}' for pfx, name in active_pfxs)
    outro_rules     = '\n'.join(f'- {name} 마무리: {_OUTRO_RULES[pfx]}' for pfx, name in active_pfxs)

    # 출력 형식: 요청 플랫폼만
    def _end_tag_for(i):
        if i + 1 < len(active_pfxs):
            return f"==={active_pfxs[i+1][0]}_TITLE==="
        return "===END==="

    output_fmt_parts = []
    for pfx, name in active_pfxs:
        output_fmt_parts.append(
            f"==={pfx}_TITLE===\n({name} 제목)\n"
            f"==={pfx}_INTRO===\n({name} 도입부)\n"
            f"==={pfx}_OUTRO===\n({name} 마무리 감성글 {_L.build_length_phrase(_L.OUTRO_TARGET_SENTS)})\n"
            f"{_sfmt(pfx)}"
        )
    output_fmt = '\n'.join(output_fmt_parts) + '\n===END==='

    n_platforms = len(active_pfxs)
    title_rule = (f"- {n_platforms}개 제목의 뒷부분(부제)은 서로 달라야 함 (중복 불가)\n" if n_platforms > 1
                  else "")

    # SEO 심화 지침 (seo_standards 단일 진실 소스) — f-string 밖에서 미리 조합
    _seo_raw   = build_platform_seo_section(active_pfxs, theme)
    _seo_block = ("[플랫폼별 SEO 심화 지침]\n" + _seo_raw + "\n\n") if _seo_raw else ""

    # 글쓰기 최상위 헌법 — 매번 파일에서 읽어 주입 (캐시 없음)
    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_block
    _supreme = _law_block()

    prompt = (
        f"{_supreme}\n"
        f"'{theme}' 테마 주식에 대해 {n_platforms}개 블로그 플랫폼용 원고를 섹션별로 작성해줘.\n"
        f"같은 데이터를 기반으로, 플랫폼마다 제목·도입부·섹션 내용·마무리를 완전히 다르게 표현해야 해.\n\n"
        f"오늘 날짜: {_TODAY_STR} — 연도·날짜 언급 시 반드시 이 날짜 기준으로 작성. 다른 연도 사용 금지.\n\n"
        f"참고 데이터:\n{source}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{section_rule}\n\n"
        f"{_L.build_prompt_length_block(n_sections=n)}\n"
        f"- 위 한도는 *각 플랫폼 본문(intro + 모든 sections + outro 합산)* 기준.\n\n"
        f"[★★★ SEO 4원칙 — 모든 플랫폼 적용 ★★★]\n"
        f"① 제목 첫 단어 = '{theme}' (이미 [제목 규칙]에 명시)\n"
        f"② 도입부(intro) 첫 {_L.build_length_phrase(_L.INTRO_KEYWORD_WINDOW_SENTS)} 안에 '{theme}' 또는 그 핵심 단어 자연스럽게 등장\n"
        f"③ 본문 분포: 도입부·중간 섹션·마무리(outro) 모두에 '{theme}' 또는 핵심 단어 1회 이상 등장\n"
        f"④ 키워드 밀도: 본문 500자당 '{theme}' 또는 핵심 단어 1회 정도. 스터핑(3문장 안 2번 이상 반복) 금지.\n\n"
        f"{_seo_block}"
        f"[플랫폼별 스타일]\n"
        f"{style_lines}\n\n"
        f"[절대 금지 — 이 규칙은 모든 플랫폼·모든 섹션에 적용] (헌법 제13조 2항 적용)\n"
        f"- 현재가, 시가총액, PER, ROE, 영업이익률, 매출액, 순이익, 부채비율 등 재무 수치 및 재무 관련 설명 원고에 절대 기재 금지\n"
        f"- 종목 섹션에서 재무 지표(수익성·성장률·밸류에이션 등) 언급 금지. 오직 사업 특징·기술·경쟁력·시장 포지션·최근 이슈만 작성\n"
        f"- 수치가 필요한 경우 '흑자 기조', '고성장', '안정적 수익성' 등 정성적 표현으로 대체\n"
        f"  (재무 표와 차트가 이미 이미지로 제공되므로 원고에서 반복 불필요)\n\n"
        f"[완결 규칙] (헌법 제3조·제4조 적용)\n"
        f"- 모든 문장은 반드시 마침표(.)로 끝낼 것. 문장 중간에 끝나는 것은 무효.\n"
        f"- 도입부: 정확히 {_L.INTRO_SENTS}문장. 마무리: 정확히 {_L.OUTRO_SENTS}문장.\n"
        f"- 섹션별 지정 문장 수 엄수. 한 문장에 핵심 1개만 담을 것.\n"
        f"- 각 섹션 내용을 반드시 2개 이상의 단락으로 분리 (이미지 삽입 공간 확보)\n\n"
        f"[도입부/마무리 규칙]\n"
        f"{intro_rules}\n"
        f"{outro_rules}\n\n"
        f"[공통 규칙] (헌법 제7조 적용)\n"
        f'- 수익률 관련 섹션: 반드시 "3개월 수익률"로만 표현 (1개월·6개월 언급 절대 금지 — 차트에 없는 데이터임)\n\n'
        f"[제목 규칙]\n"
        f"- '{theme}' 키워드는 모든 제목 맨 앞에 반드시 포함\n"
        f"{title_rule}"
        f"- 각 {_L.TITLE_THEME_MAX}자 이내, 날짜 포함 금지\n"
        f"- 아래 공식 중 하나를 반드시 사용 (각 플랫폼은 서로 다른 공식 사용):\n"
        f"  · 숫자형: '{theme} 관련주 TOP 5, 지금 담아도 될까?'\n"
        f"  · 질문형: '{theme} 주식 지금 사도 될까? 투자 전 핵심 체크'\n"
        f"  · 비교형: '{theme} 대장주 수익률 비교 — 어디가 강할까'\n"
        f"  · 시의성: '{theme} 지금이 기회? 핵심 종목 총정리'\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"아래 형식으로만 출력해 (마무리를 섹션보다 먼저 작성):\n"
        f"{output_fmt}"
    )

    # 후처리 공통 패턴 (루프 밖에 정의)
    _yt_pat    = re.compile(r'.*?(구독|공감은|좋아요).*?\n?', re.MULTILINE)
    _cr_pat    = re.compile(r'.*?(©|&copy;|All rights reserved|마켓시그널).*?\n?', re.MULTILINE | re.IGNORECASE)
    _fin_sent  = re.compile(r'[^.!?\n]*(?:현재가|시가총액|PER|ROE|영업이익률|순이익|매출액?)[^.!?\n]*[\d,]+[^.!?\n]*[.!?]?')
    _img_ref   = re.compile(r'(위|아래)\s*(표|그래프|차트|이미지|그림)(는|은|에서|를|을|에|의|를\s*보면|에서\s*보듯)[^.!?\n]*[.!?]?', re.IGNORECASE)
    _delim_pat = re.compile(r'===\w+===', re.IGNORECASE)
    # 면책 누락 검증 패턴 (참고|권유|책임 중 2개 이상이면 OK)
    _DISC_KEYS = re.compile(r'참고|권유|책임|판단.*본인|본인.*책임|매수.*매도')
    def _to_sumnida(text: str) -> str:
        text = re.sub(r'만든다(?=[.\s]|$)', '만듭니다', text)
        text = re.sub(r'([가-힣])한다(?=[.\s]|$)', r'\1합니다', text)
        text = re.sub(r'([가-힣])된다(?=[.\s]|$)', r'\1됩니다', text)
        text = re.sub(r'([가-힣])있다(?=[.\s]|$)', r'\1있습니다', text)
        text = re.sub(r'([가-힣])없다(?=[.\s]|$)', r'\1없습니다', text)
        text = re.sub(r'([가-힣])크다(?=[.\s]|$)', r'\1큽니다', text)
        text = re.sub(r'([가-힣])다(?=\s|$)', r'\1습니다', text)
        return text

    def _between(text, start, end):
        if start not in text:
            return ""
        part = text.split(start)[1]
        if end in part:
            part = part.split(end)[0]
        return part.strip()

    def _parse_sections(text, prefix):
        sections = []
        for i in range(n):
            s_tag = f'==={prefix}_S{i+1}==='
            e_tag = f'==={prefix}_S{i+2}===' if i < n - 1 else '===END==='
            sections.append(_between(text, s_tag, e_tag))
        return sections

    def _fix_incomplete_sentence(text: str) -> str:
        """마침표/느낌표/물음표로 끝나지 않는 불완전 문장 제거 (절단 안전망)"""
        text = text.strip()
        if not text:
            return text
        # 이미 완전한 문장으로 끝나면 OK
        if re.search(r'[.!?。！？]\s*$', text):
            return text
        # 마지막 완결 문장 뒤 불완전 부분 제거
        last_end = max(
            text.rfind('.'), text.rfind('!'), text.rfind('?'),
            text.rfind('。'), text.rfind('！'), text.rfind('？')
        )
        if last_end > len(text) * 0.3:  # 텍스트의 30% 이상 지점에 마침표가 있으면 거기서 끊기
            truncated = text[:last_end + 1].strip()
            if truncated:
                print(f"    ⚠️ 불완전 문장 감지 → 마지막 완결 문장까지만 유지 ({len(text)}→{len(truncated)}자)")
                return truncated
        return text

    def _postprocess(p: str, art: dict) -> dict:
        for field in ("intro", "outro"):
            art[field] = _yt_pat.sub("", art[field]).strip()
            art[field] = _cr_pat.sub("", art[field]).strip()
            art[field] = _fix_incomplete_sentence(art[field])
            if p == "tistory":
                art[field] = _to_sumnida(art[field])
        if not art["intro"]:
            from shared.llm import invoke_text as _llm_invoke
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
            _tone = {"naver": "친근한 해요체", "tistory": "합니다체"}.get(p, "합니다체")
            art["intro"] = _llm_invoke(
                "writer_fast",
                f"{supreme_block}\n\n'{theme}' 테마 주식 블로그 도입부 정확히 {_L.INTRO_SENTS}문장 작성. 어조: {_tone}. 이모지 없이. 바로 본문만 출력.",
                max_tokens=150, temperature=0.7
            ) or f"{theme} 테마의 주요 흐름을 살펴봅니다."
            print(f"  ⚠️ {p}: intro 비어있음 → LLM 동적 생성")
        if not art["outro"]:
            from shared.llm import invoke_text as _llm_invoke
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
            _tone = {"naver": "친근한 해요체", "tistory": "합니다체"}.get(p, "합니다체")
            art["outro"] = _llm_invoke(
                "writer_fast",
                f"{supreme_block}\n\n'{theme}' 테마 주식 블로그 마무리 정확히 {_L.build_length_phrase(_L.OUTRO_SENTS)} 작성. 어조: {_tone}. 마지막에 '정보 제공 목적·투자 권유 아님·판단은 본인 책임' 취지 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} 포함. 이모지 없이. 바로 본문만 출력.",
                max_tokens=150, temperature=0.7
            ) or f"{theme} 관련 정보가 투자 판단에 도움이 되길 바랍니다. 본 글은 정보 제공 목적이며 투자 권유가 아닙니다."
            print(f"  ⚠️ {p}: outro 비어있음 → LLM 동적 생성")
        cleaned = []
        for s in art["sections"]:
            s = _cr_pat.sub("", _yt_pat.sub("", s)).strip()
            s = _fin_sent.sub("", s)
            s = _img_ref.sub("", s)
            s = _delim_pat.sub("", s)
            s = re.sub(r'\n{3,}', '\n\n', s).strip()
            s = _fix_incomplete_sentence(s)  # 불완전 문장 안전망
            if p == "tistory":
                s = _to_sumnida(s)
            cleaned.append(s)
        art["sections"] = cleaned
        for field in ("intro", "outro"):
            art[field] = _delim_pat.sub("", art[field]).strip()
        kor = _L.sum_korean(*art["sections"])
        kor_total = _L.sum_korean(art["intro"], *art["sections"], art["outro"])

        # ── 문장수 검증 (누수 3 수정) ────────────────────────────
        full_text = " ".join([art["intro"]] + art["sections"] + [art["outro"]])
        actual_sents = _L.count_sentences(full_text)
        _T = _L.TARGET_SENTENCES
        _MIN = _L.MIN_SENTENCES_THRESHOLD       # 20
        _MAX = _T + 10                           # 40
        if actual_sents < _MIN:
            print(f"  ⚠️ [{p}] 문장수 부족 — {actual_sents}문장 (목표 {_T}, 최소 {_MIN})")
            try:
                from shared.notify import send_tg as _stg
                _stg(f"⚠️ [{theme}/{p}] 원고 분량 부족: {actual_sents}문장 (목표 {_T}문장)")
            except Exception:
                pass
        elif actual_sents > _MAX:
            print(f"  ⚠️ [{p}] 문장수 초과 — {actual_sents}문장 (목표 {_T}, 최대 {_MAX})")
            try:
                from shared.notify import send_tg as _stg
                _stg(f"⚠️ [{theme}/{p}] 원고 분량 초과: {actual_sents}문장 (목표 {_T}문장)")
            except Exception:
                pass
        else:
            print(f"  ✅ [{p}] 문장수 정상 — {actual_sents}문장 (목표 {_T}문장)")
        # ──────────────────────────────────────────────────────────

        print(f"  ✅ {p}: 제목={art['title']} / 섹션 {len(art['sections'])}개 / 한글 {kor:,}자 / intro {len(art['intro'])}자 / outro {len(art['outro'])}자")
        return art

    # 플랫폼별 독립 API 호출 — 각 5,000 토큰 균등 배정
    _empty = {"title": f"{theme} 테마 분석", "intro": "", "sections": [], "outro": ""}
    result = {p: dict(_empty) for p in ['naver', 'tistory']}

    # 브랜드 보이스 학습 — 과거 유사 글에서 톤·스타일 발췌 (system prompt 주입용)
    voice_block = ""
    try:
        from shared.style import build_few_shot_block  # ★ Phase 2 통합 (2026-05-18)
        voice_block = build_few_shot_block(theme, k=2, max_chars=600)
        if voice_block:
            print(f"  🎙️  브랜드 보이스: 유사 글 {voice_block.count('[샘플')}편 주입")
    except Exception as e:
        print(f"  ⚠️ 브랜드 보이스 검색 실패(무시하고 진행): {e}")
        _g_report("writer", e, module=__name__)

    # ── 누적 학습 지침 — ★ ADR 014 (2026-07-03): JARVIS07 quality_learner 단일 진입점.
    #    UCB 랭킹 선택 + 사용 기록(보상 귀속 대기) → 검증된 지침만 살아남는 강화학습.
    _learn_block = ""
    try:
        from JARVIS07_GUARDIAN.quality_learner import build_insights_block as _ql_block
        _learn_block = _ql_block(scope="theme", theme=str(theme or ""))
        if _learn_block:
            print("  📚 테마글 학습 지침 주입됨 (강화학습 랭킹)")
    except Exception as _le:
        print(f"  ⚠️ 학습 블록 로드 실패(무시): {_le}")
        _g_report("writer", _le, module=__name__)

    def _generate_platform_article(pfx, name):
        """플랫폼별 원고 생성 (ThreadPoolExecutor 용)"""
        p = pfx.lower()
        # 이 플랫폼 전용 출력 형식
        single_fmt = (
            f"==={pfx}_TITLE===\n({name} 제목)\n"
            f"==={pfx}_INTRO===\n({name} 도입부)\n"
            f"==={pfx}_OUTRO===\n({name} 마무리 감성글 {_L.build_length_phrase(_L.OUTRO_TARGET_SENTS)})\n"
            + '\n'.join(f'==={pfx}_S{i+1}===\n(내용)' for i in range(n))
            + '\n===END==='
        )
        single_prompt = (
            f"{_supreme}\n"
            f"'{theme}' 테마 주식에 대해 {name} 블로그용 원고를 섹션별로 작성해줘.\n"
            f"참고 데이터:\n{source}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{section_rule}\n\n"
            + (f"{_learn_block}\n\n" if _learn_block else "")
            + f"[스타일] {name}: {_STYLES[pfx]}\n\n"
            f"[절대 금지] (헌법 제7조·제13조 2항 적용)\n"
            f"- 현재가, 시가총액, PER, ROE, 영업이익률, 매출액, 순이익, 부채비율 등 재무 수치 및 재무 관련 설명 원고에 절대 기재 금지\n"
            f"- 종목 섹션에서 재무 지표 언급 금지. 오직 사업 특징·기술·경쟁력·시장 포지션·최근 이슈만 작성\n"
            f"- 수치가 필요한 경우 '흑자 기조', '고성장', '안정적 수익성' 등 정성적 표현으로 대체\n"
            f'- 수익률 관련 섹션: 반드시 "3개월 수익률"로만 표현 (1개월·6개월 언급 절대 금지)\n\n'
            f"[완결 규칙]\n"
            f"- 모든 문장은 반드시 마침표(.)로 끝낼 것. 문장 중간에 끝나는 것은 무효.\n"
            f"- 도입부: 정확히 {_L.INTRO_SENTS}문장. 마무리: 정확히 {_L.OUTRO_SENTS}문장.\n"
            f"- 섹션별 지정 문장 수 엄수. 한 문장에 핵심 1개만 담을 것.\n"
            f"- 각 섹션 내용을 반드시 2개 이상의 단락으로 분리 (이미지 삽입 공간 확보, 헌법 제4조 적용)\n\n"
            f"[도입부/마무리 규칙]\n"
            f"- 도입부: {_INTRO_RULES[pfx]}\n"
            f"- 마무리: {_OUTRO_RULES[pfx]}\n\n"
            f"[제목 규칙]\n"
            f"- '{theme}' 키워드 맨 앞에 반드시 포함. {_L.TITLE_MAX}자 이내, 날짜 포함 금지.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"아래 형식으로만 출력해 (마무리를 섹션보다 먼저 작성):\n"
            f"{single_fmt}"
        )
        try:
            from shared.personas import with_system as _with_persona
            _sys_block = _with_persona("jarvis02_writer", voice_block)
            _full_prompt = f"{_sys_block}\n\n{single_prompt}".strip() if _sys_block else single_prompt
            raw_text = (_inv_cli("writer", _full_prompt, timeout=300) or "").strip()
            if "===END===" not in raw_text:
                print(f"  ⚠️ {name}: 응답에 ===END=== 없음 — 보완 추가")
                raw_text += "\n===END==="
            art = {
                "title":    _between(raw_text, f"==={pfx}_TITLE===", f"==={pfx}_INTRO==="),
                "intro":    _between(raw_text, f"==={pfx}_INTRO===", f"==={pfx}_OUTRO==="),
                "outro":    _between(raw_text, f"==={pfx}_OUTRO===", f"==={pfx}_S1==="),
                "sections": _parse_sections(raw_text, pfx),
            }
            if not art["title"]:
                art["title"] = f"{theme} 테마 분석"
            return (p, _postprocess(p, art))
        except Exception as e:
            print(f"  ⚠️ {name} 원고 생성 실패: {e}")
            _g_report("writer", e, module=__name__)
            tg(f"❌ [{theme}] {name} 원고 생성 실패 (해당 플랫폼 빈 원고)\n{str(e)[:150]}")
            return (p, dict(_empty))

    # 플랫폼별 순차 생성
    # Naver·Tistory 대본 (텍스트) 생성은 모두 Claude Code SDK 통과 — 동시 호출 시 throttling.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {executor.submit(_generate_platform_article, pfx, name): p
                   for pfx, name in active_pfxs for p in [pfx.lower()]}
        for future in as_completed(futures):
            p, art = future.result()
            result[p] = art

    return result


def _make_platform_blocks(base_blocks: list, sections: list) -> list:
    """
    이미지/divider 블록 구조 유지, text 블록을 sections 리스트로 1:1 교체.
    sections[i] → text_indices[i] 직접 매핑 (소제목별 내용 정합 보장).
    """
    if not sections:
        return base_blocks

    text_indices = [i for i, (t, _) in enumerate(base_blocks) if t == 'text']

    if not text_indices:
        return base_blocks

    result = list(base_blocks)
    for idx, pos in enumerate(text_indices):
        if idx < len(sections) and sections[idx]:
            result[pos] = ('text', sections[idx])
        # idx >= len(sections) 이면 원본 텍스트 유지
    return result


def make_section_title_image(title: str, save_path: str, level: int = 2, number: int = 0) -> bool:
    """소제목 배너 이미지 생성 — JARVIS06_IMAGE.section_title 위임."""
    from JARVIS06_IMAGE.section_title import make_section_title_image as _make
    return _make(title, save_path, level=level, number=number)


def inject_intro_outro(report: str, intro: str, outro: str) -> str:
    """HTML 리포트에 도입부(body 직후)·마무리(body 직전) 삽입"""
    if not intro and not outro:
        return report

    intro_html = (
        f'<div style="background:#f8f9fa;border-left:4px solid #4CAF50;'
        f'padding:16px 20px;margin:0 0 28px;border-radius:0 8px 8px 0;'
        f'font-size:15px;line-height:1.8;color:#333;">{intro}</div>'
    ) if intro else ""

    outro_html = (
        f'<div style="background:#f8f9fa;border-left:4px solid #2196F3;'
        f'padding:16px 20px;margin:28px 0 0;border-radius:0 8px 8px 0;'
        f'font-size:15px;line-height:1.8;color:#333;">{outro}</div>'
    ) if outro else ""

    if "<body>" in report:
        report = report.replace("<body>", f"<body>{intro_html}", 1)
    else:
        report = intro_html + report

    if "</body>" in report:
        report = report.replace("</body>", f"{outro_html}</body>", 1)
    else:
        report = report + outro_html

    return report


# ══════════════════════════════════════════
#  결과 파일 저장/로드
# ══════════════════════════════════════════

def get_result_path(theme: str) -> Path:
    safe = theme.replace("/", "_").replace(" ", "_")
    return LOGS_DIR / f"result_{safe}.json"


def save_result(theme: str, results: dict):
    path = get_result_path(theme)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  💾 결과 저장: {path.name} → {results}")


def load_result(theme: str) -> dict:
    path = get_result_path(theme)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {"naver": False, "tistory": False}


def _inject_para_images_into_blocks(blocks: list, theme: str,
                                     platform: str = 'naver') -> list:
    """text 블록 내 2문장 초과 시 단락 분리 + 단락 사이 AI 이미지 삽입.

    규칙 (CLAUDE.md 모든 블로그글 동일 기준):
      - 각 text 블록을 2문장 단위 단락으로 분리
      - 단락 수 ≥ 2 인 블록: 단락 사이(마지막 제외)마다 AI 이미지 삽입
      - 단락 수 = 1 인 블록: 이미 소제목이미지로 앞뒤가 감싸짐 → 내부 이미지 없음
    """
    import urllib.request
    import urllib.parse
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 이미지 저장 폴더 — JARVIS06_IMAGE/output/images/theme_{platform}/ (사용자 박제 2026-05-14)
    _plat_dirs = {
        'naver':   BASE_DIR.parent / "JARVIS06_IMAGE" / "output" / "images" / "theme_naver",
        'tistory': BASE_DIR.parent / "JARVIS06_IMAGE" / "output" / "images" / "theme_tistory",
    }
    img_dir = _plat_dirs.get(platform, _plat_dirs['naver'])
    img_dir.mkdir(parents=True, exist_ok=True)

    # 한국어 → 영어 prompt 간단 변환 (Pollinations용)
    _KO_EN = {
        '주식': 'stock market', '투자': 'investment', '경제': 'economy',
        '테마': 'theme', '성장': 'growth', '기업': 'company',
        '반도체': 'semiconductor', '배터리': 'battery', 'AI': 'artificial intelligence',
        '에너지': 'energy', '바이오': 'biotech', '금융': 'finance',
        '수출': 'export', '환율': 'exchange rate', '금리': 'interest rate',
    }
    def _make_prompt(text: str) -> str:
        words = [_KO_EN.get(w, '') for w in _KO_EN if w in text]
        base = ' '.join(filter(None, [theme] + words[:3]))
        if not base.strip():
            base = 'financial market analysis'
        return f"professional infographic about {base}, clean data visualization, minimal style, white background"

    def _make_para_image(text: str, idx: int, section_title: str = "") -> str:
        """단락 이미지 생성 — matplotlib 인포그래픽 우선, 외부 API 폴백."""
        dest = img_dir / f"para_{idx:04d}.jpg"
        try:
            from JARVIS06_IMAGE.image_spec import generate_image_spec, render_from_spec
            spec = generate_image_spec(
                section_text=text,
                keyword=theme,
                sector="",
                section_title=section_title,
            )
            path = render_from_spec(spec, dest)
            if path and path.exists():
                return str(path)
            return ''
        except Exception as e:
            print(f"  ⚠️ [{platform}] 단락이미지 {idx} 인포그래픽 실패: {e} → AI사진 폴백")
            _g_report("writer", e, module=__name__)
        # 폴백: AI 사진 (기존 방식)
        try:
            from JARVIS06_IMAGE.image_agent import generate_photo
            fallback_dest = img_dir / f"para_{idx:04d}.png"
            path = generate_photo(prompt_ko=_make_prompt(text), out_dir=img_dir)
            if path and path.exists():
                path.rename(fallback_dest)
                return str(fallback_dest)
            return ''
        except Exception as e2:
            print(f"  ⚠️ [{platform}] 단락이미지 {idx} 최종 실패: {e2}")
            _g_report("writer", e2, module=__name__)
            return ''

    def _split_2sent(text: str) -> list:
        """2문장 단위 분리. 이미 분리된 단락(\n\n) 기준 우선, 없으면 문장 단위."""
        # \n\n 으로 나뉜 단락이 있으면 그대로 활용
        raw = [p.strip() for p in text.split('\n\n') if p.strip()]
        if len(raw) >= 2:
            return raw
        # 그외: 마침표 기준 2문장씩 분리
        sents = re.split(r'(?<=[.!?])\s+', text.strip())
        sents = [s.strip() for s in sents if s.strip()]
        if len(sents) <= 2:
            return [text]  # 2문장 이하 → 분리 불필요
        chunks = []
        for i in range(0, len(sents), 2):
            chunk = ' '.join(sents[i:i+2])
            if chunk:
                chunks.append(chunk)
        return chunks

    # ── PASS 1: 분리 대상 블록 수집 ──
    tasks = []   # (block_idx, para_idx, text, heading)
    block_paras = {}  # block_idx → [para, ...]
    _cur_heading = ""  # 직전 소제목 추적 → image_spec section_title 전달

    for bi, (btype, bdata) in enumerate(blocks):
        if btype == 'heading':
            import re as _re
            _cur_heading = _re.sub(r'<[^>]+>', '', str(bdata)).strip()[:30]
            continue
        if btype != 'text':
            continue
        text = str(bdata).strip()
        if not text:
            continue
        paras = _split_2sent(text)
        if len(paras) < 2:
            continue
        block_paras[bi] = paras
        for pi in range(len(paras) - 1):   # 마지막 단락 뒤엔 이미지 없음
            tasks.append((bi, pi, paras[pi] + ' ' + paras[pi + 1], _cur_heading))

    if not tasks:
        return blocks

    print(f"  🤖 [{platform.upper()}-테마] 단락이미지 {len(tasks)}개 순차 생성 중...")

    # ── PASS 2: 순차 생성 ──
    # _make_para_image → generate_image_spec → invoke_text(analyzer) → Claude Code SDK 통과.
    img_results: dict = {}   # (bi, pi) → path
    def _gen(task):
        bi, pi, ctx, heading = task
        path = _make_para_image(ctx, bi * 100 + pi, section_title=heading)
        return (bi, pi), path

    with ThreadPoolExecutor(max_workers=1) as ex:
        futs = {ex.submit(_gen, t): t for t in tasks}
        for fut in as_completed(futs):
            try:
                key, path = fut.result()
                if path:
                    img_results[key] = path
            except Exception as e:
                print(f"  ⚠️ [{platform}] 단락이미지 결과 오류: {e}")
                _g_report("writer", e, module=__name__)

    # ── PASS 3: blocks 재조립 ──
    result = []
    for bi, (btype, bdata) in enumerate(blocks):
        if bi not in block_paras:
            result.append((btype, bdata))
            continue
        paras = block_paras[bi]
        for pi, para in enumerate(paras):
            result.append(('text', para))
            if pi < len(paras) - 1:
                img_path = img_results.get((bi, pi), '')
                if img_path and Path(img_path).exists():
                    result.append(('image', img_path))
    return result


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
            import os, requests as _req
            token = os.getenv('TELEGRAM_TOKEN', '')
            chat  = os.getenv('TELEGRAM_CHAT_ID', '')
            if token and chat:
                _req.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          json={'chat_id': chat, 'text': msg}, timeout=5)
        except Exception:
            pass
        for img in deferred:
            if last_content_img:
                result.append(('text', '<p style="margin:4px 0;">&nbsp;</p>'))
            _emit(img)

    return result


def _build_jsonld(title: str, excerpt: str, keywords: str, date_str: str) -> str:
    """BlogPosting JSON-LD 구조화 데이터 블록 생성"""
    import json as _json
    schema = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": excerpt,
        "datePublished": date_str,
        "dateModified": date_str,
        "author": {"@type": "Organization", "name": "마켓시그널"},
        "publisher": {"@type": "Organization", "name": "마켓시그널"},
        "keywords": keywords,
        "inLanguage": "ko-KR",
    }
    return f'<script type="application/ld+json">{_json.dumps(schema, ensure_ascii=False)}</script>\n'


# ══════════════════════════════════════════
#  원고 로드 (캐시 우선)
# ══════════════════════════════════════════

def load_or_generate_report(theme: str, platform: str = "naver") -> str | None:
    """캐시 원고 있으면 재사용 (24h 이내), 없거나 만료면 새로 생성.
    만료된 캐시 파일은 즉시 삭제.

    ★ new_run() 은 캐시 히트·미스 모두 먼저 호출 — 이미지 상태 격리 보장.
    """
    from JARVIS09_COLLECTOR.run_context import new_run as _new_run
    _new_run(theme, platform=platform, post_type="theme")

    cache_pattern = str(LOGS_DIR / f"report_{theme.replace('/', '_').replace(' ', '_')}_*.txt")
    cached = sorted(glob.glob(cache_pattern))

    # 만료된 캐시 파일 삭제
    for f in cached:
        age_hours = (time.time() - Path(f).stat().st_mtime) / 3600
        if age_hours >= 24:
            Path(f).unlink(missing_ok=True)
            print(f"  🗑️ 만료 캐시 삭제: {Path(f).name} ({age_hours:.0f}h)")

    # 유효한 캐시 재검색
    cached = sorted(glob.glob(cache_pattern))
    if cached:
        cache_file = Path(cached[-1])
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        print(f"\n📂 캐시 원고 재사용: {cache_file.name} ({age_hours:.1f}h 전)")
        report = cache_file.read_text(encoding='utf-8')
        print(f"  ✅ 원고 로드 완료 ({len(report):,}자)")
        return report

    print("\n🧠 CrewAI 테마 분석 & 원고 생성 중...")
    try:
        report = generate_report(theme)
        print(f"  ✅ 원고 생성 완료 ({len(report):,}자)")
        # 캐시 저장
        ts         = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe       = theme.replace("/", "_").replace(" ", "_")
        cache_path = LOGS_DIR / f"report_{safe}_{ts}.txt"
        cache_path.write_text(report, encoding='utf-8')
        print(f"  💾 원고 캐시 저장: {cache_path.name}")
        return report
    except Exception as e:
        print(f"  ❌ 원고 생성 실패: {e}")
        _g_report("writer", e, module=__name__)
        return None

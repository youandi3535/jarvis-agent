"""발행 메타데이터 단일 진입점 — 태그 + 메타 설명 (2026-08-07 신설).

★ 왜 신설했나 — "채점하는 필드에 **생산자가 없었다**"

  100점 루브릭은 티스토리 글의 메타 설명을 3점(`T7_meta_desc`), 네이버 글의 해시태그를
  2점(`N7_hashtags`) 배점한다. 그런데 실측 결과 두 항목은 **전건 0점**이었다:

  · `meta_description` — 저장소 전체에서 이 키를 *채우는* 코드가 **0줄**이었다.
    기준(`seo_standards`)·프롬프트 문구·채점 규칙만 있고 생산자가 없었다.
  · `tags` — 생산자는 있었지만 **부르는 시점이 틀렸다**. 태그는 발행(Layer 4) 안에서
    만들어져 `post_to_*(tags=)` 인자로만 흘렀고, 채점(Layer 3)은 그보다 앞이라
    채점 시점의 `draft["tags"]` 는 언제나 비어 있었다.

  즉 글을 아무리 잘 써도 받을 수 없는 5점이었다. 학습으로 해결될 문제가 아니라
  **배선이 없던 것**이다. 이 모듈은 그 생산을 *대본 완성 시점* 으로 앞당긴다 —
  채점기가 보는 draft 와 실제로 발행되는 메타가 같아진다.

★ 3원칙
  ① 단일 진입점 — 태그 문자열의 주인은 여전히 `tags.generate_tags` 하나다. 바뀌는 것은
    '언제 부르는가' 뿐. 메타 설명은 주인이 없었으므로 같은 폴더(발행 도메인)에 만든다.
    조립자는 `build_post_meta` 하나이고 그 호출자는 `draft_processor` 한 곳이다.
  ② 동적 설계 — 목표 길이·개수를 여기 박지 않는다. `seo_standards.PLATFORM_STANDARDS`
    에서 파생하며, 그건 **채점기 `_std()` 가 읽는 바로 그 dict** 다. 생성 목표와 채점
    기준이 어긋날 수 없는 구조다. "메타를 만들지 말지" 조차 파생이다 — 네이버 표준에는
    `meta_desc_*` 키가 없으므로 네이버는 **플랫폼 if 분기 없이** 자동으로 건너뛴다.
  ③ 4조합 — `process_draft` 가 경제 네이버·경제 티스토리·테마 네이버·테마 티스토리
    전부가 지나는 단일 깔때기다. 여기 한 번 걸면 4조합에 동시에 걸린다.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis")

__all__ = ["build_post_meta", "meta_description", "meta_target_range",
           "post_meta_effective"]


def _std(platform: str, key: str, default):
    """플랫폼 SEO 기준 조회 — `post_scorer._std` 와 **같은 dict** 를 읽는다.

    같은 함수를 import 하지 않는 이유는 층 방향이다(발행 도메인이 작성 도메인의
    내부 헬퍼에 의존하면 안 된다). 읽는 *대상* 이 하나면 사본 문제는 생기지 않는다.
    """
    try:
        from JARVIS02_WRITER.seo_standards import PLATFORM_STANDARDS
        v = PLATFORM_STANDARDS.get(platform, {}).get(key)
        return v if v else default
    except Exception:
        return default



# 재시도 상한 — 파생 leaf 하나(`shared/limits.py`)에서 받는다.
#   ★ 여기에 accessor 를 다시 정의하지 말 것: 사본이 늘면 폴백 하나만 어긋나도
#     경로마다 재시도 횟수가 갈린다(①단일 진입점 · CLAUDE.md 재시도 상한 SSOT).
from shared.limits import max_attempts as _max_attempts


_MAX_ATTEMPTS = _max_attempts()

def meta_target_range(platform: str) -> "tuple[int, int] | None":
    """메타 설명 목표 길이 `(min, max)`. 기준이 없는 플랫폼은 None → 생성 안 함.

    ★ 이 None 이 '네이버는 메타를 만들지 않는다' 는 정책의 **유일한 표현**이다.
      코드에 `if platform == "naver"` 를 쓰지 않는 이유다(② 동적 설계).
    """
    lo = _std(platform, "meta_desc_min_chars", 0)
    hi = _std(platform, "meta_desc_max_chars", 0)
    if not lo or not hi:
        return None
    return int(lo), int(hi)


def _trim_to_range(text: str, lo: int, hi: int) -> str:
    """길이를 목표 구간으로 맞춘다 — 문장 경계를 우선하고, 없으면 그대로 둔다.

    ★ 늘리지 않는다. 모자란 것을 억지로 채우면 없는 말을 지어내게 된다
      (BLOG_SUPREME_LAW 제5조 진실성). 초과분만 문장 단위로 자른다.
    """
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= hi:
        return t
    cut = t[:hi]
    m = list(re.finditer(r"[.!?。]", cut))
    if m and m[-1].end() >= lo:
        return cut[:m[-1].end()].strip()
    return cut.rstrip()


def meta_description(title: str, body_text: str, platform: str) -> str:
    """검색 스니펫용 메타 설명. 기준 없는 플랫폼은 `""`.

    실패해도 **절대 raise 하지 않는다** — 메타가 없으면 종전과 같은 0점일 뿐,
    발행이 막혀서는 안 된다(`generate_tags` 의 폴백 철학 그대로).
    """
    rng = meta_target_range(platform)
    if not rng:
        return ""
    lo, hi = rng
    plain = re.sub(r"<[^>]+>", " ", body_text or "")
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return ""
    try:
        from JARVIS02_WRITER import length_manager as _LM
        snippet = plain[:_LM.BODY_SNIPPET_LEN]
    except Exception:
        snippet = plain[:1200]

    def _ask(feedback: str = "") -> str:
        from shared.llm import invoke_text
        return invoke_text(
            "writer_short_title",
            f"아래 글의 검색 결과 요약문(메타 설명)을 **한 문단**으로 쓰세요.\n"
            f"규칙:\n"
            f"- 정확히 {lo}~{hi}자. 이 범위를 벗어나면 실패입니다\n"
            f"- 이 글이 실제로 다루는 내용만. 본문에 없는 사실·수치 금지\n"
            f"- 검색자가 클릭하고 싶게 — 무엇을 알 수 있는지 구체적으로\n"
            f"- 요약문 본문만 출력. 따옴표·머리말·설명 금지\n"
            f"{feedback}\n"
            f"제목: {title}\n"
            f"본문: {snippet}",
            timeout=60,
        ) or ""

    out = ""
    try:
        # ★ 길이 미달이면 **한 번만** 다시 묻는다 (2026-08-08 — 실측 교정).
        #   `_trim_to_range` 는 자르기만 한다(늘리면 없는 말을 지어내게 되므로 옳다).
        #   그래서 짧게 오면 그대로 감점이었다 — 실측 2026-08-07 21:29 티스토리 글이
        #   99자로 와서 T7 이 0.5/3 이었다. 재시도 상한은 harness 표준(최대 2회)을 따른다.
        for attempt in range(_MAX_ATTEMPTS):
            fb = ""
            if attempt:
                fb = (f"- 직전 답이 {len(out)}자였습니다. {lo}~{hi}자 범위를 반드시 지키세요."
                      f" 내용을 지어내지 말고 본문에 있는 내용을 더 담아 늘리세요.\n")
            cand = _trim_to_range(_ask(fb), lo, hi)
            # ★ 거부문·머리말이 오면 버린다 — 어휘 목록이 아니라 *꼴* 로 판정한다
            #   (`tags.response_is_tag_shaped` 와 같은 철학: 새 거부 표현이 생겨도 낡지 않는다).
            if len(cand) < lo // 2 or "\n" in cand.strip():
                log.warning(f"[post_meta] 메타 설명 응답이 요약문 형태가 아님 — 폐기 ({len(cand)}자)")
                cand = ""
            if cand and len(cand) > len(out):
                out = cand
            if lo <= len(out) <= hi:
                break
    except Exception as e:
        log.warning(f"[post_meta] 메타 설명 생성 실패(발행은 계속): {type(e).__name__}: {e}")
        return out
    if out and not (lo <= len(out) <= hi):
        log.warning(f"[post_meta] 메타 설명 {len(out)}자 — 목표 {lo}~{hi}자 미달(부분 점수)")
    return out


def build_post_meta(title: str, body_text: str, platform: str,
                    post_type: str = "", seed_tags: "list[str] | None" = None) -> dict:
    """발행 메타 조립 — 이 함수가 유일한 조립자다.

    Returns: `{"tags": [...], "meta_description": "..."}`
      · 어느 항목이 실패해도 빈 값으로 떨어질 뿐 **raise 하지 않는다**.
      · `meta_description` 은 기준 있는 플랫폼(현재 티스토리)만 채워진다 — 파생.
    """
    tags: list = []
    try:
        from JARVIS08_PUBLISH.tags import generate_tags
        tags = generate_tags(title, body_text, platform, seed_tags=seed_tags) or []
    except Exception as e:
        log.warning(f"[post_meta] 태그 생성 실패(발행은 계속): {type(e).__name__}: {e}")
    try:
        md = meta_description(title, body_text, platform)
    except Exception as e:
        log.warning(f"[post_meta] 메타 설명 실패(발행은 계속): {type(e).__name__}: {e}")
        md = ""
    return {"tags": tags, "meta_description": md}


def post_meta_effective(platform: str = "tistory") -> dict:
    """★ 실제로 채점 가능한 메타가 나오는지 **동작으로 확인** (patch_effective 표준).

    "생산자를 만들었다" 는 적용의 증거가 아니다 — 채점기에 통과시켜 봐야 안다.
    가짜 글로 한 번 조립한 뒤 **실제 소비자인 `post_scorer`** 로 채점해
    N7_hashtags·T7_meta_desc 가 만점인지 본다.
    """
    body = ("코스피가 상승 마감했다. 반도체 업종이 지수를 끌어올렸다. "
            "외국인 순매수가 이어졌다. 환율은 소폭 하락했다. ") * 12
    title = "코스피 상승 마감 — 반도체가 이끈 하루"
    try:
        meta = build_post_meta(title, body, platform, post_type="economic")
        from JARVIS02_WRITER.post_scorer import score_post, item_scores
        sr = score_post({"html": body, "content": body, "title": title,
                         "keyword": "코스피", "post_type": "economic",
                         "tags": meta["tags"], "meta_description": meta["meta_description"]},
                        platform=platform, post_type="economic")
        got = {i["key"]: (i["score"], i["max"]) for i in item_scores(sr)}
        rng = meta_target_range(platform)
        return {
            "ok": True, "platform": platform,
            "tags": len(meta["tags"]),
            "meta_len": len(meta["meta_description"]),
            "meta_target": rng,
            "N7_hashtags": got.get("N7_hashtags"),
            "T7_meta_desc": got.get("T7_meta_desc"),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

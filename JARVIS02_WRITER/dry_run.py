"""JARVIS02_WRITER/dry_run.py — 발행 안 하고 *발행 직전까지* 확인 도구.

★ ERRORS [141] 사용자 박제 2026-05-17 — 실제 발행은 10분+ 걸림. *발행 전 결과*만 빠르게 확인.

3 단계 모드 (가장 빠른 것부터):

  1. section  — 섹션 plan 만 LLM 1회 호출. 약 30초.
                "이 주제로 어떤 섹션이 나올지" 확인.
  2. draft    — Phase 1 작성까지. 약 2-3분.
                글·이미지·블록 모두 생성. 검증·발행 안 함.
  3. full     — Phase 1 + 1.5 검증·재작성 순환까지. 약 5-10분.
                Layer 3 검증 통과 여부 확인. 발행만 skip.

CLI:
    python -m JARVIS02_WRITER.dry_run --mode section --topic "반도체 HBM"
    python -m JARVIS02_WRITER.dry_run --mode draft --topic "환율 약세"
    python -m JARVIS02_WRITER.dry_run --mode full --topic "코인 시장"

결과:
    /tmp/dry_run_<mode>_<topic>_<timestamp>.json
    + 텔레그램 요약 알림 (가능 시)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis")


def _save_result(mode: str, topic: str, data: dict) -> Path:
    """결과 JSON 저장."""
    safe_topic = "".join(c if c.isalnum() else "_" for c in topic)[:30]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(f"/tmp/dry_run_{mode}_{safe_topic}_{ts}.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _notify_tg(msg: str) -> None:
    """텔레그램 요약 알림 (가능 시). ★ ERRORS [141] — parse_mode 없이 plain text 전송.

    Markdown 파싱은 _·*·[ 같은 특수문자에서 "can't parse entities" 폭발 → plain 강제.
    """
    try:
        from shared.notify import send_tg
        try:
            send_tg(msg, parse_mode=None)
        except TypeError:
            # send_tg 가 parse_mode 인자 미지원이면 — 이모지·markdown 문자 escape 후 전송
            safe = msg.replace("`", "").replace("*", "").replace("_", "-").replace("[", "(").replace("]", ")")
            send_tg(safe)
    except Exception:
        pass


def _summarize_draft(draft: dict) -> dict:
    """draft dict 의 *외부 노출 가능 요약*."""
    if not draft:
        return {"empty": True}
    import re
    try:
        from JARVIS02_WRITER import length_manager as _LM
    except ImportError:
        import length_manager as _LM
    html = draft.get("html") or draft.get("content") or ""
    full_html = draft.get("full_html") or ""
    blocks = draft.get("blocks") or []
    text = re.sub(r"<[^>]+>", " ", html if isinstance(html, str) else "")
    kor = _LM.count(text)
    sents = len([s for s in re.split(r"[.!?。]\s*|\n+", text) if len(s.strip()) > 3])
    img_count = (full_html or html).count("<img ") if isinstance(full_html or html, str) else 0
    svg_count = (full_html or html).count("<svg") if isinstance(full_html or html, str) else 0
    block_imgs = sum(1 for bt, _ in blocks if bt == "image") if isinstance(blocks, list) else 0
    return {
        "success": draft.get("success", False),
        "keyword": draft.get("keyword", ""),
        "title": (draft.get("title") or "")[:80],
        "html_len": len(html) if isinstance(html, str) else 0,
        "full_html_len": len(full_html) if isinstance(full_html, str) else 0,
        "korean_chars": kor,
        "sentences_est": sents,
        "img_tags": img_count,
        "svg_tags": svg_count,
        "block_count": len(blocks) if isinstance(blocks, list) else 0,
        "block_images": block_imgs,
        "verify_blocked": draft.get("_verify_blocked", False),
        "verify_issues": draft.get("_issues", []),
        "html_preview": (html[:500] if isinstance(html, str) else "")[:500],
    }


# ── 모드 1: section plan 만 ─────────────────────────────────

def run_section_plan(topic: str, post_type: str = "economic", context: str = "") -> dict:
    """섹션 plan 만 LLM 호출. 약 30초. 가장 빠른 검증."""
    t0 = time.time()
    from JARVIS02_WRITER.post_type_specs import get_spec, generate_section_plan
    spec = get_spec(post_type)
    plan = generate_section_plan(spec, topic, context=context)
    elapsed = time.time() - t0
    result = {
        "mode": "section",
        "topic": topic,
        "post_type": post_type,
        "elapsed_sec": round(elapsed, 1),
        "spec": {
            "purpose": spec.purpose,
            "section_range": f"{spec.min_sections}~{spec.max_sections}",
            "sentence_range_per_section": list(spec.sentences_per_section),
            "total_sentence_range": f"{spec.min_sentences}~{spec.max_sentences}",
            "max_korean": spec.max_korean,
            "required_sections": spec.required_sections,
        },
        "section_plan": [
            {"name": p.name, "sentences": p.sentences, "images": p.images, "tables": p.tables}
            for p in plan
        ],
        "total_sentences": sum(p.sentences for p in plan),
        "total_images": sum(p.images for p in plan),
        "total_tables": sum(p.tables for p in plan),
    }
    return result


# ── 모드 2: Phase 1 draft 까지 ─────────────────────────────

def run_draft(topic: str, post_type: str = "economic", market: Optional[dict] = None) -> dict:
    """Phase 1 작성까지. 검증·재작성·발행 안 함. 약 2-3분.

    ★ ERRORS [141] — topic 인자가 *진짜 키워드* 로 작용 (JARVIS_FORCE_TOPIC env 주입).
    """
    import os as _os
    t0 = time.time()
    # ★ topic 강제 주입 — 작성 함수가 RADAR 우회하고 topic 사용
    _prev_force = _os.environ.get("JARVIS_FORCE_TOPIC")
    _os.environ["JARVIS_FORCE_TOPIC"] = topic
    _os.environ["JARVIS_FORCE_SECTOR"] = "dry_run"
    _os.environ["JARVIS_FORCE_REASON"] = "dry_run 사용자 지정"

    # ★ 수집(ts_collect) + 대본(ts_generate_draft) 분리 구조 (2026-07-10 리팩터링)
    try:
        from JARVIS02_WRITER.trend_economic_writer import ts_collect, ts_generate_draft
        collect_result = ts_collect()
        if not collect_result.get("success"):
            return {
                "mode": "draft",
                "topic": topic,
                "post_type": post_type,
                "error": f"수집 실패: {collect_result.get('error', 'unknown')}",
                "elapsed_sec": round(time.time() - t0, 1),
            }
        draft = ts_generate_draft(
            keyword=collect_result["keyword"],
            sector=collect_result["sector"],
            reason=collect_result["reason"],
            collected=collect_result["collected"],
            supreme_block=collect_result.get("supreme_block"),
            source_docs=collect_result.get("source_docs"),
        )
        # ★ post_type 강제 박기 — 검증 함수가 올바른 spec 사용
        if isinstance(draft, dict):
            draft["post_type"] = post_type
    except Exception as e:
        if _prev_force is None:
            _os.environ.pop("JARVIS_FORCE_TOPIC", None)
        else:
            _os.environ["JARVIS_FORCE_TOPIC"] = _prev_force
        return {
            "mode": "draft",
            "topic": topic,
            "post_type": post_type,
            "error": f"draft 생성 실패: {type(e).__name__}: {e}",
            "elapsed_sec": round(time.time() - t0, 1),
        }
    finally:
        # 환경변수 정리 — carryover 차단
        if _prev_force is None:
            _os.environ.pop("JARVIS_FORCE_TOPIC", None)
            _os.environ.pop("JARVIS_FORCE_SECTOR", None)
            _os.environ.pop("JARVIS_FORCE_REASON", None)

    return {
        "mode": "draft",
        "topic": topic,
        "post_type": post_type,
        "elapsed_sec": round(time.time() - t0, 1),
        "draft_summary": _summarize_draft(draft),
    }


# ── 모드 3: Phase 1 + 1.5 검증·재작성 순환까지 ──────────────

def run_full(topic: str, post_type: str = "economic", market: Optional[dict] = None) -> dict:
    """Phase 1 + 1.5 검증·재작성 순환까지. 약 5-10분. 발행만 skip.

    ★ ERRORS [141] — topic·post_type 강제 주입 + draft.post_type 박기.
    """
    import os as _os
    t0 = time.time()
    _prev_force = _os.environ.get("JARVIS_FORCE_TOPIC")
    _os.environ["JARVIS_FORCE_TOPIC"] = topic
    _os.environ["JARVIS_FORCE_SECTOR"] = "dry_run"
    _os.environ["JARVIS_FORCE_REASON"] = "dry_run 사용자 지정"

    # ★ 수집(ts_collect) + 대본(ts_generate_draft) 분리 구조 (2026-07-10 리팩터링)
    try:
        from JARVIS02_WRITER.trend_economic_writer import ts_collect, ts_generate_draft
        collect_result = ts_collect()
        if not collect_result.get("success"):
            return {
                "mode": "full",
                "topic": topic,
                "error": f"수집 실패: {collect_result.get('error', 'unknown')}",
                "elapsed_sec": round(time.time() - t0, 1),
            }
        draft = ts_generate_draft(
            keyword=collect_result["keyword"],
            sector=collect_result["sector"],
            reason=collect_result["reason"],
            collected=collect_result["collected"],
            supreme_block=collect_result.get("supreme_block"),
            source_docs=collect_result.get("source_docs"),
        )
        # ★ post_type 강제 박기 — Layer 3 검증이 올바른 spec 사용
        if isinstance(draft, dict):
            draft["post_type"] = post_type
    except Exception as e:
        return {
            "mode": "full",
            "topic": topic,
            "error": f"Phase 1 실패: {type(e).__name__}: {e}",
            "elapsed_sec": round(time.time() - t0, 1),
        }
    finally:
        if _prev_force is None:
            _os.environ.pop("JARVIS_FORCE_TOPIC", None)
            _os.environ.pop("JARVIS_FORCE_SECTOR", None)
            _os.environ.pop("JARVIS_FORCE_REASON", None)

    if not draft.get("success"):
        return {
            "mode": "full",
            "topic": topic,
            "post_type": post_type,
            "phase1_failed": True,
            "draft_summary": _summarize_draft(draft),
            "elapsed_sec": round(time.time() - t0, 1),
        }

    # Phase 1.5 검증 — draft.post_type 박혔으니 올바른 spec 사용
    from JARVIS02_WRITER.economic_poster import _layer3_verify_draft
    initial_issues = _layer3_verify_draft(draft, post_type if post_type else "naver")

    return {
        "mode": "full",
        "topic": topic,
        "post_type": post_type,
        "elapsed_sec": round(time.time() - t0, 1),
        "draft_summary": _summarize_draft(draft),
        "layer3_verify": {
            "passed": not initial_issues,
            "issues_count": len(initial_issues),
            "issues": initial_issues,
        },
        "would_publish": not initial_issues,
        "note": "★ 실제 발행은 안 됨. would_publish=True 면 Phase 2 통과 예상.",
    }


# ── CLI 진입점 ─────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="발행 안 하고 발행 전까지 확인 — 3 모드 (section/draft/full)"
    )
    parser.add_argument("--mode", choices=["section", "draft", "full"], default="section",
                        help="section(30초) / draft(2-3분) / full(5-10분)")
    parser.add_argument("--topic", required=True, help="주제 (키워드)")
    parser.add_argument("--post-type", default="economic", choices=["economic", "theme"],
                        help="글 종류")
    parser.add_argument("--context", default="", help="추가 맥락")
    parser.add_argument("--quiet", action="store_true", help="JSON 만 출력")
    args = parser.parse_args(argv)

    print(f"\n🔍 dry_run [{args.mode}] topic='{args.topic}' post_type={args.post_type}\n")

    if args.mode == "section":
        result = run_section_plan(args.topic, args.post_type, args.context)
    elif args.mode == "draft":
        result = run_draft(args.topic, args.post_type)
    else:
        result = run_full(args.topic, args.post_type)

    out_path = _save_result(args.mode, args.topic, result)
    print(f"\n📄 결과 저장: {out_path}\n")

    if not args.quiet:
        if args.mode == "section":
            print(f"  spec: {result['spec']['section_range']} 섹션 / {result['spec']['sentence_range_per_section']} 문장")
            print(f"  생성: {len(result['section_plan'])} 섹션 · {result['total_sentences']}문장 · {result['total_images']} 이미지 · {result['total_tables']} 표")
            print()
            for i, s in enumerate(result["section_plan"], 1):
                tbl = f", 표 {s['tables']}" if s['tables'] else ""
                print(f"  {i}. {s['name']}: {s['sentences']}문장, 이미지 {s['images']}{tbl}")
        else:
            summary = result.get("draft_summary", {})
            print(f"  소요: {result['elapsed_sec']}초")
            print(f"  draft 성공: {summary.get('success')}")
            print(f"  키워드: {summary.get('keyword')}")
            print(f"  제목: {summary.get('title')}")
            print(f"  본문: {summary.get('sentences_est')}문장, {summary.get('korean_chars')}자")
            print(f"  이미지: img {summary.get('img_tags')} + svg {summary.get('svg_tags')}")
            print(f"  블록: {summary.get('block_count')}개 (이미지 {summary.get('block_images')})")
            if args.mode == "full":
                ver = result.get("layer3_verify", {})
                print(f"\n  ★ Layer 3 검증: {'✅ 통과 — 발행 가능' if ver.get('passed') else '❌ 차단'}")
                if ver.get("issues"):
                    print(f"  issues ({ver['issues_count']}):")
                    for iss in ver["issues"][:10]:
                        print(f"    • {iss}")
                print(f"\n  would_publish: {result.get('would_publish')}")

    _notify_tg(f"🔍 dry_run [{args.mode}] '{args.topic}' 완료 — {result.get('elapsed_sec', 0)}초\n결과: {out_path.name}")
    return 0 if result.get("would_publish", True) and "error" not in result else 1


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18)
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    sys.exit(main())

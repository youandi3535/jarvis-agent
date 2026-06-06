"""
자동 발행 작업 로그 모니터링 및 요약
- 매일 07:30: 경제 브리핑 로그 확인
- 매일 16:30: 테마주 로그 확인
"""
from pathlib import Path
from datetime import datetime
import re

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"

def get_todays_date():
    """오늘 날짜를 YYYYMMDD 형식으로 반환"""
    return datetime.now().strftime("%Y%m%d")

def read_latest_economic_log():
    """최근 경제 브리핑 로그 읽기"""
    today = get_todays_date()
    log_pattern = LOG_DIR / f"economic_{today}*.log"

    logs = sorted(LOG_DIR.glob(f"economic_{today}*.log"), reverse=True)
    if not logs:
        return None

    return logs[0]

def read_latest_theme_log():
    """최근 테마주 로그 읽기 (scheduler.log)"""
    log_file = LOG_DIR / "scheduler.log"
    if not log_file.exists():
        return None
    return log_file

def summarize_economic_log(log_file):
    """경제 브리핑 로그 요약"""
    if not log_file:
        return "❌ 오늘 경제 브리핑 로그를 찾을 수 없습니다."

    try:
        content = log_file.read_text(encoding='utf-8')

        # 결과 추출
        naver_success = "네이버" in content and ("✅" in content or "성공" in content)
        tistory_success = "티스토리" in content and ("✅" in content or "성공" in content)

        # 에러 확인
        has_error = "❌" in content or "실패" in content or "오류" in content

        # 이미지 개수
        img_count = len(re.findall(r'\.png|\.jpg|\.jpeg', content))

        time_str = datetime.now().strftime("%H:%M")
        status = "✅" if (naver_success or tistory_success) else "⚠️"

        result = f"""📰 아침 경제 브리핑 ({time_str})
━━━━━━━━━━━━━━━━━━
네이버: {'✅' if naver_success else '❌'}
티스토리: {'✅' if tistory_success else '❌'}
이미지: {img_count}개
상태: {status}{'에러 발생' if has_error else '정상'}"""

        return result
    except Exception as e:
        return f"❌ 로그 읽기 오류: {e}"

def summarize_theme_log(log_file):
    """테마주 로그 요약"""
    if not log_file:
        return "❌ 오늘 테마주 로그를 찾을 수 없습니다."

    try:
        content = log_file.read_text(encoding='utf-8', errors='ignore')

        # 테마명 추출
        theme_match = re.search(r'RADAR 선택:\s*([^\(]+)', content)
        theme = theme_match.group(1).strip() if theme_match else "불명"

        # 결과 추출 (가장 최근 결과만)
        naver_match = re.search(r'네이버[: ]+([✅❌])', content)
        tistory_match = re.search(r'티스토리[: ]+([✅❌])', content)

        naver_success = naver_match and "✅" in naver_match.group(1) if naver_match else False
        tistory_success = tistory_match and "✅" in tistory_match.group(1) if tistory_match else False

        # 에러 확인
        has_error = "❌" in content or "실패" in content

        # 이미지 개수
        img_count = len(re.findall(r'\.png|\.jpg|\.jpeg', content[-5000:]))  # 마지막 5000글자 검사

        time_str = datetime.now().strftime("%H:%M")
        status = "✅" if (naver_success or tistory_success) else "⚠️"

        result = f"""🎯 테마주 발행 ({time_str})
━━━━━━━━━━━━━━━━━━
테마: {theme}
네이버: {'✅' if naver_success else '❌'}
티스토리: {'✅' if tistory_success else '❌'}
이미지: {img_count}개
상태: {status}{'에러 발생' if has_error else '정상'}"""

        return result
    except Exception as e:
        return f"❌ 로그 읽기 오류: {e}"

def job_check_economic_result():
    """APScheduler 콜백: 경제 브리핑 로그 확인"""
    log_file = read_latest_economic_log()
    summary = summarize_economic_log(log_file)
    print(f"\n{summary}\n")

def job_check_theme_result():
    """APScheduler 콜백: 테마주 로그 확인"""
    log_file = read_latest_theme_log()
    summary = summarize_theme_log(log_file)
    print(f"\n{summary}\n")

if __name__ == "__main__":
    import sys
    if "economic" in sys.argv:
        job_check_economic_result()
    elif "theme" in sys.argv:
        job_check_theme_result()

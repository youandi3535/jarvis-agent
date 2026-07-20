#!/bin/bash
# JARVIS 통합 데몬 재시작 스크립트
#
# 경로 하드코딩 금지 — 스크립트 자신의 위치에서 ROOT 를 도출한다.
# 폴더를 어디로 옮겨도 수정 없이 동작 (ERRORS: 2026-07-19 폴더 이동 사고).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT" || exit 1

if [ ! -x "$PY" ]; then
    echo "❌ venv 없음: $PY"
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

echo "📁 ROOT: $ROOT"
echo "🛑 기존 프로세스 전체 종료..."
# keeper 를 먼저 내려야 데몬 kill 후 되살리지 못함 (KeepAlive=true)
launchctl unload ~/Library/LaunchAgents/com.jarvis.keeper.plist 2>/dev/null
pkill -f "jarvis_daemon.py" 2>/dev/null
pkill -f "jarvis_keeper.py" 2>/dev/null
pkill -f "scheduler.py" 2>/dev/null
# 옛 경로에서 살아남은 좀비까지 정리 (텔레그램 polling 409 Conflict 방지)
pkill -f "uvicorn api_server" 2>/dev/null
sleep 3

echo "🚀 데몬 시작..."
# stdout은 /dev/null — 로그는 Python FileHandler가 단독으로 daemon.log에 기록
nohup "$PY" "$ROOT/jarvis_daemon.py" > /dev/null 2>&1 &

sleep 4

COUNT=$(pgrep -f "jarvis_daemon.py" | wc -l | tr -d ' ')
if [ "$COUNT" -eq "1" ]; then
    echo "✅ 데몬 정상 시작 (인스턴스: 1개)"
    echo ""
    echo "📋 최근 로그:"
    tail -8 "$ROOT/logs/daemon.log"
else
    echo "⚠️ 인스턴스 ${COUNT}개 — 강제 정리 후 재시작..."
    pkill -f "jarvis_daemon.py"
    sleep 2
    nohup "$PY" "$ROOT/jarvis_daemon.py" > /dev/null 2>&1 &
    sleep 3
    tail -5 "$ROOT/logs/daemon.log"
fi

# keeper 재등록 — 앞에서 unload 했으므로 반드시 복원해야 감시자가 살아난다.
# (복원 누락 시 데몬 사망 시 아무도 되살리지 못함)
echo ""
if [ -f "$HOME/Library/LaunchAgents/com.jarvis.keeper.plist" ]; then
    launchctl load "$HOME/Library/LaunchAgents/com.jarvis.keeper.plist" 2>/dev/null
    echo "🛡️ keeper 재등록 완료"
else
    echo "⚠️ keeper plist 없음 — ./install_keeper.sh 를 먼저 실행하세요"
fi

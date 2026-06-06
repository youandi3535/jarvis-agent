#!/bin/bash
# JARVIS 통합 데몬 재시작 스크립트
cd ~/jarvis-agent

echo "🛑 기존 프로세스 전체 종료..."
launchctl unload ~/Library/LaunchAgents/com.jarvis.keeper.plist 2>/dev/null
pkill -f "jarvis_daemon.py" 2>/dev/null
pkill -f "jarvis_keeper.py" 2>/dev/null
pkill -f "scheduler.py" 2>/dev/null
sleep 3

echo "🚀 데몬 시작..."
# stdout은 /dev/null — 로그는 Python FileHandler가 단독으로 daemon.log에 기록
nohup ~/jarvis-agent/.venv/bin/python ~/jarvis-agent/jarvis_daemon.py \
  > /dev/null 2>&1 &

sleep 4

COUNT=$(pgrep -f "jarvis_daemon.py" | wc -l | tr -d ' ')
if [ "$COUNT" -eq "1" ]; then
    echo "✅ 데몬 정상 시작 (인스턴스: 1개)"
    echo ""
    echo "📋 최근 로그:"
    tail -8 ~/jarvis-agent/logs/daemon.log
else
    echo "⚠️ 인스턴스 ${COUNT}개 — 강제 정리 후 재시작..."
    pkill -f "jarvis_daemon.py"
    sleep 2
    nohup ~/jarvis-agent/.venv/bin/python ~/jarvis-agent/jarvis_daemon.py \
      > /dev/null 2>&1 &
    sleep 3
    tail -5 ~/jarvis-agent/logs/daemon.log
fi

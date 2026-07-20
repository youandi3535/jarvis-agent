#!/bin/bash
# JARVIS Keeper launchd 등록 스크립트
#
# launchd plist 는 절대경로만 받으므로 하드코딩을 피할 수 없다.
# 대신 *plist 자체를 이 스크립트 위치에서 생성* 한다 → 폴더를 옮기면
# 이 스크립트만 다시 실행하면 끝. (ERRORS: 2026-07-19 폴더 이동 사고 —
# 옛 경로 plist 가 KeepAlive=true 로 좀비 데몬을 계속 되살림)
#
# 사용법:
#   ./install_keeper.sh            # 새 경로로 재등록
#   ./install_keeper.sh --uninstall # 등록 해제

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"
LABEL="com.jarvis.keeper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# ── 등록 해제 ──────────────────────────────────────────────
if [ "$1" = "--uninstall" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✅ $LABEL 등록 해제 완료"
    exit 0
fi

# ── 사전 검증 ──────────────────────────────────────────────
if [ ! -x "$PY" ]; then
    echo "❌ venv 없음: $PY"
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
if [ ! -f "$ROOT/jarvis_keeper.py" ]; then
    echo "❌ jarvis_keeper.py 없음: $ROOT"
    exit 1
fi

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

# ── 기존 등록 해제 (옛 경로 plist 포함) ─────────────────────
if [ -f "$PLIST" ]; then
    OLD=$(grep -A1 '<key>WorkingDirectory</key>' "$PLIST" | tail -1 | sed -E 's/.*<string>(.*)<\/string>.*/\1/')
    if [ -n "$OLD" ] && [ "$OLD" != "$ROOT" ]; then
        echo "🔄 옛 경로 plist 발견 → 교체"
        echo "   old: $OLD"
        echo "   new: $ROOT"
    fi
    launchctl unload "$PLIST" 2>/dev/null || true
fi

# ── plist 생성 (현재 위치 기준) ────────────────────────────
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$ROOT/jarvis_keeper.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/keeper.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/keeper.log</string>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
</dict>
</plist>
PLIST_EOF

launchctl load "$PLIST"
sleep 2

echo "✅ $LABEL 등록 완료"
echo "   ROOT   : $ROOT"
echo "   python : $PY"
echo ""
if launchctl list | grep -q "$LABEL"; then
    echo "📋 launchd 상태:"
    launchctl list | grep "$LABEL"
else
    echo "⚠️ launchctl list 에 없음 — $ROOT/logs/keeper.log 확인"
fi

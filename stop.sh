#!/bin/bash
cd "$(dirname "$0")"

if [ -f bot.pid ]; then
    PID=$(cat bot.pid)
    
    # Jarayon ishlayotganini tekshirish
    if ps -p $PID > /dev/null; then
        echo "Bot (PID: $PID) to'xtatilmoqda..."
        kill $PID
        
        # Biroz kutamiz va agar to'xtamasa majburiy to'xtatamiz
        sleep 2
        if ps -p $PID > /dev/null; then
            kill -9 $PID
            echo "Bot majburiy to'xtatildi."
        fi
        
        echo "Bot muvaffaqiyatli to'xtatildi."
    else
        echo "Bot (PID: $PID) allaqachon to'xtatilgan yoki topilmadi."
    fi
    
    rm -f bot.pid
else
    echo "bot.pid fayli topilmadi. Jarayon qidirilmoqda..."
    SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/main.py"
    pkill -f "$SCRIPT_PATH"
    if [ $? -eq 0 ]; then
        echo "Bot jarayoni topildi va to'xtatildi."
    else
        echo "Ishlab turgan bot jarayoni topilmadi."
    fi
fi

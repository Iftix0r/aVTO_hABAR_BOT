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
    
    # pid faylni o'chirish
    rm bot.pid
else
    echo "bot.pid fayli topilmadi. Bot ishlayotganiga ishonch hosil qiling."
    # Ehtiyot shart sifatida main.py qidirib to'xtatish
    pkill -f "python3 main.py"
    if [ $? -eq 0 ]; then
        echo "Orqa fondagi barcha bot jarayonlari topildi va to'xtatildi."
    fi
fi

#!/bin/bash
# Dastur ishlayotgan papkaga o'tamiz
cd "$(dirname "$0")"

if [ -f bot.pid ]; then
    echo "Bot allaqachon ishlayotgan ko'rinadi (bot.pid mavjud). Avval stop.sh ni ishlating."
    exit 1
fi

echo "Bot ishga tushirilmoqda..."
# Virtual muhitni faollashtirish (agar mavjud bo'lsa)
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Botni orqa fonda (background) ishga tushirish
nohup python3 main.py > bot.log 2>&1 &
PID=$!

# PID ni faylga saqlash
echo $PID > bot.pid

echo "Bot muvaffaqiyatli ishga tushirildi! (PID: $PID)"
echo "Jarayonni kuzatish uchun: tail -f bot.log"

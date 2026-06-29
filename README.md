# Avto Habar Bot

Ushbu loyiha Telegram orqali bot foydalanuvchilariga o'z hisoblariga ulanish (userbot), barcha guruhlarini bitta jildga (folder) jamlash va belgilangan vaqt oralig'ida shu guruhlarga avtomatik ravishda xabar yuborish imkonini beradi.

## Xususiyatlar
- **Xavfsiz Kirish:** Foydalanuvchilar to'g'ridan-to'g'ri Telegram bot orqali telefon raqami va kod yordamida o'z hisoblariga kira oladilar.
- **Jild Yaratish:** Bot foydalanuvchining barcha guruh va superguruhlarini avtomatik qidirib topadi va ularni "Avto Habar Guruhlar" nomli jildga birlashtiradi.
- **Avto-Xabar:** Foydalanuvchi xohlagan xabar matnini va yuborish vaqtini (intervalni) sozlashi mumkin. Bot ushbu matnni orqa fonda guruhlarga avtomatik yuborib turadi.
- **Boshqaruv:** Xabar yuborishni xohlagan paytda to'xtatish yoki qayta ishga tushirish imkoniyati. Holatni ko'rish funksiyasi mavjud.
- **Qayta Tiklanish:** Server yoki bot o'chib yonganda ham avto-xabar jarayonlari bazadan o'qilib qayta ishga tushadi.

## O'rnatish

1. Kutubxonalarni o'rnating:
   ```bash
   pip install -r requirements.txt
   ```

2. `.env.example` faylidan nusxa ko'chirib, nomini `.env` qilib saqlang va kerakli ma'lumotlarni to'ldiring:
   ```env
   API_ID=1234567
   API_HASH=your_api_hash_here
   BOT_TOKEN=your_bot_token_here
   ```
   *(API kalitlarini [my.telegram.org](https://my.telegram.org) dan olishingiz mumkin. Bot Tokenni esa [BotFather](https://t.me/botfather) dan olasiz).*

3. Botni ishga tushiring:
   ```bash
   python main.py
   ```

## Ishlatish (Foydalanuvchilar uchun)
1. Botga kiring va `/start` tugmasini bosing.
2. **"🔐 Hisobga kirish"** tugmasi orqali o'z profilingizga kiring.
3. **"📁 Jild yaratish va guruhlarni qo'shish"** ni bosib guruhlaringizni bot xotirasiga oling.
4. **"✉️ Avto habar sozlash"** orqali guruhlarga yuboriladigan xabar matnini yozing.
5. **"⏱ Vaqtni sozlash"** orqali necha daqiqada bir marta yuborish kerakligini belgilang.
6. **"▶️ Boshlash"** tugmasi orqali jarayonni ishga tushiring.

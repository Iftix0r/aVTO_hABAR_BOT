import asyncio
import os
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid
from pyrogram.raw.functions.messages import UpdateDialogFilter
from pyrogram.raw.types import DialogFilter
from pyrogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import config
from db import update_user, get_user, load_db

bot = Client("bot_session", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)
scheduler = AsyncIOScheduler()

user_clients = {}
user_states = {}

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔐 Hisobga kirish")],
        [KeyboardButton("📁 Jild yaratish va guruhlarni qo'shish")],
        [KeyboardButton("✉️ Avto habar sozlash"), KeyboardButton("⏱ Vaqtni sozlash")],
        [KeyboardButton("▶️ Boshlash"), KeyboardButton("🛑 To'xtatish")],
        [KeyboardButton("ℹ️ Holat")]
    ], resize_keyboard=True)

def cancel_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)

async def get_or_create_client(user_id):
    if user_id in user_clients:
        return user_clients[user_id]
    
    session_name = f"session_{user_id}"
    if os.path.exists(f"{session_name}.session"):
        client = Client(session_name, config.API_ID, config.API_HASH)
        await client.connect()
        user_clients[user_id] = client
        return client
    return None

async def send_auto_message(user_id):
    user_data = get_user(user_id)
    if user_data.get('status') != 'running':
        return
        
    client = await get_or_create_client(user_id)
    if not client:
        return
        
    message_id = user_data.get('auto_message_id')
    old_message = user_data.get('auto_message')
    groups = user_data.get('groups', [])
    if not groups or (not message_id and not old_message):
        return
        
    bot_id = int(config.BOT_TOKEN.split(':')[0])
    is_forward = user_data.get('is_forward', False)
        
    for g in groups:
        try:
            if message_id:
                if is_forward:
                    await client.forward_messages(chat_id=g, from_chat_id=bot_id, message_ids=message_id)
                else:
                    await client.copy_message(chat_id=g, from_chat_id=bot_id, message_id=message_id)
            else:
                await client.send_message(g, old_message)
            await asyncio.sleep(1)
        except Exception as e:
            print(f"[{user_id}] Xatolik guruhga yuborishda {g}: {e}")

def setup_job(user_id):
    user_data = get_user(user_id)
    interval_minutes = int(user_data.get('interval', 60))
    job_id = f"job_{user_id}"
    
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        
    if user_data.get('status') == 'running':
        scheduler.add_job(send_auto_message, 'interval', minutes=interval_minutes, args=[user_id], id=job_id)

@bot.on_message(filters.private)
async def message_handler(client, message):
    chat_id = message.chat.id
    text = message.text or ""

    if text == "/start":
        user_states.pop(chat_id, None)
        await message.reply_text(
            "👋 Salom! Avto-habar botiga xush kelibsiz.\n"
            "Quyidagi menyu orqali botni boshqaring:",
            reply_markup=main_menu()
        )
        return

    if text == "❌ Bekor qilish":
        user_states.pop(chat_id, None)
        await message.reply_text("Amal bekor qilindi.", reply_markup=main_menu())
        return

    # Check states
    state_info = user_states.get(chat_id, {})
    state = state_info.get("state")

    if state == "WAITING_PHONE":
        if message.contact:
            phone = message.contact.phone_number
        else:
            phone = text.strip()
            
        if not phone:
            await message.reply_text("Iltimos, raqamni kiriting yoki tugma orqali yuboring.")
            return
            
        await message.reply_text("Kodni kutmoqdamiz... Iltimos kuting.", reply_markup=cancel_menu())
        try:
            user_client = Client(f"session_{chat_id}", config.API_ID, config.API_HASH)
            await user_client.connect()
            sent_code = await user_client.send_code(phone)
            
            user_states[chat_id] = {
                "state": "WAITING_CODE",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "client": user_client
            }
            await message.reply_text("📲 Telegramdan kelgan kodni kiriting:\n(Agar raqamlardan iborat bo'lsa, xato bo'lmasligi uchun orasiga vergul yoki bo'sh joy qo'shib yozing, masalan: 1,2,3,4,5)", reply_markup=cancel_menu())
        except Exception as e:
            await message.reply_text(f"❌ Xatolik yuz berdi: {e}", reply_markup=main_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_CODE":
        code = text.replace(" ", "").replace(",", "").strip()
        state_data = user_states[chat_id]
        user_client = state_data["client"]
        phone = state_data["phone"]
        phone_code_hash = state_data["phone_code_hash"]
        
        try:
            await user_client.sign_in(phone, phone_code_hash, code)
            user_clients[chat_id] = user_client
            update_user(chat_id, phone=phone)
            user_states.pop(chat_id, None)
            await message.reply_text("✅ Hisobga muvaffaqiyatli kirdingiz!", reply_markup=main_menu())
        except SessionPasswordNeeded:
            user_states[chat_id]["state"] = "WAITING_PASSWORD"
            await message.reply_text("🔐 Ikki bosqichli autentifikatsiya parolini kiriting:", reply_markup=cancel_menu())
        except Exception as e:
            await message.reply_text(f"❌ Xato kod kiritildi yoki xatolik: {e}", reply_markup=main_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_PASSWORD":
        password = text.strip()
        user_client = user_states[chat_id]["client"]
        try:
            await user_client.check_password(password)
            user_clients[chat_id] = user_client
            update_user(chat_id, phone=user_states[chat_id]["phone"])
            user_states.pop(chat_id, None)
            await message.reply_text("✅ Hisobga muvaffaqiyatli kirdingiz!", reply_markup=main_menu())
        except Exception as e:
            await message.reply_text(f"❌ Noto'g'ri parol yoki xatolik: {e}", reply_markup=main_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_AUTO_MESSAGE":
        is_forward = True if getattr(message, "forward_date", None) else False
        update_user(chat_id, auto_message_id=message.id, is_forward=is_forward, auto_message=None)
        user_states.pop(chat_id, None)
        await message.reply_text("✅ Reklama xabari saqlandi! (Rasm, video va forwardlar qo'llab-quvvatlanadi)", reply_markup=main_menu())
        return

    elif state == "WAITING_INTERVAL":
        if text.isdigit():
            update_user(chat_id, interval=int(text))
            user_states.pop(chat_id, None)
            setup_job(chat_id)
            await message.reply_text(f"✅ Interval {text} daqiqaga o'rnatildi!", reply_markup=main_menu())
        else:
            await message.reply_text("Iltimos, faqat raqam kiriting (daqiqalarda):")
        return

    # Button handlers
    if text == "🔐 Hisobga kirish":
        if os.path.exists(f"session_{chat_id}.session"):
            await message.reply_text("✅ Siz allaqachon hisobga kirgansiz.")
            return
        user_states[chat_id] = {"state": "WAITING_PHONE"}
        contact_menu = ReplyKeyboardMarkup([
            [KeyboardButton("📞 Raqamni yuborish", request_contact=True)],
            [KeyboardButton("❌ Bekor qilish")]
        ], resize_keyboard=True)
        await message.reply_text("Telefon raqamingizni xalqaro formatda kiriting (masalan: +998901234567) yoki pastdagi tugmani bosing:", reply_markup=contact_menu)

    elif text == "📁 Jild yaratish va guruhlarni qo'shish":
        user_client = await get_or_create_client(chat_id)
        if not user_client:
            await message.reply_text("❌ Oldin hisobga kiring!")
            return
            
        msg = await message.reply_text("⏳ Guruhlar izlanmoqda va jild yaratilmoqda...")
        groups = []
        async for d in user_client.get_dialogs():
            if str(d.chat.type) in ["ChatType.GROUP", "ChatType.SUPERGROUP"]:
                groups.append(d.chat.id)
                
        if not groups:
            await msg.edit_text("❌ Sizda hech qanday guruh topilmadi.")
            return
            
        peers = []
        for g in groups:
            try:
                peers.append(await user_client.resolve_peer(g))
            except:
                pass
                
        try:
            filter_folder = DialogFilter(
                id=10,
                title="Avto Habar Guruhlar",
                pinned_peers=[],
                include_peers=peers,
                exclude_peers=[]
            )
            await user_client.invoke(UpdateDialogFilter(id=10, filter=filter_folder))
            update_user(chat_id, groups=groups)
            await msg.edit_text(f"✅ Jild yaratildi va unga {len(groups)} ta guruh qo'shildi!")
        except Exception as e:
            await msg.edit_text(f"❌ Xatolik yuz berdi: {e}")

    elif text == "✉️ Avto habar sozlash":
        user_states[chat_id] = {"state": "WAITING_AUTO_MESSAGE"}
        await message.reply_text("Guruhlarga yuboriladigan habar matnini yuboring:", reply_markup=cancel_menu())

    elif text == "⏱ Vaqtni sozlash":
        user_states[chat_id] = {"state": "WAITING_INTERVAL"}
        await message.reply_text("Habar yuborish intervalini daqiqalarda kiriting (masalan: 60):", reply_markup=cancel_menu())

    elif text == "▶️ Boshlash":
        user_data = get_user(chat_id)
        if not user_data.get('groups') or (not user_data.get('auto_message_id') and not user_data.get('auto_message')):
            await message.reply_text("❌ Avto-habar kiritilmagan yoki guruhlar topilmadi.")
            return
        update_user(chat_id, status="running")
        setup_job(chat_id)
        await message.reply_text("✅ Avto-habar yuborish boshlandi!")

    elif text == "🛑 To'xtatish":
        update_user(chat_id, status="stopped")
        job_id = f"job_{chat_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        await message.reply_text("🛑 Avto-habar yuborish to'xtatildi!")

    elif text == "ℹ️ Holat":
        user_data = get_user(chat_id)
        status = "Ishlayapti ✅" if user_data.get('status') == 'running' else "To'xtatilgan 🛑"
        groups_count = len(user_data.get('groups', []))
        interval = user_data.get('interval', 60)
        
        msg_type = "Matn"
        if user_data.get('auto_message_id'):
            msg_type = "Media/Forward (Rasm, Video...)"
        
        await message.reply_text(
            f"📊 **Holat:** {status}\n"
            f"👥 **Guruhlar soni:** {groups_count} ta\n"
            f"⏱ **Interval:** Har {interval} daqiqada\n"
            f"✉️ **Habar turi:** {msg_type}",
            parse_mode=ParseMode.MARKDOWN
        )

# Resume jobs on startup
def startup_jobs():
    data = load_db() if os.path.exists("users_data.json") else {}
    for user_id_str, user_data in data.items():
        if user_data.get('status') == 'running':
            setup_job(int(user_id_str))

if __name__ == "__main__":
    import pyrogram
    import asyncio
    
    async def main_loop():
        await bot.start()
        print(f"Bot @{(await bot.get_me()).username} sifatida ishga tushdi!")
        scheduler.start()
        startup_jobs()
        await pyrogram.idle()
        await bot.stop()
        
    print("Bot is starting...")
    asyncio.run(main_loop())

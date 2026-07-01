import asyncio
import os
from pyrogram import Client, filters
from pyrogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeExpired, PhoneCodeInvalid
from pyrogram.raw.functions.messages import UpdateDialogFilter
from pyrogram.raw.types import DialogFilter
from pyrogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import config
from db import update_user, get_user, load_db

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

bot = Client("bot_session", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)
scheduler = AsyncIOScheduler()

user_clients = {}
user_states = {}

# ── Menyular ──────────────────────────────────────────────────────────────────

def login_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("🔐 Hisobga kirish")]], resize_keyboard=True)

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📁 Guruhlarni yangilash")],
        [KeyboardButton("✉️ Avto habar"), KeyboardButton("⏱ Vaqtni sozlash")],
        [KeyboardButton("📊 Boshqaruv paneli")],
        [KeyboardButton("🚪 Hisobdan chiqish")]
    ], resize_keyboard=True)

def cancel_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)

def control_panel_inline(status: str):
    is_running = status == "running"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 To'xtatish" if is_running else "▶️ Boshlash",
                                 callback_data="stop" if is_running else "start"),
        ],
        [InlineKeyboardButton("🔄 Guruhlarni yangilash", callback_data="refresh_groups")],
        [InlineKeyboardButton("🗑 Xabarni o'chirish", callback_data="clear_message")],
        [InlineKeyboardButton("❌ Yopish", callback_data="close_panel")]
    ])

def confirm_logout_inline():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha, chiqish", callback_data="confirm_logout"),
        InlineKeyboardButton("❌ Bekor", callback_data="close_panel")
    ]])

# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

def session_path(user_id):
    return os.path.join(SESSIONS_DIR, f"session_{user_id}")

def is_logged_in(user_id):
    return os.path.exists(f"{session_path(user_id)}.session")

async def get_or_create_client(user_id):
    if user_id in user_clients:
        try:
            if not user_clients[user_id].is_connected:
                await user_clients[user_id].connect()
            return user_clients[user_id]
        except Exception:
            del user_clients[user_id]

    path = session_path(user_id)
    if os.path.exists(f"{path}.session"):
        try:
            client = Client(path, config.API_ID, config.API_HASH)
            await client.connect()
            user_clients[user_id] = client
            return client
        except Exception:
            return None
    return None

MAX_FOLDER_PEERS = 100

async def fetch_groups(user_client):
    """Barcha guruhlarni to'liq yuklab oladi"""
    groups = []
    count = 0
    async for d in user_client.get_dialogs():
        count += 1
        if str(d.chat.type) in ["ChatType.GROUP", "ChatType.SUPERGROUP"]:
            groups.append(d.chat.id)
    return groups, count

async def save_folder(user_client, groups):
    """Guruhlarni jildga saqlaydi, 100 ta limit bilan"""
    limited = groups[:MAX_FOLDER_PEERS]
    
    async def resolve(g):
        try:
            return await user_client.resolve_peer(g)
        except Exception:
            return None

    results = await asyncio.gather(*[resolve(g) for g in limited])
    peers = [p for p in results if p is not None]

    filter_folder = DialogFilter(
        id=10,
        title="Avto Habar Guruhlar",
        pinned_peers=[],
        include_peers=peers,
        exclude_peers=[]
    )
    await user_client.invoke(UpdateDialogFilter(id=10, filter=filter_folder))
    return len(peers)

async def send_auto_message(user_id):
    user_data = get_user(user_id)
    if user_data.get("status") != "running":
        return

    client = await get_or_create_client(user_id)
    if not client:
        return

    message_id = user_data.get("auto_message_id")
    old_message = user_data.get("auto_message")
    groups = user_data.get("groups", [])
    if not groups or (not message_id and not old_message):
        return

    bot_id = int(config.BOT_TOKEN.split(":")[0])
    is_forward = user_data.get("is_forward", False)

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
            print(f"[{user_id}] Guruhga yuborishda xatolik {g}: {e}")

def setup_job(user_id):
    user_data = get_user(user_id)
    interval_minutes = max(1, int(user_data.get("interval", 60)))
    job_id = f"job_{user_id}"

    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if user_data.get("status") == "running":
        scheduler.add_job(send_auto_message, "interval", minutes=interval_minutes,
                          args=[user_id], id=job_id)

async def build_status_text(user_id):
    user_data = get_user(user_id)
    status = "🟢 Ishlayapti" if user_data.get("status") == "running" else "🔴 To'xtatilgan"
    groups_count = len(user_data.get("groups", []))
    interval = user_data.get("interval", 60)
    msg_type = "Yo'q ❌"
    if user_data.get("auto_message_id"):
        msg_type = "Media/Matn ✅"
    elif user_data.get("auto_message"):
        msg_type = "Matn ✅"

    return (
        f"📊 **Boshqaruv paneli**\n\n"
        f"**Holat:** {status}\n"
        f"**Guruhlar:** {groups_count} ta\n"
        f"**Interval:** Har {interval} daqiqada\n"
        f"**Xabar:** {msg_type}"
    )

# ── Xabar handleri ────────────────────────────────────────────────────────────

@bot.on_message(filters.private)
async def message_handler(client, message):
    chat_id = message.chat.id
    text = message.text or ""

    # /start
    if text == "/start":
        user_states.pop(chat_id, None)
        logged = is_logged_in(chat_id)
        await message.reply_text(
            "👋 **Avto-Habar Botiga Xush Kelibsiz!**\n\n"
            + ("Quyidagi menyu orqali botni boshqaring." if logged
               else "Boshlash uchun hisobingizga kiring."),
            reply_markup=main_menu() if logged else login_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Bekor qilish
    if text == "❌ Bekor qilish":
        state_info = user_states.pop(chat_id, {})
        # Agar client yaratilgan bo'lsa, disconnect qilamiz
        tmp_client = state_info.get("client")
        if tmp_client:
            try:
                await tmp_client.disconnect()
            except Exception:
                pass
        logged = is_logged_in(chat_id)
        await message.reply_text("Amal bekor qilindi.",
                                 reply_markup=main_menu() if logged else login_menu())
        return

    # State mashinasi
    state_info = user_states.get(chat_id, {})
    state = state_info.get("state")

    if state == "WAITING_PHONE":
        if message.contact:
            phone = message.contact.phone_number
            if not phone.startswith("+"):
                phone = "+" + phone
        else:
            phone = text.strip()
            if not phone or not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 10:
                await message.reply_text("❌ To'g'ri telefon raqam kiriting (masalan: +998901234567)")
                return

        await message.reply_text("⏳ Kod yuborilmoqda...", reply_markup=cancel_menu())
        try:
            user_client = Client(session_path(chat_id), config.API_ID, config.API_HASH)
            await user_client.connect()
            sent_code = await user_client.send_code(phone)
            user_states[chat_id] = {
                "state": "WAITING_CODE",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "client": user_client
            }
            await message.reply_text(
                "📲 Telegramdan kelgan **5 xonali kodni** kiriting:\n"
                "_(Masalan: `12345`)_",
                reply_markup=cancel_menu(),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}", reply_markup=login_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_CODE":
        code = text.replace(" ", "").replace(",", "").replace(".", "").strip()
        if not code.isdigit() or len(code) < 4:
            await message.reply_text("❌ Kod faqat raqamlardan iborat (4-6 ta). Qaytadan kiriting:")
            return

        state_data = user_states[chat_id]
        user_client = state_data["client"]
        phone = state_data["phone"]
        phone_code_hash = state_data["phone_code_hash"]

        try:
            await user_client.sign_in(phone, phone_code_hash, code)
            user_clients[chat_id] = user_client
            update_user(chat_id, phone=phone)
            user_states.pop(chat_id, None)
            await message.reply_text(
                "✅ Hisobga muvaffaqiyatli kirdingiz!\nEndi barcha funksiyalar mavjud.",
                reply_markup=main_menu()
            )
        except SessionPasswordNeeded:
            user_states[chat_id]["state"] = "WAITING_PASSWORD"
            await message.reply_text(
                "🔐 Ikki bosqichli himoya yoqilgan.\nParolni kiriting:",
                reply_markup=cancel_menu()
            )
        except (PhoneCodeInvalid, PhoneCodeExpired):
            await message.reply_text("❌ Kod noto'g'ri yoki muddati o'tgan. Qaytadan urinib ko'ring.",
                                     reply_markup=login_menu())
            user_states.pop(chat_id, None)
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}", reply_markup=login_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_PASSWORD":
        password = text.strip()
        if not password:
            await message.reply_text("❌ Parol bo'sh bo'lishi mumkin emas.")
            return
        user_client = user_states[chat_id]["client"]
        try:
            await user_client.check_password(password)
            user_clients[chat_id] = user_client
            update_user(chat_id, phone=user_states[chat_id]["phone"])
            user_states.pop(chat_id, None)
            await message.reply_text(
                "✅ Hisobga muvaffaqiyatli kirdingiz!",
                reply_markup=main_menu()
            )
        except Exception as e:
            await message.reply_text(f"❌ Noto'g'ri parol: {e}", reply_markup=login_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_AUTO_MESSAGE":
        is_forward = True if getattr(message, "forward_date", None) else False
        update_user(chat_id, auto_message_id=message.id, is_forward=is_forward, auto_message=None)
        user_states.pop(chat_id, None)
        await message.reply_text(
            "✅ Xabar saqlandi! (Matn, rasm, video, forward — barchasi qo'llab-quvvatlanadi)",
            reply_markup=main_menu()
        )
        return

    elif state == "WAITING_INTERVAL":
        val = text.strip()
        if not val.isdigit() or int(val) < 1:
            await message.reply_text("❌ Iltimos, 1 dan katta raqam kiriting (daqiqalarda):")
            return
        update_user(chat_id, interval=int(val))
        user_states.pop(chat_id, None)
        setup_job(chat_id)
        await message.reply_text(f"✅ Interval {val} daqiqaga o'rnatildi!", reply_markup=main_menu())
        return

    # ── Tugma handlerlari ─────────────────────────────────────────────────────

    if not is_logged_in(chat_id) and text != "🔐 Hisobga kirish":
        await message.reply_text("❌ Avval hisobga kiring.", reply_markup=login_menu())
        return

    if text == "🔐 Hisobga kirish":
        if is_logged_in(chat_id):
            await message.reply_text("✅ Siz allaqachon hisobga kirgansiz.", reply_markup=main_menu())
            return
        user_states[chat_id] = {"state": "WAITING_PHONE"}
        contact_menu = ReplyKeyboardMarkup([
            [KeyboardButton("📞 Raqamni yuborish", request_contact=True)],
            [KeyboardButton("❌ Bekor qilish")]
        ], resize_keyboard=True)
        await message.reply_text(
            "📱 Telefon raqamingizni kiriting yoki tugma orqali yuboring:",
            reply_markup=contact_menu
        )

    elif text == "📁 Guruhlarni yangilash":
        user_client = await get_or_create_client(chat_id)
        if not user_client:
            await message.reply_text("❌ Sessiya topilmadi. Qaytadan kiring.", reply_markup=login_menu())
            return

        msg = await message.reply_text("⏳ Guruhlar izlanmoqda...")
        try:
            groups, count = await fetch_groups(user_client)
        except Exception as e:
            await msg.edit_text(f"❌ Dialoglarni olishda xatolik: {e}")
            return

        if not groups:
            await msg.edit_text("❌ Sizda hech qanday guruh topilmadi.")
            return

        await msg.edit_text(f"⏳ {len(groups)} ta guruh topildi, jildga qo'shilmoqda...")
        try:
            saved = await save_folder(user_client, groups)
            update_user(chat_id, groups=groups)
            note = f"\n_(Jildga {saved} ta qo'shildi, limit: {MAX_FOLDER_PEERS})_" if len(groups) > MAX_FOLDER_PEERS else ""
            await msg.edit_text(
                f"✅ {len(groups)} ta guruh saqlandi!{note}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await msg.edit_text(f"❌ Jild yaratishda xatolik: {e}")

    elif text == "✉️ Avto habar":
        user_states[chat_id] = {"state": "WAITING_AUTO_MESSAGE"}
        await message.reply_text(
            "📨 Guruhlarga yuboriladigan xabarni yuboring:\n"
            "_(Matn, rasm, video yoki forward qilish mumkin)_",
            reply_markup=cancel_menu(),
            parse_mode=ParseMode.MARKDOWN
        )

    elif text == "⏱ Vaqtni sozlash":
        user_states[chat_id] = {"state": "WAITING_INTERVAL"}
        user_data = get_user(chat_id)
        current = user_data.get("interval", 60)
        await message.reply_text(
            f"⏱ Hozirgi interval: **{current} daqiqa**\n\nYangi intervalini daqiqalarda kiriting:",
            reply_markup=cancel_menu(),
            parse_mode=ParseMode.MARKDOWN
        )

    elif text == "📊 Boshqaruv paneli":
        user_data = get_user(chat_id)
        status_text = await build_status_text(chat_id)
        await message.reply_text(
            status_text,
            reply_markup=control_panel_inline(user_data.get("status", "stopped")),
            parse_mode=ParseMode.MARKDOWN
        )

    elif text == "🚪 Hisobdan chiqish":
        await message.reply_text(
            "⚠️ Hisobdan chiqmoqchimisiz?\nBarcha ma'lumotlar o'chiriladi.",
            reply_markup=confirm_logout_inline()
        )

# ── Callback handleri ─────────────────────────────────────────────────────────

@bot.on_callback_query()
async def callback_handler(client, callback_query):
    chat_id = callback_query.from_user.id
    data = callback_query.data

    if data == "close_panel":
        await callback_query.message.delete()
        return

    if data == "confirm_logout":
        # Clientni disconnect qilamiz
        if chat_id in user_clients:
            try:
                await user_clients[chat_id].disconnect()
            except Exception:
                pass
            del user_clients[chat_id]

        # Session faylini o'chiramiz
        session_file = f"{session_path(chat_id)}.session"
        journal_file = f"{session_path(chat_id)}.session-journal"
        for f in [session_file, journal_file]:
            if os.path.exists(f):
                os.remove(f)

        # Jobni to'xtatamiz
        job_id = f"job_{chat_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        update_user(chat_id, status="stopped", groups=[], auto_message_id=None,
                    auto_message=None, phone=None)
        user_states.pop(chat_id, None)

        await callback_query.message.edit_text("✅ Hisobdan muvaffaqiyatli chiqdingiz.")
        await bot.send_message(chat_id, "Qaytadan kirish uchun tugmani bosing.", reply_markup=login_menu())
        return

    if data == "start":
        user_data = get_user(chat_id)
        if not user_data.get("groups"):
            await callback_query.answer("❌ Avval guruhlarni yangilang!", show_alert=True)
            return
        if not user_data.get("auto_message_id") and not user_data.get("auto_message"):
            await callback_query.answer("❌ Avval xabar kiriting!", show_alert=True)
            return
        update_user(chat_id, status="running")
        setup_job(chat_id)
        status_text = await build_status_text(chat_id)
        await callback_query.message.edit_text(
            status_text,
            reply_markup=control_panel_inline("running"),
            parse_mode=ParseMode.MARKDOWN
        )
        await callback_query.answer("✅ Boshlandi!")
        return

    if data == "stop":
        update_user(chat_id, status="stopped")
        job_id = f"job_{chat_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        status_text = await build_status_text(chat_id)
        await callback_query.message.edit_text(
            status_text,
            reply_markup=control_panel_inline("stopped"),
            parse_mode=ParseMode.MARKDOWN
        )
        await callback_query.answer("🛑 To'xtatildi!")
        return

    if data == "refresh_groups":
        user_client = await get_or_create_client(chat_id)
        if not user_client:
            await callback_query.answer("❌ Sessiya topilmadi!", show_alert=True)
            return
        await callback_query.answer("⏳ Yangilanmoqda...")
        try:
            groups, _ = await fetch_groups(user_client)
        except Exception as e:
            await callback_query.answer(f"❌ Xatolik: {e}", show_alert=True)
            return

        if not groups:
            await callback_query.answer("❌ Guruh topilmadi!", show_alert=True)
            return

        try:
            await save_folder(user_client, groups)
            update_user(chat_id, groups=groups)
        except Exception:
            pass

        status_text = await build_status_text(chat_id)
        user_data = get_user(chat_id)
        note = f"\n_(Jildga {MAX_FOLDER_PEERS} ta, hammasi: {len(groups)} ta)_" if len(groups) > MAX_FOLDER_PEERS else ""
        await callback_query.message.edit_text(
            status_text + f"\n\n✅ {len(groups)} ta guruh yangilandi!{note}",
            reply_markup=control_panel_inline(user_data.get("status", "stopped")),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "clear_message":
        update_user(chat_id, auto_message_id=None, auto_message=None, is_forward=False)
        status_text = await build_status_text(chat_id)
        user_data = get_user(chat_id)
        await callback_query.message.edit_text(
            status_text,
            reply_markup=control_panel_inline(user_data.get("status", "stopped")),
            parse_mode=ParseMode.MARKDOWN
        )
        await callback_query.answer("🗑 Xabar o'chirildi!")
        return

    await callback_query.answer()

# ── Startup ───────────────────────────────────────────────────────────────────

def startup_jobs():
    data = load_db() if os.path.exists("users_data.json") else {}
    for user_id_str, user_data in data.items():
        if user_data.get("status") == "running":
            setup_job(int(user_id_str))

if __name__ == "__main__":
    import pyrogram

    async def main_loop():
        await bot.start()
        print(f"Bot @{(await bot.get_me()).username} sifatida ishga tushdi!")
        scheduler.start()
        startup_jobs()
        await pyrogram.idle()
        await bot.stop()

    print("Bot is starting...")
    loop.run_until_complete(main_loop())

import asyncio
import os
import pyrogram.raw.functions.messages
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

MAX_FOLDER_PEERS = 100
PAGE_SIZE = 8  # bir sahifada nechta guruh

# ── Menyular ──────────────────────────────────────────────────────────────────

def login_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("🔐 Hisobga kirish")]], resize_keyboard=True)

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📁 Guruhlarni tanlash")],
        [KeyboardButton("✉️ Avto habar"), KeyboardButton("⏱ Vaqtni sozlash")],
        [KeyboardButton("📊 Boshqaruv paneli")],
        [KeyboardButton("🚪 Hisobdan chiqish")]
    ], resize_keyboard=True)

def cancel_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)

def control_panel_inline(status: str):
    is_running = status == "running"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 To'xtatish" if is_running else "▶️ Boshlash",
                              callback_data="stop" if is_running else "start")],
        [InlineKeyboardButton("📁 Guruhlarni tanlash", callback_data="open_groups")],
        [InlineKeyboardButton("🗑 Xabarni o'chirish", callback_data="clear_message")],
        [InlineKeyboardButton("❌ Yopish", callback_data="close_panel")]
    ])

def confirm_logout_inline():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha, chiqish", callback_data="confirm_logout"),
        InlineKeyboardButton("❌ Bekor", callback_data="close_panel")
    ]])

def groups_page_inline(all_groups, selected_ids, page=0):
    """Guruhlar sahifasi — checkbox ko'rinishida"""
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_groups = all_groups[start:end]
    total_pages = (len(all_groups) + PAGE_SIZE - 1) // PAGE_SIZE

    rows = []
    for gid, gname in page_groups:
        check = "✅" if gid in selected_ids else "☑️"
        title = gname[:28] if len(gname) > 28 else gname
        rows.append([InlineKeyboardButton(f"{check} {title}", callback_data=f"gtoggle_{gid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"gpage_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="gnoop"))
    if end < len(all_groups):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"gpage_{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(f"✅ Saqlash ({len(selected_ids)} ta)", callback_data="gsave"),
        InlineKeyboardButton("❌ Bekor", callback_data="gcancel")
    ])
    return InlineKeyboardMarkup(rows)

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

async def fetch_groups(user_client):
    """(id, name) juftliklari ro'yxatini qaytaradi — tez usul"""
    groups = []
    offset_date = 0
    offset_id = 0
    offset_peer = await user_client.resolve_peer("me")

    while True:
        r = await user_client.invoke(
            pyrogram.raw.functions.messages.GetDialogs(
                offset_date=offset_date,
                offset_id=offset_id,
                offset_peer=offset_peer,
                limit=100,
                hash=0
            )
        )
        if not hasattr(r, "dialogs") or not r.dialogs:
            break

        chats = {c.id: c for c in r.chats}
        for d in r.dialogs:
            peer = d.peer
            if hasattr(peer, "chat_id"):
                cid = peer.chat_id
                chat = chats.get(cid)
                if chat and not getattr(chat, "deactivated", False) and not getattr(chat, "left", False):
                    groups.append((-cid, getattr(chat, "title", str(cid))))
            elif hasattr(peer, "channel_id"):
                cid = peer.channel_id
                chat = chats.get(cid)
                if chat and getattr(chat, "megagroup", False) and not getattr(chat, "left", False):
                    groups.append((-cid, getattr(chat, "title", str(cid))))

        if len(r.dialogs) < 100:
            break

        last = r.messages[-1] if r.messages else None
        if last:
            offset_date = last.date
            offset_id = last.id
            last_peer = r.dialogs[-1].peer
            try:
                if hasattr(last_peer, "channel_id"):
                    offset_peer = await user_client.resolve_peer(-last_peer.channel_id)
                elif hasattr(last_peer, "chat_id"):
                    offset_peer = await user_client.resolve_peer(-last_peer.chat_id)
                elif hasattr(last_peer, "user_id"):
                    offset_peer = await user_client.resolve_peer(last_peer.user_id)
            except Exception:
                pass
        else:
            break

    return groups

async def save_folder(user_client, group_ids):
    """Jild yaratishga harakat qiladi, xato bo'lsa None qaytaradi"""
    async def resolve(g):
        try:
            return await user_client.resolve_peer(g)
        except Exception:
            return None

    results = await asyncio.gather(*[resolve(g) for g in group_ids])
    peers = [p for p in results if p is not None]
    if not peers:
        return None

    limit = min(len(peers), 100)
    while limit > 0:
        try:
            await user_client.invoke(UpdateDialogFilter(
                id=10,
                filter=DialogFilter(
                    id=10, title="Avto Habar Guruhlar",
                    pinned_peers=[], include_peers=peers[:limit], exclude_peers=[]
                )
            ))
            return limit
        except Exception:
            limit -= 10
    return None

async def send_auto_message(user_id):
    user_data = get_user(user_id)
    if user_data.get("status") != "running":
        return

    client = await get_or_create_client(user_id)
    if not client:
        print(f"[{user_id}] Client topilmadi")
        return

    groups = user_data.get("groups", [])
    if not groups:
        print(f"[{user_id}] Guruhlar bo'sh")
        return

    message_id = user_data.get("auto_message_id")
    text = user_data.get("auto_message", "")
    has_media = user_data.get("has_media", False)
    is_forward = user_data.get("is_forward", False)
    bot_id = int(config.BOT_TOKEN.split(":")[0])

    if not message_id and not text:
        print(f"[{user_id}] Xabar yo'q")
        return

    print(f"[{user_id}] Yuborish: {len(groups)} guruh, media={has_media}, text='{text[:30]}'")
    ok, fail = 0, 0

    for g in groups:
        try:
            if has_media and message_id:
                # Media xabarni forward yoki copy
                if is_forward:
                    await client.forward_messages(chat_id=g, from_chat_id=bot_id, message_ids=message_id)
                else:
                    await client.copy_message(chat_id=g, from_chat_id=bot_id, message_id=message_id)
            elif text:
                await client.send_message(g, text)
            elif message_id:
                # Oxirgi urinish — forward
                await client.forward_messages(chat_id=g, from_chat_id=bot_id, message_ids=message_id)
            ok += 1
            await asyncio.sleep(1)
        except Exception as e:
            fail += 1
            print(f"[{user_id}] Xatolik {g}: {e}")

    print(f"[{user_id}] Natija: {ok} ok, {fail} xato")

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

async def open_group_selector(user_id, target_message):
    """Guruh tanlash sahifasini ochadi yoki yangilaydi"""
    user_client = await get_or_create_client(user_id)
    if not user_client:
        await target_message.edit_text("❌ Sessiya topilmadi. Qaytadan kiring.")
        return

    await target_message.edit_text("⏳ Guruhlar yuklanmoqda...")
    try:
        all_groups = await fetch_groups(user_client)
    except Exception as e:
        await target_message.edit_text(f"❌ Xatolik: {e}")
        return

    if not all_groups:
        await target_message.edit_text("❌ Sizda hech qanday guruh topilmadi.")
        return

    # Avval saqlangan guruhlarni tanlangan deb belgilaymiz
    saved = set(get_user(user_id).get("groups", []))
    selected = saved & {gid for gid, _ in all_groups}

    user_states[user_id] = {
        "state": "SELECTING_GROUPS",
        "all_groups": all_groups,
        "selected": selected,
        "page": 0
    }

    await target_message.edit_text(
        f"📋 **Guruhlarni tanlang** ({len(all_groups)} ta topildi)\n"
        f"✅ — tanlangan, ☑️ — tanlanmagan\n"
        f"Tugagach **Saqlash** tugmasini bosing:",
        reply_markup=groups_page_inline(all_groups, selected, 0),
        parse_mode=ParseMode.MARKDOWN
    )

# ── Xabar handleri ────────────────────────────────────────────────────────────

@bot.on_message(filters.private)
async def message_handler(client, message):
    chat_id = message.chat.id
    text = message.text or ""

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

    if text == "❌ Bekor qilish":
        state_info = user_states.pop(chat_id, {})
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
                "📲 Telegramdan kelgan **5 xonali kodni** kiriting:\n_(Masalan: `12345`)_",
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
            await message.reply_text("🔐 Ikki bosqichli himoya yoqilgan.\nParolni kiriting:",
                                     reply_markup=cancel_menu())
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
            await message.reply_text("✅ Hisobga muvaffaqiyatli kirdingiz!", reply_markup=main_menu())
        except Exception as e:
            await message.reply_text(f"❌ Noto'g'ri parol: {e}", reply_markup=login_menu())
            user_states.pop(chat_id, None)
        return

    elif state == "WAITING_AUTO_MESSAGE":
        is_forward = True if getattr(message, "forward_date", None) else False
        # Xabarni to'liq saqlash: matn, caption, media
        msg_data = {
            "message_id": message.id,
            "is_forward": is_forward,
            "text": message.text or message.caption or "",
            "has_media": bool(message.media),
        }
        update_user(chat_id,
                    auto_message_id=message.id,
                    auto_message=message.text or message.caption or "",
                    is_forward=is_forward,
                    has_media=bool(message.media))
        user_states.pop(chat_id, None)
        await message.reply_text(
            "✅ Xabar saqlandi!",
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
        await message.reply_text("📱 Telefon raqamingizni kiriting yoki tugma orqali yuboring:",
                                 reply_markup=contact_menu)

    elif text == "📁 Guruhlarni tanlash":
        user_client = await get_or_create_client(chat_id)
        if not user_client:
            await message.reply_text("❌ Sessiya topilmadi. Qaytadan kiring.", reply_markup=login_menu())
            return
        msg = await message.reply_text("⏳ Yuklanmoqda...")
        await open_group_selector(chat_id, msg)

    elif text == "✉️ Avto habar":
        user_states[chat_id] = {"state": "WAITING_AUTO_MESSAGE"}
        await message.reply_text(
            "📨 Guruhlarga yuboriladigan xabarni yuboring:\n_(Matn, rasm, video yoki forward)_",
            reply_markup=cancel_menu(),
            parse_mode=ParseMode.MARKDOWN
        )

    elif text == "⏱ Vaqtni sozlash":
        user_states[chat_id] = {"state": "WAITING_INTERVAL"}
        current = get_user(chat_id).get("interval", 60)
        await message.reply_text(
            f"⏱ Hozirgi interval: **{current} daqiqa**\n\nYangi intervalini daqiqalarda kiriting:",
            reply_markup=cancel_menu(),
            parse_mode=ParseMode.MARKDOWN
        )

    elif text == "📊 Boshqaruv paneli":
        user_data = get_user(chat_id)
        await message.reply_text(
            await build_status_text(chat_id),
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

    # ── Guruh tanlash callbacklari ────────────────────────────────────────────

    if data == "open_groups":
        await open_group_selector(chat_id, callback_query.message)
        await callback_query.answer()
        return

    if data.startswith("gtoggle_"):
        state_info = user_states.get(chat_id, {})
        if state_info.get("state") != "SELECTING_GROUPS":
            await callback_query.answer("Sessiya tugagan. Qaytadan oching.", show_alert=True)
            return
        gid = int(data.split("_", 1)[1])
        selected = state_info["selected"]
        if gid in selected:
            selected.discard(gid)
        else:
            if len(selected) >= MAX_FOLDER_PEERS:
                await callback_query.answer(f"❌ Maksimum {MAX_FOLDER_PEERS} ta tanlash mumkin!", show_alert=True)
                return
            selected.add(gid)
        await callback_query.message.edit_reply_markup(
            reply_markup=groups_page_inline(state_info["all_groups"], selected, state_info["page"])
        )
        await callback_query.answer()
        return

    if data.startswith("gpage_"):
        state_info = user_states.get(chat_id, {})
        if state_info.get("state") != "SELECTING_GROUPS":
            await callback_query.answer()
            return
        page = int(data.split("_", 1)[1])
        state_info["page"] = page
        await callback_query.message.edit_reply_markup(
            reply_markup=groups_page_inline(state_info["all_groups"], state_info["selected"], page)
        )
        await callback_query.answer()
        return

    if data == "gnoop":
        await callback_query.answer()
        return

    if data == "gsave":
        state_info = user_states.pop(chat_id, {})
        if state_info.get("state") != "SELECTING_GROUPS":
            await callback_query.answer("Sessiya tugagan.", show_alert=True)
            return

        selected = list(state_info["selected"])
        if not selected:
            await callback_query.answer("❌ Hech bo'lmaganda 1 ta guruh tanlang!", show_alert=True)
            user_states[chat_id] = state_info  # qaytaramiz
            return

        user_client = await get_or_create_client(chat_id)
        await callback_query.message.edit_text(f"⏳ {len(selected)} ta guruh jildga qo'shilmoqda...")
        try:
            update_user(chat_id, groups=selected)
            saved = await save_folder(user_client, selected)
            if saved is None:
                await callback_query.message.edit_text(
                    f"✅ {len(selected)} ta guruh saqlandi! Xabar yuborish ishlaydi.\n"
                    f"_(Telegram jild yaratishga ruxsat bermadi — bu normal)_",
                    parse_mode=ParseMode.MARKDOWN
                )
            elif saved < len(selected):
                await callback_query.message.edit_text(
                    f"✅ {len(selected)} ta guruh saqlandi! Xabar yuborish ishlaydi.\n"
                    f"_(Jildga {saved} ta qo'shildi)_",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await callback_query.message.edit_text(
                    f"✅ {len(selected)} ta guruh saqlandi va jildga qo'shildi!"
                )
        except Exception as e:
            await callback_query.message.edit_text(f"❌ Jild yaratishda xatolik: {e}")
        await callback_query.answer()
        return

    if data == "gcancel":
        user_states.pop(chat_id, None)
        await callback_query.message.delete()
        await callback_query.answer("Bekor qilindi.")
        return

    # ── Boshqaruv paneli callbacklari ─────────────────────────────────────────

    if data == "close_panel":
        await callback_query.message.delete()
        return

    if data == "confirm_logout":
        if chat_id in user_clients:
            try:
                await user_clients[chat_id].disconnect()
            except Exception:
                pass
            del user_clients[chat_id]

        for ext in [".session", ".session-journal"]:
            f = session_path(chat_id) + ext
            if os.path.exists(f):
                os.remove(f)

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
            await callback_query.answer("❌ Avval guruhlarni tanlang!", show_alert=True)
            return
        if not user_data.get("auto_message_id") and not user_data.get("auto_message"):
            await callback_query.answer("❌ Avval xabar kiriting!", show_alert=True)
            return
        update_user(chat_id, status="running")
        setup_job(chat_id)
        await callback_query.message.edit_text(
            await build_status_text(chat_id),
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
        await callback_query.message.edit_text(
            await build_status_text(chat_id),
            reply_markup=control_panel_inline("stopped"),
            parse_mode=ParseMode.MARKDOWN
        )
        await callback_query.answer("🛑 To'xtatildi!")
        return

    if data == "clear_message":
        update_user(chat_id, auto_message_id=None, auto_message=None, is_forward=False)
        user_data = get_user(chat_id)
        await callback_query.message.edit_text(
            await build_status_text(chat_id),
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

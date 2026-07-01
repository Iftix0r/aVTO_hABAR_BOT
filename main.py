import asyncio
import os
import pyrogram.raw.functions.messages
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeExpired, PhoneCodeInvalid
from pyrogram.raw.functions.messages import UpdateDialogFilter
from pyrogram.raw.types import DialogFilter
from pyrogram.enums import ParseMode
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
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

PAGE_SIZE = 8

# ── Inline menyular ───────────────────────────────────────────────────────────

def main_inline(is_logged=True):
    if not is_logged:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔐 Hisobga kirish", callback_data="login")
        ]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Guruhlarni tanlash", callback_data="open_groups")],
        [InlineKeyboardButton("✉️ Xabar kiritish", callback_data="set_message"),
         InlineKeyboardButton("⏱ Interval", callback_data="set_interval")],
        [InlineKeyboardButton("📊 Holat", callback_data="status")],
        [InlineKeyboardButton("🚪 Chiqish", callback_data="logout_ask")]
    ])

def status_inline(status):
    running = status == "running"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 To'xtatish" if running else "▶️ Boshlash",
                              callback_data="stop" if running else "start")],
        [InlineKeyboardButton("📁 Guruhlarni tanlash", callback_data="open_groups"),
         InlineKeyboardButton("✉️ Xabar", callback_data="set_message")],
        [InlineKeyboardButton("⏱ Interval", callback_data="set_interval"),
         InlineKeyboardButton("🗑 Xabarni o'chir", callback_data="clear_msg")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]
    ])

def confirm_inline(yes_cb, no_cb="main_menu"):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha", callback_data=yes_cb),
        InlineKeyboardButton("❌ Yo'q", callback_data=no_cb)
    ]])

def cancel_inline():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_state")
    ]])

def groups_inline(all_groups, selected, page):
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    total_pages = max(1, (len(all_groups) + PAGE_SIZE - 1) // PAGE_SIZE)
    rows = []
    for gid, gname in all_groups[start:end]:
        mark = "✅" if gid in selected else "☑️"
        rows.append([InlineKeyboardButton(
            f"{mark} {gname[:30]}", callback_data=f"gt_{gid}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"gp_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="gnoop"))
    if end < len(all_groups):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"gp_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(f"💾 Saqlash ({len(selected)} ta)", callback_data="gsave"),
        InlineKeyboardButton("❌ Bekor", callback_data="gcancel")
    ])
    return InlineKeyboardMarkup(rows)

# ── Yordamchi ─────────────────────────────────────────────────────────────────

def session_path(uid):
    return os.path.join(SESSIONS_DIR, f"session_{uid}")

def is_logged(uid):
    return os.path.exists(f"{session_path(uid)}.session")

async def get_client(uid):
    if uid in user_clients:
        try:
            if not user_clients[uid].is_connected:
                await user_clients[uid].connect()
            return user_clients[uid]
        except Exception:
            del user_clients[uid]
    path = session_path(uid)
    if os.path.exists(f"{path}.session"):
        try:
            c = Client(path, config.API_ID, config.API_HASH)
            await c.connect()
            user_clients[uid] = c
            return c
        except Exception:
            return None
    return None

async def fetch_groups(client):
    groups = []
    offset_date, offset_id = 0, 0
    offset_peer = await client.resolve_peer("me")
    while True:
        r = await client.invoke(
            pyrogram.raw.functions.messages.GetDialogs(
                offset_date=offset_date, offset_id=offset_id,
                offset_peer=offset_peer, limit=100, hash=0
            )
        )
        if not getattr(r, "dialogs", None):
            break
        chats = {c.id: c for c in r.chats}
        for d in r.dialogs:
            p = d.peer
            if hasattr(p, "chat_id"):
                c = chats.get(p.chat_id)
                if c and not getattr(c, "deactivated", False) and not getattr(c, "left", False):
                    groups.append((-p.chat_id, c.title or str(p.chat_id)))
            elif hasattr(p, "channel_id"):
                c = chats.get(p.channel_id)
                if c and getattr(c, "megagroup", False) and not getattr(c, "left", False):
                    groups.append((int(f"-100{p.channel_id}"), c.title or str(p.channel_id)))
        if len(r.dialogs) < 100:
            break
        last = r.messages[-1] if r.messages else None
        if not last:
            break
        offset_date, offset_id = last.date, last.id
        lp = r.dialogs[-1].peer
        try:
            if hasattr(lp, "channel_id"):
                offset_peer = await client.resolve_peer(-lp.channel_id)
            elif hasattr(lp, "chat_id"):
                offset_peer = await client.resolve_peer(-lp.chat_id)
            elif hasattr(lp, "user_id"):
                offset_peer = await client.resolve_peer(lp.user_id)
        except Exception:
            break
    return groups

async def try_save_folder(client, group_ids):
    async def res(g):
        try:
            return await client.resolve_peer(g)
        except Exception:
            return None
    peers = [p for p in await asyncio.gather(*[res(g) for g in group_ids]) if p]
    if not peers:
        return 0
    limit = min(len(peers), 100)
    while limit > 0:
        try:
            await client.invoke(UpdateDialogFilter(
                id=10, filter=DialogFilter(
                    id=10, title="Avto Habar Guruhlar",
                    pinned_peers=[], include_peers=peers[:limit], exclude_peers=[]
                )
            ))
            return limit
        except Exception:
            limit -= 10
    return 0

async def status_text(uid):
    d = get_user(uid)
    st = "🟢 Ishlayapti" if d.get("status") == "running" else "🔴 To'xtatilgan"
    has_msg = bool(d.get("auto_message") or d.get("auto_message_id"))
    return (
        f"📊 **Holat:** {st}\n"
        f"👥 **Guruhlar:** {len(d.get('groups', []))} ta\n"
        f"⏱ **Interval:** {d.get('interval', 60)} daqiqa\n"
        f"✉️ **Xabar:** {'✅' if has_msg else '❌'}"
    )

def setup_job(uid):
    d = get_user(uid)
    jid = f"job_{uid}"
    if scheduler.get_job(jid):
        scheduler.remove_job(jid)
    if d.get("status") == "running":
        mins = max(1, int(d.get("interval", 60)))
        scheduler.add_job(send_messages, "interval", minutes=mins, args=[uid], id=jid)
        print(f"[job] {uid} uchun job qo'shildi, interval={mins} daqiqa")

async def send_messages(uid):
    d = get_user(uid)
    if d.get("status") != "running":
        return
    client = await get_client(uid)
    if not client:
        print(f"[{uid}] client yo'q")
        return
    groups = d.get("groups", [])
    text = d.get("auto_message") or ""
    msg_id = d.get("auto_message_id")
    has_media = d.get("has_media", False)
    is_fwd = d.get("is_forward", False)
    bot_id = int(config.BOT_TOKEN.split(":")[0])

    if not groups:
        print(f"[{uid}] guruh yo'q")
        return
    if not text and not msg_id:
        print(f"[{uid}] xabar yo'q")
        return

    print(f"[{uid}] yuborish: {len(groups)} guruh, text='{text[:20]}', media={has_media}")
    ok = fail = 0
    errors = []
    for g in groups:
        try:
            if text and not has_media:
                await client.send_message(g, text)
            elif msg_id:
                if is_fwd:
                    await client.forward_messages(chat_id=g, from_chat_id=bot_id, message_ids=msg_id)
                else:
                    await client.copy_message(chat_id=g, from_chat_id=bot_id, message_id=msg_id)
            ok += 1
            await asyncio.sleep(1)
        except Exception as e:
            fail += 1
            err_str = str(e).split("\n")[0][:80]
            if err_str not in errors:
                errors.append(err_str)
            print(f"[{uid}] xatolik {g}: {e}")
    print(f"[{uid}] natija: {ok} ok, {fail} xato")
    try:
        if ok > 0 and fail == 0:
            await bot.send_message(uid, f"✅ Xabar {ok} ta guruhga yuborildi!")
        elif ok > 0:
            await bot.send_message(uid,
                f"✅ {ok} ta guruhga yuborildi, {fail} ta xato:\n" +
                "\n".join(f"• `{e}`" for e in errors),
                parse_mode=ParseMode.MARKDOWN)
        else:
            await bot.send_message(uid,
                f"❌ Xabar yuborilmadi! Xato sababi:\n" +
                "\n".join(f"• `{e}`" for e in errors),
                parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

# ── /start va xabar handleri ──────────────────────────────────────────────────

@bot.on_message(filters.private)
async def on_message(client, message):
    uid = message.chat.id
    text = message.text or ""

    if text == "/start":
        user_states.pop(uid, None)
        logged = is_logged(uid)
        await message.reply_text(
            "👋 **Avto-Habar Botiga Xush Kelibsiz!**\n\n"
            + ("Quyidagi tugmalar orqali boshqaring:" if logged else "Boshlash uchun hisobga kiring:"),
            reply_markup=main_inline(logged),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    state = user_states.get(uid, {}).get("state")

    # Login holatlari
    if state == "WAIT_PHONE":
        if message.contact:
            phone = message.contact.phone_number
            if not phone.startswith("+"):
                phone = "+" + phone
        else:
            phone = text.strip()
            if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 10:
                await message.reply_text("❌ To'g'ri raqam kiriting: +998901234567",
                                         reply_markup=cancel_inline())
                return
        await message.reply_text("⏳ Kod yuborilmoqda...")
        try:
            uc = Client(session_path(uid), config.API_ID, config.API_HASH)
            await uc.connect()
            sent = await uc.send_code(phone)
            user_states[uid] = {"state": "WAIT_CODE", "phone": phone,
                                 "hash": sent.phone_code_hash, "client": uc}
            await message.reply_text(
                "📲 Telegramdan kelgan kodni kiriting:\n_(masalan: `12345`)_",
                reply_markup=cancel_inline(), parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}", reply_markup=main_inline(False))
            user_states.pop(uid, None)
        return

    if state == "WAIT_CODE":
        code = text.replace(" ", "").replace(",", "").strip()
        if not code.isdigit() or len(code) < 4:
            await message.reply_text("❌ Kod noto'g'ri. Qaytadan kiriting:", reply_markup=cancel_inline())
            return
        st = user_states[uid]
        try:
            await st["client"].sign_in(st["phone"], st["hash"], code)
            user_clients[uid] = st["client"]
            update_user(uid, phone=st["phone"])
            user_states.pop(uid, None)
            await message.reply_text("✅ Muvaffaqiyatli kirdingiz!", reply_markup=main_inline(True))
        except SessionPasswordNeeded:
            user_states[uid]["state"] = "WAIT_PASS"
            await message.reply_text("🔐 2FA parolini kiriting:", reply_markup=cancel_inline())
        except (PhoneCodeInvalid, PhoneCodeExpired):
            await message.reply_text("❌ Kod noto'g'ri yoki eskirgan.", reply_markup=main_inline(False))
            user_states.pop(uid, None)
        except Exception as e:
            await message.reply_text(f"❌ Xatolik: {e}", reply_markup=main_inline(False))
            user_states.pop(uid, None)
        return

    if state == "WAIT_PASS":
        try:
            await user_states[uid]["client"].check_password(text.strip())
            user_clients[uid] = user_states[uid]["client"]
            update_user(uid, phone=user_states[uid]["phone"])
            user_states.pop(uid, None)
            await message.reply_text("✅ Muvaffaqiyatli kirdingiz!", reply_markup=main_inline(True))
        except Exception as e:
            await message.reply_text(f"❌ Noto'g'ri parol: {e}", reply_markup=main_inline(False))
            user_states.pop(uid, None)
        return

    if state == "WAIT_MSG":
        is_fwd = bool(getattr(message, "forward_date", None))
        has_media = bool(message.media)
        msg_text = message.text or message.caption or ""
        update_user(uid, auto_message_id=message.id, auto_message=msg_text,
                    has_media=has_media, is_forward=is_fwd)
        user_states.pop(uid, None)
        await message.reply_text("✅ Xabar saqlandi!", reply_markup=main_inline(True))
        return

    if state == "WAIT_INTERVAL":
        if not text.isdigit() or int(text) < 1:
            await message.reply_text("❌ 1 dan katta raqam kiriting:", reply_markup=cancel_inline())
            return
        update_user(uid, interval=int(text))
        setup_job(uid)
        user_states.pop(uid, None)
        await message.reply_text(f"✅ Interval {text} daqiqaga o'rnatildi!", reply_markup=main_inline(True))
        return

    # Kontakt yuborilsa (telefon raqam)
    if message.contact and state == "WAIT_PHONE":
        return  # yuqorida handle qilingan

# ── Callback handleri ─────────────────────────────────────────────────────────

@bot.on_callback_query()
async def on_callback(client, cq):
    uid = cq.from_user.id
    data = cq.data

    async def edit(text, markup=None, md=True):
        try:
            await cq.message.edit_text(text, reply_markup=markup,
                                       parse_mode=ParseMode.MARKDOWN if md else None)
        except Exception:
            pass

    if data == "main_menu":
        logged = is_logged(uid)
        await edit(
            "👋 **Avto-Habar Bot**\n\nQuyidagi tugmalar orqali boshqaring:" if logged
            else "Boshlash uchun hisobga kiring:",
            main_inline(logged)
        )
        await cq.answer()
        return

    if data == "cancel_state":
        st = user_states.pop(uid, {})
        c = st.get("client")
        if c:
            try:
                await c.disconnect()
            except Exception:
                pass
        logged = is_logged(uid)
        await edit("Bekor qilindi.", main_inline(logged))
        await cq.answer()
        return

    if data == "login":
        if is_logged(uid):
            await edit("✅ Allaqachon kirgansiz.", main_inline(True))
            await cq.answer()
            return
        user_states[uid] = {"state": "WAIT_PHONE"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel_state")]])
        # Kontakt tugmasi faqat reply keyboard orqali ishlaydi
        from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
        contact_kb = ReplyKeyboardMarkup([
            [KeyboardButton("📞 Raqamni yuborish", request_contact=True)],
        ], resize_keyboard=True, one_time_keyboard=True)
        await cq.message.reply_text(
            "📱 Telefon raqamingizni yuboring yoki yozing (+998...):",
            reply_markup=contact_kb
        )
        await cq.answer()
        return

    if data == "status":
        d = get_user(uid)
        await edit(await status_text(uid), status_inline(d.get("status", "stopped")))
        await cq.answer()
        return

    if data == "set_message":
        if not is_logged(uid):
            await cq.answer("❌ Avval hisobga kiring!", show_alert=True)
            return
        user_states[uid] = {"state": "WAIT_MSG"}
        await edit("📨 Guruhlarga yuboriladigan xabarni yuboring:\n_(matn, rasm, video yoki forward)_",
                   cancel_inline())
        await cq.answer()
        return

    if data == "set_interval":
        d = get_user(uid)
        user_states[uid] = {"state": "WAIT_INTERVAL"}
        await edit(f"⏱ Hozirgi interval: **{d.get('interval', 60)} daqiqa**\n\nYangi qiymat kiriting:",
                   cancel_inline())
        await cq.answer()
        return

    if data == "start":
        d = get_user(uid)
        if not d.get("groups"):
            await cq.answer("❌ Avval guruhlarni tanlang!", show_alert=True)
            return
        if not d.get("auto_message") and not d.get("auto_message_id"):
            await cq.answer("❌ Avval xabar kiriting!", show_alert=True)
            return
        update_user(uid, status="running")
        setup_job(uid)
        await edit(await status_text(uid), status_inline("running"))
        await cq.answer("✅ Boshlandi!")
        return

    if data == "stop":
        update_user(uid, status="stopped")
        jid = f"job_{uid}"
        if scheduler.get_job(jid):
            scheduler.remove_job(jid)
        await edit(await status_text(uid), status_inline("stopped"))
        await cq.answer("🛑 To'xtatildi!")
        return

    if data == "clear_msg":
        update_user(uid, auto_message=None, auto_message_id=None, has_media=False, is_forward=False)
        await edit(await status_text(uid), status_inline(get_user(uid).get("status", "stopped")))
        await cq.answer("🗑 Xabar o'chirildi!")
        return

    if data == "logout_ask":
        await edit("⚠️ Hisobdan chiqmoqchimisiz?", confirm_inline("logout_yes", "main_menu"))
        await cq.answer()
        return

    if data == "logout_yes":
        if uid in user_clients:
            try:
                await user_clients[uid].disconnect()
            except Exception:
                pass
            del user_clients[uid]
        for ext in [".session", ".session-journal"]:
            f = session_path(uid) + ext
            if os.path.exists(f):
                os.remove(f)
        jid = f"job_{uid}"
        if scheduler.get_job(jid):
            scheduler.remove_job(jid)
        update_user(uid, status="stopped", groups=[], auto_message=None,
                    auto_message_id=None, phone=None)
        user_states.pop(uid, None)
        await edit("✅ Hisobdan chiqdingiz.", main_inline(False))
        await cq.answer()
        return

    # ── Guruh tanlash ─────────────────────────────────────────────────────────

    if data == "open_groups":
        if not is_logged(uid):
            await cq.answer("❌ Avval hisobga kiring!", show_alert=True)
            return
        uc = await get_client(uid)
        if not uc:
            await cq.answer("❌ Sessiya topilmadi!", show_alert=True)
            return
        await edit("⏳ Guruhlar yuklanmoqda...")
        try:
            all_groups = await fetch_groups(uc)
        except Exception as e:
            await edit(f"❌ Xatolik: {e}", main_inline(True))
            await cq.answer()
            return
        if not all_groups:
            await edit("❌ Guruh topilmadi.", main_inline(True))
            await cq.answer()
            return
        saved = set(get_user(uid).get("groups", []))
        selected = saved & {g[0] for g in all_groups}
        user_states[uid] = {"state": "SEL_GROUPS", "all": all_groups,
                             "sel": selected, "page": 0}
        await edit(
            f"📋 **Guruhlarni tanlang** ({len(all_groups)} ta)\n✅ tanlangan | ☑️ tanlanmagan",
            groups_inline(all_groups, selected, 0)
        )
        await cq.answer()
        return

    if data.startswith("gt_"):
        st = user_states.get(uid, {})
        if st.get("state") != "SEL_GROUPS":
            await cq.answer("Qaytadan oching.", show_alert=True)
            return
        gid = int(data[3:])
        sel = st["sel"]
        if gid in sel:
            sel.discard(gid)
        else:
            sel.add(gid)
        try:
            await cq.message.edit_reply_markup(groups_inline(st["all"], sel, st["page"]))
        except Exception:
            pass
        await cq.answer()
        return

    if data.startswith("gp_"):
        st = user_states.get(uid, {})
        if st.get("state") != "SEL_GROUPS":
            await cq.answer()
            return
        st["page"] = int(data[3:])
        try:
            await cq.message.edit_reply_markup(groups_inline(st["all"], st["sel"], st["page"]))
        except Exception:
            pass
        await cq.answer()
        return

    if data == "gnoop":
        await cq.answer()
        return

    if data == "gsave":
        st = user_states.pop(uid, {})
        if st.get("state") != "SEL_GROUPS":
            await cq.answer("Sessiya tugagan.", show_alert=True)
            return
        selected = list(st["sel"])
        if not selected:
            await cq.answer("❌ Kamida 1 ta guruh tanlang!", show_alert=True)
            user_states[uid] = st
            return
        update_user(uid, groups=selected)
        uc = await get_client(uid)
        if uc:
            await try_save_folder(uc, selected)
        await edit(f"✅ {len(selected)} ta guruh saqlandi!", main_inline(True))
        await cq.answer()
        return

    if data == "gcancel":
        user_states.pop(uid, None)
        await edit("Bekor qilindi.", main_inline(True))
        await cq.answer()
        return

    await cq.answer()

# ── Startup ───────────────────────────────────────────────────────────────────

def startup_jobs():
    data = load_db()
    for uid_str, d in data.items():
        if d.get("status") == "running":
            setup_job(int(uid_str))

if __name__ == "__main__":
    import pyrogram

    async def main():
        await bot.start()
        print(f"Bot @{(await bot.get_me()).username} ishga tushdi!")
        scheduler.start()
        startup_jobs()
        await pyrogram.idle()
        await bot.stop()

    print("Bot starting...")
    loop.run_until_complete(main())

import os
import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramRetryAfter

import db

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

USE_WEBHOOK = os.getenv("USE_WEBHOOK", "0") == "1"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))

HINT_REPLY = "Чтобы ответить — смахни это сообщение и ответь кружком (video note) или голосовым (voice)."
QID_RE = re.compile(r"#Q(\d+)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bot")

if not BOT_TOKEN or not BOT_USERNAME:
    raise RuntimeError("BOT_TOKEN and BOT_USERNAME are required")

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS or (ADMIN_CHAT_ID and tg_id == ADMIN_CHAT_ID)

def link_by_token(token: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ask_{token}"

async def send_safe(chat_id: int, *args, **kwargs):
    retries = 3
    for i in range(retries):
        try:
            return await bot.send_message(chat_id, *args, **kwargs)
        except TelegramRetryAfter as e:
            sleep_for = min(int(e.retry_after) + 1, 30)
            log.warning(f"FloodWait: sleeping {sleep_for}s")
            await asyncio.sleep(sleep_for)
        except Exception:
            log.exception("send_message failed")
            if i == retries - 1:
                raise
            await asyncio.sleep(1 + i)

def ask_more_kb(target_user_id: int, question_id: int | None = None):
    kb = InlineKeyboardBuilder()
    kb.button(text="Задать ещё один вопрос", callback_data=f"askmore:{target_user_id}")
    if question_id:
        kb.button(text="Пожаловаться", callback_data=f"report:{question_id}")
    return kb.as_markup()

def consent_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Согласен(на)", callback_data="consent:yes")
    kb.button(text="❌ Не согласен(на)", callback_data="consent:no")
    return kb.as_markup()

@dp.message(CommandStart())
async def on_start(message: Message):
    user = db.ensure_user_by_tg(message.from_user.id)
    db.update_last_active(user["id"])

    args = (message.text or "").split(maxsplit=1)
    param = args[1].strip() if len(args) > 1 else ""

    if not user["consent_accepted"]:
        await message.answer(
            "Правила: запрещены злоупотребления. Мы храним минимум данных и ограниченное время. Нажимая \"Согласен\", ты подтверждаешь согласие.",
            reply_markup=consent_kb()
        )
        return

    if not param:
        await message.answer(
            "Ты зарегистрирован(а) ✅\n"
            "Поделись персональной ссылкой — по ней тебе смогут анонимно задавать вопросы:\n"
            f"{link_by_token(user['token'])}\n\n"
            "Чтобы ответить — смахни входящее сообщение и ответь кружком/голосовым."
        )
        return

    if param.startswith("ask_"):
        token = param[4:]
        target = db.get_user_by_token(token)
        if not target:
            await message.answer("Некорректная ссылка. Попроси новую.")
            return
        if target["id"] == user["id"]:
            await message.answer("Нельзя задавать вопрос самому себе.")
            return
        if not target["accepts_questions"]:
            await message.answer("Пользователь сейчас не принимает вопросы.")
            return
        db.create_session(user_id=user["id"], target_user_id=target["id"], typ="ask")
        await message.answer("Отправь кружок (video note) или голосовое (voice) — я доставлю его адресату.")
        return

    await message.answer("Неизвестный параметр. Отправь /start.")

@dp.callback_query(F.data == "consent:yes")
async def on_consent_yes(cq: CallbackQuery):
    user = db.ensure_user_by_tg(cq.from_user.id)
    db.mark_consent(user["id"])
    await cq.message.answer(
        "Спасибо! Аккаунт активирован.\n"
        f"Твоя ссылка: {link_by_token(user['token'])}"
    )
    await cq.answer()

@dp.callback_query(F.data == "consent:no")
async def on_consent_no(cq: CallbackQuery):
    await cq.message.answer("Без согласия с правилами бот недоступен. Отправь /start, если передумаешь.")
    await cq.answer()

def media_from_message(msg: Message):
    if msg.video_note:
        return "video_note", msg.video_note.file_id
    if msg.voice:
        return "voice", msg.voice.file_id
    return None, None

@dp.message(F.reply_to_message)
async def on_reply(message: Message):
    user = db.ensure_user_by_tg(message.from_user.id)
    db.update_last_active(user["id"])

    replied = message.reply_to_message
    db.mark_read_by_msg(user["id"], replied.message_id)
    q = db.get_question_by_reply(user["id"], replied.message_id)
    if not q:
        await message.answer("Ответь реплаем именно на сообщение-вопрос.")
        return

    media_type, file_id = media_from_message(message)
    if media_type is None:
        await message.answer("Разрешены только кружки (video note) и голосовые (voice).")
        return

    db.create_answer(question_id=q["id"], from_user=user["id"], text=None, media_type=media_type, file_id=file_id)
    db.add_metric("answers_sent")

    to_notify = db.get_user_by_id(q["from_user"])
    await send_safe(to_notify["tg_id"], "📬 Пришёл ответ на твой вопрос:")
    if media_type == "voice":
        await bot.send_voice(to_notify["tg_id"], file_id, caption="", reply_markup=ask_more_kb(user["id"], question_id=q["id"]))
    elif media_type == "video_note":
        await bot.send_video_note(to_notify["tg_id"], file_id, reply_markup=ask_more_kb(user["id"], question_id=q["id"]))
    await message.answer("Ответ отправлен ✅")

@dp.message(F.voice | F.video_note | F.text | F.photo)
async def on_content(message: Message):
    user = db.ensure_user_by_tg(message.from_user.id)
    db.update_last_active(user["id"])

    if message.reply_to_message:
        return

    sess = db.pop_session(user["id"], "ask")
    if sess:
        target = db.get_user_by_id(sess["target_user_id"])
        if not target:
            await message.answer("Пользователь недоступен.")
            return
        if db.is_blocked(blocker_id=target["id"], blocked_id=user["id"]):
            await message.answer("Этот пользователь заблокировал вопросы от тебя.")
            return

        media_type, file_id = media_from_message(message)
        if media_type is None:
            await message.answer("Отправь кружок (video note) или голосовое (voice). Фото и текст запрещены.")
            return

        qid = db.create_question(from_user=user["id"], to_user=target["id"], text=None, media_type=media_type, file_id=file_id)
        db.add_metric("questions_sent")

        await send_safe(target["tg_id"], f"📨 Тебе задали анонимный вопрос.\n{HINT_REPLY}\nID: #Q{qid}")
        if media_type == "voice":
            sent_msg = await bot.send_voice(target["tg_id"], file_id, caption="", reply_markup=ask_more_kb(target["id"], question_id=qid))
        elif media_type == "video_note":
            sent_msg = await bot.send_video_note(target["tg_id"], file_id, reply_markup=ask_more_kb(target["id"], question_id=qid))
        db.set_question_msg(to_user=target["id"], question_id=qid, msg_id=sent_msg.message_id)
        await message.answer("Вопрос отправлен анонимно ✅")
        return

    await message.answer(
        "Чтобы задать вопрос — перейди по ссылке адресата, затем отправь кружок (video note) или голосовое (voice).\n"
        f"Твоя ссылка: {link_by_token(user['token'])}"
    )

@dp.callback_query(F.data.startswith("askmore:"))
async def on_ask_more(cq: CallbackQuery):
    user = db.ensure_user_by_tg(cq.from_user.id)
    db.update_last_active(user["id"])
    try:
        target_uid = int(cq.data.split(":")[1])
    except Exception:
        await cq.answer("Сессия устарела", show_alert=True)
        return
    if target_uid == user["id"]:
        await cq.answer("Себе вопросы нельзя 🙂", show_alert=True)
        return
    db.create_session(user_id=user["id"], target_user_id=target_uid, typ="ask")
    await cq.message.answer("Напиши следующий вопрос (кружок/голос) — я отправлю его тому же пользователю.")
    await cq.answer()

@dp.message(Command("health"))
async def health(message: Message):
    await message.answer("OK")

@dp.callback_query(F.data.startswith("report:"))
async def on_report(cq: CallbackQuery):
    user = db.ensure_user_by_tg(cq.from_user.id)
    db.update_last_active(user["id"])
    try:
        qid = int(cq.data.split(":")[1])
    except Exception:
        await cq.answer("Сессия устарела", show_alert=True)
        return
    db.create_report(reporter=user["id"], target_user=user["id"], question_id=qid, reason=None)
    if ADMIN_CHAT_ID:
        try:
            await send_safe(ADMIN_CHAT_ID, f"⚠️ Новый репорт: question_id={qid}, reporter={user['id']}")
        except Exception:
            log.exception("Failed to notify admin")
    await cq.answer("Спасибо. Жалоба передана модератору.", show_alert=True)

# ===== Admin =====

def admin_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Кол-во пользователей", callback_data="adm:users")
    kb.button(text="🔍 Найти по #QID", callback_data="adm:qfind")
    kb.button(text="📣 Рассылка", callback_data="adm:bcast")
    return kb.as_markup()

@dp.message(Command("admin"))
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Админ-панель:", reply_markup=admin_menu_kb())

@dp.callback_query(F.data == "adm:users")
async def adm_users(cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return
    cnt = db.count_users()
    await cq.message.answer(f"Пользователей: {cnt}")
    await cq.answer()

@dp.callback_query(F.data == "adm:qfind")
async def adm_qfind_hint(cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return
    await cq.message.answer("Перешли мне сообщение с строкой вроде ID: #Q123 — и я покажу детали.")
    await cq.answer()

@dp.message(F.text | F.caption)
async def admin_forward_lookup(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "") + "\n" + (message.caption or "")
    m = QID_RE.search(text)
    if not m:
        return
    qid = int(m.group(1))
    q = db.get_question_by_id(qid)
    if not q:
        await message.answer("ID не найден.")
        return
    asker = db.get_user_by_id(q["from_user"])
    rec = db.get_user_by_id(q["to_user"])
    status = "✅ отвечено" if q["answered"] else ("👀 прочитано" if q["read_at"] else "⏳ не прочитано")
    mt = q["media_type"] or "?"
    await message.answer(
        f"Вопрос #{qid}\n— От TG: {asker['tg_id']}\n— Кому TG: {rec['tg_id']}\n— Тип: {mt}\n— Статус: {status}\n— Время: {q['created_at']}"
    )

async def periodic_maintenance():
    while True:
        try:
            db.cleanup_old_and_archive()
        except Exception:
            log.exception("maintenance error")
        await asyncio.sleep(3600)

async def on_startup():
    db.migrate()
    asyncio.create_task(periodic_maintenance())
    log.info("Bot started")

async def main():
    await on_startup()
    if USE_WEBHOOK:
        await bot.set_webhook(WEBHOOK_URL)
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        from aiohttp import web
        app = web.Application()
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        await web._run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)
    else:
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
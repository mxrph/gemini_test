import os
import asyncio
import logging
import time
import base64
from io import BytesIO
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile, BotCommand, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
import google.generativeai as genai
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")
MY_ID = int(os.getenv("MY_TELEGRAM_ID", "0"))

# Конфигурация Google API
genai.configure(api_key=API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Модели
PRIMARY_MODEL = "models/gemini-3-flash-preview"
FALLBACK_MODEL = "models/gemini-1.5-flash"
IMAGE_MODEL = "models/imagen-3.0-generate-001"

# Состояния
chat_session = None
translate_mode = {}  
user_languages = {}  
usage_stats = {"text": 0, "image": 0, "last_reset": time.time()}

# --- Клавиатуры ---

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎨 Создать фото", callback_data="btn_image"),
                InlineKeyboardButton(text="🌍 Переводчик", callback_data="btn_translate"))
    builder.row(InlineKeyboardButton(text="📊 Лимиты", callback_data="btn_limits"),
                InlineKeyboardButton(text="🧹 Сброс чата", callback_data="btn_reset"))
    return builder.as_markup()

def get_lang_menu():
    builder = InlineKeyboardBuilder()
    langs = {"English 🇬🇧": "английский", "Japanese 🇯🇵": "японский", "German 🇩🇪": "немецкий", "Chinese 🇨🇳": "китайский"}
    for name, code in langs.items():
        builder.add(InlineKeyboardButton(text=name, callback_data=f"lang_{code}"))
    builder.adjust(2)
    return builder.as_markup()

# --- Логика Gemini ---

async def call_gemini(text, data=None, mime_type=None, user_id=None):
    global chat_session
    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            model = genai.GenerativeModel(model_name)
            
            if user_id and translate_mode.get(user_id):
                target = user_languages.get(user_id, "английский")
                text = f"Переведи на {target}. Если уже на нем, переведи на русский: {text}"

            if data:
                content = [{"mime_type": mime_type, "data": data}, text]
                response = model.generate_content(content)
            else:
                if chat_session is None:
                    chat_session = model.start_chat(history=[])
                response = chat_session.send_message(text)
            
            usage_stats["text"] += 1
            return response.text
        except Exception as e:
            logger.error(f"Сбой модели {model_name}: {e}")
            continue
    return "❌ Все модели сейчас недоступны. Попробуйте позже."

# --- Обработчики Callback-кнопок ---

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(call: types.CallbackQuery):
    lang = call.data.split("_")[1]
    user_languages[call.from_user.id] = lang
    await call.message.answer(f"✅ Язык перевода установлен: {lang.capitalize()}")
    await call.answer()

@dp.callback_query(F.data.startswith("btn_"))
async def callbacks(call: types.CallbackQuery):
    action = call.data.split("_")[1]
    if action == "image": await call.message.answer("Чтобы создать фото, напиши: `/image описание`")
    elif action == "translate": await toggle_translate(call.message)
    elif action == "limits": await limits_cmd(call.message)
    elif action == "reset": await reset_cmd(call.message)
    await call.answer()

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start(m: types.Message):
    await m.answer("🚀 Бот Gemini 3 готов. Используйте меню:", reply_markup=get_main_menu())

@dp.message(Command("translate"))
async def toggle_translate(m: types.Message):
    uid = m.from_user.id
    translate_mode[uid] = not translate_mode.get(uid, False)
    if translate_mode[uid]:
        await m.answer("🌍 Режим перевода ВКЛЮЧЕН. Выберите язык:", reply_markup=get_lang_menu())
    else:
        await m.answer("⚪ Режим перевода ВЫКЛЮЧЕН.")

@dp.message(Command("image"))
async def gen_image(m: types.Message):
    prompt = m.text.replace("/image", "").strip()
    if not prompt: return await m.answer("Укажите описание для генерации фото.")
    await bot.send_chat_action(m.chat.id, "upload_photo")
    try:
        model = genai.GenerativeModel(IMAGE_MODEL)
        response = model.generate_content(prompt)
        # Улучшенный захват байтов
        part = response.candidates[0].content.parts[0]
        img_data = part.inline_data.data if hasattr(part, 'inline_data') else part.blob.data
        if isinstance(img_data, str): img_data = base64.b64decode(img_data)
        
        await m.answer_photo(BufferedInputFile(img_data, filename="gen.jpg"), reply_markup=get_main_menu())
        usage_stats["image"] += 1
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        await m.answer(f"❌ Не удалось создать фото. Попробуйте другой запрос.", reply_markup=get_main_menu())

@dp.message(Command("video"))
async def gen_video(m: types.Message):
    await m.answer("⏳ Модель видео (Veo) сейчас в режиме ожидания доступа. Ожидайте уведомления в Google Cloud.", reply_markup=get_main_menu())

@dp.message(Command("limits"))
async def limits_cmd(m: types.Message):
    await m.answer(f"📊 Использовано сегодня:\n💬 Текст: {usage_stats['text']}\n🖼️ Фото: {usage_stats['image']}", reply_markup=get_main_menu())

@dp.message(Command("reset"))
async def reset_cmd(m: types.Message):
    global chat_session
    chat_session = None
    await m.answer("🧹 История чата очищена.", reply_markup=get_main_menu())

# --- Обработка медиа и текста ---

@dp.message(F.voice)
async def voice_msg(m: types.Message):
    file = await bot.get_file(m.voice.file_id)
    data = await bot.download_file(file.file_path)
    ans = await call_gemini("Ответь на голосовое:", data.read(), "audio/ogg", m.from_user.id)
    await m.reply(ans, reply_markup=get_main_menu())

@dp.message(F.document)
async def doc_msg(m: types.Message):
    if m.document.mime_type == "application/pdf":
        file = await bot.get_file(m.document.file_id)
        data = await bot.download_file(file.file_path)
        ans = await call_gemini("Проанализируй PDF:", data.read(), "application/pdf", m.from_user.id)
        await m.answer(ans, reply_markup=get_main_menu())

@dp.message(F.photo)
async def photo_msg(m: types.Message):
    file = await bot.get_file(m.photo[-1].file_id)
    data = await bot.download_file(file.file_path)
    ans = await call_gemini(m.caption or "Что на фото?", data.read(), "image/jpeg", m.from_user.id)
    await m.answer(ans, reply_markup=get_main_menu())

@dp.message(F.text)
async def text_msg(m: types.Message):
    if MY_ID and m.from_user.id != MY_ID: return
    ans = await call_gemini(m.text, user_id=m.from_user.id)
    await m.answer(ans, reply_markup=get_main_menu())

# --- Запуск ---
async def health(request): return web.Response(text="OK")

async def main():
    server = web.Application(); server.router.add_get("/", health)
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

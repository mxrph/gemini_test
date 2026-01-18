import os
import asyncio
import logging
import time
import base64
from io import BytesIO
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
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

# Конфигурация Google API (используем v1beta для стабильности новых моделей)
genai.configure(api_key=API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Модели
PRIMARY_VARIANTS = ["models/gemini-2.0-flash-exp", "models/gemini-1.5-flash"]
IMAGE_GEN_MODEL = "models/imagen-3.0-generate-001"

# Состояния пользователя
chat_session = None
translate_mode = {}  # {user_id: bool}
user_languages = {}  # {user_id: str}
usage_stats = {"text": 0, "image": 0, "video_gen": 0, "last_reset": time.time()}
LIMITS = {"text": 1500, "image": 50}

# --- Вспомогательные функции ---

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎨 Создать фото", callback_data="btn_image"))
    builder.row(InlineKeyboardButton(text="🌍 Переводчик", callback_data="btn_translate"))
    builder.row(InlineKeyboardButton(text="📊 Лимиты", callback_data="btn_limits"),
                InlineKeyboardButton(text="🧹 Сброс", callback_data="btn_reset"))
    builder.row(InlineKeyboardButton(text="❓ Помощь", callback_data="btn_help"))
    return builder.as_markup()

def get_lang_menu():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_английский"))
    builder.add(InlineKeyboardButton(text="🇯🇵 Japanese", callback_data="lang_японский"))
    builder.add(InlineKeyboardButton(text="🇩🇪 German", callback_data="lang_немецкий"))
    builder.add(InlineKeyboardButton(text="🇨🇳 Chinese", callback_data="lang_китайский"))
    builder.add(InlineKeyboardButton(text="🇫🇷 French", callback_data="lang_французский"))
    builder.adjust(2)
    return builder.as_markup()

async def call_gemini(text, data=None, mime_type=None, user_id=None):
    global chat_session
    target_lang = user_languages.get(user_id, "английский")
    
    if user_id and translate_mode.get(user_id):
        text = f"ПЕРЕВОДЧИК: Переведи следующий контент на {target_lang}. Если он уже на этом языке, переведи на русский: {text}"

    for model_name in PRIMARY_VARIANTS:
        try:
            model = genai.GenerativeModel(model_name)
            if data:
                content = [{"mime_type": mime_type, "data": data}, text]
                response = model.generate_content(content)
                return response.text
            else:
                if chat_session is None:
                    chat_session = model.start_chat(history=[])
                response = chat_session.send_message(text)
                usage_stats["text"] += 1
                return response.text
        except Exception as e:
            logger.error(f"Ошибка {model_name}: {e}")
            continue
    return "❌ Ошибка API. Попробуйте позже."

# --- Обработчики Callback-кнопок ---

@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_languages[callback.from_user.id] = lang
    await callback.message.answer(f"✅ Язык перевода установлен: **{lang.capitalize()}**")
    await callback.answer()

@dp.callback_query(F.data.startswith("btn_"))
async def menu_callbacks(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    if action == "image":
        await callback.message.answer("Чтобы создать фото, напиши: `/image описание`")
    elif action == "translate":
        await toggle_translate(callback.message)
    elif action == "limits":
        await limits_cmd(callback.message)
    elif action == "reset":
        await reset_cmd(callback.message)
    elif action == "help":
        await help_cmd(callback.message)
    await callback.answer()

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("🚀 Бот Gemini 3 готов к работе! Используйте кнопки ниже:", reply_markup=get_main_menu())

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    help_text = "📖 **Доступные функции:**\n\n- Голосовой чат\n- Анализ PDF и фото\n- Режим /translate\n- Генерация /image"
    await message.answer(help_text, reply_markup=get_main_menu())

@dp.message(Command("translate"))
async def toggle_translate(message: types.Message):
    uid = message.from_user.id
    translate_mode[uid] = not translate_mode.get(uid, False)
    if translate_mode[uid]:
        await message.answer("🌍 Режим перевода ВКЛЮЧЕН. Выберите целевой язык:", reply_markup=get_lang_menu())
    else:
        await message.answer("⚪ Режим перевода ВЫКЛЮЧЕН. Теперь я просто чат-бот.")

@dp.message(Command("limits"))
async def limits_cmd(message: types.Message):
    msg = f"📊 Лимиты: Текст {usage_stats['text']}/{LIMITS['text']}, Фото {usage_stats['image']}/{LIMITS['image']}"
    await message.answer(msg, reply_markup=get_main_menu())

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("🧹 История чата очищена.", reply_markup=get_main_menu())

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    if not prompt: return await message.answer("Напишите описание после команды.")
    
    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        # Прямой вызов Imagen 3
        model = genai.GenerativeModel(IMAGE_GEN_MODEL)
        response = model.generate_content(prompt)
        
        # Исправленный захват байтов для v1beta
        try:
            img_data = response.candidates[0].content.parts[0].inline_data.data
            image_bytes = base64.b64decode(img_data)
        except:
            image_bytes = response.candidates[0].content.parts[0].blob.data
            
        await message.answer_photo(BufferedInputFile(image_bytes, filename="gen.jpg"), reply_markup=get_main_menu())
        usage_stats["image"] += 1
    except Exception as e:
        logger.error(f"Imagen Error: {e}")
        await message.answer("❌ Ошибка генерации. Попробуйте другое описание.")

# --- Обработка входящих данных ---

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    file_info = await bot.get_file(message.voice.file_id)
    data = await bot.download_file(file_info.file_path)
    ans = await call_gemini("Ответь на голосовое:", data.read(), "audio/ogg", message.from_user.id)
    await message.reply(ans, reply_markup=get_main_menu())

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    file_info = await bot.get_file(message.photo[-1].file_id)
    data = await bot.download_file(file_info.file_path)
    ans = await call_gemini(message.caption or "Что на фото?", data.read(), "image/jpeg", message.from_user.id)
    await message.answer(ans, reply_markup=get_main_menu())

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    ans = await call_gemini(message.text, user_id=message.from_user.id)
    await message.answer(ans, reply_markup=get_main_menu())

# --- Запуск ---
async def health_check(request): return web.Response(text="OK")

async def main():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import os
import asyncio
import logging
import socket
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import URLInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from google import genai
from google.genai import types as genai_types
from aiohttp import web # Добавлено для веб-сервера

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")
MY_ID_STR = os.getenv("MY_TELEGRAM_ID", "0")
MY_ID = int(MY_ID_STR) if MY_ID_STR.isdigit() else 0

# Глобальные переменные
client = None
chat = None
MODEL_ID = "gemini-2.0-flash"

# Инициализация
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

def is_owner(user_id):
    return MY_ID == 0 or user_id == MY_ID

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_owner(message.from_user.id): return
    await message.answer("Bot is online. Available features: text, voice, photo analysis, PDF documents, and /draw command.")

@dp.message(Command("reset"))
async def reset(message: types.Message):
    if not is_owner(message.from_user.id): return
    global chat
    if client:
        chat = client.chats.create(model=MODEL_ID, config=genai_types.GenerateContentConfig(
            system_instruction="You are a professional AI assistant. Provide concise and accurate answers."
        ))
        await message.answer("Context has been reset.")

@dp.message(Command("draw"))
async def draw(message: types.Message):
    if not is_owner(message.from_user.id): return
    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        return await message.answer("Please provide a prompt after the command.")
    
    await message.answer("Generating image...")
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&nologo=true"
    try:
        await message.answer_photo(photo=URLInputFile(url), caption=f"Result for: {prompt}")
    except Exception as e:
        await message.answer("Error during image generation.")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    if not is_owner(message.from_user.id): return
    try:
        v_file = await bot.get_file(message.voice.file_id)
        v_data = await bot.download_file(v_file.file_path)
        response = chat.send_message(message=[
            "Analyze this audio.",
            genai_types.Part.from_bytes(data=v_data.read(), mime_type="audio/ogg")
        ])
        await message.answer(response.text)
    except Exception as e:
        await message.answer(f"Error: {e}")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not is_owner(message.from_user.id): return
    try:
        p_file = await bot.get_file(message.photo[-1].file_id)
        p_data = await bot.download_file(p_file.file_path)
        response = chat.send_message(message=[
            message.caption or "Analyze this image.",
            genai_types.Part.from_bytes(data=p_data.read(), mime_type="image/jpeg")
        ])
        await message.answer(response.text)
    except Exception as e:
        await message.answer(f"Error: {e}")

@dp.message(F.document)
async def handle_doc(message: types.Message):
    if not is_owner(message.from_user.id): return
    allowed_types = ["application/pdf", "text/plain"]
    if message.document.mime_type not in allowed_types:
        return await message.answer("Only PDF/TXT supported.")
    try:
        d_file = await bot.get_file(message.document.file_id)
        d_data = await bot.download_file(d_file.file_path)
        response = chat.send_message(message=[
            message.caption or "Summarize document.",
            genai_types.Part.from_bytes(data=d_data.read(), mime_type=message.document.mime_type)
        ])
        await message.answer(response.text)
    except Exception as e:
        await message.answer(f"Error: {e}")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if not is_owner(message.from_user.id): return
    try:
        response = chat.send_message(message.text)
        await message.answer(response.text)
    except Exception as e:
        await message.answer(f"Error: {e}")

# --- ФИКТИВНЫЙ ВЕБ-СЕРВЕР ДЛЯ KOYEB ---

async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    # Koyeb ищет порт 8000 по умолчанию
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    logger.info("Web server for Health Check started on port 8000")

# --- ЗАПУСК ---

async def main():
    global chat, client
    
    # Запускаем фиктивный сервер в фоне
    await start_web_server()

    if not API_KEY or not TOKEN:
        logger.error("Missing credentials!")
        return

    try:
        client = genai.Client(api_key=API_KEY)
        chat = client.chats.create(model=MODEL_ID, config=genai_types.GenerateContentConfig(
            system_instruction="Professional AI assistant. No emojis. Professional tone."
        ))
        logger.info("Gemini initialized.")
    except Exception as e:
        logger.error(f"Init failed: {e}")
        return

    # Уменьшаем время ожидания, на Koyeb сеть работает сразу
    await asyncio.sleep(5)

    try:
        ip = socket.gethostbyname('api.telegram.org')
        logger.info(f"Telegram DNS OK: {ip}")
    except Exception as e:
        logger.error(f"DNS failed: {e}")

    logger.info("Starting polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Polling error: {e}")
        os._exit(1)

if __name__ == "__main__":
    asyncio.run(main())

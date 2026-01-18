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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация из Secrets
TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")  # Исправленное имя ключа
MY_ID_STR = os.getenv("MY_TELEGRAM_ID", "0")
MY_ID = int(MY_ID_STR) if MY_ID_STR.isdigit() else 0

# Глобальные переменные
client = None
chat = None
MODEL_ID = "gemini-2.0-flash"

# Инициализация бота
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()


def is_owner(user_id):
    return MY_ID == 0 or user_id == MY_ID


# Обработчики
@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_owner(message.from_user.id): return
    await message.answer("Bot is online and ready to work.")


@dp.message(Command("reset"))
async def reset(message: types.Message):
    if not is_owner(message.from_user.id): return
    global chat
    if client:
        chat = client.chats.create(model=MODEL_ID)
        await message.answer("Memory cleared.")


@dp.message(Command("генерация"))
async def draw(message: types.Message):
    if not is_owner(message.from_user.id): return
    prompt = message.text.replace("/генерация", "").strip()
    if not prompt:
        return await message.answer("Provide a prompt after the command.")
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&nologo=true"
    await message.answer_photo(photo=URLInputFile(url), caption=f"Generated: {prompt}")


@dp.message(F.voice)
async def handle_voice(message: types.Message):
    if not is_owner(message.from_user.id): return
    v_file = await bot.get_file(message.voice.file_id)
    v_data = await bot.download_file(v_file.file_path)
    response = chat.send_message(message=[
        "Analyze this audio.",
        genai_types.Part.from_bytes(data=v_data.read(), mime_type="audio/ogg")
    ])
    await message.answer(response.text)


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not is_owner(message.from_user.id): return
    p_file = await bot.get_file(message.photo[-1].file_id)
    p_data = await bot.download_file(p_file.file_path)
    response = chat.send_message(message=[
        message.caption or "What is this?",
        genai_types.Part.from_bytes(data=p_data.read(), mime_type="image/jpeg")
    ])
    await message.answer(response.text)


@dp.message(F.text)
async def handle_text(message: types.Message):
    if not is_owner(message.from_user.id): return
    try:
        response = chat.send_message(message.text)
        await message.answer(response.text)
    except Exception as e:
        await message.answer(f"Gemini error: {e}")


# Запуск
async def main():
    global chat, client

    if not API_KEY or not TOKEN:
        logger.error("CRITICAL: GEMINI_API_KEY or TELEGRAM_TOKEN missing!")
        return

    try:
        client = genai.Client(api_key=API_KEY)
        chat = client.chats.create(model=MODEL_ID)
        logger.info("Gemini initialized.")
    except Exception as e:
        logger.error(f"Client init failed: {e}")
        return

    logger.info("Waiting for network (30s)...")
    await asyncio.sleep(30)

    try:
        ip = socket.gethostbyname('api.telegram.org')
        logger.info(f"DNS OK. IP: {ip}")
    except Exception as e:
        logger.error(f"DNS still failing: {e}")

    logger.info("Starting polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Polling error: {e}")
        await asyncio.sleep(60)
        os._exit(1)


if __name__ == "__main__":
    asyncio.run(main())
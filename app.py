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
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")
MY_ID_STR = os.getenv("MY_TELEGRAM_ID", "0")
MY_ID = int(MY_ID_STR) if MY_ID_STR.isdigit() else 0

# Модели
PRIMARY_MODEL = "models/gemini-2.0-flash"
FALLBACK_MODEL = "models/gemini-1.5-flash"

# Инициализация
session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()
client = None
chat = None

def is_owner(user_id):
    return MY_ID == 0 or user_id == MY_ID

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОТПРАВКИ С FALLBACK ---

async def send_gemini_message(message_content, content_type="text", mime_type=None):
    global chat
    try:
        # Попытка через основную модель (Chat session для контекста)
        if content_type == "text":
            response = chat.send_message(message_content)
        else:
            response = chat.send_message(message=[
                "Analyze this file.",
                genai_types.Part.from_bytes(data=message_content, mime_type=mime_type)
            ])
        return response.text
    except Exception as e:
        if "429" in str(e):
            logger.info(f"Rate limit on {PRIMARY_MODEL}. Switching to {FALLBACK_MODEL}")
            # Прямой запрос к 1.5 (без контекста чата для надежности)
            try:
                if content_type == "text":
                    fb_res = client.models.generate_content(
                        model=FALLBACK_MODEL, contents=message_content,
                        config=genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                    )
                else:
                    fb_res = client.models.generate_content(
                        model=FALLBACK_MODEL,
                        contents=[genai_types.Part.from_bytes(data=message_content, mime_type=mime_type), "Analyze this."],
                        config=genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
                    )
                return fb_res.text + "\n\n*(Used backup model due to rate limits)*"
            except Exception as fe:
                return f"Error: All models are busy. {fe}"
        return f"Error: {e}"

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_owner(message.from_user.id): return
    await message.answer("Bot is online with Auto-Fallback (2.0 -> 1.5).")

@dp.message(Command("reset"))
async def reset(message: types.Message):
    if not is_owner(message.from_user.id): return
    global chat
    chat = client.chats.create(model=PRIMARY_MODEL, config=genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT))
    await message.answer("Context reset.")

@dp.message(Command("draw"))
async def draw(message: types.Message):
    if not is_owner(message.from_user.id): return
    prompt = message.text.replace("/draw", "").strip()
    if not prompt: return await message.answer("Provide a prompt.")
    await message.answer_photo(photo=URLInputFile(f"https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&nologo=true"))

@dp.message(F.text)
async def handle_text(message: types.Message):
    if not is_owner(message.from_user.id): return
    ans = await send_gemini_message(message.text, "text")
    await message.answer(ans)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not is_owner(message.from_user.id): return
    p_file = await bot.get_file(message.photo[-1].file_id)
    p_data = await bot.download_file(p_file.file_path)
    ans = await send_gemini_message(p_data.read(), "file", "image/jpeg")
    await message.answer(ans)

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    if not is_owner(message.from_user.id): return
    v_file = await bot.get_file(message.voice.file_id)
    v_data = await bot.download_file(v_file.file_path)
    ans = await send_gemini_message(v_data.read(), "file", "audio/ogg")
    await message.answer(ans)

# --- ВЕБ-СЕРВЕР ДЛЯ KOYEB ---

async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    logger.info("Health Check server started on port 8000")

# --- ЗАПУСК ---

async def main():
    global chat, client
    await start_web_server()
    
    if not API_KEY or not TOKEN:
        logger.error("Missing credentials!")
        return

    client = genai.Client(api_key=API_KEY)
    chat = client.chats.create(model=PRIMARY_MODEL, config=genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT))
    
    logger.info("Bot is ready. Starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


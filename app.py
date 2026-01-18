import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import URLInputFile
from aiogram.client.session.aiohttp import AiohttpSession
from google import genai
from google.genai import types as genai_types
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")
MY_ID = int(os.getenv("MY_TELEGRAM_ID", "0"))

# Используем уточненные имена
PRIMARY_MODEL = "gemini-2.0-flash-exp"
FALLBACK_MODEL = "gemini-1.5-flash"

session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()
client = None
chat = None

async def send_gemini_message(message_content, content_type="text", mime_type=None):
    global chat, client
    try:
        # Попытка через 2.0
        if content_type == "text":
            response = chat.send_message(message_content)
        else:
            response = chat.send_message(message=[
                "Analyze this.",
                genai_types.Part.from_bytes(data=message_content, mime_type=mime_type)
            ])
        return response.text
    except Exception as e:
        logger.error(f"Error with 2.0: {e}")
        # При ЛЮБОЙ ошибке (404, 429) пробуем 1.5
        try:
            if content_type == "text":
                fb_res = client.models.generate_content(model=FALLBACK_MODEL, contents=message_content)
            else:
                fb_res = client.models.generate_content(
                    model=FALLBACK_MODEL,
                    contents=[genai_types.Part.from_bytes(data=message_content, mime_type=mime_type), "Analyze this."]
                )
            return fb_res.text + "\n\n*(Backup 1.5)*"
        except Exception as fe:
            return f"Критическая ошибка API: {fe}"

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Бот онлайн. Напиши что-нибудь!")

@dp.message(Command("reset"))
async def reset(message: types.Message):
    global chat
    chat = client.chats.create(model=PRIMARY_MODEL)
    await message.answer("Контекст сброшен.")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID != 0 and message.from_user.id != MY_ID: return
    ans = await send_gemini_message(message.text, "text")
    await message.answer(ans)

# --- WEB SERVER ---
async def handle_health(request): return web.Response(text="OK")

async def main():
    global chat, client
    # Health check
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()

    # Client с явным указанием версии
    client = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1beta'})
    chat = client.chats.create(model=PRIMARY_MODEL)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

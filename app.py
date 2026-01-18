import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
import google.generativeai as genai
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")
MY_ID = int(os.getenv("MY_TELEGRAM_ID", "0"))

# Имена моделей для старой библиотеки
PRIMARY_MODEL = "gemini-2.0-flash-exp"
FALLBACK_MODEL = "gemini-1.5-flash-latest" # Добавили -latest

genai.configure(api_key=API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальная переменная для чата
chat_session = None

def get_chat():
    global chat_session
    if chat_session is None:
        model = genai.GenerativeModel(PRIMARY_MODEL)
        chat_session = model.start_chat(history=[])
    return chat_session

async def send_gemini_message(text):
    global chat_session
    try:
        # Попытка через 2.0
        chat = get_chat()
        response = chat.send_message(text)
        return response.text
    except Exception as e:
        logger.error(f"Error with 2.0: {e}")
        # Если лимит или ошибка — идем в 1.5 напрямую (без истории, чтобы точно сработало)
        try:
            model_fallback = genai.GenerativeModel(FALLBACK_MODEL)
            response = model_fallback.generate_content(text)
            return response.text + "\n\n*(Used 1.5 Flash backup)*"
        except Exception as fe:
            return f"API Error: {fe}"

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Бот включен. Если 2.0 спит из-за лимитов, ответит 1.5.")

@dp.message(Command("reset"))
async def reset(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("История очищена.")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID != 0 and message.from_user.id != MY_ID: return
    # Отправляем статус "печатает"
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ans = await send_gemini_message(message.text)
    await message.answer(ans)

# --- KOYEB HEALTH CHECK ---
async def handle_health(request): return web.Response(text="OK")

async def main():
    # Запуск сервера для Koyeb
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


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

genai.configure(api_key=API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Список вариантов имен для перебора
PRIMARY_VARIANTS = ["gemini-2.0-flash-exp", "models/gemini-2.0-flash-exp"]
FALLBACK_VARIANTS = ["gemini-1.5-flash", "models/gemini-1.5-flash", "gemini-1.5-flash-latest"]

chat_session = None

async def send_gemini_message(text):
    global chat_session
    
    # 1. Попытка через Gemini 2.0 (пробуем разные имена)
    for model_name in PRIMARY_VARIANTS:
        try:
            model = genai.GenerativeModel(model_name)
            if chat_session is None:
                chat_session = model.start_chat(history=[])
            response = chat_session.send_message(text)
            return response.text
        except Exception as e:
            if "404" not in str(e): # Если ошибка не в имени (например, лимит), сразу идем в fallback
                break
            logger.warning(f"Имя {model_name} не подошло для 2.0, пробуем дальше...")

    # 2. Если 2.0 не сработала, пробуем варианты 1.5
    for fb_name in FALLBACK_VARIANTS:
        try:
            logger.info(f"Пробуем резервную модель: {fb_name}")
            model_fb = genai.GenerativeModel(fb_name)
            response = model_fb.generate_content(text)
            return response.text + f"\n\n*(Ответил через: {fb_name})*"
        except Exception as e:
            logger.error(f"Ошибка с {fb_name}: {e}")
            continue
            
    return "❌ Ошибка: Google API не принял ни одно из имен моделей. Проверьте API ключ."

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Бот запущен в режиме авто-подбора моделей!")

@dp.message(Command("list_models"))
async def list_models(message: types.Message):
    """Секретная команда для проверки доступных моделей"""
    if MY_ID != 0 and message.from_user.id != MY_ID: return
    try:
        models = [m.name for m in genai.list_models()]
        await message.answer("Доступные тебе модели:\n" + "\n".join(models))
    except Exception as e:
        await message.answer(f"Не удалось получить список: {e}")

@dp.message(Command("reset"))
async def reset(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("Контекст очищен.")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID != 0 and message.from_user.id != MY_ID: return
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ans = await send_gemini_message(message.text)
    await message.answer(ans)

# --- KOYEB HEALTH CHECK ---
async def handle_health(request): return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

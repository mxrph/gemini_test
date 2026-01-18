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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("GEMINI_API_KEY")
MY_ID_STR = os.getenv("MY_TELEGRAM_ID", "0")
MY_ID = int(MY_ID_STR) if MY_ID_STR.isdigit() else 0

# УКАЗЫВАЕМ ЧИСТЫЕ ИМЕНА БЕЗ "models/"
PRIMARY_MODEL = "gemini-2.0-flash"
FALLBACK_MODEL = "gemini-1.5-flash"

session = AiohttpSession()
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()
client = None
chat = None

def is_owner(user_id):
    return MY_ID == 0 or user_id == MY_ID

async def send_gemini_message(message_content, content_type="text", mime_type=None):
    global chat, client
    try:
        if content_type == "text":
            response = chat.send_message(message_content)
        else:
            response = chat.send_message(message=[
                "Analyze this file.",
                genai_types.Part.from_bytes(data=message_content, mime_type=mime_type)
            ])
        return response.text
    except Exception as e:
        error_str = str(e).upper()
        # Если лимит (429) или модель не найдена/недоступна (404/503)
        if any(code in error_str for code in ["429", "404", "503", "NOT_FOUND"]):
            logger.info(f"Переключение на fallback из-за ошибки: {e}")
            try:
                if content_type == "text":
                    fb_res = client.models.generate_content(model=FALLBACK_MODEL, contents=message_content)
                else:
                    fb_res = client.models.generate_content(
                        model=FALLBACK_MODEL,
                        contents=[genai_types.Part.from_bytes(data=message_content, mime_type=mime_type), "Analyze this."]
                    )
                return fb_res.text + "\n\n*(Использована модель 1.5 Flash)*"
            except Exception as fe:
                return f"Ошибка всех моделей: {fe}"
        return f"Произошла ошибка: {e}"

@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_owner(message.from_user.id): return
    await message.answer("Бот готов! Пиши что угодно.")

@dp.message(Command("reset"))
async def reset(message: types.Message):
    if not is_owner(message.from_user.id): return
    global chat
    chat = client.chats.create(model=PRIMARY_MODEL)
    await message.answer("Контекст сброшен.")

@dp.message(Command("draw"))
async def draw(message: types.Message):
    if not is_owner(message.from_user.id): return
    prompt = message.text.replace("/draw", "").strip()
    if not prompt: return await message.answer("Опиши картинку.")
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

async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()

async def main():
    global chat, client
    await start_web_server()
    if not API_KEY or not TOKEN: return
    client = genai.Client(api_key=API_KEY)
    chat = client.chats.create(model=PRIMARY_MODEL)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

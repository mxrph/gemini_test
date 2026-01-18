import os
import asyncio
import logging
import base64
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile
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

# Те самые модели из работающей версии
PRIMARY_MODEL = "gemini-3-flash-preview"
IMAGE_MODEL = "imagen-3.0-generate-001"
VIDEO_MODEL = "veo-1.0-generate-001"

chat_session = None

async def call_gemini(text, data=None, mime_type=None):
    global chat_session
    try:
        model = genai.GenerativeModel(PRIMARY_MODEL)
        if data:
            content = [{"mime_type": mime_type, "data": data}, text]
            response = model.generate_content(content)
            return response.text
        else:
            if chat_session is None:
                chat_session = model.start_chat(history=[])
            response = chat_session.send_message(text)
            return response.text
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "Произошла ошибка при обращении к серверу."

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. Используйте /help для просмотра списка функций.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer("Список функций:\n\n/image [описание] - Генерация фото\n/video [описание] - Генерация видео\n/reset - Очистить чат\n\nТакже можно присылать фото, PDF и голосовые.")

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    if not prompt: return await message.answer("Укажите описание.")
    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        # Прямой вызов той самой Imagen 3
        model = genai.GenerativeModel(IMAGE_MODEL)
        response = model.generate_content(prompt)
        part = response.candidates[0].content.parts[0]
        
        # Универсальный захват данных (blob или inline)
        image_bytes = getattr(part, 'inline_data', getattr(part, 'blob', None)).data
        if isinstance(image_bytes, str): image_bytes = base64.b64decode(image_bytes)
        
        await message.answer_photo(BufferedInputFile(image_bytes, filename="gen.jpg"))
    except Exception as e:
        logger.error(f"Ошибка Imagen: {e}")
        await message.answer("Не удалось создать фото. Попробуйте другой запрос.")

@dp.message(Command("video"))
async def video_gen_cmd(message: types.Message):
    prompt = message.text.replace("/video", "").strip()
    if not prompt: return await message.answer("Укажите описание.")
    await message.answer("Запрос на видео принят. Ожидайте.")
    try:
        model = genai.GenerativeModel(VIDEO_MODEL)
        response = model.generate_content(prompt)
        part = response.candidates[0].content.parts[0]
        video_bytes = getattr(part, 'inline_data', getattr(part, 'blob', None)).data
        if isinstance(video_bytes, str): video_bytes = base64.b64decode(video_bytes)
        await message.answer_video(BufferedInputFile(video_bytes, filename="gen.mp4"))
    except Exception as e:
        await message.answer("Видео пока недоступно для вашего API-ключа.")

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("История чата очищена.")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    file_info = await bot.get_file(message.photo[-1].file_id)
    data = await bot.download_file(file_info.file_path)
    ans = await call_gemini(message.caption or "Что на фото?", data.read(), "image/jpeg")
    await message.answer(ans)

@dp.message(F.document)
async def handle_docs(message: types.Message):
    if message.document.mime_type == "application/pdf":
        file_info = await bot.get_file(message.document.file_id)
        data = await bot.download_file(file_info.file_path)
        ans = await call_gemini("Проанализируй PDF:", data.read(), "application/pdf")
        await message.answer(ans)

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    ans = await call_gemini(message.text)
    await message.answer(ans)

async def health(request): return web.Response(text="OK")

async def main():
    server = web.Application(); server.router.add_get("/", health)
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

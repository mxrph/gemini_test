import os
import asyncio
import logging
import time
import base64
from io import BytesIO
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile
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
IMAGE_MODEL = "models/imagen-3.0-generate-001"
VIDEO_MODEL = "models/veo-1.0-generate-001"

# Состояния
chat_session = None

# --- Логика Gemini ---

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
        logger.error(f"Ошибка API: {e}")
        return "Произошла ошибка при обращении к серверу."

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. Используйте /help для просмотра списка функций.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    help_text = (
        "Список доступных функций:\n\n"
        "Текстовое общение: Просто отправьте сообщение в чат.\n"
        "Анализ изображений: Пришлите фото с вопросом в подписи.\n"
        "Анализ PDF: Отправьте документ в формате PDF.\n"
        "Голосовые сообщения: Бот распознает речь и ответит текстом.\n"
        "Генерация изображений: Команда /image [описание].\n"
        "Генерация видео: Команда /video [описание].\n"
        "Сброс контекста: Команда /reset."
    )
    await message.answer(help_text)

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("История диалога очищена.")

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    prompt = message.text.replace("/image", "").strip()
    if not prompt:
        return await message.answer("Укажите описание изображения.")

    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        model = genai.GenerativeModel(IMAGE_MODEL)
        response = model.generate_content(prompt)
        
        part = response.candidates[0].content.parts[0]
        if hasattr(part, 'inline_data'):
            image_bytes = part.inline_data.data
        elif hasattr(part, 'blob'):
            image_bytes = part.blob.data
        else:
            raise Exception("Данные изображения не найдены.")

        if isinstance(image_bytes, str):
            image_bytes = base64.b64decode(image_bytes)

        await message.answer_photo(BufferedInputFile(image_bytes, filename="gen.jpg"))
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}")
        await message.answer(f"Не удалось создать изображение. Ошибка: {str(e)[:100]}")

@dp.message(Command("video"))
async def video_gen_cmd(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    prompt = message.text.replace("/video", "").strip()
    if not prompt:
        return await message.answer("Укажите описание видео.")

    await message.answer("Запрос на генерацию видео принят. Это может занять несколько минут.")
    try:
        model = genai.GenerativeModel(VIDEO_MODEL)
        response = model.generate_content(prompt)
        # Обработка видеофайла аналогична фото, но возвращает другой mime_type
        part = response.candidates[0].content.parts[0]
        video_data = part.inline_data.data if hasattr(part, 'inline_data') else part.blob.data
        
        if isinstance(video_data, str):
            video_data = base64.b64decode(video_data)
            
        await message.answer_video(BufferedInputFile(video_data, filename="gen.mp4"))
    except Exception as e:
        logger.error(f"Ошибка генерации видео: {e}")
        await message.answer(f"Не удалось создать видео. Причина: модель Veo может быть недоступна для вашего региона или ключа.")

# --- Обработка медиаконтента ---

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    file_info = await bot.get_file(message.voice.file_id)
    data = await bot.download_file(file_info.file_path)
    ans = await call_gemini("Прослушай и ответь на сообщение:", data.read(), "audio/ogg")
    await message.reply(ans)

@dp.message(F.document)
async def handle_docs(message: types.Message):
    if message.document.mime_type == "application/pdf":
        file_info = await bot.get_file(message.document.file_id)
        data = await bot.download_file(file_info.file_path)
        ans = await call_gemini("Проанализируй этот PDF документ:", data.read(), "application/pdf")
        await message.answer(ans)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    prompt = message.caption or "Что на этом изображении?"
    file_info = await bot.get_file(message.photo[-1].file_id)
    data = await bot.download_file(file_info.file_path)
    ans = await call_gemini(prompt, data.read(), "image/jpeg")
    await message.answer(ans)

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    ans = await call_gemini(message.text)
    await message.answer(ans)

# --- Настройка Web-сервера ---
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

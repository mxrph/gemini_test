import os
import asyncio
import logging
import base64
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

# Конфигурация Gemini
genai.configure(api_key=API_KEY)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Эти модели из твоего списка точно поддерживают метод generateContent для картинок
PRIMARY_MODEL_NAME = "models/gemini-3-flash-preview"
IMAGE_MODEL_NAME = "models/gemini-3-pro-image-preview" # Или "models/gemini-2.5-flash-image"
VIDEO_MODEL_NAME = "models/veo-3.0-generate-001"

# Настройки безопасности
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

chat_session = None

async def call_gemini(text, data=None, mime_type=None):
    global chat_session
    try:
        model = genai.GenerativeModel(PRIMARY_MODEL_NAME, safety_settings=SAFETY_SETTINGS)
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
        logger.error(f"Ошибка чата: {e}")
        return f"Ошибка: {e}"

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. /help — список команд.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Команды:\n"
        "/image [текст] — Генерация фото (Imagen 4.0)\n"
        "/video [текст] — Генерация видео (Veo 3.0)\n"
        "/listmodels — Посмотреть список доступных моделей\n"
        "/reset — Очистить чат"
    )

@dp.message(Command("listmodels"))
async def list_models_cmd(message: types.Message):
    try:
        models = [m.name for m in genai.list_models()]
        text = "Доступные модели:\n\n" + "\n".join(models)
        # Если текст слишком длинный, режем его для Telegram
        await message.answer(text[:4000])
    except Exception as e:
        await message.answer(f"Не удалось получить список: {e}")

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    if not prompt: return await message.answer("Укажите описание.")
    
    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        # Используем гибридную модель из твоего списка
        model = genai.GenerativeModel("models/gemini-3-pro-image-preview")
        
        # Для этих моделей важно явно попросить сгенерировать файл
        response = model.generate_content(
            f"Please generate an image based on this description: {prompt}",
            safety_settings=SAFETY_SETTINGS
        )
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                # Ищем данные изображения в частях ответа
                data_source = getattr(part, 'inline_data', getattr(part, 'blob', None))
                if data_source:
                    image_bytes = data_source.data
                    if isinstance(image_bytes, str):
                        image_bytes = base64.b64decode(image_bytes)
                    return await message.answer_photo(BufferedInputFile(image_bytes, filename="gen.jpg"))
        
        await message.answer("Модель ответила текстом, но не прислала картинку. Попробуйте другой промпт.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await message.answer(f"Ошибка: {e}")

@dp.message(Command("video"))
async def video_gen_cmd(message: types.Message):
    prompt = message.text.replace("/video", "").strip()
    if not prompt: return await message.answer("Укажите описание для видео.")
    
    await message.answer("Генерирую видео через Veo 3.0...")
    try:
        model = genai.GenerativeModel(VIDEO_MODEL_NAME)
        response = model.generate_content(prompt, safety_settings=SAFETY_SETTINGS)
        
        if response.candidates and response.candidates[0].content.parts:
            part = response.candidates[0].content.parts[0]
            video_data = getattr(part, 'inline_data', getattr(part, 'blob', None))
            if video_data:
                video_bytes = video_data.data
                if isinstance(video_bytes, str):
                    video_bytes = base64.b64decode(video_bytes)
                return await message.answer_video(BufferedInputFile(video_bytes, filename="video.mp4"))
        await message.answer("Видео не создано.")
    except Exception as e:
        await message.answer(f"Ошибка видео: {e}")

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

async def health(request):
    return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


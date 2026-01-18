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

# АКТУАЛЬНЫЕ МОДЕЛИ ИЗ ТВОЕГО СПИСКА (2026)
PRIMARY_MODEL_NAME = "models/gemini-3-flash-preview"
IMAGE_MODEL_NAME = "models/imagen-4.0-generate-001"
VIDEO_MODEL_NAME = "models/veo-3.0-generate-001"

# Настройки безопасности (отключаем лишние блокировки)
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
        return "Произошла ошибка при обращении к текстовой модели."

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен на базе Gemini 3 и Imagen 4.0! Используйте /help.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Доступные команды:\n\n"
        "/image [описание] — Генерация фото (Imagen 4.0)\n"
        "/video [описание] — Генерация видео (Veo 3.0)\n"
        "/reset — Очистить историю чата\n\n"
        "Вы также можете присылать картинки и PDF для анализа."
    )

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    if not prompt:
        return await message.answer("Пожалуйста, введите описание после команды /image.")
    
    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        model = genai.GenerativeModel(IMAGE_MODEL_NAME, safety_settings=SAFETY_SETTINGS)
        response = model.generate_content(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            part = response.candidates[0].content.parts[0]
            # Получаем данные изображения (поддержка blob и inline_data)
            image_data = getattr(part, 'inline_data', getattr(part, 'blob', None))
            
            if image_data:
                image_bytes = image_data.data
                if isinstance(image_bytes, str):
                    image_bytes = base64.b64decode(image_bytes)
                
                return await message.answer_photo(
                    BufferedInputFile(image_bytes, filename="generated_image.jpg"),
                    caption=f"Результат Imagen 4.0 по запросу: {prompt[:50]}..."
                )
        
        await message.answer("Не удалось сгенерировать изображение. Возможно, запрос отклонен фильтрами.")
    except Exception as e:
        logger.error(f"Ошибка Imagen 4.0: {e}")
        await message.answer(f"Ошибка генерации: {e}")

@dp.message(Command("video"))
async def video_gen_cmd(message: types.Message):
    prompt = message.text.replace("/video", "").strip()
    if not prompt:
        return await message.answer("Введите описание для видео.")
    
    await message.answer("Генерирую видео через Veo 3.0 (это может занять время)...")
    try:
        model = genai.GenerativeModel(VIDEO_MODEL_NAME, safety_settings=SAFETY_SETTINGS)
        response = model.generate_content(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            part = response.candidates[0].content.parts[0]
            video_data = getattr(part, 'inline_data', getattr(part, 'blob', None))
            
            if video_data:
                video_bytes = video_data.data
                if isinstance(video_bytes, str):
                    video_bytes = base64.b64decode(video_bytes)
                
                return await message.answer_video(
                    BufferedInputFile(video_bytes, filename="generated_video.mp4")
                )
        
        await message.answer("Видео не было создано.")
    except Exception as e:
        logger.error(f"Ошибка Veo 3.0: {e}")
        await message.answer("Ваш аккаунт или регион пока не поддерживает Veo 3.0 через API.")

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("История диалога успешно очищена.")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    file_info = await bot.get_file(message.photo[-1].file_id)
    data = await bot.download_file(file_info.file_path)
    ans = await call_gemini(message.caption or "Что изображено на этом фото?", data.read(), "image/jpeg")
    await message.answer(ans)

@dp.message(F.document)
async def handle_docs(message: types.Message):
    if message.document.mime_type == "application/pdf":
        file_info = await bot.get_file(message.document.file_id)
        data = await bot.download_file(file_info.file_path)
        ans = await call_gemini("Проанализируй содержимое этого PDF-документа:", data.read(), "application/pdf")
        await message.answer(ans)

@dp.message(F.text)
async def handle_text(message: types.Message):
    # Если MY_ID настроен, отвечаем только владельцу
    if MY_ID and message.from_user.id != MY_ID:
        return
    ans = await call_gemini(message.text)
    await message.answer(ans)

# Эндпоинт для проверки жизнеспособности (Health Check)
async def health(request):
    return web.Response(text="Бот активен")

async def main():
    # Создаем веб-сервер для поддержки работы на PaaS (Render, Railway и т.д.)
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    
    # Запуск бота
    logger.info("Запуск Telegram бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")

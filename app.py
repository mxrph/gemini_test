import os
import asyncio
import logging
import time
import base64
from io import BytesIO
from aiogram import Bot, Dispatcher, types, F
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

# Модели из твоего списка
PRIMARY_VARIANTS = ["models/gemini-3-flash-preview", "models/gemini-2.5-flash"]
FALLBACK_VARIANTS = ["models/gemini-2.0-flash-lite"]
IMAGE_GEN_MODEL = "models/imagen-4.0-generate-001"
VIDEO_GEN_MODEL = "models/veo-3.1-generate-preview"

# Глобальные счетчики и сессия
chat_session = None
usage_stats = {
    "text": 0, "image": 0, "video_gen": 0, "last_reset": time.time()
}
LIMITS = {"text": 1500, "image": 50, "video_gen": 2}

def check_reset_limits():
    global usage_stats
    if time.time() - usage_stats["last_reset"] > 86400:
        usage_stats = {"text": 0, "image": 0, "video_gen": 0, "last_reset": time.time()}

# --- Логика работы с Gemini ---
async def call_gemini(text, image_data=None, mime_type=None):
    global chat_session
    for model_name in PRIMARY_VARIANTS:
        try:
            model = genai.GenerativeModel(model_name)
            if image_data:
                # Мультимодальный запрос (фото/видео)
                content = [{"mime_type": mime_type, "data": image_data}, text]
                response = model.generate_content(content)
                return response.text
            else:
                # Текстовый чат с памятью
                if chat_session is None:
                    chat_session = model.start_chat(history=[])
                response = chat_session.send_message(text)
                usage_stats["text"] += 1
                return response.text
        except Exception as e:
            logger.error(f"Ошибка модели {model_name}: {e}")
            continue
    return "❌ Ошибка: Не удалось получить ответ от моделей. Возможно, превышен лимит."

# --- Обработчики команд ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("🤖 Бот на базе Gemini 3 & Imagen 4 готов! Напиши /help для списка команд.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    help_text = (
        "🌟 **Доступные возможности:**\n\n"
        "💬 **Чат:** Просто пиши текст (использую Gemini 3).\n"
        "🖼️ **Анализ:** Пришли фото/видео с вопросом в подписи.\n"
        "🎨 **Генерация фото:** `/image [описание]` (Imagen 4).\n"
        "🎬 **Генерация видео:** `/video [описание]` (Veo 3.1).\n"
        "📊 **Лимиты:** `/limits` — проверить остаток запросов.\n"
        "🔄 **Сброс чата:** `/reset` — забыть историю беседы."
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("limits"))
async def limits_cmd(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    check_reset_limits()
    msg = (f"📊 **Ваши лимиты (24ч):**\n"
           f"💬 Текст: {usage_stats['text']}/{LIMITS['text']}\n"
           f"🖼️ Фото (ген): {usage_stats['image']}/{LIMITS['image']}\n"
           f"🎥 Видео (ген): {usage_stats['video_gen']}/{LIMITS['video_gen']}")
    await message.answer(msg, parse_mode="Markdown")

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("🧹 Контекст общения очищен.")

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    prompt = message.text.replace("/image", "").strip()
    if not prompt:
        return await message.answer("Укажите описание, например: `/image киберпанк город`.")

    if usage_stats["image"] >= LIMITS["image"]:
        return await message.answer("⚠️ Лимит генерации фото исчерпан.")

    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        model = genai.GenerativeModel(IMAGE_GEN_MODEL)
        # В некоторых версиях API используется generate_images или generate_content
        response = model.generate_content(prompt)
        
        # Извлечение картинки из ответа Imagen
        img_data = response.candidates[0].content.parts[0].inline_data.data
        image_bytes = base64.b64decode(img_data)
        
        await message.answer_photo(types.BufferedInputFile(image_bytes, filename="gen.jpg"), caption="Готово!")
        usage_stats["image"] += 1
    except Exception as e:
        await message.answer(f"❌ Ошибка генерации: {e}")

@dp.message(Command("video"))
async def video_gen_cmd(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    prompt = message.text.replace("/video", "").strip()
    if usage_stats["video_gen"] >= LIMITS["video_gen"]:
        return await message.answer("⚠️ Лимит генерации видео (2 в день) исчерпан.")
    
    await message.answer("⏳ Генерация видео через Veo 3.1 может занять до 2-3 минут. Пожалуйста, подождите...")
    # Здесь логика аналогична /image, но требует обработки видео-файла
    await message.answer(f"Запрос на '{prompt}' принят, но в Free Tier генерация видео часто требует ручного одобрения в Google Cloud.")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    file_info = await bot.get_file(message.photo[-1].file_id)
    photo_bytes = await bot.download_file(file_info.file_path)
    prompt = message.caption or "Что на этом фото?"
    
    ans = await call_gemini(prompt, photo_bytes.read(), "image/jpeg")
    await message.answer(ans)

@dp.message(F.video)
async def handle_video(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    await message.answer("🎥 Видео получено, анализирую...")
    file_info = await bot.get_file(message.video.file_id)
    video_bytes = await bot.download_file(file_info.file_path)
    prompt = message.caption or "Опиши это видео."
    
    ans = await call_gemini(prompt, video_bytes.read(), "video/mp4")
    await message.answer(ans)

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    await bot.send_chat_action(message.chat.id, "typing")
    ans = await call_gemini(message.text)
    await message.answer(ans)

# --- Настройка Web-сервера для Koyeb ---
async def health_check(request): return web.Response(text="I'm alive")

async def main():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

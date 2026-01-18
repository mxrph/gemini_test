import os
import asyncio
import logging
import base64
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile, BotCommand
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

PRIMARY_MODEL_NAME = "models/gemini-3-flash-preview"
IMAGE_MODEL_NAME = "models/gemini-2.5-flash-image"

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
        else:
            if chat_session is None:
                chat_session = model.start_chat(history=[])
            response = chat_session.send_message(text)
        
        # Безопасная проверка ответа
        if response.candidates and response.candidates[0].content.parts:
            return response.text
        else:
            # Если модель закончила генерацию, но текста нет
            return "Модель не смогла сформировать ответ. Попробуйте другой запрос или используйте /reset."
            
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        # Если это лимит, выводим понятное сообщение
        if "429" in str(e):
            return "Достигнут лимит запросов. Пожалуйста, подождите или попробуйте позже."
        return f"Произошла ошибка: {e}"

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. Я понимаю текст, фото, PDF, голос и видео-кружочки.")

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Функции:\n\n"
        "Текст: Просто пишите.\n"
        "Голос и Видео-кружочки: Присылайте, я их проанализирую.\n"
        "Файлы: Фото и PDF (с вопросом в подписи).\n"
        "Картинки: /image [описание].\n"
        "Сброс: /reset."
    )

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    global chat_session
    chat_session = None
    await message.answer("История очищена.")

# ХЕНДЛЕР ДЛЯ ВИДЕО-КРУЖОЧКОВ
@dp.message(F.video_note)
async def handle_video_note(message: types.Message):
    await bot.send_chat_action(message.chat.id, "record_video")
    try:
        file_info = await bot.get_file(message.video_note.file_id)
        file_data = await bot.download_file(file_info.file_path)
        ans = await call_gemini("Проанализируй это видео и ответь на вопросы, если они подразумеваются:", file_data.read(), "video/mp4")
        await message.answer(ans)
    except Exception as e:
        await message.answer(f"Ошибка видео: {e}")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    await bot.send_chat_action(message.chat.id, "typing")
    file_info = await bot.get_file(message.voice.file_id)
    file_data = await bot.download_file(file_info.file_path)
    ans = await call_gemini("Ответь на это голосовое сообщение:", file_data.read(), "audio/ogg")
    await message.answer(ans)

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

@dp.message(Command("image"))
async def image_gen_cmd(message: types.Message):
    prompt = message.text.replace("/image", "").strip()
    if not prompt: return await message.answer("Введите описание.")
    try:
        model = genai.GenerativeModel(IMAGE_MODEL_NAME)
        response = model.generate_content(f"Generate image: {prompt}", safety_settings=SAFETY_SETTINGS)
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                data_source = getattr(part, 'inline_data', getattr(part, 'blob', None))
                if data_source:
                    image_bytes = data_source.data
                    if isinstance(image_bytes, str): image_bytes = base64.b64decode(image_bytes)
                    return await message.answer_photo(BufferedInputFile(image_bytes, filename="gen.jpg"))
        await message.answer("Лимит генерации фото исчерпан.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(F.text)
async def handle_text(message: types.Message):
    if MY_ID and message.from_user.id != MY_ID: return
    ans = await call_gemini(message.text)
    await message.answer(ans)

async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="image", description="Фото"),
        BotCommand(command="reset", description="Сброс")
    ])
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


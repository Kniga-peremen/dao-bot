import os
import json
import random
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "🔮 Бот активен! Версия 2.0", 200

@app.route('/health')
def health_check():
    return "OK", 200

def run():
    app.run(host='0.0.0.0', port=8080)

# Запускаем Flask-сервер в отдельном потоке
threading.Thread(target=run, daemon=True).start()

# Импорт для Telegram
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler
)
from openai import OpenAI

# Конфигурация
HEXAGRAMS_FILE = "hexagrams.json"
STOP_WORDS_FILE = "stop_words.json"
INTERPRETATIONS_FILE = "interpretations.json"
RATINGS_FILE = "ratings.json"
USER_SESSIONS_FILE = "user_sessions.txt"
ERROR_LOG_FILE = "error.txt"
GPT_MODEL = "gpt-4.1-mini"
ADMIN_ID = 774452314

# Веса для генерации линий
WEIGHTS = {
    "ЯнСтарый": 16,
    "ИньСтарый": 6,
    "Ян": 39,
    "Инь": 39
}

# Загрузка .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('PROXY_API_KEY')

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("Необходимо указать TELEGRAM_TOKEN и OPENAI_API_KEY в .env")

try:
    client = OpenAI(
        api_key=os.getenv("PROXY_API_KEY"),
        base_url="https://api.proxyapi.ru/openai/v1"
    )
except Exception as e:
    with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().isoformat()} - Ошибка инициализации OpenAI: {str(e)}\n")
    raise

# Состояния диалога
FORMULATE_PROBLEM, CONFIRM_QUESTION, HEXAGRAM_INTERPRETATION = range(3)

def load_json_data(filename):
    try:
        file_path = Path(__file__).parent / filename
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if filename == HEXAGRAMS_FILE:
                return {int(k): v for k, v in data.items()}
            return data
    except Exception as e:
        with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - Ошибка загрузки {filename}: {str(e)}\n")
        return {}

HEXAGRAMS = load_json_data(HEXAGRAMS_FILE)
STOP_WORDS_DATA = load_json_data(STOP_WORDS_FILE)
INTERPRETATIONS = load_json_data(INTERPRETATIONS_FILE)

async def log_user_action(user_id: int, username: str, full_name: str, action: str, details: str = ""):
    """Логирование действий пользователя"""
    try:
        with open(USER_SESSIONS_FILE, 'a', encoding='utf-8') as f:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'user_id': user_id,
                'username': username,
                'full_name': full_name,
                'action': action,
                'details': details
            }
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - Ошибка записи в {USER_SESSIONS_FILE}: {str(e)}\n")

async def log_error(error_message: str):
    """Логирование ошибок"""
    try:
        with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - {error_message}\n")
    except Exception as e:
        print(f"Критическая ошибка логирования: {str(e)}")

def contains_stop_words(text: str) -> bool:
    text_lower = text.lower()
    return any(word in text_lower for word in STOP_WORDS_DATA.get("words", []))

def get_stop_word_response() -> str:
    return random.choice(STOP_WORDS_DATA.get("responses", ["Извините, я не могу ответить на этот вопрос"]))

def generate_hexagram():
    lines = [
        random.choices(list(WEIGHTS.keys()), weights=list(WEIGHTS.values()), k=1)[0]
        for _ in range(6)
    ]
    binary = ''.join(['1' if x in ['Ян', 'ЯнСтарый'] else '0' for x in reversed(lines)])
    number = int(binary, 2) + 1
    changing_lines = [i for i, line in enumerate(lines, 1) if "Старый" in line]
    return number, changing_lines, lines

def main_menu():
    return ReplyKeyboardMarkup(
        [["Помочь сформулировать", "Готовый вопрос", "Быстрый ответ И-Цзин"], 
         ["Толкование гексаграммы", "English version ➡️"],
         ["Старт", "Выйти", "Инфо"]],
        resize_keyboard=True
    )

def cancel_menu():
    return ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)

def confirmation_menu():
    return ReplyKeyboardMarkup([["1. Да", "2. Уточнить"], ["3. Свой вариант"]], resize_keyboard=True)

def interpretation_menu():
    return ReplyKeyboardMarkup([["Краткое толкование", "Развернутое толкование"], ["Отмена"]], resize_keyboard=True)

def context_menu():
    return ReplyKeyboardMarkup([
        ["💑 Отношения", "👨‍👩‍👧‍👦 Дети"],
        ["💰 Финансы", "🧘 Здоровье"],
        ["🎓 Образование", "🏛 Бизнес"],
        ["🔮 Общее толкование"]
    ], resize_keyboard=True, one_time_keyboard=True) 

async def draw_changing_lines(number: int, changing_lines: list, user_id: int):
    image_path = Path(__file__).parent / "gg" / f"{number}.png"
    if not image_path.exists():
        await log_error(f"Изображение гексаграммы №{number} не найдено")
        return None

    try:
        img = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        line_spacing = height / 6

        for line_number in changing_lines:
            y = height - (line_number - 0.7) * line_spacing
            x = width * 0.93
            radius = 3
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill="red"
            )

        temp_path = Path(__file__).parent / "temp" / f"{number}_{user_id}.png"
        temp_path.parent.mkdir(exist_ok=True)
        img.save(temp_path)
        return temp_path
    except Exception as e:
        await log_error(f"Ошибка при создании изображения гексаграммы №{number}: {str(e)}")
        return None

async def send_hexagram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Определяем тип обновления (сообщение или callback)
    if update.callback_query:
        message = update.callback_query.message
        user = update.callback_query.from_user
    else:
        message = update.message
        user = update.effective_user

    number, changing_lines, lines = generate_hexagram()
    hex_data = HEXAGRAMS.get(number, ["", f"Гексаграмма №{number}"])
    hex_name = hex_data[1]

    await log_user_action(
        user.id, 
        user.username, 
        user.full_name, 
        "Генерация гексаграммы",
        f"Номер: {number}, Название: {hex_name}, Изменяющиеся линии: {changing_lines}"
    )

    # Отправка изображения
    temp_path = await draw_changing_lines(number, changing_lines, user.id)
    if temp_path:
        try:
            await message.reply_photo(photo=open(temp_path, 'rb'))
        finally:
            temp_path.unlink()
    else:
        await message.reply_text(f"Гексаграмма №{number}")

    # Формирование текста ответа
    response = f"🔮 {number} — {hex_name} ({hex_data[0]})"

    if changing_lines:
        response += f"\n\n♻️ Старые линии: {', '.join(map(str, changing_lines))}"

    if str(number) in INTERPRETATIONS:
        selected = random.choice(INTERPRETATIONS[str(number)])
        response += f"\n\n💬 Быстрый ответ: {selected}"

    await message.reply_text(response)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["question_count"] = 0
    context.user_data["divination_count"] = 0  # Сбрасываем счетчик при старте
    await log_user_action(user.id, user.username, user.full_name, "Начало сессии")
    await update.message.reply_text(
        f"🔮 Привет, {user.full_name}! Я твой персональный Дао-бот. Могу помочь сформулировать вопрос, дать совет или даже заглянуть в будущее. Выбери пункт меню или почитай Инфо.",
        reply_markup=main_menu()
    )

async def exit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user_action(user.id, user.username, user.full_name, "Завершение сессии")
    await update.message.reply_text(
        "Сессия завершена. Для нового диалога нажмите /start",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user_action(user.id, user.username, user.full_name, "Пауза сессии")
    await update.message.reply_text(
        "Сессия приостановлена. Нажмите 'Старт' чтобы продолжить.",
        reply_markup=main_menu()
    )

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("🚷 Команда только для администратора")
        return

    try:
        with open(RATINGS_FILE, 'r', encoding='utf-8') as f:
            ratings = [json.loads(line) for line in f if line.strip()]

        if not ratings:
            await update.message.reply_text("📭 Нет данных для анализа")
            return

        good = sum(1 for r in ratings if r["rate"] == "good")
        bad = sum(1 for r in ratings if r["rate"] == "bad")
        total = good + bad

        await update.message.reply_text(
            f"📊 Статистика оценок:\n\n"
            f"• Пользователей: {len({r['user_id'] for r in ratings})}\n"
            f"• 👍 Хорошо: {good}\n"
            f"• 👎 Неактуально: {bad}\n"
            f"• 📈 % позитивных: {good/total*100:.1f}%"
        )
    except Exception as e:
        await log_error(f"Ошибка показа статистики: {str(e)}")
        await update.message.reply_text("⚠️ Ошибка загрузки данных")

async def send_advice_with_rating(update: Update, text: str, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_advice"] = text
    await update.message.reply_text(
        f"{text}\n\n_Оцените совет:_",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👍 Хорошо", callback_data="rate_good"),
             InlineKeyboardButton("👎 Неактуально", callback_data="rate_bad")]
        ]),
        parse_mode="Markdown"
    )

async def handle_interpretation_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_choice = update.message.text
    context.user_data["interpretation_type"] = user_choice

    if user_choice == "Развернутое толкование":
        await update.message.reply_text(
            "Выберите контекст для толкования:",
            reply_markup=context_menu()
        )
        return HEXAGRAM_INTERPRETATION
    else:
        return await generate_hexagram_interpretation(update, context)

    # функцию для обработки выбора контекста  
async def handle_context_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["interpretation_context"] = update.message.text
    return await generate_hexagram_interpretation(update, context)      

async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    rate = query.data.replace("rate_", "")

    rating_data = {
        "time": datetime.now().isoformat(),
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "advice": context.user_data.get("last_advice", "?"),
        "rate": rate
    }

    try:
        with open(RATINGS_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rating_data, ensure_ascii=False) + "\n")

        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.reply_text(f"✅ Спасибо за оценку!", reply_markup=main_menu())
    except Exception as e:
        await log_error(f"Ошибка обработки оценки: {str(e)}")

async def ready_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user_action(user.id, user.username, user.full_name, "Начало готового вопроса")
    await update.message.reply_text(
        "Напиши свой вопрос, и я постараюсь помочь:",
        reply_markup=cancel_menu()
    )
    return FORMULATE_PROBLEM

async def process_ready_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_text = update.message.text

    if user_text.lower() == "отмена":
        await log_user_action(user.id, user.username, user.full_name, "Отмена готового вопроса")
        await update.message.reply_text("Отменено.", reply_markup=main_menu())
        return ConversationHandler.END

    if contains_stop_words(user_text):
        await log_user_action(user.id, user.username, user.full_name, "Стоп-слова в готовом вопросе")
        response = get_stop_word_response()
        await update.message.reply_text(response, reply_markup=main_menu())
        return ConversationHandler.END

    await log_user_action(user.id, user.username, user.full_name, "Готовый вопрос", user_text)
    advice = await generate_advice(user_text, context)
    await send_advice_with_rating(update, f"🔮 Дао-бот говорит:\n\n{advice}", context)
    return ConversationHandler.END

async def start_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["user_name"] = user.full_name
    await log_user_action(user.id, user.username, user.full_name, "Начало помощи в формулировке")
    await update.message.reply_text(
        "Опиши свою проблему хотя бы одним предложением:",
        reply_markup=cancel_menu()
    )
    return FORMULATE_PROBLEM

async def formulate_problem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if update.message.text.lower() == "отмена":
        await log_user_action(user.id, user.username, user.full_name, "Отмена формулировки")
        await update.message.reply_text("Отменено.", reply_markup=main_menu())
        return ConversationHandler.END

    problem_text = update.message.text

    if contains_stop_words(problem_text):
        await log_user_action(user.id, user.username, user.full_name, "Стоп-слова в запросе")
        response = get_stop_word_response()
        await update.message.reply_text(response, reply_markup=main_menu())
        return ConversationHandler.END

    await log_user_action(user.id, user.username, user.full_name, "Формулировка проблемы", problem_text)
    context.user_data["problem"] = problem_text

    try:
        question = await generate_clear_question(problem_text)
        context.user_data["current_question"] = question
        await update.message.reply_text(
            f"🔍 Ты имеешь в виду:\n\n«{question}»\n\n"
            "1. Да, верно\n2. Нет, уточнить\n3. Свой вариант",
            reply_markup=confirmation_menu()
        )
        return CONFIRM_QUESTION
    except Exception as e:
        await log_error(f"Ошибка в formulate_problem: {str(e)}")
        await update.message.reply_text("Ошибка обработки запроса", reply_markup=main_menu())
        return ConversationHandler.END

async def generate_clear_question(text: str) -> str:
    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "Сформулируй проблему как четкий вопрос"},
                {"role": "user", "content": text}
            ],
            temperature=0.3,
            max_tokens=50
        )
        return response.choices[0].message.content.strip('"')
    except Exception as e:
        await log_error(f"Ошибка уточнения вопроса: {str(e)}")
        return text

async def confirm_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_text = update.message.text
    question = context.user_data.get("current_question", "")

    if context.user_data.get("waiting_for_custom_question", False):
        if contains_stop_words(user_text):
            await log_user_action(user.id, user.username, user.full_name, "Стоп-слова в кастомном вопросе")
            response = get_stop_word_response()
            await update.message.reply_text(response, reply_markup=main_menu())
            context.user_data.pop("waiting_for_custom_question", None)
            return ConversationHandler.END

        context.user_data["current_question"] = user_text
        advice = await generate_advice(user_text, context)
        await send_advice_with_rating(update, f"🔮 Дао-бот говорит:\n\n{advice}", context)
        context.user_data.pop("waiting_for_custom_question", None)
        return ConversationHandler.END

    if user_text.startswith(("1", "Да")):
        await log_user_action(user.id, user.username, user.full_name, "Подтверждение вопроса", question)
        advice = await generate_advice(question, context)
        await send_advice_with_rating(update, f"🔮 Дао-бот говорит:\n\n{advice}", context)
        return ConversationHandler.END
    elif user_text.startswith(("2", "Уточнить")):
        await log_user_action(user.id, user.username, user.full_name, "Запрос уточнения")
        await update.message.reply_text("Опиши проблему более подробно:", reply_markup=cancel_menu())
        return FORMULATE_PROBLEM
    elif user_text.startswith(("3", "Свой вариант")):
        await log_user_action(user.id, user.username, user.full_name, "Запрос своего варианта")
        await update.message.reply_text("Введи свой вариант вопроса:", reply_markup=cancel_menu())
        context.user_data["waiting_for_custom_question"] = True
        return CONFIRM_QUESTION

    await update.message.reply_text(
        "Пожалуйста, выбери один из вариантов:\n\n"
        "1. Да, верно\n2. Нет, уточнить\n3. Свой вариант",
        reply_markup=confirmation_menu()
    )
    return CONFIRM_QUESTION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user_action(user.id, user.username, user.full_name, "Отмена действия")
    await update.message.reply_text("Действие отменено.", reply_markup=main_menu())
    return ConversationHandler.END

async def timeout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user_action(user.id, user.username, user.full_name, "Тайм-аут диалога")
    context.user_data.clear()
    await update.message.reply_text(
        "⏰ Время ожидания истекло. Если хочешь продолжить, нажми 'Старт'.",
        reply_markup=main_menu()
    )
    return ConversationHandler.END

async def divination_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Инициализация счетчика, если его нет
    if "divination_count" not in context.user_data:
        context.user_data["divination_count"] = 0

    # Увеличиваем счетчик
    context.user_data["divination_count"] += 1

    # Проверка на 3-е нажатие
    if context.user_data["divination_count"] == 3:
        # Создаем клавиатуру с вариантами ответа
        reply_markup = ReplyKeyboardMarkup(
            [["Да", "Нет"]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text(
            "Вы собрались третий раз задать мысленный вопрос И-Цзин. Уверены?",
            reply_markup=reply_markup
        )
        # Устанавливаем состояние ожидания ответа
        context.user_data["awaiting_confirmation"] = True
        return

    # Если пользователь подтвердил или это не 3-й раз
    if context.user_data.get("awaiting_confirmation", False):
        user_choice = update.message.text.lower()
        if user_choice == "нет":
            await update.message.reply_text(
                "Спасибо за мудрое решение",
                reply_markup=main_menu()
            )
            # Сбрасываем счетчик и состояние
            context.user_data["divination_count"] = 0
            context.user_data.pop("awaiting_confirmation", None)
            # Возвращаемся в начало (эмулируем нажатие "Старт")
            await start_command(update, context)
            return
        elif user_choice == "да":
            context.user_data.pop("awaiting_confirmation", None)
            # Продолжаем как обычно

    # Основная логика генерации гексаграммы
    await send_hexagram(update, context)

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        info_path = Path(__file__).parent / "info.txt"
        with open(info_path, 'r', encoding='utf-8') as f:
            info_text = f.read()
        await update.message.reply_text(info_text, reply_markup=main_menu())
        await log_user_action(update.effective_user.id, update.effective_user.username, 
                            update.effective_user.full_name, "Просмотр информации")
    except Exception as e:
        await log_error(f"Ошибка чтения info.txt: {str(e)}")
        await update.message.reply_text(
            "Дао-бот может:\n1) Генерировать гексаграммы И-Цзин\n2) Давать советы на основе ИИ",
            reply_markup=main_menu()
        )

async def start_hexagram_interpretation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user_action(user.id, user.username, user.full_name, "Начало толкования гексаграммы")
    await update.message.reply_text(
        "Введите номер гексаграммы и изменяющиеся линии (если есть) в формате:\n\n"
        "Например: 43.1,2 или 22\n\n"
        "Где 43 - номер гексаграммы, а 1,2 - изменяющиеся линии",
        reply_markup=cancel_menu()
    )
    return HEXAGRAM_INTERPRETATION

async def process_hexagram_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_input = update.message.text.strip()

    if user_input.lower() == "отмена":
        await log_user_action(user.id, user.username, user.full_name, "Отмена толкования гексаграммы")
        await update.message.reply_text("Отменено.", reply_markup=main_menu())
        return ConversationHandler.END

    try:
        # Парсим ввод пользователя
        if '.' in user_input:
            parts = user_input.split('.')
            hex_number = int(parts[0].strip())
            changing_lines = [int(x.strip()) for x in parts[1].split(',')] if len(parts) > 1 and parts[1] else []
        else:
            hex_number = int(user_input)
            changing_lines = []

        # Проверяем корректность номера гексаграммы
        if hex_number < 1 or hex_number > 64:
            raise ValueError("Номер гексаграммы должен быть от 1 до 64")

        # Проверяем корректность номеров линий
        for line in changing_lines:
            if line < 1 or line > 6:
                raise ValueError("Номера линий должны быть от 1 до 6")

        # Сохраняем данные в контексте
        context.user_data["hex_number"] = hex_number
        context.user_data["changing_lines"] = changing_lines

        # Получаем данные гексаграммы
        hex_data = HEXAGRAMS.get(hex_number, ["", f"Гексаграмма №{hex_number}"])

        # Формируем сообщение с подтверждением
        response = f"🔮 Гексаграмма {hex_number} — {hex_data[1]} ({hex_data[0]})"
        if changing_lines:
            response += f"\n\n♻️ Изменяющиеся линии: {', '.join(map(str, changing_lines))}"

        response += "\n\nВыберите тип толкования:"

        await update.message.reply_text(response, reply_markup=interpretation_menu())
        return HEXAGRAM_INTERPRETATION

    except ValueError as e:
        await update.message.reply_text(
            f"Ошибка ввода: {str(e)}\n\nПожалуйста, введите данные в правильном формате, например:\n\n43.1,2 или 22",
            reply_markup=cancel_menu()
        )
        return HEXAGRAM_INTERPRETATION
    except Exception as e:
        await log_error(f"Ошибка обработки ввода гексаграммы: {str(e)}")
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте еще раз.",
            reply_markup=cancel_menu()
        )
        return HEXAGRAM_INTERPRETATION

    except ValueError as e:
        await update.message.reply_text(
            f"Ошибка ввода: {str(e)}\n\nПожалуйста, введите данные в правильном формате, например:\n\n43.1,2 или 22",
            reply_markup=cancel_menu()
        )
        return HEXAGRAM_INTERPRETATION
    except Exception as e:
        await log_error(f"Ошибка обработки ввода гексаграммы: {str(e)}")
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте еще раз.",
            reply_markup=cancel_menu()
        )
        return HEXAGRAM_INTERPRETATION

async def generate_hexagram_interpretation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    hex_number = context.user_data["hex_number"]
    changing_lines = context.user_data.get("changing_lines", [])
    hex_data = HEXAGRAMS.get(hex_number, ["", f"Гексаграмма №{hex_number}"])
    interpretation_type = context.user_data.get("interpretation_type", "Краткое толкование")

    try:
        if interpretation_type == "Краткое толкование":
            prompt = f"Дайте краткое толкование (2-3 предложения) гексаграммы {hex_number} '{hex_data[1]}'"
            if changing_lines:
                prompt += f" с учетом изменяющихся линий: {', '.join(map(str, changing_lines))}"
            prompt += ". Будьте лаконичны."
            max_tokens = 100
        else:
            context_type = context.user_data.get("interpretation_context", "🔮 Общее толкование")
            context_prompts = {
                 "💑 Отношения": "Сосредоточьтесь на аспектах любовных, семейных и межличностных отношений.",
                "👨‍👩‍👧‍👦 Дети": "Дайте толкование в контексте воспитания детей, родительства и детского развития.",
                "💰 Финансы": "Сделайте акцент на финансовых аспектах, инвестициях и карьерном росте.",
                "🧘 Здоровье": "Интерпретируйте с точки зрения физического и психического здоровья.",
                "🎓 Образование": "Рассмотрите в контексте обучения, саморазвития и приобретения знаний.",
                "🏛 Бизнес": "Дайте толкование для бизнес-решений, управления и предпринимательства.",
                "🔮 Общее толкование": "Дайте развернутое толкование без специфического контекста."
            }

            prompt = f"Дайте развернутое толкование гексаграммы {hex_number} '{hex_data[1]}' "
            prompt += f"в контексте: {context_type}. {context_prompts.get(context_type, '')}\n\n"
            if changing_lines:
                prompt += f"Учтите изменяющиеся линии: {', '.join(map(str, changing_lines))}.\n"
            prompt += "Структурируйте ответ:\n1. Общее значение (1 предложение)\n2. Особенности в выбранном контексте (1 предложение)\n3. Толкование линий (1 предложение)\n4. Практические рекомендации (1 предложение)"
            max_tokens = 350

        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "Вы специалист по И-Цзин. Дайте точное толкование гексаграммы с учетом контекста"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=max_tokens
        )

        interpretation = response.choices[0].message.content

        if interpretation_type == "Развернутое толкование":
            header = f"🔮 Развернутое толкование ({context_type}) гексаграммы {hex_number} — {hex_data[1]}:\n\n"
        else:
            header = f"🔮 Краткое толкование гексаграммы {hex_number} — {hex_data[1]}:\n\n"

        await update.message.reply_text(
            header + interpretation,
            reply_markup=main_menu()
        )

        # Очищаем временные данные
        context.user_data.pop("interpretation_type", None)
        context.user_data.pop("interpretation_context", None)

        return ConversationHandler.END

    except Exception as e:
        await log_error(f"Ошибка генерации толкования: {str(e)}")
        await update.message.reply_text(
            "Произошла ошибка при генерации толкования. Пожалуйста, попробуйте позже.",
            reply_markup=main_menu()
        )
        return ConversationHandler.END

async def handle_unrecognized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Добавляем обработку ответов на подтверждение
    if context.user_data.get("awaiting_confirmation", False):
        await divination_command(update, context)
        return

    user = update.effective_user
    user_text = update.message.text
    await log_user_action(user.id, user.username, user.full_name, "Неопознанное сообщение", user_text)
    reply = await generate_fallback_reply(user_text)
    await update.message.reply_text(reply, reply_markup=main_menu())

async def generate_advice(question: str, context: ContextTypes.DEFAULT_TYPE):
    if contains_stop_words(question):
        await log_user_action(context.user_data.get("user_id", 0), 
                            context.user_data.get("username", ""), 
                            context.user_data.get("full_name", ""), 
                            "Обнаружены стоп-слова", question)
        return get_stop_word_response()

    hex_num, changing_lines, _ = generate_hexagram()
    hex_data = HEXAGRAMS.get(hex_num, ["", ""])

    prompt = (
        f"Пользователь спрашивает:\n"
        f"«{question}»\n\n"
        f"*Скрытый контекст*:\n"
        f"Гексаграмма {hex_num}: {hex_data[1]}\n"
        f"{'Изменяющиеся линии: ' + ', '.join(map(str, changing_lines)) if changing_lines else ''}\n\n"
        f"Дай практичный совет (2 предложения), упоминая номер гексаграммы, название и изменяющиеся линии (если будут)."
    )

    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "Ты — ментор Silicon Valley, который помогает решать проблемы методами design thinking. Твои советы — конкретные шаги, проверенные кейсы и неочевидные инсайты."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=150
        )
        advice = response.choices[0].message.content
        context.user_data["question_count"] = context.user_data.get("question_count", 0) + 1

        await log_user_action(
            context.user_data.get("user_id", 0), 
            context.user_data.get("username", ""), 
            context.user_data.get("full_name", ""), 
            "Сгенерирован совет", 
            f"Вопрос: {question}\nГексаграмма: {hex_num} {hex_data[1]}\nСовет: {advice}"
        )
        return advice
    except Exception as e:
        await log_error(f"Ошибка GPT при генерации совета: {str(e)}")
        return "Произошла ошибка. Попробуйте позже."

async def generate_fallback_reply(user_text: str):
    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "Ты — вежливый, мудрый собеседник."},
                {"role": "user", "content": user_text}
            ],
            temperature=0.7,
            max_tokens=100
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        await log_error(f"Ошибка обработки необработанного сообщения: {str(e)}")
        return "Я тебя понял. Спасибо за сообщение."

def main():
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()

        # Основные команды
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("stats", show_stats))
        app.add_handler(MessageHandler(filters.Regex(r"^(Старт|Start)$"), start_command))
        app.add_handler(MessageHandler(filters.Regex(r"^(Выйти|Exit)$"), exit_command))
        app.add_handler(MessageHandler(filters.Regex(r"^(Быстрый ответ И-Цзин|Divination)$"), divination_command))
        app.add_handler(MessageHandler(filters.Regex(r"^(Инфо|Info)$"), info_command))
        app.add_handler(CallbackQueryHandler(handle_rating, pattern="^rate_"))
        app.add_handler(MessageHandler(filters.Regex(r"^English version ➡️$"), 
             lambda update, context: update.message.reply_text(
                 "For English version, please visit @TaoDronBot",
                 reply_markup=main_menu()
             )))

        # Обработчик для толкования гексаграмм
        hex_interpretation_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex(r"^Толкование гексаграммы$"), start_hexagram_interpretation)],
    states={
        HEXAGRAM_INTERPRETATION: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^(Краткое толкование|Развернутое толкование)$") & 
                ~filters.Regex(r"^(💑 Отношения|👨‍👩‍👧‍👦 Дети|💰 Финансы|🧘 Здоровье|🎓 Образование|🏛 Бизнес|🔮 Общее толкование)$"),
                process_hexagram_input
            ),
            MessageHandler(
                filters.Regex(r"^(Краткое толкование|Развернутое толкование)$"),
                handle_interpretation_choice
            ),
            MessageHandler(
                filters.Regex(r"^(💑 Отношения|👨‍👩‍👧‍👦 Дети|💰 Финансы|🧘 Здоровье|🎓 Образование|🏛 Бизнес|🔮 Общее толкование)$"),
                handle_context_choice
            )
        ],
    },
    fallbacks=[
        CommandHandler("cancel", cancel),
        MessageHandler(filters.ALL, timeout_handler)
    ],
    conversation_timeout=300
)

        app.add_handler(hex_interpretation_handler)

        # Обработчик для готовых вопросов
        ready_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r"^(Готовый вопрос|Ready question)$"), ready_question)],
            states={
                FORMULATE_PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ready_question)],
            },
            fallbacks=[
                CommandHandler("cancel", cancel),
                MessageHandler(filters.ALL, timeout_handler)
            ],
            conversation_timeout=300
        )
        app.add_handler(ready_handler)

        # Обработчик для помощи в формулировке вопроса
        help_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r"^(Помочь сформулировать|Help)$"), start_help)],
            states={
                FORMULATE_PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, formulate_problem)],
                CONFIRM_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_question)]
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            conversation_timeout=300
        )
        app.add_handler(help_handler)

        # Обработчик для нераспознанных сообщений
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unrecognized))

        app.run_polling()
    except Exception as e:
        with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - ФАТАЛЬНАЯ ОШИБКА: {str(e)}\n")
        raise

if __name__ == "__main__":
    main()
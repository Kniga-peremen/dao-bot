from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')  # Главная страница
def home():
    return "Бот работает! Сервер активен."

def run():
    app.run(host='0.0.0.0', port=8080)  # Запускаем сервер

def keep_alive():
    t = Thread(target=run)  # Запускаем в отдельном потоке
    t.start()
import asyncio
import logging
import re
import sys
import paramiko

from aiogram import Bot, Dispatcher, types, Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

from aiogram.filters import Command

import os
from dotenv import load_dotenv
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")

EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
PHONE_PATTERN = r'(\+7|8)?\s?(\d{3}|\d{3})\s?\d{3}\s?\d{2}\s?\d{2}'

DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')

SSH_HOST = os.getenv('SSH_HOST')
SSH_PORT = int(os.getenv('SSH_PORT'))
SSH_USER = os.getenv('SSH_USER')
SSH_PASS = os.getenv('SSH_PASS')

import psycopg2

# Подключение к PostgreSQL
conn = psycopg2.connect(
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT
)

cur = conn.cursor()

storage = MemoryStorage()
form_router = Router()


class SearchState(StatesGroup):
    waiting_for_email_or_phone = State()
    waiting_for_email_and_phone = State()
    waiting_for_password = State()
    waiting_for_command = State()


def create_table():
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE,
            phone VARCHAR(20) UNIQUE
        )
    """)
    conn.commit()


create_table()  # Создаем таблицу при старте бота


def execute_ssh_command(command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASS)
    stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode('utf-8')
    error = stderr.read().decode('utf-8')
    client.close()
    if error:
        return f"Error: {error}"
    return output


@form_router.message(Command('start'))
async def start_bot(message: types.Message):
    await message.reply("Добро пожаловать!")


@form_router.message(Command('monitor'))
async def start_monitoring(message: types.Message, state: FSMContext):
    await message.reply("Введите команду для выполнения на удаленном сервере:\n\n"
                        "/get_release - О релизе\n"
                        "/get_uname - Об архитектуры процессора, имени хоста системы и версии ядра\n"
                        "/get_uptime - О времени работы\n"
                        "/get_df - Сбор информации о состоянии файловой системы\n"
                        "/get_free - Сбор информации о состоянии оперативной памяти\n"
                        "/get_mpstat - Сбор информации о производительности системы\n"
                        "/get_w - Сбор информации о работающих в данной системе пользователях\n"
                        "/get_auths - Последние 10 входов в систему\n"
                        "/get_critical - Последние 5 критических события\n"
                        "/get_ps - Сбор информации о запущенных процессах\n"
                        "/get_ss - Сбор информации об используемых портах\n"
                        "/get_apt_list - Сбор информации об установленных пакетах")
    await state.set_state(SearchState.waiting_for_command)


@form_router.message(SearchState.waiting_for_command)
async def process_command(message: types.Message, state: FSMContext):
    commands = {
        '/get_release': 'cat /etc/os-release',
        '/get_uname': 'uname -a',
        '/get_uptime': 'uptime',
        '/get_df': 'df -h',
        '/get_free': 'free -m',
        '/get_mpstat': 'mpstat 1 5',
        '/get_w': 'w',
        '/get_auths': 'last | head -n 10',
        '/get_critical': 'dmesg | grep -E "CRITICAL|FATAL" | head -n 5',
        '/get_ps': 'ps aux',
        '/get_ss': 'ss -tunap',
        '/get_apt_list': 'apt list --installed'
    }

    command = message.text.lower()
    if command in commands:
        result = execute_ssh_command(commands[command])
        await message.reply(f"Результат выполнения команды: {result}")
    else:
        result = "Неверная команда. Пожалуйста, используйте одну из поддерживаемых команд."
        await message.reply(result)

    await state.clear()


@form_router.message(Command('find'))
async def start_search(message: types.Message, state: FSMContext):
    await message.reply("Пожалуйста, укажите email или номер телефона для поиска в базе данных.")
    await state.set_state(SearchState.waiting_for_email_or_phone)


@form_router.message(SearchState.waiting_for_email_or_phone)
async def process_search(message: types.Message, state: FSMContext):
    data = message.text
    cur.execute("SELECT * FROM contacts WHERE email = %s OR phone = %s", (data, data))
    result = cur.fetchone()
    if result:
        await message.reply(f"Контакт с email или телефоном '{data}' найден в базе данных.")
    else:
        await message.reply(f"Контакт с email или телефоном '{data}' не найден в базе данных.")
    await state.clear()


@form_router.message(Command('add_contact'))
async def add_contact(message: types.Message, state: FSMContext):
    await message.reply("Пожалуйста, укажите email и номер телефона через пробел.")
    await state.set_state(SearchState.waiting_for_email_and_phone)


@form_router.message(SearchState.waiting_for_email_and_phone)
async def process_add_contact(message: types.Message, state: FSMContext):
    data = message.text.split()
    if len(data) != 2:
        await message.reply("Некорректный формат ввода. Пожалуйста, укажите email и номер телефона через пробел.")
        return

    email, phone = data
    try:
        cur.execute("INSERT INTO contacts (email, phone) VALUES (%s, %s)", (email, phone))
        conn.commit()
        await message.reply(f"Контакт с email '{email}' и телефоном '{phone}' успешно добавлен в базу данных.")
    except psycopg2.IntegrityError:
        conn.rollback()  # Откат транзакции при возникновении ошибки
        await message.reply(f"Контакт с email '{email}' или телефоном '{phone}' уже существует в базе данных.")
    await state.clear()


@form_router.message(Command('verify_password'))
async def verify_password(message: types.Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_password)
    await message.reply("Пожалуйста, отправьте пароль для проверки сложности.")


@form_router.message(SearchState.waiting_for_password)
async def process_verify_password(message: types.Message, state: FSMContext):
    password = message.text
    if len(password) < 8:
        await message.reply("Пароль слишком простой. Он должен содержать не менее 8 символов.")
    elif not re.search(r'[A-Z]', password):
        await message.reply("Пароль слишком простой. Он должен включать хотя бы одну заглавную букву (A–Z).")
    elif not re.search(r'[a-z]', password):
        await message.reply("Пароль слишком простой. Он должен включать хотя бы одну строчную букву (a–z).")
    elif not re.search(r'[0-9]', password):
        await message.reply("Пароль слишком простой. Он должен включать хотя бы одну цифру (0–9).")
    elif not re.search(r'[!@#$%^&()]', password):
        await message.reply(
            "Пароль слишком простой. Он должен включать хотя бы один специальный символ, такой как !@#$%^&().")
    else:
        await message.reply("Пароль сложный. Он соответствует требованиям.")
    await state.clear()


async def main():
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(form_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())

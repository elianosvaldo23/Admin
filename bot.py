import logging
import re
import html
import json
import os
import time
import sqlite3
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, Counter

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError, BadRequest

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuración del bot
TOKEN = "7675635354:AAEkxM528h5vEa2auoMr94x1tWIGop8xKgo"
ADMIN_ID = 1742433244
GROUP_ID = "botoneraMultimediaTv"  # Grupo username sin @
CATEGORY_CHANNEL_ID = -1002259108243

# Categorías con sus URLs de post
CATEGORIES = {
    "Películas y Series 🖥": "https://t.me/c/2259108243/4",
    "Anime 💮": "https://t.me/c/2259108243/18",
    "Música 🎶": "https://t.me/c/2259108243/20",
    "Videojuegos 🎮": "https://t.me/c/2259108243/22",
    "Memes y Humor 😂": "https://t.me/c/2259108243/24",
    "Frases 📝": "https://t.me/c/2259108243/26",
    "Libros 📚": "https://t.me/c/2259108243/28",
    "Wallpapers 🌆": "https://t.me/c/2259108243/30",
    "Fotografía 📸": "https://t.me/c/2259108243/42",
    "Chicas y Belleza 👩‍🦰💄": "https://t.me/c/2259108243/44",
    "Apks 📱": "https://t.me/c/2259108243/46",
    "Bins y Cuentas 💳": "https://t.me/c/2259108243/48",
    "Redes Sociales 😎": "https://t.me/c/2259108243/51",
    "Noticias 🧾": "https://t.me/c/2259108243/53",
    "Deportes 🥇": "https://t.me/c/2259108243/56",
    "Grupos 👥": "https://t.me/c/2259108243/60",
    "Otros ♾": "https://t.me/c/2259108243/62",
    "+18 🔥": "https://t.me/c/2259108243/64",
}

# Mensaje de bienvenida predeterminado
DEFAULT_WELCOME_MESSAGE = "Hola bienvenido al grupo Botonera Multimedia-TV"

# Configuración de la base de datos
DB_PATH = "bot_data.db"

# Configuración anti-spam
SPAM_WINDOW = 60  # segundos
SPAM_LIMIT = 5  # mensajes
SPAM_MUTE_TIME = 300  # segundos (5 minutos)

# Almacenamiento en memoria
pending_submissions = {}
admin_rejecting = {}
custom_welcome = {
    "message": DEFAULT_WELCOME_MESSAGE,
    "buttons": [
        {"text": "Canal Principal", "url": "https://t.me/botoneraMultimediaTv"},
        {"text": "Categorías", "url": "https://t.me/c/2259108243/2"}
    ]
}
user_message_count = defaultdict(list)  # Para anti-spam
muted_users = {}  # Para seguimiento de usuarios silenciados
user_stats = defaultdict(Counter)  # Para estadísticas
user_warnings = defaultdict(int)  # Para sistema de advertencias
user_last_activity = {}  # Para seguimiento de actividad

# Inicialización de la base de datos
def init_db():
    """Inicializa la base de datos SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tabla para configuraciones
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')
    
    # Tabla para estadísticas
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stats (
        user_id INTEGER,
        chat_id INTEGER,
        messages INTEGER DEFAULT 0,
        media INTEGER DEFAULT 0,
        commands INTEGER DEFAULT 0,
        last_active TEXT,
        PRIMARY KEY (user_id, chat_id)
    )
    ''')
    
    # Tabla para advertencias
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS warnings (
        user_id INTEGER,
        chat_id INTEGER,
        count INTEGER DEFAULT 0,
        reasons TEXT,
        PRIMARY KEY (user_id, chat_id)
    )
    ''')
    
    # Tabla para canales aprobados
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS approved_channels (
        channel_id TEXT PRIMARY KEY,
        channel_name TEXT,
        channel_username TEXT,
        category TEXT,
        added_by INTEGER,
        added_date TEXT
    )
    ''')
    
    # Tabla para solicitudes pendientes
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pending_submissions (
        submission_id TEXT PRIMARY KEY,
        user_id INTEGER,
        user_name TEXT,
        category TEXT,
        channel_name TEXT,
        channel_username TEXT,
        channel_id TEXT,
        message_id INTEGER,
        chat_id INTEGER,
        submission_date TEXT
    )
    ''')
    
    conn.commit()
    conn.close()

# Funciones de base de datos
def save_config():
    """Guarda la configuración actual en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Guardar mensaje de bienvenida
    cursor.execute("INSERT OR REPLACE INTO config VALUES (?, ?)", 
                  ("welcome_message", custom_welcome["message"]))
    
    # Guardar botones de bienvenida
    cursor.execute("INSERT OR REPLACE INTO config VALUES (?, ?)", 
                  ("welcome_buttons", json.dumps(custom_welcome["buttons"])))
    
    conn.commit()
    conn.close()

def load_config():
    """Carga la configuración desde la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Cargar mensaje de bienvenida
    cursor.execute("SELECT value FROM config WHERE key = ?", ("welcome_message",))
    result = cursor.fetchone()
    if result:
        custom_welcome["message"] = result[0]
    
    # Cargar botones de bienvenida
    cursor.execute("SELECT value FROM config WHERE key = ?", ("welcome_buttons",))
    result = cursor.fetchone()
    if result:
        custom_welcome["buttons"] = json.loads(result[0])
    
    conn.close()

def load_pending_submissions():
    """Carga las solicitudes pendientes desde la base de datos."""
    global pending_submissions
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM pending_submissions")
    results = cursor.fetchall()
    
    for row in results:
        submission_id = row[0]
        pending_submissions[submission_id] = {
            "user_id": row[1],
            "user_name": row[2],
            "category": row[3],
            "channel_name": row[4],
            "channel_username": row[5],
            "channel_id": row[6],
            "message_id": row[7],
            "chat_id": row[8],
            "submission_date": row[9]
        }
    
    conn.close()

def save_pending_submission(submission_id, submission_data):
    """Guarda una solicitud pendiente en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT OR REPLACE INTO pending_submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            submission_id,
            submission_data["user_id"],
            submission_data["user_name"],
            submission_data["category"],
            submission_data["channel_name"],
            submission_data["channel_username"],
            submission_data["channel_id"],
            submission_data["message_id"],
            submission_data["chat_id"],
            datetime.now().isoformat()
        )
    )
    
    conn.commit()
    conn.close()

def delete_pending_submission(submission_id):
    """Elimina una solicitud pendiente de la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM pending_submissions WHERE submission_id = ?", (submission_id,))
    
    conn.commit()
    conn.close()

def update_user_stats(user_id, chat_id, stat_type):
    """Actualiza las estadísticas de un usuario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    
    # Verificar si el usuario ya existe en la base de datos
    cursor.execute(
        "SELECT * FROM stats WHERE user_id = ? AND chat_id = ?", 
        (user_id, chat_id)
    )
    
    if cursor.fetchone():
        # Actualizar estadísticas existentes
        cursor.execute(
            f"UPDATE stats SET {stat_type} = {stat_type} + 1, last_active = ? WHERE user_id = ? AND chat_id = ?",
            (now, user_id, chat_id)
        )
    else:
        # Crear nuevo registro
        cursor.execute(
            f"INSERT INTO stats (user_id, chat_id, {stat_type}, last_active) VALUES (?, ?, 1, ?)",
            (user_id, chat_id, now)
        )
    
    conn.commit()
    conn.close()

def get_user_stats(user_id, chat_id):
    """Obtiene las estadísticas de un usuario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT messages, media, commands, last_active FROM stats WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            "messages": result[0],
            "media": result[1],
            "commands": result[2],
            "last_active": result[3]
        }
    else:
        return {
            "messages": 0,
            "media": 0,
            "commands": 0,
            "last_active": None
        }

def add_warning(user_id, chat_id, reason):
    """Añade una advertencia a un usuario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Obtener advertencias actuales
    cursor.execute(
        "SELECT count, reasons FROM warnings WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    
    result = cursor.fetchone()
    
    if result:
        count = result[0] + 1
        reasons = json.loads(result[1]) if result[1] else []
        reasons.append({
            "reason": reason,
            "date": datetime.now().isoformat()
        })
        
        cursor.execute(
            "UPDATE warnings SET count = ?, reasons = ? WHERE user_id = ? AND chat_id = ?",
            (count, json.dumps(reasons), user_id, chat_id)
        )
    else:
        reasons = [{
            "reason": reason,
            "date": datetime.now().isoformat()
        }]
        
        cursor.execute(
            "INSERT INTO warnings (user_id, chat_id, count, reasons) VALUES (?, ?, 1, ?)",
            (user_id, chat_id, json.dumps(reasons))
        )
    
    conn.commit()
    conn.close()
    
    # Devolver el número actual de advertencias
    return count if result else 1

def get_warnings(user_id, chat_id):
    """Obtiene las advertencias de un usuario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT count, reasons FROM warnings WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            "count": result[0],
            "reasons": json.loads(result[1]) if result[1] else []
        }
    else:
        return {
            "count": 0,
            "reasons": []
        }

def reset_warnings(user_id, chat_id):
    """Reinicia las advertencias de un usuario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "DELETE FROM warnings WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    
    conn.commit()
    conn.close()

def save_approved_channel(channel_id, channel_name, channel_username, category, added_by):
    """Guarda un canal aprobado en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT OR REPLACE INTO approved_channels VALUES (?, ?, ?, ?, ?, ?)",
        (channel_id, channel_name, channel_username, category, added_by, datetime.now().isoformat())
    )
    
    conn.commit()
    conn.close()

def get_approved_channels(category=None):
    """Obtiene los canales aprobados, opcionalmente filtrados por categoría."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if category:
        cursor.execute(
            "SELECT * FROM approved_channels WHERE category = ?",
            (category,)
        )
    else:
        cursor.execute("SELECT * FROM approved_channels")
    
    results = cursor.fetchall()
    conn.close()
    
    channels = []
    for row in results:
        channels.append({
            "channel_id": row[0],
            "channel_name": row[1],
            "channel_username": row[2],
            "category": row[3],
            "added_by": row[4],
            "added_date": row[5]
        })
    
    return channels

# Funciones de utilidad
async def is_admin(user_id, chat_id, context):
    """Verifica si un usuario es administrador del chat."""
    if user_id == ADMIN_ID:
        return True
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ["creator", "administrator"]
    except TelegramError:
        return False

def check_spam(user_id):
    """Verifica si un usuario está enviando spam."""
    current_time = time.time()
    
    # Eliminar mensajes antiguos
    user_message_count[user_id] = [t for t in user_message_count[user_id] if current_time - t < SPAM_WINDOW]
    
    # Añadir mensaje actual
    user_message_count[user_id].append(current_time)
    
    # Verificar límite
    return len(user_message_count[user_id]) > SPAM_LIMIT

def format_time_delta(seconds):
    """Formatea un número de segundos en un formato legible."""
    if seconds < 60:
        return f"{seconds} segundos"
    elif seconds < 3600:
        return f"{seconds // 60} minutos"
    elif seconds < 86400:
        return f"{seconds // 3600} horas"
    else:
        return f"{seconds // 86400} días"

# Manejadores de comandos
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /start."""
    if update.effective_chat.type == ChatType.PRIVATE:
        user = update.effective_user
        
        # Crear teclado con botones
        keyboard = [
            [InlineKeyboardButton("📚 Comandos", callback_data="show_commands")],
            [InlineKeyboardButton("📊 Estadísticas", callback_data="show_stats")],
            [InlineKeyboardButton("🔍 Ver Categorías", callback_data="show_categories")],
            [InlineKeyboardButton("➕ Añadir Canal", callback_data="add_channel_help")]
        ]
        
        if user.id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("⚙️ Panel de Administrador", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_html(
            f"Hola {user.mention_html()}! Soy el bot administrador de Botonera Multimedia-TV.\n\n"
            f"Puedo ayudarte a gestionar el grupo y procesar solicitudes de canales.\n\n"
            f"Selecciona una opción para continuar:",
            reply_markup=reply_markup
        )
    else:
        # En grupos, mostrar un mensaje más simple
        await update.message.reply_text(
            "¡Hola! Soy el bot administrador de este grupo. Envíame un mensaje privado para ver todas mis funciones."
        )
    
    # Actualizar estadísticas
    if update.effective_user:
        update_user_stats(update.effective_user.id, update.effective_chat.id, "commands")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /help."""
    # Crear teclado con categorías de ayuda
    keyboard = [
        [
            InlineKeyboardButton("📝 Comandos Básicos", callback_data="help_basic"),
            InlineKeyboardButton("👮 Comandos de Moderación", callback_data="help_mod")
        ],
        [
            InlineKeyboardButton("📊 Estadísticas", callback_data="help_stats"),
            InlineKeyboardButton("🔄 Canales", callback_data="help_channels")
        ],
        [
            InlineKeyboardButton("⚙️ Configuración", callback_data="help_config"),
            InlineKeyboardButton("🎮 Diversión", callback_data="help_fun")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        "<b>🤖 Centro de Ayuda</b>\n\n"
        "Selecciona una categoría para ver los comandos disponibles:",
        reply_markup=reply_markup
    )
    
    # Actualizar estadísticas
    if update.effective_user:
        update_user_stats(update.effective_user.id, update.effective_chat.id, "commands")

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Da la bienvenida a nuevos miembros del grupo."""
    if not update.message or not update.message.new_chat_members:
        return
    
    for new_user in update.message.new_chat_members:
        # Omitir si el nuevo miembro es el bot
        if new_user.id == context.bot.id:
            continue
        
        # Crear mensaje de bienvenida con botones
        keyboard = []
        row = []
        for i, button in enumerate(custom_welcome["buttons"]):
            row.append(InlineKeyboardButton(button["text"], url=button["url"]))
            # Crear nueva fila después de cada 2 botones
            if (i + 1) % 2 == 0 or i == len(custom_welcome["buttons"]) - 1:
                keyboard.append(row)
                row = []
        
        # Añadir botones adicionales
        keyboard.append([
            InlineKeyboardButton("📚 Reglas del Grupo", callback_data="show_rules"),
            InlineKeyboardButton("🔍 Ver Categorías", callback_data="show_categories")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Enviar mensaje de bienvenida
        await update.message.reply_html(
            f"{custom_welcome['message']}, {new_user.mention_html()}!\n\n"
            f"Por favor, lee las reglas del grupo y disfruta de tu estancia.",
            reply_markup=reply_markup
        )

async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Establece un mensaje de bienvenida personalizado."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden cambiar el mensaje de bienvenida.")
        return
    
    # Obtener el texto del mensaje después del comando
    if not context.args:
        await update.message.reply_text(
            "Por favor, proporciona un mensaje de bienvenida.\n"
            "Ejemplo: /setwelcome Bienvenido a nuestro grupo!"
        )
        return
    
    new_message = " ".join(context.args)
    custom_welcome["message"] = new_message
    
    # Guardar en la base de datos
    save_config()
    
    # Mostrar vista previa
    keyboard = []
    row = []
    for i, button in enumerate(custom_welcome["buttons"]):
        row.append(InlineKeyboardButton(button["text"], url=button["url"]))
        if (i + 1) % 2 == 0 or i == len(custom_welcome["buttons"]) - 1:
            keyboard.append(row)
            row = []
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        f"✅ Mensaje de bienvenida actualizado.\n\n"
        f"<b>Vista previa:</b>\n\n"
        f"{new_message}, Usuario!",
        reply_markup=reply_markup
    )
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def add_welcome_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Añade un botón al mensaje de bienvenida."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden añadir botones.")
        return
    
    # Verificar argumentos
    if len(context.args) < 2:
        await update.message.reply_text(
            "Por favor, proporciona el texto y la URL del botón.\n"
            "Ejemplo: /addbutton \"Canal Principal\" https://t.me/botoneraMultimediaTv"
        )
        return
    
    # Extraer texto y URL del botón
    button_text = context.args[0]
    button_url = context.args[1]
    
    # Añadir botón a la configuración
    custom_welcome["buttons"].append({"text": button_text, "url": button_url})
    
    # Guardar en la base de datos
    save_config()
    
    await update.message.reply_text(f"✅ Botón añadido: {button_text} -> {button_url}")
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def remove_welcome_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elimina un botón del mensaje de bienvenida."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden eliminar botones.")
        return
    
    if not custom_welcome["buttons"]:
        await update.message.reply_text("No hay botones para eliminar.")
        return
    
    # Crear teclado con botones para eliminar
    keyboard = []
    for i, button in enumerate(custom_welcome["buttons"]):
        callback_data = f"remove_button_{i}"
        keyboard.append([InlineKeyboardButton(f"Eliminar: {button['text']}", callback_data=callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Selecciona el botón que deseas eliminar:", reply_markup=reply_markup)
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def show_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la configuración actual del mensaje de bienvenida."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden ver la configuración.")
        return
    
    # Mostrar mensaje de bienvenida actual
    message_text = f"<b>Mensaje actual:</b>\n{custom_welcome['message']}\n\n<b>Botones:</b>\n"
    
    for i, button in enumerate(custom_welcome["buttons"]):
        message_text += f"{i+1}. {button['text']} -> {button['url']}\n"
    
    # Crear ejemplo de cómo se ve
    keyboard = []
    row = []
    for i, button in enumerate(custom_welcome["buttons"]):
        row.append(InlineKeyboardButton(button["text"], url=button["url"]))
        if (i + 1) % 2 == 0 or i == len(custom_welcome["buttons"]) - 1:
            keyboard.append(row)
            row = []
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(message_text)
    await update.message.reply_html(
        f"<b>Vista previa:</b>\n\n{custom_welcome['message']}, Usuario!",
        reply_markup=reply_markup
    )
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def reset_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restablece el mensaje de bienvenida a los valores predeterminados."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden restablecer la configuración.")
        return
    
    # Restablecer a valores predeterminados
    custom_welcome["message"] = DEFAULT_WELCOME_MESSAGE
    custom_welcome["buttons"] = [
        {"text": "Canal Principal", "url": "https://t.me/botoneraMultimediaTv"},
        {"text": "Categorías", "url": "https://t.me/c/2259108243/2"}
    ]
    
    # Guardar en la base de datos
    save_config()
    
    await update.message.reply_text("✅ Mensaje de bienvenida restablecido a los valores predeterminados.")
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def process_channel_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa solicitudes de canales."""
    # Verificar que hay un mensaje y tiene texto
    if not update.message or not update.message.text:
        return
    
    message_text = update.message.text
    user = update.effective_user
    
    # Verificar si el mensaje contiene formato de solicitud de canal
    if "#" not in message_text:
        return
    
    # Intentar analizar la solicitud
    try:
        # Extraer categoría usando regex
        category_match = re.search(r'#([^\n]+)', message_text)
        if not category_match:
            return
        
        category_text = category_match.group(1).strip()
        
        # Verificar si es una categoría válida
        valid_category = None
        for cat in CATEGORIES.keys():
            if category_text.lower() in cat.lower():
                valid_category = cat
                break
        
        if not valid_category:
            await update.message.reply_text(
                f"❌ Categoría no reconocida: {category_text}\n"
                f"Por favor, usa una de las categorías disponibles."
            )
            return
        
        # Extraer nombre del canal, nombre de usuario e ID
        # Mejorado para evitar errores de regex
        lines = message_text.split('\n')
        channel_name = None
        channel_username = None
        channel_id = None
        
        for i, line in enumerate(lines):
            if '#' in line and i < len(lines) - 1:
                # La línea después de una línea con # podría ser el nombre del canal
                channel_name = lines[i + 1].strip()
            
            if '@' in line and 'admin' not in line.lower():
                # Línea con @ pero sin "admin" podría ser el username
                username_match = re.search(r'@(\w+)', line)
                if username_match:
                    channel_username = username_match.group(1)
            
            if 'ID' in line or 'id' in line:
                # Línea con ID podría contener el ID del canal
                id_match = re.search(r'[-]?\d+', line)
                if id_match:
                    channel_id = id_match.group(0)
        
        if not (channel_name and channel_username and channel_id):
            await update.message.reply_html(
                "❌ <b>Formato incorrecto</b>. Por favor, usa el siguiente formato:\n\n"
                "#Categoría\n"
                "Nombre del Canal\n"
                "@username_canal\n"
                "ID -100xxxxxxxxxx\n"
                "@admin bot añadido"
            )
            return
        
        # Almacenar solicitud para aprobación del administrador
        submission_id = f"{user.id}_{update.message.message_id}"
        submission_data = {
            "user_id": user.id,
            "user_name": user.full_name,
            "category": valid_category,
            "channel_name": channel_name,
            "channel_username": channel_username,
            "channel_id": channel_id,
            "message_id": update.message.message_id,
            "chat_id": update.effective_chat.id
        }
        
        # Guardar en memoria y en la base de datos
        pending_submissions[submission_id] = submission_data
        save_pending_submission(submission_id, submission_data)
        
        # Crear botones de aprobación para el administrador
        keyboard = [
            [
                InlineKeyboardButton("✅ Aprobar", callback_data=f"approve_{submission_id}"),
                InlineKeyboardButton("❌ Rechazar", callback_data=f"reject_{submission_id}")
            ],
            [
                InlineKeyboardButton("🔍 Ver Canal", url=f"https://t.me/{channel_username}"),
                InlineKeyboardButton("📋 Ver Categoría", url=CATEGORIES[valid_category])
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Notificar al administrador
        admin_message = (
            f"📢 <b>Nueva solicitud de canal</b>\n\n"
            f"<b>Usuario:</b> {user.mention_html()}\n"
            f"<b>Categoría:</b> {valid_category}\n"
            f"<b>Canal:</b> {html.escape(channel_name)}\n"
            f"<b>Username:</b> @{html.escape(channel_username)}\n"
            f"<b>ID:</b> {html.escape(channel_id)}\n\n"
            f"¿Deseas aprobar esta solicitud?"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        
        # Notificar al usuario
        user_keyboard = [
            [
                InlineKeyboardButton("📊 Estado de Solicitud", callback_data=f"check_status_{submission_id}"),
                InlineKeyboardButton("❌ Cancelar Solicitud", callback_data=f"cancel_{submission_id}")
            ]
        ]
        user_reply_markup = InlineKeyboardMarkup(user_keyboard)
        
        await update.message.reply_html(
            f"✅ Tu solicitud para añadir el canal <b>{html.escape(channel_name)}</b> a la categoría <b>{valid_category}</b> "
            f"ha sido enviada al administrador para su aprobación.",
            reply_markup=user_reply_markup,
            reply_to_message_id=update.message.message_id
        )
        
        # Actualizar estadísticas
        update_user_stats(user.id, update.effective_chat.id, "messages")
        
    except Exception as e:
        logger.error(f"Error processing channel submission: {e}")
        await update.message.reply_text(
            "❌ Ocurrió un error al procesar tu solicitud. Por favor, verifica el formato e intenta nuevamente."
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja callbacks de botones."""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    user_id = update.effective_user.id
    
    # Manejar eliminación de botones de bienvenida
    if callback_data.startswith("remove_button_"):
        if not await is_admin(user_id, query.message.chat.id, context):
            await query.edit_message_text("Solo los administradores pueden eliminar botones.")
            return
        
        button_index = int(callback_data.split("_")[-1])
        if 0 <= button_index < len(custom_welcome["buttons"]):
            removed_button = custom_welcome["buttons"].pop(button_index)
            # Guardar en la base de datos
            save_config()
            await query.edit_message_text(f"✅ Botón eliminado: {removed_button['text']}")
        else:
            await query.edit_message_text("❌ Botón no encontrado.")
        return
    
    # Manejar aprobación/rechazo de solicitudes de canales
    if callback_data.startswith("approve_") or callback_data.startswith("reject_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("Solo el administrador principal puede aprobar o rechazar solicitudes.")
            return
        
        submission_id = callback_data.split("_", 1)[1]
        
        if submission_id not in pending_submissions:
            await query.edit_message_text("Esta solicitud ya no está disponible o ha sido procesada.")
            return
        
        submission = pending_submissions[submission_id]
        
        if callback_data.startswith("approve_"):
            # Aprobar la solicitud
            try:
                # Obtener la URL del post para la categoría
                post_url = CATEGORIES[submission["category"]]
                
                # Extraer message_id de la URL
                post_message_id = int(post_url.split("/")[-1])
                
                # Crear el enlace del canal
                channel_link = f"[{submission['channel_name']}](https://t.me/{submission['channel_username']})"
                
                try:
                    # Primero, intentamos obtener el mensaje actual
                    try:
                        # Usar copyMessage para obtener el contenido actual
                        copied_message = await context.bot.copy_message(
                            chat_id=ADMIN_ID,  # Copiar al admin temporalmente
                            from_chat_id=CATEGORY_CHANNEL_ID,
                            message_id=post_message_id,
                            disable_notification=True
                        )
                        
                        # Obtener el texto del mensaje copiado
                        current_text = copied_message.text if copied_message.text else ""
                        
                        # Eliminar el mensaje copiado
                        await context.bot.delete_message(
                            chat_id=ADMIN_ID,
                            message_id=copied_message.message_id
                        )
                        
                        # Añadir el nuevo canal al texto existente
                        if current_text:
                            # Añadir una línea en blanco y luego el nuevo canal
                            new_text = f"{current_text}\n\n{channel_link}"
                        else:
                            new_text = f"{submission['category']}\n\n{channel_link}"
                        
                        # Editar el mensaje original
                        await context.bot.edit_message_text(
                            chat_id=CATEGORY_CHANNEL_ID,
                            message_id=post_message_id,
                            text=new_text,
                            parse_mode=ParseMode.MARKDOWN
                        )
                        
                        # Guardar canal aprobado en la base de datos
                        save_approved_channel(
                            submission["channel_id"],
                            submission["channel_name"],
                            submission["channel_username"],
                            submission["category"],
                            submission["user_id"]
                        )
                        
                        # Notificar al administrador
                        await query.edit_message_text(
                            f"✅ Canal aprobado y añadido a la categoría {submission['category']}."
                        )
                        
                        # Notificar al usuario
                        user_keyboard = [
                            [
                                InlineKeyboardButton("🔍 Ver Categoría", url=post_url),
                                InlineKeyboardButton("📢 Compartir Canal", url=f"https://t.me/share/url?url=https://t.me/{submission['channel_username']}")
                            ]
                        ]
                        user_reply_markup = InlineKeyboardMarkup(user_keyboard)
                        
                        await context.bot.send_message(
                            chat_id=submission["chat_id"],
                            text=f"✅ Tu canal <b>{html.escape(submission['channel_name'])}</b> ha sido aprobado y añadido a la categoría <b>{submission['category']}</b>.",
                            parse_mode=ParseMode.HTML,
                            reply_to_message_id=submission["message_id"],
                            reply_markup=user_reply_markup
                        )
                        
                    except Exception as copy_error:
                        logger.error(f"Error copying message: {copy_error}")
                        
                        # Si no podemos copiar el mensaje, intentamos usar forward_message como alternativa
                        try:
                            # Intentar reenviar el mensaje para obtener su contenido
                            forwarded_message = await context.bot.forward_message(
                                chat_id=ADMIN_ID,
                                from_chat_id=CATEGORY_CHANNEL_ID,
                                message_id=post_message_id,
                                disable_notification=True
                            )
                            
                            # Obtener el texto del mensaje reenviado
                            current_text = forwarded_message.text if forwarded_message.text else ""
                            
                            # Eliminar el mensaje reenviado
                            await context.bot.delete_message(
                                chat_id=ADMIN_ID,
                                message_id=forwarded_message.message_id
                            )
                            
                            # Añadir el nuevo canal al texto existente
                            if current_text:
                                new_text = f"{current_text}\n\n{channel_link}"
                            else:
                                new_text = f"{submission['category']}\n\n{channel_link}"
                                
                        except Exception as forward_error:
                            logger.error(f"Error forwarding message: {forward_error}")
                            # Si todo falla, intentar editar directamente
                            new_text = f"{submission['category']}\n\n{channel_link}"
                        
                        # Editar el mensaje original con la mejor información que tenemos
                        await context.bot.edit_message_text(
                            chat_id=CATEGORY_CHANNEL_ID,
                            message_id=post_message_id,
                            text=new_text,
                            parse_mode=ParseMode.MARKDOWN
                        )
                        
                        # Guardar canal aprobado en la base de datos
                        save_approved_channel(
                            submission["channel_id"],
                            submission["channel_name"],
                            submission["channel_username"],
                            submission["category"],
                            submission["user_id"]
                        )
                        
                        # Notificar al administrador
                        await query.edit_message_text(
                            f"✅ Canal aprobado y añadido a la categoría {submission['category']}."
                        )
                        
                        # Notificar al usuario
                        user_keyboard = [
                            [
                                InlineKeyboardButton("🔍 Ver Categoría", url=post_url),
                                InlineKeyboardButton("📢 Compartir Canal", url=f"https://t.me/share/url?url=https://t.me/{submission['channel_username']}")
                            ]
                        ]
                        user_reply_markup = InlineKeyboardMarkup(user_keyboard)
                        
                        await context.bot.send_message(
                            chat_id=submission["chat_id"],
                            text=f"✅ Tu canal <b>{html.escape(submission['channel_name'])}</b> ha sido aprobado y añadido a la categoría <b>{submission['category']}</b>.",
                            parse_mode=ParseMode.HTML,
                            reply_to_message_id=submission["message_id"],
                            reply_markup=user_reply_markup
                        )
                    
                    # Eliminar la solicitud de la base de datos
                    delete_pending_submission(submission_id)
                    
                except TelegramError as e:
                    logger.error(f"Error editing message: {e}")
                    
                    # Notificar al administrador sobre el error
                    await query.edit_message_text(
                        f"❌ Error al editar el mensaje: {e}\n\n"
                        f"Por favor, edita manualmente el mensaje en la categoría {submission['category']} "
                        f"y añade el canal: {submission['channel_name']} (@{submission['channel_username']})"
                    )
            
            except Exception as e:
                logger.error(f"Error approving submission: {e}")
                await query.edit_message_text(
                    f"❌ Error al aprobar la solicitud: {e}"
                )
        
        elif callback_data.startswith("reject_"):
            # Iniciar proceso de rechazo
            admin_rejecting[user_id] = submission_id
            
            # Crear teclado con razones comunes de rechazo
            keyboard = [
                [InlineKeyboardButton("Canal duplicado", callback_data=f"reject_reason_{submission_id}_duplicado")],
                [InlineKeyboardButton("Contenido inapropiado", callback_data=f"reject_reason_{submission_id}_inapropiado")],
                [InlineKeyboardButton("Información incorrecta", callback_data=f"reject_reason_{submission_id}_incorrecto")],
                [InlineKeyboardButton("Categoría equivocada", callback_data=f"reject_reason_{submission_id}_categoria")],
                [InlineKeyboardButton("Otro motivo (escribir)", callback_data=f"reject_custom_{submission_id}")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"Selecciona el motivo del rechazo para el canal {submission['channel_name']}:",
                reply_markup=reply_markup
            )
        
        # Eliminar de solicitudes pendientes si se aprobó
        if callback_data.startswith("approve_"):
            del pending_submissions[submission_id]
        
        return

    
    # Manejar razones de rechazo predefinidas
    if callback_data.startswith("reject_reason_"):
        parts = callback_data.split("_")
        submission_id = parts[2]
        reason_code = parts[3]
        
        if submission_id not in pending_submissions:
            await query.edit_message_text("Esta solicitud ya no está disponible.")
            return
        
        submission = pending_submissions[submission_id]
        
        # Mapear códigos de razón a mensajes
        reason_messages = {
            "duplicado": "El canal ya existe en nuestras categorías.",
            "inapropiado": "El contenido del canal no cumple con nuestras normas.",
            "incorrecto": "La información proporcionada es incorrecta o incompleta.",
            "categoria": "La categoría seleccionada no es adecuada para este canal."
        }
        
        reason = reason_messages.get(reason_code, "No cumple con los requisitos.")
        
        # Notificar al usuario sobre el rechazo
        try:
            user_keyboard = [
                [
                    InlineKeyboardButton("🔄 Enviar Nueva Solicitud", callback_data="add_channel_help"),
                    InlineKeyboardButton("❓ Ayuda", callback_data="help_channels")
                ]
            ]
            user_reply_markup = InlineKeyboardMarkup(user_keyboard)
            
            await context.bot.send_message(
                chat_id=submission["chat_id"],
                text=f"❌ Tu solicitud para añadir el canal <b>{html.escape(submission['channel_name'])}</b> "
                     f"a la categoría <b>{submission['category']}</b> ha sido rechazada.\n\n"
                     f"<b>Motivo:</b> {html.escape(reason)}",
                parse_mode=ParseMode.HTML,
                reply_to_message_id=submission["message_id"],
                reply_markup=user_reply_markup
            )
            
            # Confirmar al administrador
            await query.edit_message_text(
                f"✅ Rechazo enviado al usuario para el canal {submission['channel_name']}.\n"
                f"Motivo: {reason}"
            )
            
            # Eliminar la solicitud de la base de datos
            delete_pending_submission(submission_id)
            
        except Exception as e:
            logger.error(f"Error sending rejection: {e}")
            await query.edit_message_text(
                f"❌ Error al enviar el rechazo: {e}"
            )
        
        # Limpiar
        del pending_submissions[submission_id]
        if user_id in admin_rejecting:
            del admin_rejecting[user_id]
        
        return
    
    # Manejar rechazo personalizado
    if callback_data.startswith("reject_custom_"):
        submission_id = callback_data.split("_")[2]
        
        if submission_id not in pending_submissions:
            await query.edit_message_text("Esta solicitud ya no está disponible.")
            return
        
        admin_rejecting[user_id] = submission_id
        
        await query.edit_message_text(
            f"Por favor, envía el motivo personalizado del rechazo para el canal {pending_submissions[submission_id]['channel_name']}."
        )
        
        return
    
    # Manejar cancelación de solicitud
    if callback_data.startswith("cancel_"):
        submission_id = callback_data.split("_")[1]
        
        if submission_id not in pending_submissions:
            await query.edit_message_text("Esta solicitud ya no está disponible o ha sido procesada.")
            return
        
        submission = pending_submissions[submission_id]
        
        # Verificar que el usuario es el propietario de la solicitud
        if user_id != submission["user_id"]:
            await query.answer("Solo el usuario que envió la solicitud puede cancelarla.", show_alert=True)
            return
        
        # Eliminar la solicitud
        del pending_submissions[submission_id]
        delete_pending_submission(submission_id)
        
        # Notificar al usuario
        await query.edit_message_text(
            "✅ Tu solicitud ha sido cancelada. Puedes enviar una nueva cuando lo desees."
        )
        
        # Notificar al administrador si es necesario
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"ℹ️ El usuario {submission['user_name']} ha cancelado su solicitud para el canal {submission['channel_name']}."
            )
        except:
            pass
        
        return
    
    # Manejar verificación de estado de solicitud
    if callback_data.startswith("check_status_"):
        submission_id = callback_data.split("_")[2]
        
        if submission_id not in pending_submissions:
            await query.edit_message_text(
                "Esta solicitud ya no está disponible o ha sido procesada. Si fue aprobada, deberías haber recibido una notificación."
            )
            return
        
        submission = pending_submissions[submission_id]
        
        # Verificar que el usuario es el propietario de la solicitud
        if user_id != submission["user_id"]:
            await query.answer("Solo el usuario que envió la solicitud puede verificar su estado.", show_alert=True)
            return
        
        # Mostrar estado actual
        await query.edit_message_text(
            f"ℹ️ Tu solicitud para añadir el canal <b>{html.escape(submission['channel_name'])}</b> "
            f"a la categoría <b>{submission['category']}</b> está pendiente de aprobación por el administrador.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancelar Solicitud", callback_data=f"cancel_{submission_id}")]
            ])
        )
        
        return
    
    # Manejar visualización de categorías
    if callback_data == "show_categories":
        categories_text = "<b>📚 Categorías disponibles:</b>\n\n"
        
        keyboard = []
        for i, (category, url) in enumerate(CATEGORIES.items(), 1):
            categories_text += f"{i}. {category}\n"
            # Crear filas de 2 botones
            if i % 2 == 1:
                row = [InlineKeyboardButton(category, url=url)]
            else:
                row.append(InlineKeyboardButton(category, url=url))
                keyboard.append(row)
        
        # Añadir la última fila si quedó incompleta
        if len(CATEGORIES) % 2 == 1:
            keyboard.append(row)
        
        # Añadir botón de volver
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            categories_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
        return
    
    # Manejar visualización de reglas
    if callback_data == "show_rules":
        rules_text = (
            "<b>📜 Reglas del Grupo</b>\n\n"
            "1. Sé respetuoso con todos los miembros.\n"
            "2. No envíes spam ni contenido no relacionado.\n"
            "3. No compartas contenido ilegal o inapropiado.\n"
            "4. Usa los canales adecuados para cada tipo de contenido.\n"
            "5. Sigue las instrucciones de los administradores.\n"
            "6. Para añadir un canal, sigue el formato establecido.\n"
            "7. No promociones otros grupos sin permiso.\n"
            "8. Respeta los temas de cada categoría.\n\n"
            "El incumplimiento de estas reglas puede resultar en advertencias o expulsión."
        )
        
        await query.edit_message_text(
            rules_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
            ])
        )
        
        return
    
    # Manejar ayuda para añadir canales
    if callback_data == "add_channel_help":
        help_text = (
            "<b>📝 Cómo añadir un canal</b>\n\n"
            "Para añadir un canal, envía un mensaje con el siguiente formato:\n\n"
            "<code>#Categoría\nNombre del Canal\n@username_canal\nID -100xxxxxxxxxx\n@admin bot añadido</code>\n\n"
            "<b>Ejemplo:</b>\n\n"
            "<code>#Música 🎶\nCanal de Música Pop\n@musica_pop\nID -1001234567890\n@admin bot añadido</code>\n\n"
            "<b>Notas:</b>\n"
            "- Puedes añadir #Nuevo si es un canal nuevo\n"
            "- La categoría debe ser una de las disponibles\n"
            "- Para obtener el ID del canal, reenvía un mensaje del canal a @getidsbot"
        )
        
        # Crear teclado con categorías
        keyboard = []
        row = []
        for i, category in enumerate(CATEGORIES.keys()):
            if i % 2 == 0 and i > 0:
                keyboard.append(row)
                row = []
            row.append(InlineKeyboardButton(category, callback_data=f"select_category_{category}"))
        
        if row:
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
        return
    
    # Manejar selección de categoría
    if callback_data.startswith("select_category_"):
        category = callback_data[16:]
        
        template = (
            f"#Nuevo\n#{category}\nNombre del Canal\n@username_canal\nID -100xxxxxxxxxx\n@admin bot añadido"
        )
        
        await query.edit_message_text(
            f"<b>📋 Plantilla para la categoría {category}</b>\n\n"
            f"<code>{template}</code>\n\n"
            f"Copia esta plantilla, reemplaza los datos con la información de tu canal y envíala al grupo o al bot.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Copiar Plantilla", callback_data=f"copy_template_{category}")],
                [InlineKeyboardButton("🔙 Volver a Categorías", callback_data="add_channel_help")]
            ])
        )
        
        return
    
    # Manejar copia de plantilla
    if callback_data.startswith("copy_template_"):
        category = callback_data[14:]
        
        template = (
            f"#Nuevo\n#{category}\nNombre del Canal\n@username_canal\nID -100xxxxxxxxxx\n@admin bot añadido"
        )
        
        await query.answer("Plantilla copiada al portapapeles", show_alert=False)
        
        # No podemos realmente copiar al portapapeles, así que enviamos un mensaje nuevo
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<code>{template}</code>\n\n"
                 f"👆 Copia esta plantilla, reemplaza los datos con la información de tu canal y envíala.",
            parse_mode=ParseMode.HTML
        )
        
        return
    
    # Manejar panel de administrador
    if callback_data == "admin_panel":
        if user_id != ADMIN_ID:
            await query.answer("Solo el administrador principal puede acceder a este panel.", show_alert=True)
            return
        
        keyboard = [
            [
                InlineKeyboardButton("📊 Estadísticas", callback_data="admin_stats"),
                InlineKeyboardButton("⚙️ Configuración", callback_data="admin_config")
            ],
            [
                InlineKeyboardButton("👮 Moderación", callback_data="admin_moderation"),
                InlineKeyboardButton("📢 Anuncios", callback_data="admin_announce")
            ],
            [
                InlineKeyboardButton("🔍 Ver Solicitudes", callback_data="admin_submissions"),
                InlineKeyboardButton("📋 Ver Canales", callback_data="admin_channels")
            ],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "<b>⚙️ Panel de Administrador</b>\n\n"
            "Bienvenido al panel de administración. Selecciona una opción para continuar:",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
        return
    
    # Manejar volver al menú principal
    if callback_data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("📚 Comandos", callback_data="show_commands")],
            [InlineKeyboardButton("📊 Estadísticas", callback_data="show_stats")],
            [InlineKeyboardButton("🔍 Ver Categorías", callback_data="show_categories")],
            [InlineKeyboardButton("➕ Añadir Canal", callback_data="add_channel_help")]
        ]
        
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("⚙️ Panel de Administrador", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"Hola {update.effective_user.first_name}! Soy el bot administrador de Botonera Multimedia-TV.\n\n"
            f"Puedo ayudarte a gestionar el grupo y procesar solicitudes de canales.\n\n"
            f"Selecciona una opción para continuar:",
            reply_markup=reply_markup
        )
        
        return
    
    # Manejar mostrar comandos
    if callback_data == "show_commands":
        commands_text = (
            "<b>📚 Comandos Disponibles</b>\n\n"
            "<b>Comandos Básicos:</b>\n"
            "/start - Iniciar el bot\n"
            "/help - Mostrar ayuda\n"
            "/categories - Ver categorías disponibles\n"
            "/stats - Ver tus estadísticas\n\n"
            "<b>Comandos para Administradores:</b>\n"
            "/setwelcome - Establecer mensaje de bienvenida\n"
            "/addbutton - Añadir botón al mensaje de bienvenida\n"
            "/removebutton - Eliminar botón del mensaje de bienvenida\n"
            "/showwelcome - Mostrar configuración actual\n"
            "/resetwelcome - Restablecer configuración por defecto\n"
            "/warn - Advertir a un usuario\n"
            "/unwarn - Quitar advertencia a un usuario\n"
            "/mute - Silenciar a un usuario\n"
            "/unmute - Quitar silencio a un usuario\n"
            "/ban - Banear a un usuario\n"
            "/unban - Desbanear a un usuario\n"
            "/announce - Enviar anuncio al grupo"
        )
        
        await query.edit_message_text(
            commands_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
            ])
        )
        
        return
    
    # Manejar estadísticas
    if callback_data == "show_stats":
        user_statistics = get_user_stats(user_id, query.message.chat.id)
        warnings = get_warnings(user_id, query.message.chat.id)
        
        stats_message = (
            f"📊 <b>Estadísticas de {update.effective_user.first_name}</b>\n\n"
            f"Mensajes enviados: {user_statistics['messages']}\n"
            f"Medios compartidos: {user_statistics['media']}\n"
            f"Comandos utilizados: {user_statistics['commands']}\n"
            f"Advertencias: {warnings['count']}/3\n"
            f"Última actividad: {user_statistics['last_active'] if user_statistics['last_active'] else 'Desconocida'}"
        )
        
        await query.edit_message_text(
            stats_message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
            ])
        )
        
        return
    
    # Manejar ver solicitudes pendientes (admin)
    if callback_data == "admin_submissions":
        if user_id != ADMIN_ID:
            await query.answer("Solo el administrador principal puede ver las solicitudes pendientes.", show_alert=True)
            return
        
        if not pending_submissions:
            await query.edit_message_text(
                "No hay solicitudes pendientes en este momento.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
                ])
            )
            return
        
        # Mostrar lista de solicitudes pendientes
        submissions_text = "<b>📋 Solicitudes Pendientes</b>\n\n"
        
        keyboard = []
        for submission_id, submission in pending_submissions.items():
            submissions_text += f"• Canal: <b>{html.escape(submission['channel_name'])}</b>\n"
            submissions_text += f"  Categoría: {submission['category']}\n"
            submissions_text += f"  Usuario: {html.escape(submission['user_name'])}\n\n"
            
            keyboard = [[
                InlineKeyboardButton(
                    f"Ver: {submission['channel_name'][:20]}...", 
                    callback_data=f"view_submission_{submission_id}"
                )
            ]]
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")])
        
        await query.edit_message_text(
            submissions_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return
    
    # Manejar ver una solicitud específica
    if callback_data.startswith("view_submission_"):
        if user_id != ADMIN_ID:
            await query.answer("Solo el administrador principal puede ver las solicitudes.", show_alert=True)
            return
        
        submission_id = callback_data[15:]
        
        if submission_id not in pending_submissions:
            await query.edit_message_text(
                "Esta solicitud ya no está disponible o ha sido procesada.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver a Solicitudes", callback_data="admin_submissions")]
                ])
            )
            return
        
        submission = pending_submissions[submission_id]
        
        # Mostrar detalles de la solicitud
        submission_text = (
            f"📋 <b>Detalles de la Solicitud</b>\n\n"
            f"<b>Canal:</b> {html.escape(submission['channel_name'])}\n"
            f"<b>Username:</b> @{html.escape(submission['channel_username'])}\n"
            f"<b>ID:</b> {html.escape(submission['channel_id'])}\n"
            f"<b>Categoría:</b> {submission['category']}\n"
            f"<b>Solicitado por:</b> {html.escape(submission['user_name'])}\n"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Aprobar", callback_data=f"approve_{submission_id}"),
                InlineKeyboardButton("❌ Rechazar", callback_data=f"reject_{submission_id}")
            ],
            [
                InlineKeyboardButton("🔍 Ver Canal", url=f"https://t.me/{submission['channel_username']}"),
                InlineKeyboardButton("📋 Ver Categoría", url=CATEGORIES[submission['category']])
            ],
            [InlineKeyboardButton("🔙 Volver a Solicitudes", callback_data="admin_submissions")]
        ]
        
        await query.edit_message_text(
            submission_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return
    
    # Manejar botones de ayuda
    if callback_data.startswith("help_"):
        help_type = callback_data[5:]
        help_texts = {
            "basic": (
                "<b>📝 Comandos Básicos</b>\n\n"
                "/start - Iniciar el bot\n"
                "/help - Mostrar ayuda\n"
                "/categories - Ver categorías disponibles\n"
                "/stats - Ver tus estadísticas"
            ),
            "mod": (
                "<b>👮 Comandos de Moderación</b>\n\n"
                "/warn - Advertir a un usuario\n"
                "/unwarn - Quitar advertencia a un usuario\n"
                "/mute - Silenciar a un usuario\n"
                "/unmute - Quitar silencio a un usuario\n"
                "/ban - Banear a un usuario\n"
                "/unban - Desbanear a un usuario"
            ),
            "stats": (
                "<b>📊 Comandos de Estadísticas</b>\n\n"
                "/stats - Ver tus estadísticas en el grupo\n"
                "También puedes ver estadísticas globales desde el menú principal."
            ),
            "channels": (
                "<b>🔄 Comandos de Canales</b>\n\n"
                "Para añadir un canal, envía un mensaje con el formato:\n"
                "<code>#Categoría\nNombre del Canal\n@username_canal\nID -100xxxxxxxxxx\n@admin bot añadido</code>\n\n"
                "Para ver las categorías disponibles usa /categories"
            ),
            "config": (
                "<b>⚙️ Comandos de Configuración</b>\n\n"
                "/setwelcome - Establecer mensaje de bienvenida\n"
                "/addbutton - Añadir botón al mensaje de bienvenida\n"
                "/removebutton - Eliminar botón del mensaje de bienvenida\n"
                "/showwelcome - Mostrar configuración actual\n"
                "/resetwelcome - Restablecer configuración por defecto"
            ),
            "fun": (
                "<b>🎮 Comandos de Diversión</b>\n\n"
                "Próximamente se añadirán comandos de diversión."
            )
        }
        
        if help_type in help_texts:
            await query.edit_message_text(
                help_texts[help_type],
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="help_back")]
                ])
            )
        elif help_type == "back":
            # Volver al menú de ayuda principal
            keyboard = [
                [
                    InlineKeyboardButton("📝 Comandos Básicos", callback_data="help_basic"),
                    InlineKeyboardButton("👮 Comandos de Moderación", callback_data="help_mod")
                ],
                [
                    InlineKeyboardButton("📊 Estadísticas", callback_data="help_stats"),
                    InlineKeyboardButton("🔄 Canales", callback_data="help_channels")
                ],
                [
                    InlineKeyboardButton("⚙️ Configuración", callback_data="help_config"),
                    InlineKeyboardButton("🎮 Diversión", callback_data="help_fun")
                ],
                [InlineKeyboardButton("🔙 Menú Principal", callback_data="back_to_main")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "<b>🤖 Centro de Ayuda</b>\n\n"
                "Selecciona una categoría para ver los comandos disponibles:",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
        
        return

    # Manejar botones de estadísticas
    if callback_data == "refresh_stats":
        user_statistics = get_user_stats(user_id, query.message.chat.id)
        warnings = get_warnings(user_id, query.message.chat.id)
        
        stats_message = (
            f"📊 <b>Estadísticas de {update.effective_user.first_name}</b>\n\n"
            f"Mensajes enviados: {user_statistics['messages']}\n"
            f"Medios compartidos: {user_statistics['media']}\n"
            f"Comandos utilizados: {user_statistics['commands']}\n"
            f"Advertencias: {warnings['count']}/3\n"
            f"Última actividad: {user_statistics['last_active'] if user_statistics['last_active'] else 'Desconocida'}"
        )
        
        await query.edit_message_text(
            stats_message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📈 Estadísticas Globales", callback_data="global_stats"),
                    InlineKeyboardButton("🔄 Actualizar", callback_data="refresh_stats")
                ],
                [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
            ])
        )
        return

    if callback_data == "global_stats":
        # Obtener estadísticas globales (ejemplo simplificado)
        await query.edit_message_text(
            "<b>📈 Estadísticas Globales</b>\n\n"
            "Esta función estará disponible próximamente.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="show_stats")]
            ])
        )
        return

    # Manejar botones del panel de administrador
    if callback_data.startswith("admin_"):
        admin_section = callback_data[6:]
        
        if admin_section == "stats":
            await query.edit_message_text(
                "<b>📊 Estadísticas del Grupo</b>\n\n"
                "Esta función estará disponible próximamente.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
                ])
            )
        elif admin_section == "config":
            await query.edit_message_text(
                "<b>⚙️ Configuración</b>\n\n"
                "Selecciona una opción para configurar:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📝 Mensaje de Bienvenida", callback_data="admin_welcome"),
                        InlineKeyboardButton("🔔 Notificaciones", callback_data="admin_notifications")
                    ],
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
                ])
            )
        elif admin_section == "moderation":
            await query.edit_message_text(
                "<b>👮 Herramientas de Moderación</b>\n\n"
                "Selecciona una opción:",
                parse_Mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔇 Usuarios Silenciados", callback_data="admin_muted"),
                        InlineKeyboardButton("🚫 Usuarios Baneados", callback_data="admin_banned")
                    ],
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
                ])
            )
        elif admin_section == "announce":
            await query.edit_message_text(
                "<b>📢 Crear Anuncio</b>\n\n"
                "Para crear un anuncio, usa el comando /announce seguido del mensaje que quieres enviar.\n\n"
                "Ejemplo: /announce ¡Importante! Nueva actualización del grupo.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
                ])
            )
        elif admin_section == "channels":
            # Obtener canales aprobados
            channels = get_approved_channels()
            
            channels_text = "<b>📋 Canales Aprobados</b>\n\n"
            
            if not channels:
                channels_text += "No hay canales aprobados todavía."
            else:
                for i, channel in enumerate(channels[:10], 1):  # Mostrar solo los primeros 10
                    channels_text += f"{i}. {html.escape(channel['channel_name'])} (@{html.escape(channel['channel_username'])})\n"
                    channels_text += f"   Categoría: {channel['category']}\n\n"
            
            await query.edit_message_text(
                channels_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
                ])
            )
        
        return

async def handle_rejection_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el motivo de rechazo del administrador."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID or user_id not in admin_rejecting:
        return
    
    submission_id = admin_rejecting[user_id]
    if submission_id not in pending_submissions:
        await update.message.reply_text("Esta solicitud ya no está disponible.")
        del admin_rejecting[user_id]
        return
    
    submission = pending_submissions[submission_id]
    rejection_reason = update.message.text
    
    # Notificar al usuario sobre el rechazo
    try:
        user_keyboard = [
            [
                InlineKeyboardButton("🔄 Enviar Nueva Solicitud", callback_data="add_channel_help"),
                InlineKeyboardButton("❓ Ayuda", callback_data="help_channels")
            ]
        ]
        user_reply_markup = InlineKeyboardMarkup(user_keyboard)
        
        await context.bot.send_message(
            chat_id=submission["chat_id"],
            text=f"❌ Tu solicitud para añadir el canal <b>{html.escape(submission['channel_name'])}</b> "
                 f"a la categoría <b>{submission['category']}</b> ha sido rechazada.\n\n"
                 f"<b>Motivo:</b> {html.escape(rejection_reason)}",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=submission["message_id"],
            reply_markup=user_reply_markup
        )
        
        # Confirmar al administrador
        await update.message.reply_text(
            f"✅ Rechazo enviado al usuario para el canal {submission['channel_name']}."
        )
        
        # Eliminar la solicitud de la base de datos
        delete_pending_submission(submission_id)
        
    except Exception as e:
        logger.error(f"Error sending rejection: {e}")
        await update.message.reply_text(
            f"❌ Error al enviar el rechazo: {e}"
        )
    
    # Limpiar
    del pending_submissions[submission_id]
    del admin_rejecting[user_id]

async def list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista todas las categorías disponibles."""
    categories_text = "<b>📚 Categorías disponibles:</b>\n\n"
    
    keyboard = []
    for i, (category, url) in enumerate(CATEGORIES.items(), 1):
        categories_text += f"{i}. {category}\n"
        # Crear filas de 2 botones
        if i % 2 == 1:
            row = [InlineKeyboardButton(category, url=url)]
        else:
            row.append(InlineKeyboardButton(category, url=url))
            keyboard.append(row)
    
    # Añadir la última fila si quedó incompleta
    if len(CATEGORIES) % 2 == 1:
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        categories_text,
        reply_markup=reply_markup
    )
    
    # Actualizar estadísticas
    if update.effective_user:
        update_user_stats(update.effective_user.id, update.effective_chat.id, "commands")

# Comandos de moderación
async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Advierte a un usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Por favor, menciona al usuario que deseas advertir.\n"
            "Ejemplo: /warn @usuario Razón de la advertencia"
        )
        return
    
    # Obtener usuario objetivo
    target_user = None
    reason = "Sin especificar"
    
    # Si el mensaje es una respuesta, usar ese usuario
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        if len(context.args) >= 1:
            reason = " ".join(context.args)
    else:
        # Intentar obtener usuario por nombre de usuario o ID
        try:
            if context.args[0].startswith("@"):
                # Es un nombre de usuario
                username = context.args[0][1:]
                # No podemos obtener el ID directamente del nombre de usuario
                # Informar al usuario
                await update.message.reply_text(
                    "Por favor, responde al mensaje del usuario que deseas advertir o usa su ID numérico."
                )
                return
            else:
                # Podría ser un ID
                try:
                    target_id = int(context.args[0])
                    try:
                        target_user = await context.bot.get_chat_member(chat_id, target_id)
                        target_user = target_user.user
                    except TelegramError:
                        await update.message.reply_text("No se pudo encontrar al usuario con ese ID.")
                        return
                except ValueError:
                    await update.message.reply_text("ID de usuario inválido.")
                    return
            
            if len(context.args) >= 2:
                reason = " ".join(context.args[1:])
        except IndexError:
            await update.message.reply_text("Por favor, proporciona un usuario.")
            return
    
    if not target_user:
        await update.message.reply_text("No se pudo identificar al usuario.")
        return
    
    # Añadir advertencia
    warn_count = add_warning(target_user.id, chat_id, reason)
    
    # Crear mensaje de advertencia
    warn_message = (
        f"⚠️ <b>Advertencia</b> ⚠️\n\n"
        f"Usuario: {target_user.mention_html()}\n"
        f"Advertencias: {warn_count}/3\n"
        f"Razón: {html.escape(reason)}\n\n"
    )
    
    # Añadir información sobre consecuencias
    if warn_count >= 3:
        # Banear al usuario después de 3 advertencias
        try:
            await context.bot.ban_chat_member(chat_id, target_user.id)
            warn_message += "El usuario ha sido <b>baneado</b> por acumular 3 advertencias."
        except TelegramError as e:
            warn_message += f"Error al banear al usuario: {e}"
    elif warn_count == 2:
        warn_message += "Una advertencia más resultará en un <b>ban permanente</b>."
    else:
        warn_message += "Acumular 3 advertencias resultará en un <b>ban permanente</b>."
    
    # Enviar mensaje
    keyboard = [
        [InlineKeyboardButton("🔄 Quitar Advertencia", callback_data=f"unwarn_{target_user.id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(warn_message, reply_markup=reply_markup)
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def unwarn_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quita una advertencia a un usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text(
            "Por favor, menciona al usuario al que deseas quitar una advertencia.\n"
            "Ejemplo: /unwarn @usuario"
        )
        return
    
    # Obtener usuario objetivo
    target_user = None
    
    # Si el mensaje es una respuesta, usar ese usuario
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
    else:
        # Intentar obtener usuario por nombre de usuario o ID
        try:
            if context.args[0].startswith("@"):
                # Es un nombre de usuario
                username = context.args[0][1:]
                # No podemos obtener el ID directamente del nombre de usuario
                await update.message.reply_text(
                    "Por favor, responde al mensaje del usuario o usa su ID numérico."
                )
                return
            else:
                # Podría ser un ID
                try:
                    target_id = int(context.args[0])
                    try:
                        target_user = await context.bot.get_chat_member(chat_id, target_id)
                        target_user = target_user.user
                    except TelegramError:
                        await update.message.reply_text("No se pudo encontrar al usuario con ese ID.")
                        return
                except ValueError:
                    await update.message.reply_text("ID de usuario inválido.")
                    return
        except IndexError:
            await update.message.reply_text("Por favor, proporciona un usuario.")
            return
    
    if not target_user:
        await update.message.reply_text("No se pudo identificar al usuario.")
        return
    
    # Obtener advertencias actuales
    warnings = get_warnings(target_user.id, chat_id)
    
    if warnings["count"] <= 0:
        await update.message.reply_text(f"El usuario {target_user.mention_html()} no tiene advertencias.", parse_mode=ParseMode.HTML)
        return
    
    # Restar una advertencia
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE warnings SET count = count - 1 WHERE user_id = ? AND chat_id = ?",
        (target_user.id, chat_id)
    )
    
    conn.commit()
    conn.close()
    
    # Obtener nuevo conteo
    new_warnings = get_warnings(target_user.id, chat_id)
    
    await update.message.reply_html(
        f"✅ Se ha quitado una advertencia a {target_user.mention_html()}.\n"
        f"Advertencias actuales: {new_warnings['count']}/3"
    )
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silencia a un usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden usar este comando.")
        return
    
    # Verificar argumentos
    if not update.message.reply_to_message and (not context.args or len(context.args) < 1):
        await update.message.reply_text(
            "Por favor, responde al mensaje del usuario que deseas silenciar o proporciona su ID/username.\n"
            "Ejemplo: /mute @usuario 30m Razón del silencio"
        )
        return
    
    # Obtener usuario objetivo
    target_user = None
    mute_time = 60 * 60  # 1 hora por defecto
    reason = "Sin especificar"
    time_arg_index = 0
    
    # Si el mensaje es una respuesta, usar ese usuario
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        if context.args:
            # El primer argumento podría ser el tiempo
            time_arg_index = 0
    else:
        # Intentar obtener usuario por nombre de usuario o ID
        try:
            if context.args[0].startswith("@"):
                # Es un nombre de usuario
                username = context.args[0][1:]
                # No podemos obtener el ID directamente del nombre de usuario
                await update.message.reply_text(
                    "Por favor, responde al mensaje del usuario o usa su ID numérico."
                )
                return
            else:
                # Podría ser un ID
                try:
                    target_id = int(context.args[0])
                    try:
                        target_user = await context.bot.get_chat_member(chat_id, target_id)
                        target_user = target_user.user
                    except TelegramError:
                        await update.message.reply_text("No se pudo encontrar al usuario con ese ID.")
                        return
                except ValueError:
                    await update.message.reply_text("ID de usuario inválido.")
                    return
            
            # El segundo argumento podría ser el tiempo
            time_arg_index = 1
        except IndexError:
            await update.message.reply_text("Por favor, proporciona un usuario.")
            return
    
    if not target_user:
        await update.message.reply_text("No se pudo identificar al usuario.")
        return
    
    # Verificar si hay un argumento de tiempo
    if len(context.args) > time_arg_index:
        time_arg = context.args[time_arg_index]
        
        # Verificar si es un formato de tiempo válido
        if time_arg[-1] in ['m', 'h', 'd']:
            try:
                time_value = int(time_arg[:-1])
                time_unit = time_arg[-1]
                
                if time_unit == 'm':
                    mute_time = time_value * 60  # minutos a segundos
                elif time_unit == 'h':
                    mute_time = time_value * 60 * 60  # horas a segundos
                elif time_unit == 'd':
                    mute_time = time_value * 60 * 60 * 24  # días a segundos
                
                # Obtener razón (argumentos restantes)
                if len(context.args) > time_arg_index + 1:
                    reason = " ".join(context.args[time_arg_index + 1:])
            except ValueError:
                # No es un formato de tiempo, tratar como parte de la razón
                reason = " ".join(context.args[time_arg_index:])
        else:
            # No es un formato de tiempo, tratar como parte de la razón
            reason = " ".join(context.args[time_arg_index:])
    
    # Calcular tiempo de finalización
    until_date = datetime.now() + timedelta(seconds=mute_time)
    
    # Silenciar al usuario
    try:
        permissions = ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False
        )
        
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target_user.id,
            permissions=permissions,
            until_date=until_date
        )
        
        # Registrar usuario silenciado
        muted_users[target_user.id] = {
            "until": until_date,
            "reason": reason
        }
        
        # Crear mensaje de silencio
        mute_message = (
            f"🔇 <b>Usuario Silenciado</b> 🔇\n\n"
            f"Usuario: {target_user.mention_html()}\n"
            f"Duración: {format_time_delta(mute_time)}\n"
            f"Razón: {html.escape(reason)}\n\n"
            f"El silencio terminará: {until_date.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Enviar mensaje
        keyboard = [
            [InlineKeyboardButton("🔊 Quitar Silencio", callback_data=f"unmute_{target_user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_html(mute_message, reply_markup=reply_markup)
        
    except TelegramError as e:
        await update.message.reply_text(f"Error al silenciar al usuario: {e}")
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quita el silencio a un usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden usar este comando.")
        return
    
    # Verificar argumentos
    if not update.message.reply_to_message and (not context.args or len(context.args) < 1):
        await update.message.reply_text(
            "Por favor, responde al mensaje del usuario al que deseas quitar el silencio o proporciona su ID/username.\n"
            "Ejemplo: /unmute @usuario"
        )
        return
    
    # Obtener usuario objetivo
    target_user = None
    
    # Si el mensaje es una respuesta, usar ese usuario
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
    else:
        # Intentar obtener usuario por nombre de usuario o ID
        try:
            if context.args[0].startswith("@"):
                # Es un nombre de usuario
                username = context.args[0][1:]
                # No podemos obtener el ID directamente del nombre de usuario
                await update.message.reply_text(
                    "Por favor, responde al mensaje del usuario o usa su ID numérico."
                )
                return
            else:
                # Podría ser un ID
                try:
                    target_id = int(context.args[0])
                    try:
                        target_user = await context.bot.get_chat_member(chat_id, target_id)
                        target_user = target_user.user
                    except TelegramError:
                        await update.message.reply_text("No se pudo encontrar al usuario con ese ID.")
                        return
                except ValueError:
                    await update.message.reply_text("ID de usuario inválido.")
                    return
        except IndexError:
            await update.message.reply_text("Por favor, proporciona un usuario.")
            return
    
    if not target_user:
        await update.message.reply_text("No se pudo identificar al usuario.")
        return
    
    # Quitar silencio al usuario
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=True,
            can_pin_messages=False
        )
        
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target_user.id,
            permissions=permissions
        )
        
        # Eliminar de usuarios silenciados
        if target_user.id in muted_users:
            del muted_users[target_user.id]
        
        await update.message.reply_html(
            f"🔊 Se ha quitado el silencio a {target_user.mention_html()}."
        )
        
    except TelegramError as e:
        await update.message.reply_text(f"Error al quitar el silencio al usuario: {e}")
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Banea a un usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden usar este comando.")
        return
    
    # Verificar argumentos
    if not update.message.reply_to_message and (not context.args or len(context.args) < 1):
        await update.message.reply_text(
            "Por favor, responde al mensaje del usuario que deseas banear o proporciona su ID/username.\n"
            "Ejemplo: /ban @usuario Razón del ban"
        )
        return
    
    # Obtener usuario objetivo
    target_user = None
    reason = "Sin especificar"
    
    # Si el mensaje es una respuesta, usar ese usuario
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        if context.args:
            reason = " ".join(context.args)
    else:
        # Intentar obtener usuario por nombre de usuario o ID
        try:
            if context.args[0].startswith("@"):
                # Es un nombre de usuario
                username = context.args[0][1:]
                # No podemos obtener el ID directamente del nombre de usuario
                await update.message.reply_text(
                    "Por favor, responde al mensaje del usuario o usa su ID numérico."
                )
                return
            else:
                # Podría ser un ID
                try:
                    target_id = int(context.args[0])
                    try:
                        target_user = await context.bot.get_chat_member(chat_id, target_id)
                        target_user = target_user.user
                    except TelegramError:
                        await update.message.reply_text("No se pudo encontrar al usuario con ese ID.")
                        return
                except ValueError:
                    await update.message.reply_text("ID de usuario inválido.")
                    return
            
            if len(context.args) >= 2:
                reason = " ".join(context.args[1:])
        except IndexError:
            await update.message.reply_text("Por favor, proporciona un usuario.")
            return
    
    if not target_user:
        await update.message.reply_text("No se pudo identificar al usuario.")
        return
    
    # Banear al usuario
    try:
        await context.bot.ban_chat_member(chat_id, target_user.id)
        
        # Crear mensaje de ban
        ban_message = (
            f"🚫 <b>Usuario Baneado</b> 🚫\n\n"
            f"Usuario: {target_user.mention_html()}\n"
            f"ID: {target_user.id}\n"
            f"Razón: {html.escape(reason)}"
        )
        
        # Enviar mensaje
        keyboard = [
            [InlineKeyboardButton("🔓 Desbanear", callback_data=f"unban_{target_user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_html(ban_message, reply_markup=reply_markup)
        
    except TelegramError as e:
        await update.message.reply_text(f"Error al banear al usuario: {e}")
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Desbanea a un usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario es administrador
    if not await is_admin(user_id, chat_id, context):
        await update.message.reply_text("Solo los administradores pueden usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Por favor, proporciona el ID del usuario que deseas desbanear.\n"
            "Ejemplo: /unban 123456789"
        )
        return
    
    # Obtener ID del usuario
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID de usuario inválido.")
        return
    
    # Desbanear al usuario
    try:
        await context.bot.unban_chat_member(chat_id, target_id)
        
        await update.message.reply_html(
            f"✅ Usuario con ID {target_id} desbaneado exitosamente."
        )
        
    except TelegramError as e:
        await update.message.reply_text(f"Error al desbanear al usuario: {e}")
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía un anuncio al grupo."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador principal puede usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Por favor, proporciona el mensaje que deseas anunciar.\n"
            "Ejemplo: /announce Importante: Nueva actualización del grupo"
        )
        return
    
    # Obtener mensaje
    announcement = " ".join(context.args)
    
    # Crear mensaje de anuncio
    announcement_message = (
        f"📢 <b>ANUNCIO IMPORTANTE</b> 📢\n\n"
        f"{html.escape(announcement)}\n\n"
        f"<i>Enviado por: Administración</i>"
    )
    
    # Crear botones
    keyboard = [
        [
            InlineKeyboardButton("👍 Entendido", callback_data="ack_announcement"),
            InlineKeyboardButton("❓ Más Información", callback_data="more_info_announcement")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Enviar anuncio al grupo
    try:
        await context.bot.send_message(
            chat_id=f"@{GROUP_ID}",
            text=announcement_message,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
        await update.message.reply_text("✅ Anuncio enviado exitosamente.")
        
    except TelegramError as e:
        await update.message.reply_text(f"Error al enviar el anuncio: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra estadísticas del usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Obtener estadísticas
    user_stats = get_user_stats(user_id, chat_id)
    
    # Obtener advertencias
    warnings = get_warnings(user_id, chat_id)
    
    # Crear mensaje de estadísticas
    stats_message = (
        f"📊 <b>Estadísticas de {update.effective_user.mention_html()}</b>\n\n"
        f"Mensajes enviados: {user_stats['messages']}\n"
        f"Medios compartidos: {user_stats['media']}\n"
        f"Comandos utilizados: {user_stats['commands']}\n"
        f"Advertencias: {warnings['count']}/3\n"
        f"Última actividad: {user_stats['last_active'] if user_stats['last_active'] else 'Desconocida'}"
    )
    
    # Crear botones
    keyboard = [
        [
            InlineKeyboardButton("📈 Estadísticas Globales", callback_data="global_stats"),
            InlineKeyboardButton("🔄 Actualizar", callback_data="refresh_stats")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        stats_message,
        reply_markup=reply_markup
    )
    
    # Actualizar estadísticas
    update_user_stats(user_id, chat_id, "commands")

# Manejadores de mensajes
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja todos los mensajes."""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el usuario está silenciado
    if user_id in muted_users:
        mute_info = muted_users[user_id]
        if datetime.now() < mute_info["until"]:
            # Eliminar mensaje si el usuario está silenciado
            try:
                await update.message.delete()
                return
            except:
                pass
        else:
            # El silencio ha expirado, eliminar de la lista
            del muted_users[user_id]
    
    # Verificar spam
    if check_spam(user_id) and not await is_admin(user_id, chat_id, context):
        # Silenciar al usuario temporalmente
        try:
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            )
            
            until_date = datetime.now() + timedelta(seconds=SPAM_MUTE_TIME)
            
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=permissions,
                until_date=until_date
            )
            
            # Registrar usuario silenciado
            muted_users[user_id] = {
                "until": until_date,
                "reason": "Spam detectado"
            }
            
            await update.message.reply_html(
                f"🔇 {update.effective_user.mention_html()} ha sido silenciado por {format_time_delta(SPAM_MUTE_TIME)} por enviar mensajes demasiado rápido."
            )
            
            # Eliminar mensaje
            try:
                await update.message.delete()
            except:
                pass
            
            return
        except:
            pass
    
    # Procesar solicitud de canal si corresponde
    if update.message and update.message.text and "#" in update.message.text:
        await process_channel_submission(update, context)
    
    # Actualizar estadísticas
    if update.message:
        if update.message.photo or update.message.video or update.message.document or update.message.animation:
            update_user_stats(user_id, chat_id, "media")
        else:
            update_user_stats(user_id, chat_id, "messages")
    
    # Actualizar última actividad
    user_last_activity[user_id] = datetime.now()

# Función principal
def main() -> None:
    """Inicia el bot."""
    # Inicializar base de datos
    init_db()
    
    # Cargar configuración
    load_config()
    
    # Cargar solicitudes pendientes
    load_pending_submissions()
    
    # Crear la aplicación y pasarle el token del bot
    application = Application.builder().token(TOKEN).build()
    
    # Registrar manejador de errores
    application.add_error_handler(lambda update, context: logger.error(f"Error: {context.error} in update {update}"))
    
    # Comandos básicos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("categories", list_categories))
    application.add_handler(CommandHandler("stats", stats))
    
    # Comandos de mensaje de bienvenida
    application.add_handler(CommandHandler("setwelcome", set_welcome))
    application.add_handler(CommandHandler("addbutton", add_welcome_button))
    application.add_handler(CommandHandler("removebutton", remove_welcome_button))
    application.add_handler(CommandHandler("showwelcome", show_welcome))
    application.add_handler(CommandHandler("resetwelcome", reset_welcome))
    
    # Comandos de moderación
    application.add_handler(CommandHandler("warn", warn_user))
    application.add_handler(CommandHandler("unwarn", unwarn_user))
    application.add_handler(CommandHandler("mute", mute_user))
    application.add_handler(CommandHandler("unmute", unmute_user))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("announce", announce))
    
    # Dar bienvenida a nuevos miembros
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    # Manejar motivos de rechazo del administrador
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID) & filters.ChatType.PRIVATE,
        handle_rejection_reason
    ))
    
    # Manejar callbacks de botones
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Manejar todos los mensajes
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_message))
    
    # Ejecutar el bot hasta que el usuario presione Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

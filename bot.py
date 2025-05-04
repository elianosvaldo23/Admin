import logging
import re
import html
import os
import time
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, Counter

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError, BadRequest

from config import *
from db import MongoDB

# Inicializar la base de datos MongoDB
db = MongoDB()

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

# Almacenamiento en memoria
pending_submissions = {}
admin_rejecting = {}
custom_welcome = {
    "message": DEFAULT_WELCOME_MESSAGE,
    "buttons": [
        {"text": "Canal Principal", "url": "https://t.me/botoneraMultimediaTv"},
        {"text": "Categorías", "url": "https://t.me/c/2259108243/2"},
        {"text": "📣 Canales y Grupos 👥", "callback_data": "user_channels"}
    ]
}
user_message_count = defaultdict(list)  # Para anti-spam
muted_users = {}  # Para seguimiento de usuarios silenciados
user_stats = defaultdict(Counter)  # Para estadísticas
user_warnings = defaultdict(int)  # Para sistema de advertencias
user_last_activity = {}  # Para seguimiento de actividad
user_editing_state = {}  # Para seguimiento de estados de edición
scheduled_posts = {}  # Para posts programados

# Cargar configuración desde MongoDB
def load_config_from_db():
    """Carga la configuración desde la base de datos MongoDB."""
    global custom_welcome
    
    welcome_message = db.load_config("welcome_message")
    if welcome_message:
        custom_welcome["message"] = welcome_message
    
    welcome_buttons = db.load_config("welcome_buttons")
    if welcome_buttons:
        custom_welcome["buttons"] = welcome_buttons
    
    # Cargar solicitudes pendientes
    global pending_submissions
    pending_submissions = db.get_pending_submissions()


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
            [InlineKeyboardButton("📣 Canales y Grupos 👥", callback_data="user_channels")],
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
        db.update_user_stats(update.effective_user.id, update.effective_chat.id, "commands")

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
        db.update_user_stats(update.effective_user.id, update.effective_chat.id, "commands")

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
            if "url" in button:
                row.append(InlineKeyboardButton(button["text"], url=button["url"]))
            else:
                row.append(InlineKeyboardButton(button["text"], callback_data=button["callback_data"]))
                
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
    db.save_config("welcome_message", new_message)
    
    # Mostrar vista previa
    keyboard = []
    row = []
    for i, button in enumerate(custom_welcome["buttons"]):
        if "url" in button:
            row.append(InlineKeyboardButton(button["text"], url=button["url"]))
        else:
            row.append(InlineKeyboardButton(button["text"], callback_data=button["callback_data"]))
            
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
    db.update_user_stats(user_id, chat_id, "commands")

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
    db.save_config("welcome_buttons", custom_welcome["buttons"])
    
    await update.message.reply_text(f"✅ Botón añadido: {button_text} -> {button_url}")
    
    # Actualizar estadísticas
    db.update_user_stats(user_id, chat_id, "commands")

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
    db.update_user_stats(user_id, chat_id, "commands")

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
        if "url" in button:
            message_text += f"{i+1}. {button['text']} -> {button['url']}\n"
        else:
            message_text += f"{i+1}. {button['text']} -> Callback: {button['callback_data']}\n"
    
    # Crear ejemplo de cómo se ve
    keyboard = []
    row = []
    for i, button in enumerate(custom_welcome["buttons"]):
        if "url" in button:
            row.append(InlineKeyboardButton(button["text"], url=button["url"]))
        else:
            row.append(InlineKeyboardButton(button["text"], callback_data=button["callback_data"]))
            
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
    db.update_user_stats(user_id, chat_id, "commands")

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
        {"text": "Categorías", "url": "https://t.me/c/2259108243/2"},
        {"text": "📣 Canales y Grupos 👥", "callback_data": "user_channels"}
    ]
    
    # Guardar en la base de datos
    db.save_config("welcome_message", DEFAULT_WELCOME_MESSAGE)
    db.save_config("welcome_buttons", custom_welcome["buttons"])
    
    await update.message.reply_text("✅ Mensaje de bienvenida restablecido a los valores predeterminados.")
    
    # Actualizar estadísticas
    db.update_user_stats(user_id, chat_id, "commands")

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
        db.save_pending_submission(submission_id, submission_data)
        
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
        db.update_user_stats(user.id, update.effective_chat.id, "messages")
        
    except Exception as e:
        logger.error(f"Error processing channel submission: {e}")
        await update.message.reply_text(
            "❌ Ocurrió un error al procesar tu solicitud. Por favor, verifica el formato e intenta nuevamente."
        )

async def handle_channel_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la visualización de la lista de canales del usuario."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Obtener los canales añadidos por este usuario
    user_channels = db.get_approved_channels(user_id=user_id)
    
    # Contar canales y grupos
    channel_count = 0
    group_count = 0
    total_channel_subs = 0
    total_group_subs = 0
    
    for channel in user_channels:
        # Determinamos si es un canal o un grupo basado en el ID
        # Los IDs de canales suelen empezar con '-100'
        if str(channel["channel_id"]).startswith("-100"):
            channel_count += 1
            total_channel_subs += channel.get("subscribers", 0)
        else:
            group_count += 1
            total_group_subs += channel.get("subscribers", 0)
    
    # Construir el mensaje
    message = (
        "📣 Canales y Grupos 👥\n\n"
        "☁️ Gestiona los canales o grupos que has añadido a las Categorías\n\n"
        f"📣 Canales: {channel_count}\n"
        f"    ┗👤{total_channel_subs}\n"
        f"👥 Grupos: {group_count}\n"
        f"    ┗👤{total_group_subs}\n\n"
    )
    
    # Añadir la lista de canales
    keyboard = []
    
    for i, channel in enumerate(user_channels, 1):
        # Determinar si es un canal o grupo
        tipo = "📣" if str(channel["channel_id"]).startswith("-100") else "👥"
        
        message += (
            f"{i}. {tipo} {channel['channel_name']}\n"
            f"      ┗👤{channel.get('subscribers', 0)}\n\n"
        )
        
        # Añadir los botones para este canal
        row = [
            InlineKeyboardButton(f"#{i}", url=f"https://t.me/{channel['channel_username']}"),
            InlineKeyboardButton("📝", callback_data=f"edit_channel_{channel['channel_id']}"),
            InlineKeyboardButton("🗑️", callback_data=f"delete_channel_{channel['channel_id']}")
        ]
        keyboard.append(row)
    
    # Añadir botón para volver
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")])
    
    # Si no hay canales, indicarlo
    if not user_channels:
        message += "No has añadido ningún canal o grupo todavía."
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Enviar o editar el mensaje según corresponda
    if query:
        await query.edit_message_text(message, reply_markup=reply_markup)
        await query.answer()
    else:
        await update.message.reply_text(message, reply_markup=reply_markup)

async def edit_channel_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra opciones para editar la información de un canal."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Extraer el ID del canal
    channel_id = query.data.split("_")[2]
    
    # Buscar información del canal
    channels = db.get_approved_channels()
    target_channel = None
    
    for channel in channels:
        if channel["channel_id"] == channel_id:
            target_channel = channel
            break
    
    if not target_channel:
        await query.answer("No se encontró información del canal.")
        await query.edit_message_text("Canal no encontrado o eliminado.")
        return
    
    # Verificar que el usuario es el propietario o administrador
    if target_channel["added_by"] != user_id and user_id != ADMIN_ID:
        await query.answer("No tienes permiso para editar este canal.")
        return
    
    # Mostrar información y opciones de edición
    message = (
        "✏️ Editar\n\n"
        f"🏷 {target_channel['channel_name']}\n"
        f"🆔 {target_channel['channel_id']}\n"
        f"🔗 https://t.me/{target_channel['channel_username']}\n"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("📝 Cambiar Nombre", callback_data=f"change_name_{channel_id}"),
            InlineKeyboardButton("📝 Modificar Enlace", callback_data=f"change_link_{channel_id}")
        ],
        [InlineKeyboardButton("Volver 🔙", callback_data="user_channels")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    await query.answer()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra estadísticas del usuario."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Obtener estadísticas
    user_stats = db.get_user_stats(user_id, chat_id)
    warnings = db.get_warnings(user_id, chat_id)
    
    # Crear mensaje de estadísticas
    stats_message = (
        f"📊 <b>Estadísticas de {update.effective_user.first_name}</b>\n\n"
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
    db.update_user_stats(user_id, chat_id, "commands")

async def handle_change_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inicia el proceso para cambiar el nombre de un canal."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Extraer el ID del canal
    channel_id = query.data.split("_")[2]
    
    # Guardar el estado de edición
    user_editing_state[user_id] = {
        "action": "change_name",
        "channel_id": channel_id
    }
    
    # Buscar información del canal
    channels = db.get_approved_channels()
    target_channel = None
    
    for channel in channels:
        if channel["channel_id"] == channel_id:
            target_channel = channel
            break
    
    if not target_channel:
        await query.answer("No se encontró información del canal.")
        await query.edit_message_text("Canal no encontrado o eliminado.")
        return
    
    # Mostrar mensaje solicitando el nuevo nombre
    message = f"📌 Envíe un nombre personalizado para {target_channel['channel_name']}"
    
    keyboard = [[InlineKeyboardButton("Cancelar", callback_data=f"cancel_edit_{channel_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    await query.answer()

async def handle_change_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inicia el proceso para cambiar el enlace de un canal."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Extraer el ID del canal
    channel_id = query.data.split("_")[2]
    
    # Guardar el estado de edición
    user_editing_state[user_id] = {
        "action": "change_link",
        "channel_id": channel_id
    }
    
    # Buscar información del canal
    channels = db.get_approved_channels()
    target_channel = None
    
    for channel in channels:
        if channel["channel_id"] == channel_id:
            target_channel = channel
            break
    
    if not target_channel:
        await query.answer("No se encontró información del canal.")
        await query.edit_message_text("Canal no encontrado o eliminado.")
        return
    
    # Mostrar mensaje solicitando el nuevo enlace
    message = f"📌 Envíe un nuevo enlace para {target_channel['channel_name']}"
    
    keyboard = [[InlineKeyboardButton("Cancelar", callback_data=f"cancel_edit_{channel_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    await query.answer()

async def handle_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la entrada de texto para editar un canal."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario está en modo de edición
    if user_id not in user_editing_state:
        return
    
    edit_state = user_editing_state[user_id]
    channel_id = edit_state["channel_id"]
    action = edit_state["action"]
    new_value = update.message.text
    
    # Buscar información del canal
    channels = db.get_approved_channels()
    target_channel = None
    
    for channel in channels:
        if channel["channel_id"] == channel_id:
            target_channel = channel
            break
    
    if not target_channel:
        await update.message.reply_text("Canal no encontrado o eliminado.")
        del user_editing_state[user_id]
        return
    
    # Actualizar el valor según la acción
    if action == "change_name":
        # Actualizar nombre del canal
        if db.update_channel_info(channel_id, "channel_name", new_value):
            await update.message.reply_text(f"✅ Nombre del canal actualizado a: {new_value}")
            
            # Actualizar la categoría correspondiente
            category = target_channel["category"]
            await update_category_message(context, category)
        else:
            await update.message.reply_text("❌ Error al actualizar el nombre del canal.")
    
    elif action == "change_link":
        # Validar y limpiar el enlace
        if new_value.startswith("https://t.me/"):
            username = new_value.split("/")[-1]
            if username.startswith("+"):
                username = username[1:]  # Eliminar el + de enlaces privados
        else:
            username = new_value.replace("@", "")
        
        # Actualizar username del canal
        if db.update_channel_info(channel_id, "channel_username", username):
            await update.message.reply_text(f"✅ Enlace del canal actualizado a: https://t.me/{username}")
            
            # Actualizar la categoría correspondiente
            category = target_channel["category"]
            await update_category_message(context, category)
        else:
            await update.message.reply_text("❌ Error al actualizar el enlace del canal.")
    
    # Limpiar el estado de edición
    del user_editing_state[user_id]
    
    # Mostrar menú de canales actualizado
    await handle_channel_list(update, context)

async def update_category_message(context, category):
    """Actualiza el mensaje de una categoría con la lista de canales actualizada."""
    try:
        # Obtener la URL del post para la categoría
        post_url = CATEGORIES[category]
        post_message_id = int(post_url.split("/")[-1])
        
        # Obtener todos los canales de la categoría
        channels = db.get_approved_channels(category=category)
        
        # Construir el mensaje con doble espacio entre canales
        new_text = f"{category}\n\n"  # Doble salto después del título
        
        # Añadir cada canal con formato y doble espacio
        for channel in channels:
            new_text += f"[{channel['channel_name']}](https://t.me/{channel['channel_username']})\n\n"  # Doble salto después de cada canal
        
        # Eliminar el último salto de línea extra si hay canales
        if channels:
            new_text = new_text.rstrip('\n')
        
        # Actualizar el mensaje en el canal
        await context.bot.edit_message_text(
            chat_id=CATEGORY_CHANNEL_ID,
            message_id=post_message_id,
            text=new_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        return True
    except Exception as e:
        logger.error(f"Error updating category message: {e}")
        return False

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
    db.update_user_stats(user_id, chat_id, "commands")

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
    db.update_user_stats(user_id, chat_id, "commands")

async def handle_delete_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la eliminación de un canal."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Extraer el ID del canal
    channel_id = query.data.split("_")[2]
    
    # Buscar información del canal
    channels = db.get_approved_channels()
    target_channel = None
    
    for channel in channels:
        if channel["channel_id"] == channel_id:
            target_channel = channel
            break
    
    if not target_channel:
        await query.answer("No se encontró información del canal.")
        await query.edit_message_text("Canal no encontrado o eliminado.")
        return
    
    # Verificar que el usuario es el propietario o administrador
    if target_channel["added_by"] != user_id and user_id != ADMIN_ID:
        await query.answer("No tienes permiso para eliminar este canal.")
        return
    
    # Eliminar el canal
    if db.delete_approved_channel(channel_id):
        # Actualizar la categoría correspondiente
        category = target_channel["category"]
        await update_category_message(context, category)
        
        await query.answer("Canal eliminado correctamente.")
        
        # Mostrar lista actualizada
        await handle_channel_list(update, context)
    else:
        await query.answer("Error al eliminar el canal.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja callbacks de botones."""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    user_id = update.effective_user.id
    
    # Manejar canales y grupos del usuario
    if callback_data == "user_channels":
        await handle_channel_list(update, context)
        return
    
    # Manejar edición de canales
    if callback_data.startswith("edit_channel_"):
        await edit_channel_info(update, context)
        return
    
    if callback_data.startswith("change_name_"):
        await handle_change_name(update, context)
        return
    
    if callback_data.startswith("change_link_"):
        await handle_change_link(update, context)
        return
    
    if callback_data.startswith("cancel_edit_"):
        # Cancelar edición y volver al menú de canales
        if user_id in user_editing_state:
            del user_editing_state[user_id]
        await handle_channel_list(update, context)
        return
    
    if callback_data.startswith("delete_channel_"):
        await handle_delete_channel(update, context)
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
            try:
                # Obtener la URL del post para la categoría
                post_url = CATEGORIES[submission["category"]]
                post_message_id = int(post_url.split("/")[-1])
                
                # Guardar el canal en la base de datos
                success, total_channels = db.save_approved_channel(
                    submission["channel_id"],
                    submission["channel_name"],
                    submission["channel_username"],
                    submission["category"],
                    submission["user_id"]
                )
                
                if success:
                    # Obtener todos los canales de la categoría
                    channels = db.get_approved_channels(category=submission["category"])
                    
                    # Construir el mensaje con doble espacio entre canales
                    new_text = f"{submission['category']}\n\n"  # Doble salto después del título
                    
                    # Añadir cada canal con formato y doble espacio
                    for channel in channels:
                        new_text += f"[{channel['channel_name']}](https://t.me/{channel['channel_username']})\n\n"  # Doble salto después de cada canal
                    
                    # Eliminar el último salto de línea extra si hay canales
                    if channels:
                        new_text = new_text.rstrip('\n')
                    
                    try:
                        # Actualizar el mensaje en el canal
                        await context.bot.edit_message_text(
                            chat_id=CATEGORY_CHANNEL_ID,
                            message_id=post_message_id,
                            text=new_text,
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True
                        )
                        
                        # Notificar al administrador
                        await query.edit_message_text(
                            f"✅ Canal aprobado y añadido a la categoría {submission['category']}.\n"
                            f"Total de canales en la categoría: {len(channels)}"
                        )
                        
                        # Notificar al usuario
                        user_keyboard = [
                            [
                                InlineKeyboardButton("🔍 Ver Categoría", url=post_url),
                                InlineKeyboardButton("📢 Compartir Canal", 
                                    url=f"https://t.me/share/url?url=https://t.me/{submission['channel_username']}")
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
                        
                    except Exception as e:
                        logger.error(f"Error updating message: {e}")
                        await query.edit_message_text(
                            f"❌ Error al actualizar el mensaje: {str(e)}\n"
                            f"Por favor, verifica manualmente el mensaje en la categoría {submission['category']}"
                        )
                else:
                    await query.edit_message_text(
                        f"❌ Error al guardar el canal en la base de datos."
                    )
                
                # Eliminar la solicitud de la base de datos
                db.delete_pending_submission(submission_id)
                
            except Exception as e:
                logger.error(f"Error in approval process: {e}")
                await query.edit_message_text(
                    f"❌ Error en el proceso de aprobación: {str(e)}"
                )
            
            # Eliminar de solicitudes pendientes
            if submission_id in pending_submissions:
                del pending_submissions[submission_id]
            return
            
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
            return

    elif callback_data.startswith("reject_reason_"):
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
        
        try:
            # Notificar al usuario sobre el rechazo
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
            db.delete_pending_submission(submission_id)
            
            # Eliminar de solicitudes pendientes
            if submission_id in pending_submissions:
                del pending_submissions[submission_id]
            
        except Exception as e:
            logger.error(f"Error sending rejection: {e}")
            await query.edit_message_text(
                f"❌ Error al enviar el rechazo: {str(e)}"
            )
        
        return

    elif callback_data.startswith("reject_custom_"):
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
        if submission_id in pending_submissions:
            del pending_submissions[submission_id]
        db.delete_pending_submission(submission_id)
        
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
            # Corregido: Ahora muestra el estado real de la solicitud
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
            [
                InlineKeyboardButton("📅 Post Automáticos", callback_data="admin_auto_post"),
                InlineKeyboardButton("📊 Informes", callback_data="admin_reports")
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
    
    # Manejar configuración de posts automáticos
    if callback_data == "admin_auto_post":
        if user_id != ADMIN_ID:
            await query.answer("Solo el administrador principal puede acceder a esta función.", show_alert=True)
            return
        
        keyboard = [
            [
                InlineKeyboardButton("➕ Nuevo Post", callback_data="create_auto_post"),
                InlineKeyboardButton("📋 Lista de Posts", callback_data="list_auto_posts")
            ],
            [
                InlineKeyboardButton("📊 Estadísticas", callback_data="post_stats"),
                InlineKeyboardButton("⚙️ Configuración", callback_data="post_config")
            ],
            [InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "<b>📅 Sistema de Publicación Automática</b>\n\n"
            "Administra los posts automáticos para tus canales. Puedes crear nuevos posts, "
            "ver los existentes, consultar estadísticas y configurar el sistema.",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
        return
    
    # Manejar creación de posts automáticos
    if callback_data == "create_auto_post":
        if user_id != ADMIN_ID:
            await query.answer("Solo el administrador principal puede crear posts.", show_alert=True)
            return
        
        # Iniciar el proceso de creación de post
        post_id = f"post_{int(time.time())}"
        context.user_data["creating_post"] = {
            "id": post_id,
            "text": None,
            "image": None,
            "buttons": [],
            "channels": [],
            "schedule": {
                "hour": 12,
                "minute": 0,
                "daily": False,
                "days": [0, 1, 2, 3, 4, 5, 6],  # Todos los días de la semana
                "duration": 24  # Horas que estará publicado
            }
        }
        
        keyboard = [
            [
                InlineKeyboardButton("📝 Añadir Texto", callback_data="post_add_text"),
                InlineKeyboardButton("🖼 Añadir Imagen", callback_data="post_add_image")
            ],
            [
                InlineKeyboardButton("🔗 Añadir Botones", callback_data="post_add_buttons"),
                InlineKeyboardButton("📊 Seleccionar Canales", callback_data="post_select_channels")
            ],
            [
                InlineKeyboardButton("⏰ Programar", callback_data="post_schedule"),
                InlineKeyboardButton("✅ Guardar Post", callback_data="post_save")
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data="admin_auto_post")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "<b>🆕 Crear Nuevo Post Automático</b>\n\n"
            "Configure las opciones del post:\n"
            "- Añada texto y/o imagen\n"
            "- Configure botones inline\n"
            "- Seleccione los canales donde publicar\n"
            "- Establezca horario de publicación\n\n"
            "<b>Estado actual:</b>\n"
            "Texto: ❌ No configurado\n"
            "Imagen: ❌ No configurada\n"
            "Botones: ❌ No configurados\n"
            "Canales: ❌ No seleccionados\n"
            "Horario: ⏰ Por defecto (12:00 PM, publicado 24h)",
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
            [InlineKeyboardButton("📣 Canales y Grupos 👥", callback_data="user_channels")],
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
            "/stats - Ver tus estadísticas\n"
            "/MisCanales - Ver tus canales añadidos\n\n"
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
            "/announce - Enviar anuncio al grupo\n\n"
            "<b>Comandos para Posts Automáticos:</b>\n"
            "/del - Elimina un canal de las categorías\n"
            "/edit - Edita un canal de las categorías\n"
            "/A - Añade un canal para publicación automática\n"
            "/E - Elimina un canal de publicación automática\n"
            "/List - Muestra lista de canales para publicación automática\n"
            "/V - Verifica permisos de bot en los canales"
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
        user_stats = db.get_user_stats(user_id, query.message.chat.id)
        warnings = db.get_warnings(user_id, query.message.chat.id)
        
        stats_message = (
            f"📊 <b>Estadísticas de {update.effective_user.first_name}</b>\n\n"
            f"Mensajes enviados: {user_stats['messages']}\n"
            f"Medios compartidos: {user_stats['media']}\n"
            f"Comandos utilizados: {user_stats['commands']}\n"
            f"Advertencias: {warnings['count']}/3\n"
            f"Última actividad: {user_stats['last_active'] if user_stats['last_active'] else 'Desconocida'}"
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
            
            keyboard.append([
                InlineKeyboardButton(
                    f"Ver: {submission['channel_name'][:20]}...", 
                    callback_data=f"view_submission_{submission_id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="admin_panel")])
        
        await query.edit_message_text(
            submissions_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
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
        db.delete_pending_submission(submission_id)
        
    except Exception as e:
        logger.error(f"Error sending rejection: {e}")
        await update.message.reply_text(
            f"❌ Error al enviar el rechazo: {e}"
        )
    
    # Limpiar
    del pending_submissions[submission_id]
    del admin_rejecting[user_id]

# Comandos para los posts automáticos
async def add_auto_post_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Añade un canal a la lista de publicación automática."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador principal puede usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Por favor, proporciona el ID del canal que deseas añadir.\n"
            "Ejemplo: /A -1001234567890"
        )
        return
    
    channel_id = context.args[0]
    
    # Verificar si ya existe en la lista
    channels = db.get_auto_post_channels()
    for channel in channels:
        if channel["channel_id"] == channel_id:
            await update.message.reply_text(f"El canal ya está en la lista de publicación automática.")
            return
    
    # Intentar obtener información del canal
    try:
        chat = await context.bot.get_chat(channel_id)
        
        # Guardar el canal en la base de datos
        if db.save_auto_post_channel(
            channel_id,
            chat.title,
            chat.username if chat.username else "",
            user_id
        ):
            await update.message.reply_text(
                f"✅ Canal añadido correctamente a la lista de publicación automática:\n\n"
                f"Nombre: {chat.title}\n"
                f"ID: {channel_id}\n"
                f"Username: {('@' + chat.username) if chat.username else 'No disponible'}"
            )
        else:
            await update.message.reply_text("❌ Error al guardar el canal en la base de datos.")
        
    except Exception as e:
        logger.error(f"Error adding auto post channel: {e}")
        await update.message.reply_text(
            f"❌ Error al añadir el canal: {str(e)}\n\n"
            "Verifica que el bot está añadido al canal y tiene los permisos necesarios."
        )

async def delete_auto_post_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elimina un canal de la lista de publicación automática."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador principal puede usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Por favor, proporciona el ID del canal que deseas eliminar.\n"
            "Ejemplo: /E -1001234567890\n"
            "Puedes ver la lista de canales con /List"
        )
        return
    
    channel_id = context.args[0]
    
    # Eliminar el canal
    if db.delete_auto_post_channel(channel_id):
        await update.message.reply_text(f"✅ Canal eliminado correctamente de la lista de publicación automática.")
    else:
        await update.message.reply_text("❌ No se encontró el canal en la lista o hubo un error al eliminarlo.")

async def list_auto_post_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la lista de canales para publicación automática."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador principal puede usar este comando.")
        return
    
    # Obtener la lista de canales
    channels = db.get_auto_post_channels()
    
    if not channels:
        await update.message.reply_text("No hay canales en la lista de publicación automática.")
        return
    
    # Construir el mensaje
    message = "<b>📋 Canales para Publicación Automática</b>\n\n"
    
    for i, channel in enumerate(channels, 1):
        message += (
            f"{i}. <b>{html.escape(channel['channel_name'])}</b>\n"
            f"   ID: <code>{channel['channel_id']}</code>\n"
            f"   Username: {('@' + channel['channel_username']) if channel['channel_username'] else 'No disponible'}\n"
            f"   Suscriptores: {channel.get('subscribers', 0)}\n\n"
        )
    
    await update.message.reply_html(message)

async def verify_auto_post_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifica los permisos del bot en los canales para publicación automática."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador principal puede usar este comando.")
        return
    
    # Obtener la lista de canales
    channels = db.get_auto_post_channels()
    
    if not channels:
        await update.message.reply_text("No hay canales en la lista de publicación automática.")
        return
    
    # Mensaje inicial
    status_message = await update.message.reply_text("Verificando canales... ⏳")
    
    # Verificar cada canal
    results = {
        "ok": [],
        "error": []
    }
    
    for channel in channels:
        try:
            # Verificar que el bot está en el canal
            chat_member = await context.bot.get_chat_member(channel["channel_id"], context.bot.id)
            
            # Verificar permisos necesarios
            required_permissions = [
                "can_post_messages",
                "can_edit_messages",
                "can_delete_messages",
                "can_invite_users"
            ]
            
            missing_permissions = []
            for permission in required_permissions:
                if not hasattr(chat_member, permission) or not getattr(chat_member, permission):
                    missing_permissions.append(permission.replace("can_", "").replace("_", " "))
            
            if missing_permissions:
                results["error"].append({
                    "channel": channel,
                    "error": f"Faltan permisos: {', '.join(missing_permissions)}"
                })
            else:
                # Obtener el número de suscriptores
                chat = await context.bot.get_chat(channel["channel_id"])
                subscribers = chat.members_count if hasattr(chat, "members_count") else 0
                
                # Actualizar el número de suscriptores en la base de datos
                db.update_channel_subscribers(channel["channel_id"], subscribers)
                
                results["ok"].append({
                    "channel": channel,
                    "subscribers": subscribers
                })
                
        except Exception as e:
            results["error"].append({
                "channel": channel,
                "error": str(e)
            })
    
    # Construir mensaje de resultados
    message = "<b>📋 Verificación de Canales</b>\n\n"
    
    if results["ok"]:
        message += "<b>✅ Canales verificados correctamente:</b>\n\n"
        
        for result in results["ok"]:
            message += (
                f"• <b>{html.escape(result['channel']['channel_name'])}</b>\n"
                f"  ID: <code>{result['channel']['channel_id']}</code>\n"
                f"  Suscriptores: {result['subscribers']}\n\n"
            )
    
    if results["error"]:
        message += "<b>❌ Canales con problemas:</b>\n\n"
        
        for result in results["error"]:
            message += (
                f"• <b>{html.escape(result['channel']['channel_name'])}</b>\n"
                f"  ID: <code>{result['channel']['channel_id']}</code>\n"
                f"  Error: {html.escape(result['error'])}\n\n"
            )
    
    await status_message.edit_text(message, parse_mode=ParseMode.HTML)

async def handle_channel_added(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja cuando añaden el bot a un canal."""
    # Verificar si es un evento de "bot añadido a un canal"
    if update.my_chat_member and update.my_chat_member.new_chat_member:
        # Verificar si el bot fue añadido a un canal o grupo
        if update.my_chat_member.chat.type in [ChatType.CHANNEL, ChatType.GROUP, ChatType.SUPERGROUP]:
            new_status = update.my_chat_member.new_chat_member.status
            old_status = update.my_chat_member.old_chat_member.status
            
            # Si el bot fue añadido (status cambió de left/kicked a otro)
            if old_status in ["left", "kicked"] and new_status not in ["left", "kicked"]:
                chat = update.my_chat_member.chat
                
                # Enviar mensaje al administrador
                keyboard = [
                    [InlineKeyboardButton("✅ Añadir a publicación automática", callback_data=f"add_auto_{chat.id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"<b>Bot añadido a:</b>\n\n"
                        f"<b>Nombre:</b> {html.escape(chat.title)}\n"
                        f"<b>ID:</b> <code>{chat.id}</code>\n"
                        f"<b>Enlace:</b> {('https://t.me/' + chat.username) if chat.username else 'No disponible'}\n\n"
                        f"<b>Permisos necesarios para el bot:</b>\n"
                        f"• Invitar con un enlace\n"
                        f"• Enviar mensajes\n"
                        f"• Editar mensajes\n"
                        f"• Eliminar mensajes"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )

async def mis_canales_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando para mostrar los canales del usuario."""
    user_id = update.effective_user.id
    
    # Llamar directamente a la función que maneja la lista de canales
    await handle_channel_list(update, context)

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
        db.update_user_stats(update.effective_user.id, update.effective_chat.id, "commands")

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
    warn_count = db.add_warning(target_user.id, chat_id, reason)
    
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
    db.update_user_stats(user_id, chat_id, "commands")

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
    warnings = db.get_warnings(target_user.id, chat_id)
    
    if warnings["count"] <= 0:
        await update.message.reply_text(f"El usuario {target_user.mention_html()} no tiene advertencias.", parse_mode=ParseMode.HTML)
        return
    
    # Restar una advertencia
    if db.add_warning(target_user.id, chat_id, "Advertencia removida") < 0:
        await update.message.reply_text("Error al quitar la advertencia.")
        return
    
    # Obtener nuevo conteo
    new_warnings = db.get_warnings(target_user.id, chat_id)
    
    await update.message.reply_html(
        f"✅ Se ha quitado una advertencia a {target_user.mention_html()}.\n"
        f"Advertencias actuales: {new_warnings['count']}/3"
    )
    
    # Actualizar estadísticas
    db.update_user_stats(user_id, chat_id, "commands")

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
    db.update_user_stats(user_id, chat_id, "commands")

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
    db.update_user_stats(user_id, chat_id, "commands")

# Manejadores de mensajes
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja todos los mensajes."""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Verificar si el mensaje es edición para un canal
    if user_id in user_editing_state and update.message and update.message.text:
        await handle_edit_input(update, context)
        return
        
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
            db.update_user_stats(user_id, chat_id, "media")
        else:
            db.update_user_stats(user_id, chat_id, "messages")
    
    # Actualizar última actividad
    user_last_activity[user_id] = datetime.now()

# Función para publicar posts programados
async def publish_scheduled_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Publica los posts programados."""
    job = context.job
    post_id = job.data["post_id"]
    
    # Obtener configuración del post
    post_config = db.get_post_config(post_id)
    if not post_config:
        logger.error(f"Post configuration not found for id: {post_id}")
        return
    
    # Obtener canales para publicar
    channels = db.get_auto_post_channels()
    if not channels:
        logger.error("No channels found for auto posting")
        return
    
    # Preparar mensaje
    text = post_config.get("text", "")
    image = post_config.get("image")
    buttons = post_config.get("buttons", [])
    
    # Preparar teclado si hay botones
    reply_markup = None
    if buttons:
        keyboard = []
        for button_row in buttons:
            row = []
            for button in button_row:
                if button.get("url"):
                    row.append(InlineKeyboardButton(button["text"], url=button["url"]))
                elif button.get("callback_data"):
                    row.append(InlineKeyboardButton(button["text"], callback_data=button["callback_data"]))
            if row:
                keyboard.append(row)
        
        if keyboard:
            reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Estadísticas de publicación
    publish_stats = {
        "success": 0,
        "failed": 0,
        "channels": []
    }
    
    # Publicar en cada canal
    for channel in channels:
        channel_id = channel["channel_id"]
        try:
            sent_message = None
            
            # Enviar mensaje según el contenido
            if image and text:
                # Mensaje con texto e imagen
                sent_message = await context.bot.send_photo(
                    chat_id=channel_id,
                    photo=image,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
            elif image:
                # Solo imagen
                sent_message = await context.bot.send_photo(
                    chat_id=channel_id,
                    photo=image,
                    reply_markup=reply_markup
                )
            else:
                # Solo texto
                sent_message = await context.bot.send_message(
                    chat_id=channel_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
            
            # Registrar estadísticas de éxito
            publish_stats["success"] += 1
            publish_stats["channels"].append({
                "channel_id": channel_id,
                "channel_name": channel["channel_name"],
                "status": "success",
                "message_id": sent_message.message_id if sent_message else None
            })
            
            # Actualizar estadísticas del post
            db.update_post_stats(
                post_id, 
                channel_id,
                "published",
                message_id=sent_message.message_id if sent_message else None
            )
            
        except Exception as e:
            logger.error(f"Error publishing post to channel {channel_id}: {e}")
            
            # Registrar estadísticas de error
            publish_stats["failed"] += 1
            publish_stats["channels"].append({
                "channel_id": channel_id,
                "channel_name": channel["channel_name"],
                "status": "failed",
                "error": str(e)
            })
            
            # Actualizar estadísticas del post
            db.update_post_stats(post_id, channel_id, "failed")
    
    # Enviar informe al administrador
    report_message = (
        f"<b>📊 Informe de Publicación Automática</b>\n\n"
        f"Post ID: <code>{post_id}</code>\n"
        f"Canales exitosos: {publish_stats['success']}\n"
        f"Canales fallidos: {publish_stats['failed']}\n\n"
    )
    
    if publish_stats["channels"]:
        report_message += "<b>Detalles:</b>\n\n"
        
        for channel_stat in publish_stats["channels"]:
            if channel_stat["status"] == "success":
                report_message += f"✅ {html.escape(channel_stat['channel_name'])}\n"
            else:
                report_message += f"❌ {html.escape(channel_stat['channel_name'])}: {html.escape(channel_stat['error'])}\n"
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=report_message,
        parse_mode=ParseMode.HTML
    )
    
    # Programar eliminación si es necesaria
    if post_config.get("schedule", {}).get("duration"):
        duration_hours = post_config["schedule"]["duration"]
        delete_time = datetime.now() + timedelta(hours=duration_hours)
        
        # Programar tarea para eliminar el post
        context.job_queue.run_once(
            delete_scheduled_post,
            delete_time,
            data={
                "post_id": post_id,
                "channels": publish_stats["channels"]
            },
            name=f"delete_post_{post_id}"
        )

async def delete_scheduled_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elimina los posts programados después de su duración."""
    job = context.job
    post_id = job.data["post_id"]
    channels = job.data["channels"]
    
    # Estadísticas de eliminación
    delete_stats = {
        "success": 0,
        "failed": 0,
        "channels": []
    }
    
    # Eliminar de cada canal
    for channel_info in channels:
        if channel_info["status"] != "success" or not channel_info.get("message_id"):
            continue
        
        channel_id = channel_info["channel_id"]
        message_id = channel_info["message_id"]
        
        try:
            # Eliminar mensaje
            await context.bot.delete_message(
                chat_id=channel_id,
                message_id=message_id
            )
            
            # Registrar estadísticas de éxito
            delete_stats["success"] += 1
            delete_stats["channels"].append({
                "channel_id": channel_id,
                "channel_name": channel_info["channel_name"],
                "status": "success"
            })
            
            # Actualizar estadísticas del post
            db.update_post_stats(
                post_id, 
                channel_id,
                "deleted",
                deleted_at=datetime.now().isoformat()
            )
            
        except Exception as e:
            logger.error(f"Error deleting post from channel {channel_id}: {e}")
            
            # Registrar estadísticas de error
            delete_stats["failed"] += 1
            delete_stats["channels"].append({
                "channel_id": channel_id,
                "channel_name": channel_info["channel_name"],
                "status": "failed",
                "error": str(e)
            })
    
    # Enviar informe al administrador
    report_message = (
        f"<b>🗑️ Informe de Eliminación Automática</b>\n\n"
        f"Post ID: <code>{post_id}</code>\n"
        f"Canales exitosos: {delete_stats['success']}\n"
        f"Canales fallidos: {delete_stats['failed']}\n\n"
    )
    
    if delete_stats["channels"]:
        report_message += "<b>Detalles:</b>\n\n"
        
        for channel_stat in delete_stats["channels"]:
            if channel_stat["status"] == "success":
                report_message += f"✅ {html.escape(channel_stat['channel_name'])}\n"
            else:
                report_message += f"❌ {html.escape(channel_stat['channel_name'])}: {html.escape(channel_stat['error'])}\n"
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=report_message,
        parse_mode=ParseMode.HTML
    )

# Función principal
def main() -> None:
    """Inicia el bot."""
    # Cargar configuración desde la base de datos
    load_config_from_db()
    
    # Crear la aplicación y pasarle el token del bot
    application = Application.builder().token(TOKEN).build()
    
    # Registrar manejador de errores
    application.add_error_handler(lambda update, context: logger.error(f"Error: {context.error} in update {update}"))
    
    # Comandos básicos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("categories", list_categories))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("MisCanales", mis_canales_command))
    
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
    
    # Comandos para posts automáticos
    application.add_handler(CommandHandler("A", add_auto_post_channel))
    application.add_handler(CommandHandler("E", delete_auto_post_channel))
    application.add_handler(CommandHandler("List", list_auto_post_channels))
    application.add_handler(CommandHandler("V", verify_auto_post_channels))
    
    # Dar bienvenida a nuevos miembros
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    
    # Manejar cuando el bot es añadido a un canal
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_channel_added))
    
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

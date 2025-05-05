import logging
import re
import html
import os
import time
import asyncio
import telegram
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

# Estado global para manejar la creación de posts automáticos
post_creation_state = {}

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
    
    # Sistema de post automáticos
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
    
    # Crear nuevo post automático
    if callback_data == "create_auto_post":
        await create_auto_post(update, context)
        return
    
    # Lista de posts automáticos
    if callback_data == "list_auto_posts":
        await list_auto_posts(update, context)
        return
    
    # Configuración de posts
    if callback_data.startswith("post_"):
        await handle_post_configuration(update, context)
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

    # Manejar otros callbacks que no estén definidos explícitamente
    await query.answer("Esta función aún no está implementada.", show_alert=True)

async def create_auto_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inicia el proceso de creación de un post automático."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.answer("Solo el administrador principal puede crear posts.", show_alert=True)
        return
    
    # Iniciar el proceso de creación de post
    post_id = f"post_{int(time.time())}"
    post_creation_state[user_id] = {
        "post_id": post_id,
        "text": "",
        "image": None,
        "buttons": [],
        "selected_channels": [],
        "schedule": {
            "hour": 12,
            "minute": 0,
            "daily": False,
            "days": [datetime.now().weekday()],  # Todos los días de la semana
            "duration": 24  # Horas que estará publicado
        },
        "current_step": "text"
    }
    
    # Mostrar opciones iniciales
    await show_post_creation_menu(query, user_id)

async def show_post_creation_menu(query, user_id):
    """Muestra el menú de creación de post."""
    state = post_creation_state[user_id]
    current_step = state["current_step"]
    
    # Construir el mensaje según el estado actual
    message = "<b>🆕 Crear Nuevo Post Automático</b>\n\n"
    
    # Mostrar resumen del post
    message += "<b>Estado actual:</b>\n"
    
    # Texto
    if state["text"]:
        message += f"✅ Texto: {len(state['text'])} caracteres\n"
    else:
        message += "❌ Texto: No configurado\n"
    
    # Imagen
    if state["image"]:
        message += "✅ Imagen: Configurada\n"
    else:
        message += "❌ Imagen: No configurada\n"
    
    # Botones
    if state["buttons"]:
        message += f"✅ Botones: {len(state['buttons'])} configurados\n"
    else:
        message += "❌ Botones: No configurados\n"
    
    # Canales
    if state["selected_channels"]:
        message += f"✅ Canales: {len(state['selected_channels'])} seleccionados\n"
    else:
        message += "❌ Canales: No seleccionados\n"
    
    # Programación
    schedule = state["schedule"]
    message += f"⏰ Horario: {schedule['hour']:02d}:{schedule['minute']:02d}, "
    message += f"{'Diario' if schedule['daily'] else 'Días selectos'}, "
    message += f"Duración: {schedule['duration']}h\n\n"
    
    # Instrucciones según el paso actual
    if current_step == "text":
        message += "Por favor, selecciona qué acción realizar a continuación:"
    elif current_step == "waiting_for_text":
        message += "Por favor, envía el texto que deseas incluir en el post."
    elif current_step == "waiting_for_image":
        message += "Por favor, envía la imagen que deseas incluir en el post."
    elif current_step == "channels":
        message += "Selecciona los canales donde se publicará el post."
    elif current_step == "schedule":
        message += "Configura el horario de publicación del post."
    
    # Crear teclado según el paso actual
    keyboard = []
    
    # Siempre mostrar acciones principales, a menos que esté esperando una entrada
    if not current_step.startswith("waiting_for_"):
        keyboard = [
            [
                InlineKeyboardButton("📝 Añadir/Editar Texto", callback_data="post_add_text"),
                InlineKeyboardButton("🖼 Añadir/Editar Imagen", callback_data="post_add_image")
            ],
            [
                InlineKeyboardButton("🔗 Añadir/Editar Botones", callback_data="post_add_buttons"),
                InlineKeyboardButton("📢 Seleccionar Canales", callback_data="post_select_channels")
            ],
            [
                InlineKeyboardButton("⏰ Programar Horario", callback_data="post_schedule"),
                InlineKeyboardButton("👁 Vista Previa", callback_data="post_preview")
            ],
            [
                InlineKeyboardButton("✅ Guardar Post", callback_data="post_save"),
                InlineKeyboardButton("❌ Cancelar", callback_data="admin_auto_post")
            ]
        ]
    else:
        # Si está esperando entrada, solo mostrar botón de cancelar
        keyboard = [
            [InlineKeyboardButton("❌ Cancelar", callback_data="post_cancel_input")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Enviar o editar el mensaje según corresponda
    await query.edit_message_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_post_configuration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja los callbacks relacionados con la configuración de posts."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.answer("Solo el administrador principal puede configurar posts.", show_alert=True)
        return
    
    callback_data = query.data
    
    # Verificar si hay un proceso de creación activo
    if user_id not in post_creation_state and not callback_data == "create_auto_post":
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        await query.edit_message_text(
            "El proceso de creación de post ha expirado. Inicia uno nuevo.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Nuevo Post", callback_data="create_auto_post")],
                [InlineKeyboardButton("🔙 Volver", callback_data="admin_auto_post")]
            ])
        )
        return
    
    # Manejar según el callback específico
    if callback_data == "post_add_text":
        post_creation_state[user_id]["current_step"] = "waiting_for_text"
        await show_post_creation_menu(query, user_id)
        await query.answer("Envía el texto para el post")
        return
    
    elif callback_data == "post_add_image":
        post_creation_state[user_id]["current_step"] = "waiting_for_image"
        await show_post_creation_menu(query, user_id)
        await query.answer("Envía la imagen para el post")
        return
    
    elif callback_data == "post_cancel_input":
        # Cancelar la espera de entrada
        post_creation_state[user_id]["current_step"] = "text"  # Volver al menú principal
        await show_post_creation_menu(query, user_id)
        return
    
    elif callback_data == "post_add_buttons":
        await handle_post_buttons(update, context)
        return
    
    elif callback_data == "post_select_channels":
        await select_post_channels(update, context)
        return
    
    elif callback_data == "post_schedule":
        await configure_post_schedule(update, context)
        return
    
    elif callback_data == "post_preview":
        await preview_post(update, context)
        return
    
    elif callback_data == "post_save":
        await save_post(update, context)
        return
    
    # Manejar callbacks específicos para botones
    elif callback_data.startswith("post_btn_"):
        await handle_button_actions(update, context)
        return
    
    # Manejar callbacks específicos para canales
    elif callback_data.startswith("post_chan_"):
        await handle_channel_selection(update, context)
        return
    
    # Manejar callbacks específicos para programación
    elif callback_data.startswith("post_sched_"):
        await handle_schedule_setting(update, context)
        return
    
    await query.answer("Esta función aún no está implementada.", show_alert=True)

async def handle_text_input_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enruta las entradas de texto a la función adecuada según el estado del usuario."""
    user_id = update.effective_user.id
    
    # Manejar textos para posts
    if user_id in post_creation_state:
        state = post_creation_state[user_id]
        current_step = state.get("current_step", "")
        
        if current_step == "waiting_for_text":
            await process_post_text(update, context)
            return
        elif current_step == "waiting_for_button_text" or current_step == "waiting_for_button_url" or current_step == "waiting_for_button_callback" or current_step == "waiting_for_edit_button_text":
            await process_button_input(update, context)
            return
    
    # Manejar textos para editar canales
    if user_id in user_editing_state:
        await handle_edit_input(update, context)
        return
    
    # Manejar motivos de rechazo del administrador
    if user_id == ADMIN_ID and user_id in admin_rejecting:
        await handle_rejection_reason(update, context)
        return
    
    # Manejar otras entradas de texto
    await process_channel_submission(update, context)

async def edit_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /edit para editar un canal."""
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador principal puede usar este comando.")
        return
    
    # Verificar argumentos
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "Por favor, proporciona el ID del canal que deseas editar.\n"
            "Ejemplo: /edit -1001234567890"
        )
        return
    
    channel_id = context.args[0]
    
    # Buscar información del canal
    channels = db.get_approved_channels()
    target_channel = None
    
    for channel in channels:
        if channel["channel_id"] == channel_id:
            target_channel = channel
            break
    
    if not target_channel:
        await update.message.reply_text("Canal no encontrado.")
        return
    
    # Mostrar opciones de edición
    keyboard = [
        [
            InlineKeyboardButton("📝 Cambiar Nombre", callback_data=f"change_name_{channel_id}"),
            InlineKeyboardButton("📝 Modificar Enlace", callback_data=f"change_link_{channel_id}")
        ],
        [InlineKeyboardButton("🗑️ Eliminar Canal", callback_data=f"delete_channel_{channel_id}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        f"✏️ Editar Canal\n\n"
        f"🏷 {target_channel['channel_name']}\n"
        f"🆔 {target_channel['channel_id']}\n"
        f"🔗 https://t.me/{target_channel['channel_username']}\n"
        f"📂 Categoría: {target_channel['category']}\n"
    )
    
    await update.message.reply_text(message, reply_markup=reply_markup)

async def process_post_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa el texto enviado para el post."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        return
    
    state = post_creation_state[user_id]
    
    if state["current_step"] != "waiting_for_text":
        return
    
    # Guardar el texto
    state["text"] = update.message.text
    state["current_step"] = "text"  # Volver al menú principal
    
    # Enviar confirmación
    await update.message.reply_text("✅ Texto guardado correctamente.")
    
    # Enviar menú actualizado
    keyboard = [
        [
            InlineKeyboardButton("📝 Añadir/Editar Texto", callback_data="post_add_text"),
            InlineKeyboardButton("🖼 Añadir/Editar Imagen", callback_data="post_add_image")
        ],
        [
            InlineKeyboardButton("🔗 Añadir/Editar Botones", callback_data="post_add_buttons"),
            InlineKeyboardButton("📢 Seleccionar Canales", callback_data="post_select_channels")
        ],
        [
            InlineKeyboardButton("⏰ Programar Horario", callback_data="post_schedule"),
            InlineKeyboardButton("👁 Vista Previa", callback_data="post_preview")
        ],
        [
            InlineKeyboardButton("✅ Guardar Post", callback_data="post_save"),
            InlineKeyboardButton("❌ Cancelar", callback_data="admin_auto_post")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = "<b>🆕 Crear Nuevo Post Automático</b>\n\n"
    message += "<b>Estado actual:</b>\n"
    message += f"✅ Texto: {len(state['text'])} caracteres\n"
    
    # Añadir información sobre otros componentes
    if state["image"]:
        message += "✅ Imagen: Configurada\n"
    else:
        message += "❌ Imagen: No configurada\n"
    
    if state["buttons"]:
        message += f"✅ Botones: {len(state['buttons'])} configurados\n"
    else:
        message += "❌ Botones: No configurados\n"
    
    if state["selected_channels"]:
        message += f"✅ Canales: {len(state['selected_channels'])} seleccionados\n"
    else:
        message += "❌ Canales: No seleccionados\n"
    
    await context.bot.send_message(
        chat_id=user_id,
        text=message,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def process_post_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa la imagen enviada para el post."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        return
    
    state = post_creation_state[user_id]
    
    if state["current_step"] != "waiting_for_image":
        return
    
    # Verificar si hay una imagen
    if not update.message.photo:
        await update.message.reply_text("❌ Por favor, envía una imagen válida.")
        return
    
    # Guardar la imagen (el último elemento es la versión de mayor resolución)
    state["image"] = update.message.photo[-1].file_id
    state["current_step"] = "text"  # Volver al menú principal
    
    # Enviar confirmación
    await update.message.reply_text("✅ Imagen guardada correctamente.")
    
    # Enviar menú actualizado
    await context.bot.send_message(
        chat_id=user_id,
        text="<b>🆕 Crear Nuevo Post Automático</b>\n\n"
             "<b>Estado actual:</b>\n"
             f"{'✅ Texto: ' + str(len(state['text'])) + ' caracteres' if state['text'] else '❌ Texto: No configurado'}\n"
             "✅ Imagen: Configurada\n"
             f"{'✅ Botones: ' + str(len(state['buttons'])) + ' configurados' if state['buttons'] else '❌ Botones: No configurados'}\n"
             f"{'✅ Canales: ' + str(len(state['selected_channels'])) + ' seleccionados' if state['selected_channels'] else '❌ Canales: No seleccionados'}\n",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 Añadir/Editar Texto", callback_data="post_add_text"),
                InlineKeyboardButton("🖼 Añadir/Editar Imagen", callback_data="post_add_image")
            ],
            [
                InlineKeyboardButton("🔗 Añadir/Editar Botones", callback_data="post_add_buttons"),
                InlineKeyboardButton("📢 Seleccionar Canales", callback_data="post_select_channels")
            ],
            [
                InlineKeyboardButton("⏰ Programar Horario", callback_data="post_schedule"),
                InlineKeyboardButton("👁 Vista Previa", callback_data="post_preview")
            ],
            [
                InlineKeyboardButton("✅ Guardar Post", callback_data="post_save"),
                InlineKeyboardButton("❌ Cancelar", callback_data="admin_auto_post")
            ]
        ])
    )

async def handle_post_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la configuración de botones para el post."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    
    # Mostrar los botones actuales y opciones para añadir/editar
    message = "<b>🔗 Configuración de Botones</b>\n\n"
    
    if not state["buttons"]:
        message += "No hay botones configurados aún.\n\n"
    else:
        message += "<b>Botones actuales:</b>\n\n"
        for i, btn in enumerate(state["buttons"], 1):
            if "url" in btn:
                message += f"{i}. {btn['text']} -> {btn['url']}\n"
            elif "callback_data" in btn:
                message += f"{i}. {btn['text']} -> Callback: {btn['callback_data']}\n"
    
    message += "\nSelecciona una opción:"
    
    # Crear teclado con opciones
    keyboard = [
        [
            InlineKeyboardButton("➕ Añadir Botón URL", callback_data="post_btn_add_url"),
            InlineKeyboardButton("➕ Añadir Botón Callback", callback_data="post_btn_add_cb")
        ],
        [
            InlineKeyboardButton("✏️ Editar Botón", callback_data="post_btn_edit"),
            InlineKeyboardButton("🗑️ Eliminar Botón", callback_data="post_btn_delete")
        ],
        [InlineKeyboardButton("🔙 Volver", callback_data="post_btn_back")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def select_post_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la lista de canales para seleccionar."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    
    try:
        # Obtener canales disponibles para publicación automática
        channels = db.get_auto_post_channels()
        
        if not channels:
            await query.edit_message_text(
                "<b>📢 Selección de Canales</b>\n\n"
                "No hay canales configurados para publicación automática.\n\n"
                "Utiliza el comando /A para añadir canales.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="post_cancel_input")]
                ])
            )
            return
        
        # Preparar mensaje y teclado
        message = "<b>📢 Selección de Canales</b>\n\n"
        message += "Selecciona los canales donde deseas publicar este post:\n\n"
        
        # Obtener IDs de canales seleccionados
        selected_ids = [ch['channel_id'] for ch in state["selected_channels"]]
        
        # Crear teclado con canales
        keyboard = []
        for channel in channels:
            is_selected = channel['channel_id'] in selected_ids
            prefix = "✅" if is_selected else "❌"
            keyboard.append([InlineKeyboardButton(
                f"{prefix} {channel['channel_name']}",
                callback_data=f"post_chan_toggle_{channel['channel_id']}"
            )])
        
        # Añadir botones de acción
        keyboard.append([
            InlineKeyboardButton("✅ Seleccionar Todos", callback_data="post_chan_select_all"),
            InlineKeyboardButton("❌ Deseleccionar Todos", callback_data="post_chan_deselect_all")
        ])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_cancel_input")])
        
        # Intentar actualizar el mensaje
        try:
            await query.edit_message_text(
                message,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except telegram.error.BadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
            await query.answer("Lista de canales actualizada")
            
    except Exception as e:
        logger.error(f"Error en select_post_channels: {e}")
        await query.answer("Error al mostrar los canales", show_alert=True)

async def handle_channel_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja las acciones de selección de canales."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    callback_data = query.data
    
    try:
        # Toggle de canal individual
        if callback_data.startswith("post_chan_toggle_"):
            channel_id = callback_data[16:]
            selected_ids = [ch['channel_id'] for ch in state["selected_channels"]]
            changed = False
            
            if channel_id in selected_ids:
                # Deseleccionar canal
                state["selected_channels"] = [ch for ch in state["selected_channels"] if ch['channel_id'] != channel_id]
                changed = True
                await query.answer("Canal deseleccionado")
            else:
                # Buscar el canal en la lista completa
                all_channels = db.get_auto_post_channels()
                target_channel = next((ch for ch in all_channels if ch['channel_id'] == channel_id), None)
                
                if target_channel:
                    state["selected_channels"].append(target_channel)
                    changed = True
                    await query.answer("Canal seleccionado")
            
            if changed:
                await select_post_channels(update, context)
        
        # Seleccionar todos los canales
        elif callback_data == "post_chan_select_all":
            old_count = len(state["selected_channels"])
            state["selected_channels"] = db.get_auto_post_channels()
            
            if len(state["selected_channels"]) != old_count:
                await select_post_channels(update, context)
            await query.answer("Todos los canales seleccionados")
        
        # Deseleccionar todos los canales
        elif callback_data == "post_chan_deselect_all":
            if state["selected_channels"]:
                state["selected_channels"] = []
                await select_post_channels(update, context)
            await query.answer("Todos los canales deseleccionados")
        
        # Volver al menú principal
        elif callback_data == "post_cancel_input":
            state["current_step"] = "text"
            await show_post_creation_menu(query, user_id)
        
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error en handle_channel_selection: {e}")
            await query.answer("Error al procesar la selección", show_alert=True)
    except Exception as e:
        logger.error(f"Error inesperado en handle_channel_selection: {e}")
        await query.answer("Error al procesar la selección", show_alert=True)

async def handle_channel_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja las acciones de selección de canales."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    callback_data = query.data
    
    try:
        # Toggle de canal individual
        if callback_data.startswith("post_chan_toggle_"):
            channel_id = callback_data[16:]
            selected_ids = [ch['channel_id'] for ch in state["selected_channels"]]
            
            # Buscar el canal en la lista completa
            all_channels = db.get_auto_post_channels()
            target_channel = next((ch for ch in all_channels if ch['channel_id'] == channel_id), None)
            
            if channel_id in selected_ids:
                # Deseleccionar canal
                state["selected_channels"] = [ch for ch in state["selected_channels"] if ch['channel_id'] != channel_id]
                await query.answer("Canal deseleccionado")
            elif target_channel:
                # Seleccionar canal
                state["selected_channels"].append(target_channel)
                await query.answer("Canal seleccionado")
            
            await select_post_channels(update, context)
        
        # Seleccionar todos los canales
        elif callback_data == "post_chan_select_all":
            state["selected_channels"] = db.get_auto_post_channels()
            await query.answer("Todos los canales seleccionados")
            await select_post_channels(update, context)
        
        # Deseleccionar todos los canales
        elif callback_data == "post_chan_deselect_all":
            state["selected_channels"] = []
            await query.answer("Todos los canales deseleccionados")
            await select_post_channels(update, context)
        
        # Cancelar selección
        elif callback_data == "post_cancel_input":
            state["current_step"] = "text"
            await show_post_creation_menu(query, user_id)
        
    except Exception as e:
        logger.error(f"Error en handle_channel_selection: {e}")
        if "message is not modified" not in str(e):
            await query.answer("Error al procesar la selección", show_alert=True)

async def process_button_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa la entrada de texto para botones."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        return
    
    state = post_creation_state[user_id]
    current_step = state["current_step"]
    
    if current_step == "waiting_for_button_text":
        # Guardar el texto del botón y solicitar URL o callback data
        state["temp_button_text"] = update.message.text
        state["current_step"] = "waiting_for_button_url" if state["button_type"] == "url" else "waiting_for_button_callback"
        
        await update.message.reply_text(
            "Por favor, envía el " + 
            ("enlace (URL)" if state["button_type"] == "url" else "callback data") +
            " para el botón:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancelar", callback_data="post_btn_cancel")
            ]])
        )
        
    elif current_step == "waiting_for_button_url":
        # Validar y guardar URL
        url = update.message.text
        if not url.startswith(('http://', 'https://', 't.me/')):
            await update.message.reply_text(
                "❌ URL inválida. Debe comenzar con http://, https:// o t.me/\n"
                "Por favor, intenta nuevamente:"
            )
            return
        
        # Crear y guardar el botón
        new_button = {
            "text": state["temp_button_text"],
            "url": url
        }
        state["buttons"].append(new_button)
        
        # Limpiar estado temporal
        state["current_step"] = "text"
        del state["temp_button_text"]
        del state["button_type"]
        
        # Confirmar y mostrar menú de botones
        await update.message.reply_text("✅ Botón añadido correctamente.")
        await show_post_creation_menu(update.message, user_id)
        
    elif current_step == "waiting_for_button_callback":
        # Validar y guardar callback data
        callback_data = update.message.text
        if len(callback_data) > 64:
            await update.message.reply_text(
                "❌ Callback data demasiado largo. Máximo 64 caracteres.\n"
                "Por favor, intenta nuevamente:"
            )
            return
        
        # Crear y guardar el botón
        new_button = {
            "text": state["temp_button_text"],
            "callback_data": callback_data
        }
        state["buttons"].append(new_button)
        
        # Limpiar estado temporal
        state["current_step"] = "text"
        del state["temp_button_text"]
        del state["button_type"]
        
        # Confirmar y mostrar menú de botones
        await update.message.reply_text("✅ Botón añadido correctamente.")
        await show_post_creation_menu(update.message, user_id)
        
    elif current_step == "waiting_for_edit_button_text":
        # Editar texto de un botón existente
        button_index = state.get("editing_button_index")
        if button_index is not None and 0 <= button_index < len(state["buttons"]):
            state["buttons"][button_index]["text"] = update.message.text
            
            # Limpiar estado de edición
            state["current_step"] = "text"
            del state["editing_button_index"]
            
            # Confirmar y mostrar menú de botones
            await update.message.reply_text("✅ Texto del botón actualizado.")
            await show_post_creation_menu(update.message, user_id)

async def configure_post_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Configura la programación del post."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        try:
            await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        except telegram.error.BadRequest:
            pass
        return
    
    state = post_creation_state[user_id]
    schedule = state["schedule"]
    
    try:
        # Preparar mensaje
        message = "<b>⏰ Programación del Post</b>\n\n"
        message += f"Hora: <b>{schedule['hour']:02d}:{schedule['minute']:02d}</b>\n"
        
        if schedule['daily']:
            message += "Frecuencia: <b>Diario</b>\n"
        else:
            days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
            selected_days = [days[i] for i in schedule['days']]
            message += f"Días: <b>{', '.join(selected_days)}</b>\n"
        
        message += f"Duración: <b>{schedule['duration']} horas</b>\n\n"
        message += "Configura cuándo se publicará el post y por cuánto tiempo."
        
        # Crear teclado con opciones
        keyboard = [
            [
                InlineKeyboardButton("🕒 Cambiar Hora", callback_data="post_sched_hour"),
                InlineKeyboardButton("🕐 Cambiar Minutos", callback_data="post_sched_minute")
            ],
            [
                InlineKeyboardButton(
                    "📅 Modo: " + ("Diario" if schedule['daily'] else "Días específicos"), 
                    callback_data="post_sched_toggle_daily"
                )
            ],
            [
                InlineKeyboardButton("📆 Seleccionar Días", callback_data="post_sched_days"),
                InlineKeyboardButton("⏱️ Duración", callback_data="post_sched_duration")
            ],
            # Usar el callback_data correcto para volver al menú principal
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_menu")]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer()
        
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error en configure_post_schedule: {e}")
            try:
                await query.answer("Error al mostrar el menú", show_alert=True)
            except:
                pass
            
async def handle_schedule_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str, schedule: dict) -> None:
    """Maneja las acciones específicas del horario."""
    query = update.callback_query
    
    try:
        # Configuración de hora específica
        if callback_data.startswith("post_sched_set_hour_"):
            hour = int(callback_data.split("_")[-1])
            if 0 <= hour < 24:
                schedule['hour'] = hour
                await query.answer(f"Hora configurada: {hour:02d}:00")
                await configure_post_schedule(update, context)
            else:
                await query.answer("Hora inválida", show_alert=True)
                
        # Configuración de minutos específicos
        elif callback_data.startswith("post_sched_set_minute_"):
            minute = int(callback_data.split("_")[-1])
            if minute in [0, 15, 30, 45]:
                schedule['minute'] = minute
                await query.answer(f"Minutos configurados: {minute:02d}")
                await configure_post_schedule(update, context)
            else:
                await query.answer("Minutos inválidos", show_alert=True)
                
        # Toggle de modo diario
        elif callback_data == "post_sched_toggle_daily":
            schedule['daily'] = not schedule['daily']
            await query.answer(f"Modo {'diario' if schedule['daily'] else 'días específicos'} activado")
            await configure_post_schedule(update, context)
            
        # Toggle de días específicos
        elif callback_data.startswith("post_sched_toggle_day_"):
            day_index = int(callback_data.split("_")[-1])
            if 0 <= day_index <= 6:
                if day_index in schedule['days']:
                    schedule['days'].remove(day_index)
                else:
                    schedule['days'].append(day_index)
                
                if not schedule['days']:
                    schedule['days'].append(datetime.now().weekday())
                
                schedule['days'].sort()
                await query.answer("Día actualizado")
                await handle_schedule_setting(update, context)
            else:
                await query.answer("Día inválido", show_alert=True)
                
        # Configuración de duración
        elif callback_data.startswith("post_sched_set_duration_"):
            duration = int(callback_data.split("_")[-1])
            if duration in [6, 12, 24, 48, 72]:
                schedule['duration'] = duration
                await query.answer(f"Duración configurada: {duration} horas")
                await configure_post_schedule(update, context)
            else:
                await query.answer("Duración inválida", show_alert=True)
                
    except ValueError:
        await query.answer("Valor inválido", show_alert=True)
    except Exception as e:
        logger.error(f"Error en handle_schedule_actions: {e}")
        await query.answer("Error al procesar la acción", show_alert=True)

async def handle_button_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja las acciones relacionadas con los botones del post."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    callback_data = query.data
    
    # Añadir botón con URL
    if callback_data == "post_btn_add_url":
        state["current_step"] = "waiting_for_button_text"
        state["button_type"] = "url"
        await query.edit_message_text(
            "Por favor, envía el texto que deseas mostrar en el botón:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancelar", callback_data="post_btn_cancel")
            ]])
        )
    
    # Añadir botón con callback
    elif callback_data == "post_btn_add_cb":
        state["current_step"] = "waiting_for_button_text"
        state["button_type"] = "callback"
        await query.edit_message_text(
            "Por favor, envía el texto que deseas mostrar en el botón:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancelar", callback_data="post_btn_cancel")
            ]])
        )
    
    # Editar botón existente
    elif callback_data == "post_btn_edit":
        if not state["buttons"]:
            await query.answer("No hay botones para editar.", show_alert=True)
            return
        
        keyboard = []
        for i, button in enumerate(state["buttons"]):
            keyboard.append([InlineKeyboardButton(
                f"Editar: {button['text']}", 
                callback_data=f"post_btn_edit_{i}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_btn_back")])
        await query.edit_message_text(
            "Selecciona el botón que deseas editar:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # Eliminar botón
    elif callback_data == "post_btn_delete":
        if not state["buttons"]:
            await query.answer("No hay botones para eliminar.", show_alert=True)
            return
        
        keyboard = []
        for i, button in enumerate(state["buttons"]):
            keyboard.append([InlineKeyboardButton(
                f"Eliminar: {button['text']}", 
                callback_data=f"post_btn_delete_{i}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_btn_back")])
        await query.edit_message_text(
            "Selecciona el botón que deseas eliminar:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # Confirmar eliminación de botón
    elif callback_data.startswith("post_btn_delete_"):
        try:
            button_index = int(callback_data.split("_")[-1])
            if 0 <= button_index < len(state["buttons"]):
                deleted_button = state["buttons"].pop(button_index)
                await query.answer(f"Botón '{deleted_button['text']}' eliminado.")
                await handle_post_buttons(update, context)
            else:
                await query.answer("Índice de botón inválido.", show_alert=True)
        except (ValueError, IndexError):
            await query.answer("Error al eliminar el botón.", show_alert=True)
    
    # Iniciar edición de botón específico
    elif callback_data.startswith("post_btn_edit_"):
        try:
            button_index = int(callback_data.split("_")[-1])
            if 0 <= button_index < len(state["buttons"]):
                state["current_step"] = "waiting_for_edit_button_text"
                state["editing_button_index"] = button_index
                await query.edit_message_text(
                    "Por favor, envía el nuevo texto para el botón:",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancelar", callback_data="post_btn_cancel")
                    ]])
                )
            else:
                await query.answer("Índice de botón inválido.", show_alert=True)
        except (ValueError, IndexError):
            await query.answer("Error al editar el botón.", show_alert=True)
    
    # Cancelar acción de botones
    elif callback_data == "post_btn_cancel":
        state["current_step"] = "text"
        await handle_post_buttons(update, context)
    
    # Volver al menú de botones
    elif callback_data == "post_btn_back":
        await handle_post_buttons(update, context)
    
    else:
        await query.answer("Acción no reconocida.", show_alert=True)

async def handle_schedule_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la configuración de horarios del post."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        try:
            await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        except telegram.error.BadRequest:
            pass
        return
    
    state = post_creation_state[user_id]
    schedule = state["schedule"]
    callback_data = query.data
    
    try:
        if callback_data == "post_sched_days":
            await show_days_selector(update, context)
            return
            
        elif callback_data.startswith("post_sched_toggle_day_"):
            await toggle_day_selection(update, context)
            return
            
        elif callback_data == "post_sched_hour":
            await show_hour_selector(update, context)
            return
            
        elif callback_data == "post_sched_minute":
            await show_minute_selector(update, context)
            return
            
        elif callback_data == "post_sched_duration":
            await show_duration_selector(update, context)
            return
            
        elif callback_data == "post_sched":
            # Volver desde cualquier submenú al menú principal de programación
            await configure_post_schedule(update, context)
            return
            
        elif callback_data == "post_cancel_input" or callback_data == "back_to_menu":
            # Volver al menú principal de creación de post
            await return_to_main_menu(update, context)
            return
            
        # Manejar otras acciones específicas
        await handle_specific_actions(update, context, callback_data)
        
    except Exception as e:
        logger.error(f"Error en handle_schedule_setting: {e}")
        try:
            await query.answer("Error al procesar la solicitud", show_alert=True)
        except:
            pass

async def return_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vuelve al menú principal de creación de post."""
    query = update.callback_query
    user_id = query.from_user.id
    state = post_creation_state[user_id]
    
    try:
        # Actualizar estado
        state["current_step"] = "text"
        
        # Preparar mensaje del menú principal
        message = "<b>🆕 Crear Nuevo Post Automático</b>\n\n"
        message += "<b>Estado actual:</b>\n"
        message += f"{'✅ Texto: ' + str(len(state['text'])) + ' caracteres' if state['text'] else '❌ Texto: No configurado'}\n"
        message += f"{'✅ Imagen: Configurada' if state['image'] else '❌ Imagen: No configurada'}\n"
        message += f"{'✅ Botones: ' + str(len(state['buttons'])) + ' configurados' if state['buttons'] else '❌ Botones: No configurados'}\n"
        message += f"{'✅ Canales: ' + str(len(state['selected_channels'])) + ' seleccionados' if state['selected_channels'] else '❌ Canales: No seleccionados'}\n"
        
        # Crear teclado del menú principal
        keyboard = [
            [
                InlineKeyboardButton("📝 Añadir/Editar Texto", callback_data="post_add_text"),
                InlineKeyboardButton("🖼 Añadir/Editar Imagen", callback_data="post_add_image")
            ],
            [
                InlineKeyboardButton("🔗 Añadir/Editar Botones", callback_data="post_add_buttons"),
                InlineKeyboardButton("📢 Seleccionar Canales", callback_data="post_select_channels")
            ],
            [
                InlineKeyboardButton("⏰ Programar Horario", callback_data="post_schedule"),
                InlineKeyboardButton("👁 Vista Previa", callback_data="post_preview")
            ],
            [
                InlineKeyboardButton("✅ Guardar Post", callback_data="post_save"),
                InlineKeyboardButton("❌ Cancelar", callback_data="admin_auto_post")
            ]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer("Volviendo al menú principal")
        
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error volviendo al menú principal: {e}")
        try:
            await query.answer()
        except:
            pass

async def show_days_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el selector de días."""
    query = update.callback_query
    state = post_creation_state[query.from_user.id]
    schedule = state["schedule"]
    
    days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    keyboard = []
    
    # Crear botones para cada día
    for i, day in enumerate(days):
        is_selected = i in schedule['days']
        prefix = "✅" if is_selected else "❌"
        keyboard.append([InlineKeyboardButton(
            f"{prefix} {day}", 
            callback_data=f"post_sched_toggle_day_{i}"
        )])
    
    # Añadir botón volver con callback_data específico
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_sched")])
    
    try:
        await query.edit_message_text(
            "<b>📆 Selecciona los días para publicar el post</b>\n\n"
            "Marca los días en que se publicará el post:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer()
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error mostrando selector de días: {e}")

async def toggle_day_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja la selección/deselección de días."""
    query = update.callback_query
    user_id = query.from_user.id
    state = post_creation_state[user_id]
    schedule = state["schedule"]
    
    try:
        # Extraer índice del día
        day_index = int(query.data.split("_")[-1])
        if 0 <= day_index <= 6:
            # Toggle día
            if day_index in schedule['days']:
                schedule['days'].remove(day_index)
                action = "deseleccionado"
            else:
                schedule['days'].append(day_index)
                action = "seleccionado"
            
            # Asegurar que hay al menos un día seleccionado
            if not schedule['days']:
                schedule['days'].append(datetime.now().weekday())
            schedule['days'].sort()
            
            # Mostrar selector actualizado
            await show_days_selector(update, context)
            await query.answer(f"Día {action}")
        else:
            await query.answer("Día inválido", show_alert=True)
    except ValueError:
        await query.answer("Error en la selección", show_alert=True)
    except Exception as e:
        logger.error(f"Error en toggle_day_selection: {e}")
        await query.answer("Error al procesar la selección", show_alert=True)

async def show_hour_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el selector de hora."""
    query = update.callback_query
    state = post_creation_state[query.from_user.id]
    schedule = state["schedule"]
    
    keyboard = []
    row = []
    
    for hour in range(24):
        btn = InlineKeyboardButton(
            f"{hour:02d}" + ("✓" if hour == schedule['hour'] else ""), 
            callback_data=f"post_sched_set_hour_{hour}"
        )
        row.append(btn)
        
        if (hour + 1) % 6 == 0:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_sched")])
    
    try:
        await query.edit_message_text(
            "<b>⏰ Selecciona la hora para el post</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer()
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error mostrando selector de hora: {e}")

async def show_minute_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el selector de minutos."""
    query = update.callback_query
    state = post_creation_state[query.from_user.id]
    schedule = state["schedule"]
    
    keyboard = []
    row = []
    
    for minute in [0, 15, 30, 45]:
        btn = InlineKeyboardButton(
            f"{minute:02d}" + ("✓" if minute == schedule['minute'] else ""), 
            callback_data=f"post_sched_set_minute_{minute}"
        )
        row.append(btn)
    
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_sched")])
    
    try:
        await query.edit_message_text(
            "<b>⏰ Selecciona los minutos para el post</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer()
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error mostrando selector de minutos: {e}")

async def show_duration_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el selector de duración."""
    query = update.callback_query
    state = post_creation_state[query.from_user.id]
    schedule = state["schedule"]
    
    durations = [6, 12, 24, 48, 72]
    keyboard = []
    row = []
    
    for duration in durations:
        btn = InlineKeyboardButton(
            f"{duration}h" + ("✓" if duration == schedule['duration'] else ""), 
            callback_data=f"post_sched_set_duration_{duration}"
        )
        row.append(btn)
        
        if len(row) == 3:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="post_sched")])
    
    try:
        await query.edit_message_text(
            "<b>⏱️ Selecciona la duración del post</b>\n\n"
            "¿Durante cuántas horas estará publicado el post?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.answer()
    except telegram.error.BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error mostrando selector de duración: {e}")

async def handle_specific_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    """Maneja acciones específicas como establecer hora, minutos, duración, etc."""
    query = update.callback_query
    user_id = query.from_user.id
    state = post_creation_state[user_id]
    schedule = state["schedule"]
    
    try:
        if callback_data.startswith("post_sched_set_hour_"):
            # Configurar hora
            hour = int(callback_data.split("_")[-1])
            if 0 <= hour < 24:
                schedule['hour'] = hour
                await query.answer(f"Hora configurada: {hour:02d}:00")
                await configure_post_schedule(update, context)
            else:
                await query.answer("Hora inválida", show_alert=True)
                
        elif callback_data.startswith("post_sched_set_minute_"):
            # Configurar minutos
            minute = int(callback_data.split("_")[-1])
            if minute in [0, 15, 30, 45]:
                schedule['minute'] = minute
                await query.answer(f"Minutos configurados: {minute:02d}")
                await configure_post_schedule(update, context)
            else:
                await query.answer("Minutos inválidos", show_alert=True)
                
        elif callback_data.startswith("post_sched_set_duration_"):
            # Configurar duración
            duration = int(callback_data.split("_")[-1])
            if duration in [6, 12, 24, 48, 72]:
                schedule['duration'] = duration
                await query.answer(f"Duración configurada: {duration} horas")
                await configure_post_schedule(update, context)
            else:
                await query.answer("Duración inválida", show_alert=True)
                
        elif callback_data == "post_sched_toggle_daily":
            # Toggle modo diario
            schedule['daily'] = not schedule['daily']
            await query.answer(f"Modo {'diario' if schedule['daily'] else 'días específicos'} activado")
            await configure_post_schedule(update, context)
            
    except ValueError:
        await query.answer("Valor inválido", show_alert=True)
    except Exception as e:
        logger.error(f"Error en handle_specific_actions: {e}")
        await query.answer("Error al procesar la acción", show_alert=True)

async def preview_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra una vista previa del post."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    
    # Verificar si hay contenido mínimo para mostrar
    if not state["text"] and not state["image"]:
        await query.answer("Necesitas configurar al menos texto o imagen para el post.", show_alert=True)
        return
    
    # Preparar mensaje para indicar que es una vista previa
    await query.edit_message_text(
        "<b>👁 Vista Previa del Post</b>\n\n"
        "Generando vista previa...",
        parse_mode=ParseMode.HTML
    )
    
    # Crear botones de preview si hay configurados
    reply_markup = None
    if state["buttons"]:
        keyboard = []
        row = []
        for i, btn in enumerate(state["buttons"]):
            if "url" in btn:
                button = InlineKeyboardButton(btn["text"], url=btn["url"])
            else:
                button = InlineKeyboardButton(btn["text"], callback_data=f"preview_btn_{i}")
            
            row.append(button)
            
            # Crear nueva fila cada 2 botones o al final
            if (i + 1) % 2 == 0 or i == len(state["buttons"]) - 1:
                keyboard.append(row)
                row = []
        
        reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Enviar vista previa según el contenido
    try:
        if state["image"] and state["text"]:
            # Enviar imagen con pie de texto
            await context.bot.send_photo(
                chat_id=user_id,
                photo=state["image"],
                caption=state["text"],
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
        elif state["image"]:
            # Enviar solo imagen
            await context.bot.send_photo(
                chat_id=user_id,
                photo=state["image"],
                reply_markup=reply_markup
            )
        else:
            # Enviar solo texto
            await context.bot.send_message(
                chat_id=user_id,
                text=state["text"],
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
    except Exception as e:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Error al generar la vista previa: {str(e)}\n\n"
                 f"Por favor, verifica el formato del texto y los botones.",
            parse_mode=ParseMode.HTML
        )
    
    # Volver al menú de creación
    keyboard = [
        [
            InlineKeyboardButton("📝 Añadir/Editar Texto", callback_data="post_add_text"),
            InlineKeyboardButton("🖼 Añadir/Editar Imagen", callback_data="post_add_image")
        ],
        [
            InlineKeyboardButton("🔗 Añadir/Editar Botones", callback_data="post_add_buttons"),
            InlineKeyboardButton("📢 Seleccionar Canales", callback_data="post_select_channels")
        ],
        [
            InlineKeyboardButton("⏰ Programar Horario", callback_data="post_schedule"),
            InlineKeyboardButton("👁 Vista Previa", callback_data="post_preview")
        ],
        [
            InlineKeyboardButton("✅ Guardar Post", callback_data="post_save"),
            InlineKeyboardButton("❌ Cancelar", callback_data="admin_auto_post")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=user_id,
        text="<b>🆕 Crear Nuevo Post Automático</b>\n\n"
             "Vista previa generada correctamente. ¿Deseas realizar algún cambio?",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def save_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Guarda el post configurado en la base de datos."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID or user_id not in post_creation_state:
        await query.answer("No hay un proceso de creación de post activo.", show_alert=True)
        return
    
    state = post_creation_state[user_id]
    
    # Validar contenido mínimo
    if not state["text"] and not state["image"]:
        await query.answer("Necesitas configurar al menos texto o imagen para el post.", show_alert=True)
        return
    
    if not state["selected_channels"]:
        await query.answer("Necesitas seleccionar al menos un canal para publicar.", show_alert=True)
        return
    
    # Preparar datos del post
    post_data = {
        "post_id": state["post_id"],
        "text": state["text"],
        "image": state["image"],
        "buttons": state["buttons"],
        "channels": state["selected_channels"],
        "schedule": state["schedule"],
        "created_by": user_id,
        "created_at": datetime.now().isoformat(),
        "status": "scheduled"
    }
    
    # Guardar en la base de datos
    try:
        success = db.save_post_config(state["post_id"], post_data)
        
        if success:
            # Programar la publicación
            await schedule_post_publication(context, post_data)
            
            await query.edit_message_text(
                "<b>✅ Post Guardado Exitosamente</b>\n\n"
                f"ID del post: <code>{state['post_id']}</code>\n"
                f"Canales: {len(state['selected_channels'])}\n"
                f"Programado para: {state['schedule']['hour']:02d}:{state['schedule']['minute']:02d}\n\n"
                f"El post ha sido guardado y programado correctamente.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_auto_post")]
                ])
            )
            
            # Limpiar el estado de creación
            if user_id in post_creation_state:
                del post_creation_state[user_id]
            
        else:
            await query.edit_message_text(
                "❌ Error al guardar el post en la base de datos.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Reintentar", callback_data="post_save")],
                    [InlineKeyboardButton("🔙 Volver", callback_data="admin_auto_post")]
                ])
            )
    except Exception as e:
        logger.error(f"Error saving post: {e}")
        await query.edit_message_text(
            f"❌ Error al guardar el post: {str(e)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Reintentar", callback_data="post_save")],
                [InlineKeyboardButton("🔙 Volver", callback_data="admin_auto_post")]
            ])
        )

async def schedule_post_publication(context: ContextTypes.DEFAULT_TYPE, post_data):
    """Programa la publicación del post."""
    post_id = post_data["post_id"]
    schedule = post_data["schedule"]
    
    # Calcular próxima hora de publicación
    now = datetime.now()
    
    # Crear una fecha para hoy con la hora programada
    scheduled_time = datetime(
        now.year, now.month, now.day,
        schedule["hour"], schedule["minute"], 0
    )
    
    # Si ya pasó la hora programada, programar para mañana
    if scheduled_time < now:
        scheduled_time += timedelta(days=1)
    
    # Si hay días específicos, ajustar a la próxima fecha válida
    if not schedule["daily"]:
        # Continuar añadiendo días hasta encontrar un día válido
        while scheduled_time.weekday() not in schedule["days"]:
            scheduled_time += timedelta(days=1)
    
    # Calcular delay en segundos
    delay = (scheduled_time - now).total_seconds()
    
    # Programar la tarea
    context.job_queue.run_once(
        publish_scheduled_post,
        delay,
        data={"post_id": post_id},
        name=f"publish_post_{post_id}"
    )
    
    logger.info(f"Post {post_id} scheduled for {scheduled_time}")

async def list_auto_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista todos los posts automáticos."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.answer("Solo el administrador principal puede ver la lista de posts.", show_alert=True)
        return
    
    # Obtener todos los posts configurados
    posts = db.get_post_config()
    
    if not posts:
        await query.edit_message_text(
            "<b>📋 Lista de Posts Automáticos</b>\n\n"
            "No hay posts configurados actualmente.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Nuevo Post", callback_data="create_auto_post")],
                [InlineKeyboardButton("🔙 Volver", callback_data="admin_auto_post")]
            ])
        )
        return
    
    # Construir lista de posts
    message = "<b>📋 Lista de Posts Automáticos</b>\n\n"
    
    keyboard = []
    for i, post in enumerate(posts, 1):
        # Formatear fecha de creación
        created_at = datetime.fromisoformat(post.get("created_at", ""))
        created_str = created_at.strftime("%d/%m/%Y %H:%M")
        
        # Obtener detalles de programación
        schedule = post.get("schedule", {})
        time_str = f"{schedule.get('hour', 0):02d}:{schedule.get('minute', 0):02d}"
        
        # Contar canales
        channels_count = len(post.get("channels", []))
        
        message += f"{i}. <b>Post {post['post_id']}</b>\n"
        message += f"   📅 Creado: {created_str}\n"
        message += f"   ⏰ Hora: {time_str}\n"
        message += f"   📢 Canales: {channels_count}\n"
        message += f"   📊 Estado: {post.get('status', 'programado')}\n\n"
        
        # Añadir botón para administrar este post
        keyboard.append([InlineKeyboardButton(
            f"Administrar Post #{i}", 
            callback_data=f"manage_post_{post['post_id']}"
        )])
    
    keyboard.append([
        InlineKeyboardButton("➕ Nuevo Post", callback_data="create_auto_post"),
        InlineKeyboardButton("🔙 Volver", callback_data="admin_auto_post")
    ])
    
    await query.edit_message_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def publish_scheduled_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Publica un post programado."""
    job = context.job
    post_id = job.data["post_id"]
    
    # Obtener configuración del post
    post_config = db.get_post_config(post_id)
    if not post_config:
        logger.error(f"Post configuration not found for id: {post_id}")
        return
    
    # Obtener canales a publicar
    channels = post_config.get("channels", [])
    if not channels:
        logger.error(f"No channels found for post: {post_id}")
        return
    
    # Preparar mensaje
    text = post_config.get("text", "")
    image = post_config.get("image")
    buttons = post_config.get("buttons", [])
    
    # Preparar teclado si hay botones
    reply_markup = None
    if buttons:
        keyboard = []
        row = []
        for i, btn in enumerate(buttons):
            if "url" in btn:
                button = InlineKeyboardButton(btn["text"], url=btn["url"])
            elif "callback_data" in btn:
                button = InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"])
            else:
                continue
                
            row.append(button)
            
            # Crear nueva fila cada 2 botones o al final
            if (i + 1) % 2 == 0 or i == len(buttons) - 1:
                keyboard.append(row)
                row = []
        
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
        
        # Actualizar estadísticas
        successful_channels = [ch for ch in publish_stats["channels"] if ch["status"] == "success"]
        
        # Programar tarea para eliminar el post
        if successful_channels:
            context.job_queue.run_once(
                delete_scheduled_post,
                delete_time,
                data={
                    "post_id": post_id,
                    "channels": successful_channels
                },
                name=f"delete_post_{post_id}"
            )
    
    # Si es publicación diaria, programar siguiente publicación
    schedule = post_config.get("schedule", {})
    if schedule.get("daily", False) or schedule.get("days"):
        # Programar para el día siguiente a la misma hora
        next_run = datetime.now() + timedelta(days=1)
        next_run = next_run.replace(
            hour=schedule["hour"], 
            minute=schedule["minute"],
            second=0,
            microsecond=0
        )
        
        # Si hay días específicos, ajustar a la próxima fecha válida
        if not schedule.get("daily", False) and schedule.get("days", []):
            while next_run.weekday() not in schedule["days"]:
                next_run += timedelta(days=1)
        
        # Calcular delay en segundos
        delay = (next_run - datetime.now()).total_seconds()
        
        # Programar la próxima publicación
        context.job_queue.run_once(
            publish_scheduled_post,
            delay,
            data={"post_id": post_id},
            name=f"publish_post_{post_id}"
        )
        
        logger.info(f"Next publication of post {post_id} scheduled for {next_run}")

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
    
async def load_scheduled_posts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Carga y reprograma los posts existentes."""
    posts = db.get_post_config()
    
    for post in posts:
        if post.get("status") == "scheduled":
            try:
                await schedule_post_publication(context, post)
                logger.info(f"Loaded and scheduled post {post['post_id']}")
            except Exception as e:
                logger.error(f"Error loading post {post['post_id']}: {e}")  

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
    application.add_handler(CommandHandler("del", delete_auto_post_channel))
    application.add_handler(CommandHandler("edit", edit_channel_cmd))
    
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
    
    # Manejadores para creación de posts automáticos
    application.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_text_input_router
    ))    
    
        # Manejar mensajes de texto y fotos del administrador
    application.add_handler(MessageHandler(
        filters.TEXT & filters.User(ADMIN_ID) & ~filters.COMMAND & filters.ChatType.PRIVATE,
        process_post_text
    ))

    application.add_handler(MessageHandler(
        filters.PHOTO & filters.User(ADMIN_ID) & ~filters.COMMAND & filters.ChatType.PRIVATE,
        process_post_image
    ))
    
    # Programar la carga de posts cuando el bot inicie
    application.job_queue.run_once(load_scheduled_posts, 1)  # Ejecutar después de 1 segundo
    
    # Manejar todos los mensajes
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_message))
    
    # Ejecutar el bot hasta que el usuario presione Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

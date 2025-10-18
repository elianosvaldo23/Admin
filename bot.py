"""
Bot principal de Telegram para automatizar funciones de Terabox
"""

import logging
import asyncio
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler,
    filters, 
    ContextTypes
)

from terabox_api import TeraboxAPI
from config import TELEGRAM_BOT_TOKEN, LOG_LEVEL, LOG_FILE

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL),
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TeraboxBot:
    def __init__(self):
        self.terabox_api = TeraboxAPI()
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Configurar los handlers del bot"""
        # Comandos
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        
        # Mensajes con enlaces
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_message
        ))
        
        # Callbacks de botones
        self.app.add_handler(CallbackQueryHandler(self.button_callback))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /start"""
        welcome_text = """
🤖 **Bot de Automatización Terabox**

¡Bienvenido! Este bot puede ayudarte a:

✅ **Descargar archivos** de enlaces de Terabox
✅ **Subir archivos** a tu Terabox personal  
✅ **Crear enlaces** de compartir
✅ **Procesar enlaces** automáticamente

**🚀 Cómo usar:**
1. Envía un enlace de Terabox
2. El bot lo descargará y subirá a tu cuenta
3. Recibirás un nuevo enlace de compartir

Usa /help para más información.
        """
        
        keyboard = [
            [InlineKeyboardButton("📚 Ayuda", callback_data="help")],
            [InlineKeyboardButton("⚙️ Estado", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text, 
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /help"""
        help_text = """
📖 **Guía de Uso del Bot**

**Comandos disponibles:**
• `/start` - Iniciar el bot
• `/help` - Mostrar esta ayuda  
• `/status` - Verificar estado de conexión

**Funciones principales:**

🔗 **Procesar enlaces de Terabox:**
   • Envía un enlace de Terabox
   • El bot lo descargará automáticamente
   • Lo subirá a tu cuenta personal
   • Te dará un nuevo enlace de compartir

📁 **Tipos de enlace soportados:**
   • Enlaces de carpetas compartidas
   • Enlaces de archivos individuales
   • Enlaces con contraseña (próximamente)

⚠️ **Limitaciones:**
   • Archivos muy grandes pueden tardar más
   • Los tokens de sesión expiran periódicamente
   • Respeta los límites de Terabox

💡 **Consejos:**
   • Verifica que el enlace sea válido
   • Espera a que termine el proceso
   • Contacta al admin si hay problemas
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /status"""
        try:
            # Verificar estado de tokens
            is_valid = self.terabox_api.validate_tokens()
            
            if is_valid:
                status_text = """
✅ **Estado: OPERATIVO**

🔑 Tokens de autenticación: ✅ Válidos
🌐 Conexión a Terabox: ✅ Activa
🤖 Bot de Telegram: ✅ Funcionando

El bot está listo para procesar enlaces.
                """
                status_emoji = "✅"
            else:
                status_text = """
⚠️ **Estado: REQUIERE ATENCIÓN**

🔑 Tokens de autenticación: ❌ Expirados
🌐 Conexión a Terabox: ❌ Problemas
🤖 Bot de Telegram: ✅ Funcionando

Los tokens necesitan ser actualizados.
Contacta al administrador.
                """
                status_emoji = "⚠️"
                
        except Exception as e:
            status_text = f"""
❌ **Estado: ERROR**

Error verificando conexión: {str(e)}
Contacta al administrador.
            """
            status_emoji = "❌"
        
        keyboard = [[InlineKeyboardButton("🔄 Actualizar", callback_data="status")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            status_text, 
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manejar mensajes de texto (especialmente enlaces)"""
        message_text = update.message.text.strip()
        
        # Verificar si es un enlace de Terabox
        if 'terabox.com' in message_text.lower():
            await self.process_terabox_link(update, message_text)
        else:
            await update.message.reply_text(
                "📎 Por favor, envía un enlace válido de Terabox.\n\n"
                "Ejemplo: https://www.terabox.com/s/..."
            )
    
    async def process_terabox_link(self, update: Update, link: str):
        """Procesar enlace de Terabox"""
        try:
            # Mensaje inicial
            processing_msg = await update.message.reply_text(
                "🔄 **Procesando enlace...**\n\n"
                "⏳ Descargando archivo de Terabox...",
                parse_mode='Markdown'
            )
            
            # Procesar en hilo separado para no bloquear
            result = await asyncio.to_thread(
                self.terabox_api.process_terabox_link, 
                link
            )
            
            if result.get("success"):
                # Éxito
                success_text = f"""
✅ **¡Proceso completado exitosamente!**

🔗 **Enlace original:** [Archivo original]({result['original_url']})
🆕 **Nuevo enlace:** [Descargar aquí]({result['new_share_url']})

📁 El archivo ha sido guardado en tu Terabox personal.
                """
                
                keyboard = [
                    [InlineKeyboardButton("🔗 Abrir enlace", url=result['new_share_url'])],
                    [InlineKeyboardButton("🔄 Procesar otro", callback_data="new_process")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await processing_msg.edit_text(
                    success_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
                
            else:
                # Error
                error_text = f"""
❌ **Error procesando enlace**

🚫 **Problema:** {result.get('error', 'Error desconocido')}

**Posibles soluciones:**
• Verifica que el enlace sea válido
• Asegúrate de que el archivo esté disponible  
• Intenta nuevamente en unos minutos
• Si persiste, contacta al administrador

💡 Usa /status para verificar el estado del bot.
                """
                
                keyboard = [[InlineKeyboardButton("🔄 Intentar nuevamente", callback_data="retry")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await processing_msg.edit_text(
                    error_text,
                    parse_mode='Markdown', 
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            logger.error(f"Error procesando enlace: {e}")
            
            error_text = f"""
💥 **Error interno del bot**

Ha ocurrido un error inesperado. 
Por favor, contacta al administrador.

**Error:** `{str(e)}`
            """
            
            try:
                await processing_msg.edit_text(error_text, parse_mode='Markdown')
            except:
                await update.message.reply_text(error_text, parse_mode='Markdown')
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manejar callbacks de botones inline"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "help":
            await self.help_command(update, context)
        elif query.data == "status":
            await self.status_command(update, context)
        elif query.data == "new_process":
            await query.message.reply_text(
                "📎 Envía un nuevo enlace de Terabox para procesar."
            )
        elif query.data == "retry":
            await query.message.reply_text(
                "🔄 Intenta enviando el enlace nuevamente."
            )
    
    def run(self):
        """Ejecutar el bot"""
        logger.info("Iniciando bot de Telegram...")
        
        try:
            # Verificar configuración inicial
            if not self.terabox_api.validate_tokens():
                logger.warning("Los tokens de Terabox pueden estar expirados")
            
            # Ejecutar bot
            self.app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            
        except Exception as e:
            logger.error(f"Error ejecutando bot: {e}")
            raise

def main():
    """Función principal"""
    try:
        bot = TeraboxBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        raise

if __name__ == "__main__":
    main()

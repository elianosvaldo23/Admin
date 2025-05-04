import logging
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from config import MONGO_URI, DEFAULT_WELCOME_MESSAGE

# Configuración del logger
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Clase principal para manejo de base de datos MongoDB
class MongoDB:
    def __init__(self):
        self.client = None
        self.db = None
        self.connect()
        self.init_db()

    def connect(self):
        """Crea la conexión a MongoDB."""
        try:
            self.client = MongoClient(MONGO_URI)
            self.db = self.client.botonera_bot
            logger.info("Conexión a MongoDB establecida correctamente")
        except PyMongoError as e:
            logger.error(f"Error al conectar con MongoDB: {e}")
            raise

    def init_db(self):
        """Inicializa las colecciones de la base de datos."""
        try:
            # Crear índices necesarios
            self.db.approved_channels.create_index("channel_id", unique=True)
            self.db.approved_channels.create_index("channel_username")
            self.db.approved_channels.create_index("added_by")
            self.db.approved_channels.create_index("category")
            
            self.db.pending_submissions.create_index("submission_id", unique=True)
            self.db.pending_submissions.create_index("user_id")
            
            self.db.warnings.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
            self.db.stats.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
            
            self.db.auto_post_channels.create_index("channel_id", unique=True)
            
            # Verificar configuración inicial
            if not self.db.config.find_one({"key": "welcome_message"}):
                self.db.config.insert_one({
                    "key": "welcome_message",
                    "value": DEFAULT_WELCOME_MESSAGE
                })
            
            if not self.db.config.find_one({"key": "welcome_buttons"}):
                default_buttons = [
                    {"text": "Canal Principal", "url": "https://t.me/botoneraMultimediaTv"},
                    {"text": "Categorías", "url": "https://t.me/c/2259108243/2"},
                    {"text": "📣 Canales y Grupos 👥", "callback_data": "user_channels"}
                ]
                self.db.config.insert_one({
                    "key": "welcome_buttons",
                    "value": default_buttons
                })
            
            logger.info("Base de datos inicializada correctamente")
        except PyMongoError as e:
            logger.error(f"Error al inicializar la base de datos: {e}")
            raise

    # ----- FUNCIONES DE CONFIGURACIÓN -----
    def save_config(self, key, value):
        """Guarda un valor en la configuración."""
        try:
            self.db.config.update_one(
                {"key": key},
                {"$set": {"value": value}},
                upsert=True
            )
            return True
        except PyMongoError as e:
            logger.error(f"Error guardando configuración {key}: {e}")
            return False

    def load_config(self, key):
        """Carga un valor de la configuración."""
        try:
            config = self.db.config.find_one({"key": key})
            return config["value"] if config else None
        except PyMongoError as e:
            logger.error(f"Error cargando configuración {key}: {e}")
            return None

    # ----- FUNCIONES DE CANALES APROBADOS -----
    def save_approved_channel(self, channel_id, channel_name, channel_username, category, added_by):
        """Guarda un canal aprobado en la base de datos."""
        try:
            self.db.approved_channels.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "channel_name": channel_name,
                        "channel_username": channel_username,
                        "category": category,
                        "added_by": added_by,
                        "added_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "subscribers": 0  # Campo para número de suscriptores
                    }
                },
                upsert=True
            )
            
            # Contar canales en la categoría
            count = self.db.approved_channels.count_documents({"category": category})
            return True, count
        except PyMongoError as e:
            logger.error(f"Error guardando canal aprobado: {e}")
            return False, 0

    def get_approved_channels(self, category=None, user_id=None):
        """Obtiene los canales aprobados, opcionalmente filtrados por categoría o usuario."""
        try:
            filter_query = {}
            if category:
                filter_query["category"] = category
            if user_id:
                filter_query["added_by"] = user_id
                
            channels = list(self.db.approved_channels.find(filter_query, {'_id': 0}))
            return channels
        except PyMongoError as e:
            logger.error(f"Error obteniendo canales aprobados: {e}")
            return []

    def delete_approved_channel(self, channel_id):
        """Elimina un canal aprobado de la base de datos."""
        try:
            result = self.db.approved_channels.delete_one({"channel_id": channel_id})
            return result.deleted_count > 0
        except PyMongoError as e:
            logger.error(f"Error eliminando canal aprobado: {e}")
            return False

    def update_channel_info(self, channel_id, field_name, new_value):
        """Actualiza un campo específico de un canal."""
        try:
            result = self.db.approved_channels.update_one(
                {"channel_id": channel_id},
                {"$set": {field_name: new_value}}
            )
            return result.modified_count > 0
        except PyMongoError as e:
            logger.error(f"Error actualizando información del canal: {e}")
            return False
            
    def update_channel_subscribers(self, channel_id, subscribers):
        """Actualiza el número de suscriptores de un canal."""
        try:
            result = self.db.approved_channels.update_one(
                {"channel_id": channel_id},
                {"$set": {"subscribers": subscribers}}
            )
            return result.modified_count > 0
        except PyMongoError as e:
            logger.error(f"Error actualizando suscriptores del canal: {e}")
            return False

    # ----- FUNCIONES DE SOLICITUDES PENDIENTES -----
    def save_pending_submission(self, submission_id, submission_data):
        """Guarda una solicitud pendiente en la base de datos."""
        try:
            submission_data["submission_date"] = datetime.now().isoformat()
            self.db.pending_submissions.update_one(
                {"submission_id": submission_id},
                {"$set": submission_data},
                upsert=True
            )
            return True
        except PyMongoError as e:
            logger.error(f"Error guardando solicitud pendiente: {e}")
            return False

    def get_pending_submissions(self):
        """Obtiene todas las solicitudes pendientes."""
        try:
            submissions = {}
            for submission in self.db.pending_submissions.find({}, {'_id': 0}):
                submissions[submission["submission_id"]] = submission
            return submissions
        except PyMongoError as e:
            logger.error(f"Error obteniendo solicitudes pendientes: {e}")
            return {}

    def delete_pending_submission(self, submission_id):
        """Elimina una solicitud pendiente de la base de datos."""
        try:
            result = self.db.pending_submissions.delete_one({"submission_id": submission_id})
            return result.deleted_count > 0
        except PyMongoError as e:
            logger.error(f"Error eliminando solicitud pendiente: {e}")
            return False

    # ----- FUNCIONES DE ESTADÍSTICAS -----
    def update_user_stats(self, user_id, chat_id, stat_type):
        """Actualiza las estadísticas de un usuario."""
        try:
            now = datetime.now().isoformat()
            
            # Construir actualizaciones basadas en el tipo de estadística
            update_field = {f"{stat_type}": 1}
            
            self.db.stats.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$inc": update_field,
                    "$set": {"last_active": now}
                },
                upsert=True
            )
            return True
        except PyMongoError as e:
            logger.error(f"Error actualizando estadísticas de usuario: {e}")
            return False

    def get_user_stats(self, user_id, chat_id):
        """Obtiene las estadísticas de un usuario."""
        try:
            stats = self.db.stats.find_one({"user_id": user_id, "chat_id": chat_id})
            if stats:
                return {
                    "messages": stats.get("messages", 0),
                    "media": stats.get("media", 0),
                    "commands": stats.get("commands", 0),
                    "last_active": stats.get("last_active")
                }
            else:
                return {
                    "messages": 0,
                    "media": 0,
                    "commands": 0,
                    "last_active": None
                }
        except PyMongoError as e:
            logger.error(f"Error obteniendo estadísticas de usuario: {e}")
            return {
                "messages": 0,
                "media": 0,
                "commands": 0,
                "last_active": None
            }

    # ----- FUNCIONES DE ADVERTENCIAS -----
    def add_warning(self, user_id, chat_id, reason):
        """Añade una advertencia a un usuario."""
        try:
            # Obtener advertencias actuales
            warning_data = self.db.warnings.find_one({"user_id": user_id, "chat_id": chat_id})
            
            new_reason = {
                "reason": reason,
                "date": datetime.now().isoformat()
            }
            
            if warning_data:
                count = warning_data.get("count", 0) + 1
                reasons = warning_data.get("reasons", [])
                reasons.append(new_reason)
                
                self.db.warnings.update_one(
                    {"user_id": user_id, "chat_id": chat_id},
                    {"$set": {"count": count, "reasons": reasons}}
                )
            else:
                count = 1
                reasons = [new_reason]
                
                self.db.warnings.insert_one({
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "count": count,
                    "reasons": reasons
                })
            
            return count
        except PyMongoError as e:
            logger.error(f"Error añadiendo advertencia: {e}")
            return 0

    def get_warnings(self, user_id, chat_id):
        """Obtiene las advertencias de un usuario."""
        try:
            warnings = self.db.warnings.find_one({"user_id": user_id, "chat_id": chat_id})
            if warnings:
                return {
                    "count": warnings.get("count", 0),
                    "reasons": warnings.get("reasons", [])
                }
            else:
                return {
                    "count": 0,
                    "reasons": []
                }
        except PyMongoError as e:
            logger.error(f"Error obteniendo advertencias: {e}")
            return {
                "count": 0,
                "reasons": []
            }

    def reset_warnings(self, user_id, chat_id):
        """Reinicia las advertencias de un usuario."""
        try:
            result = self.db.warnings.delete_one({"user_id": user_id, "chat_id": chat_id})
            return result.deleted_count > 0
        except PyMongoError as e:
            logger.error(f"Error reiniciando advertencias: {e}")
            return False

    # ----- FUNCIONES DE PUBLICACIÓN AUTOMÁTICA -----
    def save_auto_post_channel(self, channel_id, channel_name, channel_username, added_by):
        """Guarda un canal para publicación automática."""
        try:
            self.db.auto_post_channels.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "channel_name": channel_name,
                        "channel_username": channel_username,
                        "added_by": added_by,
                        "added_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "subscribers": 0,
                        "posts_history": []
                    }
                },
                upsert=True
            )
            return True
        except PyMongoError as e:
            logger.error(f"Error guardando canal para publicación automática: {e}")
            return False

    def delete_auto_post_channel(self, channel_id):
        """Elimina un canal de la lista de publicación automática."""
        try:
            result = self.db.auto_post_channels.delete_one({"channel_id": channel_id})
            return result.deleted_count > 0
        except PyMongoError as e:
            logger.error(f"Error eliminando canal de publicación automática: {e}")
            return False

    def get_auto_post_channels(self):
        """Obtiene todos los canales para publicación automática."""
        try:
            return list(self.db.auto_post_channels.find({}, {'_id': 0}))
        except PyMongoError as e:
            logger.error(f"Error obteniendo canales para publicación automática: {e}")
            return []

    def save_post_config(self, post_id, config_data):
        """Guarda la configuración de un post automático."""
        try:
            config_data["created_date"] = datetime.now().isoformat()
            self.db.posts_config.update_one(
                {"post_id": post_id},
                {"$set": config_data},
                upsert=True
            )
            return True
        except PyMongoError as e:
            logger.error(f"Error guardando configuración de post: {e}")
            return False

    def get_post_config(self, post_id=None):
        """Obtiene la configuración de posts automáticos."""
        try:
            if post_id:
                return self.db.posts_config.find_one({"post_id": post_id}, {'_id': 0})
            else:
                return list(self.db.posts_config.find({}, {'_id': 0}))
        except PyMongoError as e:
            logger.error(f"Error obteniendo configuración de post: {e}")
            return None if post_id else []

    def update_post_stats(self, post_id, channel_id, status, message_id=None, deleted_at=None):
        """Actualiza las estadísticas de un post en un canal."""
        try:
            update_data = {
                "status": status,
                "updated_at": datetime.now().isoformat()
            }
            
            if message_id:
                update_data["message_id"] = message_id
                
            if deleted_at:
                update_data["deleted_at"] = deleted_at
                
            self.db.posts_config.update_one(
                {"post_id": post_id, "channels.channel_id": channel_id},
                {"$set": {"channels.$.status": status, "channels.$.updated_at": datetime.now().isoformat()}},
                upsert=False
            )
            return True
        except PyMongoError as e:
            logger.error(f"Error actualizando estadísticas de post: {e}")
            return False

    def count_channels_by_type(self, user_id):
        """Cuenta la cantidad de canales y grupos añadidos por un usuario."""
        try:
            channels = self.get_approved_channels(user_id=user_id)
            canal_count = 0
            grupo_count = 0
            total_subs = 0
            
            for channel in channels:
                if "channel" in channel.get("channel_id", ""):
                    canal_count += 1
                else:
                    grupo_count += 1
                total_subs += channel.get("subscribers", 0)
                
            return {
                "canales": canal_count,
                "grupos": grupo_count,
                "total_subs": total_subs
            }
        except PyMongoError as e:
            logger.error(f"Error contando canales por tipo: {e}")
            return {"canales": 0, "grupos": 0, "total_subs": 0}

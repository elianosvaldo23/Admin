# Configuración del bot
TOKEN = "7675635354:AAEkxM528h5vEa2auoMr94x1tWIGop8xKgo"
ADMIN_ID = 1742433244
GROUP_ID = "botoneraMultimediaTv"  # Grupo username sin @
CATEGORY_CHANNEL_ID = -1002259108243

# URL de MongoDB (actualiza con tu URL de MongoDB real)
MONGO_URI = "mongodb+srv://username:password@cluster.mongodb.net/botonera_bot"

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

# Configuración anti-spam
SPAM_WINDOW = 60  # segundos
SPAM_LIMIT = 5  # mensajes
SPAM_MUTE_TIME = 300  # segundos (5 minutos)

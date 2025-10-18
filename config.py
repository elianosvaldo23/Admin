"""
Copia este archivo como config.py y reemplaza los valores con tus credenciales reales
"""

# Tu token del bot de Telegram (obtenido de @BotFather)
TELEGRAM_BOT_TOKEN = "7123456789:AAHGHghGFhgfHGFhgfhgfHGFhgfhgfHGFhg"

# Credenciales de Terabox que ya tienes
TERABOX_CREDENTIALS = {
    "ndus": "tu_valor_ndus_aqui",
    "bds_token": "tu_bds_token_aqui",
    "js_token": "tu_js_token_aqui",
    "app_id": "250528",  # Generalmente es este valor para Terabox
    "upload_id": "tu_upload_id_aqui",
    "browser_id": "tu_browser_id_aqui"
}

# COOKIES ADICIONALES CRÍTICAS (obtener desde navegador)
# Ve a terabox.com, abre DevTools (F12) > Application > Cookies
TERABOX_ADDITIONAL_COOKIES = {
    "csrfToken": "",      # MUY IMPORTANTE para evitar verificación
    "ndut_fmt": "",       # Token de formato adicional
    "lang": "es",         # Idioma de la interfaz
    "BOXCLND": ""         # Cookie de sesión después del login
}

# Configuración adicional
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.terabox.com/disk/home",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors", 
    "Sec-Fetch-Site": "same-origin",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

TERABOX_URLS = {
    "share_link": "https://www.terabox.com/api/share/link",
    "download": "https://www.terabox.com/api/download/file", 
    "upload": "https://www.terabox.com/api/upload",
    "file_info": "https://www.terabox.com/api/file/info"
}

REQUEST_DELAYS = {
    "min_delay": 1,
    "max_delay": 3, 
    "error_delay": 10
}

LOG_LEVEL = "INFO"
LOG_FILE = "terabox_bot.log"

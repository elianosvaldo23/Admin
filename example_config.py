"""
Configuración para el bot de Telegram - Terabox
Copia este archivo como config.py y completa con tus datos reales
"""

import os

# Configuración del Bot de Telegram
TELEGRAM_BOT_TOKEN = "TU_BOT_TOKEN_AQUI"

# Credenciales de Terabox (las que ya tienes)
TERABOX_CREDENTIALS = {
    "ndus": "TU_NDUS_AQUI",
    "bds_token": "TU_BDS_TOKEN_AQUI", 
    "js_token": "TU_JS_TOKEN_AQUI",
    "app_id": "TU_APP_ID_AQUI",
    "upload_id": "TU_UPLOAD_ID_AQUI",
    "browser_id": "TU_BROWSER_ID_AQUI"
}

# Cookies adicionales necesarias para evitar verificación
TERABOX_ADDITIONAL_COOKIES = {
    "csrfToken": "TU_CSRF_TOKEN_AQUI",        # Obtener desde navegador
    "ndut_fmt": "TU_NDUT_FMT_AQUI",          # Obtener desde navegador
    "lang": "es",                             # Idioma (es/en)
    "BOXCLND": "TU_BOXCLND_AQUI"             # Cookie de sesión después de login
}

# Headers HTTP para simular navegador real
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

# URLs de la API de Terabox
TERABOX_URLS = {
    "share_link": "https://www.terabox.com/api/share/link",
    "download": "https://www.terabox.com/api/download/file",
    "upload": "https://www.terabox.com/api/upload",
    "file_info": "https://www.terabox.com/api/file/info"
}

# Configuración de delays para evitar detección
REQUEST_DELAYS = {
    "min_delay": 1,    # Mínimo 1 segundo entre peticiones
    "max_delay": 3,    # Máximo 3 segundos entre peticiones
    "error_delay": 10  # 10 segundos de espera después de error
}

# Configuración de logging
LOG_LEVEL = "INFO"
LOG_FILE = "terabox_bot.log"

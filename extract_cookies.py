"""
Script adicional para obtener cookies automáticamente desde el navegador
Útil para automatizar la captura de cookies críticas
"""

import json
import sqlite3
import os
import platform
import base64
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import logging

logger = logging.getLogger(__name__)

class CookieExtractor:
    """Extrae cookies de navegadores para Terabox"""
    
    def __init__(self):
        self.cookies = {}
        self.required_cookies = ['csrfToken', 'ndut_fmt', 'lang', 'BOXCLND', 'ndus']
    
    def get_chrome_cookies_path(self):
        """Obtener ruta de cookies de Chrome según el OS"""
        system = platform.system()
        
        if system == "Windows":
            return os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data\Default\Cookies")
        elif system == "Darwin":  # macOS
            return os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/Cookies")
        elif system == "Linux":
            return os.path.expanduser("~/.config/google-chrome/Default/Cookies")
        
        return None
    
    def decrypt_chrome_cookies(self, encrypted_value):
        """Desencriptar cookies de Chrome (solo para Windows/macOS)"""
        try:
            # Para Linux las cookies generalmente no están encriptadas
            if platform.system() == "Linux":
                return encrypted_value
            
            # Para Windows y macOS necesitarías implementar desencriptado específico
            # Este es un ejemplo simplificado
            if encrypted_value.startswith(b'v10') or encrypted_value.startswith(b'v11'):
                # Chrome 80+ usa encriptación AES
                logger.warning("Cookies encriptadas detectadas - se necesita implementación completa")
                return ""
            
            return encrypted_value.decode('utf-8', errors='ignore')
            
        except Exception as e:
            logger.error(f"Error desencriptando cookie: {e}")
            return ""
    
    def extract_from_chrome(self):
        """Extraer cookies de Chrome"""
        try:
            cookies_path = self.get_chrome_cookies_path()
            
            if not cookies_path or not os.path.exists(cookies_path):
                logger.warning("No se encontró la base de datos de cookies de Chrome")
                return {}
            
            # Hacer copia temporal (Chrome bloquea el archivo si está abierto)
            temp_cookies = "/tmp/cookies_temp.db"
            import shutil
            shutil.copy2(cookies_path, temp_cookies)
            
            # Conectar a la base de datos SQLite
            conn = sqlite3.connect(temp_cookies)
            cursor = conn.cursor()
            
            # Consultar cookies de Terabox
            query = """
            SELECT name, value, encrypted_value 
            FROM cookies 
            WHERE host_key LIKE '%terabox.com%'
            """
            
            cursor.execute(query)
            results = cursor.fetchall()
            
            cookies = {}
            for name, value, encrypted_value in results:
                if name in self.required_cookies:
                    if value:
                        cookies[name] = value
                    elif encrypted_value:
                        decrypted = self.decrypt_chrome_cookies(encrypted_value)
                        if decrypted:
                            cookies[name] = decrypted
            
            conn.close()
            os.remove(temp_cookies)
            
            return cookies
            
        except Exception as e:
            logger.error(f"Error extrayendo cookies de Chrome: {e}")
            return {}
    
    def extract_cookies_manual(self):
        """Método manual para obtener cookies (cuando la extracción automática falla)"""
        print("\n🍪 EXTRACCIÓN MANUAL DE COOKIES")
        print("=" * 50)
        print("Sigue estos pasos para obtener las cookies necesarias:\n")
        
        print("1. Abre Chrome y ve a https://www.terabox.com")
        print("2. Inicia sesión en tu cuenta")
        print("3. Presiona F12 para abrir DevTools")
        print("4. Ve a Application > Storage > Cookies > https://www.terabox.com")
        print("5. Busca y copia los valores de estas cookies:\n")
        
        cookies = {}
        for cookie_name in self.required_cookies:
            print(f"🔍 Busca la cookie: {cookie_name}")
            value = input(f"   Ingresa el valor de {cookie_name}: ").strip()
            if value:
                cookies[cookie_name] = value
            print()
        
        return cookies
    
    def validate_cookies(self, cookies):
        """Validar que las cookies críticas estén presentes"""
        critical_cookies = ['csrfToken', 'ndus']
        
        for cookie in critical_cookies:
            if not cookies.get(cookie):
                logger.warning(f"Cookie crítica faltante: {cookie}")
                return False
        
        return True
    
    def save_to_config(self, cookies):
        """Guardar cookies al archivo de configuración"""
        try:
            # Leer configuración actual
            config_content = ""
            try:
                with open("config.py", "r") as f:
                    config_content = f.read()
            except FileNotFoundError:
                # Si no existe config.py, usar plantilla
                with open("example_config.py", "r") as f:
                    config_content = f.read()
            
            # Actualizar cookies en la configuración
            for cookie_name, cookie_value in cookies.items():
                # Buscar y reemplazar línea de cookie
                import re
                pattern = rf'"{cookie_name}"\s*:\s*"[^"]*"'
                replacement = f'"{cookie_name}": "{cookie_value}"'
                
                if re.search(pattern, config_content):
                    config_content = re.sub(pattern, replacement, config_content)
                else:
                    logger.warning(f"No se encontró patrón para cookie {cookie_name}")
            
            # Guardar configuración actualizada
            with open("config.py", "w") as f:
                f.write(config_content)
            
            logger.info("Cookies guardadas en config.py")
            return True
            
        except Exception as e:
            logger.error(f"Error guardando cookies: {e}")
            return False
    
    def run(self):
        """Ejecutar extractor de cookies"""
        print("🤖 EXTRACTOR DE COOKIES PARA TERABOX")
        print("=" * 40)
        
        # Intentar extracción automática primero
        print("\n🔄 Intentando extracción automática desde Chrome...")
        cookies = self.extract_from_chrome()
        
        if not cookies or not self.validate_cookies(cookies):
            print("❌ Extracción automática falló o cookies incompletas")
            print("\n💡 Procediendo con extracción manual...")
            cookies = self.extract_cookies_manual()
        
        if cookies and self.validate_cookies(cookies):
            print(f"\n✅ Se obtuvieron {len(cookies)} cookies:")
            for name, value in cookies.items():
                print(f"   {name}: {value[:20]}...")
            
            # Guardar en configuración
            if self.save_to_config(cookies):
                print("\n🎉 ¡Cookies guardadas exitosamente!")
                print("Ya puedes ejecutar el bot con: python bot.py")
            else:
                print("\n⚠️  Error guardando cookies. Actualiza config.py manualmente")
                
        else:
            print("\n❌ No se pudieron obtener las cookies necesarias")
            print("Verifica que hayas iniciado sesión en Terabox")

def main():
    """Función principal del extractor"""
    extractor = CookieExtractor()
    extractor.run()

if __name__ == "__main__":
    main()

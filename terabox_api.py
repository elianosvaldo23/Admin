"""
Clase TeraboxAPI para manejar todas las interacciones con Terabox
Incluye medidas anti-detección y manejo correcto de cookies/headers
"""

import requests
import time
import random
import logging
import json
from urllib.parse import urlparse, parse_qs
from typing import Dict, Optional, Any
from config import (
    TERABOX_CREDENTIALS, 
    TERABOX_ADDITIONAL_COOKIES, 
    DEFAULT_HEADERS,
    TERABOX_URLS,
    REQUEST_DELAYS
)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TeraboxAPI:
    def __init__(self):
        self.session = requests.Session()
        self.credentials = TERABOX_CREDENTIALS
        self.additional_cookies = TERABOX_ADDITIONAL_COOKIES
        self.headers = DEFAULT_HEADERS.copy()
        self.setup_session()
    
    def setup_session(self):
        """Configurar la sesión con cookies y headers correctos"""
        # Combinar todas las cookies
        all_cookies = {**self.credentials, **self.additional_cookies}
        
        # Agregar cookies a la sesión
        for key, value in all_cookies.items():
            self.session.cookies.set(key, value, domain='.terabox.com')
        
        # Configurar headers por defecto
        self.session.headers.update(self.headers)
        
        logger.info("Sesión configurada con cookies y headers")
    
    def _make_request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """Hacer petición HTTP con medidas anti-detección"""
        # Delay aleatorio para evitar detección
        delay = random.uniform(REQUEST_DELAYS["min_delay"], REQUEST_DELAYS["max_delay"])
        time.sleep(delay)
        
        try:
            response = self.session.request(method, url, **kwargs)
            
            # Verificar si necesita verificación
            if "verification" in response.text.lower() or response.status_code == 403:
                logger.error("Terabox requiere verificación - revisar cookies/tokens")
                return None
            
            response.raise_for_status()
            return response
            
        except requests.RequestException as e:
            logger.error(f"Error en petición HTTP: {e}")
            time.sleep(REQUEST_DELAYS["error_delay"])
            return None
    
    def extract_share_info(self, share_url: str) -> Optional[Dict]:
        """Extraer información de un enlace de compartir de Terabox"""
        try:
            parsed_url = urlparse(share_url)
            
            # Extraer parámetros del enlace
            if 'terabox.com' not in parsed_url.netloc:
                logger.error("El enlace no es de Terabox")
                return None
            
            # Obtener información del archivo compartido
            response = self._make_request("GET", share_url)
            if not response:
                return None
            
            # Aquí necesitarías parsear la respuesta HTML o usar la API
            # Para simplificar, devuelvo estructura básica
            return {
                "url": share_url,
                "title": "Archivo de Terabox",
                "size": "Desconocido"
            }
            
        except Exception as e:
            logger.error(f"Error extrayendo información: {e}")
            return None
    
    def download_file(self, share_url: str, save_path: str = None) -> Optional[str]:
        """Descargar archivo desde enlace de Terabox"""
        try:
            # Primero obtener información del archivo
            file_info = self.extract_share_info(share_url)
            if not file_info:
                return None
            
            # Obtener URL de descarga directa
            download_data = {
                "url": share_url,
                "app_id": self.credentials["app_id"]
            }
            
            response = self._make_request(
                "POST", 
                TERABOX_URLS["download"],
                json=download_data
            )
            
            if not response:
                return None
            
            result = response.json()
            
            if "download_url" in result:
                # Descargar el archivo
                file_response = self._make_request("GET", result["download_url"])
                if file_response:
                    # Guardar archivo localmente si se especifica ruta
                    if save_path:
                        with open(save_path, 'wb') as f:
                            f.write(file_response.content)
                        return save_path
                    else:
                        return file_response.content
            
            return None
            
        except Exception as e:
            logger.error(f"Error descargando archivo: {e}")
            return None
    
    def upload_file(self, file_path: str, folder_path: str = "/") -> Optional[Dict]:
        """Subir archivo a Terabox personal"""
        try:
            # Preparar datos para subida
            upload_data = {
                "path": folder_path,
                "upload_id": self.credentials["upload_id"],
                "app_id": self.credentials["app_id"]
            }
            
            # Subir archivo
            with open(file_path, 'rb') as f:
                files = {'file': f}
                response = self._make_request(
                    "POST",
                    TERABOX_URLS["upload"],
                    data=upload_data,
                    files=files
                )
            
            if not response:
                return None
            
            result = response.json()
            
            if result.get("success"):
                logger.info(f"Archivo subido exitosamente: {file_path}")
                return result
            else:
                logger.error(f"Error subiendo archivo: {result}")
                return None
                
        except Exception as e:
            logger.error(f"Error en upload_file: {e}")
            return None
    
    def create_share_link(self, file_path: str, password: str = None) -> Optional[str]:
        """Crear enlace de compartir para archivo en Terabox"""
        try:
            share_data = {
                "path": file_path,
                "app_id": self.credentials["app_id"]
            }
            
            if password:
                share_data["password"] = password
            
            response = self._make_request(
                "POST",
                TERABOX_URLS["share_link"],
                json=share_data
            )
            
            if not response:
                return None
            
            result = response.json()
            
            if "share_url" in result:
                logger.info(f"Enlace creado: {result['share_url']}")
                return result["share_url"]
            
            return None
            
        except Exception as e:
            logger.error(f"Error creando enlace: {e}")
            return None
    
    def process_terabox_link(self, share_url: str) -> Optional[Dict]:
        """Procesar enlace de Terabox: descargar, subir y crear nuevo enlace"""
        try:
            logger.info(f"Procesando enlace: {share_url}")
            
            # 1. Descargar archivo del enlace compartido
            file_content = self.download_file(share_url)
            if not file_content:
                return {"error": "No se pudo descargar el archivo"}
            
            # 2. Guardar temporalmente
            temp_filename = f"temp_{int(time.time())}"
            with open(temp_filename, 'wb') as f:
                f.write(file_content)
            
            # 3. Subir a mi Terabox
            upload_result = self.upload_file(temp_filename)
            if not upload_result:
                return {"error": "No se pudo subir el archivo a Terabox"}
            
            # 4. Crear nuevo enlace de compartir
            new_share_link = self.create_share_link(upload_result.get("path"))
            
            # 5. Limpiar archivo temporal
            import os
            try:
                os.remove(temp_filename)
            except:
                pass
            
            if new_share_link:
                return {
                    "success": True,
                    "original_url": share_url,
                    "new_share_url": new_share_link,
                    "message": "Archivo procesado exitosamente"
                }
            else:
                return {"error": "No se pudo crear el enlace de compartir"}
                
        except Exception as e:
            logger.error(f"Error procesando enlace: {e}")
            return {"error": f"Error interno: {str(e)}"}
    
    def validate_tokens(self) -> bool:
        """Validar que los tokens siguen siendo válidos"""
        try:
            # Hacer petición simple para verificar autenticación
            response = self._make_request("GET", "https://www.terabox.com/api/user/info")
            
            if response and response.status_code == 200:
                logger.info("Tokens válidos")
                return True
            else:
                logger.warning("Tokens pueden haber expirado")
                return False
                
        except Exception as e:
            logger.error(f"Error validando tokens: {e}")
            return False

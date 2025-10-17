"""
Bot de WhatsApp para el chatbot
"""

import json
import logging
import os
import requests
from ai_langchain import AzureOpenAIConfig, IntelligentLeadQualificationChatbot
from state_management import ConversationStateStore
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from hubspot_manager import HubSpotManager
from check_guardrails import ContentSafetyGuardrails

# ============================================================================
# CLASE PRINCIPAL DEL BOT DE WHATSAPP
# ============================================================================

class WhatsAppBot:
    def __init__(self, state_store: Optional[ConversationStateStore] = None):
        self.access_token = os.environ['WHATSAPP_ACCESS_TOKEN']
        self.phone_number_id = os.environ['PHONE_NUMBER_ID']
        self.version = os.environ['WHATSAPP_API_VERSION']
        
        # Inicializar la configuración de LangChain
        self.langchain_config = None
        self._initialize_langchain_config()
        
        # Usar el state_store proporcionado
        self.state_store = state_store

        # Una sola instancia del chatbot que manejará todos los usuarios
        # Pasar callback de envío de mensajes para que el chatbot pueda enviar directamente
        self.chatbot = IntelligentLeadQualificationChatbot(
            self.langchain_config, 
            self.state_store,
            send_message_callback=self.send_message
        )

        # Una sola instancia del guardrails
        self.guardrails = ContentSafetyGuardrails()
        
    def _initialize_langchain_config(self):
        """Inicializa la configuración de LangChain con Azure OpenAI"""
        try:
            self.langchain_config = AzureOpenAIConfig(
                endpoint=os.environ["FOUNDRY_ENDPOINT"],
                api_key=os.environ["FOUNDRY_API_KEY"],
                deployment_name="gpt-4.1-mini",
                api_version="2024-12-01-preview",
                model_name="gpt-4.1-mini"
            )
            logging.info("Configuración de LangChain inicializada correctamente")
        except Exception as e:
            logging.error(f"Error inicializando configuración de LangChain: {e}")
            raise
        
    def normalize_mexican_number(self, phone_number: str) -> str:
        """
        Normaliza un número mexicano en formato internacional para que sea aceptado por la API de WhatsApp.
        Si el número comienza con '521' (México + celular), elimina el '1' extra.
        """
        if phone_number.startswith("521") and len(phone_number) >= 12:
            return "52" + phone_number[3:]
        return phone_number
    
    def get_text_message_input(self, recipient: str, message_type: str, content: str) -> str:
        """
        Crea el payload JSON para enviar un mensaje de texto vía WhatsApp API.
        """
        normalized_recipient = self.normalize_mexican_number(recipient)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_recipient,
            "type": message_type
        }
        if message_type == "text":
            payload["text"] = {
                "preview_url": False,
                "body": content
            }
        elif message_type == "image":
            payload["image"] = {
                "id": content
            }
        elif message_type == "audio":
            payload["audio"] = {
                "id": content
            }
        elif message_type == "document":
            payload["document"] = {
                "id": content,
                "filename": "archivo"
            }
        return json.dumps(payload)
    
    def send_message(self, wa_id: str, text: str, multimedia: Dict[str, Any] = None) -> Optional[str]:
        """
        Envía un mensaje a través de WhatsApp API.
        """
        try:
            data = None
            if multimedia:
                data = self.get_text_message_input(wa_id, multimedia["type"], multimedia["multimedia_id"])
            else:
                data = self.get_text_message_input(wa_id, "text", text)

            logging.info(f"Data of message sent to WhatsApp API: {data}")
            headers = {
                "Content-type": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            }
            
            url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/messages"
            response = requests.post(url, data=data, headers=headers, timeout=10)
            response.raise_for_status()

            json_response = response.json()
            whatsapp_message_id = json_response["messages"][0]["id"]
            
            logging.info(f"Mensaje enviado exitosamente, response: {response.json()}")
            # Mensaje enviado exitosamente, response: {'messaging_product': 'whatsapp', 'contacts': [{'input': '529931340372', 'wa_id': '5219931340372'}], 'messages': [{'id': 'wamid.HBgNNTIxOTkzMTM0MDM3MhUCABEYEjNDMUE3QkFFRjBGQjMxNzBGNQA='}]}
            return whatsapp_message_id
            
        except Exception as e:
            logging.error(f"Error enviando mensaje a {wa_id}: {e}")
            return None
    
    def process_message(self, wa_id: str, message_text: str, whatsapp_message_id: str, hubspot_manager: HubSpotManager) -> None:
        """
        Procesa un mensaje entrante usando LangChain.
        El chatbot ahora envía automáticamente las respuestas por WhatsApp.
        """
        try:
            # Verificar si es un comando especial
            if message_text.lower() == "reset":
                reset_response = self._handle_reset_command(wa_id, hubspot_manager)
                # Ignorar el Id de WhatsApp porque no se guarda en la base de datos
                self.send_message(wa_id, reset_response)
                return
            elif message_text.lower() == "status":
                status_response = self._get_conversation_status(wa_id)
                # Ignorar el Id de WhatsApp porque no se guarda en la base de datos
                self.send_message(wa_id, status_response)
                return

            # Verificar si el mensaje es seguro
            safety_result = self.guardrails.check_message_safety(message_text)
            if safety_result:
                if safety_result["type"] == "invalid_conversation":
                    message_text = "(FD) " + safety_result["message"] + " (FD) Mensaje del lead: " + message_text
                else:
                    response_for_lead = "No me queda claro lo que dices. ¿Podrías explicarme mejor?"
                    # Enviar respuesta de seguridad por WhatsApp
                    whatsapp_message_id_response = self.send_message(wa_id, response_for_lead)

                    # Ids de mensajes proporcionados por WhatsApp
                    whatsapp_ids = {
                        "safety_message": whatsapp_message_id,
                        "response_for_lead": whatsapp_message_id_response
                    }
                    # Guardar mensajes de seguridad en la base de datos
                    self._save_safety_messages(wa_id, safety_result["message"], response_for_lead, whatsapp_ids)
                    return

            # Guardamos el mensaje en la base de datos
            self.state_store.add_single_message(wa_id, message_text, whatsapp_message_id, self.chatbot.state)
            
            # Procesar mensaje con LangChain (ahora envía automáticamente por WhatsApp)
            self.chatbot.send_message(message_text, whatsapp_message_id, hubspot_manager)
                
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
            error_message = "Disculpa, hubo un problema técnico. ¿Podrías repetir tu mensaje?"
            self.send_message(wa_id, error_message)

    def process_multimedia_msg(self, wa_id: str, multimedia: Dict[str, Any], whatsapp_message_id: str) -> None:
        """
        Procesa un mensaje multimedia entrante.
        Actualmente solo responde que no se soportan mensajes multimedia.
        """
        try:
            logging.info(f"Mensaje multimedia recibido de {wa_id}. Tipo: " + multimedia.get('type') + ".")
            self.state_store.add_single_message(wa_id, multimedia, whatsapp_message_id, self.chatbot.state)
        except Exception as e:
            logging.error(f"Error procesando mensaje multimedia: {e}")

    def _handle_reset_command(self, wa_id: str, hubspot_manager: HubSpotManager) -> str:
        """Maneja el comando de reset"""
        hubspot_manager.delete_contact()
        self.chatbot.load_conversation(wa_id)
        self.chatbot.reset_conversation()
        logging.info(f"Conversación reiniciada para usuario {wa_id}")
        return "Conversación reiniciada. Puedes comenzar de nuevo."
    
    def is_authorized_user(self, wa_id: str) -> bool:
        """
        Verifica si el usuario está autorizado para usar el bot.
        """
        try:
            logging.info(f"Verificando si el usuario {wa_id} está autorizado")
            authorized_ids = []
            if "RECIPIENT_WAID" in os.environ:
                authorized_ids.append(os.environ['RECIPIENT_WAID'])
            if "RECIPIENT_WAID_2" in os.environ:
                authorized_ids.append(os.environ['RECIPIENT_WAID_2'])
            if "RECIPIENT_WAID_3" in os.environ:
                authorized_ids.append(os.environ['RECIPIENT_WAID_3'])
                
            return wa_id in authorized_ids
        except Exception as e:
            logging.error(f"Error verificando si el usuario {wa_id} está autorizado: {e}")
            return False
    
    def _save_safety_messages(self, wa_id: str, safety_message: str, response_for_lead: str, whatsapp_ids: Dict[str, str]) -> None:
        """
        Guarda los mensajes de seguridad en la base de datos usando _append_messages.
        Guarda el mensaje de seguridad del bot y la respuesta genérica.
        """
        try:
            # Asegurar que el usuario tenga una conversación cargada
            self.chatbot.save_conversation()

            # Preparar los dos mensajes a guardar
            safety_messages = [
                {
                    "content": safety_message,
                    "role": "user",
                    "whatsapp_message_id": whatsapp_ids["safety_message"],
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # Se generará automáticamente en _append_messages
                },
                {
                    "content": response_for_lead,
                    "role": "bot",
                    "whatsapp_message_id": whatsapp_ids["response_for_lead"],
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # Se generará automáticamente en _append_messages
                }
            ]
            
            # Usar _append_messages para guardar los mensajes
            self.state_store._append_messages(wa_id, safety_messages)
            logging.info(f"Mensajes de seguridad guardados para usuario {wa_id}")
            
        except Exception as e:
            logging.error(f"Error guardando mensajes de seguridad para usuario {wa_id}: {e}")

    def _get_conversation_status(self, wa_id: str) -> str:
        """Obtiene el estado actual de la conversación del usuario."""
        try:
            self.chatbot.load_conversation(wa_id)
            state = self.chatbot.state
            return f"""📊 ESTADO DE CONVERSACIÓN:
        🤖 API: LangChain (IntelligentLeadQualificationChatbot)
        👤 Usuario: {wa_id}
        ✅ Completada: {'Sí' if state.get('completed', False) else 'No'}
        📝 Nombre: {state.get('nombre', 'No especificado')}
        👤 Apellido: {state.get('apellido', 'No especificado')}
        🔧 Tipo maquinaria: {state.get('tipo_maquinaria', 'No especificado')}
        🔍 Detalles maquinaria: {state.get('detalles_maquinaria', 'No especificado')}
        💼 Nombre empresa: {state.get('nombre_empresa', 'No especificado')}
        💼 Giro empresa: {state.get('giro_empresa', 'No especificado')}
        🌐 Sitio web: {state.get('sitio_web', 'No especificado')}
        💼 Tipo de uso: {state.get('uso_empresa_o_venta', 'No especificado')}
        📧 Correo: {state.get('correo', 'No especificado')}
        📱 Teléfono: {state.get('telefono', 'No especificado')}
        📍 Lugar requerimiento: {state.get('lugar_requerimiento', 'No especificado')}
        💬 Total mensajes: {len(state.get('messages', []))}
        👤 Conversación mode: {state.get('conversation_mode', 'No especificado')}
        """
        except Exception as e:
            logging.error(f"Error obteniendo estado de conversación: {e}")
            return f"❌ Error obteniendo estado: {str(e)}"
"""
Bot de WhatsApp para el chatbot
"""

import json
import logging
import os
import requests
from ai_langchain import AzureOpenAIConfig, IntelligentLeadQualificationChatbot
from state_management import ConversationStateStore
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from odoo_manager import OdooManager
from check_guardrails import ContentSafetyGuardrails

# ============================================================================
# CLASE PRINCIPAL DEL BOT DE WHATSAPP
# ============================================================================

class WhatsAppBot:
    def __init__(self, state_store: Optional[ConversationStateStore] = None, cosmos_client: Any = None, db_name: str = None):
        self.access_token = os.environ['WHATSAPP_ACCESS_TOKEN']
        self.phone_number_id = os.environ['PHONE_NUMBER_ID']
        self.version = os.environ['WHATSAPP_API_VERSION']
        
        # Inicializar la configuración de LangChain
        self.langchain_config = None
        self._initialize_langchain_config()
        
        # Usar el state_store proporcionado
        self.state_store = state_store
        
        # Cliente Cosmos para servicios que lo requieran
        self.cosmos_client = cosmos_client
        self.db_name = db_name

        # Una sola instancia del chatbot que manejará todos los usuarios
        # Pasar callback de envío de mensajes para que el chatbot pueda enviar directamente
        # Pasar cliente cosmos para servicios internos
        self.chatbot = IntelligentLeadQualificationChatbot(
            self.langchain_config, 
            self.state_store,
            send_message_callback=self.send_message,
            send_pdf_callback=self.send_pdf_quotation,
            cosmos_client=self.cosmos_client,
            db_name=self.db_name
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

    def get_template_text(self, template_name: str) -> str:
        """
        Obtiene el texto de una plantilla de WhatsApp.
        """
        lead_name = self.chatbot.state.get("nombre", "") if self.chatbot.state.get("nombre") else ""
        lead_machine_type = self.chatbot.state.get("tipo_maquinaria", "") if self.chatbot.state.get("tipo_maquinaria") else "nuestra maquinaria"

        if template_name == "notificacion_de_leads":
            return f"Hola {lead_name}, mi nombre es Alphi, asesor comercial de Alpha C. Me pongo en contacto contigo para dar seguimiento a tu interés en la siguiente maquinaria: {lead_machine_type}. Para continuar con tu solicitud, ¿me podrías confirmar si la maquinaria la requieres para venta o uso propio?"
        elif template_name == "seguimiento_conversacion":
            return f"""Hola {lead_name}, intentamos comunicarnos contigo para brindarte la información del equipo que solicitaste.
            ¿Sigues interesado en recibir la información o una cotización?
            Quedamos a tus órdenes para cotizar o resolver cualquier duda que tengas."""

    def get_template_components(self, wa_id: str, template_name: str) -> List[Dict[str, Any]]:
        """
        Obtiene los componentes de una plantilla de WhatsApp.
        """
        self.chatbot.load_conversation(wa_id)
        lead_name = self.chatbot.state.get("nombre", "") if self.chatbot.state.get("nombre") else ""
        lead_machine_type = self.chatbot.state.get("tipo_maquinaria", "") if self.chatbot.state.get("tipo_maquinaria") else "nuestra maquinaria"
        logging.info(f"Nombre de lead: {lead_name}, Tipo de maquinaria: {lead_machine_type}")
        
        if template_name == "notificacion_de_leads":
            return [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": lead_name},
                        {"type": "text", "text": lead_machine_type}
                    ]
                }
            ]
        elif template_name == "seguimiento_conversacion":
            return [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": lead_name}
                    ]
                }
            ]
        else:
            return None
    
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
            # content can be media_id string or dict with id+filename
            if isinstance(content, dict):
                payload["document"] = {
                    "id": content["id"],
                    "filename": content.get("filename", "archivo")
                }
            else:
                payload["document"] = {
                    "id": content,
                    "filename": "archivo"
                }
        elif message_type == "template":
            payload["template"] = {
                "name": content,
                "language": {
                    "code": "es_MX"
                },
                "components": self.get_template_components(recipient, content)
            }
        return json.dumps(payload)
    
    def send_message(self, wa_id: str, text: str, multimedia: Dict[str, Any] = None, template_name: str = None) -> Optional[str]:
        """
        Envía un mensaje a través de WhatsApp API.
        """
        try:
            data = None
            if multimedia:
                data = self.get_text_message_input(wa_id, multimedia["type"], multimedia["multimedia_id"])
            elif template_name:
                data = self.get_text_message_input(wa_id, "template", template_name)
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
            return whatsapp_message_id + "___" + self.get_template_text(template_name) if template_name else whatsapp_message_id
            
        except Exception as e:
            logging.error(f"Error enviando mensaje a {wa_id}: {e}")
            return None
    
    def upload_media(self, file_bytes: bytes, mime_type: str, filename: str) -> Optional[str]:
        """
        Uploads a file to the WhatsApp Media API.
        Returns the media_id on success, None on failure.
        """
        try:
            url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/media"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
            }
            files = {
                "file": (filename, file_bytes, mime_type),
            }
            data = {
                "messaging_product": "whatsapp",
                "type": mime_type,
            }
            
            response = requests.post(url, headers=headers, files=files, data=data, timeout=30)
            response.raise_for_status()
            
            media_id = response.json().get("id")
            logging.info(f"[PDF] Media uploaded successfully. media_id: {media_id}")
            return media_id
            
        except Exception as e:
            logging.error(f"[PDF] Error uploading media: {e}")
            return None

    def send_pdf_quotation(self, wa_id: str, pdf_bytes: bytes, filename: str) -> Optional[str]:
        """
        Uploads a PDF and sends it as a WhatsApp document message.
        Returns the WhatsApp message ID on success, None on failure.
        """
        try:
            # 1. Upload PDF to WhatsApp Media API
            media_id = self.upload_media(pdf_bytes, "application/pdf", filename)
            if not media_id:
                logging.error(f"[PDF] Failed to upload PDF for {wa_id}")
                return None
            
            # 2. Send as document message using existing infrastructure
            document_content = {
                "id": media_id,
                "filename": filename
            }
            normalized_recipient = self.normalize_mexican_number(wa_id)
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": normalized_recipient,
                "type": "document",
                "document": document_content
            }
            
            headers = {
                "Content-type": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            }
            
            url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/messages"
            response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            
            whatsapp_message_id = response.json()["messages"][0]["id"]
            logging.info(f"[PDF] Quotation PDF sent to {wa_id}. message_id: {whatsapp_message_id}")

            # 3. Registrar el documento en la conversación para que la web lo muestre
            self._register_outgoing_document(wa_id, media_id, whatsapp_message_id)

            return whatsapp_message_id

        except Exception as e:
            logging.error(f"[PDF] Error sending PDF to {wa_id}: {e}")
            return None

    def _register_outgoing_document(self, wa_id: str, media_id: str, whatsapp_message_id: str) -> None:
        """
        Registra en Cosmos el documento que acaba de enviar el bot, con el mismo
        esquema que usan los documentos entrantes del lead y los que manda el
        agente desde la web, para que la plataforma web lo renderice igual.
        """
        try:
            if not self.state_store:
                logging.warning(f"[PDF] Sin state_store: el documento enviado a {wa_id} no se registró en la conversación")
                return

            multimedia = {
                "type": "document",
                "multimedia_id": media_id
            }

            self.state_store.add_outgoing_multimedia_message(
                wa_id,
                multimedia,
                whatsapp_message_id,
                self.chatbot.state,
                sender="bot"
            )
        except Exception as e:
            # El PDF ya llegó al lead; un fallo aquí no debe romper el envío
            logging.error(f"[PDF] Error registrando el documento enviado a {wa_id}: {e}")

    def process_message(self, wa_id: str, message_text: str, whatsapp_message_id: str, odoo_manager: Optional[OdooManager] = None) -> None:
        """
        Procesa un mensaje entrante usando LangChain.
        El chatbot ahora envía automáticamente las respuestas por WhatsApp.
        """
        try:
            # Verificar si es un comando especial
            if message_text.lower() == "reset":
                reset_response = self._handle_reset_command(wa_id)
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
                if safety_result["type"] == "invalid_conversation" or safety_result["type"] == "content_safety":
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
            self.chatbot.send_message(message_text, whatsapp_message_id, odoo_manager)
                
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
            error_message = "Disculpa, hubo un problema técnico. ¿Podrías repetir tu mensaje?"
            self.send_message(wa_id, error_message)

    def process_multimedia_msg(self, wa_id: str, multimedia: Dict[str, Any], whatsapp_message_id: str) -> None:
        """
        Procesa un mensaje multimedia entrante.
        Registra el mensaje e invoca al LLM enviando un texto simulado.
        """
        try:
            logging.info(f"Mensaje multimedia recibido de {wa_id}. Tipo: " + multimedia.get('type') + ".")
            self.state_store.add_single_message(wa_id, multimedia, whatsapp_message_id, self.chatbot.state)
            
            # Generar texto descriptivo para el modelo
            msg_type = multimedia.get('type', 'documento')
            # El caption es lo que el lead ESCRIBIÓ junto al archivo y suele traer
            # el dato clave ("el martillo que piden es un Atlas Copco TEX12PE").
            # Antes se descartaba y el bot respondía como si no hubiera leído nada.
            caption = (multimedia.get('caption') or "").strip()

            if msg_type == "document":
                # Usar esta frase específica para ayudar a que la extracción comprenda que llegó la constancia
                simulated_text = "Aquí está el archivo PDF adjunto."
                if caption:
                    simulated_text += f" {caption}"
            elif caption:
                simulated_text = caption
            else:
                simulated_text = f"[Archivo {msg_type} adjunto recibido]"

            # Procesar el mensaje con LangChain para generar la respuesta correspondiente
            self.chatbot.send_message(simulated_text, whatsapp_message_id)
            
        except Exception as e:
            logging.error(f"Error procesando mensaje multimedia: {e}")

    def _handle_reset_command(self, wa_id: str) -> str:
        """Maneja el comando de reset (solo para pruebas, ver AGENTS.md)"""
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
            if "RECIPIENT_WAID_4" in os.environ:
                authorized_ids.append(os.environ['RECIPIENT_WAID_4'])
            if "RECIPIENT_WAID_5" in os.environ:
                authorized_ids.append(os.environ['RECIPIENT_WAID_5'])
            if "RECIPIENT_WAID_6" in os.environ:
                authorized_ids.append(os.environ['RECIPIENT_WAID_6'])
                
            return wa_id in authorized_ids or True
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
            return self.chatbot.get_status_message()
        except Exception as e:
            logging.error(f"Error obteniendo estado de conversación: {e}")
            return f"❌ Error obteniendo estado: {str(e)}"
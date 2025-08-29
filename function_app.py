"""
Chatbot automatizado para calificación de leads de maquinaria ligera
Integra WhatsApp + Azure OpenAI GPT-4.1-mini + LangChain
Azure Function para procesar webhooks de WhatsApp
"""

import azure.functions as func
import logging
import os
import json
from whatsapp_bot import WhatsAppBot
from state_management import InMemoryStateStore, CosmosDBStateStore
from azure.cosmos import CosmosClient
from datetime import datetime, timezone, timedelta

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="whatsappbot1")
def whatsappbot1(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main Azure Function entry point for WhatsApp webhook.
    Handles both GET (verification) and POST (message) requests.
    """
    # logging.info('Python HTTP trigger function processed a request')
    # logging.info(f"req.method: {req.method}")

    if req.method == 'POST':
        return handle_message(req)
    else:
        return verify(req)

def verify(req):
    """
    Handles WhatsApp webhook verification (GET requests).
    This is called when you first set up the webhook in Meta Developer Console.
    """
    # logging.info("verify - Start")

    verify_token = os.environ["VERIFY_TOKEN"]
    # if verify_token:
    #     logging.info(f"verify_token: {verify_token}")
    # else:
    #     logging.info("VERIFY_TOKEN Empty")

    # if req.params:
    #     logging.info(req.params)

    # Parse params from the webhook verification request
    mode = req.params.get("hub.mode")
    token = req.params.get("hub.verify_token")
    challenge = req.params.get("hub.challenge")
    # logging.info(f"mode: {mode}, token: {token}, challenge: {challenge}")

    # Check if a token and mode were sent
    if mode and token:
        # Check the mode and token sent are correct
        if mode == "subscribe" and token == verify_token:
            # Respond with 200 OK and challenge token from the request
            logging.info("WEBHOOK_VERIFIED")
            return func.HttpResponse(challenge, status_code=200)
        else:
            # Responds with '403 Forbidden' if verify tokens do not match
            logging.info("VERIFICATION_FAILED")
            return func.HttpResponse("Verification failed", status_code=403)
    else:
        # Responds with '400 Bad Request' if verify tokens do not match
        logging.info("MISSING_PARAMETER")
        return func.HttpResponse("Missing parameters", status_code=400)
    
def create_whatsapp_bot() -> WhatsAppBot:
    """
    Factory method para crear una instancia fresca de WhatsAppBot por request.
    Mejora: Elimina estado global y garantiza aislamiento entre requests.
    """
    try:
        # Crear el state store apropiado para el entorno
        state_store = create_state_store()
        
        # Crear instancia fresca del bot
        bot = WhatsAppBot(state_store=state_store)
        logging.info("WhatsApp bot creado exitosamente para request")
        
        return bot
        
    except Exception as e:
        logging.error(f"Error creando WhatsApp bot: {e}")
        raise

def create_state_store():
    """
    Factory method para crear el state store apropiado según el entorno.
    """
    try:
        # Intentar usar Cosmos DB si las variables de entorno están configuradas
        if all(key in os.environ for key in ["COSMOS_CONNECTION_STRING", "COSMOS_DB_NAME", "COSMOS_CONTAINER_NAME"]):
            cosmos_client = CosmosClient.from_connection_string(os.environ["COSMOS_CONNECTION_STRING"])
            db_name = os.environ["COSMOS_DB_NAME"]
            container_name = os.environ["COSMOS_CONTAINER_NAME"]
            
            logging.info("Usando CosmosDBStateStore para producción")
            return CosmosDBStateStore(cosmos_client, db_name, container_name)
        else:
            # Fallback a InMemoryStateStore para desarrollo
            logging.info("Usando InMemoryStateStore para desarrollo")
            return InMemoryStateStore()
            
    except Exception as e:
        logging.warning(f"Error configurando Cosmos DB, usando InMemoryStateStore: {e}")
        return InMemoryStateStore()

def handle_message(req):
    """
    Handles incoming WhatsApp messages (POST requests).
    Processes the message and sends appropriate responses.
    """

    body = req.get_json()
    # logging.info(f"request body: {body}")

    # Check if it's a WhatsApp status update (ignore these)
    if (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("statuses")
    ):
        logging.info("Received a WhatsApp status update.")
        return func.HttpResponse("OK", status_code=200)

    try:
        if is_valid_whatsapp_message(body):
            # Crear instancia fresca del bot para este request
            whatsapp_bot = create_whatsapp_bot()
            process_whatsapp_message(body, whatsapp_bot)

            # logging.info(f"Webhook body completo: {json.dumps(body, indent=2)}")
            return func.HttpResponse("OK", status_code=200)
        else:
            # if the request is not a WhatsApp API event, return an error
            logging.error("Not a WhatsApp API event")
            return func.HttpResponse("Not a WhatsApp API event", status_code=404)
    except json.JSONDecodeError:
        logging.error("Failed to decode JSON")
        return func.HttpResponse("Invalid JSON provided", status_code=400)

def is_valid_whatsapp_message(body):
    """
    Check if the incoming webhook event has a valid WhatsApp message structure.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )

def process_whatsapp_message(body, whatsapp_bot: WhatsAppBot):
    """
    Processes the WhatsApp message and sends appropriate response.
    Uses the conversation manager and WhatsApp bot for intelligent responses.
    """

    # Extract sender information
    wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
    # name = body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]
    logging.info(f"wa_id: {wa_id}")
    # logging.info(f"name: {name}")
    # logging.info(f"Saved wa_id: {os.environ['RECIPIENT_WAID']}") # Debugging line

    # Safeguard against unauthorized users
    if not whatsapp_bot.is_authorized_user(wa_id):
        logging.error("Unauthorized user!!!")
        return

    # Extract message content
    message = body["entry"][0]["changes"][0]["value"]["messages"][0]
    logging.info(f"message: {message}")

    if "text" in message:
        # Handle text messages with AI conversation manager
        # logging.info(f"Message Type: TEXT")
        message_body = message["text"]["body"]
        # logging.info(f"message_body: {message_body}")
        
        # Verificar timeout de agente antes de procesar
        timeout_occurred = check_agent_timeout(wa_id, whatsapp_bot)
        if timeout_occurred:
            logging.info(f"Timeout de agente detectado para {wa_id}, regresando a modo bot")
        
        # Cargar estado actual para verificar modo
        whatsapp_bot.chatbot.load_conversation(wa_id)
        current_mode = whatsapp_bot.chatbot.state.get("conversation_mode", "bot")
        
        if current_mode == "agente":
            # Modo agente: solo ejecutar slot-filling, NO enviar respuesta automática
            logging.info(f"Modo agente activo para {wa_id}, solo ejecutando slot-filling")
            try:
                # Ejecutar slot-filling usando el contexto del último mensaje (agente o bot)
                whatsapp_bot.chatbot.send_message(message_body, user_id=wa_id)
                # Nota: send_message ejecuta slot-filling pero en modo agente no genera respuesta automática
                logging.info(f"Slot-filling ejecutado para mensaje en modo agente: {wa_id}")
            except Exception as e:
                logging.error(f"Error ejecutando slot-filling en modo agente: {e}")
        else:
            # Modo bot: procesar normalmente con respuesta automática
            logging.info(f"Modo bot activo para {wa_id}, procesando normalmente")
            try:
                response = whatsapp_bot.process_message(wa_id, message_body)
                whatsapp_bot.send_message(wa_id, response)
            except Exception as e:
                logging.error(f"Error processing message: {e}")
                error_message = "Disculpa, hubo un problema técnico. ¿Podrías repetir tu mensaje?"
                whatsapp_bot.send_message(wa_id, error_message)
        
    else:
        # Handle non-text messages with a help message
        logging.info(f"Message Type: NON-TEXT")
        help_text = "¡Hola! Solo puedo procesar mensajes de texto. Por favor, envíame un mensaje de texto y te responderé con información sobre maquinaria."
        whatsapp_bot.send_message(wa_id, help_text)

@app.route(route="agent-message", methods=["POST"])
def agent_message(req: func.HttpRequest) -> func.HttpResponse:
    """
    Endpoint para recibir mensajes del agente humano.
    Procesa el mensaje, ejecuta slot-filling y envía al lead vía WhatsApp.
    """
    logging.info('Endpoint agent-message activado')
    
    try:
        # Validar que sea POST
        if req.method != 'POST':
            return func.HttpResponse("Method not allowed", status_code=405)
        
        # Obtener datos del request
        body = req.get_json()
        if not body:
            return func.HttpResponse("Invalid JSON", status_code=400)
        
        # Validar campos requeridos
        wa_id = body.get("wa_id")
        message = body.get("message")
        
        if not wa_id or not message:
            return func.HttpResponse("Missing wa_id or message", status_code=400)
        
        # Crear instancia del bot
        whatsapp_bot = create_whatsapp_bot()
        
        # Procesar mensaje del agente
        success = process_agent_message(wa_id, message, whatsapp_bot)
        
        if success:
            return func.HttpResponse("Agent message processed successfully", status_code=200)
        else:
            return func.HttpResponse("Error processing agent message", status_code=500)
            
    except Exception as e:
        logging.error(f"Error en endpoint agent-message: {e}")
        return func.HttpResponse("Internal server error", status_code=500)

def process_agent_message(wa_id: str, message: str, whatsapp_bot: WhatsAppBot) -> bool:
    """
    Procesa un mensaje del agente humano.
    1. Cambia el modo de conversación a 'agente' si no estaba ya
    2. Agrega el mensaje del agente al historial
    3. Envía el mensaje al lead vía WhatsApp
    4. NO ejecuta slot-filling en mensajes del agente
    """
    try:
        logging.info(f"Procesando mensaje de agente para wa_id: {wa_id}")
        
        # Cargar conversación actual
        whatsapp_bot.chatbot.load_conversation(wa_id)
        current_state = whatsapp_bot.chatbot.state
        
        # Cambiar a modo agente si no estaba ya
        if current_state.get("conversation_mode") != "agente":
            current_state["conversation_mode"] = "agente"
            logging.info(f"Modo cambiado a 'agente' para usuario {wa_id}")
        
        # Agregar mensaje del agente al historial
        current_state["messages"].append({
            "role": "assistant",
            "content": message,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sender": "agente"
        })
        
        # Guardar estado actualizado
        whatsapp_bot.chatbot.save_conversation()
        
        # Enviar mensaje al lead vía WhatsApp
        success = whatsapp_bot.send_message(wa_id, message)
        
        if success:
            logging.info(f"Mensaje de agente enviado exitosamente a {wa_id}")
        else:
            logging.error(f"Error enviando mensaje de agente a {wa_id}")
        
        return success
        
    except Exception as e:
        logging.error(f"Error procesando mensaje de agente: {e}")
        return False

def check_agent_timeout(wa_id: str, whatsapp_bot: WhatsAppBot) -> bool:
    """
    Verifica si han pasado 30 minutos desde el último mensaje del agente.
    Si es así, cambia el modo de conversación de vuelta a 'bot'.
    Retorna True si se cambió el modo, False si no.
    """
    try:
        # Cargar conversación
        whatsapp_bot.chatbot.load_conversation(wa_id)
        current_state = whatsapp_bot.chatbot.state
        
        # Solo verificar si está en modo agente
        if current_state.get("conversation_mode") != "agente":
            return False
        
        # Buscar el último mensaje del agente
        last_agent_message_time = None
        
        for msg in reversed(current_state.get("messages", [])):
            if msg.get("sender") == "agente":
                last_agent_message_time = msg.get("timestamp")
                break
        
        if not last_agent_message_time:
            # No hay mensajes del agente, cambiar a bot
            current_state["conversation_mode"] = "bot"
            whatsapp_bot.chatbot.save_conversation()
            logging.info(f"Modo cambiado a 'bot' para {wa_id} (no hay mensajes de agente)")
            return True
        
        # Verificar si han pasado 30 minutos
        try:
            last_time = datetime.fromisoformat(last_agent_message_time.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            time_diff = now - last_time
            
            if time_diff > timedelta(minutes=30):
                current_state["conversation_mode"] = "bot"
                whatsapp_bot.chatbot.save_conversation()
                logging.info(f"Modo cambiado a 'bot' para {wa_id} (timeout de 30 minutos)")
                return True
                
        except Exception as e:
            logging.error(f"Error parseando timestamp: {e}")
            
        return False
        
    except Exception as e:
        logging.error(f"Error verificando timeout de agente: {e}")
        return False
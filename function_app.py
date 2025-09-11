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
from hubspot_manager import HubSpotManager

# Silencia solo los logs detallados del SDK de Azure Cosmos y del pipeline HTTP
logging.getLogger("azure.cosmos").setLevel(logging.ERROR)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.ERROR)

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

    verify_token = os.environ["VERIFY_TOKEN"]

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
    logging.info(f"request body: {body}")

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
    phone_number = message["from"] # Número de WhatsApp del lead empezando por 521
    logging.info(f"message: {message}")

    if "text" in message:
        # Extraer el contenido en texto del mensaje
        message_body = message["text"]["body"]
        # Extraer el id del mensaje asignado por WhatsApp
        whatsapp_message_id = message["id"]

        # Cargar conversación
        whatsapp_bot.chatbot.load_conversation(wa_id)

        # Verificar que en los ids de los últimos 3 mensajes no esté el id del mensaje actual
        # Esto es para evitar procesar mensajes duplicados
        # En algunas ocasiones, WhatsApp envía mensajes duplicados (parece que cuando un guardrail se tarda en procesar, envía el mismo mensaje duplicado)
        last_3_messages = whatsapp_bot.chatbot.state.get("messages", [])[-3:]
        if whatsapp_message_id in [msg.get("whatsapp_message_id") for msg in last_3_messages]:
            logging.info(f"Mensaje duplicado detectado: {whatsapp_message_id}")
            return

        # Crear instancia de HubSpotManager
        hubspot_manager = HubSpotManager(os.environ["HUBSPOT_ACCESS_TOKEN"])

        # Actualizar número de WhatsApp en estado si no se ha guardado
        # Esto solo se ejecuta cuando se inicia una conversación
        current_state = whatsapp_bot.chatbot.state
        if not current_state.get("telefono"):
            # Normalizar número de WhatsApp
            phone_number = whatsapp_bot.normalize_mexican_number(phone_number)
            current_state["telefono"] = phone_number
            current_state["hubspot_contact_id"] = hubspot_manager.create_contact(wa_id, phone_number)
        else:
            hubspot_manager.contact_id = current_state["hubspot_contact_id"]
        
        # Verificar timeout de agente antes de procesar
        timeout_occurred = check_agent_timeout(wa_id, whatsapp_bot)
        if timeout_occurred:
            logging.info(f"Timeout de agente detectado para {wa_id}, regresando a modo bot")

        # Ejecutar slot-filling usando el contexto del último mensaje (agente o bot)
        # Ahora el chatbot envía automáticamente las respuestas por WhatsApp
        whatsapp_bot.process_message(wa_id, message_body, whatsapp_message_id, hubspot_manager)
        
    else:
        # TODO: Esto se debería registrar en Cosmos DB
        # Handle non-text messages with a help message
        logging.info(f"Message Type: NON-TEXT")
        help_text = "¡Hola! Solo puedo procesar mensajes de texto. Por favor, envíame un mensaje de texto y te responderé con información sobre maquinaria."
        whatsapp_bot.send_message(wa_id, help_text)

@app.route(route="agent-message", methods=["POST"])
def agent_message(req: func.HttpRequest) -> func.HttpResponse:
    """
    Endpoint para recibir mensajes del agente humano.
    Procesa el mensaje y envía al lead vía WhatsApp.
    No ejecuta slot-filling ni guarda el estado ni mensaje en Cosmos DB.
    El mensaje ya se guardó en Cosmos DB por la otra funcion.
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
        
        # Crear instancia de WhatsAppBot
        whatsapp_bot = create_whatsapp_bot()
        
        # Enviar mensaje al lead vía WhatsApp
        whatsapp_message_id = whatsapp_bot.send_message(wa_id, message)

        if whatsapp_message_id:
            # Regresar el ID de WhatsApp del mensaje
            return func.HttpResponse(whatsapp_message_id, status_code=200)
        else:
            return func.HttpResponse("Error sending agent message", status_code=500)
            
    except Exception as e:
        logging.error(f"Error en endpoint agent-message: {e}")
        return func.HttpResponse("Internal server error", status_code=500)

def check_agent_timeout(wa_id: str, whatsapp_bot: WhatsAppBot) -> bool:
    """
    Verifica si han pasado 30 minutos desde el último mensaje del agente.
    Si es así, cambia el modo de conversación de vuelta a 'bot'.
    Retorna True si se cambió el modo, False si no.
    """
    try:
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
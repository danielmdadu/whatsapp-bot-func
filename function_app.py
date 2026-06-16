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
    logging.info('NUEVA HTTP REQUEST: whatsappbot1')
    logging.info(f"req.method: {req.method}")

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
        # 1. Crear el state store apropiado para el entorno
        state_store = create_state_store()
        
        # 2. Inicializar servicios de Maquinaria e Inventario con conexión a DB si está disponible
        cosmos_client = None
        db_name = None
        
        # Reusar el cliente si ya se creó en create_state_store (mejora: optimizar esto)
        if isinstance(state_store, CosmosDBStateStore):
            cosmos_client = state_store.cosmos_client
            db_name = state_store.database_name
        elif all(key in os.environ for key in ["COSMOS_CONNECTION_STRING", "COSMOS_DB_NAME"]):
             # Si no estamos usando CosmosDBStateStore pero queremos cargar datos de DB (ej: desarrollo local)
             try:
                cosmos_client = CosmosClient.from_connection_string(os.environ["COSMOS_CONNECTION_STRING"])
                db_name = os.environ["COSMOS_DB_NAME"]
             except Exception:
                 pass

        # Inicializar e inyectar en las variables globales (o pasar al bot si refactorizamos WhatsAppBot)
        
        # IMPORTANTE: Aquí actualizamos las instancias globales que usan machinery_config_service e inventory_service
        # Esto es un patrón temporal hasta que WhatsAppBot acepte inyección de dependencias completa
        from maquinaria_config import machinery_config_service
        
        # Re-inicializar servicios con el cliente
        machinery_config_service.__init__(cosmos_client, db_name)
        
        # Nota: InventoryService se instancia dentro de IntelligentResponseGenerator usualmente, 
        # pero para que use la DB necesitamos pasarle el cliente.
        # Esto requiere que WhatsAppBot -> IntelligentLeadQualificationChatbot -> IntelligentResponseGenerator 
        # acepten la inyección.
        
        # Por ahora, vamos a pasar los servicios al constructor de WhatsAppBot (ver siguientes pasos)
        
        # Crear instancia fresca del bot
        # Pasamos el cliente para que el bot pueda propagarlo
        bot = WhatsAppBot(state_store=state_store, cosmos_client=cosmos_client, db_name=db_name)
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
            # No hay mensajes del agente, pero mantenemos el modo agente
            logging.info(f"Modo mantenido en 'agente' para {wa_id} (no hay mensajes de agente)")
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

def process_multimedia_message(wa_id: str, message_details: dict, whatsapp_bot: WhatsAppBot):
    """
    Processes the WhatsApp multimedia message and sends appropriate response.
    """
    try:
        multimedia = {}

        # TODO: considerar todos los tipos "image", "video", "audio", "document", "sticker", "location", "contacts"
        message_type = message_details.get("type")

        multimedia["type"] = message_type

        logging.info(f"Message Type: {message_type}")
        message_id = message_details.get("id")

        multimedia_details = message_details.get(message_type, {})
        logging.info(f"Detalles del mensaje multimedia: {message_details}")

        multimedia_id = multimedia_details.get("id")
        multimedia["multimedia_id"] = multimedia_id
        if "caption" in multimedia_details:
            caption = multimedia_details.get("caption")
            multimedia["caption"] = caption

        whatsapp_bot.process_multimedia_msg(wa_id, multimedia, message_id)
    except Exception as e:
        logging.error(f"Error procesando mensaje multimedia: {e}")
        return func.HttpResponse("Internal server error", status_code=500)
        
def process_whatsapp_message(body, whatsapp_bot: WhatsAppBot):
    """
    Processes the WhatsApp message and sends appropriate response.
    Uses the conversation manager and WhatsApp bot for intelligent responses.
    """
    try:
        message_info = body["entry"][0]["changes"][0]["value"]

        # Extraer el wa_id del lead
        wa_id = message_info["contacts"][0]["wa_id"]
        logging.info(f"wa_id del lead: {wa_id}")

        # Verificar que quien manda el mensaje esté autorizado
        # TODO: Eliminar en producción
        if not whatsapp_bot.is_authorized_user(wa_id):
            logging.info(f"wa_id no autorizado: {wa_id}")
            logging.error("Unauthorized user!!!")
            return

        # Extraer el contenido del mensaje
        message_details = message_info["messages"][0]
        logging.info(f"Detalles del mensaje: {message_details}")
        phone_number = message_details["from"] # Número de WhatsApp del lead empezando por 521

        # Cargar conversación
        whatsapp_bot.chatbot.load_conversation(wa_id)

        logging.info(f"Conversación cargada para usuario {wa_id}")

        if "text" in message_details:
            # Extraer el contenido en texto del mensaje
            message_text = message_details["text"]["body"]
            # Extraer el id del mensaje asignado por WhatsApp
            whatsapp_message_id = message_details["id"]

            # Actualizar número de WhatsApp en estado si no se ha guardado
            # Esto solo se ejecuta cuando se inicia una conversación
            current_state = whatsapp_bot.chatbot.state

            # Verificar que en los ids de los últimos 3 mensajes no esté el id del mensaje actual
            # Esto es para evitar procesar mensajes duplicados
            # En algunas ocasiones, WhatsApp envía mensajes duplicados (parece que cuando un guardrail se tarda en procesar, envía el mismo mensaje duplicado)
            last_3_messages = current_state.get("messages", [])[-3:]
            if whatsapp_message_id in [msg.get("whatsapp_message_id") for msg in last_3_messages]:
                logging.info(f"Mensaje duplicado detectado: {whatsapp_message_id}")
                return

            logging.info(f"Mensaje duplicado no detectado: {whatsapp_message_id}")

            # Crear instancia de HubSpotManager
            hubspot_manager = HubSpotManager(os.environ["HUBSPOT_ACCESS_TOKEN"])

            logging.info(f"HubSpotManager creado para usuario {wa_id}")

            if not current_state.get("telefono"):
                # Normalizar número de WhatsApp
                phone_number = whatsapp_bot.normalize_mexican_number(phone_number)
                current_state["telefono"] = phone_number
                current_state["hubspot_contact_id"] = hubspot_manager.create_contact(wa_id, phone_number)
            else:
                hubspot_manager.contact_id = current_state["hubspot_contact_id"]
            
            # -------------------
            # Función desactivada para desplegar en producción
            # -------------------
            # Verificar timeout de agente antes de procesar
            # timeout_occurred = check_agent_timeout(wa_id, whatsapp_bot)
            # if timeout_occurred:
            #     logging.info(f"Timeout de agente detectado para {wa_id}, regresando a modo bot")
            # -------------------

            # Ejecutar slot-filling usando el contexto del último mensaje (agente o bot)
            # Ahora el chatbot envía automáticamente las respuestas por WhatsApp
            whatsapp_bot.process_message(wa_id, message_text, whatsapp_message_id, hubspot_manager)
            
        else:
            # TODO: Esto se debería registrar en Cosmos DB
            # Handle non-text messages with a help message
            logging.info(f"Message Type: NON-TEXT")
            process_multimedia_message(wa_id, message_details, whatsapp_bot)

    except Exception as e:
        logging.error(f"Error procesando mensaje: {e}")
        return func.HttpResponse("Internal server error", status_code=500)

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

        # Obtener el campo de multimedia
        multimedia = body.get("multimedia")
        logging.info(f"Multimedia: {multimedia}")

        template_name = body.get("template_name")
        logging.info(f"Template Name: {template_name}")
        
        if not wa_id:
            return func.HttpResponse("Missing wa_id", status_code=400)
        
        # Crear instancia de WhatsAppBot
        whatsapp_bot = create_whatsapp_bot()
        
        # Enviar mensaje al lead vía WhatsApp
        whatsapp_message_id = whatsapp_bot.send_message(wa_id, message, multimedia, template_name)

        if whatsapp_message_id:
            # Regresar el ID de WhatsApp del mensaje
            return func.HttpResponse(whatsapp_message_id, status_code=200)
        else:
            return func.HttpResponse("Error sending agent message", status_code=500)
            
    except Exception as e:
        logging.error(f"Error en endpoint agent-message: {e}")
        return func.HttpResponse("Internal server error", status_code=500)

@app.route(route="start-bot-mode", methods=["POST"])
def start_bot_mode(req: func.HttpRequest) -> func.HttpResponse:
    """
    Endpoint para activar el modo bot y procesar el último mensaje del lead.
    Verifica si el último mensaje fue enviado por el lead y, si es así,
    lo procesa y genera una respuesta contextual.
    """
    logging.info('Endpoint start-bot-mode activado')
    
    try:
        # Validar que sea POST
        if req.method != 'POST':
            return func.HttpResponse("Method not allowed", status_code=405)
        
        # Obtener datos del request
        body = req.get_json()
        if not body:
            return func.HttpResponse("Invalid JSON", status_code=400)
        
        # Validar campo requerido
        wa_id = body.get("wa_id")
        if not wa_id:
            return func.HttpResponse("Missing wa_id", status_code=400)
        
        logging.info(f"Procesando start-bot-mode para wa_id: {wa_id}")
        
        # Crear instancia de WhatsAppBot
        whatsapp_bot = create_whatsapp_bot()
        
        # Procesar el último mensaje del lead
        response = whatsapp_bot.chatbot.process_last_lead_message(wa_id)
        
        if response:
            logging.info(f"Respuesta generada para {wa_id}: {response}")
            return func.HttpResponse(json.dumps({
                "success": True,
                "message": "Bot mode activated and response generated",
                "response": response
            }), status_code=200, mimetype="application/json")
        else:
            logging.info(f"No se generó respuesta para {wa_id} - último mensaje no es del lead")
            return func.HttpResponse(json.dumps({
                "success": False,
                "message": "No response generated - last message was not from lead"
            }), status_code=200, mimetype="application/json")
            
    except Exception as e:
        logging.error(f"Error en endpoint start-bot-mode: {e}")
        return func.HttpResponse(json.dumps({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), status_code=500, mimetype="application/json")

@app.route(route="new-lead-form", methods=["POST"])
def new_lead_form(req: func.HttpRequest) -> func.HttpResponse:
    """
    Endpoint para procesar el formulario de nuevo lead.
    """
    logging.info('Endpoint new-lead-form activado')
    
    try:
        # Validar que sea POST
        if req.method != 'POST':
            return func.HttpResponse("Method not allowed", status_code=405)
        
        # Obtener datos del request
        body = req.get_json()
        if not body:
            return func.HttpResponse("Invalid JSON", status_code=400)
        
        logging.info(f"Body: {body}")
        
        # Validar campo requerido
        email_body = body.get("email_body")
        if not email_body:
            return func.HttpResponse("Missing email_body", status_code=400)

        logging.info(f"Procesando new-lead-form para email_body: {email_body}")

        return func.HttpResponse("OK", status_code=200)
    except Exception as e:
        logging.error(f"Error en endpoint new-lead-form: {e}")
        return func.HttpResponse("Internal server error", status_code=500)
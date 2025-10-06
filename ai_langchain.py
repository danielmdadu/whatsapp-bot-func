import json
import os
import random
from typing import Dict, Any, List, Optional, Tuple
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
import langchain
from maquinaria_config import MAQUINARIA_CONFIG, get_required_fields_for_tipo
from state_management import MaquinariaType, ConversationState, ConversationStateStore, InMemoryStateStore, FIELDS_CONFIG_PRIORITY
from datetime import datetime, timezone
import logging
from hubspot_manager import HubSpotManager

langchain.debug = False
langchain.verbose = False
langchain.llm_cache = False

# ============================================================================
# CONFIGURACIÓN DE DEBUG
# ============================================================================

# Variable global para controlar si se muestran los prints de DEBUG
DEBUG_MODE = True

def debug_print(*args, **kwargs):
    """
    Función helper para imprimir mensajes de DEBUG solo cuando DEBUG_MODE es True
    """
    if DEBUG_MODE:
        logging.info(*args, **kwargs)

# ============================================================================
# INVENTARIO FAKE
# ============================================================================

def get_inventory():
    return {
        "tipo_maquinaria": [
            "soldadora",
            "compresor",
            "torre_iluminacion",
            "plataforma",
            "generador",
            "rompedor",
            "apisonador",
            "montacargas",
            "manipulador"
        ],
        "modelo_maquinaria": "Cualquier modelo",
        "ubicacion": "Cualquier ubicación en México",
    }

# ============================================================================
# OBTENER EL ESTADO ACTUAL DE LOS CAMPOS EN UN STRING
# ============================================================================

def get_current_state_str(current_state: ConversationState) -> str:
    """Obtiene el estado actual de los campos como una cadena de texto"""
    field_names = [field for field in FIELDS_CONFIG_PRIORITY.keys()]
    fields_str = ""
    for field in field_names:
        if field == "detalles_maquinaria":
            fields_str += f"- {field}: " + json.dumps(current_state.get(field) or {}) + "\n"
        else:
            fields_str += f"- {field}: " + (current_state.get(field) or "") + "\n"
    return fields_str

# ============================================================================
# CONFIGURACIÓN DE AZURE OPENAI
# ============================================================================

class AzureOpenAIConfig:
    """Clase para manejar la configuración de Azure OpenAI con diferentes configuraciones según el propósito"""
    
    def __init__(self, 
                 endpoint: str,
                 api_key: str,
                 deployment_name: str,
                 api_version: str = "2024-12-01-preview",
                 model_name: str = "gpt-4.1-mini"):
        self.endpoint = endpoint
        self.api_key = api_key
        self.deployment_name = deployment_name
        self.api_version = api_version
        self.model_name = model_name
        
        # Configurar variables de entorno para Azure OpenAI
        os.environ["FOUNDRY_ENDPOINT"] = endpoint
        os.environ["FOUNDRY_API_KEY"] = api_key
        os.environ["OPENAI_API_VERSION"] = api_version
    
    def create_llm(self, temperature: float = 0.3, max_tokens: int = 1000, top_p: float = 1.0):
        """Crea una instancia de AzureChatOpenAI con parámetros personalizados"""
        return AzureChatOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            azure_deployment=self.deployment_name,
            api_version=self.api_version,
            model_name=self.model_name,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            timeout=60,
            max_retries=3,
            verbose=True
        )
    
    def create_extraction_llm(self):
        """Crea un LLM optimizado para extracción de información (temperatura baja para mayor precisión)"""
        return self.create_llm(
            temperature=0.1,  # Temperatura muy baja para extracción precisa
            top_p=0.9,        # Top-p moderado para consistencia
            max_tokens=1000
        )
    
    def create_conversational_llm(self):
        """Crea un LLM optimizado para generación conversacional (temperatura alta para mayor creatividad)"""
        return self.create_llm(
            temperature=0.7,  # Temperatura alta para respuestas más creativas y variadas
            top_p=0.95,       # Top-p alto para mayor diversidad
            max_tokens=75
        )
    
    def create_inventory_llm(self):
        """Crea un LLM para responder preguntas sobre inventario (temperatura moderada)"""
        return self.create_llm(
            temperature=0.5,  # Temperatura moderada para balance entre precisión y creatividad
            top_p=0.9,       # Top-p moderado
            max_tokens=1000
        )

# ============================================================================
# SISTEMA DE SLOT-FILLING INTELIGENTE
# ============================================================================

class IntelligentSlotFiller:
    """Sistema inteligente de slot-filling que detecta información ya proporcionada"""
    
    def __init__(self, azure_config: AzureOpenAIConfig):
        self.llm = azure_config.create_extraction_llm()  # Usar LLM optimizado para extracción
        self.parser = JsonOutputParser()
        
    def detect_negative_response(self, message: str, last_bot_question: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Detecta si el usuario está dando una respuesta negativa o de incertidumbre.
        Retorna un diccionario con el tipo de respuesta y el campo específico, o None si no es una respuesta negativa.
        Formato: {"response_type": "No tiene" o "No especificado", "field": "nombre_del_campo"}
        """
        prompt = ChatPromptTemplate.from_template(
            """
            Eres un asistente experto en detectar respuestas negativas o de incertidumbre y determinar a qué campo específico pertenecen.
            
            ÚLTIMA PREGUNTA DEL BOT: {last_bot_question}
            MENSAJE DEL USUARIO: {message}
            
            INSTRUCCIONES:
            Analiza si el usuario está dando una respuesta negativa o de incertidumbre y determina a qué campo específico pertenece.
            
            RESPUESTAS NEGATIVAS (response_type: "No tiene"):
            - "no", "no tenemos", "no hay", "no tengo", "no cuenta con"
            - "no tenemos página", "no tengo pagina web", "no tenemos sitio web"
            - "no tengo correo", "no tengo teléfono", "no tengo empresa"
            - "solo facebook", "solo instagram", "solo redes sociales"
            - Cualquier variación de "no" + el objeto de la pregunta
            
            RESPUESTAS DE INCERTIDUMBRE (response_type: "No especificado"):
            - "no sé", "no estoy seguro", "no lo sé", "no tengo idea"
            - "no quiero dar esa información", "prefiero no decir", "es confidencial"
            - "no estoy seguro", "tal vez", "posiblemente", "creo que no"
            
            CAMPOS DISPONIBLES:
            {fields_available}
            
            Si NO es una respuesta negativa ni de incertidumbre, retorna "None".
            
            IMPORTANTE: Responde EXACTAMENTE en formato JSON:
            - Si es respuesta negativa: {{"response_type": "No tiene", "field": "nombre_del_campo"}}
            - Si es respuesta de incertidumbre: {{"response_type": "No especificado", "field": "nombre_del_campo"}}
            - Si no es respuesta negativa: "None"
            """
        )
        
        try:
            # Obtener campos disponibles desde el FIELDS_CONFIG_PRIORITY
            fields_available = self._get_fields_available_str()

            response = self.llm.invoke(prompt.format_prompt(
                message=message,
                last_bot_question=last_bot_question or "No hay pregunta previa",
                fields_available=fields_available
            ))
            
            result = response.content.strip()
            
            # Intentar parsear como JSON
            try:
                import json
                parsed_result = json.loads(result)
                if isinstance(parsed_result, dict) and "response_type" in parsed_result and "field" in parsed_result:
                    return parsed_result
                else:
                    return None
            except json.JSONDecodeError:
                # Si no es JSON válido, verificar si es "None"
                if result.lower() == "none":
                    return None
                else:
                    return None
                
        except Exception as e:
            logging.error(f"Error detectando respuesta negativa: {e}")
            return None

    def extract_all_information(self, message: str, current_state: ConversationState, last_bot_question: Optional[str] = None) -> Dict[str, Any]:
        """
        Extrae TODA la información disponible en un solo mensaje
        Detecta qué slots se pueden llenar y cuáles ya están completos
        Incluye el contexto de la última pregunta del bot para mejor interpretación
        """
        
        # PRIMERO: Detectar si es una respuesta negativa o de incertidumbre
        negative_response = self.detect_negative_response(message, last_bot_question)
        
        if negative_response:
            # Si es una respuesta negativa, usar directamente el campo y valor proporcionados por el LLM
            field_name = negative_response.get("field")
            response_type = negative_response.get("response_type")
            
            if field_name and response_type:
                return {field_name: response_type}
            else:
                return {}
        
        # Crear prompt que considere el estado actual y la última pregunta del bot
        prompt = ChatPromptTemplate.from_template(
            """
            Eres un asistente experto en extraer información de mensajes de usuarios.
            
            Analiza el mensaje del usuario y extrae TODA la información disponible.
            Solo extrae campos que NO estén ya completos en el estado actual.
            
            ESTADO ACTUAL:
            {current_state_str}
            
            ÚLTIMA PREGUNTA DEL BOT: {last_bot_question}
            
            MENSAJE DEL USUARIO: {message}
            
            INSTRUCCIONES:
            1. Solo extrae campos que estén VACÍOS en el estado actual
            2. Para detalles_maquinaria, solo incluye campos específicos que no estén ya llenos
            3. Responde SOLO en formato JSON válido
            4. IMPORTANTE: Si el mensaje del usuario no contiene información nueva para campos vacíos, responde con {{}} (JSON vacío)
            5. NO extraigas información de campos que ya están llenos, incluso si el usuario dice algo que podría interpretarse como información
            6. CLASIFICACIÓN INTELIGENTE: Si la última pregunta es sobre un campo específico, clasifica la respuesta en ese campo
            
            CAMPOS A EXTRAER (solo si están vacíos):
            {fields_available}

            REGLAS ESPECIALES PARA NOMBRES:
            - Si el usuario dice "soy [nombre]", "me llamo [nombre]", "hola, soy [nombre]" → extraer nombre y apellido
            - Para nombres de 1 palabra: llenar solo "nombre"
            - Para nombres de 2+ palabras: llenar "nombre" con la primera palabra y "apellido" con el resto
            - Ejemplos: "soy Paco" → nombre: "Paco"
            - Ejemplos: "soy Paco Perez" → nombre: "Paco", apellido: "Perez"
            - Ejemplos: "soy Paco Perez Diaz" → nombre: "Paco", apellido: "Perez Diaz"

            Los tipos de maquinaria disponibles para el campo tipo_maquinaria son:
            {maquinaria_names}
            
            REGLAS ADICIONALES PARA DETALLES DE MAQUINARIA - USA ESTOS NOMBRES EXACTOS:
            - Para TORRE_ILUMINACION: es_led (true/false para LED)
            - Para SOLDADORAS: amperaje, electrodo
            - Para COMPRESOR: capacidad_volumen, herramientas_conectar
            - Para PLATAFORMA: altura_trabajo, actividad, ubicacion
            - Para GENERADORES: actividad, capacidad
            - Para ROMPEDORES: uso, tipo
            - Para APISONADOR: uso, motor, es_diafragma
            - Para MONTACARGAS: capacidad, tipo_energia, posicion_operador, altura
            - Para MANIPULADOR: capacidad, altura, actividad, tipo_energia
            - IMPORTANTE: Usa exactamente estos nombres de campos, NO inventes nombres alternativos
            - NO extraer campos que no estén en esta lista exacta
            - NO inventar campos adicionales como "proyecto", "aplicación", "capacidad_de_volumen", etc.
            
            REGLAS ESPECIALES PARA GIRO_EMPRESA:
            - Si el usuario describe la actividad de su empresa → giro_empresa: [descripción de la actividad]
            - Si el usuario dice "nos dedicamos a la [actividad]" → giro_empresa: [actividad]
            - Ejemplos: "venta de maquinaria pesada", "construcción", "manufactura", "servicios de mantenimiento", "distribución", "logística", etc.
            - Extrae la actividad principal, no solo palabras sueltas
            
            REGLAS ESPECIALES PARA USO_EMPRESA_O_VENTA:
            - Si el usuario dice "para venta", "es para vender", "para comercializar" → uso_empresa_o_venta: "venta"
            - Si el usuario dice "para uso", "para usar", "para trabajo interno" → uso_empresa_o_venta: "uso empresa"
            
            EJEMPLOS DE EXTRACCIÓN:
            - Mensaje: "soy Renato Fuentes" → {{"nombre": "Renato", "apellido": "Fuentes"}}
            - Mensaje: "me llamo Mauricio Martinez Rodriguez" → {{"nombre": "Mauricio", "apellido": "Martinez Rodriguez"}}
            - Mensaje: "venta de maquinaria" → {{"giro_empresa": "venta de maquinaria"}}
            - Mensaje: "construcción y mantenimiento" → {{"giro_empresa": "construcción y mantenimiento"}}
            - Mensaje: "para venta" → {{"uso_empresa_o_venta": "venta"}}
            - Mensaje: "www.empresa.com" → {{"sitio_web": "www.empresa.com"}}
            - Mensaje: "en la Ciudad de México" → {{"lugar_requerimiento": "Ciudad de México"}}
            - Mensaje: "daniel@empresa.com" → {{"correo": "daniel@empresa.com"}}
            - Mensaje: "555-1234" → {{"telefono": "555-1234"}}
            
            EJEMPLOS DE USO DEL CONTEXTO DE LA ÚLTIMA PREGUNTA:
            - Última pregunta: "¿En qué compañía trabajas?" + Mensaje: "Facebook" → {{"nombre_empresa": "Facebook"}}
            - Última pregunta: "¿Cuál es el giro de su empresa?" + Mensaje: "Construcción" → {{"giro_empresa": "Construcción"}}
            - Última pregunta: "¿Cuál es su correo electrónico?" + Mensaje: "daniel@empresa.com" → {{"correo": "daniel@empresa.com"}}
            - Última pregunta: "¿Es para uso de la empresa o para venta?" + Mensaje: "Para venta" → {{"uso_empresa_o_venta": "venta"}}
            - Última pregunta: "¿Cuál es el sitio web de su empresa?" + Mensaje: "www.empresa.com" → {{"sitio_web": "www.empresa.com"}}

            REGLAS ESPECIALES PARA PREGUNTAS SOBRE INVENTARIO:
            - Si el usuario pregunta "¿tienen [tipo]?" → extraer [tipo] como tipo_maquinaria
            - Si el usuario pregunta "¿manejan [tipo]?" → extraer [tipo] como tipo_maquinaria  
            - Si el usuario pregunta "necesito [tipo]" → extraer [tipo] como tipo_maquinaria
            - Ejemplos: "¿tienen generadores?" → {{"tipo_maquinaria": "generador"}}
            - Ejemplos: "¿manejan soldadoras?" → {{"tipo_maquinaria": "soldadora"}}
            - Ejemplos: "necesito un compresor" → {{"tipo_maquinaria": "compresor"}}
            IMPORTANTE: Incluso en preguntas sobre inventario, SIEMPRE extraer tipo_maquinaria si se menciona
            
            IMPORTANTE: Analiza cuidadosamente el mensaje y extrae TODA la información disponible que corresponda a campos vacíos.
            
            Respuesta (solo JSON):
            """
        )
        
        try:
            # Nombres de tipos de maquinaria
            maquinaria_names = " ".join([f"\"{name.value}\"" for name in MaquinariaType])

            # Obtener campos disponibles desde el FIELDS_CONFIG_PRIORITY
            fields_available = self._get_fields_available_str()

            response = self.llm.invoke(prompt.format_prompt(
                message=message,
                current_state_str=get_current_state_str(current_state),
                last_bot_question=last_bot_question or "No hay pregunta previa (inicio de conversación)",
                maquinaria_names=maquinaria_names,
                fields_available=fields_available
            ))
            
            # Parsear la respuesta JSON
            result = self.parser.parse(response.content)
            return result
            
        except Exception as e:
            logging.error(f"Error extrayendo información: {e}")
            return {}
    
    def get_next_question(self, current_state: ConversationState) -> Optional[str]:
        """
        Determina inteligentemente cuál es la siguiente pregunta necesaria
        generándola de manera conversacional y natural
        """
        
        try:
            # Verificar cada slot en orden de prioridad
            for slot_name, data in FIELDS_CONFIG_PRIORITY.items():
                question = data["question"]
                reason = data["reason"]
                
                if slot_name == "detalles_maquinaria":
                    # Manejar detalles específicos de maquinaria
                    question_details = self._get_maquinaria_detail_question_with_reason(current_state)
                    if question_details:
                        return question_details
                else:
                    # Verificar si el slot está vacío o tiene respuestas negativas
                    value = current_state.get(slot_name)
                    if not value:
                        # Si se debe preguntar por el nombre de la empresa y no se tiene el giro,
                        # preguntar por el giro de la empresa también.
                        if slot_name == "nombre_empresa" and not current_state.get("giro_empresa"):
                            question = "¿Cuál es el nombre y giro de su empresa?"
                        
                        return {
                            "question": question, 
                            "reason": reason, 
                            "question_type": slot_name
                        }
            
            # Si todos los slots están llenos
            return None
            
        except Exception as e:
            logging.error(f"Error generando siguiente pregunta: {e}")
            return None

    def _get_fields_available_str(self) -> str:
        """Obtiene los campos disponibles como una lista de strings con su descripción"""
        fields_available = [field for field in FIELDS_CONFIG_PRIORITY.keys()]
        fields_available_str = ""
        for field in fields_available:
            fields_available_str += f"- {field}: " + FIELDS_CONFIG_PRIORITY[field]['description'] + "\n"
        return fields_available_str
    
    def _get_maquinaria_detail_question_with_reason(self, current_state: ConversationState) -> Optional[dict]:
        """Obtiene la siguiente pregunta específica sobre detalles de maquinaria de manera conversacional con el motivo"""
        
        tipo = current_state.get("tipo_maquinaria")

        if not tipo or tipo not in MAQUINARIA_CONFIG:
            return None

        config = MAQUINARIA_CONFIG[tipo]
        detalles = current_state.get("detalles_maquinaria", {})

        # Buscar el primer campo de la configuración que no esté en los detalles
        for field_info in config["fields"]:
            field_name = field_info["name"]
            if not detalles.get(field_name):
                # Encontrado el siguiente campo a preguntar
                # Devolver la pregunta fija definida en la configuración centralizada
                return {
                    "question": field_info.get("question"), 
                    "reason": field_info.get("reason"), 
                    "question_type": "detalles_maquinaria"
                }

        return None # Todos los detalles están completos
    
    def is_conversation_complete(self, current_state: ConversationState) -> bool:
        """Verifica si la conversación está completa (todos los slots llenos)"""

        # Verificar si el nombre tiene al menos dos palabras (nombre + apellido)
        nombre = current_state.get("nombre", "")
        if not nombre or len(nombre.split()) < 2:
            return False

        # Obtener campos obligatorios desde el FIELDS_CONFIG_PRIORITY
        required_fields = [field for field in FIELDS_CONFIG_PRIORITY.keys() if FIELDS_CONFIG_PRIORITY[field]["required"]]
        
        # Verificar campos básicos
        for field in required_fields:
            value = current_state.get(field)
            if not value or value == "":
                return False
        
        # Verificar detalles de maquinaria
        detalles = current_state.get("detalles_maquinaria", {})
        
        if not detalles:
            return False
        
        # Usar la configuración centralizada para obtener campos obligatorios
        tipo = current_state.get("tipo_maquinaria")
        required_fields = get_required_fields_for_tipo(tipo)
        
        return all(
            field in detalles and 
            detalles[field] is not None and 
            detalles[field] != ""
            for field in required_fields
        )

# ============================================================================
# SISTEMA DE RESPUESTAS INTELIGENTES
# ============================================================================

class IntelligentResponseGenerator:
    """Genera respuestas inteligentes basadas en el contexto y la información extraída"""
    
    def __init__(self, azure_config: AzureOpenAIConfig):
        self.llm = azure_config.create_conversational_llm()  # Usar LLM optimizado para conversación
    
    def generate_response(self, 
        message: str, 
        history_messages: List[Dict[str, Any]],
        extracted_info: Dict[str, Any], 
        current_state: ConversationState, 
        next_question: str = None, 
        next_question_reason: str = None, 
        is_inventory_question: bool = False,
        is_same_last_question: bool = False # Si la pregunta anterior es la misma que la última pregunta
    ) -> str:
        """Genera una respuesta contextual apropiada usando un enfoque conversacional"""
        
        try:
            # Crear prompt conversacional basado en el estilo de llm.py
            prompt_str = """
                Eres Juan, un asesor comercial en Alpha C y un asistente de ventas profesional especializado en maquinaria de la empresa.
                Estás continuando una conversación con un lead.
                Tu trabajo recolectar información de manera natural y conversacional, con un tono casual y amigable.

                HISTORIAL DE CONVERSACIÓN:
                {history_messages}

                INFORMACIÓN EXTRAÍDA DEL ÚLTIMO MENSAJE:
                {extracted_info_str}
                
                ESTADO ACTUAL DE LA CONVERSACIÓN:
                {current_state_str}
                
                SIGUIENTE PREGUNTA A HACER: {next_question}
                SOLO MENCIONA LA RAZÓN DE LA SIGUIENTE PREGUNTA SI EL USUARIO LO PREGUNTA: {next_question_reason}
           
                MENSAJE DEL USUARIO: {user_message}

                IMPORTANTE:
                {inventory_instruction}
                
                INSTRUCCIONES:
                1. No repitas información que ya confirmaste anteriormente
                2. Si estás respondiendo al primer mensaje del usuario, presentate como Juan, asesor comercial de Alpha C
                3. Si ya mencionaste el nombre del usuario, no lo menciones nuevamente
                4. Si hay una siguiente pregunta, hazla de manera natural
                5. NO inventes preguntas adicionales
                6. Si no hay siguiente pregunta, simplemente confirma la información recibida
                
                Genera una respuesta natural y apropiada:
            """
            
            prompt = ChatPromptTemplate.from_template(prompt_str)

            # Preparar información extraída como string de manera más segura
            if not extracted_info:
                extracted_info_str = "Ninguna información nueva"
            else:
                # Filtrar información sensible antes de enviar
                safe_info = {}
                for key, value in extracted_info.items():
                    if key in ['apellido', 'correo', 'telefono']:
                        safe_info[key] = '[INFORMACIÓN PRIVADA]'
                    else:
                        safe_info[key] = value
                extracted_info_str = json.dumps(safe_info, ensure_ascii=False, indent=2)

            if is_inventory_question:
                # Nombres de tipos de maquinaria
                maquinaria_names = ", ".join([f"\"{name.value}\"" for name in MaquinariaType])
                # Cambiar torre_iluminacion por torre de iluminación y plataforma por plataforma de elevación
                maquinaria_names = maquinaria_names.replace("torre_iluminacion", "torre de iluminación")
                maquinaria_names = maquinaria_names.replace("plataforma", "plataforma de elevación")

                inventory_instruction = "Este mensaje del usuario incluye una pregunta sobre inventario, por lo tanto, a continuación te comparto los tipos de maquinaria que tenemos:" + maquinaria_names
            else:
                inventory_instruction = "Sigue las instrucciones dadas."

            current_state_str = get_current_state_str(current_state)
            formatedPrompt = prompt.format_prompt(
                user_message=message,
                current_state_str=current_state_str,
                history_messages=history_messages,
                extracted_info_str=extracted_info_str,
                next_question=next_question or "No hay siguiente pregunta",
                next_question_reason=next_question_reason or "No hay razón para la siguiente pregunta",
                inventory_instruction=inventory_instruction
            )
            
            response = self.llm.invoke(formatedPrompt)
            
            result = response.content.strip()
            debug_print(f"DEBUG: Respuesta conversacional generada: '{result}'")
            return result
            
        except Exception as e:
            logging.error(f"Error generando respuesta conversacional: {e}")
            # Fallback a la lógica simple si no se puede generar la respuesta
            if next_question and next_question_reason:
                return next_question + " " + next_question_reason
            else:
                return "En un momento le responderemos."
    
    def generate_final_response(self, current_state: ConversationState) -> str:
        """Genera la respuesta final cuando la conversación está completa"""

        current_state_str = get_current_state_str(current_state)
        
        return f"""¡Perfecto, {current_state['nombre']}! 

He registrado toda su información:
{current_state_str}

Procederé a generar su cotización. Nos pondremos en contacto con usted pronto.

¿Hay algo más en lo que pueda ayudarle?"""

# ============================================================================
# RESPONDEDOR DE INVENTARIO
# ============================================================================

class InventoryResponder:
    """Responde preguntas sobre el inventario de maquinaria"""
    
    def __init__(self, azure_config: AzureOpenAIConfig):
        self.llm = azure_config.create_inventory_llm()  # Usar LLM optimizado para inventario
        self.inventory = get_inventory()
    
    def is_inventory_question(self, message: str) -> bool:
        """Determina si el mensaje del usuario es una pregunta sobre el inventario"""
        try:
            prompt = ChatPromptTemplate.from_template(
                """
                Eres un asistente especializado en identificar si un mensaje del usuario es una pregunta sobre inventario de maquinaria.
                
                TU TAREA:
                Determinar si el mensaje del usuario es una pregunta sobre:
                1. Disponibilidad de maquinaria
                2. Tipos de maquinaria que vendemos
                3. Modelos disponibles
                4. Ubicaciones de entrega
                5. Precios o cotizaciones
                6. Características de la maquinaria
                7. Cualquier consulta relacionada con el inventario
                
                REGLAS:
                - Si es pregunta sobre inventario → true
                - Si es respuesta a una pregunta del bot → false
                - Si es información personal del usuario → false
                - Si es pregunta general no relacionada → false
                
                EJEMPLOS DE PREGUNTAS SOBRE INVENTARIO:
                - "¿Qué tipos de maquinaria tienen?"
                - "¿Tienen soldadoras?"
                - "¿Cuánto cuesta un compresor?"
                - "¿En qué ubicaciones entregan?"
                - "¿Qué modelos de generadores manejan?"
                - "¿Tienen inventario disponible?"
                - "¿Pueden cotizar una torre de iluminación?"
                
                EJEMPLOS DE NO INVENTARIO:
                - "me llamo Juan"
                - "quiero un compresor"
                - "no tengo página web"
                - "es para venta"
                - "mi empresa se llama ABC"
                
                Mensaje del usuario: {message}
                
                Responde SOLO con "true" si es pregunta sobre inventario, o "false" si no lo es.
                """
            )
            
            response = self.llm.invoke(prompt.format_prompt(
                message=message
            ))
            
            result = response.content.strip().lower()
            
            debug_print(f"DEBUG: ¿Es pregunta sobre inventario? '{message}' → {result}")
            
            return result == "true"
            
        except Exception as e:
            logging.error(f"Error detectando pregunta de inventario: {e}")
            import traceback
            traceback.print_exc()
            return False

# ============================================================================
# CLASE PRINCIPAL DEL CHATBOT CON SLOT-FILLING INTELIGENTE
# ============================================================================

class IntelligentLeadQualificationChatbot:
    """Chatbot con slot-filling inteligente que detecta información ya proporcionada"""
    
    def __init__(self, azure_config: AzureOpenAIConfig, state_store: Optional[ConversationStateStore] = None, send_message_callback=None):
        self.azure_config = azure_config
        # Crear instancias con configuraciones específicas para cada propósito
        self.slot_filler = IntelligentSlotFiller(azure_config)
        self.response_generator = IntelligentResponseGenerator(azure_config)
        self.inventory_responder = InventoryResponder(azure_config)
        
        # Usar el state_store proporcionado o crear uno en memoria por defecto
        self.state_store = state_store or InMemoryStateStore()
        self.current_user_id = None
        
        # Callback para enviar mensajes por WhatsApp
        self.send_message_callback = send_message_callback
        
        # El estado local sigue existiendo para compatibilidad con código existente
        self.state = self._create_empty_state()

    def _create_empty_state(self) -> ConversationState:
        """Crea un estado vacío"""
        state = {
            # Campos que no se preguntan al usuario
            "completed": False,
            "messages": [],
            "conversation_mode": "agente",
            "asignado_asesor": None,
            "hubspot_contact_id": None
        }
        
        # Agregamos los campos que se preguntan al usuario desde el FIELDS_CONFIG_PRIORITY
        fields_to_ask = [field for field in FIELDS_CONFIG_PRIORITY.keys()]
        for field in fields_to_ask:
            if field == "detalles_maquinaria":
                state[field] = {}
            else:
                state[field] = None

        return state
    
    def load_conversation(self, user_id: str):
        """Carga la conversación de un usuario específico"""
        logging.info(f"Cargando conversación para usuario {user_id}")
        self.current_user_id = user_id
        stored_state = self.state_store.get_conversation_state(user_id)
        
        if stored_state:
            self.state = stored_state
            debug_print(f"DEBUG: Estado cargado para usuario {user_id}")
        else:
            logging.info(f"No hay estado existente para usuario {user_id}, creando nuevo estado")
            self.state = self._create_empty_state()
            debug_print(f"DEBUG: Nuevo estado creado para usuario {user_id}")

    def save_conversation(self):
        """Guarda el estado actual de la conversación"""
        if self.current_user_id:
            self.state_store.save_conversation_state(self.current_user_id, self.state)
            debug_print(f"DEBUG: Estado guardado para usuario {self.current_user_id}")

    def reset_conversation(self):
        """Reinicia el estado de la conversación"""
        if self.current_user_id:
            self.state_store.delete_conversation_state(self.current_user_id)
        self.state = self._create_empty_state()
    
    def send_message(self, user_message: str, whatsapp_message_id: str = None, hubspot_manager: HubSpotManager = None) -> str:
        """
        Procesa un mensaje del usuario con slot-filling inteligente.
        Si hubspot_manager es None, no se actualiza el contacto en HubSpot (para poder usar test_chatbot.py)
        """
        
        try:
            debug_print(f"DEBUG: send_message llamado con mensaje: '{user_message}'")
            
            # Si el mensaje está vacío, no hacer nada y esperar al usuario
            if not user_message or not user_message.strip():
                return None
            
            # Mensaje que se regresa
            contextual_response = ""
            
            # Agregar mensaje del usuario
            self.state["messages"].append({
                "role": "user", 
                "whatsapp_message_id": whatsapp_message_id,
                "content": user_message,
                "question_type": "",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sender": "lead"
            })

            # Extraer TODA la información disponible del mensaje (SIEMPRE)
            # Obtener la última pregunta del bot para contexto
            last_bot_question, last_bot_question_type = self._get_last_bot_question()
            extracted_info = self.slot_filler.extract_all_information(user_message, self.state, last_bot_question)
            debug_print(f"DEBUG: Información extraída: {extracted_info}") 
            
            # Actualizar el contacto en HubSpot
            if hubspot_manager:
                hubspot_manager.update_contact(self.state, extracted_info)

            # Actualizar el estado con la información extraída
            self._update_state_with_extracted_info(extracted_info)

            # Verificar modo de conversación antes de generar respuesta
            current_mode = self.state.get("conversation_mode", "bot")
            
            if current_mode == "agente":
                # Modo agente: solo guardar estado, no generar respuesta automática
                debug_print(f"DEBUG: Modo agente activo, no generando respuesta automática")
                self.save_conversation()
                return None  # No response en modo agente
            
            is_inventory_question = False

            # Verificar si es una pregunta sobre inventario
            if self.inventory_responder.is_inventory_question(user_message):
                debug_print(f"DEBUG: Pregunta sobre inventario detectada")
                is_inventory_question = True
            
            # Si no es pregunta de inventario ni de requerimientos, continuar con el flujo normal
            debug_print(f"DEBUG: Flujo normal de calificación de leads...")

            # Verificar si la conversación está completa (solo en modo bot)
            if self.slot_filler.is_conversation_complete(self.state):
                debug_print(f"DEBUG: Conversación completa!")
                self.state["completed"] = True
                # final_response = self.response_generator.generate_final_response(self.state)
                final_response = "Gracias por la información. Pronto te contactará nuestro asesor especializado."
                return self._add_message_and_return_response(final_response, "")
            
            # Obtener la siguiente pregunta necesaria
            next_question = self.slot_filler.get_next_question(self.state)

            if next_question is None:
                debug_print(f"DEBUG: Estado completo: {self.state}")
                self.state["completed"] = True
                final_message = "Gracias por la información. Pronto te contactará nuestro asesor especializado."
                return self._add_message_and_return_response(final_message, "")

            next_question_str = next_question["question"]
            next_question_reason = next_question["reason"]

            debug_print(f"DEBUG: Siguiente pregunta: {next_question_str}")

            next_question_type = next_question['question_type']
            debug_print(f"DEBUG: Tipo de siguiente pregunta: {next_question_type}")

            # Extract only the role and content of the history messages
            history_messages = [{
                "role": msg["role"],
                "content": msg["content"]
            } for msg in self.state["messages"]]

            # Generar respuesta con LLM
            generated_response = self.response_generator.generate_response(
                user_message, 
                history_messages,
                extracted_info, 
                self.state, 
                next_question_str, 
                next_question_reason, 
                is_inventory_question,
                last_bot_question_type == next_question_type
            )
            contextual_response += generated_response

            return self._add_message_and_return_response(contextual_response, next_question_type)
        
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
            return "Disculpe, hubo un error técnico. ¿Podría intentar de nuevo?"
        
    def _add_message_and_return_response(self, response: str, question_type: str) -> str:
        """
        Añade un mensaje al estado y devuelve la respuesta final
        Si es un mensaje del bot y hay callback disponible, envía por WhatsApp primero
        """
        whatsapp_message_id = ""
        
        # Enviar mensaje por WhatsApp primero
        try:
            whatsapp_message_id = self.send_message_callback(self.current_user_id, response)
            debug_print(f"DEBUG: Mensaje enviado por WhatsApp con ID: {whatsapp_message_id}")
        except Exception as e:
            debug_print(f"DEBUG: Error enviando mensaje por WhatsApp: {e}")
            # Continuar sin el ID si hay error
        
        # Crear el mensaje con el ID de WhatsApp       
        self.state["messages"].append({
            "role": "assistant", 
            "whatsapp_message_id": whatsapp_message_id,
            "question_type": question_type,
            "content": response,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sender": "bot"
        })
        
        # Al final, guardar el estado
        self.save_conversation()

        return response
    
    def _update_state_with_extracted_info(self, extracted_info: Dict[str, Any]):
        """
        Actualiza el estado con la información extraída, confiando en el 
        pre-procesamiento y formato realizado por el LLM.
        """
        debug_print(f"DEBUG: Actualizando estado con información: {extracted_info}")
        for key, value in extracted_info.items():
            # 1. Ignorar valores nulos o vacíos para no insertar datos inútiles.
            if value is None or value == "":
                continue

            # 2. No sobrescribir campos que ya tienen un valor válido a excepción de detalles_maquinaria.
            # detalles_maquinaria se actualiza múltiples veces porque tiene varios subcampos.
            # Esto es clave para evitar que una respuesta ambigua posterior
            # borre un dato que ya se había confirmado.
            current_value = self.state.get(key)
            if key != "detalles_maquinaria" and current_value:
                debug_print(f"DEBUG: Campo '{key}' ya tiene valor válido '{current_value}', no se sobrescribe.")
                continue

            # 3. Manejo de casos especiales
            if key == "detalles_maquinaria" and isinstance(value, dict):
                current_detalles = self.state.get("detalles_maquinaria", {})
                current_detalles.update(value)
                self.state["detalles_maquinaria"] = current_detalles
                debug_print(f"DEBUG: Detalles de maquinaria actualizados: {self.state['detalles_maquinaria']}")
            
            elif key == "tipo_maquinaria":
                try:
                    self.state[key] = MaquinariaType(value)
                    debug_print(f"DEBUG: Campo '{key}' actualizado a (Enum): {self.state[key]}")
                except ValueError:
                    # Si el LLM extrae un tipo inválido, lo registramos pero no detenemos el flujo.
                    logging.error(f"ADVERTENCIA: Tipo de maquinaria inválido '{value}' extraído por el LLM.")
            
            elif key == "apellido":
                # Combinar nombre y apellido en el campo nombre
                nombre_actual = self.state.get("nombre", "")
                if nombre_actual and value:
                    self.state["nombre"] = f"{nombre_actual} {value}".strip()
                    self.state["apellido"] = value 
                    debug_print(f"DEBUG: Nombre y apellido combinados: '{self.state['nombre']}'")
                else:
                    self.state[key] = value
                    debug_print(f"DEBUG: Campo '{key}' actualizado con valor: '{value}'")
            
            # 4. Para todos los demás campos, la actualización es directa.
            # Se confía en que el LLM ya formateó la respuesta según las reglas del prompt.
            else:
                self.state[key] = value
                debug_print(f"DEBUG: Campo '{key}' actualizado con valor: '{value}'")
        
    def _get_last_bot_question(self) -> Tuple[Optional[str], Optional[str]]:
        """Obtiene la última pregunta que hizo el bot para proporcionar contexto"""
        try:
            # Buscar el último mensaje del bot en el historial
            for msg in reversed(self.state["messages"]):
                if msg["role"] == "assistant" or msg["sender"] == "bot":
                    content = msg["content"]
                    question_type = msg["question_type"]
                    # Si el mensaje contiene una pregunta, extraerla
                    if "?" in content:
                        # Buscar la última línea que contenga una pregunta
                        lines = content.split('\n')
                        for line in reversed(lines):
                            if "?" in line and line.strip():
                                return line.strip(), question_type
                        # Si no se encuentra una línea específica, devolver todo el contenido
                        return content, question_type
                    return content, question_type
            return None, None
        except Exception as e:
            logging.error(f"Error obteniendo última pregunta del bot: {e}")
            return None, None
    
    def get_lead_data_json(self) -> str:
        """Obtiene los datos del lead en formato JSON"""
        return json.dumps(get_current_state_str(self.state), indent=2, ensure_ascii=False)
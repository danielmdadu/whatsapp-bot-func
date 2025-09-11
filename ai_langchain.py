import json
import os
import random
from typing import Dict, Any, Optional
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
import langchain
from maquinaria_config import MAQUINARIA_CONFIG, get_required_fields_for_tipo
from state_management import MaquinariaType, ConversationState, ConversationStateStore, InMemoryStateStore
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
DEBUG_MODE = False

def debug_print(*args, **kwargs):
    """
    Función helper para imprimir mensajes de DEBUG solo cuando DEBUG_MODE es True
    """
    if DEBUG_MODE:
        print(*args, **kwargs)

# ============================================================================
# SISTEMA DE FRASES ALEATORIAS
# ============================================================================

# Lista de frases de confirmación/agradecimiento
CONFIRMATION_PHRASES = [
    "Muy bien",
    "Gracias por la información", 
    "Excelente",
    "Entendido",
    "Perfecto"
]

# Lista de conectores para preguntas
QUESTION_CONNECTORS = [
    "Ahora me podrías decir",
    "También me podrías compartir",
    "Ahora puedes decirme",
    "También necesito saber"
]

def get_random_confirmation_phrase() -> str:
    """Selecciona aleatoriamente una frase de confirmación"""
    return random.choice(CONFIRMATION_PHRASES)

def get_random_question_connector() -> str:
    """Selecciona aleatoriamente un conector para preguntas"""
    return random.choice(QUESTION_CONNECTORS)

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
            max_tokens=1000
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
        
    def extract_all_information(self, message: str, current_state: ConversationState, last_bot_question: Optional[str] = None) -> Dict[str, Any]:
        """
        Extrae TODA la información disponible en un solo mensaje
        Detecta qué slots se pueden llenar y cuáles ya están completos
        Incluye el contexto de la última pregunta del bot para mejor interpretación
        """
        
        # Crear prompt que considere el estado actual y la última pregunta del bot
        prompt = ChatPromptTemplate.from_template(
            """
            Eres un asistente experto en extraer información de mensajes de usuarios.
            
            Analiza el mensaje del usuario y extrae TODA la información disponible.
            Solo extrae campos que NO estén ya completos en el estado actual.
            
            ESTADO ACTUAL:
            - nombre: {current_nombre}
            - apellido: {current_apellido}
            - tipo_maquinaria: {current_tipo}
            - detalles_maquinaria: {current_detalles}
            - sitio_web: {current_sitio_web}
            - uso_empresa_o_venta: {current_uso}
            - nombre_empresa: {current_nombre_empresa}
            - giro_empresa: {current_giro}
            - lugar_requerimiento: {current_lugar_requerimiento}
            - correo: {current_correo}
            - telefono: {current_telefono}
            
            ÚLTIMA PREGUNTA DEL BOT: {last_bot_question}
            
            MENSAJE DEL USUARIO: {message}
            
            INSTRUCCIONES:
            1. Solo extrae campos que estén VACÍOS en el estado actual
            2. Si un campo ya tiene valor, NO lo incluyas en la respuesta
            3. Para detalles_maquinaria, solo incluye campos específicos que no estén ya llenos
            4. Responde SOLO en formato JSON válido
            5. IMPORTANTE: Si el mensaje del usuario no contiene información nueva para campos vacíos, responde con {{}} (JSON vacío)
            6. NO extraigas información de campos que ya están llenos, incluso si el usuario dice algo que podría interpretarse como información
            7. CLASIFICACIÓN INTELIGENTE: Si la última pregunta es sobre un campo específico, clasifica la respuesta en ese campo
            
            CAMPOS A EXTRAER (solo si están vacíos):
            - nombre: nombre de la persona
            - tipo_maquinaria: {maquinaria_names}
            - detalles_maquinaria: objeto con campos específicos según tipo_maquinaria
            - lugar_requerimiento: lugar donde se requiere la máquina
            - sitio_web: URL del sitio web o "No tiene" (para respuestas negativas como "no", "no tenemos", "no cuenta", etc.)
            - uso_empresa_o_venta: "uso empresa" o "venta"
            - nombre_empresa: nombre de la empresa
            - giro_empresa: giro o actividad de la empresa (ej: "venta de maquinaria", "construcción", "manufactura", "servicios", etc.)
            - correo: dirección de email
            - telefono: número telefónico
            
            REGLAS ESPECIALES PARA SITIO_WEB:
            - Si el usuario dice algo como "no", "no tenemos", "no hay", "no tenemos página", "no tenemos sitio", "no tenemos página web" → sitio_web: "No tiene"
            - Si el usuario proporciona una URL o sitio web → sitio_web: [URL]
            - Si el usuario dice "solo facebook", "solo instagram", "solo redes sociales" → sitio_web: "No tiene"
            
            REGLAS ESPECIALES PARA TODOS LOS CAMPOS:
            - Si el usuario dice "no tengo", "no sé", "no estoy seguro", "no lo sé", "no tengo idea", "aún no lo he decidido" → usar "No especificado" como valor
            - Si el usuario dice "no quiero dar esa información", "prefiero no decir", "es confidencial" → usar "No especificado" como valor
            - Si el usuario dice "no tengo correo", "no tengo teléfono", "no tengo empresa" → usar "No tiene" como valor
            
            REGLAS ESPECIALES PARA GIRO_EMPRESA:
            - Si el usuario describe la actividad de su empresa → giro_empresa: [descripción de la actividad]
            - Si el usuario dice "nos dedicamos a la [actividad]" → giro_empresa: [actividad]
            - Ejemplos: "venta de maquinaria pesada", "construcción", "manufactura", "servicios de mantenimiento", "distribución", "logística", etc.
            - Extrae la actividad principal, no solo palabras sueltas
            
            REGLAS ESPECIALES PARA NOMBRES:
            - Si el usuario dice "soy [nombre]", "me llamo [nombre]", "hola, soy [nombre]" → extraer nombre y apellido
            - Para nombres de 1 palabra: llenar solo "nombre"
            - Para nombres de 2+ palabras: llenar "nombre" con la primera palabra y "apellido" con el resto
            - Ejemplos: "soy Paco" → nombre: "Paco"
            - Ejemplos: "soy Paco Perez" → nombre: "Paco", apellido: "Perez"
            - Ejemplos: "soy Paco Perez Diaz" → nombre: "Paco", apellido: "Perez Diaz"
            
            REGLAS ESPECIALES PARA USO_EMPRESA_O_VENTA:
            - Si el usuario dice "para venta", "es para vender", "para comercializar" → uso_empresa_o_venta: "venta"
            - Si el usuario dice "para uso", "para usar", "para trabajo interno" → uso_empresa_o_venta: "uso empresa"
            
            EJEMPLOS DE EXTRACCIÓN:
            - Mensaje: "soy Renato Fuentes" → {{"nombre": "Renato", "apellido": "Fuentes"}}
            - Mensaje: "me llamo Mauricio Martinez Rodriguez" → {{"nombre": "Mauricio", "apellido": "Martinez Rodriguez"}}
            - Mensaje: "no hay pagina web" → {{"sitio_web": "No tiene"}}
            - Mensaje: "venta de maquinaria pesada" → {{"giro_empresa": "venta de maquinaria pesada"}}
            - Mensaje: "para venta" → {{"uso_empresa_o_venta": "venta"}}
            - Mensaje: "construcción y mantenimiento" → {{"giro_empresa": "construcción y mantenimiento"}}
            - Mensaje: "en la Ciudad de México" → {{"lugar_requerimiento": "Ciudad de México"}}
            - Mensaje: "daniel@empresa.com" → {{"correo": "daniel@empresa.com"}}
            - Mensaje: "555-1234" → {{"telefono": "555-1234"}}
            
            EJEMPLOS DE RESPUESTAS SIN INFORMACIÓN NUEVA:
            - Mensaje: "no se" → {{}} (no hay información nueva)
            - Mensaje: "aun no lo he decidido" → {{}} (no hay información nueva)
            - Mensaje: "no estoy seguro" → {{}} (no hay información nueva)
            - Mensaje: "no tengo idea" → {{}} (no hay información nueva)
            
            EJEMPLOS DE USO DEL CONTEXTO DE LA ÚLTIMA PREGUNTA:
            - Última pregunta: "¿En qué compañía trabajas?" + Mensaje: "Facebook" → {{"nombre_empresa": "Facebook"}}
            - Última pregunta: "¿Cuál es el giro de su empresa?" + Mensaje: "Construcción" → {{"giro_empresa": "Construcción"}}
            - Última pregunta: "¿Cuál es su correo electrónico?" + Mensaje: "daniel@empresa.com" → {{"correo": "daniel@empresa.com"}}
            - Última pregunta: "¿Es para uso de la empresa o para venta?" + Mensaje: "Para venta" → {{"uso_empresa_o_venta": "venta"}}
            - Última pregunta: "¿Su empresa cuenta con algún sitio web?" + Mensaje: "Solo Facebook" → {{"sitio_web": "No tiene"}}

            REGLAS ESPECIALES PARA PREGUNTAS SOBRE INVENTARIO:
            - Si el usuario pregunta "¿tienen [tipo]?" → extraer [tipo] como tipo_maquinaria
            - Si el usuario pregunta "¿manejan [tipo]?" → extraer [tipo] como tipo_maquinaria  
            - Si el usuario pregunta "necesito [tipo]" → extraer [tipo] como tipo_maquinaria
            - Ejemplos: "¿tienen generadores?" → {{"tipo_maquinaria": "generador"}}
            - Ejemplos: "¿manejan soldadoras?" → {{"tipo_maquinaria": "soldadora"}}
            - Ejemplos: "necesito un compresor" → {{"tipo_maquinaria": "compresor"}}
            IMPORTANTE: Incluso en preguntas sobre inventario, SIEMPRE extraer tipo_maquinaria si se menciona
            
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
            
            IMPORTANTE: Analiza cuidadosamente el mensaje y extrae TODA la información disponible que corresponda a campos vacíos.
            
            Respuesta (solo JSON):
            """
        )
        
        try:
            # Nombres de tipos de maquinaria
            maquinaria_names = " ".join([f"\"{name.value}\"" for name in MaquinariaType])
            
            current_detalles_str = json.dumps(current_state.get("detalles_maquinaria", {}), ensure_ascii=False)

            response = self.llm.invoke(prompt.format_prompt(
                message=message,
                current_nombre=current_state.get("nombre", "No especificado"),
                current_apellido=current_state.get("apellido", "No especificado"),
                current_tipo=current_state.get("tipo_maquinaria", "No especificado"),
                current_detalles=current_detalles_str,
                current_sitio_web=current_state.get("sitio_web", "No especificado"),
                current_uso=current_state.get("uso_empresa_o_venta", "No especificado"),
                current_nombre_empresa=current_state.get("nombre_empresa", "No especificado"),
                current_giro=current_state.get("giro_empresa", "No especificado"),
                current_lugar_requerimiento=current_state.get("lugar_requerimiento", "No especificado"),
                current_correo=current_state.get("correo", "No especificado"),
                current_telefono=current_state.get("telefono", "No especificado"),
                last_bot_question=last_bot_question or "No hay pregunta previa (inicio de conversación)",
                maquinaria_names=maquinaria_names
            ))
            
            debug_print(f"DEBUG: Respuesta completa del LLM: '{response.content}'")
            
            # Parsear la respuesta JSON
            result = self.parser.parse(response.content)
            return result
            
        except Exception as e:
            print(f"Error extrayendo información: {e}")
            return {}
    
    def get_next_question(self, current_state: ConversationState) -> Optional[str]:
        """
        Determina inteligentemente cuál es la siguiente pregunta necesaria
        generándola de manera conversacional y natural
        """
        
        try:
            # Definir el orden de prioridad de los slots con explicaciones centralizadas
            slot_priority = [
                ("nombre", "¿Con quién tengo el gusto?", "Para brindarte atención personalizada"),
                ("apellido", "¿Cuál es tu apellido?", "Para completar tu información personal"), # Solo se pregunta si en nombre solo dice 1 palabra
                ("tipo_maquinaria", "¿Qué tipo de maquinaria requiere?", "Para revisar nuestro inventario disponible"),
                ("detalles_maquinaria", None, None),  # Se maneja por separado
                ("nombre_empresa", "¿Cuál es el nombre de su empresa?", "Para generar la cotización a nombre de su empresa"),
                ("giro_empresa", "¿Cuál es el giro de su empresa?", "Para entender mejor sus necesidades específicas"), # Se pregunta junto con nombre_empresa
                ("lugar_requerimiento", "¿En qué lugar necesita el equipo?", "Para coordinar la entrega del equipo"),
                ("uso_empresa_o_venta", "¿El equipo es para uso de la empresa o para venta?", "Para ofrecerle los mejores precios"),
                ("sitio_web", "¿Cuál es el sitio web de su empresa?", "Para conocer mejor su empresa y generar una cotización más precisa"),
                ("correo", "¿Cuál es su correo electrónico?", "Para enviarle la cotización"),
                ("telefono", "¿Cuál es su teléfono?", "Para darle seguimiento personalizado") # TODO: Solo se pregunta si está respondiendo todo de forma fluida
            ]
            
            # Verificar cada slot en orden de prioridad
            for slot_name, question, reason in slot_priority:
                if slot_name == "detalles_maquinaria":
                    # Manejar detalles específicos de maquinaria
                    question_details = self._get_maquinaria_detail_question_with_reason(current_state)
                    if question_details:
                        return question_details
                else:
                    # Verificar si el slot está vacío o tiene respuestas negativas
                    value = current_state.get(slot_name)
                    if not value:
                        # Si se debe preguntar por el nombre de la empresa y no se tiene el giro, preguntar por el giro de la empresa también
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
            print(f"Error generando siguiente pregunta: {e}")
            return None
    
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
        
        required_fields = [
            "tipo_maquinaria", "lugar_requerimiento", "nombre_empresa", "giro_empresa",
            "sitio_web", "uso_empresa_o_venta", "correo", "telefono"
        ]
        
        # Verificar campos básicos
        for field in required_fields:
            value = current_state.get(field)
            if not value or value == "":
                return False
            # Solo considerar válidos los campos con información real, no respuestas negativas
            if value in ["No tiene", "No especificado"]:
                return False
        
        # Verificar detalles de maquinaria
        tipo = current_state.get("tipo_maquinaria")
        detalles = current_state.get("detalles_maquinaria", {})
        
        if not tipo or not detalles:
            return False
        
        # Usar la configuración centralizada para obtener campos obligatorios
        required_fields = get_required_fields_for_tipo(tipo)
        
        return all(
            field in detalles and 
            detalles[field] is not None and 
            detalles[field] != "" and 
            detalles[field] != "No especificado" 
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
        extracted_info: Dict[str, Any], 
        current_state: ConversationState, 
        next_question: str = None, 
        next_question_reason: str = None, 
        is_inventory_question: bool = False
    ) -> str:
        """Genera una respuesta contextual apropiada usando un enfoque conversacional"""
        
        try:
            # Crear prompt conversacional basado en el estilo de llm.py
            prompt_str = """
                Eres un asesor comercial en Alpha C y un asistente de ventas profesional especializado en maquinaria de la empresa.
                Tu trabajo es calificar leads de manera natural y conversacional.
                
                REGLAS IMPORTANTES:
                - Sé amigable pero profesional
                - No te presentes, ni digas palabras como "Hola", "Soy un asesor comercial en Alpha C"
                - Mantén respuestas CORTAS (máximo 50 palabras)

                INFORMACIÓN EXTRAÍDA DEL ÚLTIMO MENSAJE:
                {extracted_info_str}
                
                ESTADO ACTUAL DE LA CONVERSACIÓN:
                - Nombre: {current_nombre}
                - Tipo de maquinaria: {current_tipo}
                - Detalles: {current_detalles}
                - Empresa: {current_empresa}
                - Giro: {current_giro}
                - Lugar requerimiento: {current_lugar_requerimiento}
                - Sitio web: {current_sitio_web}
                - Uso: {current_uso}
                - Correo: {current_correo}
                - Teléfono: {current_telefono}
                
                SIGUIENTE PREGUNTA A HACER: {next_question}
                SOLO MENCIONA LA RAZÓN DE LA SIGUIENTE PREGUNTA SI EL USUARIO LO PREGUNTA: {next_question_reason}
           
                MENSAJE DEL USUARIO: {user_message}

                IMPORTANTE:
                {inventory_instruction}
                
                INSTRUCCIONES:
                1. No repitas información que ya confirmaste anteriormente
                2. {conectors_instruction}
                3. Si hay una siguiente pregunta, hazla de manera natural
                
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

            # Obtener el primer nombre del usuario
            primer_nombre = current_state.get("nombre", "No especificado")
            conectors_instruction = ""
            if primer_nombre:
                primer_nombre = primer_nombre.split()[0]
                
                # Seleccionar frases aleatorias
                confirmation_phrase = get_random_confirmation_phrase()
                question_connector = get_random_question_connector()
                
                conectors_instruction = f"""
                Si el mensaje del usuario proporciona alguna información solicitada, puedes iniciar el mensaje con una expresión breve de confirmación o agradecimiento según creas que sea apropiado, usando una frase como esta:
                - {confirmation_phrase}, {primer_nombre}
                
                También, haz las preguntas como si fueran parte de una charla, usando un conector natural como este:
                - '{question_connector}...'
                """
            else:
                conectors_instruction = "Si solo cuentas con el teléfono en el ESTADO ACTUAL DE LA CONVERSACIÓN, agradece por habernos contactado."

            formatedPrompt = prompt.format_prompt(
                user_message=message,
                extracted_info_str=extracted_info_str,
                current_nombre=current_state.get("nombre", "No especificado"),
                primer_nombre=primer_nombre,
                current_tipo=current_state.get("tipo_maquinaria", "No especificado"),
                current_detalles=json.dumps(current_state.get("detalles_maquinaria", {}), ensure_ascii=False),
                current_sitio_web=current_state.get("sitio_web", "No especificado"),
                current_uso=current_state.get("uso_empresa_o_venta", "No especificado"),
                current_lugar_requerimiento=current_state.get("lugar_requerimiento", "No especificado"),
                current_empresa=current_state.get("nombre_empresa", "No especificado"),
                current_giro=current_state.get("giro_empresa", "No especificado"),
                current_correo=current_state.get("correo", "No especificado"),
                current_telefono=current_state.get("telefono", "No especificado"),
                next_question=next_question or "No hay siguiente pregunta",
                next_question_reason=next_question_reason or "No hay razón para la siguiente pregunta",
                inventory_instruction=inventory_instruction,
                conectors_instruction=conectors_instruction
            )
            
            response = self.llm.invoke(formatedPrompt)
            
            result = response.content.strip()
            debug_print(f"DEBUG: Respuesta conversacional generada: '{result}'")
            return result
            
        except Exception as e:
            print(f"Error generando respuesta conversacional: {e}")
            # Fallback a la lógica simple si no se puede generar la respuesta
            if next_question and next_question_reason:
                return next_question + " " + next_question_reason
            else:
                return "En un momento le responderemos."
    
    def generate_final_response(self, current_state: ConversationState) -> str:
        """Genera la respuesta final cuando la conversación está completa"""
        
        return f"""¡Perfecto, {current_state['nombre']}! 

He registrado toda su información:
- Nombre: {current_state['nombre']}
- Maquinaria: {current_state['tipo_maquinaria'].value}
- Detalles: {json.dumps(current_state['detalles_maquinaria'], indent=2, ensure_ascii=False)}
- Empresa: {current_state['nombre_empresa']}
- Giro: {current_state['giro_empresa']}
- Lugar requerimiento: {current_state['lugar_requerimiento']}
- Uso: {current_state['uso_empresa_o_venta']}
- Sitio web: {current_state['sitio_web']}
- Correo: {current_state['correo']}
- Teléfono: {current_state['telefono']}

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
            print(f"Error detectando pregunta de inventario: {e}")
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
        return {
            "messages": [],
            "nombre": None,
            "apellido": None,
            "tipo_maquinaria": None,
            "detalles_maquinaria": {},
            "sitio_web": None,
            "uso_empresa_o_venta": None,
            "nombre_empresa": None,
            "giro_empresa": None,
            "correo": None,
            "telefono": None,
            "completed": False,
            "lugar_requerimiento": None,
            "conversation_mode": "bot",
            "asignado_asesor": None,
            "hubspot_contact_id": None
        }
    
    def load_conversation(self, user_id: str):
        """Carga la conversación de un usuario específico"""
        self.current_user_id = user_id
        stored_state = self.state_store.get_conversation_state(user_id)
        
        if stored_state:
            self.state = stored_state
            debug_print(f"DEBUG: Estado cargado para usuario {user_id}")
        else:
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

            # Si es el primer mensaje (no hay mensajes anteriores), generar saludo inicial
            if not self.state["messages"]:
                contextual_response += "¡Hola! Soy un asesor comercial en Alpha C. "
            
            # Agregar mensaje del usuario
            self.state["messages"].append({
                "role": "user", 
                "whatsapp_message_id": whatsapp_message_id,
                "content": user_message,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sender": "lead"
            })

            # Extraer TODA la información disponible del mensaje (SIEMPRE)
            # Obtener la última pregunta del bot para contexto
            last_bot_question = self._get_last_bot_question()
            extracted_info = self.slot_filler.extract_all_information(user_message, self.state, last_bot_question)
            debug_print(f"DEBUG: Información extraída: {extracted_info}") 
            
            # Actualizar el contacto en HubSpot
            if hubspot_manager:
                hubspot_manager.update_contact(self.state, extracted_info)

            # Actualizar el estado con la información extraída
            self._update_state_with_extracted_info(extracted_info)
            debug_print(f"DEBUG: Estado después de actualización: {self.state}")

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
                final_response = self.response_generator.generate_final_response(self.state)
                return self._add_message_and_return_response(final_response)
            
            # Obtener la siguiente pregunta necesaria
            next_question = self.slot_filler.get_next_question(self.state)

            if next_question is None:
                debug_print(f"DEBUG: Estado completo: {self.state}")
                final_message = "Gracias por toda la información. Estoy procesando su solicitud."
                return self._add_message_and_return_response(final_message)

            next_question_str = next_question["question"]
            next_question_reason = next_question["reason"]

            debug_print(f"DEBUG: Siguiente pregunta: {next_question_str}")

            next_question_type = next_question['question_type']
            debug_print(f"DEBUG: Tipo de siguiente pregunta: {next_question_type}")

            # Generar respuesta con LLM
            generated_response = self.response_generator.generate_response(
                user_message, 
                extracted_info, 
                self.state, 
                next_question_str, 
                next_question_reason, 
                is_inventory_question
            )
            contextual_response += generated_response

            return self._add_message_and_return_response(contextual_response)
        
        except Exception as e:
            print(f"Error procesando mensaje: {e}")
            return "Disculpe, hubo un error técnico. ¿Podría intentar de nuevo?"
        
    def _add_message_and_return_response(self, response: str) -> str:
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
            if key != "detalles_maquinaria" and current_value and current_value not in ["No especificado", "No tiene", None, ""]:
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
                    print(f"ADVERTENCIA: Tipo de maquinaria inválido '{value}' extraído por el LLM.")
            
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
        
    def _get_last_bot_question(self) -> Optional[str]:
        """Obtiene la última pregunta que hizo el bot para proporcionar contexto"""
        # Buscar el último mensaje del bot en el historial
        for msg in reversed(self.state["messages"]):
            if msg["role"] == "assistant":
                content = msg["content"]
                # Si el mensaje contiene una pregunta, extraerla
                if "?" in content:
                    # Buscar la última línea que contenga una pregunta
                    lines = content.split('\n')
                    for line in reversed(lines):
                        if "?" in line and line.strip():
                            return line.strip()
                    # Si no se encuentra una línea específica, devolver todo el contenido
                    return content
                return content
        return None
    
    def get_conversation_summary(self) -> Dict[str, Any]:
        """Obtiene un resumen completo del lead calificado"""
        return {
            "nombre": self.state["nombre"],
            "apellido": self.state["apellido"],
            "tipo_maquinaria": self.state["tipo_maquinaria"],
            "detalles_maquinaria": self.state["detalles_maquinaria"],
            "nombre_empresa": self.state["nombre_empresa"],
            "sitio_web": self.state["sitio_web"],
            "giro_empresa": self.state["giro_empresa"],
            "lugar_requerimiento": self.state["lugar_requerimiento"],
            "uso_empresa_o_venta": self.state["uso_empresa_o_venta"],
            "correo": self.state["correo"],
            "telefono": self.state["telefono"],
            "conversacion_completa": self.state["completed"],
            "mensajes_total": len(self.state["messages"])
        }
    
    def get_lead_data_json(self) -> str:
        """Obtiene los datos del lead en formato JSON"""
        return json.dumps(self.get_conversation_summary(), indent=2, ensure_ascii=False)
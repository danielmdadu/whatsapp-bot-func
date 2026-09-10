import json
import re
import os
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
import langchain
from ai_prompts import (
    NEGATIVE_RESPONSE_PROMPT, 
    EXTRACTION_PROMPT, 
    RESPONSE_GENERATION_PROMPT, 
)
from maquinaria_config import machinery_config_service, get_required_fields_for_tipo
from state_management import ConversationState, ConversationStateStore, InMemoryStateStore, FIELDS_CONFIG_PRIORITY
from datetime import datetime, timezone
import logging
from odoo_manager import OdooManager
from inventory_service import InventoryService
from company_profile import (
    CoverageStatus,
    build_company_facts,
    build_coverage_disclaimer,
    build_coverage_instruction,
    evaluate_coverage,
    mentions_coverage,
)
from brand_reference import (
    DISPONIBLE_EN_TIPO,
    BrandAvailability,
    build_brand_disclaimer,
    build_brand_facts,
    canonical_brand,
    detect_brand_mentions,
    evaluate_brand_names,
)
from machine_reference import (
    MachineReference,
    detect_machine_reference,
    extract_machine_code_candidate,
    looks_like_machine_code,
    segment_containing_code,
)

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
            value = current_state.get(field)
            # Convert to string to handle boolean values like quiere_cotizacion
            fields_str += f"- {field}: " + (str(value) if value is not None else "") + "\n"
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
            max_tokens=300
        )
    
    def create_inventory_llm(self):
        """Crea un LLM para responder preguntas sobre inventario (temperatura moderada)"""
        return self.create_llm(
            temperature=0.5,  # Temperatura moderada para balance entre precisión y creatividad
            top_p=0.9,       # Top-p moderado
            max_tokens=1000
        )

# ============================================================================
# FUNCIONES HELPER
# ============================================================================

# Preguntas que YA son sobre la máquina. Si el lead manda un código mientras se
# le pregunta una de estas, el código ES la respuesta y no hay digresión que
# reconocer. En cualquier otra pregunta (nombre, apellido, datos de empresa) el
# código interrumpe el flujo y hay que reconocerlo antes de re-preguntar.
_MACHINERY_QUESTION_TYPES = {
    "tipo_maquinaria",
    "detalles_maquinaria",
    "quiere_cotizacion",
    "seleccion_maquina",
}

# Veces que el bot puede preguntar el tipo de maquinaria con la pregunta genérica
# antes de cambiar de estrategia. Si a la tercera seguimos sin tipo, el problema
# no es que el lead no haya contestado (normalmente ya contestó dos veces): es
# que no logramos mapear lo que escribió a un tipo del catálogo. Repetir la misma
# pregunta solo hace que el lead conteste "ya lo dije arriba". En su lugar se le
# muestra la lista literal de tipos que manejamos, que sí es accionable.
_MAX_PREGUNTAS_TIPO_MAQUINARIA = 2

# Lo que un lead escribe cuando busca una REFACCIÓN y no una máquina. En México
# es casi siempre "refacción"; "repuesto" es el sinónimo regional. Se mantiene
# corto a propósito: "pieza" o "parte" aparecen en demasiadas frases que no
# tienen nada que ver ("la parte de arriba", "una pieza clave del proyecto").
_REFACCION_RE = re.compile(r"\brefacc\w*|\brepuest\w*")

# Verbos con los que se PIDE algo. Negar uno de ellos sí cambia la intención;
# negar cualquier otro NO. Esta distinción es el corazón del clasificador:
#
#   "no busco refacciones, quiero la maquina"  → NO quiere refacción
#   "no tengo la refaccion"                    → SÍ quiere una refacción
#   "no se si tienen refacciones"              → SÍ quiere una refacción
#
# Buscar un "no" cerca de la palabra (la solución ingenua) se equivoca en los
# dos últimos casos, que son los más frecuentes.
_VERBOS_PETICION = r"(?:busc|necesit|quier|requier|pid|ocup|solicit|and)\w*"
_NEGACION_PETICION_RE = re.compile(
    rf"\b(?:no|ya\s+no|nunca|tampoco|nada\s+de)\s+(?:me\s+|te\s+|le\s+|se\s+)?{_VERBOS_PETICION}"
)

# El otro modo de descartar: negar el sustantivo directamente ("nada de
# refacciones", "sin refacciones"). Entre el negador y la palabra solo se
# admiten artículos y preposiciones, NUNCA un verbo: así "no TENGO la
# refaccion" y "no SE si tienen refacciones" no caen aquí, que es justo donde
# se rompe la detección ingenua de negación.
_NEGACION_DIRECTA_RE = re.compile(
    r"\b(?:nada\s+de|sin|ni|no)\s+(?:mas\s+)?(?:de\s+)?"
    r"(?:las?\s+|los\s+|unas?\s+|unos\s+)?(?:refacc|repuest)\w*"
)

# Señales de que lo que quiere es la MÁQUINA COMPLETA. No bastan para decidir
# por sí solas (ver _clasificar_intencion_refaccion), solo para dudar.
_COMPRA_MAQUINA_RE = re.compile(
    r"\b(?:maquina|equipo)s?\s+(?:nuev|complet)\w*"
    r"|\b(?:comprar|adquirir|cotizar|cotiza|cotizame|rentar)\s+(?:me\s+)?"
    r"(?:una?\s+|el\s+|la\s+|los\s+|las\s+)?(?:maquina|equipo)s?\b"
)


def _clasificar_intencion_refaccion(message: Optional[str]) -> str:
    """
    Clasifica la intención del mensaje respecto a refacciones.

    Devuelve:
      "refaccion"  – pide una refacción; el flujo debe ir a `tipo_ayuda="otro"`.
      "maquina"    – descarta explícitamente la refacción y quiere la máquina.
      "indefinido" – no hay señal, o las señales se contradicen: NO decidimos
                     aquí y lo resuelve la extracción del LLM.

    El daño de equivocarse es ASIMÉTRICO y por eso el clasificador es cauto:
    encaminar por error a un comprador hacia refacciones cuesta una venta,
    mientras que dejar pasar una refacción al flujo de maquinaria solo cuesta
    unos turnos. Ante evidencia contradictoria se devuelve "indefinido".
    """
    normalizado = _normalize_measurement_text(message)
    if not _REFACCION_RE.search(normalizado):
        return "indefinido"

    # Descarte explícito: se niega el verbo con el que se pide la refacción,
    # o se niega la refacción misma.
    if _NEGACION_PETICION_RE.search(normalizado) or _NEGACION_DIRECTA_RE.search(normalizado):
        return "maquina"

    # Menciona la refacción Y la compra de una máquina en el mismo mensaje.
    # Puede ser cualquiera de las dos ("una refacción para mi cortadora nueva"
    # vs "mejor cotízame la máquina"): sin negación no hay forma determinista
    # de saberlo, así que no se decide aquí.
    # Solo cuenta una intención de compra EXPLÍCITA ("cotízame la máquina").
    # Deliberadamente no se usa "nuevo/nueva + tipo de máquina": "una refacción
    # para mi generador nuevo" describe la máquina que el lead YA tiene, y
    # tratarlo como duda degradaría peticiones de refacción perfectamente claras.
    if _COMPRA_MAQUINA_RE.search(normalizado):
        return "indefinido"

    return "refaccion"


def _menciona_refacciones(message: Optional[str]) -> bool:
    """True si el mensaje pide una refacción de forma inequívoca."""
    return _clasificar_intencion_refaccion(message) == "refaccion"


# Señales que POR SÍ SOLAS identifican a un distribuidor: nombran el rol, no una
# actividad que pueda ser de otra cosa.
_GIRO_DISTRIBUIDOR_FUERTE = ("distribuidor", "distribuidora", "reventa", "revendedor")

# Actividades comerciales que cuentan como distribución salvo que el lead diga
# explícitamente que vende o renta OTRA cosa ("venta de abarrotes"). A secas
# conservan el sentido de la pregunta que originó el dato: "¿te dedicas a la
# venta o renta de maquinaria?".
_GIRO_ACTIVIDAD_COMERCIAL = ("venta", "renta", "alquiler", "arrendamiento", "distribucion")

# Actividades demasiado genéricas para decidir solas: una "comercializadora"
# puede vender alimentos o acero. Solo cuentan si el objeto es maquinaria.
_GIRO_ACTIVIDAD_AMBIGUA = ("comercializadora", "comercializacion", "comercio")

# El objeto que vuelve "de distribución" a una actividad comercial.
_OBJETO_MAQUINARIA = ("maquinaria", "maquina", "equipo", "herramienta", "refaccion", "motor")

_OBJETO_DE_LA_ACTIVIDAD_RE = re.compile(
    r"\b(?:venta|renta|alquiler|arrendamiento|distribucion|comercializacion|comercializadora|comercio)\b"
    r"(?:\s+y\s+\w+)?\s+de\s+(?:la\s+|el\s+|los\s+|las\s+)?(?P<objeto>.{2,60})"
)


def _es_objeto_de_maquinaria(objeto: str) -> bool:
    """True si lo que el lead vende/renta es maquinaria y no otra mercancía."""
    if any(palabra in objeto for palabra in _OBJETO_MAQUINARIA):
        return True
    # El catálogo también sirve de vocabulario: "venta de compresores".
    return bool(machinery_config_service.resolve_type_ids(objeto))


# Orden en que se aplican los campos extraídos. Sin esto, el resultado dependía
# del orden en que el LLM emitiera las llaves de su JSON, que no controlamos:
#
#   "y me puedes cotizar también una plataforma de 10 metros" con el estado en
#   tipo_maquinaria="generador" devolvía {detalles: {altura_trabajo_m: 10},
#   tipo_maquinaria: "plataforma"}. Al procesar los detalles PRIMERO se
#   guardaba la altura, y enseguida la limpieza por cambio de tipo la borraba:
#   el bot quedaba pidiendo la altura de trabajo para siempre (6 veces
#   seguidas en el Flujo 9) sobre un dato que el lead ya había dado.
#
# La limpieza por cambio de tipo debe descartar los detalles de la máquina
# ANTERIOR, no los de la nueva que llegaron en el mismo mensaje. Fijar el orden
# lo garantiza sin importar qué mande el LLM.
_PRIORIDAD_DE_CAMPO = {
    "tipo_ayuda": 0,
    # Antes que los detalles: su limpieza deja el terreno listo para ellos.
    "tipo_maquinaria": 1,
    # 'apellido' se concatena sobre 'nombre', así que el nombre va primero.
    "nombre": 2,
    "apellido": 3,
    # Al final: se fusionan sobre los detalles ya depurados.
    "detalles_maquinaria": 99,
}
_PRIORIDAD_POR_DEFECTO = 50


def _orden_de_aplicacion(item: Tuple[str, Any]) -> Tuple[int, str]:
    key = item[0]
    return (_PRIORIDAD_DE_CAMPO.get(key, _PRIORIDAD_POR_DEFECTO), key)


def _is_distribuidor(giro: str) -> bool:
    """
    True si el giro corresponde a un distribuidor de maquinaria.

    Antes bastaba con que el texto contuviera "venta" o "renta" en cualquier
    parte, sin mirar DE QUÉ: una empresa cuyo giro es "venta de abarrotes" o
    "renta de inmuebles" quedaba marcada como distribuidora de maquinaria y se
    le negaba la cotización directa. Del otro lado, "comercializadora de
    maquinaria" —que sí lo es— no daba ninguna coincidencia.
    """
    if not giro:
        return False

    g = _normalizar_respuesta_corta(giro)
    if not g:
        return False

    if any(palabra in g for palabra in _GIRO_DISTRIBUIDOR_FUERTE):
        return True

    match = _OBJETO_DE_LA_ACTIVIDAD_RE.search(g)
    if match:
        # El lead dijo explícitamente qué vende o renta: decide el objeto.
        return _es_objeto_de_maquinaria(match.group("objeto"))

    # Sin objeto explícito, solo las actividades inequívocas cuentan.
    return any(palabra in g for palabra in _GIRO_ACTIVIDAD_COMERCIAL)


# Muletillas, confirmaciones y evasivas que NO describen la actividad de una
# empresa. El lead contesta "Claro" a "¿me compartes el giro?" queriendo decir
# "sí, ahorita te lo doy", y tanto el extractor del LLM como la inferencia por
# contexto de la pregunta lo guardaban tal cual: se vio en producción un lead
# con giro_empresa="Claro".
#
# No es solo un dato feo. `giro_empresa` se escribe en Odoo y además ALIMENTA UNA
# DECISIÓN DE NEGOCIO: la reclasificación distribuidor → cliente_final consulta
# `_is_distribuidor(giro)`, así que una muletilla guardada como giro cambia la
# clasificación comercial del lead.
_RESPUESTAS_QUE_NO_SON_GIRO = {
    # Confirmaciones
    "si", "s", "sip", "claro", "claro que si", "por supuesto", "ok", "okay",
    "oka", "va", "vale", "sale", "dale", "listo", "correcto", "exacto",
    "asi es", "de acuerdo", "entendido", "enterado", "perfecto", "excelente",
    "bueno", "buena", "aja", "ajam", "va que va",
    # Negaciones y evasivas
    "no", "nop", "nel", "no se", "no lo se", "no sabria", "no sabria decirte",
    "ninguno", "ninguna", "nada", "na", "n/a", "cualquiera", "lo que sea",
    "despues te digo", "luego te digo", "al rato", "ahorita no",
    # Cortesías y saludos
    "gracias", "muchas gracias", "hola", "buenas", "buen dia", "buenos dias",
    "buenas tardes", "buenas noches", "gusto en saludarte",
}


def _normalizar_respuesta_corta(value: str) -> str:
    """Minúsculas, sin acentos y sin puntuación de orilla, para comparar contra el set."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", sin_acentos.lower().strip(" .,;:!¡?¿\"'()")).strip()


def _is_valid_business_activity(value: Any) -> bool:
    """
    Descarta lo que el extractor confunda con el giro de la empresa.

    Rechaza datos de contacto (correos, URLs), respuestas que no describen una
    actividad ("Claro", "Si", "no sé") y valores sin una sola letra ("123").

    NO rechaza el centinela "No especificado": ese valor lo produce
    `detect_negative_response` cuando el lead se niega a dar el dato, y es lo
    único que destraba el flujo — sin él, `get_pending_empresa_fields` seguiría
    pidiendo el giro para siempre. Lo que sí hace es que ese centinela no se
    use para tomar decisiones de negocio (ver la reclasificación de tipo_cliente).
    """
    if not isinstance(value, str):
        return False

    activity = value.strip()
    if not activity or "\n" in activity or "\r" in activity:
        return False
    if re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", activity):
        return False
    if re.search(r"(?:https?://|www\.)", activity, re.IGNORECASE):
        return False

    normalizado = _normalizar_respuesta_corta(activity)
    if not normalizado:
        return False
    if normalizado in _RESPUESTAS_QUE_NO_SON_GIRO:
        return False
    # Un giro siempre tiene letras: "123", "55 1234 5678" o "---" no lo son.
    if not re.search(r"[a-z]", normalizado):
        return False
    return True


# Negarse a dar el dato no se puede enumerar en una lista: "prefiero no decirlo",
# "no te lo puedo compartir", "es confidencial", "¿para qué lo necesitas?". Se
# detecta la FORMA de la negativa (negación pegada a un verbo de decir/dar), la
# misma técnica que se usa para las refacciones.
_VERBOS_DE_DECIR = r"(?:dec\w*|dar\w*|doy|compartir\w*|proporcionar\w*|responder\w*|contestar\w*|mencionar\w*|inform\w*)"
_NEGATIVA_A_RESPONDER_RE = re.compile(
    r"\bprefiero\s+no\b"
    rf"|\bno\s+(?:te\s+|le\s+|se\s+|lo\s+)*(?:puedo|quiero|deseo|voy\s+a|pienso)\s+(?:te\s+|lo\s+)?{_VERBOS_DE_DECIR}"
    rf"|\bno\s+(?:te\s+|lo\s+)?{_VERBOS_DE_DECIR}"
    r"|\bconfidencial\b"
    r"|\bsin\s+comentarios\b"
    r"|\bpara\s+que\s+(?:lo\s+)?(?:quieres|necesitas|sirve)\b"
    r"|\bno\s+(?:lo\s+)?(?:se|sabria)\b"
)


def _es_negativa_a_responder(value: Optional[str]) -> bool:
    """True si el lead se está negando a dar el dato en vez de dándolo."""
    if not value:
        return False
    return bool(_NEGATIVA_A_RESPONDER_RE.search(_normalizar_respuesta_corta(value)))


def _normalizar_giro_empresa(value: Any) -> Optional[str]:
    """
    Decide QUÉ guardar como giro: el texto, el centinela, o nada.

    Tres resultados distintos, y la diferencia importa:
      - texto        → el lead sí describió su actividad.
      - "No especificado" → el lead se NEGÓ a decirlo. Hay que guardar algo o
        `get_pending_empresa_fields` sigue pidiendo el giro para siempre, pero
        ese algo no debe ser la frase con la que se negó ("prefiero no decirlo"
        no es un giro, y encima acabaría en Odoo y en la reclasificación de
        tipo_cliente).
      - None         → no hay dato utilizable; se vuelve a preguntar.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if _es_negativa_a_responder(value):
        return "No especificado"
    if not _is_valid_business_activity(value):
        return None
    return value.strip()


def _giro_es_informativo(giro: Optional[str]) -> bool:
    """
    True si el giro sirve para decidir algo, no solo para destrabar el flujo.

    "No especificado" se guarda a propósito cuando el lead no quiere dar el dato,
    pero NO es evidencia de nada: tratarlo como "no es distribuidor" reclasificaba
    a cliente_final a un lead que nunca contestó.
    """
    if not giro:
        return False
    return _normalizar_respuesta_corta(giro) not in ("no especificado", "no tiene")


_MEASUREMENT_UNIT_PATTERNS = {
    "amps": (r"\bamps?\b", r"\bamper(?:e|es|io|ios)\b"),
    "cfm": (r"\bcfm\b",),
    "kw": (r"\bkw\b", r"\bkilowatts?\b", r"\bkilovatios?\b"),
    "toneladas": (r"\btoneladas?\b", r"\btons?\b"),
    "m": (r"\bmetros?\b", r"\b\d+(?:[.,]\d+)?\s*m\b"),
    "litros": (r"\blitros?\b", r"\b\d+(?:[.,]\d+)?\s*l(?:/min)?\b"),
    "m3_h": (r"\bm3/h\b", r"\bm3 por hora\b", r"\bmetros cubicos por hora\b"),
    "galones": (r"\bgalones?\b", r"\bgallons?\b", r"\bgpm\b"),
    "psi": (r"\bpsi\b",),
    "bar": (r"\bbar\b",),
    "kpa": (r"\bkpa\b",),
    "kva": (r"\bkva\b",),
    "hp": (r"\bhp\b", r"\bcaballos de fuerza\b"),
    "kg": (r"\bkg\b", r"\bkilogramos?\b"),
    "libras": (r"\blbs?\b", r"\blibras?\b"),
    "pies": (r"\bft\b", r"\bpies?\b"),
    "cm": (r"\bcm\b", r"\bcentimetros?\b"),
    "mm": (r"\bmm\b", r"\bmilimetros?\b"),
}

_CONFIG_UNIT_KEYS = {
    "amp": "amps",
    "amps": "amps",
    "cfm": "cfm",
    "kw": "kw",
    "tonelada": "toneladas",
    "toneladas": "toneladas",
    "m": "m",
}

_QUESTION_STOPWORDS = {
    "cual", "cuanto", "cuanta", "que", "para", "necesitas", "necesita",
    "requiere", "requieres", "del", "las", "los", "una", "uno", "por",
}


def _normalize_measurement_text(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _contains_measurement_unit(text: str, unit_key: str) -> bool:
    return any(re.search(pattern, text) for pattern in _MEASUREMENT_UNIT_PATTERNS.get(unit_key, ()))


def _question_targets_field(question: Optional[str], field_question: str, expected_unit: str) -> bool:
    normalized_question = _normalize_measurement_text(question)
    if not normalized_question:
        return False
    if _contains_measurement_unit(normalized_question, expected_unit):
        return True

    configured_terms = {
        term for term in re.findall(r"\b[a-z0-9]+\b", _normalize_measurement_text(field_question))
        if len(term) >= 4 and term not in _QUESTION_STOPWORDS
    }
    return bool(configured_terms.intersection(re.findall(r"\b[a-z0-9]+\b", normalized_question)))


def _discard_values_with_incompatible_units(
    extracted_info: Dict[str, Any],
    message: str,
    last_bot_question: Optional[str],
    current_state: Optional[ConversationState],
) -> None:
    details = extracted_info.get("detalles_maquinaria")
    if not isinstance(details, dict):
        return

    machine_type = extracted_info.get("tipo_maquinaria")
    if not machine_type and current_state:
        machine_type = current_state.get("tipo_maquinaria")
    config = machinery_config_service.get_config(machine_type) if machine_type else None
    if not config:
        return

    normalized_message = _normalize_measurement_text(message)
    sanitized_details = dict(details)

    for field in config.fields:
        if field.name not in sanitized_details or field.type != "number" or not field.unit:
            continue

        expected_unit = _CONFIG_UNIT_KEYS.get(_normalize_measurement_text(field.unit))
        if not expected_unit:
            continue

        has_incompatible_unit = any(
            unit_key != expected_unit and _contains_measurement_unit(normalized_message, unit_key)
            for unit_key in _MEASUREMENT_UNIT_PATTERNS
        )
        has_expected_unit = _contains_measurement_unit(normalized_message, expected_unit)
        is_direct_answer = _question_targets_field(last_bot_question, field.question, expected_unit)

        if has_incompatible_unit or (not has_expected_unit and not is_direct_answer):
            sanitized_details.pop(field.name)
            logging.warning(
                "Descartado %s: el mensaje no usa la unidad requerida %s.",
                field.name,
                field.unit,
            )

    if sanitized_details:
        extracted_info["detalles_maquinaria"] = sanitized_details
    else:
        extracted_info.pop("detalles_maquinaria", None)


def _build_unit_clarification_response(
    message: str,
    current_state: ConversationState,
) -> Optional[str]:
    machine_type = current_state.get("tipo_maquinaria")
    config = machinery_config_service.get_config(machine_type) if machine_type else None
    if not config:
        return None

    normalized_message = _normalize_measurement_text(message)
    details = current_state.get("detalles_maquinaria")
    details = details if isinstance(details, dict) else {}

    for field in config.fields:
        if field.type != "number" or not field.unit or details.get(field.name) not in (None, ""):
            continue

        expected_unit = _CONFIG_UNIT_KEYS.get(_normalize_measurement_text(field.unit))
        if not expected_unit:
            continue

        has_incompatible_unit = any(
            unit_key != expected_unit and _contains_measurement_unit(normalized_message, unit_key)
            for unit_key in _MEASUREMENT_UNIT_PATTERNS
        )
        if has_incompatible_unit:
            return (
                f"Por ahora no puedo convertir otras unidades a {field.unit}. "
                f"Para continuar, necesito que me compartas el valor directamente en {field.unit}."
            )

    return None


def _is_explicit_catalog_request(message: str) -> bool:
    normalized = _normalize_measurement_text(message)
    words = set(re.findall(r"\b[a-z0-9]+\b", normalized))
    question_words = {"que", "cuales"}
    catalog_nouns = {"maquina", "maquinas", "maquinaria", "equipo", "equipos", "producto", "productos"}
    catalog_verbs = {"maneja", "manejan", "tiene", "tienen", "vende", "venden", "ofrece", "ofrecen"}

    known_type_tokens = {
        token
        for machinery_type in machinery_config_service.get_all_types()
        for token in re.findall(
            r"\b[a-z0-9]+\b",
            _normalize_measurement_text(
                f"{machinery_type.type_id} "
                f"{machinery_config_service.get_type_display_name(machinery_type.type_id)}"
            ),
        )
        if len(token) >= 4
    }
    mentions_specific_type = bool(words.intersection(known_type_tokens))

    if "catalogo" in words or "lista" in words:
        return not mentions_specific_type

    asks_general_offering = bool(
        words.intersection(question_words)
        and words.intersection(catalog_nouns)
        and words.intersection(catalog_verbs)
    )
    asks_types = bool(
        words.intersection(question_words)
        and words.intersection({"tipo", "tipos"})
        and words.intersection({"maquinaria", "maquina", "maquinas", "equipo", "equipos"})
    )
    asks_what_is_sold = "que" in words and bool(words.intersection({"vende", "venden", "ofrece", "ofrecen"}))

    return asks_general_offering or asks_types or (asks_what_is_sold and not mentions_specific_type)


def _build_catalog_response(next_question: Optional[str] = None) -> str:
    catalog = ", ".join(machinery_config_service.get_type_display_list())
    response = f"En Alpha C manejamos: {catalog}."
    if next_question:
        response += f"\n\n{next_question}"
    return response


def _sanitize_extracted_info(
    extracted_info: Dict[str, Any],
    message: Optional[str] = None,
    last_bot_question: Optional[str] = None,
    current_state: Optional[ConversationState] = None,
) -> Dict[str, Any]:
    """Elimina valores inválidos antes de persistirlos en estado o CRM."""
    sanitized = dict(extracted_info)
    if (
        sanitized.get("tipo_cliente") == "No tiene"
        and last_bot_question
        and any(
            phrase in _normalize_measurement_text(last_bot_question)
            for phrase in (
                "venta o renta",
                "venta/renta",
                "para venta",
                "uso propio",
                "uso de la empresa",
            )
        )
    ):
        sanitized["tipo_cliente"] = "cliente_final"

    giro = sanitized.get("giro_empresa")
    if giro is not None:
        giro_normalizado = _normalizar_giro_empresa(giro)
        if giro_normalizado is None:
            sanitized.pop("giro_empresa")
            logging.warning(
                "Descartado giro_empresa %r: contiene datos de contacto o no describe una actividad.",
                giro,
            )
        elif giro_normalizado != giro:
            sanitized["giro_empresa"] = giro_normalizado
            logging.info("giro_empresa %r es una negativa a responder; se guarda el centinela.", giro)
    if message is not None:
        _discard_values_with_incompatible_units(
            sanitized,
            message,
            last_bot_question,
            current_state,
        )
    return sanitized

def _is_compresor_estacionario(current_state: dict) -> bool:
    """Verifica si el lead está solicitando un compresor estacionario.
    En ese caso, el bot no cotiza automáticamente y deriva a un asesor."""
    if current_state.get("tipo_maquinaria") != "compresor":
        return False
    detalles = current_state.get("detalles_maquinaria", {})
    tipo_compresor = str(detalles.get("tipo_compresor", "")).lower()
    return "estacionario" in tipo_compresor or "electrico" in tipo_compresor or "eléctrico" in tipo_compresor

def _format_machine_details(machine: Dict[str, Any]) -> str:
    """Extrae las características técnicas de una máquina en un string amigable."""
    ignore_keys = {"modelo", "categoria", "id", "_rid", "_self", "_etag", "_attachments", "_ts", "precio", "moneda"}
    details = []
    for key, value in machine.items():
        if key not in ignore_keys and value is not None and str(value).strip() != "":
            # Convert keys: "altura_trabajo_m" -> "Altura Trabajo M"
            label = " ".join(word.capitalize() for word in key.split("_"))
            details.append(f"{label}: {value}")
    
    if details:
        return f" ({', '.join(details)})"
    return ""

def get_pending_empresa_fields(current_state: ConversationState) -> List[str]:
    """
    Extrae los campos pendientes de la empresa según el flujo de venta o uso propio.
    Retorna una lista con los labels de los campos que aún no han sido respondidos.
    """
    uso = current_state.get("tipo_cliente")
    
    # 1. Primer bloque: uso, correo, ubicacion
    if not uso:
        pending = ["si te dedicas a la venta/renta de maquinaria"]
        if not current_state.get("correo"):
            pending.append("correo electrónico")
        if not current_state.get("lugar_requerimiento"):
            pending.append("ubicación (estado de la República Mexicana)")
        return pending

    pending_basic = []
    if not current_state.get("correo"):
        pending_basic.append("correo electrónico")
    if not current_state.get("lugar_requerimiento"):
        pending_basic.append("ubicación (estado de la República Mexicana)")
        
    if uso == "distribuidor":
        constancia = current_state.get("constancia_fiscal_entregada")
        if constancia is None:
            return pending_basic + ["Constancia de Situación Fiscal"]
        elif constancia == "No tiene" or constancia is False:
            giro = current_state.get("giro_empresa")
            if not giro:
                return pending_basic + ["Giro de la empresa"]
            
            is_distribuidor = _is_distribuidor(giro)
            if is_distribuidor:
                return pending_basic
            else:
                if not current_state.get("nombre_empresa"):
                    return pending_basic + ["Nombre de la empresa"]
                return pending_basic
        else:
            return pending_basic
            
    else: # uso == "cliente_final"
        if not current_state.get("nombre_empresa"):
            pending_basic.append("Nombre de la empresa")
        if not current_state.get("giro_empresa"):
            pending_basic.append("Giro de la empresa")
            
        return pending_basic


# Cómo se le pide al lead cada campo pendiente cuando es el ÚNICO que falta.
# Las llaves son los labels que devuelve get_pending_empresa_fields().
_EMPRESA_FIELD_SINGLE_QUESTION = {
    "si te dedicas a la venta/renta de maquinaria":
        "Para continuar con la cotización, ¿te dedicas a la venta o renta de maquinaria?",
    "correo electrónico":
        "Para continuar con la cotización, ¿me podrías compartir tu correo electrónico?",
    "ubicación (estado de la República Mexicana)":
        "Para continuar con la cotización, ¿en qué estado de la República Mexicana requieres el equipo?",
    "Constancia de Situación Fiscal":
        "Para poder brindarle un precio preferencial como distribuidor, le pido de favor "
        "que me comparta por este medio su Constancia de Situación Fiscal.",
    "Giro de la empresa":
        "Para continuar, ¿me podrías indicar cuál es el giro de tu empresa?",
    "Nombre de la empresa":
        "Para generar la cotización, ¿me podrías indicar el nombre de tu empresa?",
}

# Cómo se enumera cada campo cuando falta más de uno.
_EMPRESA_FIELD_LIST_ITEM = {
    "si te dedicas a la venta/renta de maquinaria": "¿Te dedicas a la venta o renta de maquinaria?",
    "correo electrónico": "Correo electrónico",
    "ubicación (estado de la República Mexicana)": "Estado de la República Mexicana",
}

# Palabras que delatan que la respuesta generada SÍ nombró el campo pendiente.
# Se usan como red de seguridad, no para validar la redacción exacta.
_EMPRESA_FIELD_KEYWORDS = {
    "si te dedicas a la venta/renta de maquinaria": ("venta", "renta", "distribuidor"),
    "correo electrónico": ("correo", "email", "e-mail"),
    "ubicación (estado de la República Mexicana)": ("estado", "ubicaci"),
    "Constancia de Situación Fiscal": ("constancia", "csf", "situación fiscal", "situacion fiscal"),
    "Giro de la empresa": ("giro",),
    "Nombre de la empresa": ("nombre de", "razón social", "razon social"),
}


def build_datos_empresa_question(pending_fields: List[str]) -> str:
    """
    Texto autocontenido para pedir los datos de empresa que faltan.

    SIEMPRE nombra los campos pendientes. Este texto viaja al LLM como
    "SIGUIENTE PREGUNTA A HACER" y además se usa como fallback, y el LLM lo
    repite literal con frecuencia; cuando era genérico ("Necesito los
    siguientes datos de su empresa para continuar con la cotización.") el lead
    recibía la petición sin saber qué datos faltaban y reenviaba los mismos
    una y otra vez (ver real_conversations_withLeads/6.json).
    """
    if not pending_fields:
        return ""

    if len(pending_fields) == 1:
        field = pending_fields[0]
        return _EMPRESA_FIELD_SINGLE_QUESTION.get(
            field, f"Para continuar con la cotización necesito un dato más: {field}"
        )

    items = [_EMPRESA_FIELD_LIST_ITEM.get(f, f) for f in pending_fields]
    numbered = "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))
    return f"Para avanzar con la cotización necesito algunos datos de tu empresa.\n{numbered}"


def response_omite_campos_pendientes(response: str, pending_fields: List[str]) -> bool:
    """
    True si la respuesta generada no nombra NI UNO de los campos pendientes.
    En ese caso el lead no tiene forma de saber qué se le está pidiendo.
    """
    if not pending_fields or not response:
        return bool(pending_fields)

    lowered = response.lower()
    for field in pending_fields:
        for keyword in _EMPRESA_FIELD_KEYWORDS.get(field, (field.lower(),)):
            if keyword in lowered:
                return False
    return True

# ============================================================================
# SISTEMA DE SLOT-FILLING INTELIGENTE
# ============================================================================

class IntelligentSlotFiller:
    """Sistema inteligente de slot-filling que detecta información ya proporcionada"""
    
    def __init__(self, azure_config: AzureOpenAIConfig):
        self.llm = azure_config.create_extraction_llm()  # Usar LLM optimizado para extracción
        self.parser = JsonOutputParser()
    
    def _parse_json_robust(self, text: str) -> dict:
        """
        Parsing robusto de JSON que maneja:
        - JSON envuelto en bloques de código markdown (```json ... ```)
        - Texto extra antes/después del JSON
        - Respuestas con explicaciones antes del JSON
        Retorna un dict o lanza una excepción si no se puede parsear.
        """
        if not text or not text.strip():
            return {}
        
        original_text = text
        text = text.strip()
        
        # 1. Intentar parseo directo
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        
        # 2. Intentar extraer JSON de bloques de código markdown
        code_block_match = re.search(r'```(?:json)?\s*\n?(\{[\s\S]*?\})\s*\n?```', text)
        if code_block_match:
            try:
                result = json.loads(code_block_match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
        
        # 3. Intentar encontrar la primera aparición de un objeto JSON { ... }
        brace_match = re.search(r'(\{[\s\S]*\})', text)
        if brace_match:
            try:
                result = json.loads(brace_match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
        
        # 4. Intentar con el parser de LangChain como último recurso
        try:
            result = self.parser.parse(original_text)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        
        raise ValueError(f"No se pudo extraer JSON válido del texto: {text[:200]}")
        
    def detect_negative_response(self, message: str, last_bot_question: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Detecta si el usuario está dando una respuesta negativa o de incertidumbre.
        Retorna un diccionario con el tipo de respuesta y el campo específico, o None si no es una respuesta negativa.
        Formato: {"response_type": "No tiene" o "No especificado", "field": "nombre_del_campo"}
        """
        prompt = NEGATIVE_RESPONSE_PROMPT
        
        try:
            # Obtener campos disponibles desde el FIELDS_CONFIG_PRIORITY
            fields_available = self._get_fields_available_str()

            response = self.llm.invoke(prompt.format_prompt(
                message=message,
                last_bot_question=last_bot_question or "No hay pregunta previa",
                fields_available=fields_available
            ))
            
            result = response.content.strip()
            
            # Verificar si es "None" (no es respuesta negativa)
            if result.lower().strip('"\'') == "none":
                return None
            
            # Intentar parsear como JSON con método robusto
            try:
                parsed_result = self._parse_json_robust(result)
                if isinstance(parsed_result, dict) and "response_type" in parsed_result and "field" in parsed_result:
                    return parsed_result
                else:
                    return None
            except (ValueError, json.JSONDecodeError):
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
        
        extracted_data = {}

        if negative_response:
            # Si es una respuesta negativa, guardar el campo y valor
            field_name = negative_response.get("field")
            response_type = negative_response.get("response_type")
            
            if field_name and response_type:
                if field_name == "tipo_cliente" and response_type == "No tiene":
                    response_type = "cliente_final"
                extracted_data[field_name] = response_type
        
        # SEGUNDO: Extraer el resto de la información usando el prompt general
        # Crear prompt que considere el estado actual y la última pregunta del bot
        prompt = EXTRACTION_PROMPT
        
        try:
            # Nombres de tipos de maquinaria
            # OBTENER DINÁMICAMENTE LOS NOMBRES DESDE LA CONFIGURACIÓN (Strings)
            maquinaria_names = " ".join([f"\"{m.type_id}\"" for m in machinery_config_service.get_all_types()])

            # Obtener campos disponibles desde el FIELDS_CONFIG_PRIORITY
            fields_available = self._get_fields_available_str()

            # Campos específicos de los tipos de maquinaria EN JUEGO: el que ya
            # está en el estado y, sobre todo, cualquiera que el lead nombre en
            # ESTE mensaje.
            #
            # El prompt es estricto ("NO extraigas campos que no estén en esta
            # lista"), así que declarar solo los del tipo anterior le PROHIBÍA al
            # extractor emitir los detalles del tipo nuevo. Un lead que cambia de
            # máquina a media conversación da el tipo y su detalle en la misma
            # frase —"y también una plataforma de 10 metros de altura"— con el
            # estado todavía en "generador": `altura_trabajo_m` no estaba
            # permitido, el 10 se perdía, y el bot se quedaba pidiendo la altura
            # que el lead ya había dado (6 veces seguidas en el Flujo 9).
            machine_type = current_state.get("tipo_maquinaria")
            tipos_en_juego: List[str] = []
            if machine_type:
                tipos_en_juego.append(machine_type)
            for tipo_mencionado in machinery_config_service.resolve_type_ids(message):
                if tipo_mencionado not in tipos_en_juego:
                    tipos_en_juego.append(tipo_mencionado)

            field_instructions = []
            for tipo in tipos_en_juego:
                config = machinery_config_service.get_config(tipo)
                if not config:
                    continue
                for field in config.fields:
                    unit_instruction = f", unidad obligatoria: {field.unit}" if field.unit else ""
                    field_instructions.append(
                        f"- Para {tipo.upper()}: {field.name} "
                        f"({field.question}{unit_instruction})"
                    )
            machine_specific_fields = "\n".join(field_instructions)

            if not machine_specific_fields:
                machine_specific_fields = "- No hay un tipo de maquinaria seleccionado aún, o no hay configuración específica."

            # Formatear la lista de máquinas recomendadas para el prompt
            recomendadas = current_state.get("maquinas_recomendadas", [])
            if recomendadas:
                maquinas_lines = [f"  {i+1}. {modelo}" for i, modelo in enumerate(recomendadas)]
                maquinas_recomendadas_str = "\n".join(maquinas_lines)
            else:
                maquinas_recomendadas_str = "  (No hay máquinas recomendadas aún)"

            response = self.llm.invoke(prompt.format_prompt(
                message=message,
                current_state_str=get_current_state_str(current_state),
                last_bot_question=last_bot_question or "No hay pregunta previa (inicio de conversación)",
                maquinaria_names=maquinaria_names,
                fields_available=fields_available,
                machine_specific_fields=machine_specific_fields,
                maquinas_recomendadas_str=maquinas_recomendadas_str
            ))
            
            # Parsear la respuesta JSON con método robusto
            raw_content = response.content
            try:
                general_extraction = self._parse_json_robust(raw_content)
            except ValueError as parse_err:
                logging.error(f"Error parseando JSON de extracción. Respuesta del LLM: '{raw_content[:300]}'. Error: {parse_err}")
                general_extraction = {}
            
            # Fusionar resultados (la extracción general tiene prioridad si encuentra algo más específico,
            # pero mantenemos la respuesta negativa si no hay conflicto o si es complementaria)
            if isinstance(general_extraction, dict):
                extracted_data.update(general_extraction)
            
            logging.info(f"Extracción completada. Mensaje: '{message[:50]}' → Datos: {json.dumps(extracted_data, ensure_ascii=False, default=str)}")
            
            # Lógica determinista de selección implícita
            quiere_cot_new = extracted_data.get("quiere_cotizacion")
            quiere_cot_curr = current_state.get("quiere_cotizacion")
            is_quoting = quiere_cot_new is True or quiere_cot_curr is True
            
            if is_quoting and not extracted_data.get("maquina_seleccionada") and not current_state.get("maquina_seleccionada"):
                recomendadas = current_state.get("maquinas_recomendadas", [])
                if isinstance(recomendadas, list) and len(recomendadas) == 1:
                    extracted_data["maquina_seleccionada"] = recomendadas[0]
                    logging.info(f"Seleccionada automáticamente la única opción recomendada: {recomendadas[0]}")
            
            return extracted_data
            
        except Exception as e:
            logging.error(f"Error extrayendo información: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            return extracted_data
    
    def get_next_question(self, current_state: ConversationState) -> Optional[str]:
        """
        Determina inteligentemente cuál es la siguiente pregunta necesaria
        siguiendo el flujo definido en el diagrama PlantUML.
        """
        try:
            # 1. NOMBRE Y APELLIDO
            # Verificar si tenemos el nombre
            nombre = current_state.get("nombre")
            if not nombre:
                return {
                    "question": FIELDS_CONFIG_PRIORITY["nombre"]["question"],
                    "reason": FIELDS_CONFIG_PRIORITY["nombre"]["reason"],
                    "question_type": "nombre"
                }
            
            # Verificar si tenemos el apellido (o si el nombre ya incluye apellido)
            apellido = current_state.get("apellido")
            if not apellido and len(nombre.split()) < 2:
                return {
                    "question": FIELDS_CONFIG_PRIORITY["apellido"]["question"],
                    "reason": FIELDS_CONFIG_PRIORITY["apellido"]["reason"],
                    "question_type": "apellido"
                }

            # 2. TIPO DE AYUDA
            tipo_ayuda = current_state.get("tipo_ayuda")
            if not tipo_ayuda:
                return {
                    "question": FIELDS_CONFIG_PRIORITY["tipo_ayuda"]["question"],
                    "reason": FIELDS_CONFIG_PRIORITY["tipo_ayuda"]["reason"],
                    "question_type": "tipo_ayuda"
                }
            
            # Si el tipo de ayuda es "otro", terminamos el flujo de preguntas
            if tipo_ayuda == "otro":
                return None

            # 3. TIPO DE MAQUINARIA (Solo si tipo_ayuda es "maquinaria")
            tipo_maquinaria = current_state.get("tipo_maquinaria")
            if not tipo_maquinaria:
                return {
                    "question": FIELDS_CONFIG_PRIORITY["tipo_maquinaria"]["question"],
                    "reason": FIELDS_CONFIG_PRIORITY["tipo_maquinaria"]["reason"],
                    "question_type": "tipo_maquinaria"
                }

            # 4. DETALLES DE MAQUINARIA
            # Verificar si faltan detalles específicos
            if (not current_state.get("modelo_verificado_inventario")
                    and not self._are_maquinaria_details_complete(current_state)):
                question_details = self._get_maquinaria_detail_question_with_reason(current_state)
                if question_details:
                    return question_details

            # 5. COTIZACIÓN / INVENTARIO
            # Para compresores estacionarios: saltar recomendaciones, auto-set quiere_cotizacion y pasar a datos_empresa
            if _is_compresor_estacionario(current_state):
                if current_state.get("quiere_cotizacion") is None:
                    current_state["quiere_cotizacion"] = True
                # Saltar directamente a datos_empresa (paso 6)
            else:
                # Si no hemos recomendado máquinas aún, forzamos este paso para que se active la búsqueda de inventario.
                quiere_cotizacion = current_state.get("quiere_cotizacion")
                if not current_state.get("maquinas_recomendadas"):
                    return {
                        "question": FIELDS_CONFIG_PRIORITY["quiere_cotizacion"]["question"],
                        "reason": FIELDS_CONFIG_PRIORITY["quiere_cotizacion"]["reason"],
                        "question_type": "quiere_cotizacion"
                    }
            
            # Si ya se recomendaron máquinas y el usuario no quiere cotización, terminamos
            quiere_cotizacion = current_state.get("quiere_cotizacion")
            if quiere_cotizacion is False:
                return None

            # 5.5 SELECCIÓN DE MÁQUINA (cuando hay múltiples opciones)
            # Si el usuario dijo "sí" pero no especificó cuál máquina, re-preguntar
            recomendadas = current_state.get("maquinas_recomendadas", [])
            maquina_seleccionada = current_state.get("maquina_seleccionada")
            if quiere_cotizacion is True and len(recomendadas) > 1 and not maquina_seleccionada:
                machines_list = ""
                for i, modelo in enumerate(recomendadas, 1):
                    machines_list += f"{i}. {modelo}\n"
                return {
                    "question": f"Perfecto, estas son las opciones disponibles:\n{machines_list}\n¿Cuál de estas opciones te interesa?",
                    "reason": "El usuario no especificó cuál máquina desea cotizar",
                    "question_type": "seleccion_maquina"
                }

            # 6. DATOS DE EMPRESA
            # Si quiere cotización o está pendiente, pedir datos de empresa si faltan
            pending_fields = get_pending_empresa_fields(current_state)
            if len(pending_fields) > 0:
                return {
                    # El texto enumera los campos que faltan: el LLM lo repite
                    # literal muy seguido y también es el fallback si falla la
                    # generación, así que nunca debe ser genérico.
                    "question": build_datos_empresa_question(pending_fields),
                    "reason": "Para generar la cotización",
                    "question_type": "datos_empresa"
                }

            # Si llegamos aquí, tenemos toda la información necesaria
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
    
    def _get_contextual_required_fields(self, current_state: ConversationState) -> list:
        """
        Obtiene los campos requeridos para el tipo de maquinaria actual,
        filtrando campos condicionales según el contexto.
        Ej: tipo_alimentacion solo se requiere para plataformas articuladas.
        """
        tipo = current_state.get("tipo_maquinaria")
        required_fields = get_required_fields_for_tipo(tipo)
        
        # Para plataformas, tipo_alimentacion solo aplica a "articulada"
        if tipo == "plataforma":
            detalles = current_state.get("detalles_maquinaria", {})
            tipo_plataforma = detalles.get("tipo_plataforma", "")
            if tipo_plataforma and tipo_plataforma != "articulada":
                required_fields = [f for f in required_fields if f != "tipo_alimentacion"]
                
        # Para soldadoras, tipo_alimentacion NUNCA se pregunta:
        # - amperaje ≤ 200: solo se recomienda la EGW185MS (gasolina) → no preguntar
        # - amperaje > 200: todas las opciones son diésel → no preguntar
        if tipo == "soldadora":
            required_fields = [f for f in required_fields if f != "tipo_alimentacion"]
        
        # Para compresores estacionarios, saltar CFM (asesor se encarga)
        if _is_compresor_estacionario(current_state):
            required_fields = [f for f in required_fields if f != "caudal_cfm_max"]
        
        return required_fields

    def _are_maquinaria_details_complete(self, current_state: ConversationState) -> bool:
        """Verifica si todos los detalles de maquinaria están completos"""
        tipo = current_state.get("tipo_maquinaria")
        
        if not tipo:
            return False
            
        # Verificar si existe configuración para este tipo
        if not machinery_config_service.get_config(tipo):
            return False
        
        detalles = current_state.get("detalles_maquinaria", {})
        required_fields = self._get_contextual_required_fields(current_state)
        
        return all(
            field in detalles and 
            detalles[field] is not None and 
            detalles[field] != ""
            for field in required_fields
        )
    
    def _get_maquinaria_detail_question_with_reason(self, current_state: ConversationState) -> Optional[dict]:
        """Obtiene la siguiente pregunta específica sobre detalles de maquinaria de manera conversacional con el motivo"""
        
        tipo = current_state.get("tipo_maquinaria")

        config = machinery_config_service.get_config(tipo)
        if not config:
            return None

        detalles = current_state.get("detalles_maquinaria", {})

        # Obtener campos requeridos según contexto (ej: tipo_alimentacion solo para articulada)
        contextual_required = self._get_contextual_required_fields(current_state)

        # Buscar el primer campo de la configuración que no esté en los detalles
        for field_info in config.fields:
            field_name = field_info.name
            # Saltar campos que no aplican en el contexto actual
            if field_name not in contextual_required:
                continue
            if not detalles.get(field_name):
                # Encontrado el siguiente campo a preguntar
                # Devolver la pregunta fija definida en la configuración centralizada
                return {
                    "question": field_info.question, 
                    "reason": field_info.reason, 
                    "question_type": "detalles_maquinaria"
                }

        return None # Todos los detalles están completos
    
    def is_conversation_complete(self, current_state: ConversationState) -> bool:
        """Verifica si la conversación está completa (todos los slots llenos)"""

        # Verificar si el nombre tiene al menos dos palabras (nombre + apellido)
        nombre = current_state.get("nombre", "")
        if not nombre or len(nombre.split()) < 2:
            return False

        # Verificar tipo_ayuda
        tipo_ayuda = current_state.get("tipo_ayuda")
        if not tipo_ayuda:
            return False
        
        # Si tipo_ayuda es "otro", solo se requiere nombre y apellido
        if tipo_ayuda == "otro":
            # Solo verificar nombre y apellido
            nombre = current_state.get("nombre", "")
            if not nombre or len(nombre.split()) < 2:
                return False
            
            return True
        
        # Si tipo_ayuda es "maquinaria", verificar también tipo_maquinaria y detalles_maquinaria
        # Obtener campos obligatorios desde el FIELDS_CONFIG_PRIORITY
        required_fields = [field for field in FIELDS_CONFIG_PRIORITY.keys() if FIELDS_CONFIG_PRIORITY[field]["required"]]
        
        # Verificar campos básicos
        for field in required_fields:
            value = current_state.get(field)
            if not value or value == "":
                return False
        
        # Verificar tipo_maquinaria
        tipo_maquinaria = current_state.get("tipo_maquinaria")
        if not tipo_maquinaria:
            return False
        
        # Un modelo exacto confirmado en Cosmos ya aporta la especificación del
        # equipo; no hace falta volver a preguntar sus detalles técnicos.
        if not current_state.get("modelo_verificado_inventario"):
            detalles = current_state.get("detalles_maquinaria", {})

            if not detalles:
                return False

            # Usar la configuración centralizada para obtener campos obligatorios (con contexto)
            required_fields = self._get_contextual_required_fields(current_state)

            if not all(
                field in detalles and
                detalles[field] is not None and
                detalles[field] != ""
                for field in required_fields
            ):
                return False

        # Verificar si quiere cotización
        quiere_cot = current_state.get("quiere_cotizacion")
        if quiere_cot is None:
            return False
            
        if quiere_cot is True:
            # Reutilizamos get_pending_empresa_fields para validar
            if len(get_pending_empresa_fields(current_state)) > 0:
                return False
            # Para cotización (excepto compresor estacionario), se requiere máquina seleccionada
            if not _is_compresor_estacionario(current_state) and not current_state.get("maquina_seleccionada"):
                return False

        return True

# ============================================================================
# SISTEMA DE RESPUESTAS INTELIGENTES
# ============================================================================

class IntelligentResponseGenerator:
    """Genera respuestas inteligentes basadas en el contexto y la información extraída"""
    
    def __init__(self, azure_config: AzureOpenAIConfig, cosmos_client=None, db_name=None):
        self.llm = azure_config.create_conversational_llm()  # Usar LLM optimizado para conversación
        self.inventory_service = InventoryService(cosmos_client, db_name)
    
    def generate_response(self, 
        message: str, 
        history_messages: List[Dict[str, Any]],
        extracted_info: Dict[str, Any], 
        current_state: ConversationState, 
        next_question: str = None,
        is_inventory_question: bool = False,
        question_type: str = None,
        machine_reference: Optional[MachineReference] = None,
        coverage: Optional[CoverageStatus] = None
    ) -> str:
        """Genera una respuesta contextual apropiada usando un enfoque conversacional"""
        
        try:
            # Crear prompt conversacional basado en el estilo de llm.py
            # Crear prompt conversacional basado en el estilo de llm.py
            prompt = RESPONSE_GENERATION_PROMPT

            # Verificar si es el inicio de la conversación (menos de 2 elementos en history_messages)
            is_initial_conversation = len(history_messages) < 2
            
            # Instrucción de presentación obligatoria si es el inicio
            presentation_instruction = ""
            if is_initial_conversation:
                presentation_instruction = """
                
                PRESENTACIÓN:
                Presentate como Alphi, asesor comercial de Alpha C.
                Si en el primer mensaje del usuario este menciona que requiere algún producto o servicio, o solo quiere más información, dile "Hola, sí claro, puedo ayudarte con eso. Soy Alphi, asesor comercial de Alpha C." y luego haz la pregunta correspondiente.
                Si el usuario NO menciona ninguna necesidad (solo saluda o se presenta), dile "Hola, soy Alphi, asesor comercial de Alpha C." y luego haz la pregunta correspondiente.
                IMPORTANTE: SIEMPRE debes incluir tu nombre y cargo en el PRIMER mensaje.
                """

            # Instrucción para manejar el nombre y apellido del usuario
            extracted_name_instruction = ""

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

                if extracted_info.get("nombre"):
                    nombre = extracted_info.get("nombre")
                    if is_initial_conversation:
                        extracted_name_instruction = f"El usuario ya proporcionó su nombre ({nombre}). Úsalo amablemente en tu saludo, PERO NO dejes de presentarte tú primero."
                    else:
                        extracted_name_instruction = f"El usuario acaba de decir su nombre, así que responde con un 'Gracias, {nombre}.' Y haz la siguiente pregunta."
                elif extracted_info.get("apellido"):
                    if is_initial_conversation:
                        extracted_name_instruction = "El usuario proporcionó su apellido. Tómalo en cuenta."
                    else:
                        extracted_name_instruction = "El usuario acaba de decir su apellido, así que responde con un 'Va.' Y haz la siguiente pregunta, no repitas el nombre ni apellido."
                else:
                    extracted_name_instruction = "No menciones el nombre ni apellido del usuario."

            # Lista autorizada de tipos de maquinaria (nombres amigables). Fuente de
            # verdad única; se inyecta SIEMPRE en el prompt para que el bot nunca
            # invente tipos que no existen en el inventario.
            tipos_maquinaria_validos = ", ".join(machinery_config_service.get_type_display_list())

            # Marcas que pidió el lead, contrastadas contra el inventario. Es la
            # ÚNICA fuente con la que el bot puede afirmar o negar una marca; sin
            # esto el LLM improvisa y se contradice entre un mensaje y otro.
            brand_evaluations = self._pending_brand_evaluations(current_state)

            # Aclaración de cobertura para un lead de fuera de México. Se le
            # sigue calificando igual; solo se le dice una vez el alcance real.
            coverage_pendiente = bool(
                coverage
                and coverage.fuera_de_mexico
                and not current_state.get("cobertura_aclarada")
            )

            if is_inventory_question:
                inventory_instruction = (
                    "El mensaje del usuario incluye una pregunta sobre inventario. "
                    "Enumérale los tipos de maquinaria que manejamos usando EXCLUSIVAMENTE la lista "
                    "'TIPOS DE MAQUINARIA VÁLIDOS'. No agregues, parafrasees a otro producto ni inventes "
                    "tipos que no estén en esa lista, y no uses 'entre otros'."
                )
            else:
                inventory_instruction = "Sigue las instrucciones dadas."

            # Instrucción especial para cuando se pregunta sobre cotización de maquinarias
            if question_type == "quiere_cotizacion":
                # START MODIFICATION: Lógica dinámica de recomendación
                machine_type = current_state.get("tipo_maquinaria")
                detalles = current_state.get("detalles_maquinaria", {})
                
                # Si el lead pidió una marca que SÍ manejamos en este tipo, la
                # recomendación debe ser de esa marca: ofrecerle otra después de
                # confirmarle que la tenemos se lee como que no lo escuchamos.
                marcas_solicitadas_disponibles = [
                    ev.marca for ev in brand_evaluations
                    if ev.estatus == DISPONIBLE_EN_TIPO
                ]

                recommended_machines = []
                if machine_type:
                    recommended_machines = self.inventory_service.find_matching_machines(
                        machine_type, detalles, brands=marcas_solicitadas_disponibles or None
                    )

                # Aclaraciones para este camino, que arma el texto sin LLM.
                brand_disclaimer = build_brand_disclaimer(brand_evaluations)
                if brand_evaluations:
                    current_state["marcas_aclaradas"] = True

                coverage_disclaimer = build_coverage_disclaimer(coverage) if coverage_pendiente else ""
                if coverage_disclaimer:
                    current_state["cobertura_aclarada"] = True

                prefijo_aclaraciones = "\n\n".join(
                    p for p in (coverage_disclaimer, brand_disclaimer) if p
                )
                prefijo_aclaraciones = f"{prefijo_aclaraciones}\n\n" if prefijo_aclaraciones else ""

                if recommended_machines:
                    current_state["sin_coincidencias_contexto"] = None
                    current_state["derivacion_asesor_confirmada"] = False
                    current_state["recordatorios_derivacion_asesor"] = 0
                    # Formatear lista de máquinas recomendadas
                    machines_list = ""
                    recommended_models = []  # Lista de modelos para guardar en el estado
                    for machine in recommended_machines: # Cantidad controlada por filtro de proximidad
                         # Intentar construir un nombre descriptivo
                        modelo = machine.get("modelo", "Modelo Desconocido")
                        recommended_models.append(modelo)  # Guardar modelo
                        cat = machine.get("categoria", "")
                        
                        # Agregar detalles clave según el tipo (simplificado)
                        extra_info = _format_machine_details(machine)
                        
                        warning_msg = ""
                        if machine.get("categoria") == "soldadora" and machine.get("amperaje_amps_max") == 185 and str(machine.get("tipo_alimentacion", "")).lower() == "gasolina":
                            req_amp = detalles.get("amperaje_amps_max")
                            if req_amp is not None:
                                try:
                                    if 185 < float(req_amp) <= 200:
                                        warning_msg = " (Nota: Esta soldadora funcionará dependiendo del tipo de electrodo o la varilla a utilizar)"
                                except ValueError:
                                    pass
                        
                        # Nota para soldadoras de alto amperaje (400A y 500A) que soportan 2 usuarios simultáneos
                        if machine.get("categoria") == "soldadora" and machine.get("amperaje_amps_max", 0) >= 390:
                            warning_msg += " (Nota: Esta soldadora tiene la ventaja de poder ser utilizada por 2 usuarios al mismo tiempo)"
                        
                        # NOTE: Prices are NOT shown in recommendations.
                        machines_list += f"- {modelo}{extra_info}{warning_msg}\n"
                    
                    # Guardar la lista de modelos recomendados en el estado
                    current_state["maquinas_recomendadas"] = recommended_models
                    
                    intro_recomendacion = "la siguiente opción disponible" if len(recommended_models) == 1 else "las siguientes opciones disponibles"
                    # Tras la aclaración de marcas, un "Muy bien" suena a que se
                    # celebró la mala noticia; se enlaza directo.
                    apertura = "Basándome" if prefijo_aclaraciones else "Muy bien, basándome"

                    if current_state.get("quiere_cotizacion") is True:
                        cierre_cotizacion = "Para la cotización que solicitaste, ¿te interesa esta opción?" if len(recommended_models) == 1 else "Para la cotización que solicitaste, ¿te interesa alguna de estas opciones?"
                        return f"""{prefijo_aclaraciones}{apertura} en tus requerimientos, te recomiendo {intro_recomendacion} en nuestro inventario:
{machines_list}

{cierre_cotizacion}"""
                    else:
                        cierre_cotizacion = "¿Te gustaría recibir una cotización formal por esta?" if len(recommended_models) == 1 else "¿Te gustaría recibir una cotización formal por alguna de estas?"
                        return f"""{prefijo_aclaraciones}{apertura} en tus requerimientos, te recomiendo {intro_recomendacion} en nuestro inventario:
{machines_list}
{cierre_cotizacion}"""
                else:
                    # Fallback si no hay coincidencias exactas. El contexto permite
                    # volver a informar si el lead cambia de equipo o especificaciones.
                    no_match_context = json.dumps(
                        {
                            "tipo_maquinaria": machine_type,
                            "detalles_maquinaria": detalles,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                        default=str,
                    )
                    already_informed = current_state.get("sin_coincidencias_contexto") == no_match_context

                    if already_informed:
                        if not current_state.get("derivacion_asesor_confirmada"):
                            current_state["derivacion_asesor_confirmada"] = True
                            return "Perfecto. Un asesor experto se pondrá en contacto contigo para buscar una alternativa."

                        reminders = current_state.get("recordatorios_derivacion_asesor", 0)
                        current_state["recordatorios_derivacion_asesor"] = reminders + 1
                        if reminders == 0:
                            return "Tu solicitud ya quedó registrada. Un asesor se pondrá en contacto contigo por este medio."
                        return "No necesitas volver a solicitarlo; el asesor continuará contigo por este medio en cuanto esté disponible."

                    current_state["sin_coincidencias_contexto"] = no_match_context
                    current_state["derivacion_asesor_confirmada"] = False
                    current_state["recordatorios_derivacion_asesor"] = 0
                    # Con aclaración de marcas previa, el "Entendido." sobra.
                    entendido = "" if prefijo_aclaraciones else "Entendido. "
                    if current_state.get("quiere_cotizacion") is True:
                        current_state["derivacion_asesor_confirmada"] = True
                        return f"""{prefijo_aclaraciones}{entendido}No manejamos una máquina en el inventario con esas características, pero un asesor experto te buscará una alternativa para cotizarte."""
                    else:
                        return f"""{prefijo_aclaraciones}{entendido}No manejamos una máquina en el inventario con esas características, pero tenemos muchas opciones que podrían adaptarse.

¿Te gustaría que un asesor te contacte para ofrecerte una solución personalizada?"""
                # END MODIFICATION

            # Si la pregunta es de selección de máquina (re-preguntar cuál opción quiere),
            # devolver directamente el texto con la lista numerada sin pasar por el LLM
            if question_type == "seleccion_maquina":
                return next_question
            
            # Instrucción especial para datos_empresa
            datos_empresa_instruction = ""
            pending_fields = []
            if question_type == "datos_empresa":
                pending_fields = get_pending_empresa_fields(current_state)
                if not pending_fields:
                    return ""
                
                # La petición sale de build_datos_empresa_question() para que la
                # instrucción, el next_question y el fallback digan exactamente
                # lo mismo y siempre nombren los campos que faltan.
                peticion_explicita = build_datos_empresa_question(pending_fields)
                datos_empresa_instruction = f"""

                INSTRUCCIÓN ESPECIAL PARA RECOPILAR DATOS:
                PASO 1 (OBLIGATORIO): Si el usuario hace una pregunta o comentario, PRIMERO respóndele de forma breve y natural. Por ejemplo, si pregunta sobre estados de entrega, ubicaciones, características, etc., responde a su duda con la información que tengas. EXCEPCIÓN: si pregunta por el precio o costo, NO se lo digas ni inventes una cifra; explícale de forma amable que el precio se incluye en la cotización formal y que para generarla necesitas los datos que le estás solicitando.
                PASO 2: Después de responder, haz una transición natural para pedir los datos pendientes. La transición NO debe empezar con una expresión de confirmación ("Claro", "Perfecto", "Por supuesto", etc.); enlaza directamente con la petición.
                - Pide los datos con este mensaje, respetando su contenido y su formato:
{peticion_explicita}
                - OBLIGATORIO: nombra EXPLÍCITAMENTE cada dato que falta. PROHIBIDO pedir "los siguientes datos", "algunos datos" o "la información" sin decir cuáles son.
                - PROHIBIDO pedir cualquier dato que no aparezca en el mensaje de arriba (por ejemplo: teléfono, nombre de la empresa o correo si no están ahí). Si el usuario ya te dio un dato, NO se lo vuelvas a pedir.
                - Si el mensaje de arriba trae una lista enumerada, consérvala tal cual. NUNCA uses viñetas (•) ni guiones (-); SIEMPRE números (1. 2. 3.).
                - NUNCA menciones información que se extrajo previamente, ni confirmes la información recién extraída, a menos de que el usuario lo pregunte.
                - IMPORTANTE: NO te despidas, NO digas 'Perfecto, con esto terminamos', NO digas 'Gracias por la información' como cierre. Debes dejar claro que FALTAN datos y que la conversación continúa.
                    """

            tipo_ayuda_instruction = ""
            if question_type == "tipo_ayuda":
                tipo_ayuda_instruction = "IMPORTANTÍSIMO: Cuando vayas a preguntar en qué le puedes ayudar al usuario, EXCLUSIVAMENTE usa la frase: '¿En qué te puedo ayudar?' de forma literal y directa, sin agregar texto adicional a la pregunta."
            elif question_type == "post_cierre":
                # El lead rechazó la cotización y ya se le preguntó una vez si
                # necesitaba algo más. Repetir esa pregunta lo dejaba en bucle.
                tipo_ayuda_instruction = """
                SITUACIÓN: el usuario ya rechazó la cotización y TÚ YA le preguntaste si había algo más en lo que pudieras ayudarle. NO se lo vuelvas a preguntar.
                - PROHIBIDO repetir '¿hay algo más en lo que te pueda ayudar?' o cualquier variante.
                - Decide SOLO con el ÚLTIMO mensaje del usuario, no con lo que hayas dicho antes: aunque ya te hayas despedido, si ahora te dice que sí necesita algo, la conversación SIGUE.
                - Si el último mensaje es afirmativo ('sí', 'si', 'claro', 'así es') o dice que necesita algo sin decir qué, PROHIBIDO despedirte: pregúntale DIRECTAMENTE qué necesita, con '¿Qué maquinaria necesitas?'.
                - Si el usuario menciona una máquina, un requerimiento o pide una cotización, retoma la conversación normal y pídele el dato que falte para poder ayudarle.
                - Despídete de forma breve y cordial SOLO si el último mensaje dice que ya no necesita nada.
                """

            machine_reference_instruction = self._build_machine_reference_instruction(
                machine_reference, next_question, question_type
            )

            # La identidad de la empresa va SIEMPRE: es barata y corta de raíz
            # que el bot invente ubicaciones, sucursales o países.
            company_instruction = build_company_facts()

            # OJO: aquí NO se marca cobertura_aclarada. Solo se marca si la
            # respuesta generada realmente menciona México (ver más abajo): en el
            # primer turno el LLM está presentándose y suele ignorar esta
            # instrucción, y darla por dicha dejaría al lead sin enterarse nunca.
            coverage_instruction = build_coverage_instruction(coverage) if coverage_pendiente else ""

            brand_instruction = self._build_brand_instruction(brand_evaluations)
            if brand_instruction:
                # Ya se le entregó al LLM la verdad sobre las marcas pedidas: no
                # hay que volver a aclararlas en los turnos siguientes.
                current_state["marcas_aclaradas"] = True

            current_state_str = get_current_state_str(current_state)
            formatedPrompt = prompt.format_prompt(
                user_message=message,
                current_state_str=current_state_str,
                history_messages=history_messages,
                extracted_info_str=extracted_info_str,
                next_question=next_question or "No hay siguiente pregunta",
                inventory_instruction=inventory_instruction,
                presentation_instruction=presentation_instruction,
                extracted_name_instruction=extracted_name_instruction,
                datos_empresa_instruction=datos_empresa_instruction,
                tipo_ayuda_instruction=tipo_ayuda_instruction,
                machine_reference_instruction=machine_reference_instruction,
                brand_instruction=brand_instruction,
                company_instruction=company_instruction,
                coverage_instruction=coverage_instruction,
                tipos_maquinaria_validos=tipos_maquinaria_validos
            )

            debug_print(f"DEBUG: Prompt conversacional: {formatedPrompt}")
            
            response = self.llm.invoke(formatedPrompt)
            
            result = response.content.strip()
            debug_print(f"DEBUG: Respuesta conversacional generada: '{result}'")

            # La aclaración de cobertura se da por hecha solo si el bot la dijo.
            if coverage_instruction and mentions_coverage(result):
                current_state["cobertura_aclarada"] = True
            
            # Ya no agreamos la lista de campos pendientes hardcoded,
            # porque el LLM ya incorpora la pregunta dentro del propio texto.

            # Red de seguridad: si el LLM pidió datos sin decir cuáles, el lead
            # se queda sin saber qué mandar y reenvía lo que ya dio (6.json).
            # Ahí se descarta la redacción del LLM por la petición explícita.
            if question_type == "datos_empresa" and response_omite_campos_pendientes(result, pending_fields):
                logging.warning(
                    "Respuesta de datos_empresa sin nombrar los campos pendientes %s; "
                    "se sustituye por la petición explícita. Descartada: %r",
                    pending_fields, result
                )
                result = build_datos_empresa_question(pending_fields)

            return result
            
        except Exception as e:
            logging.error(f"Error generando respuesta conversacional: {e}")
            # Fallback a la lógica simple si no se puede generar la respuesta
            if next_question:
                return next_question
            else:
                return "En un momento le responderemos."
    
    def _pending_brand_evaluations(self, current_state: ConversationState) -> List[BrandAvailability]:
        """
        Marcas pedidas por el lead que el bot todavía NO le ha aclarado,
        evaluadas contra el inventario y contra el tipo de maquinaria vigente.

        Se re-evalúan cada turno en lugar de guardarse ya resueltas porque el
        tipo de maquinaria suele llegar DESPUÉS de la marca: el mismo "solo
        marca Dewalt" significa una cosa antes de saber que quiere un rompedor y
        otra después.
        """
        if current_state.get("marcas_aclaradas"):
            return []

        marcas = current_state.get("marcas_solicitadas") or []
        if not marcas:
            return []

        return evaluate_brand_names(marcas, current_state.get("tipo_maquinaria"))

    def _build_brand_instruction(self, brand_evaluations: List[BrandAvailability]) -> str:
        """
        Inyecta al LLM lo que el inventario dice de las marcas que pidió el lead.

        Sin esto el LLM inventa: en una misma conversación llegó a decir
        "manejamos rompedores Dewalt y Makita" y luego lo contrario.
        """
        if not brand_evaluations:
            return ""

        facts = build_brand_facts(brand_evaluations)
        if not facts:
            return ""

        hay_no_disponibles = any(
            ev.estatus != DISPONIBLE_EN_TIPO for ev in brand_evaluations
        )
        instruccion_negativa = (
            """
                - Dilo de forma clara y directa, sin rodeos y sin disculpas largas: el lead necesita
                  saber que no la tenemos para no seguir esperándola.
                - Inmediatamente después ofrécele lo que SÍ manejamos en el tipo que busca, usando
                  EXCLUSIVAMENTE las marcas listadas arriba.
                - PROHIBIDO prometer que la conseguiremos, que la podemos pedir o que llegará después."""
            if hay_no_disponibles else
            """
                - Confírmaselo de forma breve, sin exagerar ni prometer disponibilidad inmediata,
                  stock, tiempos de entrega ni precio."""
        )

        return f"""
                DISPONIBILIDAD DE MARCAS (VERDAD ABSOLUTA - PRIORIDAD MÁXIMA):
                El lead preguntó por marcas específicas. Esto es lo que dice nuestro inventario
                REAL y es la ÚNICA fuente válida sobre marcas:
{facts}
                REGLAS OBLIGATORIAS:
                - Responde a la marca ANTES de continuar con la pregunta pendiente. No la ignores.{instruccion_negativa}
                - PROHIBIDO afirmar o insinuar que manejamos una marca que arriba diga "NO la manejamos".
                - PROHIBIDO mencionar cualquier otra marca que no aparezca en la lista de arriba.
                - No inventes modelos, características ni precios de ninguna marca.
                - Después de aclarar la marca, en el MISMO mensaje enlaza con la pregunta pendiente.
                """

    def _build_machine_reference_instruction(
        self,
        machine_reference: Optional[MachineReference],
        next_question: Optional[str],
        question_type: Optional[str]
    ) -> str:
        """
        Construye la instrucción para reconocer la máquina que mencionó el lead
        antes de re-preguntar el dato pendiente.

        Solo aplica cuando el código INTERRUMPE el flujo: hay una pregunta
        pendiente y NO es una pregunta sobre la máquina (en ese caso el código es
        la respuesta, no una digresión).
        """
        if not machine_reference or not next_question:
            return ""

        if machine_reference.en_inventario and question_type in _MACHINERY_QUESTION_TYPES:
            return ""

        if machine_reference.en_inventario and machine_reference.modelo:
            contexto = (
                f"Ese código corresponde a la {machine_reference.modelo} "
                f"({machine_reference.categoria}), que sí manejamos. Puedes referirte a ella "
                "por su nombre, pero NO afirmes disponibilidad, precio ni características técnicas."
            )
        else:
            contexto = (
                f"El modelo exacto '{machine_reference.texto}' NO aparece en el inventario real. "
                "Díselo claramente: no contamos con ese modelo. Después aclara que sí manejamos "
                "otras máquinas y continúa con la pregunta pendiente para ofrecer alternativas."
            )

        return f"""
                RECONOCIMIENTO DE LA MÁQUINA MENCIONADA (PRIORIDAD ALTA):
                El usuario mencionó el código/modelo de una máquina ("{machine_reference.texto}")
                en lugar de responder la pregunta pendiente. {contexto}
                PROHIBIDO repetir la pregunta pendiente en seco: se lee como si no lo hubieras leído.
                PASO 1 (OBLIGATORIO): Reconoce su interés en UNA sola frase breve, con este patrón:
                "Entiendo que te interesa esa máquina, pero primero..."
                PASO 2: En el MISMO mensaje, enlaza de inmediato con la pregunta pendiente.
                - NO uses otra expresión de confirmación ("Perfecto", "Claro") además de este reconocimiento.
                - NO inventes datos técnicos, precios ni tiempos de entrega de ese modelo.
                - Ejemplo de tono: "Entiendo que te interesa esa máquina, pero primero, ¿con quién tengo el gusto?"
                """

    def generate_final_response(self, current_state: ConversationState) -> str:
        """Genera la respuesta final cuando la conversación está completa"""
        
        uso = current_state.get("tipo_cliente")
        constancia = current_state.get("constancia_fiscal_entregada")
        giro = current_state.get("giro_empresa", "")
        
        is_advisor_handoff = False
        if uso == "distribuidor":
            if constancia and constancia != "No tiene":
                is_advisor_handoff = True
            elif (constancia == "No tiene" or constancia is False) and giro and _is_distribuidor(giro):
                is_advisor_handoff = True
                
        if is_advisor_handoff:
            return "En un momento te contactará el asesor de la zona para darle el precio preferencial."
        
        # Compresores estacionarios: siempre handoff a asesor especializado
        if _is_compresor_estacionario(current_state):
            return f"Gracias por tu información, {current_state.get('nombre', 'Usuario')}. Un asesor especializado en compresores estacionarios se comunicará contigo para profundizar al respecto."

        from pricing_service import get_pricing_service
        
        # Fetch price for the selected machine only
        pricing_str = ""
        maquina_seleccionada = current_state.get("maquina_seleccionada")
        
        logging.info(f"[PRICING_DEBUG] generate_final_response: maquina_seleccionada = '{maquina_seleccionada}'")
        
        if maquina_seleccionada:
            try:
                pricing_service = get_pricing_service()
                logging.info(f"[PRICING_DEBUG] generate_final_response: pricing_service.is_available() = {pricing_service.is_available()}")
                price_info = pricing_service.get_price(maquina_seleccionada)
                
                logging.info(f"[PRICING_DEBUG] generate_final_response: price_info = {price_info}")
                
                if price_info:
                    precio = price_info["price"]
                    moneda = price_info.get("currency", "USD")
                    pricing_str = f"\n\nMáquina seleccionada:\n- {maquina_seleccionada}: ${precio:,.0f} {moneda}"
                    logging.info(f"[PRICING_DEBUG] generate_final_response: Price FOUND - ${precio:,.0f} {moneda}")
                else:
                    # Sin precio en la base: NO se cotiza ni se envía PDF; se deriva a un asesor.
                    logging.warning(f"[PRICING_DEBUG] generate_final_response: No price found for '{maquina_seleccionada}'. Deriving to advisor (no quotation/PDF).")
                    return self._no_price_handoff_message(current_state)
            except Exception as e:
                logging.error(f"[PRICING_DEBUG] generate_final_response: EXCEPTION fetching price: {type(e).__name__}: {e}")
                import traceback
                logging.error(f"[PRICING_DEBUG] generate_final_response: Traceback: {traceback.format_exc()}")
                # Ante un error obteniendo el precio tampoco arriesgamos enviar una
                # cotización sin precio: derivamos a un asesor.
                return self._no_price_handoff_message(current_state)
        
        return f"""¡Perfecto, {current_state.get('nombre', 'Usuario')}!{pricing_str}

Procederé a generar su cotización."""

    def _no_price_handoff_message(self, current_state: ConversationState) -> str:
        """
        Mensaje final cuando la máquina seleccionada NO tiene precio en la base.
        En este caso NO se genera cotización ni se envía el PDF: un asesor
        contactará al cliente para darle la cotización.
        """
        nombre = current_state.get("nombre", "Usuario")
        return (
            f"Gracias por tu información, {nombre}. "
            "Un asesor se pondrá en contacto contigo para brindarte la cotización."
        )

# ============================================================================
# RESPONDEDOR DE INVENTARIO
# ============================================================================

class InventoryResponder:
    """Responde preguntas sobre el inventario de maquinaria"""
    
    def __init__(self, azure_config: AzureOpenAIConfig):
        self.llm = azure_config.create_inventory_llm()  # Usar LLM optimizado para inventario
        self.inventory = get_inventory() # TODO: Mover esto también a DB si es necesario, por ahora usa el fake

    def is_inventory_question(self, message: str) -> bool:
        """Determina si el usuario pidió explícitamente el catálogo general."""
        return _is_explicit_catalog_request(message)

# ============================================================================
# CLASE PRINCIPAL DEL CHATBOT CON SLOT-FILLING INTELIGENTE
# ============================================================================

class IntelligentLeadQualificationChatbot:
    """Chatbot con slot-filling inteligente que detecta información ya proporcionada"""
    
    def __init__(self, azure_config: AzureOpenAIConfig, state_store: Optional[ConversationStateStore] = None, send_message_callback=None, send_pdf_callback=None, cosmos_client=None, db_name=None):
        self.azure_config = azure_config
        # Crear instancias con configuraciones específicas para cada propósito
        self.slot_filler = IntelligentSlotFiller(azure_config)
        self.response_generator = IntelligentResponseGenerator(azure_config, cosmos_client, db_name)
        self.inventory_responder = InventoryResponder(azure_config)
        
        # Usar el state_store proporcionado o crear uno en memoria por defecto
        self.state_store = state_store or InMemoryStateStore()
        self.current_user_id = None
        
        # Callback para enviar mensajes por WhatsApp
        self.send_message_callback = send_message_callback
        
        # Callback para enviar PDFs por WhatsApp
        self.send_pdf_callback = send_pdf_callback
        
        # El estado local sigue existiendo para compatibilidad con código existente
        self.state = self._create_empty_state()

        # Referencia a máquina detectada en el mensaje del turno actual (si hubo)
        self._machine_ref: Optional[MachineReference] = None
        self._model_lookup_status: Optional[str] = None
        self._coverage: Optional[CoverageStatus] = None

    def _create_empty_state(self) -> ConversationState:
        """Crea un estado vacío"""
        state = {
            # Campos que no se preguntan al usuario
            "completed": False,
            "cotizacion_enviada": False,  # True cuando ya se envió la respuesta final (evita ciclo)
            "cierre_ofrecido": False,  # True cuando ya se preguntó "¿hay algo más...?" (se pregunta una sola vez)
            "sin_coincidencias_contexto": None,
            "derivacion_asesor_confirmada": False,
            "recordatorios_derivacion_asesor": 0,
            "messages": [],
            "conversation_mode": "bot", # agente o bot
            "asignado_asesor": None,
            "odoo_lead_id": None,
            "quiere_cotizacion": None,
            "maquinas_recomendadas": [],  # Lista de máquinas recomendadas para mapear posición a modelo
            "maquina_mencionada": None,  # Código/modelo que el lead mencionó por su cuenta
            "modelo_verificado_inventario": False,
            "tipos_maquinaria_mencionados": [],  # Todos los tipos que nombró el lead, en orden
            "intentos_tipo_maquinaria": 0,  # Veces que se preguntó el tipo sin lograr resolverlo
            "solicita_refacciones": False,  # El lead pidió una refacción, no una máquina
            "marcas_solicitadas": [],  # Marcas que pidió el lead (ej. ["DeWalt", "Makita"])
            "marcas_aclaradas": False,  # True cuando el bot ya le respondió sobre esas marcas
            "cobertura_aclarada": False  # True cuando ya se le dijo que solo operamos en México
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
            # Reparar conversaciones que quedaron con detalles_maquinaria como
            # string ("No especificado"). Sin esto, un lead cuyo estado ya se
            # corrompió en producción recibe "hubo un error técnico" en CADA
            # mensaje y no hay forma de que salga de ahí.
            if not isinstance(self.state.get("detalles_maquinaria"), dict):
                logging.warning(
                    f"detalles_maquinaria corrupto para {user_id} "
                    f"({self.state.get('detalles_maquinaria')!r}); se reinicia a {{}}."
                )
                self.state["detalles_maquinaria"] = {}
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
    
    def _get_final_response_message(self) -> str:
        """
        Determina el mensaje final basado en el tipo_ayuda del estado actual.
        Retorna un mensaje diferente si el usuario necesita algo diferente a maquinaria.
        """
        tipo_ayuda = self.state.get("tipo_ayuda")
        if self.state.get("solicita_refacciones"):
            # No prometemos "te comparto la información": el bot no consulta el
            # catálogo de refacciones y no puede confirmar disponibilidad.
            return (
                "Sobre la refacción que necesitas, un asesor especializado se pondrá "
                "en contacto contigo para confirmarte disponibilidad y precio."
            )
        if tipo_ayuda == "otro":
            return "Claro, en un momento te comparto la información."
        else:
            return "Gracias por la información. Pronto te contactará nuestro asesor especializado."
    
    def send_message(self, user_message: str, whatsapp_message_id: str = None, odoo_manager: OdooManager = None) -> str:
        """
        Procesa un mensaje del usuario con slot-filling inteligente.
        Si odoo_manager es None, no se actualiza el lead en Odoo (para poder usar
        test_chatbot.py). La actualización en Odoo es best-effort: si falla, se
        loguea y la conversación continúa sin interrupción.
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
            last_bot_question, _ = self._get_last_bot_question()
            extracted_info = self.slot_filler.extract_all_information(user_message, self.state, last_bot_question)
            extracted_info = _sanitize_extracted_info(
                extracted_info,
                user_message,
                last_bot_question,
                self.state,
            )
            debug_print(f"DEBUG: Información extraída: {extracted_info}")

            # Encaminar las peticiones de refacciones ANTES de mirar la máquina:
            # el modelo que menciona el lead identifica la pieza, no una venta.
            self._detect_refacciones_request(user_message, extracted_info)

            # Detectar si el lead mencionó el código/modelo de una máquina. Se hace
            # ANTES de actualizar Odoo y el estado para que el tipo de maquinaria
            # inferido llegue a los dos.
            self._machine_ref = self._detect_and_merge_machine_reference(user_message, extracted_info)

            # Registrar las marcas que pidió el lead para poder confirmarlas o
            # negarlas contra el inventario al generar la respuesta.
            self._detect_and_store_brands(user_message)

            # Actualizar el lead en Odoo. Best-effort: OdooManager ya atrapa sus
            # propios errores, pero se envuelve también aquí para garantizar que
            # nada de esta integración pueda interrumpir la conversación con el lead.
            if odoo_manager:
                try:
                    odoo_manager.update_lead(self.state, extracted_info)
                except Exception as e:
                    debug_print(f"DEBUG: Error actualizando lead en Odoo (no bloqueante): {e}")

            # Actualizar el estado con la información extraída
            self._update_state_with_extracted_info(extracted_info)
            self._apply_model_lookup_to_state()

            # Después de actualizar el estado: el lugar del requerimiento puede
            # acabar de llegar y es parte del veredicto de cobertura.
            self._coverage = self._evaluate_lead_coverage()

            # Verificar modo de conversación antes de generar respuesta
            current_mode = self.state.get("conversation_mode", "bot")
            
            if current_mode == "agente":
                # Modo agente: solo guardar estado, no generar respuesta automática
                debug_print(f"DEBUG: Modo agente activo, no generando respuesta automática")
                self.save_conversation()
                return None  # No response en modo agente
            
            return self._process_and_respond(user_message, extracted_info)
        
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
            return "Disculpe, hubo un error técnico. ¿Podría intentar de nuevo?"

    def _wants_pdf_resend(self, message: str) -> bool:
        """Detecta si el usuario pide explícitamente re-enviar la cotización PDF."""
        if self.state.get("tipo_cliente") == "distribuidor":
            return False  # Distribuidores no reciben PDF, se les asigna asesor
        keywords = ["cotización", "cotizacion", "pdf", "mándame", "mandame",
                    "envíame", "enviame", "reenvía", "reenvia", "otra vez",
                    "de nuevo", "vuelve a enviar", "manda otra"]
        msg_lower = message.lower()
        return any(kw in msg_lower for kw in keywords)

    def _detect_refacciones_request(self, user_message: str, extracted_info: Dict[str, Any]) -> None:
        """
        Encamina a un asesor al lead que pide una refacción, no una máquina.

        El lead que escribe "busco refacción para mi Simpedil C54 EVO" menciona
        el modelo para identificar la PIEZA, no porque quiera comprar la
        cortadora. Antes eso se leía como interés en la máquina: se llenaba
        `tipo_maquinaria`, de ahí se infería `tipo_ayuda="maquinaria"` y arrancaba
        el flujo completo de cotización. El lead terminaba recibiendo la
        recomendación de una máquina que ya tiene y la petición de su Constancia
        de Situación Fiscal, mientras repetía que solo quería una refacción.

        `tipo_ayuda="otro"` es la rama correcta: no se cotiza ni se manda PDF, se
        deriva a un asesor que continúa la conversación.

        Solo aplica si el `tipo_ayuda` todavía no está definido. Un lead que ya
        venía calificando una máquina y de paso menciona refacciones no debe
        perder ese flujo.
        """
        intencion = _clasificar_intencion_refaccion(user_message)

        if intencion == "maquina":
            # El lead descarta la refacción y pide la máquina. Si veníamos
            # encaminados a refacciones hay que devolverlo al flujo de cotización.
            self._reclasificar_a_maquinaria()
            return

        if intencion != "refaccion":
            # "indefinido": evidencia contradictoria o inexistente. Lo resuelve
            # la extracción del LLM, que sí lee el contexto de la frase.
            return

        self.state["solicita_refacciones"] = True

        if self.state.get("tipo_ayuda") not in (None, "", "otro"):
            debug_print(
                "DEBUG: Se mencionaron refacciones pero el flujo ya es "
                f"tipo_ayuda='{self.state.get('tipo_ayuda')}'; no se redirige."
            )
            return

        extracted_info["tipo_ayuda"] = "otro"
        # El modelo mencionado identifica la refacción; no es una máquina a cotizar.
        for campo in ("tipo_maquinaria", "quiere_cotizacion", "maquina_seleccionada"):
            extracted_info.pop(campo, None)
        debug_print("DEBUG: Solicitud de refacciones detectada; tipo_ayuda='otro' (derivación a asesor)")

    def _reclasificar_a_maquinaria(self) -> None:
        """
        Devuelve al flujo de cotización a un lead mal encaminado a refacciones.

        Ningún clasificador acierta siempre, así que lo importante no es no
        equivocarse: es que el error se pueda deshacer. Sin esto, un lead que
        cayera en `tipo_ayuda="otro"` quedaba atrapado — el campo no se puede
        sobrescribir (ver la lista de excepciones en
        `_update_state_with_extracted_info`), `is_conversation_complete` da True
        con solo nombre y apellido, y `cotizacion_enviada` manda el resto de la
        conversación a la rama del LLM libre. Decir "no, quiero comprar la
        máquina" no lo sacaba de ahí.
        """
        if not self.state.get("solicita_refacciones") and self.state.get("tipo_ayuda") != "otro":
            return

        debug_print("DEBUG: El lead descarta la refacción y pide la máquina; se reabre el flujo de cotización.")
        self.state["solicita_refacciones"] = False
        self.state["tipo_ayuda"] = "maquinaria"
        self.state["completed"] = False
        self.state["cotizacion_enviada"] = False
        self.state["cierre_ofrecido"] = False

    def _es_solicitud_de_refacciones(self, extracted_info: Dict[str, Any]) -> bool:
        """True si el turno actual quedó encaminado como petición de refacciones."""
        return (
            extracted_info.get("tipo_ayuda") == "otro"
            and bool(self.state.get("solicita_refacciones"))
        )

    def _registrar_tipos_mencionados(self, tipos: List[str]) -> None:
        """
        Deja constancia de TODOS los tipos que nombró el lead, en orden.

        El estado solo puede sostener un `tipo_maquinaria` a la vez, pero un lead
        que pide "10 generadores y 4 torres de iluminación" mencionó dos. Sin
        esto, la segunda máquina desaparecía del lead y ni el asesor ni el CRM se
        enteraban de que existía.
        """
        if not tipos:
            return
        registrados = self.state.get("tipos_maquinaria_mencionados") or []
        for tipo in tipos:
            if tipo not in registrados:
                registrados.append(tipo)
        self.state["tipos_maquinaria_mencionados"] = registrados

    def _sembrar_tipo_maquinaria(self, extracted_info: Dict[str, Any], categoria: str) -> None:
        """
        Inyecta `tipo_maquinaria` en la extracción cuando el LLM no lo trajo.

        Se escribe en `extracted_info` y no en el estado directo para que pase
        por las validaciones de `_update_state_with_extracted_info` y para que el
        dato llegue también a Odoo.
        """
        if not categoria:
            return
        if extracted_info.get("tipo_maquinaria") or self.state.get("tipo_maquinaria"):
            return
        # Si el lead pide una refacción, el tipo que nombró describe la máquina
        # que YA tiene. Sembrarlo reactivaría el flujo de cotización del que
        # _detect_refacciones_request acaba de sacarlo.
        if extracted_info.get("tipo_ayuda") == "otro" or self.state.get("solicita_refacciones"):
            return
        extracted_info["tipo_maquinaria"] = categoria
        debug_print(f"DEBUG: tipo_maquinaria='{categoria}' resuelto de forma determinista del mensaje del lead")

    def _categoria_del_modelo(self, modelo: Any) -> Optional[str]:
        """Categoría del modelo según el inventario, o None si no se reconoce."""
        if not isinstance(modelo, str) or not modelo.strip():
            return None
        ref = detect_machine_reference(modelo)
        return ref.categoria if ref and ref.categoria else None

    def _categoria_por_familia_del_codigo(self, code_candidate: str) -> str:
        """
        Categoría que revela el prefijo del código, aunque no tengamos ese modelo.

        Se consulta el código AISLADO (no el mensaje completo) para que el
        veredicto venga del código que ya se identificó y no de otro token.
        """
        ref = detect_machine_reference(code_candidate)
        return ref.categoria if ref else ""

    def _detect_and_merge_machine_reference(
        self, user_message: str, extracted_info: Dict[str, Any]
    ) -> Optional[MachineReference]:
        """
        Detecta el código/modelo de máquina que mencionó el lead y aprovecha lo
        que revela: guarda el código para no perderlo y, si aún no sabemos el
        tipo de maquinaria, lo deduce de la categoría del código.

        Devuelve la referencia detectada (o None) para que la generación de
        respuesta pueda reconocerla antes de re-preguntar el dato pendiente.
        """
        self._model_lookup_status = None

        # Tipos que el lead nombró en LENGUAJE NATURAL ("generadores portátiles",
        # "torres de iluminación"). Es una vía determinista, independiente del
        # LLM: cuando el mensaje trae varias máquinas o un código de modelo que
        # distrae, la extracción del LLM se queda sin `tipo_maquinaria` y el bot
        # se atoraba preguntando el tipo una y otra vez.
        tipos_en_texto = machinery_config_service.resolve_type_ids(user_message)
        self._registrar_tipos_mencionados(tipos_en_texto)

        code_candidate = extract_machine_code_candidate(user_message)
        if code_candidate:
            # La marca se busca SOLO en el tramo donde está el código. Buscarla en
            # todo el mensaje mezclaba máquinas distintas: con "-10 Generadores
            # GV-8000S / -4 Torres Trime X-Start" el bot armaba "Trime GV-8000S"
            # y negaba un modelo que el lead nunca pidió.
            segmento_codigo = segment_containing_code(user_message, code_candidate)
            brand_mentions = detect_brand_mentions(segmento_codigo)
            requested_model = (
                f"{brand_mentions[0]} {code_candidate}"
                if brand_mentions else code_candidate
            )
            self.state["maquina_mencionada"] = requested_model
            lookup = self.response_generator.inventory_service.lookup_exact_model(requested_model)
            self._model_lookup_status = lookup.status

            if lookup.status == "found" and lookup.model and lookup.category:
                # Con una solicitud de refacciones el modelo SÍ existe en el
                # inventario, pero es la máquina que el lead ya tiene: no es una
                # venta que cotizar. Se conserva la referencia (sirve para
                # nombrar la máquina de la refacción) sin tocar el flujo.
                if not self._es_solicitud_de_refacciones(extracted_info):
                    extracted_info["tipo_ayuda"] = "maquinaria"
                    extracted_info["tipo_maquinaria"] = lookup.category
                    extracted_info["maquina_seleccionada"] = lookup.model
                    extracted_info["quiere_cotizacion"] = True
                return MachineReference(
                    texto=requested_model,
                    categoria=lookup.category,
                    modelo=lookup.model,
                    en_inventario=True,
                    confianza="cosmos_exacta",
                )

            if lookup.status == "not_found":
                # Que NO tengamos ese modelo exacto no significa que no sepamos
                # de qué tipo de máquina habla el lead. Antes se devolvía
                # categoria="" y el flujo se quedaba sin tipo_maquinaria: el bot
                # decía "no contamos con ese modelo" y volvía a preguntar el tipo
                # que el lead ya había escrito, en bucle.
                categoria = (
                    extracted_info.get("tipo_maquinaria")
                    or self.state.get("tipo_maquinaria")
                    or machinery_config_service.resolve_type_id(segmento_codigo)
                    or self._categoria_por_familia_del_codigo(code_candidate)
                    or (tipos_en_texto[0] if tipos_en_texto else "")
                    or ""
                )
                if categoria:
                    self._sembrar_tipo_maquinaria(extracted_info, categoria)
                return MachineReference(
                    texto=requested_model,
                    categoria=categoria,
                    modelo=None,
                    en_inventario=False,
                    confianza="cosmos_no_encontrada",
                )

            if lookup.status in ("unavailable", "ambiguous"):
                logging.warning(
                    "No se pudo verificar de forma concluyente el modelo '%s' en Cosmos (%s).",
                    requested_model,
                    lookup.status,
                )
                # Cosmos no concluyó, pero el texto del lead sí dice el tipo.
                if tipos_en_texto:
                    self._sembrar_tipo_maquinaria(extracted_info, tipos_en_texto[0])
                return None

        ref = detect_machine_reference(user_message)
        if not ref:
            if tipos_en_texto:
                self._sembrar_tipo_maquinaria(extracted_info, tipos_en_texto[0])
            return None

        debug_print(f"DEBUG: Referencia a máquina detectada: {ref}")

        # Guardar el código tal como lo escribió el lead para que no se pierda.
        # Solo el primero: de ahí en adelante manda el flujo normal de maquinaria.
        if not self.state.get("maquina_mencionada"):
            self.state["maquina_mencionada"] = ref.texto

        # Si no sabemos el tipo de maquinaria, la categoría del código lo resuelve:
        # un lead que manda "PDSG900VR" quiere un compresor, no hace falta
        # preguntárselo. Se inyecta en extracted_info (en lugar de escribir el
        # estado directo) para que apliquen las validaciones de
        # _update_state_with_extracted_info y para que el dato llegue a Odoo.
        if not extracted_info.get("tipo_maquinaria") and not self.state.get("tipo_maquinaria"):
            extracted_info["tipo_maquinaria"] = ref.categoria
            debug_print(
                f"DEBUG: tipo_maquinaria='{ref.categoria}' inferido del código '{ref.texto}'"
            )

        return ref

    def _apply_model_lookup_to_state(self) -> None:
        """Aplica el veredicto de Cosmos después de la actualización general."""
        if not self._machine_ref:
            return

        if self._model_lookup_status == "found" and self._machine_ref.modelo:
            if self.state.get("solicita_refacciones"):
                return
            self.state["tipo_ayuda"] = "maquinaria"
            self.state["tipo_maquinaria"] = self._machine_ref.categoria
            self.state["maquina_seleccionada"] = self._machine_ref.modelo
            self.state["maquinas_recomendadas"] = [self._machine_ref.modelo]
            self.state["quiere_cotizacion"] = True
            self.state["modelo_verificado_inventario"] = True
            return

        if self._model_lookup_status == "not_found":
            self.state["maquina_seleccionada"] = None
            self.state["maquinas_recomendadas"] = []
            self.state["quiere_cotizacion"] = None
            self.state["modelo_verificado_inventario"] = False
            self.state["completed"] = False
            self.state["cotizacion_enviada"] = False

    def _evaluate_lead_coverage(self) -> CoverageStatus:
        """
        Ubica al lead respecto a la zona de operación de Alpha C, con la lada de
        su WhatsApp y el lugar donde pide el equipo.

        Se recalcula cada turno porque `lugar_requerimiento` puede llegar tarde
        y cambiar el veredicto: un número de Venezuela que pide la máquina para
        Nuevo León sí está en cobertura.
        """
        coverage = evaluate_coverage(
            self.current_user_id, self.state.get("lugar_requerimiento")
        )
        if coverage.fuera_de_mexico:
            debug_print(
                f"DEBUG: Lead fuera de cobertura ({coverage.pais}, "
                f"detectado por {coverage.motivo})."
            )
        return coverage

    def _detect_and_store_brands(self, user_message: str) -> None:
        """
        Guarda en el estado las marcas que pidió el lead.

        Solo DETECTA y acumula; la evaluación contra el inventario se hace al
        generar la respuesta, cuando el estado ya tiene el tipo de maquinaria
        (el lead suele decir la marca antes de decir qué máquina quiere).

        Mencionar una marca reabre SIEMPRE la aclaración, aunque ya se le haya
        respondido antes: si el lead vuelve a preguntar es porque necesita la
        respuesta otra vez. La bandera `marcas_aclaradas` solo evita repetir la
        aclaración en los turnos donde el lead ya no habla de marcas.
        """
        mentions = detect_brand_mentions(user_message)
        if not mentions:
            return

        marcas = list(self.state.get("marcas_solicitadas") or [])
        for mention in mentions:
            marca = canonical_brand(mention)
            if marca.lower() not in [m.lower() for m in marcas]:
                marcas.append(marca)

        self.state["marcas_solicitadas"] = marcas
        self.state["marcas_aclaradas"] = False
        debug_print(f"DEBUG: Marcas solicitadas por el lead: {marcas}")

    def _process_and_respond(self, user_message: str, extracted_info: Dict[str, Any]) -> str:
        """
        Lógica común para procesar un mensaje y generar una respuesta.
        Detecta preguntas de inventario, verifica si la conversación está completa,
        obtiene la siguiente pregunta y genera la respuesta con LLM.
        """

        # ── Manejo de mensajes de seguimiento en conversaciones ya completadas ──
        # Si la respuesta final (cotización/asesor) ya fue enviada, no repetirla.
        # En su lugar, responder de forma natural con el LLM.
        if self.state.get("cotizacion_enviada"):
            debug_print("DEBUG: Conversación ya completada y cotización ya enviada. Respondiendo naturalmente.")
            history_messages = [{"role": msg["role"], "content": msg["content"]} for msg in self.state["messages"]]

            # Permitir re-envío de PDF si el cliente_final lo pide explícitamente
            if self._wants_pdf_resend(user_message):
                debug_print("DEBUG: El usuario pidió re-enviar la cotización PDF.")
                pdf_sent = self._try_send_pdf_quotation()
                if pdf_sent:
                    response = "¡Claro! Te reenvío la cotización."
                else:
                    # No hay PDF que reenviar (p. ej. la máquina no tiene precio):
                    # no prometemos una cotización; reiteramos la derivación a asesor.
                    response = "Tu cotización la está gestionando un asesor, quien se pondrá en contacto contigo para brindártela."
                return self._add_message_and_return_response(response, "")

            generated_response = self.response_generator.generate_response(
                user_message,
                history_messages,
                extracted_info,
                self.state,
                next_question=None,
                is_inventory_question=False,
                question_type="conversation_complete",
                coverage=self._coverage
            )
            return self._add_message_and_return_response(generated_response, "")

        unit_clarification = _build_unit_clarification_response(user_message, self.state)
        if unit_clarification:
            return self._add_message_and_return_response(
                unit_clarification,
                "detalles_maquinaria",
            )

        # ── Flujo normal ──
        is_inventory_question = False

        # Verificar si es una pregunta sobre inventario
        if self.inventory_responder.is_inventory_question(user_message):
            debug_print(f"DEBUG: Solicitud explícita de catálogo detectada")
            is_inventory_question = True
        
        # Si no es pregunta de inventario ni de requerimientos, continuar con el flujo normal
        debug_print(f"DEBUG: Flujo normal de calificación de leads...")

        # Preparar historial de mensajes para el LLM
        history_messages = [{
            "role": msg["role"],
            "content": msg["content"]
        } for msg in self.state["messages"]]

        if self.state.get("quiere_cotizacion") is False:
            self.state["completed"] = True

            if not self.state.get("cierre_ofrecido"):
                self.state["cierre_ofrecido"] = True
                return self._add_message_and_return_response(
                    "De acuerdo, ¿hay algo más en lo que te pueda ayudar?",
                    "",
                )

            generated_response = self.response_generator.generate_response(
                user_message,
                history_messages,
                extracted_info,
                self.state,
                next_question=None,
                is_inventory_question=is_inventory_question,
                question_type="post_cierre",
                machine_reference=self._machine_ref,
                coverage=self._coverage,
            )
            return self._add_message_and_return_response(generated_response, "")

        next_question_str = None
        next_question_type = "conversation_complete"
        # Por defecto, si la conversación está completa, guardamos tipo vacío o un marcador
        storage_question_type = "" 

        # 1. Verificar si la conversación YA estaba marcada como completa o cumple condiciones
        if self.slot_filler.is_conversation_complete(self.state):
            debug_print(f"DEBUG: Conversación completa!")
            self.state["completed"] = True
            # Se usan los valores por defecto (None, conversation_complete)
        
        else:
            # 2. Si no está completa, buscar siguiente pregunta
            next_question_data = self.slot_filler.get_next_question(self.state)

            if next_question_data is None:
                debug_print(f"DEBUG: Estado completo (sin siguiente pregunta): {self.state}")

                self.state["completed"] = True
                # Se usan los valores por defecto
            else:
                # 3. Hay una siguiente pregunta
                next_question_str = next_question_data["question"]
                next_question_type = next_question_data['question_type']
                storage_question_type = next_question_type

                debug_print(f"DEBUG: Siguiente pregunta: {next_question_str}")
                debug_print(f"DEBUG: Tipo de siguiente pregunta: {next_question_type}")

        # If conversation is complete, use the final response with prices
        if self.state.get("completed") and next_question_str is None:
            # Caso "otro" (refacciones, créditos, consultas): NO se cotiza ni se envía PDF.
            # Según el flujo, se confirma y se deriva a un asesor que continúa la conversación.
            if self.state.get("tipo_ayuda") == "otro":
                final_response = self._get_final_response_message()
                self.state["cotizacion_enviada"] = True  # Marcar que la respuesta final ya fue enviada
                return self._add_message_and_return_response(final_response, storage_question_type)

            final_response = self.response_generator.generate_final_response(self.state)
            self.state["cotizacion_enviada"] = True  # Marcar que la respuesta final ya fue enviada
            result = self._add_message_and_return_response(final_response, storage_question_type)

            # Generate and send PDF quotation if applicable
            self._try_send_pdf_quotation()

            # Send ficha técnica (technical datasheet) if available
            self._try_send_ficha_tecnica()

            return result

        # Guardia anti-bucle: si ya preguntamos el tipo de maquinaria varias veces
        # y seguimos sin poder resolverlo, dejar de repetir la pregunta genérica y
        # enseñarle al lead la lista literal de tipos que manejamos.
        if next_question_type == "tipo_maquinaria" and next_question_str:
            intentos = self.state.get("intentos_tipo_maquinaria", 0)
            if intentos >= _MAX_PREGUNTAS_TIPO_MAQUINARIA:
                debug_print(
                    f"DEBUG: {intentos} intentos sin resolver tipo_maquinaria; "
                    "se responde con el catálogo de tipos en vez de repetir la pregunta."
                )
                prefijo = ""
                if self._machine_ref and self._model_lookup_status == "not_found":
                    prefijo = (
                        f"No contamos con el modelo {self._machine_ref.texto} "
                        "en nuestro inventario. "
                    )
                return self._add_message_and_return_response(
                    prefijo + _build_catalog_response(next_question_str),
                    storage_question_type,
                )

        # La disponibilidad de un modelo exacto viene de Cosmos y se comunica de
        # forma determinista para que el LLM no la omita ni la contradiga.
        if self._machine_ref and next_question_str:
            if self._model_lookup_status == "found" and self._machine_ref.modelo:
                response = (
                    f"Sí contamos con el modelo {self._machine_ref.modelo} en nuestro inventario. "
                    f"{next_question_str}"
                )
                return self._add_message_and_return_response(response, storage_question_type)

            if self._model_lookup_status == "not_found":
                response = (
                    f"No contamos con el modelo {self._machine_ref.texto} en nuestro inventario. "
                    "Sí manejamos otras máquinas que podrían ajustarse a lo que necesitas. "
                    f"{next_question_str}"
                )
                return self._add_message_and_return_response(response, storage_question_type)

        if is_inventory_question:
            return self._add_message_and_return_response(
                _build_catalog_response(next_question_str),
                storage_question_type,
            )

        # Generar respuesta con LLM (Llamada unificada)
        generated_response = self.response_generator.generate_response(
            user_message, 
            history_messages,
            extracted_info, 
            self.state, 
            next_question=next_question_str,
            is_inventory_question=is_inventory_question,
            question_type=next_question_type,
            machine_reference=self._machine_ref,
            coverage=self._coverage
        )

        return self._add_message_and_return_response(generated_response, storage_question_type)
        
    def _add_message_and_return_response(self, response: str, question_type: str) -> str:
        """
        Añade un mensaje al estado y devuelve la respuesta final
        Si es un mensaje del bot y hay callback disponible, envía por WhatsApp primero
        """
        has_previous_bot_message = any(
            message.get("role") == "assistant" or message.get("sender") == "bot"
            for message in self.state.get("messages", [])
        )
        if not has_previous_bot_message and "alphi" not in response.lower():
            response = f"Hola, soy Alphi, asesor comercial de Alpha C. {response}"

        whatsapp_message_id = ""
        
        # Enviar mensaje por WhatsApp primero
        try:
            whatsapp_message_id = self.send_message_callback(self.current_user_id, response)
            debug_print(f"DEBUG: Mensaje enviado por WhatsApp con ID: {whatsapp_message_id}")
        except Exception as e:
            debug_print(f"DEBUG: Error enviando mensaje por WhatsApp: {e}")
            # Continuar sin el ID si hay error
        
        # Contador de reintentos de la pregunta de tipo de maquinaria (guardia
        # anti-bucle; ver _MAX_PREGUNTAS_TIPO_MAQUINARIA).
        if question_type == "tipo_maquinaria":
            self.state["intentos_tipo_maquinaria"] = self.state.get("intentos_tipo_maquinaria", 0) + 1

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

    def _try_send_pdf_quotation(self) -> bool:
        """
        Attempts to generate and send a PDF quotation via WhatsApp.
        Only triggers when:
        - conversation is completed
        - quiere_cotizacion is True
        - maquina_seleccionada is set (and has a price)
        - send_pdf_callback is available (running in WhatsApp context)

        Returns True only if the PDF was actually sent; False otherwise
        (skipped conditions, no price for the machine, or send failure).
        """
        try:
            # Check conditions
            # Guardia de negocio: el precio (que va dentro del PDF) NUNCA debe salir
            # antes de que el lead haya proporcionado todos sus datos. Defensa en
            # profundidad por si este método se invoca fuera del bloque `completed`.
            if not self.state.get("completed"):
                logging.info("[PDF] Skipping PDF: conversation not completed (price must not leak before all data is collected)")
                return False

            if not self.state.get("quiere_cotizacion"):
                logging.info("[PDF] Skipping PDF: quiere_cotizacion is not True")
                return False

            if self.state.get("tipo_cliente") == "distribuidor":
                logging.info("[PDF] Skipping PDF: uso is 'distribuidor', handoff triggered instead.")
                return False
            
            maquina = self.state.get("maquina_seleccionada")
            if not maquina:
                logging.info("[PDF] Skipping PDF: no maquina_seleccionada in state")
                return False
            
            if not self.send_pdf_callback:
                logging.info("[PDF] Skipping PDF: no send_pdf_callback (test mode)")
                return False
            
            if not self.current_user_id:
                logging.warning("[PDF] Skipping PDF: no current_user_id set")
                return False
            
            logging.info(f"[PDF] Starting PDF generation for machine: {maquina}, user: {self.current_user_id}")
            
            # Get price info
            from pricing_service import get_pricing_service
            price_info = None
            try:
                pricing_service = get_pricing_service()
                price_info = pricing_service.get_price(maquina)
                logging.info(f"[PDF] Price info retrieved: {price_info}")
            except Exception as e:
                logging.warning(f"[PDF] Could not fetch price for PDF: {e}")

            # Sin precio: NO se envía la cotización en PDF; un asesor dará la cotización.
            if not price_info:
                logging.info(f"[PDF] Skipping PDF quotation: no price for '{maquina}' (advisor will provide the quote)")
                return False

            # Generate PDF
            from pdf_service import get_pdf_generator
            generator = get_pdf_generator()
            pdf_bytes = generator.generate(self.state, price_info)
            logging.info(f"[PDF] PDF generated successfully, size: {len(pdf_bytes)} bytes")
            
            # Build filename
            safe_machine_name = maquina.replace(" ", "_").replace("/", "-")
            filename = f"Cotizacion_{safe_machine_name}.pdf"
            
            # Send via WhatsApp
            logging.info(f"[PDF] Sending PDF '{filename}' to {self.current_user_id}")
            result = self.send_pdf_callback(self.current_user_id, pdf_bytes, filename)
            
            if result:
                logging.info(f"[PDF] PDF quotation sent successfully. WhatsApp message_id: {result}")
                return True
            else:
                logging.error(f"[PDF] send_pdf_callback returned None/empty for {self.current_user_id}")
                return False

        except Exception as e:
            logging.error(f"[PDF] Error generating/sending PDF quotation: {e}")
            import traceback
            logging.error(f"[PDF] Traceback: {traceback.format_exc()}")
            return False

    def _try_send_ficha_tecnica(self):
        """
        Attempts to download and send the ficha técnica (technical datasheet)
        PDF for the selected machine via WhatsApp.
        Triggers for both regular customers AND distributors when:
        - maquina_seleccionada is set
        - A ficha técnica exists for that model in Blob Storage
        - send_pdf_callback is available (running in WhatsApp context)
        """
        try:
            maquina = self.state.get("maquina_seleccionada")
            if not maquina:
                logging.info("[FICHA] Skipping ficha técnica: no maquina_seleccionada")
                return

            if not self.send_pdf_callback:
                logging.info("[FICHA] Skipping ficha técnica: no send_pdf_callback (test mode)")
                return

            if not self.current_user_id:
                logging.warning("[FICHA] Skipping ficha técnica: no current_user_id")
                return

            from blob_storage_service import get_blob_storage_service
            blob_service = get_blob_storage_service()

            if not blob_service.has_ficha_tecnica(maquina):
                logging.info(f"[FICHA] No ficha técnica available for {maquina}")
                return

            result = blob_service.get_ficha_tecnica(maquina)
            if not result:
                logging.error(f"[FICHA] Failed to download ficha técnica for {maquina}")
                return

            pdf_bytes, blob_filename = result

            # Send introductory message
            intro_message = "Te comparto la ficha técnica de la máquina que te interesó."
            try:
                wa_msg_id = self.send_message_callback(self.current_user_id, intro_message)
                self.state["messages"].append({
                    "role": "assistant",
                    "whatsapp_message_id": wa_msg_id or "",
                    "question_type": "",
                    "content": intro_message,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sender": "bot"
                })
                # Persistir el intro ANTES de mandar el PDF: el envío del documento
                # registra su propio mensaje en Cosmos, y save_conversation solo
                # persiste el último mensaje nuevo.
                self.save_conversation()
            except Exception as e:
                logging.error(f"[FICHA] Error sending intro message: {e}")

            # Send the PDF
            send_result = self.send_pdf_callback(self.current_user_id, pdf_bytes, blob_filename)

            if send_result:
                logging.info(f"[FICHA] Ficha técnica sent for {maquina}. WhatsApp message_id: {send_result}")
            else:
                logging.error(f"[FICHA] Failed to send ficha técnica for {maquina}")

            # Save conversation with new messages
            self.save_conversation()

        except Exception as e:
            logging.error(f"[FICHA] Error sending ficha técnica: {e}")
            import traceback
            logging.error(f"[FICHA] Traceback: {traceback.format_exc()}")
    
    # Alias de llaves de detalle que a veces produce la extracción -> campo canónico.
    _DETALLE_ALIASES = {
        "plataforma": {"altura_plataforma_m": "altura_trabajo_m"},
    }

    def _normalize_detalles_maquinaria(self, detalles: Dict[str, Any], tipo: Optional[str]) -> Dict[str, Any]:
        """
        Normaliza un dict de detalles de maquinaria al esquema canónico del tipo:
        1) Remapea alias conocidos (ej. altura_plataforma_m -> altura_trabajo_m).
        2) Descarta llaves que no existan en la config del tipo.
        Si no hay tipo o no hay config, devuelve los detalles sin filtrar (evita
        perder datos cuando aún no se conoce el tipo).
        """
        if not isinstance(detalles, dict) or not detalles or not tipo:
            return detalles if isinstance(detalles, dict) else {}

        result = dict(detalles)

        # 1) Remapear alias conocidos sin pisar un valor canónico ya presente.
        for alias, canonical in self._DETALLE_ALIASES.get(tipo, {}).items():
            if alias in result:
                if canonical not in result:
                    result[canonical] = result[alias]
                del result[alias]

        # 2) Descartar llaves no canónicas (solo si conocemos la config del tipo).
        config = machinery_config_service.get_config(tipo)
        if config:
            valid_fields = {f.name for f in config.fields}
            dropped = [k for k in list(result.keys()) if k not in valid_fields]
            for k in dropped:
                del result[k]
            if dropped:
                debug_print(f"DEBUG: Detalles descartados por no ser canónicos de '{tipo}': {dropped}")

        return result

    def _update_state_with_extracted_info(self, extracted_info: Dict[str, Any]):
        """
        Actualiza el estado con la información extraída, confiando en el
        pre-procesamiento y formato realizado por el LLM.
        """
        extracted_info = _sanitize_extracted_info(extracted_info)
        debug_print(f"DEBUG: Actualizando estado con información: {extracted_info}")

        # Pre-check: si el flujo ya estaba cerrado y llega nueva info de maquinaria,
        # reiniciar el flujo de cotización (mantiene datos de empresa).
        # Esto permite: "también cotízame un generador de 35 kw" tras completar otra
        # cotización, y también retomar después de que el lead rechazó la anterior
        # ("no, gracias" → "¿hay algo más?" → "sí, cotízame un generador").
        flujo_cerrado = self.state.get("completed") or self.state.get("quiere_cotizacion") is False
        if flujo_cerrado:
            has_new_machinery_request = (
                "tipo_maquinaria" in extracted_info
                or ("detalles_maquinaria" in extracted_info and isinstance(extracted_info["detalles_maquinaria"], dict) and len(extracted_info["detalles_maquinaria"]) > 0)
                # Volver a pedir cotización tras haberla rechazado también reabre.
                or extracted_info.get("quiere_cotizacion") is True
            )
            if has_new_machinery_request:
                debug_print("DEBUG: Flujo cerrado recibe nueva solicitud de maquinaria. Reiniciando flujo de cotización.")
                new_tipo = extracted_info.get("tipo_maquinaria")
                old_tipo = self.state.get("tipo_maquinaria")
                # Si es el mismo tipo, limpiar detalles para que se llenen con los nuevos
                # Si es diferente tipo, la lógica de tipo_maquinaria más abajo también limpia
                if not new_tipo or new_tipo == old_tipo:
                    self.state["detalles_maquinaria"] = {}
                self.state["maquinas_recomendadas"] = []
                self.state["maquina_seleccionada"] = None
                self.state["quiere_cotizacion"] = None
                self.state["modelo_verificado_inventario"] = False
                self.state["completed"] = False
                self.state["cotizacion_enviada"] = False
                # La conversación se reabre: el cierre vuelve a estar disponible
                # para cuando este nuevo requerimiento termine.
                self.state["cierre_ofrecido"] = False

                # Un lead cerrado como refacciones que ahora pide una máquina
                # concreta vuelve al flujo de cotización. Sin esto, reabrir no
                # servía de nada: `tipo_ayuda` seguía en "otro" y
                # `is_conversation_complete` volvía a cerrar la conversación en
                # el mismo turno con solo nombre y apellido. Se exige un
                # `tipo_maquinaria` real y no un simple `quiere_cotizacion=True`,
                # que puede venir de un "sí" a cualquier otra pregunta.
                if new_tipo and self.state.get("tipo_ayuda") == "otro":
                    self._reclasificar_a_maquinaria()

        # Si el usuario CAMBIA un detalle de la maquinaria DESPUÉS de que ya se
        # recomendaron opciones (ej: pasa de 300A a 185A), los requerimientos
        # cambiaron y la recomendación previa quedó obsoleta. Invalida la
        # recomendación/selección y vuelve a pedir cotización para recalcular las
        # NUEVAS opciones. Sin esto, el flujo podría completarse con una selección
        # obsoleta/nula y nunca re-presentar la opción correcta.
        if not self.state.get("completed") and self.state.get("maquinas_recomendadas"):
            # Normalizar a los campos canónicos del tipo ANTES de comparar. Sin esto,
            # una llave no-canónica (ej. altura o "interior/exterior" en montacargas)
            # se veía como "detalle nuevo" y reseteaba recomendación/selección en cada
            # turno, dejando el flujo inestable. Solo un cambio REAL en un campo canónico
            # (capacidad, combustible, etc.) debe invalidar la recomendación previa.
            tipo_actual = extracted_info.get("tipo_maquinaria") or self.state.get("tipo_maquinaria")
            new_detalles = self._normalize_detalles_maquinaria(extracted_info.get("detalles_maquinaria"), tipo_actual)
            if isinstance(new_detalles, dict) and new_detalles:
                current_detalles = self.state.get("detalles_maquinaria", {}) or {}
                # Detectar si CAMBIA un detalle existente (ej. 300A -> 185A) o si se
                # AGREGA uno nuevo que afina la búsqueda (ej. el usuario por fin
                # especifica el tipo de plataforma). En ambos casos los requerimientos
                # cambiaron y la recomendación previa quedó obsoleta.
                details_changed = any(
                    current_detalles.get(k) != v
                    for k, v in new_detalles.items()
                )
                if details_changed:
                    debug_print("DEBUG: Detalles de maquinaria cambiaron tras recomendar. Recalculando recomendación y re-pidiendo cotización.")
                    self.state["maquinas_recomendadas"] = []
                    self.state["maquina_seleccionada"] = None
                    self.state["quiere_cotizacion"] = None
                    self.state["modelo_verificado_inventario"] = False
                    self.state["completed"] = False
                    # Evitar que un quiere_cotizacion mal interpretado en ESTE mismo
                    # mensaje (ej: "sabes qué, mejor de 185A" leído como "no") termine
                    # el flujo antes de re-presentar las nuevas opciones.
                    extracted_info.pop("quiere_cotizacion", None)

        # Guardia determinista: si el LLM extrajo tipo_cliente="distribuidor" y giro_empresa
        # en la misma extracción, eliminar giro_empresa. Según el flujo de conversación,
        # el giro solo se pregunta DESPUÉS de que el distribuidor dice que no tiene la constancia.
        # Cuando el usuario dice "nos dedicamos a la renta de maquinaria" para responder si se
        # dedica a la venta/renta, eso solo debe setear tipo_cliente, no giro_empresa.
        if (extracted_info.get("tipo_cliente") == "distribuidor" 
            and "giro_empresa" in extracted_info
            and not self.state.get("tipo_cliente")):
            giro_value = extracted_info["giro_empresa"]
            if _is_distribuidor(giro_value):
                del extracted_info["giro_empresa"]
                debug_print(f"DEBUG: Eliminado giro_empresa='{giro_value}' de extracción simultánea con tipo_cliente='distribuidor'. El giro se preguntará por separado.")

        for key, value in sorted(extracted_info.items(), key=_orden_de_aplicacion):
            # 1. Ignorar valores nulos o vacíos para no insertar datos inútiles.
            if value is None or value == "":
                continue

            # 1.5 Guardia: el código de una máquina NUNCA es el nombre, apellido o
            # giro del lead. Cuando el lead responde con un código en lugar de
            # contestar (ej. "PDSG900VR" a "¿Con quién tengo el gusto?"), el LLM a
            # veces lo clasifica como nombre. Guardarlo contaminaría el estado y el
            # lead en Odoo con un dato falso imposible de corregir después.
            if key in ("nombre", "apellido", "giro_empresa") and looks_like_machine_code(value):
                logging.warning(
                    f"Descartado '{key}'='{value}': parece el código de una máquina, no un dato del lead."
                )
                continue

            # 2. No sobrescribir campos que ya tienen un valor válido a excepción de:
            # - detalles_maquinaria: se actualiza múltiples veces porque tiene varios subcampos.
            # - quiere_cotizacion: puede cambiar si el usuario corrige su respuesta.
            # - maquina_seleccionada: puede cambiar si el usuario elige otra máquina.
            # - tipo_maquinaria: puede cambiar si el usuario cambia de opinión.
            # - giro_empresa: el usuario puede corregirlo (ej: "en realidad nos dedicamos a la construcción").
            # - tipo_cliente: puede cambiar por reclasificación (distribuidor → cliente_final).
            # Esto es clave para evitar que una respuesta ambigua posterior
            # borre un dato que ya se había confirmado.
            current_value = self.state.get(key)
            if key not in ["detalles_maquinaria", "quiere_cotizacion", "maquina_seleccionada", "tipo_maquinaria", "tipo_ayuda", "giro_empresa", "tipo_cliente"] and current_value:
                debug_print(f"DEBUG: Campo '{key}' ya tiene valor válido '{current_value}', no se sobrescribe.")
                continue

            # 3. Manejo de casos especiales
            if key == "detalles_maquinaria" and not isinstance(value, dict):
                # detalles_maquinaria SIEMPRE es un dict. Cuando el lead dice "no
                # tengo esa información" sobre un detalle de la máquina,
                # detect_negative_response devuelve field="detalles_maquinaria" y
                # value="No especificado" (un string). Escribirlo dejaba el estado
                # corrupto y TODAS las llamadas posteriores a detalles.get()
                # reventaban: la conversación quedaba muerta con "hubo un error
                # técnico" en cada mensaje (visto en 5.json al replicarla).
                debug_print(
                    f"DEBUG: Ignorando detalles_maquinaria no-dict ({value!r}); "
                    "el estado conserva los detalles ya extraídos."
                )
                continue

            if key == "detalles_maquinaria" and isinstance(value, dict):
                # Normalizar a los campos canónicos del tipo actual: remapear alias
                # conocidos (ej. altura_plataforma_m -> altura_trabajo_m) y descartar
                # llaves que no estén en la config del tipo. Evita que una llave mal
                # extraída bloquee el flujo (el campo requerido quedaría vacío y el
                # bot re-preguntaría indefinidamente).
                tipo_actual = extracted_info.get("tipo_maquinaria") or self.state.get("tipo_maquinaria")
                value = self._normalize_detalles_maquinaria(value, tipo_actual)
                current_detalles = self.state.get("detalles_maquinaria", {})
                current_detalles.update(value)
                self.state["detalles_maquinaria"] = current_detalles
                debug_print(f"DEBUG: Detalles de maquinaria actualizados: {self.state['detalles_maquinaria']}")
            
            elif key == "tipo_ayuda":
                # `tipo_ayuda` se dejó de bloquear para permitir UNA sola
                # transición: "otro" → "maquinaria". Es la red de seguridad para
                # cuando el clasificador determinista no se pronunció
                # ("indefinido") y fue el LLM quien detectó que el lead sí quiere
                # comprar la máquina. Cualquier otra reescritura sigue prohibida:
                # un lead ya calificado como "maquinaria" no debe caer a "otro"
                # por una frase suelta.
                tipo_ayuda_actual = self.state.get("tipo_ayuda")
                if not tipo_ayuda_actual:
                    self.state[key] = value
                    debug_print(f"DEBUG: Campo '{key}' actualizado con valor: '{value}'")
                elif tipo_ayuda_actual == "otro" and value == "maquinaria":
                    debug_print("DEBUG: El LLM reclasificó tipo_ayuda 'otro' → 'maquinaria'.")
                    self._reclasificar_a_maquinaria()
                else:
                    debug_print(
                        f"DEBUG: Transición de tipo_ayuda '{tipo_ayuda_actual}' → '{value}' "
                        "no permitida; se conserva el valor actual."
                    )

            elif key == "tipo_maquinaria":
                # Validar dinámicamente si el tipo existe en la configuración
                config = machinery_config_service.get_config(value)
                if not config:
                    # El LLM suele devolver el tipo como lo dijo el lead
                    # ("generadores", "torre de iluminación", "planta de luz") en
                    # vez del type_id. Antes eso se descartaba en silencio:
                    # tipo_maquinaria se quedaba en None y el bot repetía la
                    # misma pregunta en cada turno. Se intenta normalizar contra
                    # el vocabulario del catálogo ANTES de tirar el valor.
                    normalizado = machinery_config_service.resolve_type_id(value)
                    if normalizado:
                        debug_print(f"DEBUG: tipo_maquinaria '{value}' normalizado a '{normalizado}'")
                        value = normalizado
                        config = machinery_config_service.get_config(value)
                if config:
                    old_tipo = self.state.get("tipo_maquinaria")
                    self.state[key] = value
                    self.state["intentos_tipo_maquinaria"] = 0
                    debug_print(f"DEBUG: Campo '{key}' actualizado a: {value}")
                    
                    # Si el tipo de maquinaria CAMBIÓ, limpiar campos relacionados
                    if old_tipo and old_tipo != value:
                        debug_print(f"DEBUG: Tipo de maquinaria cambió de '{old_tipo}' a '{value}'. Limpiando detalles, recomendaciones y selección.")
                        self.state["detalles_maquinaria"] = {}
                        self.state["maquinas_recomendadas"] = []
                        self.state["maquina_seleccionada"] = None
                        self.state["quiere_cotizacion"] = None
                        self.state["modelo_verificado_inventario"] = False
                        self.state["completed"] = False
                else:
                    logging.error(
                        f"ADVERTENCIA: Tipo de maquinaria inválido '{value}' extraído por el LLM "
                        "y no reconocible en el catálogo; se descarta."
                    )
            
            elif key == "maquina_seleccionada":
                # La máquina elegida tiene que ser del tipo que se está
                # cotizando. Cuando el lead cambia de máquina a media
                # conversación ("y también una plataforma de 10 metros"), el
                # extractor suele leer ese mensaje como que además ACEPTA la
                # recomendación anterior y devuelve el modelo viejo; como
                # `tipo_maquinaria` se aplica primero, su limpieza ya corrió y
                # el modelo viejo se colaba, dejando seleccionado un generador
                # en una cotización de plataforma.
                #
                # Solo se descarta ante una discrepancia POSITIVA: un valor
                # parcial que aún no resuelve a ningún modelo ("la segunda")
                # pasa y lo resuelve el bloque de nombres parciales de más abajo.
                tipo_actual = self.state.get("tipo_maquinaria")
                categoria = self._categoria_del_modelo(value)
                if tipo_actual and categoria and categoria != tipo_actual:
                    logging.warning(
                        "Descartada maquina_seleccionada '%s': es de '%s' y la cotización "
                        "actual es de '%s'.", value, categoria, tipo_actual
                    )
                    continue
                self.state[key] = value
                debug_print(f"DEBUG: Campo '{key}' actualizado con valor: '{value}'")

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
        
        # Lógica de inferencia post-extracción
        # Si tenemos tipo_maquinaria pero no tipo_ayuda, inferimos que es "maquinaria"
        if self.state.get("tipo_maquinaria") and not self.state.get("tipo_ayuda"):
            self.state["tipo_ayuda"] = "maquinaria"
            debug_print("DEBUG: Inferido tipo_ayuda='maquinaria' basado en presencia de tipo_maquinaria")
        
        # Si el LLM extrajo maquina_seleccionada, inferir quiere_cotizacion=True
        # (seleccionar una máquina implica querer cotización)
        if self.state.get("maquina_seleccionada") and not self.state.get("quiere_cotizacion"):
            self.state["quiere_cotizacion"] = True
            debug_print("DEBUG: Inferido quiere_cotizacion=True por selección de máquina")
        
        # Inferencia determinista de tipo_cliente basada en palabras clave.
        # El LLM a veces no extrae tipo_cliente de frases claras como "es para uso propio".
        # Este fallback garantiza que frases inequívocas se clasifiquen correctamente.
        if not self.state.get("tipo_cliente") and not extracted_info.get("tipo_cliente"):
            # Obtener el último mensaje del usuario para analizar
            user_messages = [m for m in self.state.get("messages", []) if m.get("role") == "user"]
            if user_messages:
                last_user_msg = user_messages[-1].get("content", "").lower().strip()
                
                # Palabras clave para cliente_final
                cliente_final_keywords = [
                    "uso propio", "uso de la empresa", "uso interno", "para mi empresa",
                    "para nuestra empresa", "para la empresa", "no me dedico",
                    "no nos dedicamos", "no, es para", "cliente final", "cliente_final"
                ]
                # Palabras clave para distribuidor
                distribuidor_keywords = [
                    "sí me dedico", "si me dedico", "me dedico a la venta",
                    "me dedico a la renta", "para venta", "para reventa",
                    "para distribución", "para distribucion", "soy distribuidor"
                ]
                
                for kw in cliente_final_keywords:
                    if kw in last_user_msg:
                        self.state["tipo_cliente"] = "cliente_final"
                        debug_print(f"DEBUG: Inferido tipo_cliente='cliente_final' por palabra clave '{kw}' en mensaje: '{last_user_msg}'")
                        break
                
                if not self.state.get("tipo_cliente"):
                    for kw in distribuidor_keywords:
                        if kw in last_user_msg:
                            self.state["tipo_cliente"] = "distribuidor"
                            debug_print(f"DEBUG: Inferido tipo_cliente='distribuidor' por palabra clave '{kw}' en mensaje: '{last_user_msg}'")
                            break
        
        # Inferencia determinista de giro_empresa basada en contexto de la pregunta.
        # Cuando el bot preguntó "¿cuál es el giro de tu empresa?" y el usuario respondió,
        # pero el LLM no extrajo giro_empresa (a veces lo confunde con tipo_cliente),
        # seteamos giro_empresa directamente del mensaje del usuario.
        if not self.state.get("giro_empresa") and not extracted_info.get("giro_empresa"):
            last_bot_question, last_question_type = self._get_last_bot_question()
            if last_bot_question and "giro" in last_bot_question.lower():
                user_messages = [m for m in self.state.get("messages", []) if m.get("role") == "user"]
                if user_messages:
                    last_user_msg = user_messages[-1].get("content", "").strip()
                    # Si el lead contestó con el código de una máquina en vez del giro,
                    # NO tomarlo como giro: se re-preguntará y el bot reconocerá la máquina.
                    giro_inferido = (
                        _normalizar_giro_empresa(last_user_msg)
                        if last_user_msg
                        and len(last_user_msg) < 100  # Respuesta razonable, no un párrafo largo
                        and not looks_like_machine_code(last_user_msg)
                        else None
                    )
                    if giro_inferido:
                        self.state["giro_empresa"] = giro_inferido
                        debug_print(f"DEBUG: Inferido giro_empresa='{giro_inferido}' por contexto de pregunta sobre giro.")

        # Reclasificar distribuidor → cliente_final cuando:
        # - El usuario dijo que se dedica a la venta/renta (tipo_cliente="distribuidor")
        # - Pero NO tiene la Constancia de Situación Fiscal
        # - Y su giro de empresa NO es de distribución/venta/renta de maquinaria
        # En este caso, el usuario realmente es un cliente final que usa la maquinaria
        # para su propio negocio (ej: construcción), así que se le cotiza directamente.
        # Se exige un giro INFORMATIVO: el centinela "No especificado" significa
        # que el lead no quiso decirlo, y tomarlo como "su giro no es de
        # distribución" reclasificaba a cliente_final a alguien que nunca
        # contestó — cambiándole el precio al que tiene derecho.
        if (self.state.get("tipo_cliente") == "distribuidor"
            and (self.state.get("constancia_fiscal_entregada") == "No tiene" or self.state.get("constancia_fiscal_entregada") is False)
            and _giro_es_informativo(self.state.get("giro_empresa"))
            and not _is_distribuidor(self.state.get("giro_empresa"))):
            self.state["tipo_cliente"] = "cliente_final"
            debug_print(f"DEBUG: Reclasificado de distribuidor a cliente_final. Giro '{self.state.get('giro_empresa')}' no es de distribución y no tiene constancia fiscal.")
        
        # Resolve partial model names against recommended machines
        # e.g. "X-START" → "Trime X-START", "DGM250MK-D" → "Shindaiwa DGM250MK-D"
        maquina_sel = self.state.get("maquina_seleccionada")
        maquinas_recomendadas = self.state.get("maquinas_recomendadas", [])
        if maquina_sel:
            partial_lower = maquina_sel.lower().strip()
            resolved = False
            
            # 1. Intentar resolver contra las máquinas recomendadas
            if maquinas_recomendadas:
                for full_model in maquinas_recomendadas:
                    full_model_lower = full_model.lower().strip()
                    if (partial_lower in full_model_lower or full_model_lower in partial_lower) and partial_lower != full_model_lower:
                        debug_print(f"DEBUG: maquina_seleccionada resolved (recomendadas): '{maquina_sel}' → '{full_model}'")
                        self.state["maquina_seleccionada"] = full_model
                        resolved = True
                        break
            
            # 2. Si no se encontró en recomendadas, buscar en todo el inventario local
            #    para el tipo de maquinaria actual (ej: "340" → "Shindaiwa DGW340DM")
            if not resolved:
                from update_invertory_db.inventory_data import inventario
                tipo = self.state.get("tipo_maquinaria")
                for machine in inventario:
                    if machine.get("categoria") == tipo:
                        full_model = machine.get("modelo", "")
                        if partial_lower in full_model.lower() and partial_lower != full_model.lower():
                            debug_print(f"DEBUG: maquina_seleccionada resolved (inventario): '{maquina_sel}' → '{full_model}'")
                            self.state["maquina_seleccionada"] = full_model
                            break
        
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
    
    def get_status_message(self) -> str:
        """
        Construye un resumen LEGIBLE y agrupado del estado de la conversación
        (comando 'status'). Usado tanto en WhatsApp como en las pruebas manuales.
        """
        s = self.state

        def val(key, default="—"):
            v = s.get(key)
            if v is None or v == "" or v == []:
                return default
            return v

        def yesno(key):
            return "Sí" if s.get(key) else "No"

        # quiere_cotizacion es tri-estado: None (sin preguntar) / True / False
        qc = s.get("quiere_cotizacion")
        qc_str = "—" if qc is None else ("Sí" if qc else "No")

        detalles = s.get("detalles_maquinaria") or {}
        detalles_str = ", ".join(f"{k}={v}" for k, v in detalles.items()) if detalles else "—"

        recomendadas = s.get("maquinas_recomendadas") or []
        recomendadas_str = ", ".join(recomendadas) if recomendadas else "—"

        return f"""📊 ESTADO DE LA CONVERSACIÓN
━━━━━━━━━━━━━━━━━━━━━━━━━
👤 Usuario: {self.current_user_id or '—'}
🔄 Modo: {val('conversation_mode')}  |  ✅ Completada: {yesno('completed')}  |  📨 Cotización enviada: {yesno('cotizacion_enviada')}

👤 LEAD
   • Nombre: {val('nombre')}
   • Apellido: {val('apellido')}
   • Correo: {val('correo')}
   • Teléfono: {val('telefono')}
   • Estado (ubicación): {val('lugar_requerimiento')}

🏢 EMPRESA
   • Tipo de cliente: {val('tipo_cliente')}
   • Nombre empresa: {val('nombre_empresa')}
   • Giro: {val('giro_empresa')}
   • Constancia fiscal: {val('constancia_fiscal_entregada')}

🔧 MAQUINARIA / COTIZACIÓN
   • Tipo de ayuda: {val('tipo_ayuda')}
   • Tipo de maquinaria: {val('tipo_maquinaria')}
   • Detalles: {detalles_str}
   • Quiere cotización: {qc_str}
   • Recomendadas: {recomendadas_str}
   • Seleccionada: {val('maquina_seleccionada')}

💬 Mensajes: {len(s.get('messages', []))}"""

    def get_lead_data_json(self) -> str:
        """Obtiene los datos del lead en formato JSON"""
        return json.dumps(get_current_state_str(self.state), indent=2, ensure_ascii=False)
    
    def process_last_lead_message(self, wa_id: str) -> Optional[str]:
        """
        Procesa el último mensaje del lead y genera una respuesta contextual.
        Esta función es específica para el endpoint /start-bot-mode.
        """
        try:
            debug_print(f"DEBUG: Procesando último mensaje del lead para {wa_id}")

            self.load_conversation(wa_id)
                        
            # Verificar que hay mensajes en la conversación
            messages = self.state.get("messages", [])
            if not messages:
                debug_print(f"DEBUG: No hay mensajes en la conversación para {wa_id}")
                return None
            
            # Obtener el último mensaje
            last_message = messages[-1]
            
            # Verificar que el último mensaje sea del lead
            if last_message.get("sender") != "lead" and last_message.get("role") != "user":
                debug_print(f"DEBUG: El último mensaje no es del lead para {wa_id}")
                return None
            
            # Obtener el contenido del mensaje
            message_content = last_message.get("content", "")
            if not message_content or not message_content.strip():
                debug_print(f"DEBUG: El último mensaje del lead está vacío para {wa_id}")
                return None
            
            debug_print(f"DEBUG: Procesando mensaje del lead: '{message_content}'")

            # Detectar la referencia a máquina también en este camino. Es
            # OBLIGATORIO fijar self._machine_ref en cada turno: la instancia del
            # chatbot se reutiliza entre requests y un valor viejo haría que el
            # bot reconociera una máquina que el lead nunca mencionó.
            extracted_info: Dict[str, Any] = {}
            self._machine_ref = self._detect_and_merge_machine_reference(message_content, extracted_info)
            self._detect_and_store_brands(message_content)
            if extracted_info:
                self._update_state_with_extracted_info(extracted_info)
            self._coverage = self._evaluate_lead_coverage()

            return self._process_and_respond(message_content, extracted_info)
            
        except Exception as e:
            logging.error(f"Error procesando último mensaje del lead: {e}")
            return "Disculpe, hubo un error técnico. ¿Podría intentar de nuevo?"
"""
Datos duros de Alpha C y detección de leads fuera de cobertura.

Caso real que originó esto (real_conversations_withLeads/5.json): un lead con
número de Venezuela preguntó "¿están ubicados en Venezuela?" y el bot contestó
"estamos ubicados en Venezuela". Es falso: Alpha C solo opera en México.

La causa es la misma que la de las marcas: el prompt de respuesta no tenía UN
SOLO dato sobre la empresa, así que ante una pregunta el LLM le daba la razón a
quien la hacía. `get_inventory()` en ai_langchain.py sí trae "Cualquier ubicación
en México", pero es dato muerto: nunca llega a ningún prompt.

Este módulo da la verdad de terreno, de forma DETERMINISTA (sin LLM):

  1. Qué es Alpha C y hasta dónde llega, para que el bot no lo improvise.
  2. Si el lead está fuera de México, deducido de la lada de su número de
     WhatsApp y del lugar donde pide el equipo.

Regla de negocio: a un lead fuera de México se le SIGUE calificando; solo hay
que aclararle que únicamente proveemos maquinaria dentro del territorio
mexicano.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

# Los 32 estados son la misma lista que ya usa HubSpot: una sola fuente de verdad.
from hubspot_manager import ESTADOS as ESTADOS_MEXICO

# ============================================================================
# IDENTIDAD DE LA EMPRESA
# ============================================================================

COMPANY_NAME = "Alpha C"
COMPANY_COUNTRY = "México"

# Lada de México. WhatsApp entrega los celulares mexicanos como "521..." y el
# resto como "52...".
MEXICO_COUNTRY_CODE = "52"

# Ladas para poder NOMBRAR el país del lead en la aclaración ("veo que escribes
# desde Venezuela"). Una lada que no esté aquí igual se detecta como fuera de
# México; solo se pierde el nombre del país.
COUNTRY_CODES = {
    "1": "Estados Unidos o Canadá",
    "34": "España",
    "51": "Perú",
    "53": "Cuba",
    "54": "Argentina",
    "55": "Brasil",
    "56": "Chile",
    "57": "Colombia",
    "58": "Venezuela",
    "502": "Guatemala",
    "503": "El Salvador",
    "504": "Honduras",
    "505": "Nicaragua",
    "506": "Costa Rica",
    "507": "Panamá",
    "591": "Bolivia",
    "593": "Ecuador",
    "595": "Paraguay",
    "598": "Uruguay",
    "809": "República Dominicana",
    "829": "República Dominicana",
    "849": "República Dominicana",
}

# Países que el lead puede nombrar en texto al decir dónde necesita el equipo.
# Solo sirven para detectar; la lada del teléfono es la señal principal.
PAISES_EXTRANJEROS = {
    "venezuela", "colombia", "peru", "ecuador", "bolivia", "chile",
    "argentina", "uruguay", "paraguay", "brasil", "guatemala", "honduras",
    "el salvador", "nicaragua", "costa rica", "panama", "cuba",
    "republica dominicana", "puerto rico", "espana", "estados unidos",
    "canada", "eu", "usa",
}


@dataclass(frozen=True)
class CoverageStatus:
    """Dónde está el lead respecto a la cobertura de Alpha C."""

    fuera_de_mexico: bool
    pais: Optional[str]      # Nombre del país si se pudo deducir
    motivo: Optional[str]    # "telefono" | "lugar" — de dónde salió la señal


# ============================================================================
# NORMALIZACIÓN
# ============================================================================

def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    cleaned = _strip_accents(str(text or "")).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


# ============================================================================
# DETECCIÓN DE COBERTURA
# ============================================================================

def country_from_wa_id(wa_id: Optional[str]) -> Optional[str]:
    """
    País del lead a partir de la lada de su número de WhatsApp.

    Devuelve "México" para los números mexicanos, el nombre del país para las
    ladas conocidas, y None cuando no se puede deducir (número vacío o lada
    fuera de la tabla).
    """
    digits = re.sub(r"\D", "", str(wa_id or ""))
    if not digits:
        return None

    if digits.startswith(MEXICO_COUNTRY_CODE):
        return COMPANY_COUNTRY

    # Las ladas de 3 dígitos se prueban primero: "507" (Panamá) no debe leerse
    # como "50" + resto.
    for length in (3, 2, 1):
        code = digits[:length]
        if code in COUNTRY_CODES:
            return COUNTRY_CODES[code]

    return None


def is_mexican_state(lugar: Optional[str]) -> bool:
    """True si el texto menciona alguno de los 32 estados de México."""
    if not lugar:
        return False
    texto = _normalize(lugar)
    return any(_normalize(estado) in texto for estado in ESTADOS_MEXICO)


def foreign_country_in_text(lugar: Optional[str]) -> Optional[str]:
    """Nombre del país extranjero que menciona el texto, si menciona alguno."""
    if not lugar:
        return None
    texto = _normalize(lugar)
    for pais in PAISES_EXTRANJEROS:
        if re.search(rf"(?<![\w]){re.escape(pais)}(?![\w])", texto):
            return pais.title()
    return None


def evaluate_coverage(wa_id: Optional[str], lugar_requerimiento: Optional[str] = None) -> CoverageStatus:
    """
    Determina si el lead está fuera de la cobertura de Alpha C.

    Es CONSERVADOR: solo marca fuera de México cuando hay una señal clara (una
    lada extranjera conocida o un país extranjero nombrado en el texto). Un
    lugar que no reconocemos NO basta: hay miles de ciudades mexicanas que no
    están en la lista de estados, y decirle a un lead mexicano que no lo
    atendemos sería mucho peor que no decir nada.
    """
    # Señal 1: el país que nombra el lead gana sobre la lada. Puede escribir
    # desde un número extranjero pidiendo el equipo para México, o al revés.
    pais_en_texto = foreign_country_in_text(lugar_requerimiento)
    if pais_en_texto:
        return CoverageStatus(fuera_de_mexico=True, pais=pais_en_texto, motivo="lugar")

    if is_mexican_state(lugar_requerimiento):
        return CoverageStatus(fuera_de_mexico=False, pais=COMPANY_COUNTRY, motivo="lugar")

    # Señal 2: la lada del teléfono.
    pais_telefono = country_from_wa_id(wa_id)
    if pais_telefono and pais_telefono != COMPANY_COUNTRY:
        return CoverageStatus(fuera_de_mexico=True, pais=pais_telefono, motivo="telefono")

    return CoverageStatus(fuera_de_mexico=False, pais=pais_telefono, motivo=None)


# ============================================================================
# TEXTO PARA EL PROMPT
# ============================================================================

def build_company_facts() -> str:
    """
    Identidad de Alpha C. Se inyecta SIEMPRE en el prompt de respuesta: es
    barato y corta de raíz toda una clase de invenciones (sucursales,
    direcciones, países, horarios).
    """
    return f"""
                QUIÉNES SOMOS (VERDAD ABSOLUTA - PRIORIDAD MÁXIMA):
                - {COMPANY_NAME} es una empresa MEXICANA. Operamos ÚNICAMENTE en {COMPANY_COUNTRY} y
                  solo proveemos y entregamos maquinaria dentro del territorio mexicano.
                - NO tenemos sucursales, oficinas, inventario, entregas ni operaciones en ningún
                  otro país.
                REGLAS OBLIGATORIAS:
                - Si el usuario pregunta o da por hecho que estamos en otro país, NIÉGALO de forma
                  clara y amable, y dile que estamos en {COMPANY_COUNTRY}. NUNCA le des la razón solo
                  porque lo dio por hecho.
                - PROHIBIDO inventar direcciones, sucursales, ciudades, teléfonos, horarios de
                  atención o tiempos de entrega. Si no lo sabes, dile que un asesor se lo confirma.
                - PROHIBIDO decir "estamos ubicados para atenderte" o cualquier frase que suene a
                  ubicación sin serlo: o das el dato real ({COMPANY_COUNTRY}) o no hablas de ubicación.
                - Lo ÚNICO que puedes afirmar que ofrecemos son los TIPOS DE MAQUINARIA VÁLIDOS
                  listados abajo. Sobre refacciones, accesorios y consumibles (cinceles, puntas,
                  brocas, mangueras, repuestos), renta, servicio o mantenimiento NO afirmes que
                  los damos ni que no: dile que un asesor se lo confirma. Que el usuario los dé
                  por hecho en su pregunta NO es evidencia de que los manejemos."""


def build_coverage_instruction(coverage: CoverageStatus) -> str:
    """
    Aclaración de cobertura para un lead que escribe desde fuera de México.

    NO se corta la conversación: se le sigue calificando normalmente y solo se
    le aclara el alcance para que no invierta tiempo esperando algo que no
    podemos darle.
    """
    if not coverage.fuera_de_mexico:
        return ""

    origen = (
        f"El lead escribe desde {coverage.pais}"
        if coverage.motivo == "telefono"
        else f"El lead necesita el equipo en {coverage.pais}"
    )

    return f"""
                COBERTURA FUERA DE MÉXICO (PRIORIDAD ALTA):
                {origen}, fuera de nuestra zona de operación.
                - Acláraselo UNA sola vez, en UNA frase breve y amable dentro de este mensaje:
                  que {COMPANY_NAME} solo provee maquinaria dentro de {COMPANY_COUNTRY}.
                - NO cortes la conversación ni te despidas: sigue con la pregunta pendiente
                  con toda normalidad, para dejarle sus datos a un asesor.
                - PROHIBIDO prometer envíos, exportación, entregas o cotizaciones fuera de
                  {COMPANY_COUNTRY}, y PROHIBIDO decir que "lo consultaremos" si no es cierto.
                - No repitas esta aclaración en mensajes posteriores."""


def mentions_coverage(text: Optional[str]) -> bool:
    """
    True si la respuesta del bot efectivamente nombró a México.

    Sirve para no dar por dicha la aclaración de cobertura solo porque se le
    mandó la instrucción al LLM: en el primer turno el modelo está ocupado
    presentándose y la deja pasar. Si se marca como aclarada ahí, el lead nunca
    se entera de que no lo cubrimos.
    """
    return "mexico" in _normalize(text)


def build_coverage_disclaimer(coverage: CoverageStatus) -> str:
    """
    Versión ya redactada, para los caminos que arman el texto sin pasar por el
    LLM (la recomendación de máquinas).
    """
    if not coverage.fuera_de_mexico:
        return ""
    return (
        f"Antes de continuar, te comento que {COMPANY_NAME} solo provee maquinaria "
        f"dentro de {COMPANY_COUNTRY}."
    )

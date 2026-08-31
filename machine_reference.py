"""
Detección de referencias a modelos/códigos de maquinaria en el mensaje del lead.

Es MUY común que un lead abra la conversación (o interrumpa el flujo de
calificación) mandando directamente el código de una máquina: "PDSG900VR",
"quiero la S4046E II", "me interesa el AIRMAN PDS185S-6C2".

Antes, el bot ignoraba por completo ese mensaje: la extracción del LLM no llenaba
ningún campo y el bot repetía la pregunta pendiente en seco ("¿Con quién tengo el
gusto?"), lo que se lee como si no hubiera leído al lead. Este módulo detecta la
referencia de forma DETERMINISTA (sin llamada al LLM) para que el bot pueda:

  1. Reconocer el interés antes de re-preguntar el dato que falta.
  2. No perder el código: se guarda en el estado (`maquina_mencionada`).
  3. Resolver el tipo de maquinaria cuando el código pertenece a una familia de
     nuestro catálogo (PDSG900VR → compresor), aunque ese modelo exacto no esté
     en inventario.

El índice se construye a partir del inventario local (misma fuente de verdad que
usa InventoryService como respaldo), así que se mantiene en sync solo.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

try:
    from update_invertory_db.inventory_data import inventario as _local_inventory
except ImportError:  # pragma: no cover - solo en entornos sin el paquete
    _local_inventory = []
    logging.warning("machine_reference: no se pudo importar el inventario local.")


# ============================================================================
# PATRONES
# ============================================================================

# Un código de máquina empieza con letras y contiene al menos DOS dígitos
# consecutivos: PDSG900VR, S4046E, CPD30, GV5500S, TPB90, H1840, FE4P25Q.
# Exigir 2+ dígitos consecutivos y que arranque con letra descarta las
# respuestas numéricas del flujo (300A de amperaje, 15M de altura, 3TON de
# capacidad, códigos postales, teléfonos).
_CODE_TOKEN_RE = re.compile(r"^[A-Z]{1,6}[A-Z0-9]*\d{2,}[A-Z0-9]*$")

# RFC mexicano (persona moral 3 letras, física 4) + 6 dígitos de fecha +
# homoclave. Aparece en el flujo de Constancia de Situación Fiscal y encaja con
# el patrón de código, así que se excluye explícitamente.
_RFC_RE = re.compile(r"^[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{2,3}$")

# Correos y URLs se eliminan antes de tokenizar para no leer un código dentro de
# una dirección (ej. "compras2024@empresa.com").
_EMAIL_RE = re.compile(r"\S+@\S+")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

# Longitud mínima del prefijo de letras para inferir la familia. Con una sola
# letra (S4046E, C54, H1840, A45JE) el prefijo no identifica nada: esos modelos
# solo se resuelven por coincidencia directa contra el inventario.
_MIN_FAMILY_PREFIX = 2

# Longitud mínima para aceptar una coincidencia parcial por prefijo
# (ej. "PDS185" → "AIRMAN PDS185S-6C2").
_MIN_PARTIAL_LEN = 5


# ============================================================================
# RESULTADO
# ============================================================================

@dataclass(frozen=True)
class MachineReference:
    """Referencia a una máquina detectada en el mensaje del lead."""

    texto: str                  # Lo que escribió el lead, tal cual ("PDSG900VR")
    categoria: str              # type_id de maquinaria ("compresor")
    modelo: Optional[str]       # Modelo canónico de nuestro inventario, si se resolvió
    en_inventario: bool         # True si ese modelo exacto sí lo manejamos
    confianza: str              # "cosmos_exacta" | "cosmos_no_encontrada" | "exacta" | "parcial" | "familia"


# ============================================================================
# NORMALIZACIÓN
# ============================================================================

def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Deja solo A-Z0-9 en mayúsculas: 'Koshin GV-5500s' → 'KOSHINGV5500S'."""
    return re.sub(r"[^A-Z0-9]", "", _strip_accents(text).upper())


def _letter_prefix(code: str) -> str:
    match = re.match(r"^[A-Z]+", code)
    return match.group(0) if match else ""


# ============================================================================
# ÍNDICES (se construyen una vez al importar)
# ============================================================================

def _build_indexes() -> Tuple[Dict[str, Tuple[str, str]], Dict[str, str]]:
    """
    Construye los índices de búsqueda a partir del inventario local.

    Returns:
        (models, families) donde
          models[clave_normalizada] = (modelo_canonico, categoria)
          families[prefijo_de_letras] = categoria
    """
    models: Dict[str, Tuple[str, str]] = {}
    # prefijo -> categorías vistas; los prefijos ambiguos se descartan al final
    prefix_categories: Dict[str, Set[str]] = {}

    for item in _local_inventory:
        modelo = (item.get("modelo") or "").strip()
        categoria = (item.get("categoria") or "").strip()
        if not modelo or not categoria:
            continue

        # Clave del modelo completo, con marca: "AIRMAN PDS185S-6C2"
        models.setdefault(_normalize(modelo), (modelo, categoria))

        # Clave de cada token que parezca código, sin la marca: "PDS185S6C2".
        # Así el lead puede escribir solo el código.
        for token in modelo.split():
            code = _normalize(token)
            if not code or not _CODE_TOKEN_RE.match(code):
                continue
            models.setdefault(code, (modelo, categoria))

            prefix = _letter_prefix(code)
            if len(prefix) >= _MIN_FAMILY_PREFIX:
                prefix_categories.setdefault(prefix, set()).add(categoria)

        # Clave del código sin la marca pero con los sufijos separados por
        # espacio: "S4046E II" → "S4046EII", "KTH-100 X" → "KTH100X".
        tokens = modelo.split()
        if len(tokens) > 1:
            sin_marca = _normalize("".join(tokens[1:]))
            if sin_marca:
                models.setdefault(sin_marca, (modelo, categoria))

    # Un prefijo que apunta a más de una categoría no sirve para inferir nada.
    families = {
        prefix: next(iter(cats))
        for prefix, cats in prefix_categories.items()
        if len(cats) == 1
    }

    return models, families


_MODELS, _FAMILIES = _build_indexes()

# Si el inventario local no se pudo importar, los índices quedan vacíos y
# detect_machine_reference() devolvería None SIEMPRE: el bot volvería a repetir la
# pregunta en seco sin fallar en ningún lado. Dejar rastro para que se note en los
# logs de la Function en vez de degradarse en silencio.
if not _MODELS:
    logging.error(
        "machine_reference: índice de modelos VACÍO (¿falta el paquete "
        "update_invertory_db en el deploy?). No se detectarán códigos de máquina."
    )
else:
    logging.info(
        f"machine_reference: {len(_MODELS)} claves de modelo y "
        f"{len(_FAMILIES)} familias indexadas."
    )


# ============================================================================
# API PÚBLICA
# ============================================================================

def looks_like_machine_code(text: Optional[str]) -> bool:
    """
    True si el texto contiene algo que parece el código de una máquina.

    Se usa como guardia para NO guardar un código como si fuera un dato del
    lead: ningún nombre de persona ni giro de empresa lleva un bloque de dos o
    más dígitos pegado a un prefijo de letras.
    """
    if not text:
        return False

    for token in _tokenize(str(text)):
        if _is_code_token(token):
            return True
    return False


def extract_machine_code_candidate(message: Optional[str]) -> Optional[str]:
    """Extrae el primer código de modelo explícito sin consultar inventario."""
    if not message:
        return None

    for token in _raw_tokens(message):
        if _is_code_token(_normalize(token)):
            return token
    return None


def detect_machine_reference(message: Optional[str]) -> Optional[MachineReference]:
    """
    Detecta si el lead mencionó el modelo/código de una máquina.

    Es CONSERVADOR a propósito: solo devuelve una referencia cuando el código se
    puede anclar a nuestro catálogo (modelo exacto, coincidencia parcial o
    familia conocida). Un código de otra marca que no manejamos (ej. "CAT320D")
    devuelve None, para no reconocer un interés que no podemos atender ni
    inferir una categoría equivocada.
    """
    if not message or not message.strip():
        return None

    raw_tokens = _raw_tokens(message)
    if not raw_tokens:
        return None

    # 1. Coincidencia directa contra el inventario. Se prueban ventanas de 3, 2
    #    y 1 token para capturar modelos escritos con espacios ("S4046E II",
    #    "AIRMAN PDS185S-6C2", "Koshin KTH-100 X").
    for window in (3, 2, 1):
        for i in range(len(raw_tokens) - window + 1):
            fragment = " ".join(raw_tokens[i:i + window])
            key = _normalize(fragment)
            if len(key) < 3:
                continue
            hit = _MODELS.get(key)
            if hit:
                modelo, categoria = hit
                return MachineReference(
                    texto=fragment,
                    categoria=categoria,
                    modelo=modelo,
                    en_inventario=True,
                    confianza="exacta",
                )

    # 2. Sin coincidencia directa: buscar tokens con forma de código.
    for token in raw_tokens:
        code = _normalize(token)
        if not _is_code_token(code):
            continue

        # 2a. Coincidencia parcial: el lead escribió el código incompleto.
        partial = _match_partial(code)
        if partial:
            modelo, categoria = partial
            return MachineReference(
                texto=token,
                categoria=categoria,
                modelo=modelo,
                en_inventario=True,
                confianza="parcial",
            )

        # 2b. Familia conocida: no tenemos ESE modelo, pero el prefijo nos dice
        #     de qué tipo de máquina habla (PDSG900VR → familia PDSG → compresor).
        categoria = _FAMILIES.get(_letter_prefix(code))
        if categoria:
            return MachineReference(
                texto=token,
                categoria=categoria,
                modelo=None,
                en_inventario=False,
                confianza="familia",
            )

    return None


# ============================================================================
# HELPERS INTERNOS
# ============================================================================

def _raw_tokens(message: str) -> List[str]:
    """Tokens del mensaje tal como los escribió el lead, sin correos ni URLs."""
    cleaned = _URL_RE.sub(" ", _EMAIL_RE.sub(" ", message))
    return re.findall(r"[^\s,;:()¿?¡!\"']+", cleaned)


def _tokenize(message: str) -> List[str]:
    """Tokens normalizados (A-Z0-9) del mensaje."""
    return [_normalize(t) for t in _raw_tokens(message)]


def _is_code_token(code: str) -> bool:
    """True si el token normalizado tiene forma de código de máquina."""
    if not code or _RFC_RE.match(code):
        return False
    return bool(_CODE_TOKEN_RE.match(code))


def _match_partial(code: str) -> Optional[Tuple[str, str]]:
    """
    Resuelve un código incompleto contra el inventario por prefijo.
    Solo acepta la coincidencia si es ÚNICA, para no adivinar.
    """
    if len(code) < _MIN_PARTIAL_LEN:
        return None

    matches = {
        hit for key, hit in _MODELS.items()
        if key.startswith(code) and key != code
    }
    if len(matches) == 1:
        return next(iter(matches))
    return None

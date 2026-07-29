"""
Detección de MARCAS mencionadas por el lead y verificación contra el inventario.

Es muy común que el lead pida una marca concreta ("solo marca Dewalt y Makita",
"¿no tienen Bosch?"). Hasta ahora nada en el sistema sabía qué marcas manejamos:
`machine_reference.py` solo detecta CÓDIGOS de modelo (exige 2+ dígitos, así que
"dewalt" nunca se detectaba) y el prompt de respuesta no recibía ningún dato de
marcas. El LLM improvisaba, y en la misma conversación llegó a afirmar
"manejamos rompedores Dewalt y Makita" y minutos después "no contamos con
rompedores Dewalt o Makita".

Este módulo da la verdad de terreno, de forma DETERMINISTA (sin llamada al LLM):

  1. Qué marcas maneja Alpha C, derivadas del inventario (fuente de verdad única).
  2. Para qué TIPOS de maquinaria maneja cada marca. Tener la marca no implica
     tenerla en la categoría que el lead busca: manejamos Shindaiwa, pero en
     soldadoras/generadores/torres, NO en rompedores.
  3. Qué marcas sí manejamos del tipo que el lead pidió, para ofrecer la
     alternativa real en lugar de solo negar.

El índice se construye del inventario local (mismo respaldo que usa
InventoryService), así que se mantiene en sync solo.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from maquinaria_config import machinery_config_service

try:
    from update_invertory_db.inventory_data import inventario as _local_inventory
except ImportError:  # pragma: no cover - solo en entornos sin el paquete
    _local_inventory = []
    logging.warning("brand_reference: no se pudo importar el inventario local.")


# ============================================================================
# ESTATUS DE DISPONIBILIDAD
# ============================================================================

# La marca sí se maneja Y hay equipos de esa marca en el tipo que pidió el lead.
DISPONIBLE_EN_TIPO = "disponible_en_tipo"

# La marca sí se maneja, pero NO en el tipo que pidió el lead (ej. Shindaiwa
# existe en soldadoras y generadores, pero no en rompedores).
DISPONIBLE_EN_OTROS_TIPOS = "disponible_en_otros_tipos"

# La marca no está en el inventario en ningún tipo.
NO_DISPONIBLE = "no_disponible"


@dataclass(frozen=True)
class BrandAvailability:
    """Veredicto sobre una marca que mencionó el lead."""

    texto: str                    # Lo que escribió el lead ("dewalt")
    marca: str                    # Nombre canónico para mostrar ("DeWalt", "Toku")
    estatus: str                  # Uno de los tres estatus de arriba
    tipos_de_la_marca: List[str]  # Tipos (nombres amigables) que sí manejamos de esa marca
    tipo_solicitado: Optional[str]        # type_id que pidió el lead, si se conoce
    tipo_solicitado_display: Optional[str]  # Nombre amigable del tipo solicitado
    marcas_del_tipo: List[str]    # Marcas que sí manejamos en el tipo solicitado


# ============================================================================
# MARCAS EXTERNAS CONOCIDAS
# ============================================================================

# Marcas que NO manejamos pero que los leads piden con frecuencia en este
# mercado. Sirven para reconocer la mención y poder negarla con certeza, en
# lugar de dejar que el LLM adivine. La clave es el alias normalizado y el valor
# es el nombre canónico para mostrar.
#
# NOTA: esta lista solo mejora la DETECCIÓN. La disponibilidad siempre se
# resuelve contra el inventario, así que una marca de aquí que algún día
# entrara al catálogo se reportaría correctamente como disponible.
_EXTERNAL_BRANDS: Dict[str, str] = {
    # Herramienta eléctrica / neumática
    "dewalt": "DeWalt",
    "de walt": "DeWalt",
    "makita": "Makita",
    "bosch": "Bosch",
    "milwaukee": "Milwaukee",
    "hilti": "Hilti",
    "stanley": "Stanley",
    "black and decker": "Black & Decker",
    "black & decker": "Black & Decker",
    "ryobi": "Ryobi",
    "metabo": "Metabo",
    "truper": "Truper",
    "urrea": "Urrea",
    "husqvarna": "Husqvarna",
    "stihl": "Stihl",
    "kango": "Kango",
    # Maquinaria pesada
    "caterpillar": "Caterpillar",
    "cat": "Caterpillar",
    "komatsu": "Komatsu",
    "john deere": "John Deere",
    "deere": "John Deere",
    "jcb": "JCB",
    "volvo": "Volvo",
    "hitachi": "Hitachi",
    "doosan": "Doosan",
    "hyundai": "Hyundai",
    "bobcat": "Bobcat",
    "kubota": "Kubota",
    "yanmar": "Yanmar",
    "new holland": "New Holland",
    "liebherr": "Liebherr",
    "sany": "Sany",
    "xcmg": "XCMG",
    "manitou": "Manitou",
    "merlo": "Merlo",
    # Plataformas de elevación
    "genie": "Genie",
    "jlg": "JLG",
    "skyjack": "Skyjack",
    "haulotte": "Haulotte",
    "snorkel": "Snorkel",
    "terex": "Terex",
    # Montacargas
    "toyota": "Toyota",
    "hyster": "Hyster",
    "yale": "Yale",
    "crown": "Crown",
    "clark": "Clark",
    "linde": "Linde",
    "jungheinrich": "Jungheinrich",
    "mitsubishi": "Mitsubishi",
    # Compresores
    "atlas copco": "Atlas Copco",
    "atlascopco": "Atlas Copco",
    "epiroc": "Epiroc",
    "ingersoll rand": "Ingersoll Rand",
    "ingersoll": "Ingersoll Rand",
    "sullair": "Sullair",
    "kaeser": "Kaeser",
    "chicago pneumatic": "Chicago Pneumatic",
    "elgi": "ELGi",
    "schulz": "Schulz",
    "champion": "Champion",
    # Soldadoras
    "lincoln electric": "Lincoln Electric",
    "lincoln": "Lincoln Electric",
    "miller": "Miller",
    "esab": "ESAB",
    "hobart": "Hobart",
    "infra": "Infra",
    # Generadores / motores
    "generac": "Generac",
    "cummins": "Cummins",
    "kohler": "Kohler",
    "honda": "Honda",
    "yamaha": "Yamaha",
    "perkins": "Perkins",
    "denyo": "Denyo",
    "multiquip": "Multiquip",
    "evans": "Evans",
    "wacker neuson": "Wacker Neuson",
    "wacker": "Wacker Neuson",
    "briggs and stratton": "Briggs & Stratton",
    "briggs & stratton": "Briggs & Stratton",
    "briggs": "Briggs & Stratton",
    # Bombas
    "grundfos": "Grundfos",
    "tsurumi": "Tsurumi",
    "barnes": "Barnes",
}


# ============================================================================
# NORMALIZACIÓN
# ============================================================================

def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Minúsculas sin acentos y con espacios colapsados, para comparar marcas."""
    cleaned = _strip_accents(str(text)).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


# ============================================================================
# ÍNDICE DE MARCAS DEL INVENTARIO (se construye una vez al importar)
# ============================================================================

def _build_brand_index() -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """
    Deriva las marcas del inventario. La marca es el primer token del `modelo`
    ("Toku TCB-300" → Toku, "AIRMAN PDS185S-6C2" → AIRMAN).

    Returns:
        (canonical, categories) donde
          canonical[marca_normalizada] = nombre tal como aparece en inventario
          categories[marca_normalizada] = {type_id, ...} donde la manejamos
    """
    canonical: Dict[str, str] = {}
    categories: Dict[str, Set[str]] = {}

    for item in _local_inventory:
        modelo = (item.get("modelo") or "").strip()
        categoria = (item.get("categoria") or "").strip()
        if not modelo or not categoria:
            continue

        marca = modelo.split()[0].strip()
        if not marca:
            continue

        key = _normalize(marca)
        canonical.setdefault(key, marca)
        categories.setdefault(key, set()).add(categoria)

    return canonical, categories


_OWN_BRANDS, _OWN_BRAND_CATEGORIES = _build_brand_index()

if not _OWN_BRANDS:
    logging.error(
        "brand_reference: índice de marcas VACÍO (¿falta el paquete "
        "update_invertory_db en el deploy?). No se validarán marcas."
    )
else:
    logging.info(
        f"brand_reference: {len(_OWN_BRANDS)} marcas propias indexadas: "
        f"{', '.join(sorted(_OWN_BRANDS.values()))}"
    )


def _all_known_brands() -> Dict[str, str]:
    """Alias normalizado → nombre canónico, propias + externas conocidas."""
    known = dict(_EXTERNAL_BRANDS)
    # Las propias mandan: si una marca está en ambas listas, se muestra como
    # aparece en el inventario.
    known.update(_OWN_BRANDS)
    return known


_KNOWN_BRANDS = _all_known_brands()

# Se ordenan de más larga a más corta para que "atlas copco" gane sobre "atlas"
# y "ingersoll rand" sobre "ingersoll".
# Alias que también son apellidos o palabras comunes. El bot pregunta "¿con quién
# tengo el gusto?" y "¿cuál es tu apellido?": un lead que responde "Miller" no
# está pidiendo una marca. Solo se aceptan si el mensaje habla de maquinaria.
_AMBIGUOUS_ALIASES: Set[str] = {
    "cat", "miller", "clark", "crown", "yale", "linde", "champion",
    "lincoln", "deere", "evans", "barnes", "stanley", "hobart", "kango",
    "infra", "honda", "merlo", "briggs",
}

# Señales de que el mensaje habla de equipo, no de personas.
_BRAND_CONTEXT_WORDS: Set[str] = {
    "marca", "marcas", "modelo", "modelos", "equipo", "equipos",
    "maquina", "maquinas", "maquinaria", "tienen", "tiene", "tienes",
    "manejan", "maneja", "manejas", "venden", "vende", "rentan", "renta",
    "hay", "disponible", "disponibles", "cotizar", "cotizacion", "cotiza",
    "inventario", "catalogo", "precio", "precios", "stock",
}

_BRAND_PATTERN = re.compile(
    r"(?<![\w-])(" + "|".join(
        re.escape(alias) for alias in sorted(_KNOWN_BRANDS, key=len, reverse=True)
    ) + r")(?![\w-])",
    re.IGNORECASE,
) if _KNOWN_BRANDS else None


# ============================================================================
# DETECCIÓN POR CONTEXTO ("marca X")
# ============================================================================

# Captura lo que sigue a "marca"/"marcas" para reconocer marcas que no están en
# ninguna lista: "solo marca Dewalt y Makita", "la marca Hyundai".
_MARCA_CONTEXT_RE = re.compile(r"\bmarcas?\b\s*[:\-]?\s*(.{1,60})", re.IGNORECASE)

# Palabras que jamás son una marca. Cortan la captura tras "marca".
_STOPWORDS: Set[str] = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "por", "favor", "porfavor", "que", "cual", "cuales", "cualquiera",
    "es", "son", "esta", "estan", "para", "con", "sin", "en", "al", "lo",
    "tienen", "tiene", "tienes", "tengo", "manejan", "maneja", "manejas",
    "hay", "hayan", "venden", "vende", "rentan", "renta", "cuentan", "cuenta",
    "prefiero", "quiero", "quisiera", "busco", "buscamos", "necesito",
    "solo", "solamente", "tambien", "pero", "mas", "menos", "otra", "otro",
    "misma", "mismo", "propia", "propio", "alguna", "algun", "alguno",
    "si", "no", "ok", "gracias", "buenas", "buenos", "dias", "tardes",
    "maquina", "maquinas", "maquinaria", "equipo", "equipos", "modelo",
    "modelos", "marca", "marcas", "disponible", "disponibles", "nueva",
    "nuevo", "usada", "usado", "y", "o", "u", "e",
}


# Caché de las palabras de maquinaria. Se invalida si cambia el número de tipos:
# function_app re-inicializa machinery_config_service con Cosmos DESPUÉS de
# importar este módulo, así que no se puede calcular una sola vez al importar.
_MACHINERY_WORDS_CACHE: Dict[int, Set[str]] = {}


def _machinery_words() -> Set[str]:
    """
    Palabras de tipos de maquinaria (rompedor, soldadoras, martillos…) que tras
    "marca" no son una marca sino el tipo: "¿qué marca de rompedor manejan?".
    """
    configs = machinery_config_service.get_all_types()
    cached = _MACHINERY_WORDS_CACHE.get(len(configs))
    if cached is not None:
        return cached

    words: Set[str] = set()
    for config in configs:
        for source in (config.type_id, config.name, machinery_config_service.get_type_display_name(config.type_id)):
            for token in re.findall(r"[a-zA-Zñáéíóúü]+", _normalize(source or "")):
                if len(token) > 2:
                    words.add(token)
                    words.add(token + "s")
                    words.add(token + "es")
                    if token.endswith("es"):
                        words.add(token[:-2])
                    if token.endswith("s"):
                        words.add(token[:-1])

    _MACHINERY_WORDS_CACHE[len(configs)] = words
    return words


def _extract_brands_after_marca(message: str) -> List[str]:
    """
    Marcas escritas tras la palabra "marca", aunque no estén en ninguna lista.

    "Solo marca dewalt y makita por favor" → ["dewalt", "makita"]
    "¿qué marca de rompedores manejan?"    → []  (rompedores es el tipo)
    """
    excluded = _STOPWORDS | _machinery_words()
    found: List[str] = []

    for match in _MARCA_CONTEXT_RE.finditer(message):
        tail = match.group(1)
        # Se corta en el primer signo de puntuación fuerte: lo que sigue ya es
        # otra idea ("marca Bosch. Necesito 3 piezas").
        tail = re.split(r"[.;!?¿¡]", tail)[0]
        tokens = re.findall(r"[\w&áéíóúñÁÉÍÓÚÑ-]+", tail)

        for token in tokens:
            norm = _normalize(token)
            if not norm:
                continue
            if norm in ("y", "o", "u", "e"):
                continue  # separador entre dos marcas: seguir leyendo
            if norm in excluded or len(norm) < 3 or norm.isdigit():
                break  # se acabó la enumeración de marcas
            found.append(token)
            if len(found) >= 4:
                break

    return found


# ============================================================================
# API PÚBLICA
# ============================================================================

def _has_brand_context(message: str) -> bool:
    """True si el mensaje habla de maquinaria (y no, por ejemplo, de un apellido)."""
    tokens = set(re.findall(r"[a-z0-9ñ]+", _normalize(message)))
    return bool(tokens & (_BRAND_CONTEXT_WORDS | _machinery_words()))


def get_brands_for_category(machine_type: Optional[str]) -> List[str]:
    """Marcas que Alpha C maneja en un tipo de maquinaria, según el inventario."""
    if not machine_type:
        return []
    target = _normalize(machine_type)
    marcas = [
        canonical
        for key, canonical in _OWN_BRANDS.items()
        if any(_normalize(cat) == target for cat in _OWN_BRAND_CATEGORIES.get(key, set()))
    ]
    return sorted(set(marcas))


def get_all_own_brands() -> List[str]:
    """Todas las marcas que maneja Alpha C."""
    return sorted(set(_OWN_BRANDS.values()))


def detect_brand_mentions(message: Optional[str]) -> List[str]:
    """
    Marcas mencionadas en el mensaje, tal como las escribió el lead.

    Combina dos vías para no depender de una lista cerrada:
      1. Coincidencia contra marcas conocidas (las nuestras + las que más piden).
      2. Lo que sigue a la palabra "marca", que captura cualquier otra.
    """
    if not message or not message.strip():
        return []

    hits: List[str] = []
    if _BRAND_PATTERN:
        con_contexto = _has_brand_context(message)
        for match in _BRAND_PATTERN.finditer(message):
            alias = _normalize(match.group(1))
            if alias in _AMBIGUOUS_ALIASES and not con_contexto:
                continue
            hits.append(match.group(1))
    hits.extend(_extract_brands_after_marca(message))

    # Dedup preservando el orden de aparición.
    seen: Set[str] = set()
    unique: List[str] = []
    for hit in hits:
        canonical = _KNOWN_BRANDS.get(_normalize(hit), hit)
        key = _normalize(canonical)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def canonical_brand(text: str) -> str:
    """Nombre canónico de una marca ('dewalt' → 'DeWalt'). Sin cambios si no se conoce."""
    return _KNOWN_BRANDS.get(_normalize(text), str(text).strip())


def evaluate_brand_names(
    names: List[str], machine_type: Optional[str] = None
) -> List[BrandAvailability]:
    """
    Evalúa contra el inventario una lista de marcas ya detectadas.

    Se separa de la detección a propósito: las marcas que pidió el lead se
    guardan en el estado y se re-evalúan en cada turno contra el
    tipo_maquinaria vigente, que puede llegar DESPUÉS de que las mencionó
    ("solo marca Dewalt" → "…para rompedor").
    """
    mentions = [n for n in (names or []) if n and str(n).strip()]
    if not mentions:
        return []

    tipo_display = (
        machinery_config_service.get_type_display_name(machine_type)
        if machine_type else None
    )
    marcas_del_tipo = get_brands_for_category(machine_type)

    evaluations: List[BrandAvailability] = []
    for texto in mentions:
        key = _normalize(texto)
        canonical = _KNOWN_BRANDS.get(key, texto.strip())

        own_categories = _OWN_BRAND_CATEGORIES.get(key, set())
        tipos_de_la_marca = sorted(
            machinery_config_service.get_type_display_name(cat) for cat in own_categories
        )

        if not own_categories:
            estatus = NO_DISPONIBLE
        elif machine_type and any(_normalize(c) == _normalize(machine_type) for c in own_categories):
            estatus = DISPONIBLE_EN_TIPO
        elif machine_type:
            estatus = DISPONIBLE_EN_OTROS_TIPOS
        else:
            # Sin tipo solicitado no se puede afirmar más que "sí la manejamos".
            estatus = DISPONIBLE_EN_TIPO

        evaluations.append(BrandAvailability(
            texto=texto,
            marca=canonical,
            estatus=estatus,
            tipos_de_la_marca=tipos_de_la_marca,
            tipo_solicitado=machine_type,
            tipo_solicitado_display=tipo_display,
            marcas_del_tipo=marcas_del_tipo,
        ))

    return evaluations


def evaluate_brands(
    message: Optional[str], machine_type: Optional[str] = None
) -> List[BrandAvailability]:
    """Detecta y evalúa en un paso. Atajo para pruebas y usos puntuales."""
    return evaluate_brand_names(detect_brand_mentions(message), machine_type)


def _join_es(items: List[str], conjuncion: str = "y") -> str:
    """['a', 'b', 'c'] → 'a, b y c'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {conjuncion} {items[-1]}"


def build_brand_facts(evaluations: List[BrandAvailability]) -> str:
    """
    Hechos de inventario sobre las marcas mencionadas, en texto plano.

    Es lo que se le pasa al LLM como verdad de terreno y también lo que usa el
    camino determinista de recomendación.
    """
    if not evaluations:
        return ""

    lines: List[str] = []
    for ev in evaluations:
        if ev.estatus == NO_DISPONIBLE:
            lines.append(
                f"- {ev.marca}: NO la manejamos. No tenemos ningún equipo de esa "
                f"marca en el inventario, en ningún tipo de maquinaria."
            )
        elif ev.estatus == DISPONIBLE_EN_OTROS_TIPOS:
            lines.append(
                f"- {ev.marca}: SÍ la manejamos, pero únicamente en "
                f"{_join_es(ev.tipos_de_la_marca)}. NO tenemos "
                f"{ev.tipo_solicitado_display} de esa marca."
            )
        else:
            alcance = (
                f"en {ev.tipo_solicitado_display}"
                if ev.tipo_solicitado_display
                else f"en {_join_es(ev.tipos_de_la_marca)}"
            )
            lines.append(f"- {ev.marca}: SÍ la manejamos {alcance}.")

    ev = evaluations[0]
    if ev.tipo_solicitado_display:
        if ev.marcas_del_tipo:
            lines.append(
                f"- En {ev.tipo_solicitado_display} las únicas marcas que manejamos "
                f"son: {_join_es(ev.marcas_del_tipo)}."
            )
        else:
            lines.append(
                f"- No tenemos ningún equipo de tipo {ev.tipo_solicitado_display} "
                f"en el inventario."
            )

    return "\n".join(lines)


def build_brand_disclaimer(evaluations: List[BrandAvailability]) -> str:
    """
    Aclaración breve, ya redactada, para los caminos que NO pasan por el LLM
    (la recomendación de máquinas se arma con texto fijo).

    Devuelve "" si todas las marcas pedidas sí se manejan en el tipo solicitado:
    en ese caso no hay nada que aclarar.
    """
    if not evaluations:
        return ""

    no_disponibles = [ev.marca for ev in evaluations if ev.estatus == NO_DISPONIBLE]
    otros_tipos = [ev for ev in evaluations if ev.estatus == DISPONIBLE_EN_OTROS_TIPOS]
    if not no_disponibles and not otros_tipos:
        return ""

    ev = evaluations[0]
    partes: List[str] = []

    if no_disponibles:
        marca_str = _join_es(no_disponibles, "ni")
        plural = "esas marcas" if len(no_disponibles) > 1 else "esa marca"
        partes.append(
            f"Actualmente no manejamos {marca_str}; no contamos con equipos de "
            f"{plural} en nuestro inventario."
        )

    for otro in otros_tipos:
        partes.append(
            f"De {otro.marca} sí manejamos equipo, pero solo en "
            f"{_join_es(otro.tipos_de_la_marca)}, no en "
            f"{otro.tipo_solicitado_display}."
        )

    if ev.tipo_solicitado_display and ev.marcas_del_tipo:
        partes.append(
            f"En {ev.tipo_solicitado_display} trabajamos con "
            f"{_join_es(ev.marcas_del_tipo)}."
        )

    return " ".join(partes)

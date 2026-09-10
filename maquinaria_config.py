"""
Configuración centralizada de maquinaria
"""

import re
import unicodedata
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

# ============================================================================
# MODELOS DE DATOS PARA CONFIGURACIÓN (SCHEMA)
# ============================================================================

class MachineryFieldSchema(BaseModel):
    name: str = Field(..., description="Nombre del campo (clave interna)")
    question: str = Field(..., description="Pregunta que hace el bot al usuario")
    reason: str = Field(..., description="Razón por la cual se pide este dato")
    type: str = Field("text", description="Tipo de dato: text, number, boolean, selection")
    required: bool = Field(True, description="Si es obligatorio")
    # Campos para futura lógica de filtrado
    comparison_operator: str = Field("eq", description="Operador de comparación por defecto: eq, gte, lte, contains")
    unit: Optional[str] = Field(None, description="Unidad de medida si aplica (m, kg, cfm, etc)")

class MachineryTypeSchema(BaseModel):
    type_id: str
    name: str
    display_name: Optional[str] = None  # Nombre amigable (plural) para mostrar al usuario
    fields: List[MachineryFieldSchema]

# ============================================================================
# NOMBRES AMIGABLES (PLURAL) PARA MOSTRAR AL USUARIO
# Respaldo en código por si el config (Cosmos) aún no trae 'display_name'.
# Es la fuente de verdad para responder "¿qué máquinas manejan?".
# ============================================================================

TYPE_DISPLAY_NAMES: Dict[str, str] = {
    "soldadora": "soldadoras",
    "compresor": "compresores",
    "rompedor": "rompedores (martillos neumáticos)",
    "motobomba": "motobombas",
    "apisonador": "apisonadores",
    "generador": "generadores",
    "cortadora_varillas": "cortadoras de varilla",
    "dobladora_varillas": "dobladoras de varilla",
    "torre_iluminacion": "torres de iluminación",
    "montacargas": "montacargas",
    "plataforma": "plataformas de elevación",
    "manipulador": "manipuladores telescópicos",
}

# ============================================================================
# VOCABULARIO EN LENGUAJE NATURAL → type_id
#
# El lead casi nunca escribe el type_id ("torre_iluminacion"): escribe
# "Torres de Iluminación", "planta de luz", "martillo neumático". La extracción
# del LLM normalmente lo traduce, pero cuando falla (mensajes con VARIAS
# máquinas, o con un código de modelo que distrae) devolvía un valor inválido o
# ninguno, `get_config()` lo rechazaba en silencio y `tipo_maquinaria` se
# quedaba en None PARA SIEMPRE: el bot volvía a preguntar el tipo en cada turno.
# Este vocabulario da un camino DETERMINISTA (sin LLM) para resolver el tipo.
#
# Solo son sinónimos que NO se deducen del type_id ni del display_name; esos dos
# se agregan automáticamente al índice.
# ============================================================================

_TYPE_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "soldadora": ("planta de soldar", "maquina de soldar", "equipo de soldar", "soldadura"),
    "compresor": ("compresora", "compresor de aire"),
    "rompedor": ("martillo neumatico", "martillo demoledor", "martillo rompedor",
                 "rompedora", "martillo"),
    "motobomba": ("bomba de agua", "bomba autocebante", "bomba para agua"),
    "apisonador": ("apisonadora", "bailarina", "vibrocompactador", "compactadora",
                   "compactador de suelo", "placa vibratoria"),
    "generador": ("planta de luz", "planta electrica", "planta generadora",
                  "planta de energia", "generador portatil", "generador electrico"),
    # Sin "cortadora"/"dobladora" a secas: sin el complemento no identifican el
    # tipo (una "cortadora de concreto" no es una cortadora de varilla).
    "cortadora_varillas": ("cortadora de varilla", "cortadora de varillas",
                           "cortadora de acero"),
    "dobladora_varillas": ("dobladora de varilla", "dobladora de varillas",
                           "dobladora de acero"),
    "torre_iluminacion": ("torre de iluminacion", "torre de luz", "torre de luces",
                          "torre iluminacion", "torre de alumbrado"),
    "montacargas": ("monta cargas",),
    "plataforma": ("plataforma de elevacion", "plataforma elevadora",
                   "plataforma de tijera", "brazo articulado", "manlift",
                   "man lift", "tijera elevadora", "elevador de personal"),
    "manipulador": ("manipulador telescopico", "telehandler", "manipulador"),
}


def _normalize_type_text(text: Optional[str]) -> str:
    """
    Minúsculas, sin acentos, sin puntuación y en singular aproximado.

    La singularización es cruda a propósito (quita 'es'/'s' final) porque se
    aplica IGUAL a los dos lados de la comparación: "Generadores Portátiles" y
    "generador portatil" colapsan al mismo texto sin necesitar un lematizador.
    """
    if not text:
        return ""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)
    )
    limpio = re.sub(r"[^a-z0-9]+", " ", sin_acentos.lower()).strip()
    palabras = []
    for palabra in limpio.split():
        if len(palabra) > 4 and palabra.endswith("es"):
            palabra = palabra[:-2]
        elif len(palabra) > 3 and palabra.endswith("s"):
            palabra = palabra[:-1]
        palabras.append(palabra)
    return " ".join(palabras)


# ============================================================================
# SERVICIO DE CONFIGURACIÓN
# ============================================================================

class MachineryConfigService:
    """
    Servicio para gestionar la configuración de tipos de maquinaria.
    Lee de la base de datos Cosmos DB (contenedor: machinery_configuration).
    """
    
    def __init__(self, cosmos_client=None, database_name=None):
        self._configs: Dict[str, MachineryTypeSchema] = {}
        self._vocab_signature: Optional[Tuple[str, ...]] = None
        self._vocab_cache: List[Tuple[str, str]] = []
        if cosmos_client and database_name:
            self._db = cosmos_client.get_database_client(database_name)
            self._container = self._db.get_container_client("machinery_configuration")
            self._load_configs_from_db()
        else:
             # Fallback logic or empty init for testing/offline support if needed
             # For now we can keep the local load as fallback or strictly require DB
             self._configs = self._load_initial_configs_fallback()

    def _load_configs_from_db(self):
        """Carga configuraciones desde Cosmos DB"""
        try:
            # Query all items
            items = list(self._container.read_all_items())
            for item in items:
                # Clean system properties if necessary, though Pydantic usually ignores extras unless configured otherwise
                # But read_all_items returns dicts.
                try:
                    # Remove Cosmos DB specific fields to avoid Pydantic validation errors if strict
                    clean_item = {k: v for k, v in item.items() if not k.startswith("_")}
                    schema = MachineryTypeSchema(**clean_item)
                    self._configs[schema.type_id] = schema
                except Exception as e:
                    print(f"Error loading config for item {item.get('id')}: {e}")
            print(f"Loaded {len(self._configs)} machinery configurations from Cosmos DB.")
        except Exception as e:
            print(f"Error connecting/reading from Cosmos DB (machinery_configuration): {e}")

        # Fallback CRÍTICO: si Cosmos no aportó ninguna configuración (contenedor
        # 'machinery_configuration' ausente/vacío o error de lectura), usar la config
        # local. Sin esto, en un entorno sin ese contenedor (ej. PROD) get_config()
        # devuelve None para todos los tipos, tipo_maquinaria nunca se persiste y el
        # bot se queda en un loop infinito pidiendo el tipo de maquinaria.
        if not self._configs:
            print("ADVERTENCIA: sin configuraciones desde Cosmos. Usando config local de respaldo (machinery_data).")
            self._configs = self._load_initial_configs_fallback()

    def _load_initial_configs_fallback(self) -> Dict[str, MachineryTypeSchema]:
        """
        Carga la configuración inicial desde machinery_data.py (Fallback).
        """
        try:
            from update_invertory_db.machinery_data import machinery_configurations
            configs = {}
            for config_data in machinery_configurations:
                schema = MachineryTypeSchema(**config_data)
                configs[schema.type_id] = schema
            return configs
        except ImportError:
            return {}

    def get_config(self, type_id: str) -> Optional[MachineryTypeSchema]:
        """Obtiene la configuración para un tipo de maquinaria específico"""
        return self._configs.get(type_id)

    def get_all_types(self) -> List[MachineryTypeSchema]:
        """Obtiene todas las configuraciones de tipos de maquinaria"""
        return list(self._configs.values())

    def get_type_display_name(self, type_id: str) -> str:
        """
        Nombre amigable (plural) de un tipo para mostrar al usuario.
        Prioridad: display_name del config (Cosmos) → mapa de respaldo → name → type_id.
        """
        config = self._configs.get(type_id)
        if config and getattr(config, "display_name", None):
            return config.display_name
        if type_id in TYPE_DISPLAY_NAMES:
            return TYPE_DISPLAY_NAMES[type_id]
        if config and config.name:
            return config.name
        return type_id

    def get_type_display_list(self) -> List[str]:
        """Lista de nombres amigables de TODOS los tipos manejados (en el orden del config)."""
        return [self.get_type_display_name(t.type_id) for t in self.get_all_types()]

    def _get_type_vocabulary(self) -> List[Tuple[str, str]]:
        """
        Índice (frase_normalizada, type_id) ordenado de la frase más larga a la
        más corta, para que "torre de iluminacion" gane sobre "torre" y
        "cortadora de varilla" sobre "cortadora".

        Se reconstruye si cambió el set de tipos cargados (Cosmos puede llegar
        después del arranque con la config local de respaldo).
        """
        firma = tuple(sorted(self._configs.keys()))
        if getattr(self, "_vocab_signature", None) == firma:
            return self._vocab_cache

        frases: Dict[str, str] = {}

        def registrar(texto: Optional[str], type_id: str) -> None:
            clave = _normalize_type_text(texto)
            # La primera frase registrada manda: los type_id y nombres del config
            # tienen prioridad sobre los sinónimos, que se agregan al final.
            if clave and clave not in frases:
                frases[clave] = type_id

        for type_id in self._configs:
            registrar(type_id.replace("_", " "), type_id)
            config = self._configs[type_id]
            registrar(config.name, type_id)
            registrar(getattr(config, "display_name", None), type_id)
            registrar(TYPE_DISPLAY_NAMES.get(type_id), type_id)

        for type_id, sinonimos in _TYPE_SYNONYMS.items():
            # Un sinónimo de un tipo que no está cargado no sirve para nada.
            if type_id not in self._configs:
                continue
            for sinonimo in sinonimos:
                registrar(sinonimo, type_id)

        vocab = sorted(frases.items(), key=lambda par: len(par[0]), reverse=True)
        self._vocab_signature = firma
        self._vocab_cache = vocab
        return vocab

    def resolve_type_ids(self, text: Optional[str]) -> List[str]:
        """
        type_ids mencionados en texto libre, en orden de aparición y sin repetir.

        Determinista, sin LLM. "Requiero 10 Generadores Portátiles y 4 Torres de
        Iluminación" → ["generador", "torre_iluminacion"].
        """
        normalizado = _normalize_type_text(text)
        if not normalizado:
            return []

        # Se marcan los tramos ya consumidos para que una frase larga bloquee a
        # las cortas que contiene ("torre de iluminacion" impide un match suelto
        # de "torre" en la misma posición).
        consumido = [False] * len(normalizado)
        encontrados: List[Tuple[int, str]] = []

        for frase, type_id in self._get_type_vocabulary():
            for match in re.finditer(rf"(?<![a-z0-9]){re.escape(frase)}(?![a-z0-9])", normalizado):
                inicio, fin = match.span()
                if any(consumido[inicio:fin]):
                    continue
                for i in range(inicio, fin):
                    consumido[i] = True
                encontrados.append((inicio, type_id))

        ordenados: List[str] = []
        for _, type_id in sorted(encontrados):
            if type_id not in ordenados:
                ordenados.append(type_id)
        return ordenados

    def resolve_type_id(self, text: Optional[str]) -> Optional[str]:
        """Primer type_id mencionado en el texto, o None si no se reconoce ninguno."""
        type_ids = self.resolve_type_ids(text)
        return type_ids[0] if type_ids else None

    def get_required_fields(self, type_id: str) -> List[str]:
        """Obtiene una lista de los nombres de campos obligatorios para un tipo de maquinaria"""
        config = self.get_config(type_id)
        if not config:
            return []
        
        return [field.name for field in config.fields if field.required]



# Instancia Global (se inicializará en function_app.py o startup)
machinery_config_service = MachineryConfigService()  # Default to blank/fallback until correctly initialized with DB client

def get_required_fields_for_tipo(tipo: str) -> List[str]:
    """Helper function para compatibilidad hacia atrás"""
    return machinery_config_service.get_required_fields(tipo)

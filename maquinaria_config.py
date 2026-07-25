"""
Configuración centralizada de maquinaria
"""

from typing import List, Dict, Any, Optional
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
# SERVICIO DE CONFIGURACIÓN
# ============================================================================

class MachineryConfigService:
    """
    Servicio para gestionar la configuración de tipos de maquinaria.
    Lee de la base de datos Cosmos DB (contenedor: machinery_configuration).
    """
    
    def __init__(self, cosmos_client=None, database_name=None):
        self._configs: Dict[str, MachineryTypeSchema] = {}
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

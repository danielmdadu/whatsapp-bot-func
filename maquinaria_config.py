"""
Configuración centralizada de maquinaria
"""

from typing import List
from pydantic import BaseModel, Field
from state_management import MaquinariaType

# ============================================================================
# MODELOS DE DATOS PARA DETALLES DE MAQUINARIA
# ============================================================================

class DetallesSoldadora(BaseModel):
    amperaje: str = Field(None, description="Amperaje requerido para la soldadora")
    electrodo: str = Field(None, description="Tipo de electrodo que quema")

class DetallesCompresor(BaseModel):
    capacidad_volumen: str = Field(None, description="Capacidad de volumen de aire requerida")
    herramientas_conectar: str = Field(None, description="Herramientas que va a conectar")

class DetallesTorre(BaseModel):
    es_led: bool = Field(None, description="Si requiere LED o no")

class DetallesPlataforma(BaseModel):
    altura_trabajo: str = Field(None, description="Altura de trabajo necesaria")
    actividad: str = Field(None, description="Actividad que va a realizar")
    ubicacion: str = Field(None, description="Si es en interior o exterior")

class DetallesGenerador(BaseModel):
    actividad: str = Field(None, description="Para qué actividad lo requiere")
    capacidad: str = Field(None, description="Capacidad en kvas o kw necesaria")

class DetallesRompedor(BaseModel):
    uso: str = Field(None, description="Para qué lo va a utilizar")
    tipo: str = Field(None, description="Si lo requiere eléctrico o neumático")

class DetallesApisonador(BaseModel):
    uso: str = Field(None, description="Para qué lo va a utilizar")
    motor: str = Field(None, description="Qué tipo de motor trae")
    es_diafragma: bool = Field(None, description="Si es de diafragma o no")

class DetallesMontacargas(BaseModel):
    capacidad: str = Field(None, description="Capacidad requerida")
    tipo_energia: str = Field(None, description="Si lo requiere eléctrico, a combustión a gasolina o gas lp")
    posicion_operador: str = Field(None, description="Si lo requiere para hombre parado o sentado")
    altura: str = Field(None, description="Altura requerida")

class DetallesManipulador(BaseModel):
    capacidad: str = Field(None, description="Capacidad requerida")
    altura: str = Field(None, description="Altura necesaria")
    actividad: str = Field(None, description="Actividad que va a realizar")
    tipo_energia: str = Field(None, description="Si lo requiere eléctrico o a combustión")

# ============================================================================
# CONFIGURACIÓN CENTRALIZADA DE MAQUINARIA
# ============================================================================

MAQUINARIA_CONFIG = {
    MaquinariaType.SOLDADORAS: {
        "model": DetallesSoldadora,
        "fields": [
            {
                "name": "amperaje", 
                "reason": "Para recomendarte el modelo adecuado según tu trabajo",
                "question": "¿cuál es el amperaje que necesitas?",
                "required": True
            },
            {
                "name": "electrodo", 
                "reason": "Para asegurar compatibilidad con tus materiales",
                "question": "¿qué tipo de electrodo quemas?",
                "required": True
            }
        ]
    },
    MaquinariaType.COMPRESOR: {
        "model": DetallesCompresor,
        "fields": [
            {
                "name": "capacidad_volumen", 
                "reason": "Para seleccionar la potencia correcta",
                "question": "¿cuál es la capacidad de volumen de aire necesitas?",
                "required": True
            },
            {
                "name": "herramientas_conectar", 
                "reason": "Para verificar compatibilidad con tus equipos",
                "question": "¿qué herramientas le vas a conectar?",
                "required": True
            }
        ]
    },
    MaquinariaType.TORRE_ILUMINACION: {
        "model": DetallesTorre,
        "fields": [
            {
                "name": "es_led", 
                "reason": "Para determinar el tipo de iluminación necesario",
                "question": "¿prefieres iluminación LED?",
                "required": True
            }
        ]
    },
    MaquinariaType.PLATAFORMA: {
        "model": DetallesPlataforma,
        "fields": [
            {
                "name": "altura_trabajo", 
                "reason": "Para asegurar que la máquina alcance la altura necesaria",
                "question": "¿cuál es la altura de trabajo que necesitas?",
                "required": True
            },
            {
                "name": "actividad", 
                "reason": "Para entender el contexto de uso",
                "question": "¿qué actividad vas a realizar?",
                "required": True
            },
            {
                "name": "ubicacion", 
                "reason": "Para determinar el modelo más conveniente",
                "question": "¿es para interior o exterior?",
                "required": True
            }
        ]
    },
    MaquinariaType.GENERADORES: {
        "model": DetallesGenerador,
        "fields": [
            {
                "name": "actividad", 
                "reason": "Para entender el contexto de uso",
                "question": "¿para qué actividad lo requiere?",
                "required": True
            },
            {
                "name": "capacidad", 
                "reason": "Para determinar la potencia necesaria",
                "question": "¿qué capacidad en kvas o kw necesitas?",
                "required": True
            }
        ]
    },
    MaquinariaType.ROMPEDORES: {
        "model": DetallesRompedor,
        "fields": [
            {
                "name": "uso", 
                "reason": "Para entender el contexto de uso",
                "question": "¿para qué lo va a utilizar?",
                "required": True
            },
            {
                "name": "tipo", 
                "reason": "Para determinar el tipo de energía necesaria",
                "question": "¿lo requiere eléctrico o neumático?",
                "required": True
            }
        ]
    },
    MaquinariaType.APISONADOR: {
        "model": DetallesApisonador,
        "fields": [
            {
                "name": "uso", 
                "reason": "Para entender el contexto de uso",
                "question": "¿para qué lo va a utilizar?",
                "required": True
            },
            {
                "name": "motor", 
                "reason": "Para determinar las características del equipo",
                "question": "¿qué tipo de motor debe tener?",
                "required": True
            },
            {
                "name": "es_diafragma", 
                "reason": "Para determinar si lo requiere",
                "question": "¿el equipo debe ser de diafragma?",
                "required": True
            }
        ]
    },
    MaquinariaType.MONTACARGAS: {
        "model": DetallesMontacargas,
        "fields": [
            {
                "name": "capacidad", 
                "reason": "Para determinar la capacidad necesaria",
                "question": "¿qué peso requiere levantar?",
                "required": True
            },
            {
                "name": "tipo_energia", 
                "reason": "Para determinar el tipo de energía adecuado",
                "question": "¿lo requiere eléctrico, a combustión a gasolina o gas lp?",
                "required": True
            },
            {
                "name": "posicion_operador", 
                "reason": "Para determinar la posición del operador",
                "question": "¿lo requiere para hombre parado o sentado?",
                "required": True
            },
            {
                "name": "altura", 
                "reason": "Para determinar la altura necesaria",
                "question": "¿qué altura requiere?",
                "required": True
            }
        ]
    },
    MaquinariaType.MANIPULADOR: {
        "model": DetallesManipulador,
        "fields": [
            {
                "name": "capacidad", 
                "reason": "Para determinar la capacidad necesaria",
                "question": "¿qué peso requiere mover?",
                "required": True
            },
            {
                "name": "altura", 
                "reason": "Para determinar la altura necesaria",
                "question": "¿qué altura necesita?",
                "required": True
            },
            {
                "name": "actividad", 
                "reason": "Para entender el contexto de uso",
                "question": "¿qué actividad va a realizar?",
                "required": True
            },
            {
                "name": "tipo_energia", 
                "reason": "Para determinar el tipo de energía adecuado",
                "question": "¿lo requiere eléctrico o a combustión?",
                "required": True
            }
        ]
    }
}

def get_required_fields_for_tipo(tipo: MaquinariaType) -> List[str]:
    """Obtiene lista de campos obligatorios para un tipo de maquinaria"""
    if tipo not in MAQUINARIA_CONFIG:
        return []
    
    config = MAQUINARIA_CONFIG[tipo]
    return [field["name"] for field in config["fields"] if field.get("required", True)]

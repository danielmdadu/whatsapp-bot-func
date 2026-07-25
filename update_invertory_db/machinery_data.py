"""
Datos iniciales de configuración de maquinaria
"""

machinery_configurations = [
    {
        "type_id": "soldadora",
        "name": "Soldadora",
        "fields": [
            {
                "name": "amperaje_amps_max", # Check against max capacity - matches inventory field
                "question": "¿cuál es el amperaje que necesitas?",
                "reason": "Para recomendarte el modelo adecuado según tu trabajo",
                "type": "number",
                "unit": "amps",
                "comparison_operator": "gte"
            },
            {
                "name": "tipo_alimentacion",
                "question": "¿qué tipo de combustible necesitas: diésel o gasolina?",
                "reason": "Para recomendarte el modelo adecuado según tu trabajo",
                "type": "selection",
                "comparison_operator": "eq"
            }
        ]
    },
    {
        "type_id": "compresor",
        "name": "Compresor",
        "fields": [
            {
                "name": "tipo_compresor",
                "question": "¿qué tipo de compresor necesitas: portátil o estacionario/eléctrico?",
                "reason": "Para recomendarte el modelo adecuado según tu trabajo",
                "type": "string",
                "comparison_operator": "contains"
            },
            {
                "name": "caudal_cfm_max", # Check against max supplied CFM
                "question": "¿cuánto volumen de aire en CFM necesitas?",
                "reason": "Para seleccionar la capacidad correcta",
                "type": "number", 
                "unit": "CFM",
                "comparison_operator": "gte"
            }
        ]
    },
    {
        "type_id": "rompedor", # AKA Martillo
        "name": "Rompedor",
        "fields": []
    },
    {
        "type_id": "motobomba",
        "name": "Motobomba",
        "fields": []
    },
    {
        "type_id": "apisonador",
        "name": "Apisonador",
        "fields": []
    },
    {
        "type_id": "generador",
        "name": "Generador",
        "fields": [
            {
                "name": "potencia_kw", # Target kW field
                "question": "¿cuál es la potencia del generador que requiere en kW?", 
                "reason": "Para seleccionar el generador correcto",
                "type": "number",
                "unit": "kW",
                "comparison_operator": "gte"
            }
        ]
    },
    {
        "type_id": "cortadora_varillas",
        "name": "Cortadora de Varilla",
        "fields": []
    },
    {
        "type_id": "dobladora_varillas",
        "name": "Dobladora de Varilla",
        "fields": []
    },
    {
        "type_id": "torre_iluminacion",
        "name": "Torre de Iluminación",
        "fields": []
    },
    {
        "type_id": "montacargas",
        "name": "Montacargas",
        "fields": [
            {
                "name": "tipo_combustible",
                "question": "¿el montacargas lo requieres eléctrico, a gasolina o diésel?",
                "reason": "Para seleccionar el montacargas con el tipo de energía correcto",
                "type": "selection",
                "required": True,
                "comparison_operator": "contains"
            },
            {
                "name": "capacidad_toneladas",
                "question": "¿qué peso requiere levantar en toneladas?",
                "reason": "Para determinar la capacidad necesaria",
                "type": "number",
                "unit": "toneladas",
                "comparison_operator": "gte"
            }
        ]
    },
    {
        "type_id": "plataforma",
        "name": "Plataforma",
        "fields": [
            {
                "name": "tipo_plataforma",
                "question": "¿cuál es el tipo de plataforma que necesitas: articulada, de tijera, unipersonal o mástil?",
                "reason": "Para seleccionar la plataforma correcta",
                "type": "selection",
                "comparison_operator": "contains"
            },
            {
                "name": "altura_trabajo_m",
                "question": "¿cuál es la altura de trabajo que necesitas?",
                "reason": "Para asegurar que la máquina alcance la altura necesaria",
                "type": "number",
                "unit": "m",
                "comparison_operator": "gte"
            },

            {
                "name": "tipo_alimentacion",
                "question": "¿cuál es el tipo de alimentación que necesitas?",
                "reason": "Para asegurar el tipo de alimentación",
                "type": "selection",
                "comparison_operator": "eq"
            }
        ]
    },
    {
        "type_id": "manipulador",
        "name": "Manipulador Telescópico",
        "fields": [
            {
                "name": "capacidad_toneladas",
                "question": "¿qué peso requiere mover en toneladas?",
                "reason": "Para determinar la capacidad necesaria",
                "type": "number",
                "unit": "toneladas",
                "comparison_operator": "gte"
            }
        ]
    }
]

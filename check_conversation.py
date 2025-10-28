import os
import json
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
from state_management import MaquinariaType
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError


def clasificar_mensaje(message: str) -> str:
    """
    Clasifica un mensaje en: valido, competencia_prohibido, fuera_de_dominio.
    Devuelve la etiqueta como string.
    Se usa el modelo Ministral-3B de Foundry porque es el más económico.
    """

    def _clasificar():
        # Configuración de cliente
        endpoint = os.environ["FOUNDRY_ENDPOINT"] + "models"
        model_name = "Ministral-3B"
        api_key = os.environ["FOUNDRY_API_KEY"]

        client = ChatCompletionsClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(api_key),
            api_version="2024-05-01-preview"
        )   

        maquinaria_types = [maquinaria_type.value for maquinaria_type in MaquinariaType]

        system_prompt = (
            "Eres un clasificador de intenciones para un chatbot de ventas de maquinaria.\n\n"
            "Clasifica cada mensaje en UNA de estas tres categorías:\n\n"
            
            "1. VALIDO - Incluye CUALQUIER consulta con las siguientes características:\n"
            "   - Preguntas sobre tipos de maquinaria:" + ", ".join(maquinaria_types) + "\n"
            "   - Consultas sobre PRECIOS de maquinaria específica\n"
            "   - Preguntas sobre disponibilidad de inventario\n"
            "   - Preguntas sobre características y especificaciones\n"
            "   - Consultas sobre marcas de maquinaria\n"
            "   - Información sobre características y especificaciones de maquinaria (capacidad, altura, etc.)\n"
            "   - Solicitudes de cotización\n"
            "   - Información personal del cliente (nombre, empresa, contacto, lugar de requerimiento)\n"
            "   - Preguntas sobre por qué necesita ciertos datos\n"
            "   - Preguntas sobre cómo se llama el asistente\n"
            "   - Detalles sobre proyectos que requieren maquinaria\n\n"
            
            "2. COMPETENCIA_PROHIBIDO - Consultas sobre otros proveedores:\n"
            "   - Preguntas sobre precios de competidores\n"
            "   - Comparativas con otros proveedores\n"
            "   - Recomendaciones de proveedores externos\n"
            "   - Consultas sobre alternativas a Alpha C\n\n"
            
            "3. FUERA_DE_DOMINIO - Cualquier tema no relacionado con maquinaria:\n"
            "   - Historia, ciencia general\n"
            "   - Entretenimiento, deportes, cultura\n"
            "   - Tecnología no relacionada con maquinaria\n"
            "   - Política, religión, temas controversiales\n\n"
            
            "EJEMPLOS IMPORTANTES:\n"
            "- '¿Cuál es el precio de la soldadora Shindaiwa?' → valido\n"
            "- 'Lo necesito de 20 litros' → valido\n"
            "- '¿Cuál es la capital de México?' → fuera_de_dominio\n"
            "- 'Dame precios de otros proveedores' → competencia_prohibido\n\n"
            
            "Responde ÚNICAMENTE con un JSON valido. Ejemplo:\n"
            "{\"label\":\"valido\"}\n"
            "No agregues texto adicional."
        )

        response = client.complete(
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=message),
            ],
            model=model_name,
            temperature=0,
            top_p=1,
            max_tokens=100
        )

        raw_output = response.choices[0].message.content.strip()
        
        # Buscar el inicio del JSON en la respuesta
        json_start = raw_output.find('{')
        if json_start != -1:
            # Extraer solo la parte del JSON
            raw_output = raw_output[json_start:]
            # Buscar el final del JSON
            json_end = raw_output.rfind('}') + 1
            if json_end > 0:
                raw_output = raw_output[:json_end]

        result = json.loads(raw_output)
        return result.get("label", "fuera_de_dominio")

    try:
        # Usar ThreadPoolExecutor con timeout para Azure Functions
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_clasificar)
            result = future.result(timeout=30)  # 30 segundos timeout
            return result
    except FutureTimeoutError:
        print("Timeout en clasificar_mensaje después de 30 segundos")
        return "fuera_de_dominio"  # En caso de timeout, considerar como fuera de dominio por seguridad
    except Exception as e:
        print("Error parseando respuesta:", e)
        return "fuera_de_dominio"

"""
# TESTING
if __name__ == "__main__":
    def test_clasificador_intenciones(mensajes, categoria_esperada):
        print(f"Probando clasificador de intenciones esperando: {categoria_esperada}")
        for mensaje in mensajes:
            categoria = clasificar_mensaje(mensaje)
            if categoria != categoria_esperada:
                print(f"❌ Error: {mensaje} debería ser {categoria_esperada} pero es {categoria}")
            else:
                print(f"✅ {mensaje} es {categoria_esperada}")

    from dotenv import load_dotenv
    load_dotenv()

    mensajes_valido_group1 = [
        "Cuál es el precio de la soldadora Shindaiwa?",
        "Manejan plataformas de elevación?",
        # Información personal del cliente
        "Me llamo Juan Pérez",
        "Soy María González de Constructora ABC",
        "Mi empresa se llama Industrias del Norte",
        "Nos dedicamos a la construcción",
        "Trabajamos en servicios de mantenimiento industrial",
        # Detalles técnicos de maquinaria
        "Necesito un compresor de 200 litros",
        "¿Qué amperaje manejan las soldadoras?",
        "Requiero una plataforma de 15 metros de altura",
        "¿Tienen generadores de 50 kva?",
        "Necesito un montacargas eléctrico para 2 toneladas",
        # Ubicación y logística
        "El equipo es para Ciudad de México",
        "Necesito entrega en Guadalajara",
        "¿Entregas en Monterrey?"
    ]
    mensajes_valido_group2 = [
        # Uso del equipo
        "Es para uso de la empresa",
        "Lo necesito para venta",
        "Es para un proyecto de construcción",
        # Información de contacto
        "Mi correo es juan@empresa.com",
        "Mi teléfono es 555-1234",
        "No tenemos página web",
        "Solo tenemos Facebook",
        # Preguntas técnicas específicas
        "¿Qué tipo de electrodo usa esa soldadora?",
        "¿El compresor es de pistón o tornillo?",
        "¿La plataforma es articulada o telescópica?",
        "¿El generador es trifásico?",
        # Confirmaciones y respuestas afirmativas
        "Sí, necesito esa información",
        "Correcto, esa es mi empresa",
        "Exacto, es para construcción",
        "Sí, es para uso interno",
    ]
    mensajes_valido_group3 = [
        # Respuestas negativas válidas
        "No tengo página web",
        "No estoy seguro del amperaje",
        "Aún no he decidido el modelo",
        "No tengo empresa, soy particular",
        # Preguntas sobre disponibilidad
        "¿Tienen disponible el modelo X?",
        "¿Cuándo pueden entregar?",
        "¿Tienen en inventario?",
        "¿Está disponible para renta?",
        # Detalles de proyecto
        "Es para una obra en construcción",
        "Necesito para mantenimiento de equipos",
        "Es para un proyecto industrial",
        "Lo uso para trabajos de soldadura"
    ]
    mensajes_valido_group4 = [
         # Especificaciones técnicas
        "Necesito que sea eléctrico",
        "Preferiblemente a gas LP",
        "Que sea portátil",
        "Para interior",
        "Para exterior",
        # Cotización y precios
        "¿Pueden cotizar el equipo?",
        "Necesito una cotización",
        "¿Cuál es el precio de renta?",
        "¿Cuánto cuesta por día?",
        # Información adicional
        "También necesito repuestos",
        "¿Incluye mantenimiento?",
        "¿Dan capacitación?",
        "¿Tienen servicio técnico?",
    ]
    mensajes_valido_group5 = [
        "Quiero una torre de luz"
    ]
    '''
    SOLO EJECUTAR UNO DE LOS GRUPOS DE PRUEBAS POR MINUTO
    Esto para evitar que se sobrecargue el modelo y Azure no permita continuar las pruebas.
    '''
    test_clasificador_intenciones(mensajes_valido_group5, "valido")

    mensajes_competencia_prohibido = [
        "Dame precios de otros proveedores de maquinaria",
        "Haz una comparativa de precios entre Alpha C y la competencia",
        "Con quién me recomiéndas conseguir esta maquinaria que no sea Alpha C",
    ]
    # test_clasificador_intenciones(mensajes_competencia_prohibido, "competencia_prohibido")

    mensajes_fuera_de_dominio = [
        "Cuál es la capital de México?",
        "Cuentame una historia de terror",
        "Cuéntame la historia de las torres de iluminación",
    ]
    test_clasificador_intenciones(mensajes_fuera_de_dominio, "fuera_de_dominio")
"""
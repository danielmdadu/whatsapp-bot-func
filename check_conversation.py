import os
import json
import time
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from maquinaria_config import machinery_config_service
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError


def clasificar_mensaje(message: str) -> str:
    """
    Clasifica un mensaje en: valido, competencia_prohibido, fuera_de_dominio.
    Devuelve la etiqueta como string.
    Se reutiliza el mismo deployment gpt-4.1-mini del bot principal (recurso Foundry
    'ai-model-bot' en prod / 'leadsbot-resource' en pruebas, vía FOUNDRY_ENDPOINT).
    Antes se usaba Ministral-3B, pero su deployment tenía RPM=1 y throttleaba (429)
    en conversaciones reales, lo que corrompía los mensajes con el prefijo "(FD)".
    """

    def _clasificar():
        # Configuración de cliente
        endpoint = os.environ["FOUNDRY_ENDPOINT"] + "models"
        model_name = "gpt-4.1-mini"
        api_key = os.environ["FOUNDRY_API_KEY"]

        client = ChatCompletionsClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(api_key),
            api_version="2024-05-01-preview"
        )   

        # Obtener tipos de maquinaria dinámicamente
        maquinaria_types = [m.type_id for m in machinery_config_service.get_all_types()]

        system_prompt = (
            "Eres un clasificador de intenciones para un chatbot de ventas de maquinaria.\n\n"
            "Clasifica cada mensaje en UNA de estas tres categorías:\n\n"
            
            "1. VALIDO - Incluye CUALQUIER consulta con las siguientes características:\n"
            "   - Preguntas sobre tipos de maquinaria:" + ", ".join(maquinaria_types) + "\n"
            "   - Preguntas sobre refacciones de maquinaria\n"
            "   - Consultas sobre PRECIOS de maquinaria específica\n"
            "   - Consultas sobre créditos de maquinaria o financiamiento\n"
            "   - Preguntas sobre disponibilidad de inventario\n"
            "   - Preguntas sobre características y especificaciones\n"
            "   - Consultas sobre marcas de maquinaria\n"
            "   - Información sobre características y especificaciones de maquinaria (capacidad, altura, etc.)\n"
            "   - Solicitudes de cotización\n"
            "   - Información personal del cliente (nombre, empresa, contacto, lugar de requerimiento)\n"
            "   - Preguntas sobre por qué necesita ciertos datos\n"
            "   - Preguntas sobre cómo se llama el asistente\n"
            "   - Detalles sobre proyectos que requieren maquinaria\n"
            "   - Respuestas cortas sobre el giro o actividad de la empresa del cliente (ej: 'mantenimiento', 'construcción', 'minería', 'renta de maquinaria')\n"
            "   - Respuestas sobre si el equipo es para uso propio, venta o renta (ej: 'no, es para uso propio', 'sí, rentamos maquinaria')\n"
            "   - Respuestas de selección cuando el usuario elige entre opciones presentadas (ej: 'la segunda', 'me interesa el primero', 'quiero la opción 3')\n\n"
            
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
            "- 'Me interesa la segunda opción' → valido\n"
            "- 'Quiero el primero' → valido\n"
            "- 'quiero la segunda' → valido (selección de opción presentada)\n"
            "- 'quiero la primera' → valido (selección de opción presentada)\n"
            "- 'la primera opción' → valido (selección de opción presentada)\n"
            "- 'si, la segunda' → valido (selección de opción presentada)\n"
            "- 'la 1' → valido (selección de opción presentada)\n"
            "- 'la 2' → valido (selección de opción presentada)\n"
            "- 'no, nos dedicamos al mantenimiento' → valido (respuesta sobre giro de empresa)\n"
            "- 'nos dedicamos a la construcción' → valido (respuesta sobre giro de empresa)\n"
            "- 'no, es para uso propio' → valido (respuesta sobre tipo de uso)\n"
            "- 'sí, rentamos maquinaria' → valido (respuesta sobre tipo de cliente)\n"
            "- 'no me dedico a eso' → valido (respuesta sobre tipo de cliente)\n"
            "- 'mantenimiento' → valido (respuesta sobre giro de empresa)\n"
            "- 'minería' → valido (respuesta sobre giro de empresa)\n"
            "- 'ninguno' → valido (respuesta indicando que no requiere más maquinaria)\n"
            "- 'ninguna otra' → valido (respuesta indicando que no requiere más maquinaria)\n"
            "- 'no ninguno' → valido (respuesta indicando que no requiere más maquinaria)\n"
            "- 'solo la plataforma' → valido (respuesta indicando que solo requiere esa maquinaria)\n"
            "- 'por ahora solo eso' → valido (respuesta indicando que no requiere más maquinaria)\n"
            "- 'uso propio' → valido (respuesta sobre tipo de uso)\n"
            "- 'cliente_final' → valido (respuesta sobre tipo de cliente)\n"
            "- '¿Cuál es la capital de México?' → fuera_de_dominio\n"
            "- 'Dame precios de otros proveedores' → competencia_prohibido\n\n"
            
            "Responde ÚNICAMENTE con un JSON valido. Ejemplo:\n"
            "{\"label\":\"valido\"}\n"
            "No agregues texto adicional."
        )

        # Reintento con backoff ante throttling (429). gpt-4.1-mini tiene cuota holgada,
        # pero esto absorbe ráfagas puntuales sin caer directo al fail-open.
        # response_format="json_object" fuerza salida JSON limpia (el prompt incluye "JSON").
        response = None
        for intento in range(3):
            try:
                response = client.complete(
                    messages=[
                        SystemMessage(content=system_prompt),
                        UserMessage(content=message),
                    ],
                    model=model_name,
                    temperature=0,
                    top_p=1,
                    max_tokens=100,
                    response_format="json_object"
                )
                break
            except HttpResponseError as e:
                if getattr(e, "status_code", None) == 429 and intento < 2:
                    time.sleep(1.5 * (intento + 1))
                    continue
                raise

        raw_output = response.choices[0].message.content.strip()
        
        # Remove markdown code blocks if present
        if "```json" in raw_output:
            raw_output = raw_output.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_output:
            raw_output = raw_output.split("```")[1].split("```")[0].strip()
        
        # Find the first complete JSON object
        json_start = raw_output.find('{')
        if json_start != -1:
            # Extract only the first JSON object
            raw_output = raw_output[json_start:]
            # Find the matching closing brace for the first object
            brace_count = 0
            json_end = 0
            for i, char in enumerate(raw_output):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break
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
        # Fail-open: un fallo de infra del clasificador de dominio NO debe bloquear al lead.
        # Content-safety y groundness cubren el contenido realmente peligroso por separado.
        return "valido"
    except Exception as e:
        print("Error parseando respuesta:", e)
        # Fail-open: ante error de parseo/API, dejamos pasar el mensaje en vez de vetarlo.
        return "valido"

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
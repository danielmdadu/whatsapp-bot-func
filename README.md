# Bot de WhatsApp para Calificación de Leads de Maquinaria

## Variables de entorno:
```python
# WHATSAPP
VERIFY_TOKEN
RECIPIENT_WAID # Se eliminará en producción
RECIPIENT_WAID_2 # (opcional)
RECIPIENT_WAID_3 # (opcional)
RECIPIENT_WAID_4 # (opcional)
WHATSAPP_ACCESS_TOKEN
PHONE_NUMBER_ID
WHATSAPP_API_VERSION

# AI FOUNDRY (Azure OpenAI)
FOUNDRY_ENDPOINT
FOUNDRY_API_KEY

# COSMOS DB
COSMOS_CONNECTION_STRING
COSMOS_DB_NAME
COSMOS_CONTAINER_NAME

# ODOO CRM
ODOO_BASE_URL
ODOO_DATABASE
ODOO_USERNAME
ODOO_API_KEY
```

## Descripción del Proyecto

Este proyecto implementa un chatbot inteligente automatizado para la calificación de leads de maquinaria ligera, integrando WhatsApp Business API con Azure OpenAI GPT-4.1-mini y LangChain. El sistema está diseñado como una Azure Function que procesa webhooks de WhatsApp y gestiona conversaciones de manera inteligente para recopilar información de clientes potenciales, consultar inventario y sincronizar datos con Odoo CRM.

## Características Principales

### 🤖 **Inteligencia Artificial Avanzada**
- **Motor de IA**: Azure OpenAI GPT-4.1-mini con LangChain.
- **Slot-filling Inteligente**: Implementado mediante `IntelligentSlotFiller`, detecta automáticamente información en los mensajes del usuario, evitando preguntas redundantes.
- **Detección de Respuestas Negativas**: Capacidad de entender cuando un usuario indica que "no tiene" o "no sabe" un dato específico.
- **Respuestas Contextuales**: Genera respuestas naturales adaptadas al flujo de la conversación y al estado actual del lead.
- **Generación Dinámica de Prompts**: Los prompts se construyen dinámicamente basándose en la configuración de la maquinaria (`maquinaria_config.py`).

### 🏭 **Gestión Dinámica de Inventario**
- **Configuración Centralizada**: Definición de tipos de maquinaria y sus campos requeridos en `maquinaria_config.py` (respaldado por Cosmos DB).
- **Recomendaciones Inteligentes**: Sistema que compara los requerimientos del usuario con el inventario disponible (`inventory_data.py`) para sugerir modelos específicos.
- **Respuesta a Dudas de Inventario**: Módulo dedicado (`InventoryResponder`) para resolver dudas sobre disponibilidad y características técnicas.

### 📱 **Integración con WhatsApp**
- **Webhook de WhatsApp**: Conectado directamente a la API de WhatsApp Business.
- **Procesamiento en tiempo real**: Manejo eficiente de mensajes entrantes y salientes.
- **Soporte Multimedia**: Estructura lista para procesar imágenes, videos y documentos.
- **Normalización de números**: Manejo estándar de números telefónicos mexicanos.

### 🛡️ **Sistema de Guardrails de Seguridad**
- **Detección de inyección de código**: Previene ataques SQL, Python y XSS.
- **Análisis de contenido**: Azure Content Safety para detectar contenido inapropiado (Hate, SelfHarm, Sexual, Violence).
- **Protección contra ataques de groundness**: Detecta intentos de manipulación del comportamiento del bot (Jailbreaks).
- **Clasificación de conversación**: Filtra mensajes fuera del dominio de maquinaria.

### 💾 **Gestión de Datos y CRM**
- **Azure Cosmos DB**: Almacenamiento persistente del estado de las conversaciones y configuración de maquinaria.
- **Odoo CRM Integration**: Creación y actualización automática de leads en Odoo (`crm.lead`) vía su API externa JSON-RPC, de forma best-effort (si Odoo falla, la conversación continúa sin interrupción).
- **Estado de Conversación**: Modelo robusto que persiste el progreso del usuario entre mensajes.

## Funcionalidades del Bot

### 📋 **Calificación Automática de Leads**
El bot recopila sistemáticamente:

1. **Información Personal**: Nombre, Apellido, Teléfono, Correo.
2. **Información Empresarial**: Nombre de la empresa, Giro/Actividad, Lugar del requerimiento, Tipo de uso (Empresa/Venta).
3. **Requerimientos Técnicos**: Tipo de maquinaria y especificaciones técnicas precisas.

### 🔧 **Tipos de Maquinaria Soportados**
La configuración es dinámica, pero actualmente soporta:
- **Soldadoras**: Amperaje, tipo de alimentación (eléctrica/combustible).
- **Compresores**: Caudal (CFM), Presión (PSI).
- **Generadores**: Potencia (kW), Tipo (Estacionario/Portátil).
- **Torres de iluminación**: Tipo de reflector (LED), Autonomía.
- **Plataformas de elevación**: Altura de trabajo, Tipo (Tijera/Articulada).
- **Montacargas**: Capacidad de carga, Altura máxima.
- **Manipuladores**: Alcance, Capacidad.
- **Rompedores, Apisonadores, Motobombas, Cortadoras/Dobladoras de Varilla**.

## Arquitectura Técnica

### 🏗️ **Componentes Principales**

1. **`function_app.py`**: Entry point de Azure Function. Maneja el webhook, `check_agent_timeout` y endpoints auxiliares (`agent-message`, `start-bot-mode`).
2. **`whatsapp_bot.py`**: Controlador principal del flujo de WhatsApp. Orquesta la interacción entre el usuario y el cerebro de IA.
3. **`ai_langchain.py`**: Cerebro del bot. Contiene:
    - `IntelligentSlotFiller`: Extrae datos del texto.
    - `IntelligentResponseGenerator`: Crea las respuestas al usuario.
    - `InventoryResponder`: Atiende preguntas directas sobre productos.
4. **`maquinaria_config.py`**: Gestiona la configuración de los equipos (campos, preguntas, validaciones). Soporta carga desde Cosmos DB o fallback local.
5. **`inventory_service.py`** & **`inventory_data.py`**: Lógica de búsqueda y filtrado de productos y base de datos local de productos.
6. **`state_management.py`**: Capa de persistencia (InMemory para dev, Cosmos DB para prod).
7. **`check_guardrails.py`**: Capa de seguridad y filtrado de contenido.
8. **`test_chatbot.py`**: Suite de pruebas para simular conversaciones y validar flujos.

## Testing

El proyecto incluye un script robusto para probar flujos de conversación sin necesidad de usar WhatsApp real.

### 🧪 **Ejecución de Pruebas**

Para ejecutar las pruebas automatizadas de los flujos definidos:
```bash
python test_chatbot.py
```
Esto ejecutará escenarios predefinidos (Usuario directo, Usuario con múltiples datos, Usuario indeciso, etc.) y generará reportes en la carpeta `test_results`.

Para probar manualmente en consola interactiva (chat en terminal):
1. Abrir `test_chatbot.py`.
2. Asegurar que `test_manually(chatbot_instance)` esté descomentado en el bloque `if __name__ == "__main__":`.
3. Ejecutar el script y chatear con el bot en la terminal.

## Configuración y Despliegue

### 🚀 **Instalación Local**
1. Clonar el repositorio.
2. Crear entorno virtual: `python -m venv .venv`.
3. Activar entorno: `source .venv/bin/activate` (Mac/Linux) o `.venv\Scripts\activate` (Windows).
4. Instalar dependencias: `pip install -r requirements.txt`.
5. Crear archivo `.env` o `local.settings.json` con las variables de entorno.
6. Ejecutar localmente: `func start`.

### ☁️ **Despliegue en Azure**
1. Asegurar tener los recursos creados (Function App, Cosmos DB, Azure OpenAI).
2. Configurar las variables de entorno en la Function App "Configuration".
3. Desplegar: `func azure functionapp publish <APP_NAME>`.

## Contribución
El código sigue una arquitectura modular. Para agregar un nuevo tipo de maquinaria, actualice `maquinaria_config.py` y `inventory_data.py`. Para mejorar la IA, revise los prompts en `ai_prompts.py`.

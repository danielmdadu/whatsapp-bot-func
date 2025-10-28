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

# AI FOUNDRY
FOUNDRY_ENDPOINT
FOUNDRY_API_KEY

# COSMOS DB
COSMOS_CONNECTION_STRING
COSMOS_DB_NAME
COSMOS_CONTAINER_NAME
```

## Descripción del Proyecto

Este proyecto implementa un chatbot inteligente automatizado para la calificación de leads de maquinaria ligera, integrando WhatsApp Business API con Azure OpenAI GPT-4.1-mini y LangChain. El sistema está diseñado como una Azure Function que procesa webhooks de WhatsApp y gestiona conversaciones de manera inteligente para recopilar información de clientes potenciales.

## Características Principales

### 🤖 **Inteligencia Artificial Avanzada**
- **Motor de IA**: Azure OpenAI GPT-4.1-mini con LangChain
- **Slot-filling inteligente**: Extrae automáticamente información de los mensajes de los usuarios
- **Respuestas contextuales**: Genera respuestas naturales y conversacionales
- **Detección de intención**: Identifica preguntas sobre inventario vs. información personal

### 📱 **Integración con WhatsApp**
- **Webhook de WhatsApp**: Conectado directamente a la API de WhatsApp Business
- **Procesamiento en tiempo real**: Maneja mensajes entrantes y salientes automáticamente
- **Verificación de usuarios**: Sistema de autorización para usuarios específicos
- **Normalización de números**: Manejo inteligente de números telefónicos mexicanos

### 🛡️ **Sistema de Guardrails de Seguridad**
- **Detección de inyección de código**: Previene ataques SQL, Python y XSS
- **Análisis de contenido**: Azure Content Safety para detectar contenido inapropiado
- **Protección contra ataques de groundness**: Detecta intentos de manipulación del bot
- **Clasificación de conversación**: Filtra mensajes fuera del dominio de maquinaria
- **Timeouts de seguridad**: Protección contra ataques de denegación de servicio

### 💾 **Gestión de Datos**
- **Base de datos Cosmos DB**: Almacenamiento persistente y escalable de conversaciones
- **Integración con HubSpot**: Sincronización automática de leads con CRM
- **Estado de conversación**: Gestión inteligente del estado de cada usuario
- **Operaciones optimizadas**: Actualizaciones granulares para mejor rendimiento

### 🔄 **Gestión de Conversaciones**
- **Modo dual**: Bot automático y modo agente humano
- **Timeout de agente**: Regreso automático al bot después de 30 minutos de inactividad
- **Comandos especiales**: Reset y status para administración
- **Prevención de duplicados**: Detección y filtrado de mensajes duplicados

## Funcionalidades del Bot

### 📋 **Calificación Automática de Leads**
El bot recopila sistemáticamente la siguiente información:

1. **Información Personal**
   - Nombre y apellido del cliente
   - Información de contacto (teléfono, correo electrónico)

2. **Información Empresarial**
   - Nombre de la empresa
   - Giro o actividad de la empresa
   - Sitio web (opcional)
   - Tipo de uso (empresa o venta)

3. **Requerimientos Técnicos**
   - Tipo de maquinaria necesaria
   - Detalles específicos según el tipo de equipo
   - Ubicación del requerimiento

### 🔧 **Tipos de Maquinaria Soportados**
- **Soldadoras**: Amperaje y tipo de electrodo
- **Compresores**: Capacidad de volumen y herramientas a conectar
- **Torres de iluminación**: Preferencia LED
- **Plataformas de elevación**: Altura, actividad y ubicación
- **Generadores**: Actividad y capacidad en kVA/kW
- **Rompedores**: Uso y tipo (eléctrico/neumático)
- **Apisonadores**: Uso, tipo de motor y diafragma
- **Montacargas**: Capacidad, energía, posición del operador y altura
- **Manipuladores**: Capacidad, altura, actividad y tipo de energía

### 🎯 **Respuestas Inteligentes**
- **Preguntas sobre inventario**: Información automática sobre productos disponibles
- **Extracción de información**: Detección inteligente de datos en mensajes naturales
- **Respuestas negativas**: Manejo de "no tengo" o "no especificado"
- **Contexto conversacional**: Considera la última pregunta del bot para mejor interpretación

## Arquitectura Técnica

### 🏗️ **Componentes Principales**

1. **`function_app.py`**: Punto de entrada de Azure Functions
   - Manejo de webhooks de WhatsApp
   - Verificación de tokens
   - Procesamiento de mensajes

2. **`whatsapp_bot.py`**: Lógica del bot de WhatsApp
   - Envío y recepción de mensajes
   - Integración con guardrails
   - Gestión de comandos especiales

3. **`ai_langchain.py`**: Motor de IA y procesamiento de lenguaje
   - Slot-filling inteligente
   - Generación de respuestas contextuales
   - Detección de preguntas sobre inventario

4. **`state_management.py`**: Gestión de estado y persistencia
   - Almacenamiento en Cosmos DB
   - Operaciones optimizadas
   - Modelos de datos

5. **`hubspot_manager.py`**: Integración con CRM
   - Creación y actualización de contactos
   - Sincronización de datos
   - Mapeo de campos

6. **`check_guardrails.py`**: Sistema de seguridad
   - Múltiples capas de protección
   - Análisis de contenido
   - Detección de ataques

### 🔧 **Tecnologías Utilizadas**
- **Azure Functions**: Solución sin servidor para procesamiento
- **Azure OpenAI**: GPT-4.1-mini para procesamiento de lenguaje natural
- **LangChain**: Framework para aplicaciones de IA
- **Azure Cosmos DB**: Base de datos NoSQL para persistencia
- **HubSpot API**: CRM para gestión de leads
- **WhatsApp Business API**: Comunicación con usuarios
- **Azure Content Safety**: Análisis de seguridad de contenido

## Configuración y Despliegue

### 📝 **Variables de Entorno Requeridas**
```bash
# WhatsApp
WHATSAPP_ACCESS_TOKEN=your_token
PHONE_NUMBER_ID=your_phone_id
WHATSAPP_API_VERSION=your_version
VERIFY_TOKEN=your_verify_token

# Azure OpenAI
FOUNDRY_ENDPOINT=your_endpoint
FOUNDRY_API_KEY=your_api_key

# Cosmos DB
COSMOS_CONNECTION_STRING=your_connection_string
COSMOS_DB_NAME=your_database_name
COSMOS_CONTAINER_NAME=your_container_name

# HubSpot
HUBSPOT_ACCESS_TOKEN=your_hubspot_token

# Usuarios autorizados
RECIPIENT_WAID=authorized_user_id
RECIPIENT_WAID_2=authorized_user_id_2
RECIPIENT_WAID_3=authorized_user_id_3
```

### 🚀 **Instalación**
1. Clonar el repositorio
2. Instalar dependencias: `pip install -r requirements.txt`
3. Configurar variables de entorno
4. Desplegar en Azure Functions

## Flujo de Trabajo

1. **Recepción de Mensaje**: WhatsApp envía webhook a Azure Function
2. **Verificación de Seguridad**: Guardrails analizan el mensaje
3. **Procesamiento de IA**: LangChain extrae información y genera respuesta
4. **Actualización de Estado**: Cosmos DB almacena la conversación
5. **Sincronización CRM**: HubSpot actualiza el contacto del lead
6. **Respuesta**: Bot envía respuesta contextual por WhatsApp

## Características de Seguridad

- **Múltiples capas de protección** contra ataques maliciosos
- **Análisis de contenido** en tiempo real
- **Detección de inyección de código** mediante patrones regex
- **Protección contra manipulación** del comportamiento del bot
- **Filtrado de contenido** fuera del dominio de maquinaria
- **Timeouts de seguridad** para prevenir ataques de denegación de servicio

## Monitoreo y Logs

El sistema incluye logging detallado para:
- Procesamiento de mensajes
- Errores de seguridad
- Operaciones de base de datos
- Integración con HubSpot
- Rendimiento de la IA

## Contribución

Este proyecto está diseñado para ser escalable y mantenible, con una arquitectura modular que permite fácil extensión de funcionalidades y mejoras en el procesamiento de lenguaje natural.

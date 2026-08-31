from abc import ABC, abstractmethod
from hmac import new
from typing import Optional, TypedDict, List, Dict, Any
from enum import Enum
from datetime import datetime, timezone
import logging

class ConversationState(TypedDict):
    nombre: Optional[str]
    apellido: Optional[str]
    tipo_ayuda: Optional[str]  # "maquinaria" | "otro" (refacciones, créditos, etc.)
    tipo_maquinaria: Optional[str] # Ahora es string dinámico
    detalles_maquinaria: Dict[str, Any]
    maquina_seleccionada: Optional[str]  # Modelo de la máquina seleccionada para cotización
    maquinas_recomendadas: List[str]  # Lista de modelos recomendados (para mapear "la 1" a modelo)
    maquina_mencionada: Optional[str]  # Código/modelo que el lead mencionó por su cuenta (ej. "PDSG900VR")
    modelo_verificado_inventario: bool  # Modelo exacto confirmado contra Cosmos DB
    marcas_solicitadas: List[str]  # Marcas que pidió el lead (ej. ["DeWalt", "Makita"])
    marcas_aclaradas: bool  # True cuando el bot ya le respondió si manejamos esas marcas
    nombre_empresa: Optional[str]
    giro_empresa: Optional[str]
    lugar_requerimiento: Optional[str]
    tipo_cliente: Optional[str]
    correo: Optional[str]
    telefono: Optional[str]
    constancia_fiscal_entregada: Optional[bool]
    # Campos que no se preguntan al usuario
    messages: List[Dict[str, Any]]  # Cambiado para soportar campos adicionales
    conversation_mode: str  # "bot" | "agente"
    asignado_asesor: Optional[str]
    completed: bool
    cierre_ofrecido: bool  # True cuando ya se preguntó "¿hay algo más...?" (se pregunta una sola vez)
    sin_coincidencias_contexto: Optional[str]  # Requerimiento para el que ya se informó falta de inventario
    derivacion_asesor_confirmada: bool  # Evita repetir el mismo handoff en turnos posteriores
    recordatorios_derivacion_asesor: int  # Varía la respuesta si el lead insiste en el handoff
    # ID del lead en Odoo
    odoo_lead_id: Optional[int]
    # Control del flujo de cotización
    quiere_cotizacion: Optional[str]  # "sí" | "no" | None (None = aún no se ha mostrado el mensaje o no ha respondido)

# Configuración de prioridad de campos para la generación de preguntas
# IMPORTANTE: El orden de los campos de esta variable es el orden en el que se hacen las preguntas
FIELDS_CONFIG_PRIORITY = {
    "nombre": {
        "description": "Nombre del usuario",
        "question": "¿Con quién tengo el gusto?", 
        "reason": "Para brindarte atención personalizada",
        "required": True
    },
    "apellido": {
        "description": "Apellido del usuario",
        "question": "¿Cuál es tu apellido?", 
        "reason": "Para completar tu información personal",
        "required": True
    },
    "tipo_ayuda": {
        "description": "Tipo de ayuda que necesita el usuario",
        "question": "¿En qué te puedo ayudar?", 
        "reason": "Para entender mejor cómo puedo asistirte",
        "required": True
    },
    "tipo_maquinaria": {
        "description": "Tipo de maquinaria que necesita",
        "question": "¿Qué tipo de maquinaria requiere?", 
        "reason": "Para revisar nuestro inventario disponible",
        "required": False  # Solo requerido si tipo_ayuda es "maquinaria"
    },
    "detalles_maquinaria": {
        "description": "Detalles específicos de la máquina",
        "question": None, 
        "reason": None,
        "required": False # Se maneja por separado en la función is_conversation_complete
    },
    "quiere_cotizacion": {
        "description": "Si quiere cotización",
        "question": "¿Quieres que te cotice alguna de las maquinarias disponibles?",
        "reason": "Para ofrecer opciones de maquinaria disponibles",
        "required": False
    },
    "maquina_seleccionada": {
        "description": "Modelo exacto de la máquina seleccionada para cotización",
        "question": None,
        "reason": None,
        "required": False
    },
    "nombre_empresa": {
        "description": "Nombre de la empresa",
        "question": "¿Cuál es el nombre de su empresa?", 
        "reason": "Para generar la cotización a nombre de su empresa",
        "required": False
    },
    "giro_empresa": {
        "description": "Giro o actividad de la empresa",
        "question": "¿Cuál es el giro de su empresa?", 
        "reason": "Para entender mejor sus necesidades específicas",
        "required": False
    },
    "lugar_requerimiento": {
        "description": "Ubicación donde se requiere la máquina",
        "question": "¿En qué ubicación del país necesita el equipo?", 
        "reason": "Para coordinar la entrega del equipo",
        "required": False
    },
    "tipo_cliente": {
        "description": "Si es un cliente final o distribuidor",
        "question": "¿El equipo es para venta o renta?", 
        "reason": "Para ofrecerle los mejores precios",
        "required": False
    },
    "correo": {
        "description": "Correo electrónico del usuario",
        "question": "¿Cuál es su correo electrónico?", 
        "reason": "Para enviarle la cotización",
        "required": False
    },
    "telefono": {
        "description": "Teléfono del usuario",
        "question": "¿Cuál es su teléfono?", 
        "reason": "Para darle seguimiento personalizado",
        "required": False
    },
    "constancia_fiscal_entregada": {
        "description": "Si el usuario ya entregó o adjuntó su Constancia de Situación Fiscal",
        "question": "Por favor comparta su Constancia de Situación Fiscal",
        "reason": "Para verificar su información y brindarle precio preferencial",
        "required": False
    }
}

class ConversationStateStore(ABC):
    """Interfaz para almacenar y recuperar estados de conversación"""
    
    @abstractmethod
    def get_conversation_state(self, user_id: str) -> Optional[ConversationState]:
        """Recupera el estado de conversación para un usuario"""
        pass
    
    @abstractmethod
    def save_conversation_state(self, user_id: str, state: ConversationState) -> None:
        """Guarda el estado de conversación para un usuario"""
        pass
    
    @abstractmethod
    def delete_conversation_state(self, user_id: str) -> None:
        """Elimina el estado de conversación para un usuario"""
        pass

class InMemoryStateStore(ConversationStateStore):
    """Implementación en memoria para testing"""
    
    def __init__(self):
        self._states = {}
    
    def get_conversation_state(self, user_id: str) -> Optional[ConversationState]:
        return self._states.get(user_id)
    
    def save_conversation_state(self, user_id: str, state: ConversationState) -> None:
        self._states[user_id] = state.copy()  # Hacer copia para evitar referencias
    
    def delete_conversation_state(self, user_id: str) -> None:
        self._states.pop(user_id, None)

class CosmosDBStateStore(ConversationStateStore):
    """Implementación con Cosmos DB para producción"""
    
    def __init__(self, cosmos_client, database_name: str, container_name: str):
        self.cosmos_client = cosmos_client
        self.database_name = database_name
        self.container_name = container_name
        self.container = self.cosmos_client.get_database_client(database_name).get_container_client(container_name)
    
    def get_conversation_state(self, user_id: str) -> Optional[ConversationState]:
        """Recupera el estado de conversación desde Cosmos DB"""
        try:
            # Primero verificar si el documento existe
            item_id = f"conv_{user_id}"
            
            # Usar query para verificar existencia sin generar error
            query = "SELECT c.id FROM c WHERE c.id = @item_id"
            parameters = [{"name": "@item_id", "value": item_id}]
            
            items = list(self.container.query_items(
                query=query,
                parameters=parameters,
                partition_key=user_id
            ))
            
            if not items:
                logging.info(f"Lead nuevo detectado: {user_id}")
                return None
            
            # Si existe, leer el documento completo
            response = self.container.read_item(item=item_id, partition_key=user_id)
            logging.info(f"Estado existente cargado para usuario {user_id}")
            return self._cosmos_to_conversation_state(response)
            
        except Exception as e:
            logging.error(f"Error recuperando estado de Cosmos DB: {e}")
            return None
    
    def save_conversation_state(self, user_id: str, state: ConversationState) -> None:
        """
        Guarda el estado de conversación en Cosmos DB de manera optimizada.
        Detecta cambios específicos y ejecuta operaciones granulares.
        """
        try:
            # Obtener estado anterior para comparación
            old_state = self.get_conversation_state(user_id)
            
            if old_state is None:
                # Primera vez: crear documento completo
                self._create_new_conversation_state(user_id, state)
                logging.info(f"Documento inicial creado para usuario {user_id}")
                return
            
            # Detectar y aplicar cambios específicos
            changes_applied = []
            
            # 1. Verificar nuevos mensajes
            if self._has_new_messages(old_state, state):
                new_messages = self._get_new_message(state)
                self._append_messages(user_id, new_messages)
                changes_applied.append(f"{len(new_messages)} mensajes")
            
            # 2. Verificar cambios en campos del lead
            field_changes = self._detect_field_changes(old_state, state)
            if field_changes:
                self._patch_fields(user_id, field_changes)
                changes_applied.append(f"{len(field_changes)} campos")
            
            # 3. Verificar cambio de modo de conversación
            if old_state.get("conversation_mode") != state.get("conversation_mode"):
                self._update_conversation_mode(user_id, state.get("conversation_mode"))
                changes_applied.append("modo conversación")
            
            if changes_applied:
                logging.info(f"Cambios aplicados para usuario {user_id}")
                # logging.info(f"Cambios aplicados para usuario {user_id}: {', '.join(changes_applied)}")
            else:
                logging.info(f"No hay cambios que aplicar para usuario {user_id}")
                
        except Exception as e:
            logging.error(f"Error en operación optimizada, usando fallback completo: {e}")
            # Fallback: guardar documento completo
            cosmos_doc = self._conversation_state_to_cosmos(user_id, state)
            self.container.upsert_item(cosmos_doc)
            # logging.info(f"Estado guardado con fallback completo para usuario {user_id}")

    def add_single_message(self, user_id: str, message_content: Any, whatsapp_message_id: str, state: ConversationState) -> None:
        """Agrega un mensaje único al estado de conversación"""
        try: 
            # Si es una nueva conversación, crear un nuevo documento
            if state.get("messages") == []:
                self._create_new_conversation_state(user_id, state)
            
            # Formatear el mensaje
            new_message = {
                "whatsapp_message_id": whatsapp_message_id,
                "sender": "lead",
                "role": "user",
                "content": "",
                "question_type": "",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            }

            # Si es texto, agregar el texto en content, si es multimedia, agregar nuevo elemento "multimedia"
            if isinstance(message_content, str):
                new_message["content"] = message_content
            else:
                new_message["multimedia"] = message_content

            logging.info(f"Agregando mensaje del usuario {user_id}: {new_message}")
            
            # Usar _append_messages para agregar el mensaje
            self._append_messages(user_id, [new_message])
        except Exception as e:
            logging.error(f"Error agregando mensaje individual: {e}")
            raise

    def add_outgoing_multimedia_message(self, user_id: str, multimedia: Dict[str, Any], whatsapp_message_id: str, state: ConversationState, sender: str = "bot") -> None:
        """
        Registra un mensaje multimedia ENVIADO por el bot (p. ej. la cotización en PDF).

        Usa el mismo formato que los multimedia entrantes del lead y que los que
        manda el agente desde la web, para que la interfaz web los renderice con
        el mismo componente.

        También agrega el mensaje al estado en memoria: `save_conversation_state`
        solo persiste el último mensaje nuevo, así que si el estado en memoria no
        avanza junto con Cosmos, el siguiente guardado se desincroniza.
        """
        try:
            new_message = {
                "whatsapp_message_id": whatsapp_message_id or "",
                "sender": sender,
                "role": "assistant",
                "content": "",
                "question_type": "",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "multimedia": multimedia
            }

            self._append_messages(user_id, [new_message])

            # Mantener el estado en memoria alineado con lo que quedó en Cosmos
            if isinstance(state, dict) and isinstance(state.get("messages"), list):
                state["messages"].append(new_message)

            logging.info(f"Mensaje multimedia saliente registrado para usuario {user_id}: {multimedia}")
        except Exception as e:
            # El archivo ya se envió por WhatsApp; no romper el flujo si falla el registro
            logging.error(f"Error registrando mensaje multimedia saliente para {user_id}: {e}")

    def delete_conversation_state(self, user_id: str) -> None:
        """Elimina el estado de conversación de Cosmos DB"""
        try:
            self.container.delete_item(item=f"conv_{user_id}", partition_key=user_id)
            # logging.info(f"Estado eliminado exitosamente para usuario {user_id}")
        except Exception as e:
            if "Not Found" not in str(e):
                logging.error(f"Error eliminando estado de Cosmos DB: {e}")

    def _create_new_conversation_state(self, user_id: str, state: ConversationState) -> None:
        """Crea un nuevo estado de conversación"""
        cosmos_doc = self._conversation_state_to_cosmos(user_id, state)
        self.container.upsert_item(cosmos_doc)
        logging.info(f"Documento inicial creado para usuario {user_id}")
    
    def _conversation_state_to_cosmos(self, user_id: str, state: ConversationState) -> Dict[str, Any]:
        """Transforma ConversationState al formato de Cosmos DB"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Preparar mensajes con formato completo
        messages_formatted = []
        for msg in state["messages"]:
            msg_formatted = {
                "id": f"msg_{len(messages_formatted)+1}",
                "whatsapp_message_id": msg.get("whatsapp_message_id", ""),
                "sender": msg.get("sender", "lead" if msg["role"] == "user" else "bot"),
                "text": msg["content"],
                "question_type": msg.get("question_type", ""),
                "timestamp": msg.get("timestamp", now),
                "delivered": True,
                "read": False
            }
            messages_formatted.append(msg_formatted)
        
        # Preparar state sin los campos que van al nivel raíz
        state_copy = state.copy()
        state_copy.pop("messages", None)
        state_copy.pop("conversation_mode", None)
        state_copy.pop("asignado_asesor", None)
        state_copy.pop("odoo_lead_id", None)
        
        # Tipo de maquinaria ya es string, no requiere conversión
        pass
        
        cosmos_doc = {
            "id": f"conv_{user_id}",
            "lead_id": user_id,
            "canal": "whatsapp",
            "created_at": now,  # En producción, mantener el valor original si existe
            "updated_at": now,
            "state": state_copy,
            "messages": messages_formatted,
            "conversation_mode": state.get("conversation_mode", "bot"),
            "asignado_asesor": state.get("asignado_asesor"),
            "odoo_lead_id": state.get("odoo_lead_id")
        }
        
        return cosmos_doc
    
    def _cosmos_to_conversation_state(self, cosmos_doc: Dict[str, Any]) -> ConversationState:
        """Transforma documento de Cosmos DB a ConversationState"""
        state = cosmos_doc.get("state", {})
        
        # Convertir mensajes al formato esperado por ai_langchain.py
        messages = []
        for msg in cosmos_doc.get("messages", []):
            msg_converted = {
                "role": "user" if msg["sender"] == "lead" else "assistant",
                # Los mensajes multimedia se guardan con text=None; normalizar a ""
                # para no meter None en el historial que se manda al LLM.
                "content": msg.get("text") or "",
                "question_type": msg.get("question_type"),
                "timestamp": msg.get("timestamp"),
                "sender": msg["sender"]
            }
            messages.append(msg_converted)
        
        # Tipo de maquinaria ya es string
        pass
        
        # Crear ConversationState con todos los campos
        conversation_state: ConversationState = {
            "messages": messages,
            "nombre": state.get("nombre"),
            "apellido": state.get("apellido"),
            "tipo_ayuda": state.get("tipo_ayuda"),
            "tipo_maquinaria": state.get("tipo_maquinaria"),
            "detalles_maquinaria": state.get("detalles_maquinaria", {}),
            "maquina_seleccionada": state.get("maquina_seleccionada"),
            "maquinas_recomendadas": state.get("maquinas_recomendadas", []),
            "tipo_cliente": state.get("tipo_cliente"),
            "nombre_empresa": state.get("nombre_empresa"),
            "giro_empresa": state.get("giro_empresa"),
            "correo": state.get("correo"),
            "telefono": state.get("telefono"),
            "completed": state.get("completed", False),
            "cotizacion_enviada": state.get("cotizacion_enviada", False),
            # Sin esto el flag se perdía entre mensajes y el bot volvía a
            # preguntar "¿hay algo más...?" en cada turno (7.json).
            "cierre_ofrecido": state.get("cierre_ofrecido", False),
            "lugar_requerimiento": state.get("lugar_requerimiento"),
            "conversation_mode": cosmos_doc.get("conversation_mode", "bot"),
            "asignado_asesor": cosmos_doc.get("asignado_asesor"),
            "odoo_lead_id": cosmos_doc.get("odoo_lead_id"),
            "quiere_cotizacion": state.get("quiere_cotizacion"),
            "constancia_fiscal_entregada": state.get("constancia_fiscal_entregada", False)
        }
        
        return conversation_state
    
    # ============================================================================
    # MÉTODOS INTERNOS PARA OPERACIONES GRANULARES (OPTIMIZACIÓN DE PERFORMANCE)
    # ============================================================================
    
    def _has_new_messages(self, old_state: ConversationState, new_state: ConversationState) -> bool:
        """Detecta si hay mensajes nuevos comparando la longitud"""
        old_messages = old_state.get("messages", [])
        new_messages = new_state.get("messages", [])
        return len(new_messages) > len(old_messages)
    
    def _get_new_message(self, new_state: ConversationState) -> List[Dict[str, Any]]:
        """Obtiene solo los mensajes nuevos"""
        new_messages = new_state.get("messages", [])
        # Solo retorna el último mensaje porque el mensaje del lead se agregó previamente en WhatsAppBot.process_message
        return new_messages[-1:]
    
    def _detect_field_changes(self, old_state: ConversationState, new_state: ConversationState) -> Dict[str, Any]:
        """Detecta qué campos del lead han cambiado"""
        changes = {}
        
        # Campos a monitorear para cambios
        fields_to_check = [
            "nombre", "apellido", "tipo_ayuda", "tipo_maquinaria", "detalles_maquinaria",
            "maquina_seleccionada", "maquinas_recomendadas", "tipo_cliente", "nombre_empresa", 
            "giro_empresa", "correo", "telefono", "completed", "cotizacion_enviada",
            "cierre_ofrecido",
            "lugar_requerimiento", "asignado_asesor", "quiere_cotizacion",
            "constancia_fiscal_entregada"
        ]
        
        for field in fields_to_check:
            old_value = old_state.get(field)
            new_value = new_state.get(field)
            
            # Comparacion directa de strings para tipo_maquinaria
            pass
            
            if old_value != new_value:
                changes[field] = new_value
                
        return changes
    
    def _append_messages(self, user_id: str, new_messages: List[Dict[str, Any]]) -> None:
        """Agrega mensajes nuevos usando patch operation"""
        try:
            # Preparar mensajes en formato Cosmos DB
            formatted_messages = []
            for i, msg in enumerate(new_messages):
                msg_formatted = {
                    "id": f"msg_{int(datetime.now(timezone.utc).timestamp())}_{i}",
                    "whatsapp_message_id": msg.get("whatsapp_message_id", ""),
                    "sender": msg.get("sender", "lead" if msg["role"] == "user" else "bot"),
                    "text": msg["content"],
                    "question_type": msg.get("question_type", ""),
                    "timestamp": msg.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                    "delivered": True,
                    "read": False
                }

                if msg.get("multimedia"):
                    msg_formatted["text"] = None
                    msg_formatted["multimedia"] = msg["multimedia"]
                formatted_messages.append(msg_formatted)
            
            # Usar patch operation para agregar mensajes
            patch_ops = []
            for msg in formatted_messages:
                patch_ops.append({
                    "op": "add",
                    "path": f"/messages/-",
                    "value": msg
                })
            
            # También actualizar updated_at
            patch_ops.append({
                "op": "replace",
                "path": "/updated_at",
                "value": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            })
            
            self.container.patch_item(
                item=f"conv_{user_id}",
                partition_key=user_id,
                patch_operations=patch_ops
            )
            
            logging.info(f"Agregados {len(new_messages)} mensajes para usuario {user_id}")
            
        except Exception as e:
            logging.error(f"Error agregando mensajes con patch: {e}")
            raise
    
    def _patch_fields(self, user_id: str, field_changes: Dict[str, Any]) -> None:
        """Actualiza campos específicos usando patch operations"""
        try:
            patch_ops = []
            
            for field_name, new_value in field_changes.items():
                # No es necesaria la conversion de MaquinariaType
                pass
                
                patch_ops.append({
                    "op": "replace",
                    "path": f"/state/{field_name}",
                    "value": new_value
                })
            
            # Actualizar timestamp
            patch_ops.append({
                "op": "replace",
                "path": "/updated_at",
                "value": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            })
            
            self.container.patch_item(
                item=f"conv_{user_id}",
                partition_key=user_id,
                patch_operations=patch_ops
            )
            
            logging.info(f"Actualizados campos {list(field_changes.keys())} para usuario {user_id}")
            
        except Exception as e:
            logging.error(f"Error actualizando campos con patch: {e}")
            raise
    
    def _update_conversation_mode(self, user_id: str, new_mode: str) -> None:
        """Actualiza el modo de conversación"""
        try:
            patch_ops = [
                {
                    "op": "replace",
                    "path": "/conversation_mode",
                    "value": new_mode
                },
                {
                    "op": "replace",
                    "path": "/updated_at",
                    "value": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                }
            ]
            
            self.container.patch_item(
                item=f"conv_{user_id}",
                partition_key=user_id,
                patch_operations=patch_ops
            )
            
            logging.info(f"Modo de conversación actualizado a '{new_mode}' para usuario {user_id}")
            
        except Exception as e:
            logging.error(f"Error actualizando modo de conversación: {e}")
            raise
from abc import ABC, abstractmethod
from hmac import new
from typing import Optional, TypedDict, List, Dict, Any
from enum import Enum
from datetime import datetime, timezone
import logging

class MaquinariaType(str, Enum):
    SOLDADORAS = "soldadora"
    COMPRESOR = "compresor"
    TORRE_ILUMINACION = "torre_iluminacion"
    PLATAFORMA = "plataforma"
    GENERADORES = "generador"
    ROMPEDORES = "rompedor"
    APISONADOR = "apisonador"
    MONTACARGAS = "montacargas"
    MANIPULADOR = "manipulador"

class ConversationState(TypedDict):
    nombre: Optional[str]
    apellido: Optional[str]
    tipo_maquinaria: Optional[MaquinariaType]
    detalles_maquinaria: Dict[str, Any]
    nombre_empresa: Optional[str]
    giro_empresa: Optional[str]
    lugar_requerimiento: Optional[str]
    uso_empresa_o_venta: Optional[str]
    sitio_web: Optional[str]
    correo: Optional[str]
    telefono: Optional[str]
    # Campos que no se preguntan al usuario
    messages: List[Dict[str, Any]]  # Cambiado para soportar campos adicionales
    conversation_mode: str  # "bot" | "agente"
    asignado_asesor: Optional[str]
    completed: bool
    # ID del contacto en HubSpot
    hubspot_contact_id: Optional[str]

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
    "tipo_maquinaria": {
        "description": "Tipo de maquinaria que necesita",
        "question": "¿Qué tipo de maquinaria requiere?", 
        "reason": "Para revisar nuestro inventario disponible",
        "required": True
    },
    "detalles_maquinaria": {
        "description": "Detalles específicos de la máquina",
        "question": None, 
        "reason": None,
        "required": False # Se maneja por separado en la función is_conversation_complete
    },
    "nombre_empresa": {
        "description": "Nombre de la empresa",
        "question": "¿Cuál es el nombre de su empresa?", 
        "reason": "Para generar la cotización a nombre de su empresa",
        "required": True
    },
    "giro_empresa": {
        "description": "Giro o actividad de la empresa",
        "question": "¿Cuál es el giro de su empresa?", 
        "reason": "Para entender mejor sus necesidades específicas",
        "required": True
    },
    "lugar_requerimiento": {
        "description": "Ubicación donde se requiere la máquina",
        "question": "¿En qué ubicación del país necesita el equipo?", 
        "reason": "Para coordinar la entrega del equipo",
        "required": True
    },
    "uso_empresa_o_venta": {
        "description": "Si es para uso de la empresa o para venta",
        "question": "¿El equipo es para uso de la empresa o para venta?", 
        "reason": "Para ofrecerle los mejores precios",
        "required": True
    },
    "sitio_web": {
        "description": "Sitio web de la empresa",
        "question": "¿Cuál es el sitio web de su empresa?", 
        "reason": "Para conocer mejor su empresa y generar una cotización más precisa",
        "required": False
    },
    "correo": {
        "description": "Correo electrónico del usuario",
        "question": "¿Cuál es su correo electrónico?", 
        "reason": "Para enviarle la cotización",
        "required": True
    },
    "telefono": {
        "description": "Teléfono del usuario",
        "question": "¿Cuál es su teléfono?", 
        "reason": "Para darle seguimiento personalizado",
        "required": True
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
        state_copy.pop("hubspot_contact_id", None)
        
        # Convertir MaquinariaType a string para JSON
        if state_copy.get("tipo_maquinaria"):
            state_copy["tipo_maquinaria"] = state_copy["tipo_maquinaria"].value
        
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
            "hubspot_contact_id": state.get("hubspot_contact_id")
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
                "content": msg["text"],
                "question_type": msg.get("question_type"),
                "timestamp": msg.get("timestamp"),
                "sender": msg["sender"]
            }
            messages.append(msg_converted)
        
        # Convertir string a MaquinariaType si existe
        if state.get("tipo_maquinaria"):
            try:
                state["tipo_maquinaria"] = MaquinariaType(state["tipo_maquinaria"])
            except ValueError:
                state["tipo_maquinaria"] = None
        
        # Crear ConversationState con todos los campos
        conversation_state: ConversationState = {
            "messages": messages,
            "nombre": state.get("nombre"),
            "apellido": state.get("apellido"),
            "tipo_maquinaria": state.get("tipo_maquinaria"),
            "detalles_maquinaria": state.get("detalles_maquinaria", {}),
            "sitio_web": state.get("sitio_web"),
            "uso_empresa_o_venta": state.get("uso_empresa_o_venta"),
            "nombre_empresa": state.get("nombre_empresa"),
            "giro_empresa": state.get("giro_empresa"),
            "correo": state.get("correo"),
            "telefono": state.get("telefono"),
            "completed": state.get("completed", False),
            "lugar_requerimiento": state.get("lugar_requerimiento"),
            "conversation_mode": cosmos_doc.get("conversation_mode", "bot"),
            "asignado_asesor": cosmos_doc.get("asignado_asesor"),
            "hubspot_contact_id": cosmos_doc.get("hubspot_contact_id")
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
            "nombre", "apellido", "tipo_maquinaria", "detalles_maquinaria", "sitio_web",
            "uso_empresa_o_venta", "nombre_empresa", 
            "giro_empresa", "correo", "telefono", "completed", 
            "lugar_requerimiento", "asignado_asesor"
        ]
        
        for field in fields_to_check:
            old_value = old_state.get(field)
            new_value = new_state.get(field)
            
            # Convertir MaquinariaType a string para comparación
            if field == "tipo_maquinaria":
                old_value = old_value.value if old_value else None
                new_value = new_value.value if new_value else None
            
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
                # Convertir MaquinariaType a string para JSON
                if field_name == "tipo_maquinaria" and new_value:
                    new_value = new_value.value if hasattr(new_value, 'value') else new_value
                
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
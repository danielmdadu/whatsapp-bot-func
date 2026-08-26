"""
Integración con Odoo CRM (API Externa JSON-RPC, modelo crm.lead)
"""

import os
import requests
from typing import Any, Dict, List, Optional
import logging
import json
from maquinaria_config import machinery_config_service

# Timeout corto a propósito: si Odoo no responde, el bot no debe quedarse
# esperando y bloquear la respuesta al lead por WhatsApp.
REQUEST_TIMEOUT_SECONDS = 8

# País fijo para los leads del bot (México)
ODOO_COUNTRY_ID_MEXICO = 156

# Mapeo de valores: el bot usa "tipo_cliente" con "distribuidor" | "cliente_final",
# pero el campo de Odoo "tipo_prospecto" es una selección cerrada con otros valores.
TIPO_PROSPECTO_MAP = {
    "distribuidor": "distribuidor",
    "cliente_final": "cliente",
}

# Valores válidos del campo de selección "tipo_ayuda" en Odoo (coinciden 1:1 con el bot)
TIPO_AYUDA_VALUES = {"maquinaria", "otro"}


class OdooManager:
    def __init__(self, base_url: str, database: str, username: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.username = username
        self.api_key = api_key
        self.url = f"{self.base_url}/jsonrpc"

        self.lead_id: Optional[int] = None
        self._states_cache: Optional[Dict[str, int]] = None

        self.uid = self._authenticate()

    def _authenticate(self) -> Optional[int]:
        """Autentica contra Odoo (service=common, method=authenticate) y obtiene el uid."""
        try:
            uid = self._call("common", "authenticate", [self.database, self.username, self.api_key, {}])
            if not uid:
                logging.error("Autenticación con Odoo falló: no se obtuvo uid")
                return None
            logging.info(f"Autenticado en Odoo, uid={uid}")
            return uid
        except Exception as e:
            logging.error(f"Error autenticando con Odoo: {e}")
            return None

    def _call(self, service: str, method: str, args: List[Any]) -> Any:
        """Llamada JSON-RPC de bajo nivel contra /jsonrpc."""
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        response = requests.post(self.url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "Error desconocido de Odoo"))
        return data["result"]

    def _execute_kw(self, model: str, method: str, args: List[Any], kwargs: Optional[Dict] = None) -> Any:
        """Llamada a service=object, method=execute_kw sobre un modelo de Odoo."""
        return self._call(
            "object", "execute_kw",
            [self.database, self.uid, self.api_key, model, method, args, kwargs or {}],
        )

    # ------------------------------------------------------------------
    # Leads
    # ------------------------------------------------------------------

    def create_lead(self, wa_id: str, telefono: str) -> Optional[int]:
        """Crea un lead en Odoo al iniciar una conversación de WhatsApp."""
        if not self.uid:
            logging.error("No se puede crear el lead: no hay sesión autenticada con Odoo")
            return None

        values = {
            "type": "lead",
            "name": f"Lead WhatsApp {telefono}",
            "mobile": telefono,
            "identificador_conversacion_whatsapp": wa_id,
            "country_id": ODOO_COUNTRY_ID_MEXICO,
        }

        try:
            logging.info(f"Creando lead en Odoo para wa_id={wa_id}")
            self.lead_id = self._execute_kw("crm.lead", "create", [values])
            logging.info(f"Lead creado exitosamente en Odoo: {self.lead_id}")
            return self.lead_id
        except Exception as e:
            logging.error(f"Error creando lead en Odoo: {e}")
            logging.error(f"Valores que se intentaron enviar: {values}")
            return None

    def search_lead_by_wa_id(self, wa_id: str) -> Optional[int]:
        """Busca un lead existente por su identificador de conversación de WhatsApp."""
        if not self.uid:
            return None
        try:
            results = self._execute_kw(
                "crm.lead", "search_read",
                [[["identificador_conversacion_whatsapp", "=", wa_id]]],
                {"fields": ["id"], "limit": 1},
            )
            return results[0]["id"] if results else None
        except Exception as e:
            logging.error(f"Error buscando lead en Odoo por wa_id={wa_id}: {e}")
            return None

    def update_lead(self, state: Dict, extracted_info: Dict) -> Optional[int]:
        """Actualiza el lead existente con la información extraída del mensaje."""

        if not extracted_info:
            return None

        if not self.uid or not self.lead_id:
            logging.error("No se puede actualizar el lead: falta sesión autenticada o lead_id")
            return None

        values: Dict[str, Any] = {}

        try:
            logging.info(f"Actualizando lead {self.lead_id} en Odoo con información: {extracted_info}")

            for key, value in extracted_info.items():
                current_value = state.get(key)
                # Si el campo ya tenía un valor válido antes de esta extracción, no lo
                # volvemos a mandar, salvo los campos que se acumulan.
                if key not in ["detalles_maquinaria", "apellido", "marcas_solicitadas"] and current_value and current_value not in ["No especificado", "No tiene", None, ""]:
                    continue

                if key == "nombre":
                    # state.get(..., "") no sirve de fallback porque el estado inicial trae
                    # "apellido": None explícito (no ausente); .get devuelve ese None, no "".
                    apellido = state.get("apellido") or extracted_info.get("apellido") or ""
                    values["contact_name"] = f"{value} {apellido}".strip()

                elif key == "apellido":
                    nombre = state.get("nombre") or extracted_info.get("nombre") or ""
                    values["contact_name"] = f"{nombre} {value}".strip() if nombre else value

                elif key == "nombre_empresa":
                    values["partner_name"] = value

                elif key == "giro_empresa":
                    values["giro_empresa"] = value

                elif key == "tipo_ayuda":
                    if value in TIPO_AYUDA_VALUES:
                        values["tipo_ayuda"] = value

                elif key == "tipo_cliente":
                    if value in TIPO_PROSPECTO_MAP:
                        values["tipo_prospecto"] = TIPO_PROSPECTO_MAP[value]

                elif key == "tipo_maquinaria":
                    # Sin campo dedicado todavía en Odoo: se recalcula la nota completa
                    # (tipo_maquinaria + marcas_solicitadas) y se sobrescribe description.
                    values["description"] = self._build_extra_notes(state, extracted_info)

                elif key == "detalles_maquinaria" and isinstance(value, dict):
                    current_detalles = state.get("detalles_maquinaria", {})
                    merged_detalles = {**current_detalles, **value}
                    values["caracteristicas_especificas"] = self._convert_detalles_to_text(
                        merged_detalles, state.get("tipo_maquinaria")
                    )

                elif key in ("maquina_seleccionada", "maquina_mencionada"):
                    values["modelo_especifico"] = value

                elif key == "marcas_solicitadas" and isinstance(value, list):
                    # Sin campo dedicado todavía en Odoo: mismo recálculo completo que tipo_maquinaria.
                    if value:
                        values["description"] = self._build_extra_notes(state, extracted_info)

                elif key == "lugar_requerimiento":
                    state_id = self._resolve_state_id(value)
                    if state_id:
                        values["state_id"] = state_id
                        values["country_id"] = ODOO_COUNTRY_ID_MEXICO

                elif key == "correo":
                    values["email_from"] = value

                elif key == "telefono":
                    values["mobile"] = value

                elif key == "constancia_fiscal_entregada":
                    values["constancia_situacion_fiscal_entregada"] = bool(value)

            if values:
                return self._update_lead(values)
            else:
                logging.info(f"No hay propiedades para actualizar en el lead {self.lead_id}")
                return self.lead_id
        except Exception as e:
            logging.error(f"Error actualizando lead en Odoo: {e}")
            return None

    def _update_lead(self, values: Dict) -> Optional[int]:
        """Ejecuta el write() sobre el lead actual."""
        try:
            result = self._execute_kw("crm.lead", "write", [[self.lead_id], values])
            if result:
                logging.info(f"Lead actualizado exitosamente en Odoo: {self.lead_id}")
                logging.info(f"Valores actualizados: {values}")
                return self.lead_id

            logging.error(f"write() devolvió False para el lead {self.lead_id}")
            return None
        except Exception as e:
            logging.error(f"Error actualizando lead {self.lead_id} en Odoo: {e}")
            logging.error(f"Valores que se intentaron actualizar: {values}")
            return None

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------

    def _resolve_state_id(self, state_name: str) -> Optional[int]:
        """Resuelve el nombre de un estado de México a su state_id de Odoo (res.country.state)."""
        if not state_name:
            return None

        if self._states_cache is None:
            try:
                estados = self._execute_kw(
                    "res.country.state", "search_read",
                    [[["country_id", "=", ODOO_COUNTRY_ID_MEXICO]]],
                    {"fields": ["name"], "limit": 0},
                )
                self._states_cache = {e["name"].lower(): e["id"] for e in estados}
            except Exception as e:
                logging.error(f"Error obteniendo catálogo de estados de Odoo: {e}")
                self._states_cache = {}

        state_id = self._states_cache.get(state_name.strip().lower())
        if not state_id:
            logging.warning(f"No se encontró state_id en Odoo para el estado '{state_name}'")
        return state_id

    def _build_extra_notes(self, state: Dict, extracted_info: Dict) -> Optional[str]:
        """
        Arma el texto de 'description' para los dos campos que todavía no tienen
        campo dedicado en Odoo (tipo_maquinaria, marcas_solicitadas). Se recalcula
        completo a partir del estado en cada llamada (no se acumula por delta),
        así que sobrescribe cualquier nota manual que se haya escrito directamente
        en Odoo para ese lead. Quitar esto en cuanto el integrador agregue campos
        dedicados para tipo_maquinaria y marcas_solicitadas.
        """
        tipo_maquinaria = extracted_info.get("tipo_maquinaria") or state.get("tipo_maquinaria")
        marcas = extracted_info.get("marcas_solicitadas") or state.get("marcas_solicitadas") or []

        parts = []
        if tipo_maquinaria:
            parts.append(f"Tipo de maquinaria de interés: {tipo_maquinaria}")
        if marcas:
            parts.append(f"Marcas solicitadas: {', '.join(marcas)}")

        return " | ".join(parts) if parts else None

    def _convert_detalles_to_text(self, detalles: Dict, tipo_maquinaria) -> str:
        """Convierte los detalles de maquinaria a texto legible usando las preguntas de MAQUINARIA_CONFIG."""

        if not tipo_maquinaria or not detalles:
            return json.dumps(detalles, ensure_ascii=False)

        try:
            config = machinery_config_service.get_config(str(tipo_maquinaria))

            if not config:
                return json.dumps(detalles, ensure_ascii=False)

            text_parts = []

            for field_name, field_value in detalles.items():
                if field_value:
                    question = None
                    for field_config in config.fields:
                        if field_config.name == field_name:
                            question = field_config.question
                            break

                    if question:
                        text_parts.append(f"{question} Respuesta: {field_value}")
                    else:
                        text_parts.append(f"{field_name}: {field_value}")

            return ". ".join(text_parts) + "." if text_parts else ""

        except Exception as e:
            logging.error(f"Error convirtiendo detalles a texto: {e}")
            return json.dumps(detalles, ensure_ascii=False)


def create_odoo_manager_from_env() -> Optional[OdooManager]:
    """
    Crea un OdooManager leyendo credenciales de variables de entorno
    (ODOO_BASE_URL, ODOO_DATABASE, ODOO_USERNAME, ODOO_API_KEY).

    Best-effort a propósito: si falta configuración, Odoo no responde, o la
    autenticación falla, retorna None en vez de lanzar una excepción. La
    integración con Odoo es opcional para que el bot pueda seguir atendiendo
    la conversación aunque el servicio de Odoo esté caído o mal configurado.
    """
    try:
        base_url = os.environ.get("ODOO_BASE_URL")
        database = os.environ.get("ODOO_DATABASE")
        username = os.environ.get("ODOO_USERNAME")
        api_key = os.environ.get("ODOO_API_KEY")

        if not all([base_url, database, username, api_key]):
            logging.warning("Configuración de Odoo incompleta (variables de entorno faltantes); se omite Odoo para este mensaje.")
            return None

        manager = OdooManager(base_url, database, username, api_key)
        if not manager.uid:
            logging.warning("No se pudo autenticar con Odoo; se omite Odoo para este mensaje.")
            return None

        return manager
    except Exception as e:
        logging.error(f"Error inicializando OdooManager, se omite Odoo para este mensaje: {e}")
        return None

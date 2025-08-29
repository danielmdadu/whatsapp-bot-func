"""
Integración con HubSpot CRM
"""

import requests
import os
from typing import Dict, Optional, Callable, Any
import logging


class TokenExpired(Exception):
    pass


class HubSpotManager:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.hubapi.com"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        # Para refresh token
        self.refresh_token = os.environ["HUBSPOT_REFRESH_TOKEN"]
        self.client_id = os.environ["HUBSPOT_CLIENT_ID"]
        self.client_secret = os.environ["HUBSPOT_CLIENT_SECRET"]

    def _refresh_access_token(self) -> bool:
        """Obtiene un nuevo access token usando el refresh token y actualiza self.access_token y self.headers"""
        url = "https://api.hubapi.com/oauth/v1/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            response = requests.post(url, data=data, headers=headers)
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data["access_token"]
                self.headers["Authorization"] = f"Bearer {self.access_token}"
                logging.info("Nuevo access token de HubSpot obtenido correctamente.")
                return True
            else:
                logging.error(f"Error al refrescar token de HubSpot: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logging.error(f"Excepción al refrescar token de HubSpot: {e}")
            return False

    # def create_or_update_contact(self, lead: Lead) -> Optional[str]:
    #     """Crea o actualiza un contacto en HubSpot, refrescando el token si es necesario"""
    #     def _core():
    #         # Preparar propiedades del contacto
    #         properties = {
    #             "wa_id": lead.wa_id,
    #             "whatsapp_lead": "true",
    #             "lifecyclestage": "lead"
    #         }
    #         if lead.name:
    #             properties["firstname"] = lead.name
    #         if lead.company_name:
    #             properties["empresa_asociada"] = lead.company_name
    #         if lead.phone:
    #             properties["phone"] = lead.phone
    #         if lead.email:
    #             properties["email"] = lead.email
    #         if lead.equipment_interest:
    #             properties["equipo_interesado"] = lead.equipment_interest
            
    #         # TODO: Agregar campo para giro de empresa cuando se cree en HubSpot
    #         # if lead.company_business:
    #         #     properties["giro_empresa"] = lead.company_business
            
    #         # TODO: Agregar campo para características de máquina cuando se cree en HubSpot
    #         # if lead.machine_characteristics:
    #         #     properties["caracteristicas_maquina"] = "; ".join(lead.machine_characteristics)
            
    #         # TODO: Agregar campo para tipo de cliente (distribuidor/cliente final) cuando se cree en HubSpot
    #         # if lead.is_distributor is not None:
    #         #     properties["tipo_cliente"] = "distribuidor" if lead.is_distributor else "cliente_final"
            
    #         logging.info(f"Preparando contacto para HubSpot - WhatsApp ID: {lead.wa_id}")
    #         logging.info(f"Propiedades a enviar: {properties}")
            
    #         # Intentar actualizar contacto existente primero
    #         if lead.hubspot_contact_id:
    #             result = self._update_contact(lead.hubspot_contact_id, properties)
    #             if result:
    #                 return result
    #         # Buscar contacto existente por wa_id
    #         existing_contact = self._find_contact_by_wa_id(lead.wa_id)
    #         if existing_contact:
    #             result = self._update_contact(existing_contact, properties)
    #             if result:
    #                 return result
    #         # Crear nuevo contacto
    #         logging.info("Creando nuevo contacto en HubSpot")
    #         return self._create_contact(properties)
    #     try:
    #         return _core()
    #     except Exception as e:
    #         logging.error(f"Error en HubSpot: {e}")
    #         return None
    
    # def create_new_contact(self, lead: Lead) -> Optional[str]:
    #     """Crea un nuevo contacto en HubSpot sin verificar si existe uno previo"""
    #     def _core():
    #         # Preparar propiedades del contacto
    #         properties = {
    #             "wa_id": lead.wa_id,
    #             "whatsapp_lead": "true",
    #             "lifecyclestage": "lead"
    #         }
    #         if lead.name:
    #             properties["firstname"] = lead.name
    #         if lead.company_name:
    #             properties["empresa_asociada"] = lead.company_name
    #         if lead.phone:
    #             properties["phone"] = lead.phone
    #         if lead.email:
    #             properties["email"] = lead.email
    #         if lead.equipment_interest:
    #             properties["equipo_interesado"] = lead.equipment_interest
            
    #         # TODO: Agregar campo para giro de empresa cuando se cree en HubSpot
    #         # if lead.company_business:
    #         #     properties["giro_empresa"] = lead.company_business
            
    #         # TODO: Agregar campo para características de máquina cuando se cree en HubSpot
    #         # if lead.machine_characteristics:
    #         #     properties["caracteristicas_maquina"] = "; ".join(lead.machine_characteristics)
            
    #         # TODO: Agregar campo para tipo de cliente (distribuidor/cliente final) cuando se cree en HubSpot
    #         # if lead.is_distributor is not None:
    #         #     properties["tipo_cliente"] = "distribuidor" if lead.is_distributor else "cliente_final"
            
    #         logging.info(f"Creando nuevo contacto en HubSpot para reset - WhatsApp ID: {lead.wa_id}")
    #         logging.info(f"Propiedades a enviar: {properties}")
            
    #         return self._create_contact(properties)
        
    #     try:
    #         return _core()
    #     except Exception as e:
    #         logging.error(f"Error creando nuevo contacto en HubSpot: {e}")
    #         return None
    
    def _create_contact(self, properties: Dict) -> Optional[str]:
        """Crea un nuevo contacto"""
        response = requests.post(
            f"{self.base_url}/crm/v3/objects/contacts",
            headers=self.headers,
            json={"properties": properties}
        )
        if response.status_code == 201:
            data = response.json()
            logging.info(f"Contacto creado exitosamente: {data['id']}")
            logging.info(f"Propiedades del contacto creado: {properties}")
            return data['id']
        elif response.status_code == 401:
            # Intentar refresh y reintentar una vez
            logging.warning("Token de HubSpot expirado al crear contacto. Intentando refrescar...")
            if self._refresh_access_token():
                response = requests.post(
                    f"{self.base_url}/crm/v3/objects/contacts",
                    headers=self.headers,
                    json={"properties": properties}
                )
                if response.status_code == 201:
                    data = response.json()
                    logging.info(f"Contacto creado exitosamente tras refrescar token: {data['id']}")
                    return data['id']
        # Otros errores
        logging.error(f"Error creando contacto: {response.status_code}")
        logging.error(f"Respuesta de HubSpot: {response.text}")
        logging.error(f"Propiedades que se intentaron enviar: {properties}")
        return None
    
    def _update_contact(self, contact_id: str, properties: Dict) -> Optional[str]:
        """Actualiza un contacto existente"""
        response = requests.patch(
            f"{self.base_url}/crm/v3/objects/contacts/{contact_id}",
            headers=self.headers,
            json={"properties": properties}
        )
        if response.status_code == 200:
            logging.info(f"Contacto actualizado exitosamente: {contact_id}")
            logging.info(f"Propiedades actualizadas: {properties}")
            return contact_id
        elif response.status_code == 401:
            logging.warning("Token de HubSpot expirado al actualizar contacto. Intentando refrescar...")
            if self._refresh_access_token():
                response = requests.patch(
                    f"{self.base_url}/crm/v3/objects/contacts/{contact_id}",
                    headers=self.headers,
                    json={"properties": properties}
                )
                if response.status_code == 200:
                    logging.info(f"Contacto actualizado exitosamente tras refrescar token: {contact_id}")
                    return contact_id
        logging.error(f"Error actualizando contacto {contact_id}: {response.status_code}")
        logging.error(f"Respuesta de HubSpot: {response.text}")
        logging.error(f"Propiedades que se intentaron actualizar: {properties}")
        return None
    
    def _find_contact_by_wa_id(self, wa_id: str) -> Optional[str]:
        """Busca un contacto por wa_id"""
        response = requests.post(
            f"{self.base_url}/crm/v3/objects/contacts/search",
            headers=self.headers,
            json={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "wa_id",
                        "operator": "EQ",
                        "value": wa_id
                    }]
                }]
            }
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('results'):
                return data['results'][0]['id']
            return None
        elif response.status_code == 401:
            logging.warning("Token de HubSpot expirado al buscar contacto. Intentando refrescar...")
            if self._refresh_access_token():
                response = requests.post(
                    f"{self.base_url}/crm/v3/objects/contacts/search",
                    headers=self.headers,
                    json={
                        "filterGroups": [{
                            "filters": [{
                                "propertyName": "wa_id",
                                "operator": "EQ",
                                "value": wa_id
                            }]
                        }]
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('results'):
                        return data['results'][0]['id']
                    return None
        return None
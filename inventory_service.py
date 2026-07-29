
from typing import List, Dict, Any, Union
import re
import unicodedata
from maquinaria_config import machinery_config_service
from pricing_service import get_pricing_service

# Importar inventario local para fallback
try:
    from update_invertory_db.inventory_data import inventario as local_inventory
except ImportError:
    local_inventory = []
    print("Warning: Could not import local inventory from update_invertory_db.inventory_data")

class InventoryService:
    """
    Servicio para buscar y filtrar maquinaria del inventario
    basado en los requerimientos del usuario.
    """
    
    def __init__(self, cosmos_client=None, database_name=None):
        self.config_service = machinery_config_service
        self.container = None
        
        if cosmos_client and database_name:
            try:
                database = cosmos_client.get_database_client(database_name)
                self.container = database.get_container_client("machinery_inventory")
            except Exception as e:
                print(f"Error initializing Cosmos DB container: {e}")
            
        # Fallback for offline testing
        self._local_inventory_fallback = local_inventory
        
        # Pricing service for SQL Server price lookups
        self._pricing_service = get_pricing_service()

    def find_matching_machines(self, machine_type: str, requirements: Dict[str, Any], brands: List[str] = None) -> List[Dict[str, Any]]:
        """
        Encuentra máquinas que coincidan con los requerimientos.
        Returns machines sorted by relevance (closest match first).

        `brands` restringe la búsqueda a las marcas que pidió el lead. Se ignora
        si ninguna máquina de la categoría es de esas marcas: en ese caso ya se
        le aclara aparte que no manejamos esa marca, y dejarlo sin recomendación
        sería peor que ofrecerle la alternativa que sí tenemos.
        """
        # Fetch inventory
        if self.container:
            # Opción 1: Query a la DB
            inventory_items = self._fetch_from_db(machine_type)
            # Fallback CRÍTICO: si el contenedor 'machinery_inventory' no existe o está
            # vacío (ej. PROD, que no lo tiene), _fetch_from_db devuelve []. Sin este
            # respaldo el bot nunca encontraría máquinas y jamás recomendaría/cotizaría.
            if not inventory_items:
                print("ADVERTENCIA: inventario vacío desde Cosmos. Usando inventario local de respaldo (inventory_data).")
                inventory_items = self._local_inventory_fallback
        else:
            inventory_items = self._local_inventory_fallback

        # Filter in memory
        filtered_machines = [
            m for m in inventory_items
            if self._matches_category(m, machine_type)
        ]
        
        if not filtered_machines:
            return []

        if brands:
            by_brand = [m for m in filtered_machines if self._matches_brand(m, brands)]
            if by_brand:
                filtered_machines = by_brand

        # Obtener configuración de campos para saber cómo comparar
        config = self.config_service.get_config(machine_type)
        if not config:
            return filtered_machines # Si no hay config, devolvemos todo lo de la categoría
            
        matching_machines = []
        
        for machine in filtered_machines:
            if self._check_requirements(machine, requirements, config.fields):
                matching_machines.append(machine)
        
        # Sort by relevance: machines closest to requirements appear first
        matching_machines.sort(
            key=lambda m: self._calculate_relevance_score(m, requirements, config.fields)
        )
        
        # Filtrar máquinas demasiado alejadas del requerimiento (umbral 2x)
        matching_machines = self._filter_by_proximity(matching_machines, requirements, config.fields)
        
        # Reglas especiales para generadores portátiles
        if machine_type == "generador":
            matching_machines = self._apply_generator_portable_rules(matching_machines, requirements, filtered_machines)
        
        # Reglas especiales para compresores portátiles
        if machine_type == "compresor":
            matching_machines = self._apply_compressor_portable_rules(matching_machines, requirements, filtered_machines)
        
        # Reglas especiales para soldadoras
        if machine_type == "soldadora":
            matching_machines = self._apply_soldadora_rules(matching_machines, requirements, filtered_machines)
        
        # NOTE: Prices are NOT enriched here. They are fetched later
        # when the user completes the cotización flow.
        
        # Limitar a máximo 2 opciones para no abrumar al lead
        return matching_machines[:2]
    
    def _apply_generator_portable_rules(self, matching_machines: List[Dict[str, Any]], requirements: Dict[str, Any], all_generators: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Reglas especiales para generadores portátiles:
        - Si el cliente solicita < 5 kW → Ofrecer SOLO el modelo Koshin GV-5500s (4.4 kW)
        - Si solicita entre 5 kW y 8 kW → Ofrecer SOLO el modelo Koshin GV-8000S (7.2 kW)
        - Si solicita más de 8 kW → Recomendar los siguientes 3 generadores (estacionarios)
        """
        req_kw = requirements.get("potencia_kw")
        if req_kw is None:
            return matching_machines
        
        try:
            req_kw_val = float(req_kw)
        except (ValueError, TypeError):
            return matching_machines
        
        if req_kw_val < 5:
            # SOLO ofrecer Koshin GV-5500s
            for m in all_generators:
                if m.get("modelo") == "Koshin GV-5500s":
                    return [m]
            return matching_machines
        
        elif req_kw_val <= 8:
            # SOLO ofrecer Koshin GV-8000S
            for m in all_generators:
                if m.get("modelo") == "Koshin GV-8000S":
                    return [m]
            return matching_machines
        
        else:
            # > 8 kW: excluir los portátiles y dejar que se recomienden los estacionarios
            return [m for m in matching_machines if str(m.get("tipo_generador", "")).lower() != "portátil"]

    def _apply_compressor_portable_rules(self, matching_machines: List[Dict[str, Any]], requirements: Dict[str, Any], all_compressors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Reglas especiales para compresores portátiles:
        - Hasta 185 CFM → Ofrecer SOLO AIRMAN PDS185S-6C2
        - Mayor a 185 CFM y hasta 375 CFM → Ofrecer SOLO AIRMAN PDSF375S-DP
        - Arriba de 375 CFM → Recomendaciones normales
        """
        # Solo aplica si el usuario pidió un compresor portátil
        tipo_compresor = requirements.get("tipo_compresor", "")
        if not tipo_compresor or "portatil" not in self._strip_accents(str(tipo_compresor).lower()):
            return matching_machines
        
        req_cfm = requirements.get("caudal_cfm_max")
        if req_cfm is None:
            return matching_machines
        
        try:
            req_cfm_val = float(req_cfm)
        except (ValueError, TypeError):
            return matching_machines
        
        if req_cfm_val <= 185:
            # SOLO ofrecer AIRMAN PDS185S-6C2
            for m in all_compressors:
                if m.get("modelo") == "AIRMAN PDS185S-6C2":
                    return [m]
            return matching_machines
        
        elif req_cfm_val <= 375:
            # SOLO ofrecer AIRMAN PDSF375S-DP
            for m in all_compressors:
                if m.get("modelo") == "AIRMAN PDSF375S-DP":
                    return [m]
            return matching_machines
        
        else:
            # > 375 CFM: recomendaciones normales
            return matching_machines
    def _apply_soldadora_rules(self, matching_machines: List[Dict[str, Any]], requirements: Dict[str, Any], all_soldadoras: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Reglas especiales para soldadoras:
        - Si el cliente solicita ≤ 200 A → Ofrecer SOLO Shindaiwa EGW185MS (185A, gasolina)
        - Si solicita más de 200 A → Recomendaciones normales (modelos diésel)
        """
        req_amp = requirements.get("amperaje_amps_max")
        if req_amp is None:
            return matching_machines
        
        try:
            req_amp_val = float(req_amp)
        except (ValueError, TypeError):
            return matching_machines
        
        if req_amp_val <= 200:
            # SOLO ofrecer Shindaiwa EGW185MS
            for m in all_soldadoras:
                if m.get("modelo") == "Shindaiwa EGW185MS":
                    return [m]
            return matching_machines
        
        else:
            # > 200A: recomendaciones normales (filtro de proximidad ya aplicó)
            return matching_machines

    def _enrich_with_prices(self, machines: List[Dict[str, Any]]) -> None:
        """
        Enrich machines with price data from SQL Server.
        Modifies machines in place.
        """
        for machine in machines:
            modelo = machine.get("modelo", "")
            price_info = self._pricing_service.get_price(modelo)
            
            if price_info:
                machine["precio"] = price_info["price"]
                machine["moneda"] = price_info["currency"]

    def _fetch_from_db(self, machine_type: str) -> List[Dict[str, Any]]:
        """
        Obtiene ítems desde Cosmos DB. 
        """
        try:
            query = "SELECT * FROM c"
            items = list(self.container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            return items
        except Exception as e:
            print(f"Error fetching inventory from Cosmos: {e}")
            return []

    def _matches_category(self, machine: Dict[str, Any], machine_type: str) -> bool:
        """Verifica si la máquina pertenece a la categoría solicitada"""
        target_keyword = machine_type.lower()
        machine_cat = machine.get("categoria", "").lower()
        
        # Coincidencia directa o parcial
        return target_keyword in machine_cat or machine_cat in target_keyword

    def _matches_brand(self, machine: Dict[str, Any], brands: List[str]) -> bool:
        """La marca es el primer token del modelo: 'Toku TCB-300' → 'Toku'."""
        modelo = str(machine.get("modelo") or "").strip()
        if not modelo:
            return False
        marca = self._strip_accents(modelo.split()[0].lower())
        return any(marca == self._strip_accents(str(b).strip().lower()) for b in brands)

    def _check_requirements(self, machine: Dict[str, Any], requirements: Dict[str, Any], fields_config: List[Any]) -> bool:
        """Verifica si una máquina específica cumple con todos los requerimientos"""
        
        for field in fields_config:
            # Si el usuario no especificó este requerimiento, saltar
            if field.name not in requirements or not requirements[field.name]:
                continue
                
            # El nombre del campo en requirements puede diferir del nombre en inventory si la extracción no es perfecta,
            # pero asumimos que la extracción usa los nombres definidos en config.fields.
            # config.fields.name apunta a la key del inventario (ej: amperaje_amps_max).
            
            req_value = requirements.get(field.name)
            # En ciertos casos, la extracción podría devolver el campo "amperaje" en lugar de "amperaje_amps_max"
            # si el nombre del campo en el prompt no fue actualizado.
            # Pero IntelligentSlotFiller usa la config para generar los prompts, 
            # así que el LLM debería extraer "amperaje_amps_max" si ese es el nombre del field.
            
            machine_value = machine.get(field.name)
            
            # Si la máquina no tiene el dato, asumimos que NO cumple 
            if machine_value is None:
                # Opcional: si es null, tal vez permitirlo? Por ahora estricto.
                continue 

            if not self._compare_values(req_value, machine_value, field.comparison_operator, field.type):
                # Excepción: Soldadora de 185A a gasolina, si el usuario pidió <= 200A
                if field.name == "amperaje_amps_max" and machine.get("categoria") == "soldadora":
                    req_val_num = self._normalize_value(req_value, "number")
                    mach_val_num = self._normalize_value(machine_value, "number")
                    if req_val_num is not None and mach_val_num is not None:
                        # Convertimos a string de manera segura previniendo None
                        tipo_al_str = str(machine.get("tipo_alimentacion") or "")
                        if req_val_num <= 200 and mach_val_num == 185 and self._strip_accents(tipo_al_str.lower()) == "gasolina":
                            continue # Permitir que pase la validación
                
                return False
                
        return True

    def _compare_values(self, req_val: Any, mach_val: Any, operator: str, data_type: str) -> bool:
        """
        Compara valores usando el operador especificado.
        """
        try:
            # Normalización básica
            req_val_norm = self._normalize_value(req_val, data_type)
            mach_val_norm = self._normalize_value(mach_val, data_type)
            
            if req_val_norm is None or mach_val_norm is None:
                return False

            if operator == "gte": # Mayor o igual (para capacidades, alturas)
                # El valor de la máquina (capacidad) debe ser >= requerimiento
                return float(mach_val_norm) >= float(req_val_norm)
            
            elif operator == "lte": # Menor o igual
                return float(mach_val_norm) <= float(req_val_norm)
            
            if operator == "eq": # Igualdad estricta (case + accent insensitive)
                return self._strip_accents(str(mach_val_norm).lower()) == self._strip_accents(str(req_val_norm).lower())
            
            elif operator == "contains": # Contenido (fuzzy match, accent insensitive)
                return self._strip_accents(str(req_val_norm).lower()) in self._strip_accents(str(mach_val_norm).lower())

            return False
            
        except Exception as e:
            # Si falla la conversión o comparación, asumimos falso
            return False

    def _calculate_relevance_score(self, machine: Dict[str, Any], requirements: Dict[str, Any], fields_config: List[Any]) -> float:
        """
        Calculate how closely a machine matches requirements.
        Lower score = better match (closer to exact requirements).
        """
        total_diff = 0.0
        
        for field in fields_config:
            # Only consider fields the user specified
            if field.name not in requirements or not requirements[field.name]:
                continue
            
            # Only score numeric fields with gte/lte operators
            if field.type != "number":
                continue
            
            req_val = self._normalize_value(requirements[field.name], "number")
            mach_val = self._normalize_value(machine.get(field.name), "number")
            
            if req_val is not None and mach_val is not None:
                # Absolute difference between requirement and machine spec
                total_diff += abs(float(mach_val) - float(req_val))
        
        return total_diff

    def _filter_by_proximity(self, machines: List[Dict[str, Any]], requirements: Dict[str, Any], fields_config: List[Any], proximity_factor: float = 2.0) -> List[Dict[str, Any]]:
        """
        Filtra máquinas que estén demasiado alejadas de los requerimientos numéricos.
        
        1. Si hay máquinas con match exacto en TODOS los campos numéricos, retorna solo esas.
        2. Si no, aplica umbral de proximidad: excluye máquinas cuyo valor supere
           proximity_factor * valor_solicitado.
        
        Si después del filtrado no queda ninguna máquina, se devuelve al menos la más cercana.
        """
        if not machines or len(machines) <= 1:
            return machines
        
        # Identificar campos numéricos con operador gte que el usuario especificó
        numeric_gte_fields = [
            field for field in fields_config
            if field.type == "number" 
            and field.comparison_operator == "gte"
            and field.name in requirements 
            and requirements[field.name]
        ]
        
        if not numeric_gte_fields:
            return machines
        
        # Paso 1: Buscar matches exactos
        exact_matches = []
        for machine in machines:
            is_exact = True
            for field in numeric_gte_fields:
                req_val = self._normalize_value(requirements[field.name], "number")
                mach_val = self._normalize_value(machine.get(field.name), "number")
                
                if req_val is None or mach_val is None or float(mach_val) != float(req_val):
                    is_exact = False
                    break
            
            if is_exact:
                exact_matches.append(machine)
        
        # Si hay matches exactos, retornar solo esos
        if exact_matches:
            return exact_matches
        
        # Paso 2: Filtro de proximidad (umbral 2x)
        filtered = []
        for machine in machines:
            keep = True
            for field in numeric_gte_fields:
                req_val = self._normalize_value(requirements[field.name], "number")
                mach_val = self._normalize_value(machine.get(field.name), "number")
                
                if req_val is not None and mach_val is not None:
                    threshold = float(req_val) * proximity_factor
                    if float(mach_val) > threshold:
                        keep = False
                        break
            
            if keep:
                filtered.append(machine)
        
        # Garantizar que al menos la máquina más cercana se devuelva
        if not filtered:
            return [machines[0]]
        
        return filtered

    @staticmethod
    def _strip_accents(text: str) -> str:
        """Elimina acentos/diacríticos para comparaciones robustas (ej: portátil → portatil)"""
        nfkd = unicodedata.normalize('NFKD', text)
        return ''.join(c for c in nfkd if not unicodedata.combining(c))

    def _normalize_value(self, value: Any, data_type: str) -> Union[float, str, bool, None]:
        """Limpia y convierte valores para comparación"""
        if value is None:
            return None
            
        # Si ya es el tipo correcto, devolverlo
        if data_type == "number" and isinstance(value, (int, float)):
            return float(value)
            
        str_val = str(value).strip()
        
        if data_type == "number":
             # Intenta convertir string a float directamente
             # Si tiene texto extra (unidades), intentamos extraer el primer número
             match = re.search(r"[-+]?\d*\.\d+|\d+", str_val)
             if match:
                 return float(match.group())
             return None
            
        if data_type == "boolean":
            return str_val.lower() in ["true", "si", "sí", "yes", "1"]
            
        return str_val

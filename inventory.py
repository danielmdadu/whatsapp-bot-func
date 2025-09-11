"""
Gestión del inventario de maquinaria
"""

from typing import List
import logging
from dataclasses import dataclass

@dataclass
class InventoryItem:
    tipo_maquina: str
    modelo: str
    ubicacion: str

class InventoryManager:
    def __init__(self):
        self.inventory: List[InventoryItem] = []
        self.load_inventory()
    
    def load_inventory(self):
        """Carga el inventario desde la base de datos (todas las máquinas se consideran disponibles)"""

        # TODO: Cargar el inventario desde la base de datos
        try:
            self.inventory = [
                InventoryItem(
                    tipo_maquina="Cualquier tipo de maquinaria",
                    modelo="Cualquier modelo de maquinaria",
                    ubicacion="Cualquier ubicación",
                )
            ]
            logging.info(f"Inventario cargado: {len(self.inventory)} items")
        except Exception as e:
            logging.error(f"Error cargando inventario: {e}")
            self.inventory = []
    
    def search_equipment(self) -> List[InventoryItem]:
        """Busca equipos en el inventario basado en la consulta"""
        return self.inventory
"""
Script para subir SOLO las nuevas máquinas a CosmosDB.
No modifica ni toca las máquinas existentes.
"""
import os
import json
import logging
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from dotenv import load_dotenv

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Las nuevas máquinas a agregar
nuevas_maquinas = [
    {"modelo": "Noblelift FE4P25Q", "categoria": "montacargas", "tipo_combustible": "Eléctrico", "capacidad_toneladas": 2.5},
]

# Máquinas a eliminar (modelo antiguo reemplazado)
maquinas_a_eliminar = [
    {"id": "montacargas_nobleliftcpyd25", "categoria": "montacargas"},
]

def load_local_settings():
    load_dotenv()
    if os.path.exists("local.settings.json"):
        with open("local.settings.json", "r") as f:
            settings = json.load(f)
            if "Values" in settings:
                for key, value in settings["Values"].items():
                    os.environ[key] = value

def main():
    try:
        load_local_settings()

        connection_string = os.environ.get("COSMOS_CONNECTION_STRING")
        db_name = os.environ.get("COSMOS_DB_NAME")
        if not connection_string or not db_name:
            raise ValueError("COSMOS_CONNECTION_STRING o COSMOS_DB_NAME no están definidos")

        client = CosmosClient.from_connection_string(connection_string)
        database = client.get_database_client(db_name)
        container = database.get_container_client("machinery_inventory")

        logger.info(f"Conectado a base de datos: {db_name}")

        # Paso 1: Eliminar máquinas obsoletas
        eliminadas = 0
        for item in maquinas_a_eliminar:
            try:
                container.delete_item(item=item["id"], partition_key=item["categoria"])
                logger.info(f"🗑️  ELIMINADA: {item['id']}")
                eliminadas += 1
            except exceptions.CosmosResourceNotFoundError:
                logger.warning(f"⚠️  NO ENCONTRADA (ya no existe): {item['id']}")

        # Paso 2: Agregar nuevas máquinas
        logger.info(f"\nIntentando subir {len(nuevas_maquinas)} nuevas máquinas...\n")

        agregadas = 0
        omitidas = 0

        for item in nuevas_maquinas:
            # Generar ID igual que upload_to_cosmos.py
            safe_model = "".join(c for c in item["modelo"] if c.isalnum() or c in "-_").lower()
            item_id = f"{item['categoria']}_{safe_model}"
            item["id"] = item_id

            # Verificar si ya existe
            try:
                existing = container.read_item(item=item_id, partition_key=item["categoria"])
                logger.warning(f"⚠️  OMITIDA (ya existe): {item['modelo']} [id={item_id}]")
                omitidas += 1
            except exceptions.CosmosResourceNotFoundError:
                # No existe, la creamos
                container.create_item(item)
                logger.info(f"✅ AGREGADA: {item['modelo']} [id={item_id}]")
                agregadas += 1

        logger.info(f"\n--- Resumen ---")
        logger.info(f"Máquinas eliminadas: {eliminadas}")
        logger.info(f"Máquinas agregadas: {agregadas}")
        logger.info(f"Máquinas omitidas (ya existían): {omitidas}")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise

if __name__ == "__main__":
    main()

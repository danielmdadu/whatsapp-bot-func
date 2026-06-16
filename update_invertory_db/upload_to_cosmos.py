import os
import json
import logging
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from azure.cosmos import CosmosClient, PartitionKey
from maquinaria_config import machinery_config_service, MachineryTypeSchema
from inventory_data import inventario

from dotenv import load_dotenv

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_local_settings():
    """Carga variables de entorno desde local.settings.json y .env"""
    # Cargar desde .env
    load_dotenv()
    logger.info("Variables de entorno cargadas desde .env")

    # Cargar desde local.settings.json si existe
    if os.path.exists("local.settings.json"):
        with open("local.settings.json", "r") as f:
            settings = json.load(f)
            if "Values" in settings:
                for key, value in settings["Values"].items():
                    os.environ[key] = value
                logger.info("Variables de entorno cargadas desde local.settings.json")

def get_cosmos_client():
    """Obtiene el cliente de Cosmos DB"""
    connection_string = os.environ.get("COSMOS_CONNECTION_STRING")
    if not connection_string:
        raise ValueError("COSMOS_CONNECTION_STRING no está definido")
    
    return CosmosClient.from_connection_string(connection_string)

def upload_configs(database):
    """Sube la configuración de maquinaria a Cosmos DB"""
    container_name = "machinery_configuration"
    
    # Crear contenedor si no existe
    logger.info(f"Verificando contenedor {container_name}...")
    container = database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/type_id")
    )
    
    logger.info(f"Subiendo configuraciones...")
    
    # Iterar sobre todos los tipos de maquinaria dinámicamente
    all_configs = machinery_config_service.get_all_types()
    count = 0
    
    for config in all_configs:
        # Convertir a dict
        try:
            item = config.model_dump()
        except AttributeError:
            item = config.dict()
        
        # Asegurar que tiene id (usamos el mismo type_id)
        item["id"] = item["type_id"]
        
        # Subir a Cosmos
        container.upsert_item(item)
        logger.info(f"Configuración subida: {config.name}")
        count += 1
            
    logger.info(f"Total de configuraciones subidas: {count}")

def upload_inventory(database):
    """Sube el inventario a Cosmos DB"""
    container_name = "machinery_inventory"
    
    # Crear contenedor si no existe
    # Partition key por categoría parece sensato para búsquedas filtradas
    logger.info(f"Verificando contenedor {container_name}...")
    container = database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/categoria")
    )
    
    logger.info(f"Subiendo inventario...")
    
    count = 0
    for item in inventario:
        # Cosmos necesita un campo 'id' obligatorio
        # Generamos uno basado en el modelo si no existe, o un hash simple
        if "id" not in item:
            # Limpiar modelo para usarlo como ID seguro
            safe_model = "".join(c for c in item["modelo"] if c.isalnum() or c in "-_").lower()
            item["id"] = f"{item['categoria']}_{safe_model}"
        
        # Subir a Cosmos
        container.upsert_item(item)
        logger.info(f"Máquina subida: {item['modelo']}")
        count += 1
        
    logger.info(f"Total de máquinas subidas: {count}")

def main():
    try:
        load_local_settings()
        
        client = get_cosmos_client()
        db_name = os.environ.get("COSMOS_DB_NAME")
        if not db_name:
            raise ValueError("COSMOS_DB_NAME no está definido")
        
        database = client.get_database_client(db_name)
        logger.info(f"Conectado a base de datos: {db_name}")
        
        upload_configs(database)
        upload_inventory(database)
        
        logger.info("¡Carga completada exitosamente!")
        
    except Exception as e:
        logger.error(f"Error en el proceso: {e}")

if __name__ == "__main__":
    main()

"""
Model Code Mapping

Maps local inventory model names to SQL database product codes.
Only includes the 30 machines that match between inventory_data.py 
and the SQL inventario_odoo_chatbot table.

This mapping enables price lookups from SQL Server for matched machines.
"""

# Maps: local modelo name -> SQL CODIGO
MODEL_CODE_MAPPING = {
    # =========================================================================
    # APISONADOR (1)
    # =========================================================================
    "Sakai RS75": "RS75",
    
    # =========================================================================
    # COMPRESOR (9)
    # =========================================================================
    "AIRMAN SAS22RD6E": "SAS22RD6E",
    "AIRMAN SAS37RD6E": "SAS37RD6E",
    "AIRMAN SAS4SD6C": "SAS4SD6C",
    "AIRMAN SAS55RD6E": "SAS55RD6E",
    "AIRMAN SAS75RD6E": "SAS75RD6E",
    "AIRMAN SAS8SD6C": "SAS8SD6C",
    "AIRMAN PDS750S-4B1": "PDS750S4B1",
    "AIRMAN PDS185S-6C2": "PDS185S6C2",
    "AIRMAN PDSF375S-DP": "PDS375DP6B",
    
    # =========================================================================
    # ROMPEDOR / MARTILLO NEUMÁTICO (3)
    # =========================================================================
    "Toku TCB-300": "TCB300",
    "Toku TPB-60": "TPB60",
    "Toku TPB-90": "TPB90",
    
    # =========================================================================
    # MOTOBOMBA (2)
    # =========================================================================
    "Koshin KTY-100D": "KTY100D",
    "Koshin KTH-100 X": "KTH100XBAF",
    
    # =========================================================================
    # GENERADOR (6)
    # =========================================================================
    "Shindaiwa DGM250MK-D": "DGM250MKD",
    "Shindaiwa DGM450MK-D": "DGM450MKD",
    "Shindaiwa DGM600MK-D": "DGM600MKD",
    "AIRMAN SDG150S": "SDG150S3A6",
    "Koshin GV-5500s": "GV5500S",
    "Koshin GV-8000S": "GV8000S",
    
    # =========================================================================
    # MONTACARGAS (3)
    # =========================================================================
    "LGMG CPD30": "CPD30",
    "Noblelift CPCD30": "CPCD30",
    "Noblelift FE4P25Q": "FE4P25Q",
    
    # =========================================================================
    # MANIPULADOR (1)
    # =========================================================================
    "LGMG H1840": "H1840",
    
    # =========================================================================
    # PLATAFORMA (6)
    # =========================================================================
    "LGMG A45JE-LI": "A45JELI",
    "LGMG AR52J": "AR52J",
    "LGMG AR60J-2": "AR60J-2",
    "LGMG AR60JE-2": "AR60JE-2",
    "LGMG S2632E II": "S2632EII",
    "LGMG S2632EIILI": "S2632EIILI",
    "LGMG S4046E II": "S4046EII",
    "LGMG SS1230E": "SS1230E",
    "LGMG S1932EII": "S1932EII",
    "LGMG MP0607SE": "MP0607SE",
    "LGMG MP1007SE": "MP1007SE",
    "LGMG MP1208SE": "MP1208SE",
    "LGMG M2640JE": "M2640JE",
    "LGMG S3246E II": "S3246E-2",
    
    # =========================================================================
    # TORRE DE ILUMINACIÓN (0) - No tienen precio en SQL
    # =========================================================================
    
    # =========================================================================
    # CORTADORA DE VARILLAS (1)
    # =========================================================================
    "Simpedil C54 EVO": "C54TTF05",
    
    # =========================================================================
    # DOBLADORA DE VARILLAS (1)
    # =========================================================================
    "Simpedil P54 EVO": "P54TTF06",
    
    # =========================================================================
    # SOLDADORA (4)
    # =========================================================================
    "Shindaiwa DGW400DMK": "DGW400DMKD",
    "Shindaiwa DGW340DM": "DGW340DM",
    "Shindaiwa DGW500DM": "DGW500DM200",
    "Shindaiwa EGW185MS": "EGW185MS",
}

import logging


def get_sql_code(local_model: str) -> str:
    """
    Get the SQL product code for a local model name (exact match).
    
    Args:
        local_model: The model name from inventory_data.py
        
    Returns:
        The SQL CODIGO if found, None otherwise
    """
    return MODEL_CODE_MAPPING.get(local_model)


def fuzzy_get_sql_code(partial_model: str) -> tuple:
    """
    Get the SQL product code using fuzzy/partial matching.
    Handles cases where the LLM extracts a partial model name
    (e.g., 'DGM250MK-D' instead of 'Shindaiwa DGM250MK-D').
    
    Args:
        partial_model: Partial or full model name
        
    Returns:
        Tuple of (full_model_name, sql_code) if found, (None, None) otherwise
    """
    # 1. Try exact match first
    if partial_model in MODEL_CODE_MAPPING:
        return partial_model, MODEL_CODE_MAPPING[partial_model]
    
    partial_lower = partial_model.lower().strip()
    
    # 2. Check if partial_model is a substring of any key
    matches = []
    for full_name, sql_code in MODEL_CODE_MAPPING.items():
        full_lower = full_name.lower()
        if partial_lower in full_lower or full_lower.endswith(partial_lower):
            matches.append((full_name, sql_code))
    
    if len(matches) == 1:
        logging.info(f"[PRICING_DEBUG] fuzzy_get_sql_code: '{partial_model}' matched to '{matches[0][0]}' → '{matches[0][1]}'")
        return matches[0]
    elif len(matches) > 1:
        # Multiple matches — pick the one where partial matches the end (most specific)
        logging.warning(f"[PRICING_DEBUG] fuzzy_get_sql_code: '{partial_model}' matched MULTIPLE: {[m[0] for m in matches]}. Using first match.")
        return matches[0]
    
    logging.info(f"[PRICING_DEBUG] fuzzy_get_sql_code: '{partial_model}' has NO match in MODEL_CODE_MAPPING")
    return None, None


def get_all_sql_codes() -> list:
    """
    Get all SQL product codes that have mappings.
    
    Returns:
        List of all SQL CODIGOs
    """
    return list(MODEL_CODE_MAPPING.values())


def has_price_mapping(local_model: str) -> bool:
    """
    Check if a local model has a price mapping (exact match).
    
    Args:
        local_model: The model name from inventory_data.py
        
    Returns:
        True if the model has a mapping, False otherwise
    """
    return local_model in MODEL_CODE_MAPPING


def fuzzy_has_price_mapping(partial_model: str) -> bool:
    """
    Check if a partial model name has a price mapping (fuzzy match).
    
    Args:
        partial_model: Partial or full model name
        
    Returns:
        True if any mapping matches, False otherwise
    """
    full_name, _ = fuzzy_get_sql_code(partial_model)
    return full_name is not None

"""
Inventory Comparison Script (Improved)

Compares machines between the local inventory_data.py and the SQL Server database
using model code extraction for better matching.
"""

import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

try:
    import pyodbc
except ImportError:
    print("ERROR: pyodbc is not installed.")
    sys.exit(1)

from inventory_data import inventario


def extract_model_code(name):
    """Extract the model code from a machine name for comparison."""
    if not name:
        return ""
    
    # Remove common prefixes/suffixes and normalize
    name = name.upper().strip()
    
    # Extract from SQL format like "[DGW500DM200] DESCRIPTION..."
    bracket_match = re.search(r'\[([^\]]+)\]', name)
    if bracket_match:
        return bracket_match.group(1).replace('-', '').replace(' ', '')
    
    # For local format like "Shindaiwa DGW500DM" or "LGMG AR60JE-2"
    # Extract the model part (usually last word or alphanumeric code)
    parts = name.split()
    
    # Skip brand names and find the model
    brands = {'SHINDAIWA', 'AIRMAN', 'LGMG', 'KOSHIN', 'TOKU', 'SAKAI', 'SIMPEDIL', 'TRIME'}
    model_parts = [p for p in parts if p.upper() not in brands]
    
    if model_parts:
        # Join and normalize
        model = ''.join(model_parts).replace('-', '').replace(' ', '')
        return model
    
    return name.replace('-', '').replace(' ', '')


def normalize_for_comparison(code):
    """Further normalize a code for fuzzy matching."""
    code = code.upper()
    # Remove common suffixes that might differ
    code = re.sub(r'(II|2|LI|ELI|E|S|D)$', '', code)
    code = code.replace('-', '').replace(' ', '')
    return code


def get_sql_server_inventory():
    """Fetch inventory from SQL Server database."""
    server = os.environ.get('PRICES_SQL_SERVER')
    database = os.environ.get('PRICES_SQL_DATABASE')
    username = os.environ.get('PRICES_SQL_USERNAME')
    password = os.environ.get('PRICES_SQL_PASSWORD')
    
    connection_string = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={server};"
        f"Database={database};"
        f"Uid={username};"
        f"Pwd={password};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
    )
    
    print("Connecting to SQL Server...")
    conn = pyodbc.connect(connection_string, timeout=30)
    cursor = conn.cursor()
    
    cursor.execute("SELECT PRODUCTO, CODIGO, CATEGORIA, EXISTENCIA_TOTAL FROM [dbo].[inventario_odoo_chatbot]")
    
    rows = cursor.fetchall()
    conn.close()
    
    sql_inventory = []
    for row in rows:
        producto = row[0] or ''
        codigo = row[1] or ''
        sql_inventory.append({
            'producto': producto,
            'codigo': codigo,
            'categoria': row[2],
            'existencia_total': row[3],
            'model_code': codigo.upper().replace('-', '').replace(' ', '')  # Use CODIGO directly
        })
    
    return sql_inventory


def get_local_inventory():
    """Get inventory from local inventory_data.py."""
    local_machines = []
    for item in inventario:
        modelo = item.get('modelo', '')
        local_machines.append({
            'modelo': modelo,
            'categoria': item.get('categoria', ''),
            'model_code': extract_model_code(modelo)
        })
    return local_machines


def find_best_match(local_code, sql_items):
    """Find the best matching SQL item for a local model code."""
    # Exact match on code
    for item in sql_items:
        if item['model_code'] == local_code:
            return item, 'exact'
    
    # Normalized match
    local_norm = normalize_for_comparison(local_code)
    for item in sql_items:
        sql_norm = normalize_for_comparison(item['model_code'])
        if local_norm == sql_norm:
            return item, 'normalized'
        # Check if one contains the other
        if local_norm in sql_norm or sql_norm in local_norm:
            return item, 'partial'
    
    return None, None


def compare_inventories():
    """Compare local inventory with SQL Server inventory."""
    print("=" * 80)
    print("INVENTORY COMPARISON REPORT (Using Model Codes)")
    print("=" * 80)
    
    sql_inventory = get_sql_server_inventory()
    local_inventory = get_local_inventory()
    
    print(f"\n📊 Summary:")
    print(f"   - Local inventory (inventory_data.py): {len(local_inventory)} machines")
    print(f"   - SQL Server inventory: {len(sql_inventory)} products")
    
    # Track matches
    matched = []
    only_local = []
    matched_sql_codes = set()
    
    for local_item in local_inventory:
        sql_match, match_type = find_best_match(local_item['model_code'], sql_inventory)
        if sql_match:
            matched.append({
                'local': local_item,
                'sql': sql_match,
                'match_type': match_type
            })
            matched_sql_codes.add(sql_match['model_code'])
        else:
            only_local.append(local_item)
    
    only_sql = [item for item in sql_inventory if item['model_code'] not in matched_sql_codes]
    
    # ===== MATCHES =====
    print(f"\n{'=' * 80}")
    print(f"✅ MACHINES FOUND IN BOTH INVENTORIES ({len(matched)})")
    print("=" * 80)
    
    if matched:
        for m in sorted(matched, key=lambda x: x['local']['categoria']):
            local = m['local']
            sql = m['sql']
            print(f"\n   📍 Local: {local['modelo']} ({local['categoria']})")
            print(f"      SQL:   [{sql['codigo']}] {sql['producto'][:50]}...")
            print(f"      Stock: {sql['existencia_total']} | Match: {m['match_type']}")
    else:
        print("   (No matches found)")
    
    # ===== ONLY LOCAL =====
    print(f"\n{'=' * 80}")
    print(f"⚠️  MACHINES ONLY IN LOCAL INVENTORY ({len(only_local)})")
    print("   (NOT found in SQL Server - may need to be added or names differ)")
    print("=" * 80)
    
    if only_local:
        by_cat = {}
        for item in only_local:
            cat = item['categoria']
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(item)
        
        for cat in sorted(by_cat.keys()):
            print(f"\n   [{cat.upper()}]")
            for item in sorted(by_cat[cat], key=lambda x: x['modelo']):
                print(f"      • {item['modelo']} (code: {item['model_code']})")
    
    # ===== ONLY SQL =====
    print(f"\n{'=' * 80}")
    print(f"🆕 PRODUCTS ONLY IN SQL SERVER ({len(only_sql)})")
    print("   (NOT in local inventory - may need to be added)")
    print("=" * 80)
    
    if only_sql:
        by_cat = {}
        for item in only_sql:
            cat = item['categoria'] or 'Uncategorized'
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(item)
        
        for cat in sorted(by_cat.keys()):
            print(f"\n   [{cat}]")
            for item in sorted(by_cat[cat], key=lambda x: x['codigo']):
                print(f"      • [{item['codigo']}] {item['producto'][:55]}... (Stock: {item['existencia_total']})")
    
    # ===== SUMMARY =====
    print(f"\n{'=' * 80}")
    print("📈 FINAL SUMMARY")
    print("=" * 80)
    print(f"   ✅ Machines matched:       {len(matched)} ({100*len(matched)/len(local_inventory):.1f}% of local)")
    print(f"   ⚠️  Only in local:         {len(only_local)}")
    print(f"   🆕 Only in SQL Server:     {len(only_sql)}")
    print("=" * 80)


if __name__ == "__main__":
    compare_inventories()

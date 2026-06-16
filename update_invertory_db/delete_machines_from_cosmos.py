"""
Delete selected machinery items from Cosmos DB.

Matches items by searching substring (CONTAINS) in the document field `modelo`.
Deletion uses the Cosmos item `id` and the partition key `categoria`.
"""

import os
import sys
import argparse
from typing import Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv()

# Ensure repo root is on sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from azure.cosmos import CosmosClient, exceptions  # noqa: E402


TOKENS_TO_DELETE = [
    "SAS75VD-E",
    "SAS55VD-E",
    "SAS37VD-E",
    "PDSF830S",
    "PDSG750VRS-4C5",
    "PDS400S",
    "PDSF375S-DP",
    "PDS185S-6C2",
    "SAS15RD6E",
    "DGM150BMK",
    "SDG100S",
    "SDG150S",
    "CPD30",
    "CPD25",
    "H625",
    "H735",
    "AR65J",
    "AR65JE-LI",
    "A30JE",
    "SS1932E",

    "S4650EII",
    "X-SOLAR",
    "S2632EIILI",
]


def find_matches(container, tokens: List[str]) -> Dict[str, Dict]:
    """
    Returns dict keyed by (id|categoria) with the matching item payload.
    """
    matches: Dict[str, Dict] = {}
    for t in tokens:
        t_up = t.upper()
        query = """
            SELECT c.id, c.modelo, c.categoria
            FROM c
            WHERE CONTAINS(UPPER(c.modelo), @t)
        """
        params = [{"name": "@t", "value": t_up}]
        items = list(
            container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

        items_sorted = sorted(items, key=lambda x: (x.get("categoria", ""), x.get("modelo", "")))
        print(f"Token '{t}' -> matches: {len(items_sorted)}")
        for it in items_sorted[:20]:
            print(f"  - categoria={it.get('categoria')} id={it.get('id')} modelo={it.get('modelo')}")
        if len(items_sorted) > 20:
            print("  ...")

        for it in items:
            key = f"{it.get('id')}|{it.get('categoria')}"
            matches[key] = it

    return matches


def delete_matches(container, matches: Dict[str, Dict]) -> Tuple[int, int]:
    deleted = 0
    not_found = 0

    for it in matches.values():
        item_id = it.get("id")
        partition_key = it.get("categoria")
        if not item_id or not partition_key:
            continue

        try:
            container.delete_item(item=item_id, partition_key=partition_key)
            deleted += 1
            print(f"✅ Deleted: categoria={partition_key} id={item_id} modelo={it.get('modelo')}")
        except exceptions.CosmosResourceNotFoundError:
            not_found += 1
            print(f"⚠️ Not found: categoria={partition_key} id={item_id}")

    return deleted, not_found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete items. Without this flag, script only prints matches.",
    )
    args = parser.parse_args()

    cosmos_connection_string = os.environ.get("COSMOS_CONNECTION_STRING")
    cosmos_db_name = os.environ.get("COSMOS_DB_NAME")
    if not cosmos_connection_string or not cosmos_db_name:
        raise SystemExit("Missing COSMOS_CONNECTION_STRING and/or COSMOS_DB_NAME in env/.env")

    client = CosmosClient.from_connection_string(cosmos_connection_string)
    database = client.get_database_client(cosmos_db_name)
    container = database.get_container_client("machinery_inventory")

    print(f"Connected to Cosmos DB database='{cosmos_db_name}' container='machinery_inventory'")
    matches = find_matches(container, TOKENS_TO_DELETE)
    print(f"\nTotal unique matches to delete: {len(matches)}")

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to actually delete.")
        return

    deleted, not_found = delete_matches(container, matches)
    print(f"\n--- Summary ---\nDeleted: {deleted}\nNot found: {not_found}\n")


if __name__ == "__main__":
    main()


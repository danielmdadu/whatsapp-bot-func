"""
Pricing Service

Fetches prices from SQL Server database for machinery products.
Only supports machines that have mappings in model_code_mapping.py
"""

import os
import logging
from typing import Dict, Optional, List
from functools import lru_cache
import re

try:
    import pyodbc
    logging.info("[PRICING_DEBUG] pyodbc imported successfully")
except ImportError:
    pyodbc = None
    logging.error("[PRICING_DEBUG] pyodbc NOT available - ImportError. Pricing service will return None for ALL queries. "
                  "This likely means the ODBC Driver is not installed in the Azure Function environment.")

from model_code_mapping import get_sql_code, has_price_mapping, fuzzy_get_sql_code, fuzzy_has_price_mapping


class PricingService:
    """
    Service for fetching machinery prices from SQL Server.
    
    Only returns prices for machines that have mappings between
    local inventory and SQL database.
    """
    
    def __init__(self):
        """Initialize the pricing service with SQL connection details."""
        self._connection_string = None
        self._price_cache: Dict[str, Optional[dict]] = {}
        self._cache_loaded = False
        
        logging.info("[PRICING_DEBUG] Initializing PricingService...")
        logging.info(f"[PRICING_DEBUG] pyodbc available: {pyodbc is not None}")
        
        # Build connection string from environment variables
        server = os.environ.get('PRICES_SQL_SERVER')
        database = os.environ.get('PRICES_SQL_DATABASE')
        username = os.environ.get('PRICES_SQL_USERNAME')
        password = os.environ.get('PRICES_SQL_PASSWORD')
        
        # Log presence of each env var (without revealing values)
        logging.info(f"[PRICING_DEBUG] Env vars - PRICES_SQL_SERVER: {'SET' if server else 'MISSING'}, "
                     f"PRICES_SQL_DATABASE: {'SET' if database else 'MISSING'}, "
                     f"PRICES_SQL_USERNAME: {'SET' if username else 'MISSING'}, "
                     f"PRICES_SQL_PASSWORD: {'SET' if password else 'MISSING'}")
        
        if all([server, database, username, password]):
            self._connection_string = (
                f"Driver={{ODBC Driver 18 for SQL Server}};"
                f"Server={server};"
                f"Database={database};"
                f"Uid={username};"
                f"Pwd={password};"
                f"Encrypt=yes;"
                f"TrustServerCertificate=yes;"
            )
            logging.info(f"[PRICING_DEBUG] Connection string built successfully. Server: {server}, Database: {database}")
        else:
            logging.error("[PRICING_DEBUG] SQL Server environment variables NOT fully configured. Pricing DISABLED.")

    def _normalize_code(self, code: str) -> str:
        """Normalize product codes for stable cache lookup."""
        if code is None:
            return ""
        return str(code).strip().upper()

    def _sanitize_code(self, code: str) -> str:
        """Sanitize codes removing non-alphanumeric chars for tolerant matching."""
        normalized = self._normalize_code(code)
        return re.sub(r"[^A-Z0-9]", "", normalized)
    
    def _load_all_prices(self) -> None:
        """
        Load all prices from SQL Server into cache.
        Called once on first price request.
        """
        if self._cache_loaded:
            logging.info("[PRICING_DEBUG] _load_all_prices: cache already loaded, skipping.")
            return
        if not self._connection_string:
            logging.error("[PRICING_DEBUG] _load_all_prices: NO connection string available. Cannot load prices.")
            return
        if not pyodbc:
            logging.error("[PRICING_DEBUG] _load_all_prices: pyodbc is NOT available. Cannot load prices.")
            return
            
        try:
            logging.info("[PRICING_DEBUG] _load_all_prices: Attempting SQL Server connection...")
            conn = pyodbc.connect(self._connection_string, timeout=30)
            logging.info("[PRICING_DEBUG] _load_all_prices: SQL Server connection SUCCESSFUL")
            cursor = conn.cursor()
            
            # Fetch all products with their prices
            query = """
                SELECT CODIGO, fixed_price, currency_id 
                FROM [dbo].[inventario_odoo_chatbot]
                WHERE fixed_price IS NOT NULL
            """
            logging.info("[PRICING_DEBUG] _load_all_prices: Executing query...")
            cursor.execute(query)
            
            for row in cursor.fetchall():
                codigo = row[0]
                price = row[1]
                currency = row[2] or "USD"
                
                if codigo and price:
                    normalized_code = self._normalize_code(codigo)
                    self._price_cache[normalized_code] = {
                        "price": float(price),
                        "currency": currency
                    }
            
            conn.close()
            self._cache_loaded = True
            logging.info(f"[PRICING_DEBUG] _load_all_prices: SUCCESS - Loaded {len(self._price_cache)} prices from SQL Server")
            
            # Log a sample of loaded codes for verification
            sample_codes = list(self._price_cache.keys())[:5]
            logging.info(f"[PRICING_DEBUG] _load_all_prices: Sample codes in cache: {sample_codes}")
            
        except Exception as e:
            logging.error(f"[PRICING_DEBUG] _load_all_prices: FAILED to load prices from SQL Server: {type(e).__name__}: {e}")
            import traceback
            logging.error(f"[PRICING_DEBUG] _load_all_prices: Traceback: {traceback.format_exc()}")
            self._cache_loaded = True  # Mark as loaded to avoid repeated failures
    
    def _lookup_in_cache(self, sql_code: str) -> Optional[dict]:
        """Lookup helper with exact+sanitized matching against cache."""
        normalized_sql_code = self._normalize_code(sql_code)
        result = self._price_cache.get(normalized_sql_code)
        if result:
            return result

        target_sanitized = self._sanitize_code(normalized_sql_code)
        for cached_code, cached_price in self._price_cache.items():
            if self._sanitize_code(cached_code) == target_sanitized:
                logging.info(
                    f"[PRICING_DEBUG] get_price: SANITIZED fallback match "
                    f"'{normalized_sql_code}' -> cached '{cached_code}'"
                )
                return cached_price
        return None

    def _extract_model_tokens(self, local_model: str, sql_code: str) -> List[str]:
        """
        Build robust search tokens for SQL fallback lookup.
        Prioritizes model-like fragments that include digits (e.g. DGW400DMK).
        """
        raw_parts = re.split(r"[^A-Za-z0-9]+", local_model or "")
        tokens = []
        for part in raw_parts:
            p = part.strip().upper()
            if len(p) >= 4:
                tokens.append(p)

        code_token = self._sanitize_code(sql_code)
        if code_token:
            tokens.append(code_token)

        # Prioritize tokens with numbers, then by length desc.
        tokens = sorted(set(tokens), key=lambda t: (not any(ch.isdigit() for ch in t), -len(t)))
        return tokens

    def _query_price_by_model_pattern(self, local_model: str, sql_code: str) -> Optional[dict]:
        """
        Last-resort SQL lookup by pattern when mapped code was not found in cache.
        Uses SQL database directly (not local files) to find the closest priced row.
        """
        if not self._connection_string or not pyodbc:
            return None

        try:
            conn = pyodbc.connect(self._connection_string, timeout=30)
            cursor = conn.cursor()

            target_code = self._sanitize_code(sql_code)
            # 1) Strict normalized-code lookup first.
            strict_query = """
                SELECT TOP 1 CODIGO, fixed_price, currency_id
                FROM [dbo].[inventario_odoo_chatbot]
                WHERE fixed_price IS NOT NULL
                  AND UPPER(REPLACE(REPLACE(REPLACE(CODIGO, '-', ''), ' ', ''), '_', '')) = ?
            """
            cursor.execute(strict_query, (target_code,))
            strict_row = cursor.fetchone()
            if strict_row:
                codigo, price, currency = strict_row[0], strict_row[1], strict_row[2] or "USD"
                normalized_code = self._normalize_code(codigo)
                result = {"price": float(price), "currency": currency}
                self._price_cache[normalized_code] = result
                logging.info(
                    f"[PRICING_DEBUG] get_price: SQL strict normalized fallback matched "
                    f"local_model='{local_model}' target='{target_code}' codigo='{normalized_code}' "
                    f"price=${result['price']:,.2f} {result['currency']}"
                )
                conn.close()
                return result

            tokens = self._extract_model_tokens(local_model, sql_code)
            if not tokens:
                conn.close()
                return None

            best_candidate = None
            best_score = float("-inf")

            def _common_prefix_len(a: str, b: str) -> int:
                n = min(len(a), len(b))
                i = 0
                while i < n and a[i] == b[i]:
                    i += 1
                return i

            for token in tokens:
                like_token = f"%{token}%"
                query = """
                    SELECT TOP 10 CODIGO, fixed_price, currency_id
                    FROM [dbo].[inventario_odoo_chatbot]
                    WHERE fixed_price IS NOT NULL
                      AND (
                        UPPER(CODIGO) LIKE ?
                        OR UPPER(PRODUCTO) LIKE ?
                      )
                """
                cursor.execute(query, (like_token, like_token))
                rows = cursor.fetchall()
                if not rows:
                    continue

                for row in rows:
                    codigo, price, currency = row[0], row[1], row[2] or "USD"
                    if not codigo or price is None:
                        continue

                    candidate_code = self._sanitize_code(codigo)
                    prefix_len = _common_prefix_len(target_code, candidate_code)
                    score = 0
                    if candidate_code == target_code:
                        score += 1000
                    if candidate_code.startswith(target_code) or target_code.startswith(candidate_code):
                        score += 150
                    if target_code in candidate_code or candidate_code in target_code:
                        score += 80
                    score += (prefix_len * 3)
                    score -= abs(len(candidate_code) - len(target_code))

                    if score > best_score:
                        best_score = score
                        best_candidate = {
                            "codigo": self._normalize_code(codigo),
                            "price": float(price),
                            "currency": currency,
                            "token": token
                        }

            min_safe_score = 100
            if best_candidate and best_score >= min_safe_score:
                result = {
                    "price": best_candidate["price"],
                    "currency": best_candidate["currency"]
                }
                self._price_cache[best_candidate["codigo"]] = result
                logging.info(
                    f"[PRICING_DEBUG] get_price: SQL pattern fallback matched "
                    f"local_model='{local_model}' target='{target_code}' "
                    f"codigo='{best_candidate['codigo']}' token='{best_candidate['token']}' score={best_score} "
                    f"price=${result['price']:,.2f} {result['currency']}"
                )
                conn.close()
                return result
            elif best_candidate:
                logging.warning(
                    f"[PRICING_DEBUG] get_price: Pattern fallback candidate rejected "
                    f"(score={best_score} < {min_safe_score}). "
                    f"target='{target_code}', candidate='{best_candidate['codigo']}'"
                )

            conn.close()
        except Exception as e:
            logging.error(f"[PRICING_DEBUG] _query_price_by_model_pattern failed: {type(e).__name__}: {e}")

        return None

    def get_price(self, local_model: str) -> Optional[dict]:
        """
        Get the price for a machine by its local model name.
        
        Args:
            local_model: The model name from inventory_data.py (e.g., "AIRMAN SAS75RD6E")
            
        Returns:
            dict with {"price": float, "currency": str} if found, None otherwise
        """
        logging.info(f"[PRICING_DEBUG] get_price called for model: '{local_model}'")
        
        # Check if this model has an exact mapping
        if has_price_mapping(local_model):
            sql_code = get_sql_code(local_model)
            logging.info(f"[PRICING_DEBUG] get_price: EXACT match for '{local_model}' → SQL code '{sql_code}'")
        else:
            # Try fuzzy matching (handles partial names like 'DGM250MK-D' → 'Shindaiwa DGM250MK-D')
            logging.info(f"[PRICING_DEBUG] get_price: No exact match for '{local_model}', trying fuzzy match...")
            full_name, sql_code = fuzzy_get_sql_code(local_model)
            if full_name:
                logging.info(f"[PRICING_DEBUG] get_price: FUZZY match '{local_model}' → '{full_name}' → SQL code '{sql_code}'")
            else:
                logging.info(f"[PRICING_DEBUG] get_price: model '{local_model}' has NO mapping (exact nor fuzzy)")
                return None
        
        if not sql_code:
            logging.info(f"[PRICING_DEBUG] get_price: no SQL code resolved for '{local_model}'")
            return None
        
        # Ensure prices are loaded
        self._load_all_prices()
        
        result = self._lookup_in_cache(sql_code)

        # If cache was already loaded and lookup failed, force a one-time refresh and retry.
        if not result and self._cache_loaded:
            logging.warning(
                f"[PRICING_DEBUG] get_price: Miss for code={sql_code}. "
                "Refreshing cache and retrying once."
            )
            self.refresh_cache()
            result = self._lookup_in_cache(sql_code)

        if result:
            logging.info(f"[PRICING_DEBUG] get_price: FOUND price for '{local_model}' (code={sql_code}): ${result['price']:,.2f} {result['currency']}")
        else:
            # Final fallback: query SQL directly by model pattern.
            result = self._query_price_by_model_pattern(local_model, sql_code)
            if result:
                return result
            logging.warning(f"[PRICING_DEBUG] get_price: NO price found in cache for '{local_model}' (code={sql_code}). "
                          f"Cache has {len(self._price_cache)} entries. Cache loaded: {self._cache_loaded}")
        return result
    
    def get_prices_batch(self, local_models: List[str]) -> Dict[str, Optional[dict]]:
        """
        Get prices for multiple machines at once.
        
        Args:
            local_models: List of model names from inventory_data.py
            
        Returns:
            Dict mapping model name to price info (or None if not found)
        """
        # Ensure prices are loaded
        self._load_all_prices()
        
        results = {}
        for model in local_models:
            results[model] = self.get_price(model)
        
        return results
    
    def is_available(self) -> bool:
        """Check if the pricing service is properly configured."""
        return self._connection_string is not None and pyodbc is not None
    
    def refresh_cache(self) -> None:
        """Clear and reload the price cache."""
        self._price_cache.clear()
        self._cache_loaded = False
        self._load_all_prices()


# Singleton instance for use across the application
_pricing_service_instance: Optional[PricingService] = None


def get_pricing_service() -> PricingService:
    """Get the singleton pricing service instance."""
    global _pricing_service_instance
    if _pricing_service_instance is None:
        _pricing_service_instance = PricingService()
    return _pricing_service_instance

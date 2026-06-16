"""
Blob Storage Service

Downloads technical data sheets (fichas técnicas) from Azure Blob Storage.
Used to send PDF datasheets to WhatsApp users alongside quotations.
"""

import os
import logging
from typing import Optional, Tuple

# ============================================================================
# FICHA TÉCNICA MAPPING
# Maps inventory model names to blob filenames in the "pdfs" container
# ============================================================================

FICHA_TECNICA_MAP = {
    # Soldadoras
    "Shindaiwa DGW340DM": "Ficha-Tecnica-Soldadora-Shindaiwa-DGW30DM-1.pdf",
    "Shindaiwa DGW400DMK": "Ficha-Tecnica-Soldadora-Shindaiwa-DGW400DMK-1.pdf",
    "Shindaiwa DGW500DM": "Ficha-Tecnica-Soldadora-Shindaiwa-DGW500DM-2-1.pdf",
    "Shindaiwa EGW185MS": "Ficha-tecnica-Soldadora-EGW185MS.pdf",
}

CONTAINER_NAME = "pdfs"


class BlobStorageService:
    """Service for downloading fichas técnicas from Azure Blob Storage."""

    def __init__(self):
        self._container_client = None
        self._cache = {}  # In-memory cache: blob_name -> bytes
        self._initialize()

    def _initialize(self):
        """Initialize the Blob Storage client."""
        try:
            connection_string = os.environ.get("BLOB_STORAGE_CONNECTION_STRING")
            if not connection_string:
                logging.warning("[BLOB] BLOB_STORAGE_CONNECTION_STRING not set. Ficha técnica downloads disabled.")
                return

            from azure.storage.blob import BlobServiceClient
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            self._container_client = blob_service_client.get_container_client(CONTAINER_NAME)
            logging.info(f"[BLOB] Connected to container '{CONTAINER_NAME}'")
        except Exception as e:
            logging.error(f"[BLOB] Error initializing Blob Storage: {e}")

    def is_available(self) -> bool:
        """Check if the Blob Storage service is available."""
        return self._container_client is not None

    def has_ficha_tecnica(self, modelo: str) -> bool:
        """Check if a ficha técnica exists for the given model."""
        return modelo in FICHA_TECNICA_MAP

    def get_ficha_tecnica(self, modelo: str) -> Optional[Tuple[bytes, str]]:
        """
        Download the ficha técnica PDF for a given model.

        Args:
            modelo: The model name from inventory (e.g., "Shindaiwa DGW500DM")

        Returns:
            Tuple of (pdf_bytes, filename) if found, None otherwise
        """
        blob_name = FICHA_TECNICA_MAP.get(modelo)
        if not blob_name:
            logging.info(f"[BLOB] No ficha técnica mapped for model: {modelo}")
            return None

        if not self.is_available():
            logging.warning(f"[BLOB] Blob Storage not available. Cannot download ficha for {modelo}")
            return None

        # Check cache
        if blob_name in self._cache:
            logging.info(f"[BLOB] Returning cached ficha técnica for {modelo}")
            return self._cache[blob_name], blob_name

        try:
            blob_client = self._container_client.get_blob_client(blob_name)
            download_stream = blob_client.download_blob()
            pdf_bytes = download_stream.readall()

            # Cache it
            self._cache[blob_name] = pdf_bytes

            logging.info(f"[BLOB] Downloaded ficha técnica for {modelo}: {len(pdf_bytes)} bytes")
            return pdf_bytes, blob_name

        except Exception as e:
            logging.error(f"[BLOB] Error downloading ficha técnica for {modelo}: {e}")
            return None


# ============================================================================
# SINGLETON
# ============================================================================

_blob_service_instance: Optional[BlobStorageService] = None


def get_blob_storage_service() -> BlobStorageService:
    """Get the singleton BlobStorageService instance."""
    global _blob_service_instance
    if _blob_service_instance is None:
        _blob_service_instance = BlobStorageService()
    return _blob_service_instance

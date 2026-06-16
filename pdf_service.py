"""
PDF Quotation Service

Generates professional PDF quotations for machinery using fpdf2.
Used when the conversation completes and the user wants a quote.
"""

import os
import logging
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, Optional
from fpdf import FPDF


# ============================================================================
# ALPHA C COMPANY DATA (HARDCODED)
# ============================================================================

COMPANY_INFO = {
    "beneficiario": "ALPHA C SA DE CV",
    "rfc": "ARE140322KV4",
    "banorte_pesos": {
        "label": "BANORTE PESOS 🇲🇽",
        "cuenta": "0243171130",
        "clabe": "072 320 00243171130 2",
    },
    "banorte_usd": {
        "label": "BANORTE DÓLARES USD",
        "cuenta": "0227890927",
        "clabe": "072 320 00227890927 0",
    },
    "emails_comprobantes": [
        "contabilidad.general@acmaq.com.mx",
        "cxc@acmaq.com.mx",
    ],
}

CONDITIONS = [
    "Precios en USD.",
    "En caso de pagar en pesos, se tomará el TC de venta de DOF.",
    "EXW Bodega (según cotización).",
    "Flete por cuenta y riesgo del comprador.",
]

IVA_RATE = 0.16

# ============================================================================
# COLOR PALETTE
# ============================================================================

# Light gray for section headers
COLOR_HEADER_BG = (220, 220, 220)     # light gray
COLOR_TABLE_HEADER_BG = (200, 200, 200)  # slightly darker gray for table header
COLOR_WHITE = (255, 255, 255)
COLOR_LIGHT_GRAY = (240, 240, 240)
COLOR_GRAY = (180, 180, 180)
COLOR_BLACK = (0, 0, 0)
COLOR_TEXT = (0, 0, 0)
COLOR_TOTAL_BG = (50, 50, 50)          # dark gray for total row


class QuotationPDF(FPDF):
    """Custom FPDF subclass for quotation layout."""

    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="letter")
        self.set_auto_page_break(auto=True, margin=20)
        # Register fonts
        self.add_page()

    # ------------------------------------------------------------------
    # Helper: draw a colored rectangle (section header band)
    # ------------------------------------------------------------------
    def _section_header(self, label: str, y: float = None):
        """Draw a light gray band with black text as section header."""
        if y is not None:
            self.set_y(y)
        self.set_fill_color(*COLOR_HEADER_BG)
        self.set_text_color(*COLOR_BLACK)
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 8, f"  {label}", border=0, ln=True, fill=True)
        self.set_text_color(*COLOR_TEXT)
        self.ln(2)

    def _label_value(self, label: str, value: str, label_w: float = 40):
        """Draw a label: value pair."""
        self.set_font("Helvetica", "B", 9)
        self.cell(label_w, 5, label, border=0)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, value or "-", border=0, ln=True)

    def _label_value_inline(self, label: str, value: str):
        """Label and value on same line, no fixed width."""
        self.set_font("Helvetica", "B", 9)
        lw = self.get_string_width(label) + 2
        self.cell(lw, 5, label, border=0)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, value or "-", border=0, ln=True)


class QuotationPDFGenerator:
    """
    Generates a PDF quotation from conversation state and price data.

    Usage:
        generator = QuotationPDFGenerator()
        pdf_bytes = generator.generate(state, price_info)
    """

    def __init__(self):
        self.logo_path = self._find_logo()

    def _find_logo(self) -> Optional[str]:
        """Find a logo image file in the assets directory."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(base_dir, "assets")
        
        if not os.path.isdir(assets_dir):
            logging.warning("[PDF] assets/ directory not found.")
            return None
        
        # Scan for any image file in assets/
        image_extensions = (".png", ".jpg", ".jpeg")
        for filename in os.listdir(assets_dir):
            if filename.lower().endswith(image_extensions):
                path = os.path.join(assets_dir, filename)
                logging.info(f"[PDF] Logo found at: {path}")
                return path
        
        logging.warning("[PDF] No logo image found in assets/. PDF will render without logo.")
        return None

    # ------------------------------------------------------------------
    # PUBLIC: Generate the full PDF
    # ------------------------------------------------------------------
    def generate(self, state: Dict[str, Any], price_info: Optional[Dict[str, Any]] = None) -> bytes:
        """
        Generate a PDF quotation.

        Args:
            state: ConversationState dict with all lead and machine data.
            price_info: {"price": float, "currency": str} or None.

        Returns:
            PDF content as bytes.
        """
        try:
            logging.info(f"[PDF] Generating quotation PDF. Machine: {state.get('maquina_seleccionada')}, Price: {price_info}")
            pdf = QuotationPDF()

            folio = self._generate_folio()
            fecha = datetime.now().strftime("%d/%m/%Y")

            self._draw_header(pdf)
            self._draw_reference(pdf, folio, fecha)
            self._draw_client_info(pdf, state)
            self._draw_products_table(pdf, state, price_info)
            self._draw_totals(pdf, price_info)
            self._draw_comments(pdf)
            self._draw_conditions(pdf)

            # Output to bytes
            result = bytes(pdf.output())
            logging.info(f"[PDF] PDF generated successfully. Folio: {folio}, Size: {len(result)} bytes")
            return result
        except Exception as e:
            logging.error(f"[PDF] Error generating PDF: {e}")
            import traceback
            logging.error(f"[PDF] PDF generation traceback: {traceback.format_exc()}")
            raise

    # ------------------------------------------------------------------
    # FOLIO GENERATION
    # ------------------------------------------------------------------
    def _generate_folio(self) -> str:
        """Generate a quotation reference number."""
        now = datetime.now()
        return f"COT-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"

    # ------------------------------------------------------------------
    # SECTION: Header (logo)
    # ------------------------------------------------------------------
    def _draw_header(self, pdf: QuotationPDF):
        """Draw the header with logo on the top-right."""
        if self.logo_path:
            try:
                # Place logo top-right, max 50mm wide, maintain aspect ratio
                pdf.image(self.logo_path, x=145, y=10, w=55)
            except Exception as e:
                logging.warning(f"[PDF] Error loading logo: {e}")

        # Move past the header area
        pdf.set_y(35)

    # ------------------------------------------------------------------
    # SECTION: Reference & Date
    # ------------------------------------------------------------------
    def _draw_reference(self, pdf: QuotationPDF, folio: str, fecha: str):
        """Draw reference number and date."""
        pdf._section_header("COTIZACIÓN")

        x_start = pdf.get_x()

        pdf._label_value("Referencia:", folio, label_w=30)
        pdf._label_value("Fecha:", fecha, label_w=30)
        pdf.ln(3)

    # ------------------------------------------------------------------
    # SECTION: Client Information (2 columns)
    # ------------------------------------------------------------------
    def _draw_client_info(self, pdf: QuotationPDF, state: Dict[str, Any]):
        """Draw client info in two columns."""
        pdf._section_header("INFORMACIÓN DEL CLIENTE")

        y_start = pdf.get_y()
        col_width = 95  # mm

        # --- Left column: Company info ---
        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Empresa:", border=0)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col_width - 30, 5, state.get("nombre_empresa") or "-", border=0, ln=True)

        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Estado:", border=0)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col_width - 30, 5, state.get("lugar_requerimiento") or "-", border=0, ln=True)

        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Uso:", border=0)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col_width - 30, 5, state.get("tipo_cliente") or "-", border=0, ln=True)

        y_left_end = pdf.get_y()

        # --- Right column: Contact info ---
        pdf.set_xy(10 + col_width, y_start)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Contacto:", border=0)
        pdf.set_font("Helvetica", "", 9)
        nombre_completo = state.get('nombre', '') or ''
        # nombre already contains full name (e.g., "Carlos Ramírez") from slot-filling
        # Only append apellido if nombre doesn't already contain it
        apellido = state.get('apellido', '') or ''
        if apellido and apellido not in nombre_completo:
            nombre_completo = f"{nombre_completo} {apellido}".strip()
        pdf.cell(col_width - 30, 5, nombre_completo or "-", border=0, ln=True)

        pdf.set_x(10 + col_width)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Correo:", border=0)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col_width - 30, 5, state.get("correo") or "-", border=0, ln=True)

        pdf.set_x(10 + col_width)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Teléfono:", border=0)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(col_width - 30, 5, state.get("telefono") or "-", border=0, ln=True)

        # Move below whichever column is taller
        y_right_end = pdf.get_y()
        pdf.set_y(max(y_left_end, y_right_end) + 5)

    # ------------------------------------------------------------------
    # SECTION: Products Table
    # ------------------------------------------------------------------
    def _draw_products_table(self, pdf: QuotationPDF, state: Dict[str, Any], price_info: Optional[Dict[str, Any]]):
        """Draw the products/services table."""
        pdf._section_header("PRODUCTOS Y SERVICIOS")

        # Table header
        pdf.set_fill_color(*COLOR_TABLE_HEADER_BG)
        pdf.set_text_color(*COLOR_BLACK)
        pdf.set_font("Helvetica", "B", 9)

        col_desc_w = 110
        col_qty_w = 30
        col_price_w = 50

        pdf.cell(col_desc_w, 8, "  Descripción", border=1, fill=True)
        pdf.cell(col_qty_w, 8, "Cantidad", border=1, fill=True, align="C")
        pdf.cell(col_price_w, 8, "Precio Unitario", border=1, fill=True, align="C")
        pdf.ln()

        # Table row
        pdf.set_text_color(*COLOR_TEXT)
        pdf.set_font("Helvetica", "", 9)

        maquina = state.get("maquina_seleccionada", "-")
        tipo = state.get("tipo_maquinaria", "")

        # Build description with machine type + model
        if tipo:
            description = f"{tipo.replace('_', ' ').title()} - {maquina}"
        else:
            description = maquina

        # Price
        if price_info and price_info.get("price"):
            precio = price_info["price"]
            moneda = price_info.get("currency", "USD")
            price_str = f"${precio:,.2f} {moneda}"
        else:
            price_str = "A consultar"

        # Draw row with light gray background
        pdf.set_fill_color(*COLOR_LIGHT_GRAY)
        pdf.cell(col_desc_w, 10, f"  {description}", border=1, fill=True)
        pdf.cell(col_qty_w, 10, "1", border=1, fill=True, align="C")
        pdf.cell(col_price_w, 10, price_str, border=1, fill=True, align="C")
        pdf.ln(15)

    # ------------------------------------------------------------------
    # SECTION: Totals
    # ------------------------------------------------------------------
    def _draw_totals(self, pdf: QuotationPDF, price_info: Optional[Dict[str, Any]]):
        """Draw the financial summary (subtotal, IVA, total)."""
        if not price_info or not price_info.get("price"):
            # No price available - skip totals
            return

        precio = price_info["price"]
        moneda = price_info.get("currency", "USD")
        iva = precio * IVA_RATE
        total = precio + iva

        # Right-aligned totals box
        x_label = 120
        x_value = 160
        value_w = 40

        pdf.set_font("Helvetica", "", 10)

        # Subtotal
        pdf.set_x(x_label)
        pdf.cell(40, 7, "Subtotal:", border=0, align="R")
        pdf.cell(value_w, 7, f"${precio:,.2f} {moneda}", border="B", align="R")
        pdf.ln()

        # IVA
        pdf.set_x(x_label)
        pdf.cell(40, 7, f"IVA ({int(IVA_RATE * 100)}%):", border=0, align="R")
        pdf.cell(value_w, 7, f"${iva:,.2f} {moneda}", border="B", align="R")
        pdf.ln()

        # Total
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_x(x_label)
        pdf.cell(40, 9, "TOTAL:", border=0, align="R")
        pdf.set_fill_color(*COLOR_TOTAL_BG)
        pdf.set_text_color(*COLOR_WHITE)
        pdf.cell(value_w, 9, f"${total:,.2f} {moneda}", border=1, fill=True, align="R")
        pdf.set_text_color(*COLOR_TEXT)
        pdf.ln(15)

    # ------------------------------------------------------------------
    # SECTION: Comments (bank info, fiscal data)
    # ------------------------------------------------------------------
    def _draw_comments(self, pdf: QuotationPDF):
        """Draw the comments section with bank/fiscal info."""
        pdf._section_header("INFORMACIÓN DE PAGO")

        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, f"Beneficiario: {COMPANY_INFO['beneficiario']}", border=0, ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"RFC: {COMPANY_INFO['rfc']}", border=0, ln=True)
        pdf.ln(3)

        # Bank info - PESOS
        pesos = COMPANY_INFO["banorte_pesos"]
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "BANORTE PESOS (MXN):", border=0, ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"   Cuenta: {pesos['cuenta']}", border=0, ln=True)
        pdf.cell(0, 5, f"   Clave interbancaria: {pesos['clabe']}", border=0, ln=True)
        pdf.ln(2)

        # Bank info - USD
        usd = COMPANY_INFO["banorte_usd"]
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "BANORTE DÓLARES (USD):", border=0, ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"   Cuenta: {usd['cuenta']}", border=0, ln=True)
        pdf.cell(0, 5, f"   Clave interbancaria: {usd['clabe']}", border=0, ln=True)
        pdf.ln(3)

        # Payment proof emails
        emails = ", ".join(COMPANY_INFO["emails_comprobantes"])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "Enviar comprobante de pago a:", border=0, ln=True)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"   {emails}", border=0, ln=True)
        pdf.ln(5)

    # ------------------------------------------------------------------
    # SECTION: Conditions
    # ------------------------------------------------------------------
    def _draw_conditions(self, pdf: QuotationPDF):
        """Draw the commercial conditions."""
        pdf._section_header("CONDICIONES DE COMPRA")

        pdf.set_font("Helvetica", "", 9)
        for condition in CONDITIONS:
            pdf.cell(5, 5, "", border=0)  # indent
            pdf.cell(5, 5, "-", border=0)  # bullet
            pdf.cell(0, 5, f" {condition}", border=0, ln=True)

        pdf.ln(5)


# ============================================================================
# MODULE-LEVEL CONVENIENCE
# ============================================================================

_generator_instance: Optional[QuotationPDFGenerator] = None


def get_pdf_generator() -> QuotationPDFGenerator:
    """Get the singleton PDF generator instance."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = QuotationPDFGenerator()
    return _generator_instance

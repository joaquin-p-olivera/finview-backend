"""Parses a bank statement PDF with Claude.

The PDF is sent as-is (a document block): Claude reads the real table layout,
so pesos and dollars stay in their own columns, which the image + Groq
approach used to mix up. Same prompt and model as the Apps Script that imports
the Itaú statement every month, adapted to each user's categories. The PDF is
only held in memory for the request; nothing is written to disk.
"""

import base64
import io
import json
import logging

import anthropic
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from ..config import get_settings

logger = logging.getLogger(__name__)

FALLBACK_CATEGORY = "Otros"

# Categories (and the guidance the model gets for each) used by the Apps Script.
# A user without categories gets these; a user with categories of the same name
# gets the same guidance.
DEFAULT_CATEGORIES = [
    ("Supermercado", "Compras en supermercados como TATA, Disco, Devoto, Geant, etc"),
    ("Pedidos Ya", "Pedidos Ya delivery"),
    ("Combustible", "Combustible: Ancap, DISA"),
    ("UBER", "Viajes en UBER"),
    ("CORPORACION VIAL", "Corporacion Vial (Pago de peajes)"),
    ("Servicios", "Servicios del hogar: luz, agua, internet, teléfono"),
    ("Compras", "Compras generales de productos, ropa, electrónica"),
    ("Suscripciones", "Suscripciones como Spotify, Netflix, Google, Playstation, Claude, etc"),
    (FALLBACK_CATEGORY, "Cualquier transacción que no encaje claramente en otra categoría"),
]
_DEFAULT_GUIDANCE = {name.lower(): description for name, description in DEFAULT_CATEGORIES}

SUMMARY_FIELDS = [
    "previous_balance_uyu",
    "previous_balance_usd",
    "payments_uyu",
    "payments_usd",
    "insurance_uyu",
    "insurance_usd",
    "interest_uyu",
    "interest_usd",
    "fees_uyu",
    "fees_usd",
    "statement_total_uyu",
    "statement_total_usd",
]

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULLABLE_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_NULLABLE_INTEGER = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


class StatementParseError(Exception):
    """The statement could not be parsed; the message is shown to the user."""


class PdfPasswordError(StatementParseError):
    """The PDF is encrypted and the password is missing or wrong."""


def is_configured() -> bool:
    return bool(get_settings().ANTHROPIC_API_KEY)


def prompt_categories(user_categories: list[str]) -> list[str]:
    """Category names the model can choose from: the user's, or the defaults if
    they have none. Always includes the fallback category."""
    names = list(user_categories) or [name for name, _ in DEFAULT_CATEGORIES]
    if not any(name.lower() == FALLBACK_CATEGORY.lower() for name in names):
        names.append(FALLBACK_CATEGORY)
    return names


def decrypt_pdf(pdf_bytes: bytes, password: str | None) -> bytes:
    """Returns the PDF unencrypted. Claude can't read password-protected PDFs."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except PdfReadError as exc:
        raise StatementParseError("El archivo no parece ser un PDF válido.") from exc
    if not reader.is_encrypted:
        return pdf_bytes
    if not password:
        raise PdfPasswordError("El PDF está protegido con contraseña. Ingresala para poder leerlo.")
    if not reader.decrypt(password):
        raise PdfPasswordError("La contraseña del PDF no es correcta.")
    writer = PdfWriter(clone_from=reader)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _build_prompt(category_names: list[str]) -> str:
    categories = "\n".join(
        f"- {name}: {_DEFAULT_GUIDANCE[name.lower()]}" if name.lower() in _DEFAULT_GUIDANCE else f"- {name}"
        for name in category_names
    )
    return (
        "Analyze this bank credit card statement and extract its information.\n\n"
        "IMPORTANT:\n"
        "- This statement may contain TWO monetary columns: UYU and USD.\n"
        "- Determine the currency of EACH transaction from the column where its amount appears.\n"
        "- Never assume that all transactions are in the same currency.\n"
        "- Extract EVERY purchase exactly once.\n"
        "- Preserve negative amounts exactly as they appear.\n"
        "- Do NOT include statement summaries such as previous balance, payments, insurance, interest, "
        "taxes, commissions, total due or account balances as transactions.\n"
        '- Detect installment patterns such as "3/12", "2/5", "Cuota 3 de 12", etc.\n'
        "- Infer a reasonable merchant name for each transaction.\n"
        "- Assign each transaction EXACTLY ONE of these categories (use the exact name; "
        "the text after the colon is guidance to help you decide, not something to output):\n"
        f"{categories}\n"
        f'If none fits well, use "{FALLBACK_CATEGORY}". Do not invent new category names.\n\n'
        "Also extract the statement summary values when present (null when not printed).\n"
        "Dates go in YYYY-MM-DD format."
    )


def _result_schema(category_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "bank_name": {"type": "string"},
            "card_last4": _NULLABLE_STRING,
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "summary": {
                "type": "object",
                "properties": {field: _NULLABLE_NUMBER for field in SUMMARY_FIELDS},
                "required": SUMMARY_FIELDS,
                "additionalProperties": False,
            },
            "transactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "description": {"type": "string"},
                        "merchant": _NULLABLE_STRING,
                        "amount": {"type": "number"},
                        "currency": {"type": "string", "enum": ["UYU", "USD"]},
                        "installment_num": _NULLABLE_INTEGER,
                        "installment_tot": _NULLABLE_INTEGER,
                        "category": {"type": "string", "enum": category_names},
                    },
                    "required": [
                        "date",
                        "description",
                        "merchant",
                        "amount",
                        "currency",
                        "installment_num",
                        "installment_tot",
                        "category",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["bank_name", "card_last4", "period_start", "period_end", "summary", "transactions"],
        "additionalProperties": False,
    }


def parse_statement(pdf_bytes: bytes, category_names: list[str]) -> dict:
    """Sends the (unencrypted) PDF to Claude and returns the parsed statement:
    bank_name, card_last4, period_start/end, summary and transactions, each
    transaction with a `category` from category_names."""
    if not is_configured():
        raise StatementParseError("Falta configurar ANTHROPIC_API_KEY en el servidor.")

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=settings.STATEMENT_PARSER_MODEL,
            max_tokens=16000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": _build_prompt(category_names)},
                    ],
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _result_schema(category_names)}},
        )
    except anthropic.APIError as exc:
        logger.exception("Statement parse request failed")
        raise StatementParseError("No se pudo contactar a Claude. Probá de nuevo en un rato.") from exc

    if response.stop_reason == "refusal":
        raise StatementParseError("Claude no pudo leer este estado de cuenta.")
    if response.stop_reason == "max_tokens":
        raise StatementParseError("El estado de cuenta es demasiado largo: la respuesta de Claude quedó incompleta.")
    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise StatementParseError("Claude no devolvió una respuesta.")
    return json.loads(text)

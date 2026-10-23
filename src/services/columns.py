"""Column headings recognised in bank statement files (English and the ones Italian banks use).

Lists are in priority order: when a file has several matching columns the first one in the list wins
(e.g. "data registrazione" before "data valuta"). Shared by the file reader (to find the header row)
and the row parser (to find each field).
"""

DATE_COLUMNS = (
    "date", "transaction date", "booking date", "value date",
    "data", "data operazione", "data contabile", "data registrazione", "data movimento", "data valuta",
)
DESCRIPTION_COLUMNS = (
    "description", "details", "memo", "narrative", "transaction details",
    "descrizione", "descrizione operazione", "descrizione movimento", "causale", "dettagli", "dettaglio",
)
# one signed amount column
AMOUNT_COLUMNS = (
    "amount", "value", "transaction amount",
    "importo", "importo (eur)", "importo eur", "importo in eur", "importo €",
)
# separate columns for money going out and coming in
DEBIT_COLUMNS = ("uscite", "dare", "addebiti", "addebito", "debit", "importo dare", "importo uscita")
CREDIT_COLUMNS = ("entrate", "avere", "accrediti", "accredito", "credit", "importo avere", "importo entrata")
CURRENCY_COLUMNS = ("currency", "ccy", "valuta", "divisa")
CATEGORY_COLUMNS = ("category", "type", "transaction type", "categoria")

_DATE_WORDS = ("data", "date")
_DESCRIPTION_WORDS = ("descrizione", "description", "causale", "details", "dettagli", "dettaglio", "memo", "narrative")
_AMOUNT_WORDS = ("importo", "amount", "uscite", "entrate", "dare", "avere", "addebit", "accredit", "debit", "credit")


def header_kinds(cell: object) -> set:
    """Which kinds of heading ('date', 'description', 'amount') a cell looks like. Empty for ordinary data."""
    text = str(cell).strip().lower()
    if not text or len(text) > 40:  # a heading is short; long cells are text of a metadata row
        return set()
    kinds = set()
    if any(w in text for w in _DATE_WORDS) and not any(ch.isdigit() for ch in text):
        kinds.add("date")
    if any(w in text for w in _DESCRIPTION_WORDS):
        kinds.add("description")
    if any(w in text for w in _AMOUNT_WORDS):
        kinds.add("amount")
    return kinds

from xml.sax.saxutils import escape


EXCEL_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r', '\n')


def excel_safe_text(value):
    """Return user-controlled text without allowing spreadsheet formulas."""
    text = '' if value is None else str(value)
    if text.startswith(EXCEL_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def pdf_safe_text(value):
    """Escape user-controlled text before passing it to ReportLab Paragraph."""
    return escape('' if value is None else str(value))

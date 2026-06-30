import pdfplumber


def extract_text_from_pdf(pdf_file) -> str:
    """Extract all text from a PDF file-like object, page by page."""
    all_text = ""

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                all_text += page_text + "\n"

    return all_text
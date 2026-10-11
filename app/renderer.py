"""Pure PDF rendering function using ReportLab."""
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


class RenderError(Exception):
    """Raised when PDF rendering fails for deterministic reasons."""
    pass


def render(recipient: dict, event_name: str) -> bytes:
    """
    Generate a certificate PDF for a recipient.
    
    Pure function: no DB access, no file writes.
    
    Args:
        recipient: Dictionary with at least "name" and "email" keys
        event_name: Name of the event for the certificate
        
    Returns:
        bytes: PDF content
        
    Raises:
        RenderError: If name is empty or doesn't fit even at minimum font size
    """
    name = recipient.get("name", "").strip()
    if not name:
        raise RenderError("Recipient name is empty")
    
    # Create PDF in memory
    buffer = BytesIO()
    page_width, page_height = landscape(A4)
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    
    # Certificate layout - centered content
    center_x = page_width / 2
    
    # Title
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(center_x, page_height - 1.5 * inch, "Certificate of Participation")
    
    # "This is to certify that"
    c.setFont("Helvetica", 18)
    c.drawCentredString(center_x, page_height - 2.5 * inch, "This is to certify that")
    
    # Recipient name with auto-shrink
    # Try font sizes from 48 down to 24; if it still doesn't fit, raise RenderError
    name_y = page_height - 3.5 * inch
    max_width = page_width - 2 * inch  # Leave 1 inch margin on each side
    
    font_size = 48
    min_font_size = 24
    name_drawn = False
    
    while font_size >= min_font_size:
        c.setFont("Helvetica-Bold", font_size)
        text_width = c.stringWidth(name, "Helvetica-Bold", font_size)
        if text_width <= max_width:
            c.drawCentredString(center_x, name_y, name)
            name_drawn = True
            break
        font_size -= 2
    
    if not name_drawn:
        raise RenderError(f"Recipient name too long to fit: {name}")
    
    # Event participation text
    c.setFont("Helvetica", 18)
    c.drawCentredString(center_x, page_height - 4.5 * inch, f"has participated in {event_name}")
    
    # Date
    c.setFont("Helvetica", 14)
    date_str = datetime.utcnow().strftime("%B %d, %Y")
    c.drawCentredString(center_x, page_height - 5.5 * inch, date_str)
    
    # Finalize
    c.showPage()
    c.save()
    
    return buffer.getvalue()

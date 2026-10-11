#!/usr/bin/env python3
"""
Script to generate a sample certificate PDF.
Tests the renderer module independently.
"""
from app.renderer import render, RenderError


def main():
    """Generate sample.pdf in the current directory."""
    recipient = {
        "name": "John Doe",
        "email": "john.doe@example.com"
    }
    event_name = "Python Development Workshop 2024"
    
    try:
        pdf_bytes = render(recipient, event_name)
        
        with open("sample.pdf", "wb") as f:
            f.write(pdf_bytes)
        
        print(f"✓ Generated sample.pdf ({len(pdf_bytes)} bytes)")
        print(f"  Recipient: {recipient['name']}")
        print(f"  Event: {event_name}")
        
    except RenderError as e:
        print(f"✗ Render error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())

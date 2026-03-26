#!/usr/bin/env python3
"""
LaTeX to PDF converter using online compilation service.
No local LaTeX installation required!
"""

import requests
import os
import sys
from pathlib import Path

def compile_latex_online(tex_file: str, output_pdf: str = None) -> bool:
    """
    Compile a LaTeX file to PDF using latexonline.cc API.
    
    Args:
        tex_file: Path to the .tex file
        output_pdf: Output PDF path (optional, defaults to same name as tex file)
    
    Returns:
        True if successful, False otherwise
    """
    tex_path = Path(tex_file)
    
    if not tex_path.exists():
        print(f"Error: File '{tex_file}' not found")
        return False
    
    if output_pdf is None:
        output_pdf = tex_path.with_suffix('.pdf')
    
    print(f"Compiling {tex_file} to PDF...")
    print("Using online LaTeX compilation service (latexonline.cc)...")
    
    # Read the LaTeX file
    with open(tex_path, 'r', encoding='utf-8') as f:
        tex_content = f.read()
    
    # Use latexonline.cc API
    # For XeLaTeX (needed for Hebrew), we use the 'command' parameter
    api_url = "https://latexonline.cc/compile"
    
    # Method 1: Direct file upload
    files = {
        'file': (tex_path.name, tex_content, 'application/x-tex')
    }
    
    params = {
        'command': 'xelatex'  # Use XeLaTeX for Unicode/Hebrew support
    }
    
    try:
        print("Sending to compilation server...")
        response = requests.post(api_url, files=files, params=params, timeout=120)
        
        if response.status_code == 200:
            # Check if we got a PDF back
            content_type = response.headers.get('content-type', '')
            if 'application/pdf' in content_type or response.content[:4] == b'%PDF':
                with open(output_pdf, 'wb') as f:
                    f.write(response.content)
                print(f"✓ Successfully created: {output_pdf}")
                return True
            else:
                print(f"Error: Server returned non-PDF response")
                print(f"Response: {response.text[:500]}")
                return False
        else:
            print(f"Error: Server returned status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
            
    except requests.exceptions.Timeout:
        print("Error: Request timed out. The document might be too complex.")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Error: Network request failed: {e}")
        return False


def compile_with_texlive_net(tex_file: str, output_pdf: str = None) -> bool:
    """
    Alternative: Use texlive.net API (Overleaf's backend).
    """
    tex_path = Path(tex_file)
    
    if not tex_path.exists():
        print(f"Error: File '{tex_file}' not found")
        return False
    
    if output_pdf is None:
        output_pdf = tex_path.with_suffix('.pdf')
    
    print(f"Compiling {tex_file} to PDF...")
    print("Using texlive.net compilation service...")
    
    with open(tex_path, 'r', encoding='utf-8') as f:
        tex_content = f.read()
    
    # texlive.net API (latexcgi) requires multipart/form-data with strict field names.
    # https://davidcarlisle.github.io/latexcgi/
    api_url = "https://texlive.net/cgi-bin/latexcgi"
    # One filename[] must be exactly "document.tex".
    multipart_fields_pdf = [
        ('engine', (None, 'xelatex')),
        ('return', (None, 'pdf')),
        ('filename[]', (None, 'document.tex')),
        ('filecontents[]', (None, tex_content)),
    ]
    
    try:
        print("Sending to compilation server...")
        response = requests.post(api_url, files=multipart_fields_pdf, timeout=120)
        
        if response.status_code == 200 and response.content[:4] == b'%PDF':
            with open(output_pdf, 'wb') as f:
                f.write(response.content)
            print(f"✓ Successfully created: {output_pdf}")
            return True
        else:
            print("Compilation failed. Fetching compilation log...")
            multipart_fields_log = [
                ('engine', (None, 'xelatex')),
                ('return', (None, 'log')),
                ('filename[]', (None, 'document.tex')),
                ('filecontents[]', (None, tex_content)),
            ]
            log_response = requests.post(api_url, files=multipart_fields_log, timeout=120)
            if log_response.status_code == 200:
                print("\n--- Compilation Log (first 4000 chars) ---")
                print(log_response.text[:4000])
                print("--- End Log ---\n")
            return False
            
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    # Default file path
    script_dir = Path(__file__).parent
    tex_file = script_dir / "hebrew_ontology_documentation.tex"
    output_pdf = script_dir / "hebrew_ontology_documentation.pdf"
    
    # Allow command line override
    if len(sys.argv) > 1:
        tex_file = Path(sys.argv[1])
    if len(sys.argv) > 2:
        output_pdf = Path(sys.argv[2])
    
    print("=" * 60)
    print("LaTeX to PDF Compiler (Online)")
    print("=" * 60)
    
    # Try first method
    if compile_latex_online(str(tex_file), str(output_pdf)):
        print("\n✓ Compilation successful!")
        print(f"PDF saved to: {output_pdf}")
        return
    
    # Try alternative method
    print("\nTrying alternative compilation service...")
    if compile_with_texlive_net(str(tex_file), str(output_pdf)):
        print("\n✓ Compilation successful!")
        print(f"PDF saved to: {output_pdf}")
        return
    
    print("\n" + "=" * 60)
    print("Online compilation failed. Alternative options:")
    print("=" * 60)
    print("1. Upload the .tex file to Overleaf.com (free, easy)")
    print("2. Install MacTeX: brew install --cask mactex")
    print(f"\nTeX file location: {tex_file}")


if __name__ == "__main__":
    main()





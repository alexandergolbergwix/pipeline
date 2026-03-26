#!/usr/bin/env python3
"""
Extract comments and annotations from a PDF file.
"""

import fitz  # PyMuPDF
import sys
from collections import defaultdict

def extract_annotations(pdf_path):
    """Extract all annotations/comments from a PDF file."""
    
    doc = fitz.open(pdf_path)
    annotations = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        annots = page.annots()
        
        if annots:
            for annot in annots:
                annot_info = {
                    'page': page_num + 1,
                    'type': annot.type[1],  # Annotation type name
                    'author': annot.info.get('title', 'Unknown'),
                    'content': annot.info.get('content', ''),
                    'subject': annot.info.get('subject', ''),
                    'creation_date': annot.info.get('creationDate', ''),
                    'rect': annot.rect,
                }
                
                # Try to get the highlighted/marked text if applicable
                if annot.type[0] in [8, 9, 10, 11]:  # Highlight, Underline, Squiggly, StrikeOut
                    try:
                        # Get text under the annotation
                        marked_text = page.get_textbox(annot.rect)
                        annot_info['marked_text'] = marked_text.strip()
                    except:
                        annot_info['marked_text'] = ''
                
                annotations.append(annot_info)
    
    doc.close()
    return annotations

def format_annotations(annotations):
    """Format annotations for display, grouped by author."""
    
    if not annotations:
        print("No annotations found in the PDF.")
        return
    
    # Group by author
    by_author = defaultdict(list)
    for annot in annotations:
        by_author[annot['author']].append(annot)
    
    sep = "=" * 80
    hash_sep = "#" * 80
    
    print(f"\n{sep}")
    print(f"TOTAL ANNOTATIONS FOUND: {len(annotations)}")
    print(f"AUTHORS: {', '.join(by_author.keys())}")
    print(f"{sep}\n")
    
    for author, author_annots in by_author.items():
        print(f"\n{hash_sep}")
        print(f"## COMMENTS FROM: {author}")
        print(f"## Total comments: {len(author_annots)}")
        print(f"{hash_sep}\n")
        
        # Sort by page number
        author_annots.sort(key=lambda x: x['page'])
        
        for i, annot in enumerate(author_annots, 1):
            print(f"\n--- Comment {i} (Page {annot['page']}) ---")
            print(f"Type: {annot['type']}")
            
            if annot.get('subject'):
                print(f"Subject: {annot['subject']}")
            
            marked_text = annot.get('marked_text', '')
            if marked_text:
                display_text = marked_text[:200]
                if len(marked_text) > 200:
                    display_text += "..."
                print(f'Marked Text: "{display_text}"')
            
            if annot.get('content'):
                print(f"Comment: {annot['content']}")
            
            print()

def main():
    pdf_path = "/Users/alexandergo/Downloads/Multi-Entity Extraction and Role Classification for Hebrew Manuscripts Using Distant Supervision (3).pdf"
    
    print(f"Extracting annotations from: {pdf_path}")
    
    try:
        annotations = extract_annotations(pdf_path)
        format_annotations(annotations)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

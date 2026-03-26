"""
Hebrew Manuscripts Converter - Flet GUI
A modern cross-platform GUI using Flet (Flutter for Python).
"""

import flet as ft
from pathlib import Path
import threading
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from converter.parser.unified_reader import UnifiedReader
from converter.transformer.mapper import MarcToRdfMapper
from converter.validation.shacl_validator import ShaclValidator
from converter.config.namespaces import bind_namespaces
from rdflib import Graph


class HebrewManuscriptsConverter:
    """Main application class for the Hebrew Manuscripts Converter."""
    
    def __init__(self, page: ft.Page):
        self.page = page
        self.input_file: Path = None
        self.output_file: Path = None
        self.setup_page()
        self.build_ui()
    
    def setup_page(self):
        """Configure the page settings."""
        self.page.title = "Hebrew Manuscripts Converter"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.window.width = 800
        self.page.window.height = 700
        self.page.window.min_width = 600
        self.page.window.min_height = 500
        self.page.padding = 20
        
        # Custom theme with Hebrew manuscript inspired colors
        self.page.theme = ft.Theme(
            color_scheme_seed=ft.Colors.AMBER,
            use_material3=True,
        )
    
    def build_ui(self):
        """Build the main user interface."""
        # Header
        header = ft.Container(
            content=ft.Column([
                ft.Text(
                    "Hebrew Manuscripts Converter",
                    size=32,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.AMBER_200,
                ),
                ft.Text(
                    "ממיר כתבי יד עבריים",
                    size=20,
                    color=ft.Colors.AMBER_100,
                    rtl=True,
                ),
                ft.Text(
                    "Convert MARC/CSV/TSV to RDF (Turtle) format",
                    size=14,
                    color=ft.Colors.WHITE54,
                ),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=20,
        )
        
        # File selection section
        self.file_path_text = ft.Text(
            "No file selected",
            size=14,
            color=ft.Colors.WHITE54,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        
        self.pick_files_dialog = ft.FilePicker(on_result=self.on_file_picked)
        self.page.overlay.append(self.pick_files_dialog)
        
        file_section = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.UPLOAD_FILE, color=ft.Colors.AMBER_200, size=24),
                    ft.Text("Input File", size=18, weight=ft.FontWeight.W_500),
                ]),
                ft.Container(height=10),
                ft.Row([
                    ft.ElevatedButton(
                        "Select File",
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=lambda _: self.pick_files_dialog.pick_files(
                            allowed_extensions=["mrc", "csv", "tsv"],
                            dialog_title="Select MARC, CSV, or TSV file",
                        ),
                        style=ft.ButtonStyle(
                            bgcolor=ft.Colors.AMBER_700,
                            color=ft.Colors.WHITE,
                        ),
                    ),
                    ft.Container(width=10),
                    ft.Container(
                        content=self.file_path_text,
                        expand=True,
                    ),
                ]),
                ft.Container(height=5),
                ft.Text(
                    "Supported formats: .mrc (MARC), .csv, .tsv",
                    size=12,
                    color=ft.Colors.WHITE38,
                ),
            ]),
            padding=20,
            border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
        )
        
        # Output info
        self.output_path_text = ft.Text(
            "Output will be saved next to input file",
            size=14,
            color=ft.Colors.WHITE54,
        )
        
        output_section = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.OUTPUT, color=ft.Colors.GREEN_200, size=24),
                    ft.Text("Output File", size=18, weight=ft.FontWeight.W_500),
                ]),
                ft.Container(height=10),
                self.output_path_text,
            ]),
            padding=20,
            border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
        )
        
        # Convert button
        self.convert_btn = ft.ElevatedButton(
            "Convert to RDF",
            icon=ft.Icons.TRANSFORM,
            on_click=self.start_conversion,
            disabled=True,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.AMBER_700,
                color=ft.Colors.WHITE,
                padding=20,
            ),
            width=250,
            height=50,
        )
        
        # Progress indicator
        self.progress_ring = ft.ProgressRing(
            visible=False,
            width=30,
            height=30,
            color=ft.Colors.AMBER_200,
        )
        
        self.progress_text = ft.Text(
            "",
            size=14,
            color=ft.Colors.WHITE70,
        )
        
        convert_section = ft.Container(
            content=ft.Column([
                ft.Row(
                    [self.convert_btn, self.progress_ring],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Container(height=10),
                self.progress_text,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=20,
        )
        
        # Log output
        self.log_text = ft.TextField(
            value="",
            multiline=True,
            read_only=True,
            min_lines=10,
            max_lines=10,
            text_size=12,
            border_color=ft.Colors.WHITE24,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
        )
        
        log_section = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.TERMINAL, color=ft.Colors.BLUE_200, size=24),
                    ft.Text("Conversion Log", size=18, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLEAR,
                        tooltip="Clear log",
                        on_click=self.clear_log,
                        icon_color=ft.Colors.WHITE54,
                    ),
                ]),
                ft.Container(height=10),
                self.log_text,
            ]),
            padding=20,
            border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
        )
        
        # Main layout
        self.page.add(
            ft.Column([
                header,
                ft.Divider(color=ft.Colors.WHITE24),
                file_section,
                ft.Container(height=10),
                output_section,
                ft.Container(height=10),
                convert_section,
                ft.Container(height=10),
                log_section,
            ], expand=True, scroll=ft.ScrollMode.AUTO)
        )
    
    def on_file_picked(self, e: ft.FilePickerResultEvent):
        """Handle file selection."""
        if e.files and len(e.files) > 0:
            self.input_file = Path(e.files[0].path)
            self.file_path_text.value = str(self.input_file)
            self.file_path_text.color = ft.Colors.WHITE
            
            # Auto-generate output path
            self.output_file = self.input_file.with_suffix('.ttl')
            self.output_path_text.value = f"📁 {self.output_file}"
            self.output_path_text.color = ft.Colors.GREEN_200
            
            self.convert_btn.disabled = False
            self.log(f"✓ Selected: {self.input_file.name}")
        else:
            self.input_file = None
            self.output_file = None
            self.file_path_text.value = "No file selected"
            self.file_path_text.color = ft.Colors.WHITE54
            self.output_path_text.value = "Output will be saved next to input file"
            self.output_path_text.color = ft.Colors.WHITE54
            self.convert_btn.disabled = True
        
        self.page.update()
    
    def log(self, message: str):
        """Add message to log."""
        current = self.log_text.value or ""
        self.log_text.value = current + message + "\n"
        self.page.update()
    
    def clear_log(self, e):
        """Clear the log."""
        self.log_text.value = ""
        self.page.update()
    
    def start_conversion(self, e):
        """Start the conversion in a background thread."""
        self.convert_btn.disabled = True
        self.progress_ring.visible = True
        self.progress_text.value = "Converting..."
        self.page.update()
        
        # Run conversion in background thread
        thread = threading.Thread(target=self.run_conversion)
        thread.start()
    
    def run_conversion(self):
        """Run the actual conversion process."""
        try:
            self.log(f"ℹ️ Starting conversion of {self.input_file.name}")
            
            # Read input file
            reader = UnifiedReader(self.input_file)
            mapper = MarcToRdfMapper()
            
            combined_graph = Graph()
            bind_namespaces(combined_graph)
            
            record_count = 0
            for record in reader.read_file():
                record_graph = mapper.map_record(record)
                for triple in record_graph:
                    combined_graph.add(triple)
                record_count += 1
                
                # Update progress every 100 records
                if record_count % 100 == 0:
                    self.progress_text.value = f"Processing... {record_count} records"
                    self.page.update()
            
            self.log(f"✓ Processed {record_count} records")
            self.log(f"✓ Generated {len(combined_graph)} triples")
            
            # Validate
            self.progress_text.value = "Validating..."
            self.page.update()
            
            validator = ShaclValidator()
            result = validator.validate(combined_graph)
            
            errors = result.get_violations_by_severity("Violation")
            warnings = result.get_violations_by_severity("Warning")
            
            if errors:
                self.log(f"⚠️ Validation: {len(errors)} errors, {len(warnings)} warnings")
            elif warnings:
                self.log(f"✓ Validation passed with {len(warnings)} warnings")
            else:
                self.log("✓ Validation passed - no issues")
            
            # Save output
            self.progress_text.value = "Saving..."
            self.page.update()
            
            combined_graph.serialize(destination=str(self.output_file), format='turtle')
            self.log(f"✓ Saved to: {self.output_file.name}")
            
            self.progress_text.value = "✓ Conversion complete!"
            self.progress_text.color = ft.Colors.GREEN_200
            
            # Show success snackbar
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Successfully converted to {self.output_file.name}"),
                bgcolor=ft.Colors.GREEN_700,
            )
            self.page.snack_bar.open = True
            
        except Exception as ex:
            self.log(f"❌ Error: {str(ex)}")
            self.progress_text.value = "❌ Conversion failed"
            self.progress_text.color = ft.Colors.RED_200
            
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Error: {str(ex)}"),
                bgcolor=ft.Colors.RED_700,
            )
            self.page.snack_bar.open = True
        
        finally:
            self.convert_btn.disabled = False
            self.progress_ring.visible = False
            self.page.update()


def main(page: ft.Page):
    """Main entry point."""
    HebrewManuscriptsConverter(page)


if __name__ == "__main__":
    ft.app(target=main)



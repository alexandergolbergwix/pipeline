#!/usr/bin/env python3
"""Convert MARC XML files to binary .mrc format.

Reads MARC XML files from relevant-documents/ directory and converts them
to binary MARC format (.mrc) for processing with the converter pipeline.
"""

import io
from pathlib import Path

import pymarc


def wrap_in_collection(xml_content: str) -> str:
    """Wrap a single MARC record in a collection element for pymarc parsing."""
    xml_content = xml_content.strip()
    if not xml_content.startswith("<?xml"):
        xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_content

    if "<collection" not in xml_content:
        xml_content = xml_content.replace(
            '<record xmlns="http://www.loc.gov/MARC21/slim">',
            '<collection xmlns="http://www.loc.gov/MARC21/slim"><record>',
        )
        xml_content = xml_content.replace("</record>", "</record></collection>")

    return xml_content


def convert_xml_to_mrc(input_dir: Path, output_dir: Path) -> list:
    """Convert all MARC XML files in input_dir to .mrc format.

    Args:
        input_dir: Directory containing .xml files
        output_dir: Directory for output .mrc files

    Returns:
        List of successfully converted control numbers
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    xml_files = sorted(input_dir.glob("99*.xml"))

    if not xml_files:
        print(f"No MARC XML files found in {input_dir}")
        return []

    print(f"Found {len(xml_files)} MARC XML files:")
    for f in xml_files:
        print(f"  - {f.name}")
    print()

    all_records = []
    converted = []

    for xml_file in xml_files:
        print(f"Processing {xml_file.name}...")

        try:
            with open(xml_file, encoding="utf-8") as f:
                xml_content = f.read()

            wrapped_xml = wrap_in_collection(xml_content)

            records = pymarc.parse_xml_to_array(io.StringIO(wrapped_xml))

            if not records:
                print(f"  Warning: No records found in {xml_file.name}")
                continue

            record = records[0]
            control_number = record["001"].data if record["001"] else "unknown"

            mrc_filename = f"{control_number}.mrc"
            mrc_path = output_dir / mrc_filename

            with open(mrc_path, "wb") as f:
                f.write(record.as_marc())

            print(f"  ✓ Converted to {mrc_filename}")
            title = record["245"]["a"] if record["245"] else "Unknown"
            print(f"    Title: {title}")

            all_records.append(record)
            converted.append(control_number)

        except Exception as e:
            print(f"  ✗ Error: {e}")
            continue

    if all_records:
        combined_path = output_dir / "all_test_manuscripts.mrc"
        with open(combined_path, "wb") as f:
            for record in all_records:
                f.write(record.as_marc())
        print(f"\n✓ Combined {len(all_records)} records into all_test_manuscripts.mrc")

    return converted


def main() -> None:
    """Main entry point."""
    base_dir = Path(__file__).parent.parent
    input_dir = base_dir / "relevant-documents"
    output_dir = base_dir / "data" / "mrc" / "test_manuscripts"

    print("=" * 60)
    print("MARC XML to MRC Converter")
    print("=" * 60)
    print(f"\nInput directory:  {input_dir}")
    print(f"Output directory: {output_dir}\n")

    converted = convert_xml_to_mrc(input_dir, output_dir)

    print("\n" + "=" * 60)
    print(f"Conversion complete: {len(converted)} files converted")
    print("=" * 60)

    if converted:
        print("\nConverted control numbers:")
        for cn in converted:
            print(f"  - {cn}")


if __name__ == "__main__":
    main()

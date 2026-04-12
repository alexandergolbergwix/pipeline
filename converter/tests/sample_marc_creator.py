"""Utility to create sample MARC records for testing."""

from pathlib import Path

from pymarc import Field, Record


def create_sample_record() -> Record:
    """Create a sample Hebrew manuscript MARC record.

    Returns:
        pymarc Record object
    """
    record = Record()

    record.add_field(Field(tag="001", data="990001234560205171"))

    record.add_field(Field(tag="008", data="200115s1407    it            000 0 heb d"))

    record.add_field(
        Field(
            tag="100",
            indicators=["1", " "],
            subfields=["a", "משה בן יצחק אבן תבון", "d", "פעיל 1407"],
        )
    )

    record.add_field(Field(tag="245", indicators=["1", "0"], subfields=["a", "גינת אגוז"]))

    record.add_field(
        Field(tag="260", indicators=[" ", " "], subfields=["a", "קנדיאה", "c", '[שבט קס"ז]'])
    )

    record.add_field(
        Field(tag="300", indicators=[" ", " "], subfields=["a", "151 דפים", "c", '280 x 200 מ"מ'])
    )

    record.add_field(Field(tag="340", indicators=[" ", " "], subfields=["a", "קלף ונייר"]))

    record.add_field(Field(tag="500", indicators=[" ", " "], subfields=["a", "כתב ספרדי בינוני"]))

    record.add_field(
        Field(
            tag="510",
            indicators=["4", " "],
            subfields=["a", "ריכלר, כתבי יד עבריים בספריית הוותיקן"],
        )
    )

    record.add_field(
        Field(tag="561", indicators=[" ", " "], subfields=["a", "יעקב מואטי; ספריית הוותיקן"])
    )

    record.add_field(
        Field(
            tag="856",
            indicators=["4", "0"],
            subfields=["u", "https://digi.vatlib.it/mss/detail/203104"],
        )
    )

    return record


def create_sample_mrc_file(output_path: Path):
    """Create a sample .mrc file for testing.

    Args:
        output_path: Path to save the .mrc file
    """
    record = create_sample_record()

    with open(output_path, "wb") as f:
        f.write(record.as_marc())

    print(f"Created sample MARC file: {output_path}")


def create_multi_record_file(output_path: Path, count: int = 5):
    """Create a .mrc file with multiple sample records.

    Args:
        output_path: Path to save the .mrc file
        count: Number of records to create
    """
    with open(output_path, "wb") as f:
        for i in range(count):
            record = create_sample_record()
            record["001"].data = f"99000{i + 1:04d}560205171"

            record["245"]["a"] = f"כתב יד לדוגמה {i + 1}"

            f.write(record.as_marc())

    print(f"Created sample MARC file with {count} records: {output_path}")


if __name__ == "__main__":
    samples_dir = Path(__file__).parent.parent.parent / "data" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    create_sample_mrc_file(samples_dir / "single_record.mrc")
    create_multi_record_file(samples_dir / "multiple_records.mrc", count=5)

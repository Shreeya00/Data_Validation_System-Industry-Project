import pandas as pd
from io import BytesIO


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a single DataFrame to an .xlsx bytes object."""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    return buf.getvalue()


def to_excel_bytes_multi(sheets: dict) -> bytes:
    """
    Serialize multiple DataFrames to a single .xlsx bytes object.
    sheets: { "Sheet Name": df, ... }
    """
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            safe_name = sheet_name[:31]  # Excel sheet name limit
            df.to_excel(writer, index=False, sheet_name=safe_name)
    return buf.getvalue()
import pandas as pd
from io import BytesIO


def read_any_tabular(file_obj) -> pd.DataFrame:
    """
    Reads CSV, XLSX, or XLS from a file-like object (e.g. Streamlit UploadedFile).
    Returns a pandas DataFrame.
    """
    # Get filename if available
    name = getattr(file_obj, "name", "").lower()

    # Read bytes once so we can seek back
    raw = file_obj.read() if hasattr(file_obj, "read") else file_obj
    buf = BytesIO(raw)

    if name.endswith(".csv"):
        # Try UTF-8 first, fallback to latin-1
        try:
            return pd.read_csv(BytesIO(raw), encoding="utf-8")
        except UnicodeDecodeError:
            return pd.read_csv(BytesIO(raw), encoding="latin-1")

    elif name.endswith(".xls"):
        return pd.read_excel(buf, engine="xlrd")

    elif name.endswith(".xlsx"):
        return pd.read_excel(buf, engine="openpyxl")

    else:
        # Sniff: try CSV first, then Excel
        try:
            return pd.read_csv(BytesIO(raw), encoding="utf-8")
        except Exception:
            pass
        try:
            return pd.read_csv(BytesIO(raw), encoding="latin-1")
        except Exception:
            pass
        try:
            return pd.read_excel(BytesIO(raw), engine="openpyxl")
        except Exception:
            pass
        return pd.read_excel(BytesIO(raw), engine="xlrd")
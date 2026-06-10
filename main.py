# This is literally full fledged working code without ui change
# ============================
# Imports
# ============================
import sys
from pathlib import Path

# Ensure project root on sys.path (so `from app...` works)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
import pandas as pd
import numpy as np
import io
from io import BytesIO
import json
import re
import ast
import textwrap
import traceback

# Optional DB clients (wired later)
import pyodbc     # noqa: F401
from sqlalchemy import create_engine  # noqa: F401
import snowflake.connector  # noqa: F401

# Local imports
from app.core.config import load_theme_style, load_api_config, generate_auth_token
from app.engine.file_processor import read_any_tabular   # unified CSV/XLSX/XLS
from app.engine.export import to_excel_bytes, to_excel_bytes_multi
from app.agents.llm_client import ask_chatgpt


# ============================
# Streamlit Page Config (FIRST)
# ============================
st.set_page_config(
    page_title="Data Validation Assistant",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================
# Load config (theme/style + API)
# ============================
theme, style = load_theme_style()
api_config = load_api_config()

st.title("🔍 Data Validation Assistant")
st.caption("**Compare data between Source and Destination systems**")

# ============================
# CSS
# ============================
dark_css = """
<style>
    [data-testid="stAppViewContainer"] {
        background-color: #969696;
        color: #1C1C1C;
        font-family: 'Inter', sans-serif;
    }
    h1, h2, h3 { color: #ffffff; }
    label { font-size: 16px; color: #ffffff; }

    div[data-testid="stRadio"] > div {
        display: flex; align-items: center; gap: 10px;
    }
    [data-testid="stHeader"] { background-color: #969696 !important; }
    [data-testid="stMainBlockContainer"] {
        padding-left: 7rem !important;
        padding-right: 2rem !important;
        padding-top: 2.5rem !important;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #C0C0C0 !important;
        display: flex; flex-direction: column; justify-content: flex-start; align-items: center;
        padding: 20px !important;
    }
    [data-testid="stSidebar"] * {
        color: #002060 !important;
        font-family: "Calibri", monospace !important;
        font-weight: bold !important;
        width: 80px !important;
        box-sizing: content-box !important;
    }

    /* Buttons */
    div.stButton > button {
        background-color: #3E69A8 !important;
        color: white !important;
        border: 4px solid #003399 !important;
        border-radius: 8px !important;
        font-weight: bold !important;
    }
    div.stButton > button:hover {
        background-color: #3E69A8 !important;
        border: 2px solid #FFFFFF !important;
    }

    /* File uploader text */
    [data-testid="stFileUploaderDropzone"] span {
        color: #292929 !important;
        font-weight: bold !important;
    }

    /* Download button styling */
    .stDownloadButton > button {
        background-color: #3E69A8 !important;
        color: white !important;
        border: 4px solid #003399 !important;
        border-radius: 8px !important;
        font-weight: bold !important;
    }

    /* Optional card */
    .card {
        background-color: #156082 !important;
        border: 2px #156082 !important;
        border-radius: 10px !important;
        padding: 28px !important;
        text-align: center !important;
    }
</style>
"""
st.markdown(dark_css, unsafe_allow_html=True)


# =============================================
# Helpers (local)
# =============================================
def format_dataset(df_dict):
    """
    df_dict: list[dict] or dict-like
    Returns a plain-text table (string) for compact LLM context.
    """
    new_df = []
    if not df_dict:
        return ""

    headers = list(df_dict[0].keys())
    header_line = " | ".join(headers)
    separator_line = "-" * len(header_line)

    rows_lines = []
    for row in df_dict:
        formatted_row = []
        for h in headers:
            v = row.get(h, "")
            formatted_row.append(str(v))
        rows_lines.append(" | ".join(formatted_row))

    view_table = "\n".join([header_line, separator_line] + rows_lines)
    new_df.append(view_table)
    new_df.append("\n")
    return "\n".join(new_df)


def build_recommendations(df_source, df_dest, key_columns, mismatch_columns=None):
    """
    Lightweight heuristic recommendations derived from data/profile.
    Returns a list of recommendation strings (bullets).
    """
    recs = []
    try:
        src = df_source.copy()
        dst = df_dest.copy()
        src.columns = src.columns.str.strip().str.upper()
        dst.columns = dst.columns.str.strip().str.upper()

        # 1) Key integrity (if keys provided)
        if key_columns:
            for k in key_columns:
                if k in src.columns and src[k].isna().any():
                    recs.append(f"Fill or drop rows with missing key values in **{k}** on Source; missing keys break alignment.")
                if k in dst.columns and dst[k].isna().any():
                    recs.append(f"Fill or drop rows with missing key values in **{k}** on Destination; missing keys break alignment.")

            if all(k in src.columns for k in key_columns):
                dup_src = src.duplicated(subset=key_columns).sum()
                if dup_src > 0:
                    recs.append(f"Remove duplicates on Source keys **{', '.join(key_columns)}** (found {dup_src}).")
            if all(k in dst.columns for k in key_columns):
                dup_dst = dst.duplicated(subset=key_columns).sum()
                if dup_dst > 0:
                    recs.append(f"Remove duplicates on Destination keys **{', '.join(key_columns)}** (found {dup_dst}).")

        # 2) Type harmonization suggestions
        common = [c for c in src.columns if c in dst.columns]
        for c in common:
            t1, t2 = str(src[c].dtype), str(dst[c].dtype)
            if t1 != t2:
                recs.append(f"Cast **{c}** to a consistent dtype (Source: {t1}, Dest: {t2}).")

        # 3) Whitespace / case normalization
        obj_cols = [c for c in common if (pd.api.types.is_object_dtype(src[c]) or pd.api.types.is_object_dtype(dst[c]))]
        for c in obj_cols:
            s_trim_flag = (src[c].astype(str) != src[c].astype(str).str.strip()).mean() if len(src) else 0
            d_trim_flag = (dst[c].astype(str) != dst[c].astype(str).str.strip()).mean() if len(dst) else 0
            if s_trim_flag > 0.02 or d_trim_flag > 0.02:
                recs.append(f"Trim leading/trailing spaces in **{c}** on both datasets to avoid false mismatches.")

            try:
                left = src[c].astype(str).str.lower()
                right = dst[c].astype(str).str.lower()
                if (mismatch_columns and c in mismatch_columns) or (min(len(left), len(right)) <= 100000):
                    if len(set(left)) and len(set(right)):
                        if (set(src[c].astype(str)) != set(dst[c].astype(str))) and (set(left) == set(right)):
                            recs.append(f"Standardize case (e.g., upper) in **{c}** across both datasets.")
            except Exception:
                pass

        # 4) Null-handling for critical columns
        critical_cols = set((mismatch_columns or []) + (key_columns or []))
        for c in critical_cols:
            if c in src.columns and src[c].isna().any():
                recs.append(f"Define a null policy for **{c}** on Source (defaults/remove).")
            if c in dst.columns and dst[c].isna().any():
                recs.append(f"Define a null policy for **{c}** on Destination (defaults/remove).")

        # 5) Parsing suggestions
        for c in common:
            if (pd.api.types.is_object_dtype(src[c]) and pd.api.types.is_datetime64_any_dtype(dst[c])) or \
               (pd.api.types.is_object_dtype(dst[c]) and pd.api.types.is_datetime64_any_dtype(src[c])):
                recs.append(f"Parse/format dates consistently in **{c}** (e.g., ISO 8601).")
            if (pd.api.types.is_object_dtype(src[c]) and pd.api.types.is_numeric_dtype(dst[c])) or \
               (pd.api.types.is_object_dtype(dst[c]) and pd.api.types.is_numeric_dtype(src[c])):
                recs.append(f"Normalize numeric formatting in **{c}** (remove commas, coerce to numeric).")

        # 6) Column spec governance
        recs.append("Maintain a shared column spec and export pipeline to keep **consistent column order and names**.")
    except Exception:
        pass

    # Deduplicate
    seen = set()
    out = []
    for r in recs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# =====================================================================
# LLM code-gen helpers (Advanced mode)
# =====================================================================
def clean_llm_code(raw: str) -> str:
    if not isinstance(raw, str):
        return ""

    code = raw.strip()

    # Strip code fences
    fence_patterns = [
        (r"^```(?:python)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE),
        (r"^~~~(?:python)?\s*(.*?)\s*~~~$", re.DOTALL | re.IGNORECASE),
    ]
    for pat, flags in fence_patterns:
        m = re.match(pat, code, flags)
        if m:
            code = m.group(1).strip()
            break

    # drop leading 'python\n'
    if code.lower().startswith("python\n"):
        code = code.split("\n", 1)[1].lstrip()

    # ✅ NEW: Strip ALL triple-quoted docstrings (they cause syntax errors after regex fixes)
    code = re.sub(r'""".*?"""', '""" """', code, flags=re.DOTALL)
    code = re.sub(r"'''.*?'''", "''' '''", code, flags=re.DOTALL)

    # normalize quotes
    code = code.encode("utf-8", "ignore").decode("utf-8")
    if code.startswith("\ufeff"):
        code = code.lstrip("\ufeff")
    code = code.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")

    # remove import lines
    code = re.sub(r'^\s*(?:from\s+\S+\s+import\s+.*|import\s+.+)$', '', code, flags=re.MULTILINE)
    code = re.sub(r'\n{3,}', '\n\n', code)

    return code
def normalize_rule_text(rule_text: str, df_dest):


    rule_text = rule_text.strip()

    # --------------------------------------------------
    # ✅ STEP 2: Existing logic (KEEP THIS)
    # --------------------------------------------------

    dst_cols = set(df_dest.columns.str.upper().str.strip())
    src_cols_pattern = r'Source\.([A-Za-z0-9_]+)'
    dst_cols_pattern = r'Destination\.([A-Za-z0-9_]+)'

    def replace_src(m):
        col = m.group(1).upper().strip()
        return f'df_source["{col}"]'

    def replace_dst(m):
        col = m.group(1).upper().strip()
        return f'df_dest["{col}"]'

    rule_text = re.sub(dst_cols_pattern, replace_dst, rule_text, flags=re.IGNORECASE)
    rule_text = re.sub(src_cols_pattern, replace_src, rule_text, flags=re.IGNORECASE)

    return rule_text

def generate_python_from_rule(rule_text, df_source, df_dest, api_config):
    """Ask LLM to produce Python for validate_rule(...) with strict instructions (no imports)."""
    if not api_config.get("chat_endpoint"):
        raise RuntimeError("AI chat endpoint not configured. Set CHAT_ENDPOINT, API_KEY or APPKEY in .env / config.json.")

    system_prompt = textwrap.dedent("""
You are a deterministic data validation code generator.
Produce ONLY Python code (no prose) that defines:

    def validate_rule(df_source, df_dest):
        \"\"\"
        df_source: pandas.DataFrame (Source)
        df_dest:   pandas.DataFrame (Destination)

        Return:
          {
            "status": "PASS" or "FAIL",
            "message": "<short summary>",
            "violations_df": <pandas.DataFrame or None>
          }
        \"\"\"
        # ... your logic ...

========================
HARD REQUIREMENTS (MANDATORY)
========================

1. DO NOT write any imports.
2. DO NOT perform file or network I/O.
3. Use only pd.*, np.*, isclose, np_isclose (these are injected).
4. DO NOT modify df_source or df_dest in place — always work on copies.

------------------------
FLOAT COMPARISON RULE
------------------------
- NEVER compare floats using ==.
- ALWAYS use:
      isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)

------------------------
COLUMN NORMALIZATION
------------------------
ALWAYS start with:

    df_source = df_source.copy()
    df_dest = df_dest.copy()
    df_source.columns = df_source.columns.str.upper().str.strip()
    df_dest.columns = df_dest.columns.str.upper().str.strip()

------------------------
DATAFRAME METHOD RULE (CRITICAL)
------------------------
- NEVER call pandas module-level methods such as:
      pd.select_dtypes
      pd.groupby
      pd.filter
      pd.any
      pd.all

- ALWAYS call methods on DataFrame objects:

      ✅ df_source.select_dtypes(...)
      ✅ df_dest.groupby(...)
      ✅ df_source["COLUMN"]

      ❌ pd.select_dtypes(...)
      ❌ pd.groupby(...)

------------------------
RETURN RULE (CRITICAL)
------------------------

✅ PASS CASE:
- violations_df MUST be None

    return {
        "status": "PASS",
        "message": "All rows comply with the rule.",
        "violations_df": None
    }

✅ FAIL CASE:
- violations_df MUST ALWAYS be a pandas DataFrame
- violations_df MUST NEVER be None when status="FAIL"

    return {
        "status": "FAIL",
        "message": f"Validation failed: {len(violations_df)} violations found",
        "violations_df": violations_df
    }

------------------------
OUTPUT RULE
------------------------
- Output raw Python ONLY
- No markdown
- No backticks
- No text outside the function
                                   

------------------------
DATAFRAME METHOD RULE (CRITICAL)
------------------------

- NEVER call pandas module-level methods such as:

      pd.select_dtypes
      pd.groupby
      pd.filter
      pd.any
      pd.all
      pd.copy
      pd.nunique

- ALWAYS call methods on DataFrame objects:

      ✅ df.copy()
      ✅ df["COLUMN"].nunique()
      ✅ df.groupby(...)
      ✅ df.select_dtypes(...)

      ❌ pd.copy(...)
      ❌ pd.nunique(...)
""").strip()

    # Small, schema-first context
    def schema(df):
        cols = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
        return json.dumps(cols, ensure_ascii=False)

    def sample(df, n=5):
        try:
            return df.head(n).to_json(orient="records")
        except Exception:
            return "[]"

    src_col_list = [c.upper() for c in df_source.columns]
    dst_col_list = [c.upper() for c in df_dest.columns]

    ctx = io.StringIO()
    ctx.write("## Data Context\n")
    ctx.write(f"\n### Source columns (use as df_source['COL']): {src_col_list}\n")
    ctx.write(f"\n### Destination columns (use as df_dest['COL']): {dst_col_list}\n")
    ctx.write("\n### Source Schema\n" + schema(df_source) + "\n")
    ctx.write("\n### Destination Schema\n" + schema(df_dest) + "\n")
    ctx.write("\n### Source Sample (up to 5 rows)\n" + sample(df_source) + "\n")
    ctx.write("\n### Destination Sample (up to 5 rows)\n" + sample(df_dest) + "\n")

    access_token = generate_auth_token(api_config)
    code_text = ask_chatgpt(
        prompt=f"### Natural language rule\n{rule_text}\n\n{ctx.getvalue()}\n\n### Produce ONLY Python code:",
        access_token=access_token,
        system_prompt=system_prompt,
        api_config=api_config
    )
    return code_text

    # ==========================================================
# AUTOMATIC LLM PANDAS REWRITE PATCH (MANDATORY SAFETY NET)
# ==========================================================
def _auto_fix_pandas_code(code: str) -> str:



    code = code.replace("constants.", "")

    code = re.sub(
        r'pd\s*\.\s*copy\s*\(\s*([a-zA-Z0-9_\.]+)\s*\)',
        r'\1.copy()',
        code
    )
    code = re.sub(
        r'pd\s*\.\s*copy\s*\(\s*([^)]+?)\s*\)',
        r'\1.copy()',
        code,
        flags=re.DOTALL
    )

    # pd.groupby(x) -> x.groupby()
    code = re.sub(
        r'pd\s*\.\s*groupby\s*\(\s*([^)]+?)\s*\)',
        r'\1.groupby()',
        code,
        flags=re.DOTALL
    )

    # pd.nunique(df["COL"]) -> df["COL"].nunique()
    code = re.sub(
        r'pd\s*\.\s*nunique\s*\(\s*([^]]+\])\s*\)',
        r'\1.nunique()',
        code
    )

    # pd.any(x) -> x.any()
    code = re.sub(
        r'pd\s*\.\s*any\s*\(\s*([^)]+?)\s*\)',
        r'\1.any()',
        code
    )

    # pd.all(x) -> x.all()
    code = re.sub(
        r'pd\s*\.\s*all\s*\(\s*([^)]+?)\s*\)',
        r'\1.all()',
        code
    )

    # pd.isclose(...) -> np.isclose(...)
    code = re.sub(
        r'pd\s*\.\s*isclose\s*\(',
        r'np.isclose(',
        code
    )

    return code

def _fix_bare_column_refs(code: str, df_source, df_dest) -> str:
    src_cols = set(df_source.columns.str.upper().str.strip())
    dst_cols = set(df_dest.columns.str.upper().str.strip())
    all_cols = src_cols | dst_cols

    lines = code.split("\n")
    fixed_lines = []
    in_docstring = False  # ← track docstring state

    for line in lines:
        stripped = line.strip()

        # Toggle docstring state
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            fixed_lines.append(line)
            continue

        # Skip lines inside docstrings or other safe lines
        if (in_docstring
            or stripped.startswith("def ")
            or stripped.startswith("#")
            or stripped.startswith("return")
            or stripped.startswith("df_source")
            or stripped.startswith("df_dest")
            or not stripped):
            fixed_lines.append(line)
            continue

        for col in all_cols:
            pattern = r'(?<!["\'\w])' + re.escape(col) + r'(?!["\'\w\[])'
            if re.search(pattern, line, re.IGNORECASE):
                if col in dst_cols and col not in src_cols:
                    replacement = f'df_dest["{col}"]'
                elif col in src_cols and col not in dst_cols:
                    replacement = f'df_source["{col}"]'
                else:
                    replacement = f'df_dest["{col}"]' if "dest" in line.lower() else f'df_source["{col}"]'
                line = re.sub(pattern, replacement, line, flags=re.IGNORECASE)

        fixed_lines.append(line)

    return "\n".join(fixed_lines)

def run_generated_validation(code_text, df_source, df_dest):
    """Safely exec the generated code and run validate_rule(df_source, df_dest)."""

    # ----------------------------------------------------------
    # 1) Clean & auto-fix LLM generated code
    # ----------------------------------------------------------
    code_text = clean_llm_code(code_text)
    code_text = _auto_fix_pandas_code(code_text)
    code_text = _fix_bare_column_refs(code_text, df_source, df_dest)

    # Normalize column literals inside code: ["col"] → ["COL"]
    match = re.findall(r'\["([^"]+)"\]', code_text)
    for col in set(match):
        code_text = code_text.replace(
            f'["{col}"]',
            f'["{col.upper().strip()}"]'
        )

    # ----------------------------------------------------------
    # 2) ✅ FINAL REQUIRED FIX: Column existence & safety guard
    # ----------------------------------------------------------
    def _extract_columns_from_code(code: str):
        # Extract df["COL"] references
        return {c.upper().strip() for c in re.findall(r'\["([^"]+)"\]', code)}

    src_cols = set(df_source.columns.str.upper().str.strip())
    dst_cols = set(df_dest.columns.str.upper().str.strip())
    used_cols = _extract_columns_from_code(code_text)

    missing_src = []
    missing_dst = []

    for col in used_cols:
        if col not in src_cols and col not in dst_cols:
            missing_src.append(col)

    if missing_src or missing_dst:
        return {
            "status": "FAIL",
            "message": (
                f"⚠️ Column issue detected:\n"
                f"Missing in Source: {', '.join(missing_src) if missing_src else 'None'}\n"
                f"Missing in Destination: {', '.join(missing_dst) if missing_dst else 'None'}\n\n"
                f"Available Source columns: {', '.join(sorted(src_cols))}\n"
                f"Available Destination columns: {', '.join(sorted(dst_cols))}\n\n"
                f"👉 Fix column names in your rule."
            ),
            "violations_df": None
        }, ""

    # ----------------------------------------------------------
    # 3) Hard safety guards (pandas misuse, imports)
    # ----------------------------------------------------------
    if "pd.copy(" in code_text or re.search(r'pd\s*\.\s*copy\s*\(', code_text):
        raise ValueError("Invalid code: use df.copy() instead of pd.copy()")

    if re.search(r'^\s*(from\s+\S+\s+import|import\s+)', code_text, flags=re.MULTILINE):
        raise ValueError("Imports are not allowed in generated rule code.")

    # ----------------------------------------------------------
    # 4) Parse & AST guard
    # ----------------------------------------------------------
    try:
        tree = ast.parse(code_text)
    except SyntaxError as se:
        preview = code_text[:200].replace("\n", "\\n")
        raise SyntaxError(
            f"SyntaxError while parsing. First 200 chars: {preview}\nOriginal error: {se}"
        )

    banned_nodes = (ast.Import, ast.ImportFrom)
    for node in ast.walk(tree):
        if isinstance(node, banned_nodes):
            raise ValueError("Imports are not allowed in generated rule code.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"open", "exec", "eval", "__import__"}:
                raise ValueError(f"Unsafe call '{node.func.id}' is not allowed.")

    # ----------------------------------------------------------
    # 5) Safe execution environment
    # ----------------------------------------------------------
    import pandas as _pd
    import numpy as _np
    import math as _math

    try:
        _pd.isclose = _np.isclose  # safety patch
    except Exception:
        pass

    safe_builtins = {
        "len": len, "min": min, "max": max, "sum": sum, "abs": abs,
        "round": round, "sorted": sorted, "any": any, "all": all,
        "enumerate": enumerate, "range": range, "set": set,
        "list": list, "dict": dict, "tuple": tuple,
        "int": int, "float": float, "str": str, "bool": bool,
        "zip": zip, "map": map,
        "print": print,
        "isclose": _math.isclose,
    }

    global_ns = {
        "__builtins__": safe_builtins,
        "pd": _pd,
        "np": _np,
        "isclose": _math.isclose,
        "np_isclose": _np.isclose,
    }
    local_ns = {}

    # ----------------------------------------------------------
    # 🔥 GLOBAL NULL SAFETY (ADD HERE)
    # ----------------------------------------------------------
    df_source = df_source.copy()
    df_dest = df_dest.copy()

    df_source = df_source.fillna("")
    df_dest = df_dest.fillna("")


    # ----------------------------------------------------------
    # 6) Exec generated code
    # ----------------------------------------------------------
    log_buffer = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = log_buffer
        exec(code_text, global_ns, local_ns)
    finally:
        sys.stdout = old_stdout

    validate_rule = local_ns.get("validate_rule", global_ns.get("validate_rule"))
    if not callable(validate_rule):
        raise ValueError("No function 'validate_rule' found after exec.")

    # ----------------------------------------------------------
    # 7) Execute validation rule
    # ----------------------------------------------------------
    
    try:
        result = validate_rule(df_source.copy(), df_dest.copy())
        if not isinstance(result, dict):
            raise ValueError("validate_rule must return a dict.")

        # ✅ Only intercept if violations_df is None on a FAIL 
        # AND the column genuinely doesn't exist (not a false alarm)
        if result.get("status") == "FAIL" and result.get("violations_df") is None:
            msg = result.get("message", "").lower()
            if "missing" in msg and "column" in msg:
                src_cols = set(df_source.columns.str.upper().str.strip())
                dst_cols = set(df_dest.columns.str.upper().str.strip())
                # Extract column names mentioned in the message
                mentioned = re.findall(r'\b([A-Z_]{2,})\b', result.get("message", "").upper())
                truly_missing = [c for c in mentioned if c not in src_cols and c not in dst_cols
                            and c not in {"MISSING", "COLUMN", "COLUMNS", "BOTH", "DATASETS", "THE", "IN"}]
                if truly_missing:
                    # Genuine missing column — show helpful error
                    return {
                        "status": "FAIL",
                        "message": (
                            f"⚠️ Column(s) not found: {', '.join(truly_missing)}\n"
                            f"Available Source columns: {', '.join(sorted(src_cols))}\n"
                            f"Available Destination columns: {', '.join(sorted(dst_cols))}\n\n"
                            f"👉 Rephrase your rule using exact column names above and try again."
                        ),
                        "violations_df": None
                    }, log_buffer.getvalue()
                # else: message said "missing column" but it's a false alarm — fall through normally

        return result, log_buffer.getvalue()

    except Exception as e:
        raise RuntimeError(f"Execution error in validate_rule: {e}")

# =====================================================================
# Direct LLM plan → Execute (no-code Business Rule)
# =====================================================================
def _build_llm_schema_context(df_source, df_dest):
    def schema(df):
        return [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
    def sample(df, n=5):
        try:
            return df.head(n).to_dict(orient="records")
        except Exception:
            return []
    ctx = {
        "source": {"schema": schema(df_source), "sample": sample(df_source)},
        "destination": {"schema": schema(df_dest), "sample": sample(df_dest)}
    }
    return json.dumps(ctx, ensure_ascii=False)


def plan_rule_from_prompt(rule_text, df_source, df_dest, used_keys, api_config):
    """
    Convert a natural-language rule into a strict JSON PLAN (no code).
    """
    if not api_config.get("chat_endpoint"):
        raise RuntimeError("AI chat endpoint not configured. Set CHAT_ENDPOINT, API_KEY or APPKEY in .env / config.json.")

    schema_ctx = _build_llm_schema_context(df_source, df_dest)
    system_prompt = textwrap.dedent("""
    You are a deterministic data validation code generator.
    Produce ONLY Python code (no prose) that defines:

        def validate_rule(df_source, df_dest):
            \"\"\"
            df_source: pandas.DataFrame (Source)
            df_dest:   pandas.DataFrame (Destination)

            Return:
            {
                "status": "PASS" or "FAIL",
                "message": "<short summary>",
                "violations_df": <pandas.DataFrame or None>
            }
            \"\"\"
            # ... your logic ...

    ============================================================
    ✅ 1. COLUMN NORMALIZATION (MANDATORY)
    ============================================================
    ALWAYS start with:

        df_source = df_source.copy()
        df_dest = df_dest.copy()
        df_source.columns = df_source.columns.str.upper().str.strip()
        df_dest.columns = df_dest.columns.str.upper().str.strip()

    ============================================================
    ✅ 2. COLUMN ACCESS (STRICT)
    ============================================================
    - ALWAYS reference columns using:
        df_source["COLUMN"]
        df_dest["COLUMN"]

    - NEVER use raw column names like REGION directly
    - ALWAYS assume column names are UPPERCASE

    ============================================================
    ✅ 3. NULL-SAFE STRING HANDLING (CRITICAL 🔥)
    ============================================================
    Before applying ANY string operation:

        col = df["COLUMN"].fillna("").astype(str).str.strip()

    - ALWAYS use this pattern
    - NEVER call .str methods without fillna + astype(str)
    - This rule is MANDATORY

    ============================================================
    ✅ 4. FLOAT COMPARISON (CRITICAL)
    ============================================================
    NEVER use == for floats

    Use:

        isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)

    OR:

        np_isclose(series1, series2, rtol=1e-6, atol=1e-6)

    ============================================================
    ✅ 5. LOGIC TYPE DETECTION
    ============================================================
    If rule contains:
        "average", "sum", "%", "tolerance", "greater", "less"
    → AGGREGATE VALIDATION

    Else → ROW LEVEL VALIDATION

    ============================================================
    ✅ 6. ROW-LEVEL VALIDATION (DEFAULT)
    ============================================================
    - Identify column(s)
    - Apply condition row-wise
    - Build violations_df like:

        violations_df = df_dest[condition]

    Examples:

        col = df_dest["REGION"].fillna("").astype(str).str.strip()
        violations_df = df_dest[col == ""]

    ============================================================
    ✅ 7. AGGREGATE VALIDATION
    ============================================================
    - Use groupby or column-level stats
    - Compare using isclose
    - violations_df must contain failing rows

    ============================================================
    ✅ 8. RETURN FORMAT (STRICT)
    ============================================================

    PASS:

        return {
            "status": "PASS",
            "message": "All rows comply with the rule.",
            "violations_df": None
        }

    FAIL:

        return {
            "status": "FAIL",
            "message": f"Validation failed: {len(violations_df)} violations found",
            "violations_df": violations_df
        }

    🚨 violations_df MUST NEVER be None when FAIL

    ============================================================
    ✅ 9. SAFETY RULES
    ============================================================
    - DO NOT use imports
    - DO NOT use file I/O
    - DO NOT modify original dataframes
    - DO NOT use pd.select_dtypes, pd.groupby etc.
    - ALWAYS use dataframe methods:
        df.groupby(), df["COL"]

    ============================================================
    ✅ 10. OUTPUT RULE
    ============================================================
    - Output ONLY Python code
    - NO markdown
    - NO explanation
    - NO backticks

    ============================================================     
    ✅ 11. NUMERIC SAFE HANDLING (MANDATORY)
    ============================================================
    Before numeric comparison:

        col = pd.to_numeric(df["COLUMN"], errors="coerce")

    - ALWAYS convert to numeric
    - Handle nulls safely
    - Use comparisons only after conversion
                                    
    ============================================================
    ✅ 12. CROSS-DATASET COMPARISON (CRITICAL)
    ============================================================
    For rules involving both Source and Destination:

    - ALWAYS join using common keys (e.g., Product_ID)

    Example:

        merged = df_source.merge(df_dest, on="PRODUCT_ID", how="inner")

    - Then compare:

        violations_df = merged[
            ~np_isclose(
                merged["UNIT_PRICE"],
                merged["UNIT_COST"],
                rtol=1e-6,
                atol=1e-6
            )
        ]
                                    
    ============================================================
    ✅ STRICT COLUMN USAGE (CRITICAL FIX)
    ============================================================

    - ONLY use columns explicitly mentioned in the rule
    - DO NOT introduce new columns
    - DO NOT infer or guess column names
    - If column is not found, RETURN FAIL with message          

    ============================================================
    ✅ COLUMN EXISTENCE CHECK (MANDATORY)
    ============================================================

    Before using any column:

        if "COLUMN" not in df.columns:
            return {
                "status": "FAIL",
                "message": "Column COLUMN not found",
                "violations_df": None
            }    

    ============================================================
    ✅ NULL SAFE STRING HANDLING (STRICT - NO EXCEPTIONS)
    ============================================================

    For ANY string column:

        col = df["COLUMN"].fillna("").astype(str).str.strip()

    - NEVER use df["COLUMN"].str directly
    - ALWAYS apply fillna + astype(str) before .str                                                                                      
    """).strip()

    user_prompt = (
        f"RULE:\n{rule_text}\n\n"
        f"DATA CONTEXT(JSON):\n{schema_ctx}\n\n"
        f"OUTPUT: JSON PLAN ONLY."
    )

    access_token = generate_auth_token(api_config)
    plan_text = ask_chatgpt(
        prompt=user_prompt,
        access_token=access_token,
        system_prompt=system_prompt,
        api_config=api_config
    )

    cleaned = plan_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]

    # Force keys if user picked them in UI
    plan = json.loads(cleaned)
    if "join" not in plan or "calculations" not in plan or "pass_condition" not in plan:
        raise ValueError("Plan missing required keys: join/calculations/pass_condition.")
    if used_keys:
        plan.setdefault("join", {})
        plan["join"]["keys"] = used_keys
    return plan


def _normalize_cols(df):
    d = df.copy()
    d.columns = d.columns.str.strip().str.upper()
    return d


def _align_on_keys(src, dst, keys):
    s = _normalize_cols(src)
    d = _normalize_cols(dst)
    keys_u = [k.upper() for k in keys]
    if not all(k in s.columns for k in keys_u) or not all(k in d.columns for k in keys_u):
        raise ValueError(f"Join keys not found on both frames: {keys}")
    s = s.sort_values(by=keys_u, kind="mergesort").set_index(keys_u)
    d = d.sort_values(by=keys_u, kind="mergesort").set_index(keys_u)
    s, d = s.align(d, join="inner", axis=0)
    s, d = s.align(d, join="inner", axis=1)
    return s, d


SAFE_EXPR_PATTERN = re.compile(r"^[A-Za-z0-9_\.\s\(\)\+\-\*\/%|&<>=!\[\]]+$")

def _guard_and_eval_series_expr(expr, env):
    """
    Guarded evaluator for vector expressions.
    Allowed: alnum/_/./space, (), + - * / % | & < > = ! []
    Supports abs() mapped to numpy.abs.
    Rewrites 'SRC.' -> 'SRC_' and 'DEST.' -> 'DEST_' for env variables.
    """
    if not SAFE_EXPR_PATTERN.match(expr):
        raise ValueError(f"Disallowed characters in expression: {expr}")
    expr2 = expr.replace("SRC.", "SRC_").replace("DEST.", "DEST_")
    expr2 = re.sub(r"\babs\(", "ABS(", expr2, flags=re.IGNORECASE)
    return eval(expr2, {"__builtins__": {}, "ABS": np.abs}, env)  # env has Series only


def execute_rule_plan(plan, df_source, df_dest):
    keys = plan.get("join", {}).get("keys", [])
    if not keys:
        raise ValueError("Plan missing join.keys")
    s_aligned, d_aligned = _align_on_keys(df_source, df_dest, keys)

    # Build env of Series: SRC_<col>, DEST_<col>
    env = {}
    for c in s_aligned.columns:
        env[f"SRC_{c}"] = s_aligned[c]
    for c in d_aligned.columns:
        env[f"DEST_{c}"] = d_aligned[c]

    # Calculations
    for calc in plan.get("calculations", []):
        name, expr = calc.get("name"), calc.get("expression")
        if not name or not expr:
            continue
        env[name] = _guard_and_eval_series_expr(expr, env)

    # Pass/Fail condition
    pass_cond = plan.get("pass_condition", "")
    if not pass_cond:
        raise ValueError("Plan missing pass_condition.")
    mask = _guard_and_eval_series_expr(pass_cond, env).astype(bool)

    # Build table for requested columns
    disp = plan.get("display_columns", [])
    out = pd.DataFrame(index=s_aligned.index)

    def get_series(ref):
        if ref.startswith("SRC."):
            col = ref[4:].upper();  return s_aligned[col] if col in s_aligned.columns else pd.Series(index=out.index, dtype="object")
        if ref.startswith("DEST."):
            col = ref[5:].upper();  return d_aligned[col] if col in d_aligned.columns else pd.Series(index=out.index, dtype="object")
        if ref in env:  # calc name
            return env[ref]
        cu = ref.upper()
        if cu in s_aligned.columns: return s_aligned[cu]
        if cu in d_aligned.columns: return d_aligned[cu]
        return pd.Series(index=out.index, dtype="object")

    for ref in disp:
        ser = get_series(ref)
        label = ref.replace("SRC.", "SRC_").replace("DEST.", "DEST_")
        out[label] = ser

    # Split
    pass_df = out[mask].reset_index()
    fail_df = out[~mask].reset_index()

    metrics = {
        "total_aligned_rows": int(len(out)),
        "passed": int(mask.sum()),
        "failed": int((~mask).sum()),
        "pass_rate_pct": round(100.0 * (mask.mean() if len(out) else 0), 2)
    }
    return {"pass_df": pass_df, "fail_df": fail_df, "metrics": metrics}


def narrate_rule_results(rule_text, plan, metrics, pass_df, fail_df, api_config):
    """Short, business-ready narrative via LLM."""
    if not api_config.get("chat_endpoint"):
        return ""
    def df_text(df, n=5):
        return "(none)" if df is None or df.empty else df.head(n).to_string(index=False)
    sys_prompt = (
        "You are a senior data quality analyst. Provide 6–10 crisp bullets:\n"
        "1) What rule was checked, 2) Key metrics (pass/fail), 3) Examples of pass/fail rows, 4) Recommended next steps."
    )
    prompt = (
        f"RULE:\n{rule_text}\n\n"
        f"PLAN(JSON):\n{json.dumps(plan, ensure_ascii=False)}\n\n"
        f"METRICS(JSON):\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
        f"PASS SAMPLE:\n{df_text(pass_df)}\n\n"
        f"FAIL SAMPLE:\n{df_text(fail_df)}\n"
    )
    access_token = generate_auth_token(api_config)
    return ask_chatgpt(prompt, access_token=access_token, system_prompt=sys_prompt, api_config=api_config)


# =============================================
# Punchlist generation (for AI Summary)
# =============================================
def _align_frames_for_diff(df_source, df_dest, used_keys):
    src_u = df_source.copy(); src_u.columns = src_u.columns.str.strip().str.upper()
    dst_u = df_dest.copy();   dst_u.columns = dst_u.columns.str.strip().str.upper()

    if used_keys:
        if not all(k in src_u.columns for k in used_keys) or not all(k in dst_u.columns for k in used_keys):
            return None, None, [], lambda x: str(x)
        src_u = src_u.sort_values(by=used_keys, kind="mergesort")
        dst_u = dst_u.sort_values(by=used_keys, kind="mergesort")

        src_idx = src_u.set_index(used_keys)
        dest_idx = dst_u.set_index(used_keys)
        s, d = src_idx.align(dest_idx, join="inner", axis=0)
        s, d = s.align(d, join="inner", axis=1)

        def rk(x):
            if isinstance(x, tuple):
                return " | ".join([str(v) for v in x])
            return str(x)
        return s, d, list(s.columns.intersection(d.columns)), rk
    else:
        all_cols = sorted(set(src_u.columns) | set(dst_u.columns))
        s = src_u.reindex(columns=all_cols)
        d = dst_u.reindex(columns=all_cols)
        min_len = min(len(s), len(d))
        s = s.iloc[:min_len].reset_index(drop=True)
        d = d.iloc[:min_len].reset_index(drop=True)
        common = list(s.columns.intersection(d.columns))
        return s, d, common, (lambda x: str(x))


def build_correction_punchlist(df_source, df_dest, used_keys, treat_source_as_truth=True, limit_preview_rows=20):
    s, d, common_cols, rk = _align_frames_for_diff(df_source, df_dest, used_keys)
    if s is None or d is None or not common_cols:
        return pd.DataFrame(), "_No aligned comparable columns to produce a punchlist._"

    diff_mask = s[common_cols].ne(d[common_cols])
    if diff_mask.values.sum() == 0:
        return pd.DataFrame(), "_No differences detected — punchlist empty._"

    rows = []
    for col in common_cols:
        changed_idx = diff_mask.index[diff_mask[col]]
        if len(changed_idx) == 0:
            continue
        for idx in changed_idx:
            src_val = s.at[idx, col] if col in s.columns else None
            dst_val = d.at[idx, col] if col in d.columns else None
            action = "Update Destination to Source" if treat_source_as_truth else "Review/Resolve"
            rows.append({
                "Row_Key": rk(idx),
                "Column": col,
                "Current_Value_Dest": dst_val,
                "Expected_Value_Source": src_val,
                "Suggested_Action": action
            })

    punchlist_df = pd.DataFrame(rows).sort_values(by=["Row_Key", "Column"], kind="mergesort").reset_index(drop=True)

    preview_rows = min(limit_preview_rows, len(punchlist_df))
    buf = io.StringIO()
    if preview_rows == 0:
        md = "_Differences exist but punchlist could not be previewed._"
    else:
        buf.write("Row_Key | Column | Destination → Source | Action\n")
        buf.write(":--|:--|:--|:--\n")
        for i in range(preview_rows):
            r = punchlist_df.iloc[i]
            buf.write(f"{r['Row_Key']} | {r['Column']} | {r['Current_Value_Dest']} → {r['Expected_Value_Source']} | {r['Suggested_Action']}\n")
        if len(punchlist_df) > preview_rows:
            buf.write(f"\n_... and {len(punchlist_df) - preview_rows} more rows_\n")
        md = buf.getvalue()
    return punchlist_df, md


# =============================================
# Initialize session state
# =============================================
def _init_state_key(key, default):
    if key not in st.session_state:
        st.session_state[key] = default

for k, v in {
    "source_file": None,
    "dest_file": None,
    "source_connected": False,
    "destination_connected": False,
    "src_table": "",
    "dest_table": "",
    "src_sqlserver_query": "",
    "dest_sqlserver_query": "",
    "src_datafile": "",
    "dest_datafile": "",
    "src_table_sf": "",
    "src_sf_src_query": "",
    "dest_table_sf": "",
    "dest_sf_src_query": "",
    "src_conn": None,
    "dest_conn": None,
    "src_server": None,
    "src_user": None,
    "src_pwd": None,
    "src_db": None,
    "src_wh": None,
    "src_account": None,
    "src_role": None,
    "src_schema": None,
    "dest_server": None,
    "dest_user": None,
    "dest_pwd": None,
    "dest_db": None,
    "dest_wh": None,
    "dest_account": None,
    "dest_role": None,
    "dest_schema": None,
    "input_mode_src": None,
    "input_mode_dest": None,
}.items():
    _init_state_key(k, v)


def init_state():
    defaults = {
        "analysis_ready": False,
        "show_detailed_differences": True,
        "ai_summary": "",
        "stacked_diff": None,
        "common_cols": None,
        "all_cols": None,
        "mismatch_mask": None,
        "agg_df": None,
        "agg_df_json": None,
        "formatted_source_df": "",
        "formatted_dest_df": "",
        "key_columns": [],          # ordered alignment keys (pre-analysis)
        "columns_reordered": False,
        "used_key_columns": [],     # keys actually used in analysis
        "compare_mode": "unknown",  # 'no_common' | 'auto_one' | 'auto_two' | 'ask'

        # Business Rule state
        "show_rule_editor": False,
        "business_rule_text": "",
        "generated_rule_code": "",
        "rule_run_output": None,
        "rule_run_error": "",
        "rule_run_log": "",
        "direct_ai_plan": None,
        "direct_ai_results": None,

        # AI Summary helpers
        "punchlist_df": None,
        "treat_source_as_truth": True,
        "ai_on_agg_answer": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# =====================================================================
# UI: choose Source & Destination systems + Upload
# =====================================================================
init_state()
st.session_state.ai_summary = ""  # clean slate per run

c1, c2 = st.columns(2)
with c1:
    st.markdown("### Source System")
    source_option = st.radio(
        "Choose Source",
        ["Excel File", "SQL Server", "Snowflake"],   # label kept; widget accepts CSV too
        index=0,
        horizontal=True,
        key="source_radio",
    )
with c2:
    st.markdown("### Destination System")
    destination_option = st.radio(
        "Choose Destination",
        ["Excel File", "SQL Server", "Snowflake"],
        index=0,
        horizontal=True,
        key="destination_radio",
    )

st.markdown("---")

# Uploaders (accept CSV + Excel)
if source_option == "Excel File":
    st.session_state.src_datafile = st.file_uploader(
        "📂 Upload Source File", type=["csv", "xlsx", "xls"], key="file1"
    )
    st.session_state["source_connected"] = True

if destination_option == "Excel File":
    st.session_state.dest_datafile = st.file_uploader(
        "📂 Upload Destination File", type=["csv", "xlsx", "xls"], key="file2"
    )
    st.session_state["destination_connected"] = True


# =====================================================================
# Read uploaded files into DataFrames
# =====================================================================
colA, colB = st.columns(2)

with colA:
    if st.session_state.get("source_connected"):
        df_source = None
        if source_option == "Excel File" and st.session_state.src_datafile is not None:
            try:
                df_source = read_any_tabular(st.session_state.src_datafile)
            except Exception:
                st.info("ℹ️ Please upload both datasets for data validation and analysis.")
                st.stop()
        if df_source is not None:
            st.session_state["source_file"] = df_source

with colB:
    if st.session_state.get("destination_connected"):
        df_dest = None
        if destination_option == "Excel File" and st.session_state.dest_datafile is not None:
            try:
                df_dest = read_any_tabular(st.session_state.dest_datafile)
            except Exception:
                st.info("ℹ️ Please upload both datasets for data validation and analysis.")
                st.stop()
        if df_dest is not None:
            st.session_state["dest_file"] = df_dest

# Pulled once for downstream logic
df_source = st.session_state.get("source_file")
df_dest = st.session_state.get("dest_file")
src_table = st.session_state.get("src_table")
dest_table = st.session_state.get("dest_table")

# Friendly pre-upload message
if (df_source is None) or (df_dest is None):
    st.info("ℹ️ Please upload both datasets for data validation and analysis.")
    st.stop()


# =====================================================================
# Pre-analysis: detect common columns & apply ask/auto rules
# =====================================================================
if (df_source is not None) and (df_dest is not None) and (not df_source.empty) and (not df_dest.empty):
    tmp_src_cols = pd.Index(df_source.columns).str.strip().str.upper()
    tmp_dest_cols = pd.Index(df_dest.columns).str.strip().str.upper()

    if list(tmp_src_cols) != list(tmp_dest_cols):
        st.info("Detected different column orders between files. Columns will be re-ordered A→Z before comparison.")

    common_cols_tmp = sorted(list(tmp_src_cols.intersection(tmp_dest_cols)))

    if not st.session_state.get("analysis_ready"):
        st.session_state.common_cols = common_cols_tmp

    if len(common_cols_tmp) == 0:
        st.session_state.compare_mode = "no_common"
        st.info("No common columns detected. The app will **not** run row comparison. You can still use other tabs.")
        st.session_state.key_columns = []
        comparison_allowed = False
    elif len(common_cols_tmp) == 1:
        st.session_state.compare_mode = "auto_one"
        st.success("✅ 1 common column found. It will be auto-used as the primary key.")
        st.session_state.key_columns = [common_cols_tmp[0]]
        comparison_allowed = True
    elif len(common_cols_tmp) == 2:
        st.session_state.compare_mode = "auto_two"
        st.success("✅ 2 common columns found. They will be auto-used as composite primary keys.")
        st.session_state.key_columns = common_cols_tmp[:]
        comparison_allowed = True
    else:
        st.session_state.compare_mode = "ask"
        st.success(f"✅ {len(common_cols_tmp)} common columns found. Select primary key(s) in priority order.")
        st.markdown(
            "🔑 **Select column(s) to align rows for accurate comparison**  \n"
            "_Order matters: first = primary key, then secondary, etc._"
        )
        st.session_state.key_columns = st.multiselect(
            "Choose row alignment columns (in priority order)",
            options=common_cols_tmp,
            default=st.session_state.get("key_columns", []),
            key="key_columns_selector"
        )
        comparison_allowed = True

    # Pre-analysis Data Summary
    s_rows, s_cols = df_source.shape
    d_rows, d_cols = df_dest.shape
    st.markdown("### 📋 Data Summary (pre‑analysis)")
    cc1, cc2, cc3 = st.columns([1, 1, 2])
    with cc1:
        st.markdown("**Source**")
        st.write(f"Rows: **{s_rows}**")
        st.write(f"Columns: **{s_cols}**")
    with cc2:
        st.markdown("**Destination**")
        st.write(f"Rows: **{d_rows}**")
        st.write(f"Columns: **{d_cols}**")
    with cc3:
        keys_preview = st.session_state.get("key_columns") or []
        st.write("**Alignment keys**")
        if st.session_state.compare_mode == "no_common":
            st.write("_No key alignment (positional comparison disabled)_")
        else:
            st.write(" → ".join(keys_preview) if keys_preview else "_Not selected yet_")


# =====================================================================
# Start Analysis
# =====================================================================
if (df_source is not None) and (df_dest is not None) and (not df_source.empty) and (not df_dest.empty):
    mode = st.session_state.get("compare_mode", "unknown")
    if mode != "no_common":
        st.write("✅ Both datasets uploaded successfully! Let's start analysis.")
        if st.button("Start Analysis", key="btn_start_analysis"):
            try:
                src_u = df_source.copy()
                dest_u = df_dest.copy()
                src_u.columns = src_u.columns.str.strip().str.upper()
                dest_u.columns = dest_u.columns.str.strip().str.upper()

                # Standardize column order if needed
                if list(src_u.columns) != list(dest_u.columns):
                    src_u = src_u.reindex(columns=sorted(src_u.columns))
                    dest_u = dest_u.reindex(columns=sorted(dest_u.columns))
                    st.session_state.columns_reordered = True
                else:
                    st.session_state.columns_reordered = False

                common_cols_all = sorted(list(src_u.columns.intersection(dest_u.columns)))
                st.session_state.common_cols = common_cols_all

                key_columns = st.session_state.get("key_columns") or []

                if (len(common_cols_all) >= 3) and (not key_columns):
                    st.warning("⚠️ Please select at least one common column to align rows before proceeding.")
                    st.stop()

                # Align
                if key_columns:
                    if not all([(k in src_u.columns) and (k in dest_u.columns) for k in key_columns]):
                        raise ValueError("Selected primary key(s) not found in one or both files.")

                    df1_sorted = src_u.sort_values(by=key_columns, kind="mergesort").reset_index(drop=True)
                    df2_sorted = dest_u.sort_values(by=key_columns, kind="mergesort").reset_index(drop=True)

                    src_idx = df1_sorted.set_index(key_columns)
                    dest_idx = df2_sorted.set_index(key_columns)

                    src_common, dest_common = src_idx.align(dest_idx, join="inner", axis=0)
                    src_common, dest_common = src_common.align(dest_common, join="inner", axis=1)

                    st.session_state["df_source_sorted"] = df1_sorted
                    st.session_state["df_dest_sorted"] = df2_sorted
                    st.session_state["used_key_columns"] = key_columns

                    st.session_state.all_cols = sorted(set(src_idx.columns) | set(dest_idx.columns))
                    df_source_aligned = src_common
                    df_dest_aligned = dest_common
                else:
                    union_cols = sorted(set(src_u.columns) | set(dest_u.columns))
                    src_union = src_u.reindex(columns=union_cols)
                    dest_union = dest_u.reindex(columns=union_cols)
                    min_len = min(len(src_union), len(dest_union))
                    df_source_aligned = src_union.iloc[:min_len].reset_index(drop=True)
                    df_dest_aligned = dest_union.iloc[:min_len].reset_index(drop=True)

                    st.session_state["df_source_sorted"] = src_u.reset_index(drop=True)
                    st.session_state["df_dest_sorted"] = dest_u.reset_index(drop=True)
                    st.session_state["used_key_columns"] = []
                    st.session_state.all_cols = union_cols

                # Differences
                common_cols_aligned = list(df_source_aligned.columns.intersection(df_dest_aligned.columns))
                comparison_df = pd.DataFrame()
                mismatch_mask = pd.Series([], dtype=bool)

                if common_cols_aligned:
                    diff_mask = df_source_aligned[common_cols_aligned].ne(df_dest_aligned[common_cols_aligned])
                    mismatch_mask = diff_mask.any(axis=1)
                    comparison_df = df_source_aligned[common_cols_aligned].compare(
                        df_dest_aligned[common_cols_aligned], keep_shape=True, keep_equal=False
                    )
                    if not comparison_df.empty and comparison_df.isna().all().all():
                        comparison_df = pd.DataFrame()

                # Perfect match message
                if (comparison_df.empty
                    and (df_source_aligned.shape == df_dest_aligned.shape)
                    and (list(df_source_aligned.columns) == list(df_dest_aligned.columns))):
                    st.success("🎉 Data matches perfectly! No differences found.")

                st.session_state.analysis_ready = True
                st.session_state.mismatch_mask = mismatch_mask
                st.session_state.stacked_diff = comparison_df  # may be empty

                if key_columns:
                    st.info(
                        f"""
**Row Validation Configuration**
- Common columns found: {len(common_cols_aligned)}
- Alignment keys used: {", ".join(key_columns)}
- Validation mode: Ordered row comparison
"""
                    )
                else:
                    st.info(
                        f"""
**Row Validation Configuration**
- Common columns found: {len(common_cols_aligned)}
- Alignment keys used: (none; positional comparison)
- Validation mode: Positional row comparison
"""
                    )

                if not comparison_df.empty:
                    if st.session_state.get("columns_reordered"):
                        st.caption("ℹ️ Columns were re-ordered A→Z to standardize comparison.")
                    if key_columns:
                        st.caption(f"🔑 Rows aligned using ordered keys: **{', '.join(key_columns)}**")
                    st.subheader("Differences (preview)")
                    st.dataframe(comparison_df.head(50), use_container_width=True)

            except Exception as e:
                st.error(f"Comparison failed: {e}")


# =====================================================================
# Render analysis once ready
# =====================================================================
if st.session_state.analysis_ready:
    st.markdown("<h3 style='color:#002060;'>Dataset Summary</h3>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)

    with c1:
        if st.session_state.get("src_datafile") is not None:
            st.write(f"**{st.session_state.src_datafile.name} : Info**")
        st.write(f"Rows: {df_source.shape[0]}")
        st.write(f"Columns: {df_source.shape[1]}")

    with c2:
        if st.session_state.get("dest_datafile") is not None:
            st.markdown(f"**{st.session_state.dest_datafile.name} : Info**")
        st.write(f"Rows: {df_dest.shape[0]}")
        st.write(f"Columns: {df_dest.shape[1]}")

    st.markdown("<h3 style='color:#002060;'>Analysis Result</h3>", unsafe_allow_html=True)

    _src_sorted = st.session_state.get("df_source_sorted")
    _dest_sorted = st.session_state.get("df_dest_sorted")

    src_u = (_src_sorted if _src_sorted is not None else df_source).copy()
    dest_u = (_dest_sorted if _dest_sorted is not None else df_dest).copy()
    src_u.columns = src_u.columns.str.strip().str.upper()
    dest_u.columns = dest_u.columns.str.strip().str.upper()

    key_columns_used = st.session_state.get("used_key_columns") or []
    all_cols = st.session_state.get("all_cols") or sorted(set(src_u.columns) | set(dest_u.columns))

    if key_columns_used:
        src_u = src_u.sort_values(by=key_columns_used, kind="mergesort")
        dest_u = dest_u.sort_values(by=key_columns_used, kind="mergesort")

        src_idx = src_u.set_index(key_columns_used)
        dest_idx = dest_u.set_index(key_columns_used)

        src_union, dest_union = src_idx.align(dest_idx, join="inner", axis=0)
        src_union, dest_union = src_union.align(dest_union, join="outer", axis=1)

        df_source_aligned = src_union
        df_dest_aligned = dest_union
    else:
        union_cols = all_cols
        src_union = src_u.reindex(columns=union_cols)
        dest_union = dest_u.reindex(columns=union_cols)
        min_len = min(len(src_union), len(dest_union))
        df_source_aligned = src_union.iloc[:min_len].reset_index(drop=True)
        df_dest_aligned = dest_union.iloc[:min_len].reset_index(drop=True)

    common_cols_aligned = list(df_source_aligned.columns.intersection(df_dest_aligned.columns))

    if not common_cols_aligned:
        st.warning("⚠️ No common data columns found between the two files. Showing both datasets for manual inspection.")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("<b style='color:#00BFFF;'>📄 File 1 Preview</b>", unsafe_allow_html=True)
            st.dataframe(df_source.head(6), use_container_width=True)
        with cc2:
            st.markdown("<b style='color:#7CFC00;'>📄 File 2 Preview</b>", unsafe_allow_html=True)
            st.dataframe(df_dest.head(6), use_container_width=True)
    else:
        diff_mask = df_source_aligned[common_cols_aligned].ne(df_dest_aligned[common_cols_aligned])
        mismatch_mask = diff_mask.any(axis=1)
        mismatch_columns = [c for c in common_cols_aligned if diff_mask[c].any()]

        if mismatch_mask.sum() == 0:
            st.success("✅ No row-level mismatches found.")
        else:
            st.markdown(
                f"<span style='color:#E72D3F;'>Row-level mismatches found in {mismatch_mask.sum()} rows.</span>",
                unsafe_allow_html=True
            )
            st.markdown(
                f"<span style='color:#E72D3F;'>Data mismatched columns:</span> "
                f"<code>{', '.join(mismatch_columns)}</code>",
                unsafe_allow_html=True
            )

        colX, colY = st.columns(2)
        with colX:
            if st.button("Show Detailed Differences", key="btn_show_diffs"):
                src_mis = df_source_aligned[mismatch_mask].copy()
                dest_mis = df_dest_aligned[mismatch_mask].copy()
                src_mis["File_Indicator"] = "File 1"
                dest_mis["File_Indicator"] = "File 2"

                def build_key_label(idx_row):
                    if isinstance(idx_row, tuple):
                        return " | ".join([str(v) for v in idx_row])
                    return str(idx_row)

                # Index label works for both key-based and positional modes
                if isinstance(src_mis.index, pd.MultiIndex) or isinstance(dest_mis.index, pd.MultiIndex):
                    src_mis["Row_Key"] = src_mis.index.map(build_key_label)
                    dest_mis["Row_Key"] = dest_mis.index.map(build_key_label)
                else:
                    src_mis["Row_Key"] = src_mis.index.astype(str)
                    dest_mis["Row_Key"] = dest_mis.index.astype(str)

                stacked = pd.concat([src_mis, dest_mis], axis=0)
                cols = ["Row_Key", "File_Indicator"] + [
                    c for c in stacked.columns if c not in ["Row_Key", "File_Indicator"]
                ]
                stacked = stacked[cols].reset_index(drop=True)

                st.session_state.stacked_diff = stacked
                st.session_state.show_detailed_differences = True

        with colY:
            if st.button("Hide Detailed Differences", key="btn_hide_diffs"):
                st.session_state.show_detailed_differences = False

        if st.session_state.show_detailed_differences and st.session_state.stacked_diff is not None:

            def highlight_differences(x: pd.DataFrame):
                styles = pd.DataFrame("", index=x.index, columns=x.columns)
                grouped = x.groupby("Row_Key", dropna=False)
                for _, group in grouped:
                    if len(group) == 2:
                        f1 = group[group["File_Indicator"] == "File 1"]
                        f2 = group[group["File_Indicator"] == "File 2"]
                        for c in common_cols_aligned:
                            if not f1.empty and not f2.empty:
                                v1 = f1[c].iloc[0] if c in f1.columns else None
                                v2 = f2[c].iloc[0] if c in f2.columns else None
                                if pd.isna(v1) and pd.isna(v2):
                                    continue
                                if v1 != v2:
                                    styles.loc[group.index, c] = "background-color:#fccccb;"
                        if not f1.empty:
                            styles.loc[f1.index, "File_Indicator"] = "background-color:#001F3F;color:white;"
                        if not f2.empty:
                            styles.loc[f2.index, "File_Indicator"] = "background-color:#013220;color:white;"
                return styles

            st.subheader("Detailed Differences")
            st.dataframe(
                st.session_state.stacked_diff.style.apply(highlight_differences, axis=None),
                use_container_width=True
            )

            excel_bytes = to_excel_bytes(st.session_state.stacked_diff)
            st.download_button(
                label="Download Validation Report",
                data=excel_bytes,
                file_name="Data_Validation_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_stacked_xlsx",
            )


# =====================================================================
# Tabs (Data Preview, Aggregation, AI Summary, Business Rules)
# =====================================================================
if (df_source is not None) and (df_dest is not None) and (not df_source.empty) and (not df_dest.empty):
    st.markdown('<div class="card">', unsafe_allow_html=True)

    mode = st.session_state.get("compare_mode", "unknown")
    if mode == "no_common":
        tab1, tab2, tab4 = st.tabs(["📊 Data Preview", "🧾 Metadata & Aggregation", "🧠 Business Rules"])
    else:
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Data Preview", "🧾 Metadata & Aggregation", "🤖 AI-Powered Summary", "🧠 Business Rules"])

    # ======== DATA PREVIEW ========
    with tab1:
        st.write(f"**{st.session_state.src_datafile.name if st.session_state.get('src_datafile') else 'Source'} : Data Preview**")
        st.dataframe(df_source.head(), use_container_width=True)
        st.write(f"**{st.session_state.dest_datafile.name if st.session_state.get('dest_datafile') else 'Destination'} : Data Preview**")
        st.dataframe(df_dest.head(), use_container_width=True)

    # ======== METADATA & AGGREGATION ========
    with tab2:
        st.markdown("### 📈 Data Aggregation")

        src_u = df_source.copy(); src_u.columns = src_u.columns.str.strip().str.upper()
        dest_u = df_dest.copy();  dest_u.columns = df_dest.columns.str.strip().str.upper()

        common_cols = st.session_state.get("common_cols") or list(src_u.columns.intersection(dest_u.columns))

        numeric_common = [
            c for c in common_cols
            if pd.api.types.is_numeric_dtype(src_u[c]) and pd.api.types.is_numeric_dtype(dest_u[c])
            and not (pd.api.types.is_bool_dtype(src_u[c]) or pd.api.types.is_bool_dtype(dest_u[c]))
        ]
        categorical_common = [
            c for c in common_cols
            if (
                pd.api.types.is_object_dtype(src_u[c])
                or pd.api.types.is_categorical_dtype(src_u[c])
                or pd.api.types.is_bool_dtype(src_u[c])
            ) and (
                pd.api.types.is_object_dtype(dest_u[c])
                or pd.api.types.is_categorical_dtype(dest_u[c])
                or pd.api.types.is_bool_dtype(dest_u[c])
            )
        ]

        if len(categorical_common) == 0 or len(numeric_common) == 0:
            st.info("No suitable common categorical or numeric columns were detected for aggregation.")
        else:
            cat_col = st.selectbox(
                "Select a categorical column (group by):",
                options=sorted(categorical_common),
                key="agg_cat_col",
            )
            num_col = st.selectbox(
                "Select a numeric column (aggregate):",
                options=sorted(numeric_common),
                key="agg_num_col",
            )

            agg_options = ["Mean (avg)", "Sum", "Min", "Max", "Count", "Distinct count", "Median", "Std-dev"]
            agg_map = {
                "Mean (avg)": "mean",
                "Sum": "sum",
                "Min": "min",
                "Max": "max",
                "Count": "count",
                "Distinct count": "nunique",
                "Median": "median",
                "Std-dev": "std",
            }

            selected_aggs_labels = st.multiselect(
                "Choose aggregations:",
                options=agg_options,
                default=["Mean (avg)", "Sum"],
                key="agg_funcs",
            )
            selected_aggs = [agg_map[a] for a in selected_aggs_labels]

            sort_choice = st.selectbox(
                "Sort comparison by (optional):",
                options=["(No sorting)"]
                        + [f"{num_col}_{f}" for f in selected_aggs]
                        + [f"{num_col}_{f}_src" for f in selected_aggs]
                        + [f"{num_col}_{f}_dest" for f in selected_aggs]
                        + [f"{num_col}_{f}_delta" for f in selected_aggs],
                index=0,
                key="agg_sort_col",
            )
            sort_asc = st.checkbox("Sort ascending", value=False, key="agg_sort_asc")

            if st.button("Compute aggregation", key="btn_compute_agg"):
                if not selected_aggs:
                    st.warning("Please choose at least one aggregation function.")
                else:
                    def do_groupby(df_solo):
                        g = df_solo.groupby(cat_col)[num_col].agg(selected_aggs).reset_index()
                        if isinstance(g.columns, pd.MultiIndex):
                            g.columns = [
                                c if isinstance(c, str) else (c[0] if c[1] == "" else f"{c[0]}_{c[1]}")
                                for c in g.columns
                            ]
                        else:
                            if len(selected_aggs) == 1:
                                g = g.rename(columns={num_col: f"{num_col}_{selected_aggs[0]}"})
                        return g

                    src_agg = do_groupby(src_u).copy()
                    dest_agg = do_groupby(dest_u).copy()

                    value_cols = [c for c in src_agg.columns if c != cat_col]
                    src_renamed = src_agg.rename(columns={c: f"{c}_src" for c in value_cols})
                    dest_renamed = dest_agg.rename(columns={c: f"{c}_dest" for c in value_cols})

                    comp = pd.merge(src_renamed, dest_renamed, on=cat_col, how="outer")
                    for c in value_cols:
                        src_c = f"{c}_src"
                        dest_c = f"{c}_dest"
                        if src_c in comp.columns and dest_c in comp.columns:
                            comp[f"{c}_delta"] = comp[src_c] - comp[dest_c]

                    if sort_choice != "(No sorting)" and sort_choice in comp.columns:
                        comp = comp.sort_values(by=sort_choice, ascending=sort_asc, kind="mergesort")

                    st.session_state.agg_df = comp

                    st.markdown("#### 📊 Source Aggregation")
                    st.dataframe(src_agg, use_container_width=True)

                    st.markdown("#### 📊 Destination Aggregation")
                    st.dataframe(dest_agg, use_container_width=True)

                    st.markdown("#### 🔀 Comparison (joined)")
                    st.dataframe(comp, use_container_width=True)

                    xlsx_bytes = to_excel_bytes_multi({
                        "Source Aggregation": src_agg,
                        "Destination Aggregation": dest_agg,
                        "Comparison": comp
                    })
                    st.download_button(
                        label="Download aggregation workbook (XLSX)",
                        data=xlsx_bytes,
                        file_name=f"aggregation_{cat_col}_{num_col}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_agg_xlsx"
                    )

        # AI on aggregation (optional)
        st.markdown("### 💬 Ask AI about this comparison")
        agg_prompt = st.text_area(
            "Ask a specific question about the aggregation (optional):",
            value="Highlight top 5 groups with the largest positive delta and explain potential causes.",
            height=100,
            key="agg_ai_prompt"
        )
        if st.button("Ask AI (Aggregation Insights)", key="btn_agg_ai"):
            if not api_config.get("chat_endpoint"):
                st.error("AI chat endpoint not configured. Set CHAT_ENDPOINT, API_KEY or APPKEY in .env / config.json.")
            else:
                try:
                    access_token = generate_auth_token(api_config)
                    comp = st.session_state.get("agg_df")
                    comp_text = "(no aggregation yet)"
                    if isinstance(comp, pd.DataFrame) and not comp.empty:
                        comp_text = comp.head(50).to_string(index=False)

                    system_prompt = (
                        "You are a senior data analyst. Provide crisp, business-ready insights "
                        "based on the user's question and the comparison table. Use bullet points."
                    )
                    prompt = f"### Aggregation Comparison (sample)\n{comp_text}\n\n### Question\n{agg_prompt}"
                    ans = ask_chatgpt(
                        prompt=prompt,
                        access_token=access_token,
                        system_prompt=system_prompt,
                        api_config=api_config
                    )
                    st.session_state.ai_on_agg_answer = ans
                except Exception as e:
                    st.error(f"Failed to query AI on aggregation: {e}")

        if st.session_state.get("ai_on_agg_answer"):
            st.markdown("#### 🧠 AI Insights on Aggregation")
            st.write(st.session_state.ai_on_agg_answer)

    # ======== AI-POWERED SUMMARY ========
    if mode != "no_common":
        with tab3:
            # Prepare compact, plain-text tables for LLM prompt
            df_source_json = df_source.to_dict(orient="records")
            df_dest_json = df_dest.to_dict(orient="records")

            st.session_state.formatted_source_df = format_dataset(df_source_json)
            st.session_state.formatted_dest_df = format_dataset(df_dest_json)

            # Optional: include row-level mismatches if generated
            if st.session_state.stacked_diff is not None and isinstance(st.session_state.stacked_diff, pd.DataFrame):
                stacked_diff = st.session_state.stacked_diff
                stacked_diff_df_json = stacked_diff.to_dict(orient="records")
                st.session_state.agg_df_json = format_dataset(stacked_diff_df_json)
            else:
                st.session_state.agg_df_json = ""

            st.session_state.treat_source_as_truth = st.checkbox(
                "Treat Source as ground truth for the punchlist",
                value=True, key="punchlist_truth_flag"
            )

            if st.button("✨ Generate AI Summary", key="btn_ai_summary"):
                if not api_config.get("chat_endpoint"):
                    st.error("AI chat endpoint not configured. Set CHAT_ENDPOINT, API_KEY or APPKEY in .env / config.json.")
                else:
                    used_keys = st.session_state.get("used_key_columns") or st.session_state.get("key_columns") or []
                    stacked_df = st.session_state.stacked_diff if isinstance(st.session_state.stacked_diff, pd.DataFrame) else pd.DataFrame()
                    agg_df_ctx = st.session_state.get("agg_df")

                    mismatch_columns = []
                    try:
                        src_cols = set(df_source.columns.str.upper())
                        dst_cols = set(df_dest.columns.str.upper())
                        common_cols_now = sorted(list(src_cols.intersection(dst_cols)))
                        src_norm = df_source.copy(); src_norm.columns = src_norm.columns.str.upper()
                        dst_norm = df_dest.copy();   dst_norm.columns = dst_norm.columns.str.upper()

                        if used_keys and all(k in src_norm.columns for k in used_keys) and all(k in dst_norm.columns for k in used_keys):
                            s = src_norm.set_index([k for k in used_keys]).sort_index()
                            d = dst_norm.set_index([k for k in used_keys]).sort_index()
                            s, d = s.align(d, join="inner", axis=0)
                            s, d = s.align(d, join="inner", axis=1)
                            diff = s[common_cols_now].ne(d[common_cols_now])
                            mismatch_columns = list(diff.any(axis=0)[diff.any(axis=0)].index)
                        else:
                            common_cols_now = [c for c in common_cols_now if c in src_norm.columns and c in dst_norm.columns]
                            min_len = min(len(src_norm), len(dst_norm))
                            diff = src_norm.iloc[:min_len][common_cols_now].reset_index(drop=True).ne(
                                   dst_norm.iloc[:min_len][common_cols_now].reset_index(drop=True))
                            mismatch_columns = list(diff.any(axis=0)[diff.any(axis=0)].index)
                    except Exception:
                        mismatch_columns = []

                    auto_recs = build_recommendations(df_source, df_dest, used_keys, mismatch_columns)

                    system_prompt = """
You are an exceptional Data QA Analyst and Data Engineer.
Given comparison context between two datasets, produce a concise, executive-quality validation report in Markdown.

REQUIREMENTS:
1) Start with **Executive Summary** (3–6 bullet points).
2) Include **Key Issues & Metrics** (counts, affected columns, %).
3) Provide **Root-Cause Hypotheses** grounded in data patterns (types, nulls, trims, casing, duplicates, key integrity).
4) Provide **Recommendations (How to fix / prevent)** — concrete data-cleaning & pipeline governance steps.
5) Provide a **Data-Fix Checklist** — step-by-step actions an analyst can take (ordered, actionable).
6) Be specific: reference actual column names and, when possible, example values/patterns (but avoid dumping large tables).
7) Keep it self-contained and crisp. Use headings and bullet points.
""".strip()

                    access_token = generate_auth_token(api_config)
                    buffer = io.StringIO()
                    buffer.write("## Validation Results (Machine Context)\n")
                    buffer.write("\n### Alignment\n")
                    buffer.write(f"- Keys used: {', '.join(used_keys) if used_keys else '(none — positional)'}\n")
                    buffer.write(f"- Columns reordered A→Z: {bool(st.session_state.get('columns_reordered'))}\n")

                    buffer.write("\n### Mismatch Overview\n")
                    if len(mismatch_columns) == 0:
                        buffer.write("- No mismatched columns detected (post-alignment).\n")
                    else:
                        buffer.write(f"- Mismatched columns: {', '.join(mismatch_columns)}\n")

                    buffer.write("\n### Row-by-row Differences (sample)\n")
                    if stacked_df is None or stacked_df.empty:
                        buffer.write("- No differing rows sample available.\n")
                    else:
                        sample_rows = stacked_df.head(10).to_string(index=False)
                        buffer.write(sample_rows + "\n")

                    buffer.write("\n### Aggregation Comparison (sample)\n")
                    if agg_df_ctx is not None and isinstance(agg_df_ctx, pd.DataFrame) and not agg_df_ctx.empty:
                        agg_preview = agg_df_ctx.head(15).to_string(index=False)
                        buffer.write(agg_preview + "\n")
                    else:
                        buffer.write("- Not computed / empty.\n")

                    buffer.write("\n### Source (sample)\n")
                    buffer.write(st.session_state.formatted_source_df[:3000])

                    buffer.write("\n\n### Destination (sample)\n")
                    buffer.write(st.session_state.formatted_dest_df[:3000])

                    if auto_recs:
                        buffer.write("\n\n### Auto Recommendations (heuristics)\n")
                        for r in auto_recs:
                            buffer.write(f"- {r}\n")

                    validation_summary = buffer.getvalue()

                    with st.spinner("Generating AI summary..."):
                        ai_summary_text = ask_chatgpt(
                            prompt=validation_summary,
                            access_token=access_token,
                            system_prompt=system_prompt,
                            api_config=api_config
                        )

                    # Append Minimal Data Correction Punchlist
                    treat_truth = st.session_state.get("treat_source_as_truth", True)
                    punch_df, md_preview = build_correction_punchlist(
                        df_source, df_dest, used_keys, treat_source_as_truth=treat_truth, limit_preview_rows=20
                    )
                    st.session_state.punchlist_df = punch_df

                    punchlist_section = "\n\n---\n### Data Correction Punchlist (Auto‑generated)\n"
                    punchlist_section += f"_Assumption: {'Source is ground truth' if treat_truth else 'Manual review required'}._\n\n"
                    punchlist_section += md_preview

                    st.session_state.ai_summary = ai_summary_text + punchlist_section

            # Render and download AI summary
            if st.session_state.get("ai_summary"):
                st.subheader("📝 AI-Generated Summary + Recommendations")
                used_keys = st.session_state.get("used_key_columns") or st.session_state.get("key_columns") or []

                mismatch_columns = []
                try:
                    src_norm = df_source.copy(); src_norm.columns = src_norm.columns.str.upper()
                    dst_norm = df_dest.copy();   dst_norm.columns = dst_norm.columns.str.upper()
                    common_cols_now = list(set(src_norm.columns).intersection(set(dst_norm.columns)))
                    if used_keys and all(k in src_norm.columns for k in used_keys) and all(k in dst_norm.columns for k in used_keys):
                        s = src_norm.set_index(used_keys).sort_index()
                        d = dst_norm.set_index(used_keys).sort_index()
                        s, d = s.align(d, join="inner", axis=0)
                        s, d = s.align(d, join="inner", axis=1)
                        diff = s[common_cols_now].ne(d[common_cols_now])
                        mismatch_columns = list(diff.any(axis=0)[diff.any(axis=0)].index)
                    else:
                        min_len = min(len(src_norm), len(dst_norm))
                        diff = src_norm.iloc[:min_len][common_cols_now].reset_index(drop=True).ne(
                               dst_norm.iloc[:min_len][common_cols_now].reset_index(drop=True))
                        mismatch_columns = list(diff.any(axis=0)[diff.any(axis=0)].index)
                except Exception:
                    pass

                auto_recs_view = build_recommendations(df_source, df_dest, used_keys, mismatch_columns)
                if auto_recs_view:
                    st.markdown("#### 🧩 Auto Recommendations (Quick Heuristics)")
                    st.markdown("\n".join([f"- {r}" for r in auto_recs_view]))

                # AI report with appended punchlist
                st.write(st.session_state.ai_summary)

                st.download_button(
                    "⬇️ Download AI Validation Summary",
                    data=st.session_state.ai_summary or "No summary generated.",
                    file_name="AI_validation_Summary.txt",
                    key="download_ai_txt"
                )

                # Full Punchlist Table + Download
                if isinstance(st.session_state.get("punchlist_df"), pd.DataFrame) and not st.session_state.punchlist_df.empty:
                    st.markdown("#### 📌 Full Data Correction Punchlist (table)")
                    st.dataframe(st.session_state.punchlist_df, use_container_width=True)
                    try:
                        xlsx_bytes = to_excel_bytes(st.session_state.punchlist_df)
                        st.download_button(
                            label="Download Punchlist (XLSX)",
                            data=xlsx_bytes,
                            file_name="Data_Correction_Punchlist.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="download_punchlist_xlsx"
                        )
                    except Exception:
                        pass

    # ======== BUSINESS RULES ========
    # ======== BUSINESS RULES (auto-run backend python) ========
    with tab4:
        st.markdown("### 🧠 Business Rule Validation")
        st.caption("Enter a natural-language rule. The system will generate Python in the backend, run it, and display only the results.")

        # Text area for rule
        st.session_state.business_rule_text = st.text_area(
            "Business Rule (natural language)",
            value=st.session_state.get("business_rule_text", ""),
            height=140,
            key="business_rule_input",
            placeholder="Example: For each Brand, Destination.FastCharge_KmH must be within ±5% of Source.FastCharge_KmH."
        )

        used_keys = (
            st.session_state.get("used_key_columns")
            or st.session_state.get("key_columns")
            or []
        )

        # === Single clean button ===
        if st.button("Run Business Rule", key="btn_br_auto_run"):
            if not api_config.get("chat_endpoint"):
                st.error("AI endpoint not configured.")
            else:
                try:
                    with st.spinner("Generating & executing the rule..."):
                        # Send original rule text directly — LLM gets schema context anyway
                        code_text = generate_python_from_rule(
                            st.session_state.business_rule_text,  # ← original, not normalized
                            df_source,
                            df_dest,
                            api_config
                        )
                       

                        # 2) Execute python dynamically (backend)
                       
                    result, logs = run_generated_validation(
                        code_text,
                        df_source,
                        df_dest
                    )


                    # === Show result ONLY ===
                    st.subheader("✅ Business Rule Result")
                    st.write(f"**Status:** {result.get('status')}")
                    st.write(result.get("message", ""))

                    vdf = result.get("violations_df")
                    if isinstance(vdf, pd.DataFrame) and not vdf.empty:
                        st.markdown("#### ❌ Non-compliant Rows Found")
                        st.dataframe(vdf, use_container_width=True)
                        st.download_button(
                            "Download Violations (XLSX)",
                            to_excel_bytes(vdf),
                            "BusinessRule_Violations.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="download_violations"
                        )
                    else:
                        st.success("✅ All rows comply with the business rule.")

                except Exception as e:
                    st.error(f"Business rule failed: {e}")
import os
import re
import unicodedata
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import date

import pandas as pd
import pdfplumber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STD_COLS = [
    "N.", "Descripción Residuo", "Código principal", "Código secundario", "Lista A",
    "Peligrosidad", "E. físico", "Contenedor", "Estado del Residuo", "Cantidad (Kg)",
]
META_COLS = [
    "Instalación", "Empresa destinataria", "FechaDeclaración", "Mes", "Año", "Archivo", "Ruta",
    "DEFRA_English", "DEFRA_Español", "Clasificación DEFRA",
]
MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
STREAM_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 1,
    "min_words_horizontal": 1,
    "intersection_tolerance": 3,
}


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _norm(s: str) -> str:
    return _strip_accents(_clean_spaces(s)).lower()


def _clean_row(row: List[Optional[str]]) -> List[str]:
    return [_clean_spaces(c or "") for c in row]


def clasificar_defra(descripcion_residuo: str) -> str:
    d = _norm(descripcion_residuo)
    if not d:
        return "Commercial and industrial waste"
    if any(k in d for k in ["pila", "bateria", "baterias"]):
        return "Batteries"
    if "refrigerante" in d or "fridges" in d or "freezer" in d:
        return "WEEE - fridges and freezers"
    if any(k in d for k in ["tubo fluorescente", "fluorescente"]):
        return "WEEE - large"
    if any(k in d for k in ["toner", "tonner", "cartridge", "impresora"]):
        return "WEEE - small"
    if any(k in d for k in ["weee", "electronico", "electronicos", "equipo electrico", "equipos electricos", "chatarra electronica"]):
        return "WEEE - mixed"
    if any(k in d for k in ["vidrio", "vidrios", "ampolleta", "ampolletas", "uv"]):
        return "Glass"
    if any(k in d for k in ["aceite", "lubricante", "lubricantes", "petroleo", "hidrocarburo", "hidrocarburos", "liquido contaminado", "contaminado con amoniaco"]):
        return "Mineral oil"
    if any(k in d for k in ["envase", "envases", "bidon", "bidones", "balde", "baldes", "tambor", "tambores"]):
        if any(k in d for k in ["bidon", "bidones", "balde", "baldes", "tambor", "tambores"]):
            return "Plastics: HDPE (incl. forming)"
        return "Plastics: average plastics"
    if "bolsa" in d or "bolsas" in d:
        return "Plastics: average plastic film"
    if "lata" in d or "latas" in d or "spray" in d or "aerosol" in d:
        return "Metal: mixed cans"
    if any(k in d for k in ["filtro", "filtros", "mascara", "mascarilla", "textil", "textiles", "impregnado", "cortopunzante", "corto punzante", "laboratorio", "quimico", "quimicos", "vencido", "vencidos", "detergente", "hipoclorito", "acido"]):
        return "Commercial and industrial waste"
    if "alimento" in d or "comida" in d or "food" in d:
        return "Organic: food and drink waste"
    return "Commercial and industrial waste"


SIDREP_CODE_TO_DEFRA_EN: Dict[str, str] = {
    # Lista I
    "I.1": "Commercial and industrial waste",
    "I.2": "Commercial and industrial waste",
    "I.3": "Commercial and industrial waste",
    "I.4": "Commercial and industrial waste",
    "I.5": "Wood",
    "I.6": "Commercial and industrial waste",
    "I.7": "Commercial and industrial waste",
    "I.8": "Mineral oil",
    "I.9": "Mineral oil",
    "I.10": "Commercial and industrial waste",
    "I.11": "Asphalt",
    "I.12": "Commercial and industrial waste",
    "I.13": "Commercial and industrial waste",
    "I.14": "Commercial and industrial waste",
    "I.15": "Commercial and industrial waste",
    "I.16": "Commercial and industrial waste",
    "I.17": "Metals",
    "I.18": "Commercial and industrial waste",
    # Lista II
    "II.1": "Commercial and industrial waste",
    "II.2": "Commercial and industrial waste",
    "II.3": "Commercial and industrial waste",
    "II.4": "Metals",
    "II.5": "Metals",
    "II.6": "Commercial and industrial waste",
    "II.7": "Commercial and industrial waste",
    "II.8": "Commercial and industrial waste",
    "II.9": "Commercial and industrial waste",
    "II.10": "Commercial and industrial waste",
    "II.11": "Commercial and industrial waste",
    "II.12": "Commercial and industrial waste",
    "II.13": "Batteries",
    "II.14": "Commercial and industrial waste",
    "II.15": "Commercial and industrial waste",
    "II.16": "Commercial and industrial waste",
    "II.17": "Commercial and industrial waste",
    "II.18": "Asbestos",
    "II.19": "Commercial and industrial waste",
    "II.20": "Commercial and industrial waste",
    "II.21": "Commercial and industrial waste",
    "II.22": "Commercial and industrial waste",
    "II.23": "Commercial and industrial waste",
    "II.24": "Commercial and industrial waste",
    "II.25": "Commercial and industrial waste",
    "II.26": "Commercial and industrial waste",
    "II.27": "Commercial and industrial waste",
    # Lista III
    "III.1": "Commercial and industrial waste",
    "III.2": "Commercial and industrial waste",
    "III.3": "Household residual waste",
    "III.4": "Soils",
}

SIDREP_CODE_TO_DEFRA_ES: Dict[str, str] = {
    "Commercial and industrial waste": "Residuos comerciales e industriales",
    "Wood": "Madera",
    "Mineral oil": "Aceite mineral",
    "Asphalt": "Asfalto",
    "Metals": "Metales",
    "Batteries": "Baterías",
    "Asbestos": "Asbesto",
    "Household residual waste": "Residuos domiciliarios residuales",
    "Soils": "Suelos",
}


def _normalize_sidrep_code(codigo_principal: str) -> str:
    code = _clean_spaces(codigo_principal).upper().replace(" ", "")
    m = re.match(r"^(I{1,3})\.(\d{1,2})$", code)
    if not m:
        return ""
    return f"{m.group(1)}.{int(m.group(2))}"


def clasificar_defra_sidrep(codigo_principal: str) -> Tuple[str, str]:
    code = _normalize_sidrep_code(codigo_principal)
    default_en = "Commercial and industrial waste"
    default_es = "Residuos comerciales e industriales"
    if not code:
        return default_en, default_es
    defra_en = SIDREP_CODE_TO_DEFRA_EN.get(code, default_en)
    defra_es = SIDREP_CODE_TO_DEFRA_ES.get(defra_en, default_es)
    return defra_en, defra_es


def extract_empresa_destinataria(full_text: str) -> str:
    if not full_text:
        return ""
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    for i, ln in enumerate(lines):
        nln = _norm(ln)
        if nln.startswith("empresa destinataria"):
            m = re.match(r"(?i)^empresa destinataria\s*:?\s*(.+)$", ln)
            if m and _clean_spaces(m.group(1)):
                return _clean_spaces(m.group(1))
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if not _norm(nxt).startswith(("rut", "direccion", "comuna", "fecha", "empresa", "transportista", "gestor", "destinataria")):
                    return _clean_spaces(nxt)
    m2 = re.search(r"(?is)empresa destinataria\s*:?\s*(.+?)(?:\n|rut\s*:|rut\b|direccion\b|comuna\b|fecha\b)", full_text)
    return _clean_spaces(m2.group(1)) if m2 else ""


def _looks_like_detalle_table(header_row: List[str]) -> bool:
    joined = " ".join(_norm(c) for c in header_row if c)
    return ("descripcion" in joined and "residuo" in joined and "cantidad" in joined)


def _standardize_headers(header_row: List[str]) -> Dict[int, str]:
    idx_to_name: Dict[int, str] = {}
    for i, h in enumerate(header_row):
        t = _norm(h)
        if t in ("n.", "n", "n°", "nº", "no"):
            idx_to_name[i] = "N."
        elif "descripcion" in t and "residuo" in t:
            idx_to_name[i] = "Descripción Residuo"
        elif "codigo" in t and "principal" in t:
            idx_to_name[i] = "Código principal"
        elif "codigo" in t and "secundario" in t:
            idx_to_name[i] = "Código secundario"
        elif "lista" in t and "a" in t:
            idx_to_name[i] = "Lista A"
        elif "peligrosidad" in t:
            idx_to_name[i] = "Peligrosidad"
        elif "fisico" in t:
            idx_to_name[i] = "E. físico"
        elif "contenedor" in t:
            idx_to_name[i] = "Contenedor"
        elif "estado" in t and "residuo" in t:
            idx_to_name[i] = "Estado del Residuo"
        elif "cantidad" in t:
            idx_to_name[i] = "Cantidad (Kg)"
    return idx_to_name


def _rows_from_table(table: List[List[Optional[str]]]) -> Optional[List[Dict[str, str]]]:
    if not table or len(table) < 2:
        return None
    header_idx = None
    for i in range(min(6, len(table))):
        candidate = _clean_row(table[i])
        if _looks_like_detalle_table(candidate):
            header_idx = i
            break
    if header_idx is None:
        return None
    header = _clean_row(table[header_idx])
    colmap = _standardize_headers(header)
    must_have = {"N.", "Descripción Residuo", "Código principal", "Peligrosidad", "Cantidad (Kg)"}
    if not must_have.issubset(set(colmap.values())):
        return None
    physical_rows = []
    for raw in table[header_idx + 1:]:
        cells = _clean_row(raw)
        if not any(cells):
            continue
        if _norm(" ".join(cells)).startswith("total"):
            break
        row_partial = {c: "" for c in STD_COLS}
        for idx, val in enumerate(cells):
            if idx in colmap:
                row_partial[colmap[idx]] = val
        if any(row_partial[k] for k in STD_COLS):
            physical_rows.append(row_partial)
    if not physical_rows:
        return None
    merged_rows = []
    for pr in physical_rows:
        n_val = _clean_spaces(pr.get("N.", ""))
        if n_val:
            merged_rows.append(pr)
            continue
        if not merged_rows:
            continue
        prev = merged_rows[-1]
        if pr.get("Descripción Residuo"):
            prev["Descripción Residuo"] = _clean_spaces((prev.get("Descripción Residuo", "") + " " + pr["Descripción Residuo"]).strip())
        for k in STD_COLS:
            if k in ("N.", "Descripción Residuo"):
                continue
            if (not prev.get(k)) and pr.get(k):
                prev[k] = pr[k]
    return [r for r in merged_rows if _clean_spaces(r.get("N.", "")) and (_clean_spaces(r.get("Descripción Residuo", "")) or _clean_spaces(r.get("Cantidad (Kg)", "")))] or None


def _extract_text_block_detalle(page_text: str) -> Optional[str]:
    if not page_text:
        return None
    start = page_text.find("Detalle de Declaración")
    if start < 0:
        return None
    block = page_text[start:]
    cut_t = block.find("TRANSPORTISTA")
    return block[:cut_t] if cut_t > 0 else block


def _parse_detalle_by_text(block: str) -> Optional[List[Dict[str, str]]]:
    lines = [l.strip() for l in (block or "").splitlines() if l.strip()]
    rows = []
    buf = ""

    def flush_buf(b: str):
        b = _clean_spaces(b)
        if not b:
            return None
        m = re.match(
            r"^(?P<n>\d+)\s+(?P<desc>.+?)\s+(?P<codp>[IVX]+\.\d+)\s+(?P<pelig>[A-Z,]+)\s+(?P<efis>líquido|liquido|sólido|solido)\s+(?P<listaA>A?\d+)\s+(?P<cont>.+?)\s+(?P<estado>Cerrado|Abierto|CERRADO|ABIERTO)\s+(?P<kg>\d+(?:[.,]\d+)?)$",
            b,
            flags=re.IGNORECASE,
        )
        if not m:
            return None
        ef = m.group("efis").lower().replace("liquido", "líquido").replace("solido", "sólido")
        return {
            "N.": m.group("n"),
            "Descripción Residuo": m.group("desc"),
            "Código principal": m.group("codp"),
            "Código secundario": "",
            "Lista A": m.group("listaA"),
            "Peligrosidad": m.group("pelig"),
            "E. físico": ef,
            "Contenedor": m.group("cont"),
            "Estado del Residuo": m.group("estado").capitalize(),
            "Cantidad (Kg)": m.group("kg"),
        }

    for ln in lines:
        if _norm(ln).startswith("total"):
            if buf:
                parsed = flush_buf(buf)
                if parsed:
                    rows.append(parsed)
            break
        if re.match(r"^\d+\s+", ln):
            if buf:
                parsed = flush_buf(buf)
                if parsed:
                    rows.append(parsed)
            buf = ln
        else:
            buf = f"{buf} {ln}".strip()
    if buf:
        parsed = flush_buf(buf)
        if parsed:
            rows.append(parsed)
    return rows or None


def _try_parse_date(date_str: str) -> Optional[date]:
    s = _clean_spaces(date_str)
    m = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", s)
    if not m:
        return None
    dd, mm, yy = re.split(r"[/-]", m.group(1))
    if len(yy) == 2:
        yy = "20" + yy
    try:
        return date(int(yy), int(mm), int(dd))
    except Exception:
        return None


def extract_fecha_declaracion(full_text: str) -> Optional[date]:
    if not full_text:
        return None
    idx = full_text.find("Fecha y Hora")
    if idx < 0:
        idx = full_text.lower().find("fecha y hora")
    if idx < 0:
        return None
    return _try_parse_date(full_text[idx: idx + 250]) or _try_parse_date(full_text[idx:])


def extract_detalle_from_pdf(pdf_path: str) -> Tuple[Optional[List[Dict[str, str]]], Optional[date], str]:
    logger.info("Procesando PDF: %s", pdf_path)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join([(p.extract_text() or "") for p in pdf.pages])
            fecha_decl = extract_fecha_declaracion(full_text)
            empresa_dest = extract_empresa_destinataria(full_text)
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                tables = page.extract_tables() or []
                for table in tables:
                    rows_out = _rows_from_table(table)
                    if rows_out:
                        return rows_out, fecha_decl, empresa_dest
                if "Detalle de Declaración" in page_text:
                    try:
                        table_stream = page.extract_table(table_settings=STREAM_SETTINGS)
                        if table_stream:
                            rows_out = _rows_from_table(table_stream)
                            if rows_out:
                                return rows_out, fecha_decl, empresa_dest
                    except Exception:
                        pass
                    block = _extract_text_block_detalle(page_text)
                    if block:
                        parsed = _parse_detalle_by_text(block)
                        if parsed:
                            return parsed, fecha_decl, empresa_dest
        return None, fecha_decl, empresa_dest
    except Exception as e:
        logger.error("Error procesando PDF: %s", e)
        return None, None, ""


def find_pdfs_recursively(root_dir: str) -> List[str]:
    pdfs = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in filenames:
            if fn.lower().endswith(".pdf"):
                pdfs.append(os.path.join(dirpath, fn))
    return pdfs


def get_instalacion_from_path(pdf_path: str) -> str:
    return os.path.basename(os.path.dirname(pdf_path))


def process_folder(root_dir: str, output_xlsx: str) -> pd.DataFrame:
    pdf_files = find_pdfs_recursively(root_dir)
    all_rows = []
    for pdf_path in pdf_files:
        instalacion = get_instalacion_from_path(pdf_path)
        rows, fecha_decl, empresa_dest = extract_detalle_from_pdf(pdf_path)
        mes = MESES_ES.get(fecha_decl.month, "") if fecha_decl else ""
        anio = fecha_decl.year if fecha_decl else ""
        if rows:
            for r in rows:
                defra_en, defra_es = clasificar_defra_sidrep(r.get("Código principal", ""))
                r["DEFRA_English"] = defra_en
                r["DEFRA_Español"] = defra_es
                # Mantener compatibilidad con columnas/consumidores antiguos.
                r["Clasificación DEFRA"] = defra_en
                r["Instalación"] = instalacion
                r["Empresa destinataria"] = empresa_dest
                r["FechaDeclaración"] = fecha_decl.isoformat() if fecha_decl else ""
                r["Mes"] = mes
                r["Año"] = anio
                r["Archivo"] = os.path.basename(pdf_path)
                r["Ruta"] = pdf_path
            all_rows.extend(rows)
    cols_finales = STD_COLS + META_COLS
    df = pd.DataFrame(all_rows).reindex(columns=cols_finales) if all_rows else pd.DataFrame(columns=cols_finales)
    if not df.empty:
        df["Cantidad (Kg)"] = (
            df["Cantidad (Kg)"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        )
        df["Cantidad (Kg)"] = pd.to_numeric(df["Cantidad (Kg)"], errors="coerce")
    Path(output_xlsx).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_xlsx, index=False)
    logger.info("Excel generado exitosamente en: %s", output_xlsx)
    return df


# =========================
# API PARA APP.PY
# =========================
def extract_sidrep_data(pdf_path: str) -> "pd.DataFrame":
    """Función principal llamada por app.py. Devuelve DataFrame con columnas estándar."""
    try:
        rows, fecha_decl, empresa_dest = extract_detalle_from_pdf(pdf_path)
        if not rows:
            return pd.DataFrame()
        fecha_iso = fecha_decl.isoformat() if fecha_decl else ""
        for r in rows:
            r["Empresa destinataria"] = empresa_dest or ""
            r["FechaDeclaración"] = fecha_iso
            if "Cantidad (Kg)" in r:
                s = str(r["Cantidad (Kg)"]).replace(".", "").replace(",", ".")
                try:
                    r["Cantidad (Kg)"] = float(s)
                except Exception:
                    r["Cantidad (Kg)"] = 0.0
        return pd.DataFrame(rows)
    except Exception as e:
        logger.error(f"Error procesando SIDREP: {str(e)}")
        return pd.DataFrame()

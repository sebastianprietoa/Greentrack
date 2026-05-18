import re
import unicodedata
import logging
import os
import glob
import importlib
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from difflib import SequenceMatcher

import pandas as pd
import pdfplumber

_pytesseract_spec = importlib.util.find_spec("pytesseract")
pytesseract = importlib.import_module("pytesseract") if _pytesseract_spec else None
_fitz_spec = importlib.util.find_spec("fitz")
fitz = importlib.import_module("fitz") if _fitz_spec else None
_cv2_spec = importlib.util.find_spec("cv2")
cv2 = importlib.import_module("cv2") if _cv2_spec else None
_np_spec = importlib.util.find_spec("numpy")
np = importlib.import_module("numpy") if _np_spec else None
_pil_image_spec = importlib.util.find_spec("PIL.Image")
PIL_Image = importlib.import_module("PIL.Image") if _pil_image_spec else None
_pil_draw_spec = importlib.util.find_spec("PIL.ImageDraw")
PIL_ImageDraw = importlib.import_module("PIL.ImageDraw") if _pil_draw_spec else None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STREAM_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 1,
    "min_words_horizontal": 1,
    "intersection_tolerance": 3,
    "text_tolerance": 3,
}

MASTER_RESIDUOS: Dict[str, List[str]] = {
    "02 01 99": ["Residuos no especificados en otra categoría"],
    "02 02 04": ["Lodos del tratamiento in situ de efluentes"],
    "10 01 01": ["Cenizas del hogar, escorias y polvo de caldera (excepto el polvo de caldera especificado en el código 10 01 04)"],
    "15 01 01": ["Envases de papel y cartón"],
    "15 01 02": ["Envases de plástico"],
    "15 01 04": ["Envases metálicos"],
    "19 08 05": ["Lodos del tratamiento de aguas residuales urbanas"],
    "20 01 99": ["Otras fracciones no especificadas en otra categoría"],
    "21 04 04": ["Residuos de plásticos (HDPE, PEE, PETE, PVC) excepto planzas, boyas, flotadores, redes y cabos."],
}

DEFAULT_CATALOG_PATH = Path("assets/sinader_codigos.xlsx")
PREFERRED_CATALOG_SHEETS = ("LER_completo_842",)
TREATMENT_CATALOG_SHEET = "Tratamiento_SINADER"
DEFAULT_TREATMENT_DEFRA_MAP = {
    "reutilizacion": "Re-use",
    "reciclaje": "Open-loop",
    "combustion": "Combustion",
    "vertedero": "Landfill",
    "anaerobic digestion": "Anaerobic digestion",
}
DEFAULT_TREATMENT_TRAINING_GLOB = "assets/*output*.xlsx"
KNOWN_DESTINATIONS = [
    "ECOPRIAL",
    "ECOFIBRAS SUCURSAL PUERTO MONTT",
    "PLASTICOS DEL SUR SPA",
    "CONSORCIO COLLIPULLI",
    "ESCOMBRERA TRESOL",
    "CENTRO CRUCERO",
    "PLANTA DE TRATAMIENTO DE AGUAS SERVIDAS DE CASTRO",
    "RELLENO SANITARIO LOS ANGELES",
    "PLANTA DE TRATAMIENTO DE RESIDUOS DOMICILIARIOS LAUTARO",
    "SALMONOIL S.A.",
    "LOS GLACIARES",
    "PESQUERA LANDES ISLA ROCUANT",
    "PESQUERA LA PORTADA",
    "REPLACAR",
    "PLANTA RILESUR",
    "ESTACIÓN DE TRANSFERENCIA",
    "ESTACION DE TRANSFERENCIA",
    "ECOFIBRAS SUCURSAL CORONEL",
    "ECOBIO",
    "CANCHA COMPOSTAJE LOS REBALSES DEL SUR",
    "CANCHA LOS REBALSES DEL SUR",
]
DESTINATION_NOISE_FRAGMENTS = [
    "en otra categoría",
    "planzas, boyas, flotadores, redes y cabos",
    "cenizas del hogar",
    "lodos del tratamiento",
    "del tratamiento in situ de efluentes",
    "cartón y productos de papel",
]
TREATMENT_NOISE_FRAGMENTS = [
    "en otra categoría",
    "especificadas en otra categoría",
    "planzas, boyas, flotadores, redes y cabos",
    "cenizas del hogar",
    "lodos del tratamiento in situ de efluentes",
]
STRONG_TREATMENT_CATALOG = [
    "Pretratamiento de papel, cartón y productos de papel",
    "Pretratamiento de pinturas, tintas, adhesivos y resinas que no contienen sustancias peligrosas",
    "Pretratamiento de detergentes que no contienen sustancias peligrosas",
    "Reciclaje de residuos de pastas o productos alimenticios para consumo animal",
    "Reciclaje de residuos hidrobiológicos para consumo animal",
    "Residuos municipales asimilables a domiciliarios",
    "Reciclaje de papel, cartón y productos de papel",
    "Sitio de Escombros de la Construcción",
    "Recepción de Lodos en PTAS",
    "Incineración con recuperación de energía",
    "Pretratamiento de aparatos eléctricos y electrónicos",
    "Pretratamiento de aceites y grasas comestibles",
    "Pretratamiento de madera que no contiene sustancias peligrosas",
    "Pretratamiento de residuos voluminosos",
    "Pretratamiento de materiales eléctricos",
    "Pretratamiento de neumáticos fuera de uso",
    "Pretratamiento de textil, cuero y piel",
    "Pretratamiento de plásticos",
    "Pretratamiento de metales",
    "Pretratamiento de caucho y goma",
    "Pretratamiento de ropa",
    "Pretratamiento vidrio",
    "Degradación Anaeróbica",
    "Reciclaje de aparatos eléctricos y electrónicos",
    "Reciclaje de neumáticos fuera de uso",
    "Reciclaje de plásticos",
    "Reciclaje de metales",
    "Reciclaje de textiles",
    "Reciclaje de vidrio",
    "Relleno sanitario",
    "Disposición final",
    "Coincineración",
    "Aplicación a suelo",
    "Monorelleno",
    "Compostaje",
    "Pretratamiento",
    "Residuos voluminosos",
]
KNOWN_SINADER_CODES = {
    "15 01 01", "15 01 02", "15 01 04", "20 01 99", "19 08 05", "10 01 01", "21 04 04", "02 02 04",
    "02 01 99", "02 01 02", "02 02 02", "02 02 03", "20 01 39", "15 01 06", "21 07 09", "21 07 01",
}
V2_FIN_TABLA_PATTERNS = [
    "La integridad y veracidad de la información",
    "DECLARACIÓN MENSUAL DE RESIDUOS NO PELIGROSOS",
    "Documento generado electrónicamente",
]
TRATAMIENTOS_CONOCIDOS_V2 = sorted([
    "Reciclaje de papel, cartón y productos de papel",
    "Residuos municipales asimilables a domiciliarios",
    "Sitio de Escombros de la Construcción",
    "Recepción de Lodos en PTAS",
    "Degradación Anaeróbica",
    "Reciclaje de plásticos",
    "Reciclaje de metales",
    "Disposición final",
    "Relleno sanitario",
    "Pretratamiento",
    "Monorelleno",
    "Compostaje",
], key=len, reverse=True)
MAPA_RESIDUOS_SINADER_V2 = {
    "02 01 99": "Residuos no especificados en otra categoría",
    "02 02 04": "Lodos del tratamiento in situ de efluentes",
    "10 01 01": "Cenizas del hogar, escorias y polvo de caldera (excepto el polvo de caldera especificado en el código 10 01 04)",
    "15 01 01": "Envases de papel y cartón",
    "15 01 02": "Envases de plástico",
    "15 01 04": "Envases metálicos",
    "15 01 06": "Envases mezclados",
    "19 08 05": "Lodos del tratamiento de aguas residuales urbanas",
    "20 01 99": "Otras fracciones no especificadas en otra categoría",
    "21 04 04": "Residuos de plásticos (HDPE, PEE, PETE, PVC) excepto planzas, boyas, flotadores, redes y cabos",
    "21 07 01": "Residuos orgánicos (ejemplo como conchas, algas, carne, entre otros; incluye mortalidad)",
    "21 07 09": "Lodos orgánicos (ejemplo fecas y alimento no consumido)",
}

V2_REGEX_CODIGO_INICIO = re.compile(r"^\s*(\d{2}\s\d{2}\s\d{2})\s*\|")
V2_REGEX_CANTIDAD = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\b", re.IGNORECASE)


def _strip_accents(s: str) -> str:
    if s is None:
        return ""
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def _norm(s: str) -> str:
    s = _strip_accents(s or "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_cell(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_code(code: Optional[str]) -> str:
    raw = _clean_cell(code)
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6:
        return f"{digits[0:2]} {digits[2:4]} {digits[4:6]}"
    if len(digits) > 6:
        return f"{digits[0:2]} {digits[2:4]} {digits[4:6]}"
    return raw


def _cell_join_multiline(x: Optional[str]) -> str:
    if x is None:
        return ""
    x = str(x).replace("\r", "\n").replace("\u00a0", " ").replace("\n", " ")
    x = re.sub(r"\s+", " ", x).strip()
    return x


def _parse_spanish_month(text_norm: str) -> Optional[int]:
    month_map = {
        "ene": 1, "enero": 1,
        "feb": 2, "febrero": 2,
        "mar": 3, "marzo": 3,
        "abr": 4, "abril": 4,
        "may": 5, "mayo": 5,
        "jun": 6, "junio": 6,
        "jul": 7, "julio": 7,
        "ago": 8, "agosto": 8,
        "sep": 9, "set": 9, "septiembre": 9,
        "oct": 10, "octubre": 10,
        "nov": 11, "noviembre": 11,
        "dic": 12, "diciembre": 12,
    }
    for k, v in month_map.items():
        if re.search(rf"\b{k}\b", text_norm):
            return v
    return None


def _to_float_kg(x: Optional[str]) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    s = re.sub(r"[^0-9\.\-]", "", s)
    if s in ("", ".", "-", "-.", ".-"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def is_sinader_pdf(full_text: str) -> bool:
    t = _norm(full_text)
    keys = ["sinader", "retc", "declaracion mensual", "residuos no peligrosos", "comprobante"]
    return sum(1 for k in keys if k in t) >= 2


def extract_key_value_lines(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for ln in lines:
        m = re.match(r"^([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\.\-/\s]{2,80})\s*:\s*(.+)$", ln)
        if m:
            out[_norm(_clean_cell(m.group(1)))] = _clean_cell(m.group(2))
    return out


def parse_period_from_filename(filename: str) -> Optional[str]:
    t = _norm(Path(filename).stem)
    ym = re.search(r"\b(20\d{2})\b", t)
    year = int(ym.group(1)) if ym else None
    month = _parse_spanish_month(t)
    return f"{month:02d}/{year}" if year and month else None


def extract_period_from_text(full_text: str) -> Optional[str]:
    t = _strip_accents(full_text or "")
    m = re.search(r"Periodo\s+declarado\s*:\s*(\d{2}\s*/\s*\d{4})", t, flags=re.IGNORECASE)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    m2 = re.search(r"Periodo\s*:\s*(\d{4}\s*[-/]\s*\d{2}|\d{2}\s*[-/]\s*\d{4})", t, flags=re.IGNORECASE)
    if m2:
        raw = re.sub(r"\s+", "", m2.group(1))
        if re.match(r"^\d{4}[-/]\d{2}$", raw):
            y, mm = re.split(r"[-/]", raw)
            return f"{mm}/{y}"
        if re.match(r"^\d{2}[-/]\d{4}$", raw):
            mm, y = re.split(r"[-/]", raw)
            return f"{mm}/{y}"
    return None


def sinader_has_no_movements(full_text: str) -> bool:
    t = _norm(full_text)
    patterns = ["periodo sin movimientos", "período sin movimientos", "sin movimientos", "no registra movimientos"]
    return any(p in t for p in patterns)


def extract_sinader_metadata(full_text: str, filename: str) -> Dict[str, str]:
    kv = extract_key_value_lines(full_text)

    def get_any(*keys: str) -> str:
        for k in keys:
            kn = _norm(k)
            if kn in kv:
                return kv[kn]
        for kk, vv in kv.items():
            for k in keys:
                if _norm(k) in kk:
                    return vv
        return ""

    meta = {
        "FuentePDF": Path(filename).name,
        "Folio": get_any("Folio"),
        "Establecimiento": get_any("Establecimiento"),
        "Razón social": get_any("Razón social", "Razon social"),
        "RUT Titular": get_any("RUT Titular", "Rut titular", "RUT"),
        "Realizado por": get_any("Realizado por"),
        "Tipo": get_any("Tipo"),
        "Estado": get_any("Estado"),
        "Código identificador": get_any("Código identificador", "Codigo identificador"),
        "Región": get_any("Región", "Region"),
        "Comuna": get_any("Comuna"),
    }
    meta["Periodo declarado"] = (
        extract_period_from_text(full_text)
        or get_any("Periodo declarado", "Periodo")
        or parse_period_from_filename(filename)
        or ""
    )
    return meta


def _looks_like_sinader_table(table: List[List[str]]) -> bool:
    if not table or not table[0]:
        return False
    header = " ".join(_norm(_clean_cell(c)) for c in table[0] if c is not None)
    return ("residuo" in header) and ("cantidad" in header) and ("destino" in header)


def _split_code_and_desc(residuo_cell: str) -> Tuple[str, str]:
    s = _cell_join_multiline(residuo_cell)
    s = re.sub(r"\s*\|\s*", " | ", s)
    if " | " in s:
        left, right = s.split(" | ", 1)
        return _clean_cell(left), _clean_cell(right)
    m = re.match(r"^\s*(\d{2}\s+\d{2}\s+\d{2})\s+(.*)$", s)
    if m:
        return _clean_cell(m.group(1)), _clean_cell(m.group(2))
    return "", s


def _extract_table_text_block(full_text: str) -> str:
    text = full_text.replace("\r", "\n").replace("\u00a0", " ")
    start_match = re.search(r"Residuo.*Cantidad.*Destino", text, flags=re.IGNORECASE | re.DOTALL)
    if not start_match:
        return text
    block = text[start_match.start():]
    end_match = re.search(r"La\s+integridad\s+y\s+veracidad", block, flags=re.IGNORECASE)
    if end_match:
        block = block[:end_match.start()]
    return block


def _reconstruct_row_blocks_from_lines(lines: List[str]) -> List[str]:
    blocks: List[str] = []
    current: List[str] = []
    ler_start = re.compile(r"^\s*\d{2}\s+\d{2}\s+\d{2}\b(?:\s*\|)?")
    header_noise = re.compile(r"^\s*(residuo|cantidad|tipo\s*tratamiento|tratamiento|destino|transportista|patente)\b", flags=re.IGNORECASE)
    for ln in lines:
        line = _clean_cell(ln)
        if not line:
            continue
        if header_noise.search(line) and "|" not in line and not ler_start.match(line):
            continue
        if "cantidad residuo tipo tratamiento destino" in _norm(line):
            continue
        if ler_start.match(line):
            if current:
                blocks.append(_clean_cell(" ".join(current)))
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(_clean_cell(" ".join(current)))
    return blocks


def _extract_treatment_from_tail_after_kg_with_source(
    tail: str,
    known_treatments: Optional[List[str]] = None,
) -> Tuple[str, str]:
    tail_clean = _clean_cell(tail)
    if not tail_clean:
        return "", ""
    tail_norm = _norm(tail_clean)
    treatment_catalog = list(dict.fromkeys((known_treatments or []) + STRONG_TREATMENT_CATALOG))
    for term in sorted((t for t in treatment_catalog if _clean_cell(t)), key=lambda x: len(x), reverse=True):
        term_norm = _norm(term)
        if not term_norm:
            continue
        if re.search(rf"\b{re.escape(term_norm)}\b", tail_norm, flags=re.IGNORECASE):
            return term, "catalog_tail"

    regex_candidates = [
        r"pretratamiento\s+de\s+papel,\s*cart[oó]n\s+y\s+productos\s+de\s+papel",
        r"pretratamiento\s+de\s+pinturas,\s*tintas,\s*adhesivos\s+y\s+resinas\s+que\s+no\s+contienen\s+sustancias\s+peligrosas",
        r"pretratamiento\s+de\s+detergentes\s+que\s+no\s+contienen\s+sustancias\s+peligrosas",
        r"reciclaje\s+de\s+residuos\s+de\s+pastas\s+o\s+productos\s+alimenticios\s+para\s+consumo\s+animal",
        r"reciclaje\s+de\s+residuos\s+hidrobiol[oó]gicos\s+para\s+consumo\s+animal",
        r"residuos\s+municipales\s+asimilables\s+a\s+domiciliarios",
        r"reciclaje\s+de\s+papel,\s*cart[oó]n\s+y\s+productos\s+de\s+papel",
        r"sitio\s+de\s+escombros\s+de\s+la\s+construcci[oó]n",
        r"recepci[oó]n\s+de\s+lodos\s+en\s+ptas",
        r"incineraci[oó]n\s+con\s+recuperaci[oó]n\s+de\s+energ[ií]a",
        r"pretratamiento\s+de\s+aparatos\s+el[eé]ctricos\s+y\s+electr[oó]nicos",
        r"pretratamiento\s+de\s+aceites\s+y\s+grasas\s+comestibles",
        r"pretratamiento\s+de\s+madera\s+que\s+no\s+contiene\s+sustancias\s+peligrosas",
        r"pretratamiento\s+de\s+residuos\s+voluminosos",
        r"pretratamiento\s+de\s+materiales\s+el[eé]ctricos",
        r"pretratamiento\s+de\s+neum[aá]ticos\s+fuera\s+de\s+uso",
        r"pretratamiento\s+de\s+textil,\s*cuero\s+y\s+piel",
        r"pretratamiento\s+de\s+pl[aá]sticos",
        r"pretratamiento\s+de\s+metales",
        r"pretratamiento\s+de\s+caucho\s+y\s+goma",
        r"pretratamiento\s+de\s+ropa",
        r"pretratamiento\s+vidrio",
        r"degradaci[oó]n\s+anaer[oó]bica",
        r"reciclaje\s+de\s+aparatos\s+el[eé]ctricos\s+y\s+electr[oó]nicos",
        r"reciclaje\s+de\s+neum[aá]ticos\s+fuera\s+de\s+uso",
        r"reciclaje\s+de\s+pl[aá]sticos",
        r"reciclaje\s+de\s+metales",
        r"reciclaje\s+de\s+textiles",
        r"reciclaje\s+de\s+vidrio",
        r"relleno\s+sanitario",
        r"disposici[oó]n\s+final",
        r"coincineraci[oó]n",
        r"aplicaci[oó]n\s+a\s+suelo",
        r"monorelleno",
        r"compostaje",
        r"pretratamiento",
        r"residuos\s+voluminosos",
        r"reutilizaci[oó]n",
        r"combusti[oó]n",
        r"anaerobic\s+digestion",
        r"reciclaje",
    ]
    for pat in regex_candidates:
        m = re.search(pat, tail_clean, flags=re.IGNORECASE)
        if m:
            return _clean_cell(m.group(0)), "regex_tail"
    return "", ""


def extract_treatment_from_tail_after_kg(tail: str, known_treatments: Optional[List[str]] = None) -> str:
    treatment, _ = _extract_treatment_from_tail_after_kg_with_source(tail, known_treatments)
    return treatment


def _parse_reconstructed_row_block(
    block: str,
    known_treatments: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    def _find_catalog_match_spans(text: str, catalog: List[str]) -> List[Tuple[int, int, str]]:
        spans: List[Tuple[int, int, str]] = []
        for term in sorted(set(catalog), key=lambda x: len(x), reverse=True):
            if not _clean_cell(term):
                continue
            m = re.search(re.escape(term), text, flags=re.IGNORECASE)
            if m:
                spans.append((m.start(), m.end(), term))
        return spans

    def _is_destination_clean(dst_text: str, desc_text: str) -> bool:
        d = _norm(dst_text)
        if not d:
            return False
        if any(_norm(x) in d for x in DESTINATION_NOISE_FRAGMENTS):
            return False
        dn = _norm(desc_text)
        if dn:
            generic_tokens = {
                "residuos", "residuo", "plastico", "plasticos", "plástico", "plásticos", "envases", "organicos",
                "orgánicos", "tratamiento", "lodos", "subproductos", "fracciones", "especificadas",
            }
            for token in [t for t in dn.split() if len(t) >= 6]:
                if token in generic_tokens:
                    continue
                if token in d and token not in {"puerto", "tratamiento"}:
                    return False
        return True

    def _is_treatment_clean(treatment_text: str, desc_text: str) -> bool:
        t = _norm(treatment_text)
        if not t:
            return False
        if any(_norm(x) in t for x in TREATMENT_NOISE_FRAGMENTS):
            return False
        dn = _norm(desc_text)
        if dn:
            long_tokens = [tok for tok in dn.split() if len(tok) >= 8]
            if long_tokens and sum(1 for tok in long_tokens if tok in t) >= 2:
                return False
        return True

    def _parse_tail_right_to_left(tail: str, desc_text: str) -> Tuple[str, str, str, str, bool, bool]:
        text = _clean_cell(tail)
        if not text:
            return "", "", "", "", False, False
        pat = ""
        trp = ""
        m_marker = re.search(r"\b\d+\|\s*$", text)
        if m_marker:
            text = _clean_cell(text[:m_marker.start()])
        m_plate = re.search(r"\b((?=[A-Z0-9-]*\d)(?:[A-Z]{2,4}-[A-Z0-9]{2,4}|[A-Z]{2,4}[0-9]{2,4}))\b\s*$", text)
        if m_plate:
            candidate_plate = _clean_cell(m_plate.group(1)).replace(" ", "-")
            if not re.fullmatch(r"\d+\|?", candidate_plate):
                pat = candidate_plate
                text = _clean_cell(text[:m_plate.start()])

        treatment_catalog = list(dict.fromkeys(STRONG_TREATMENT_CATALOG + (known_treatments or [])))
        chosen_treatment = ""
        chosen_destination = ""
        destination_spans = _find_catalog_match_spans(text, KNOWN_DESTINATIONS)
        treatment_spans = _find_catalog_match_spans(text, treatment_catalog) if treatment_catalog else []
        best_pair: Optional[Tuple[Tuple[int, int, str], Tuple[int, int, str]]] = None
        best_pair_score = -1
        for t_span in treatment_spans:
            for d_span in destination_spans:
                overlap = not (t_span[1] <= d_span[0] or d_span[1] <= t_span[0])
                if overlap:
                    continue
                score = (t_span[1] - t_span[0]) + (d_span[1] - d_span[0])
                if t_span[0] <= d_span[0]:
                    score += 5
                if score > best_pair_score:
                    best_pair_score = score
                    best_pair = (t_span, d_span)

        if best_pair:
            t_span, d_span = best_pair
            chosen_treatment = t_span[2]
            chosen_destination = d_span[2]
            for span in sorted([t_span, d_span], key=lambda x: x[0], reverse=True):
                text = _clean_cell(text[:span[0]] + " " + text[span[1]:])
        else:
            if destination_spans:
                d_span = destination_spans[0]
                chosen_destination = d_span[2]
                text = _clean_cell(text[:d_span[0]] + " " + text[d_span[1]:])
            if treatment_spans:
                t_span = treatment_spans[0]
                chosen_treatment = t_span[2]
                text = _clean_cell(text[:t_span[0]] + " " + text[t_span[1]:])

        text = re.sub(r"\b(destino|transportista|patente|cantidad|residuo|tipo tratamiento)\b", " ", text, flags=re.IGNORECASE)
        text = _clean_cell(re.sub(r"\s+", " ", text))
        dst = chosen_destination or text
        trt_ok = bool(chosen_treatment) and _norm(chosen_treatment) in {_norm(x) for x in treatment_catalog} and _is_treatment_clean(chosen_treatment, desc_text)
        dst_ok = bool(chosen_destination) and _is_destination_clean(dst, desc_text)
        return chosen_treatment, dst, trp, pat, trt_ok, dst_ok

    block = _clean_cell(block)
    if not block:
        return None
    m_code = re.match(r"^\s*(\d{2}\s+\d{2}\s+\d{2})\s*(?:\|\s*)?(.*)$", block)
    if not m_code:
        return None
    code = _clean_cell(m_code.group(1))
    rest = _clean_cell(m_code.group(2))
    if not rest:
        return None
    m_qty = re.search(r"(?P<qty>\d[\d\.,]*)\s*kg\b", rest, flags=re.IGNORECASE)
    if not m_qty:
        return {
            "Código principal": code,
            "Descripción Residuo": rest,
            "Cantidad (Kg)": "",
            "Tratamiento": "",
            "Destino": "",
            "Transportista": "",
            "Patente": "",
            "Peligrosidad": "",
            "Estado contenedor": "",
            "Contenedor": "",
            "Texto fila original": block,
            "Parsing_OK": "NO",
            "Tratamiento_confiable": "NO",
            "Destino_confiable": "NO",
        }
    desc = _clean_cell(rest[:m_qty.start()])
    qty = _clean_cell(m_qty.group("qty"))
    tail = _clean_cell(rest[m_qty.end():])
    treatment_from_tail = extract_treatment_from_tail_after_kg(tail, known_treatments)
    _, treatment_source = _extract_treatment_from_tail_after_kg_with_source(tail, known_treatments)
    logger.debug(
        "SINADER tail post-kg | tail='%s' | treatment='%s' | source='%s'",
        tail,
        treatment_from_tail,
        treatment_source or "none",
    )
    tail_remainder = tail
    if treatment_from_tail:
        tail_remainder = _clean_cell(re.sub(re.escape(treatment_from_tail), " ", tail_remainder, count=1, flags=re.IGNORECASE))
    trt_raw, dst_raw, trp_raw, pat_raw, trt_ok, dst_ok = _parse_tail_right_to_left(tail_remainder, desc)
    if treatment_from_tail:
        trt_raw = treatment_from_tail
        trt_ok = True
    trt, dst, trp, pat = _sanitize_treatment_and_logistics(
        trt_raw or tail_remainder,
        dst_raw or tail_remainder,
        trp_raw,
        pat_raw,
        qty,
        known_treatments,
        desc,
    )
    if not treatment_from_tail and trt:
        treatment_source = "sanitize_fallback"
    treatment_catalog_norm = {_norm(x) for x in (STRONG_TREATMENT_CATALOG + (known_treatments or []))}
    known_destination_norm = {_norm(x) for x in KNOWN_DESTINATIONS}
    trt_ok = bool(trt) and _norm(trt) in treatment_catalog_norm and _is_treatment_clean(trt, desc)
    dst_ok = bool(dst) and (
        _norm(dst) in known_destination_norm
        or any(k in _norm(dst) for k in known_destination_norm if k)
    ) and _is_destination_clean(dst, desc)
    qty_ok = _to_float_kg(qty) is not None
    code_ok = bool(code and re.match(r"^\d{2}\s+\d{2}\s+\d{2}$", code))
    semantic_ok = (trt_ok or dst_ok) and (not trt or _is_treatment_clean(trt, desc))
    parsing_ok = bool(code_ok and desc and qty_ok and semantic_ok)
    return {
        "Código principal": code,
        "Descripción Residuo": desc,
        "Cantidad (Kg)": qty,
        "Tratamiento": trt,
        "Destino": dst,
        "Transportista": trp,
        "Patente": pat,
        "Peligrosidad": "",
        "Estado contenedor": "",
        "Contenedor": "",
        "Texto fila original": block,
        "_treatment_source": treatment_source or "none",
        "Parsing_OK": "SI" if parsing_ok else "NO",
        "Tratamiento_confiable": "SI" if trt_ok and bool(trt) else "NO",
        "Destino_confiable": "SI" if dst_ok and bool(dst) else "NO",
    }


def extraer_lineas_por_coordenadas(pdf_path: str) -> List[str]:
    lineas_totales: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
            ) or []
            if not words:
                continue
            grupos: Dict[float, List[Dict[str, object]]] = {}
            for w in words:
                y = round(float(w.get("top", 0.0)), 1)
                grupos.setdefault(y, []).append(w)
            for y in sorted(grupos.keys()):
                fila = sorted(grupos[y], key=lambda z: float(z.get("x0", 0.0)))
                texto_linea = " ".join(str(w.get("text", "")) for w in fila)
                texto_linea = re.sub(r"\s+", " ", texto_linea).strip()
                if texto_linea:
                    lineas_totales.append(texto_linea)
    return lineas_totales


def aislar_bloque_tabla_desde_lineas(lineas: List[str]) -> List[str]:
    inicio = None
    fin = None
    for i, linea in enumerate(lineas):
        linea_norm = re.sub(r"\s+", " ", linea).strip().lower()
        if ("residuo" in linea_norm and "cantidad" in linea_norm and "tratamiento" in linea_norm and "destino" in linea_norm):
            inicio = i + 1
            break
    if inicio is None:
        for i, linea in enumerate(lineas):
            if V2_REGEX_CODIGO_INICIO.match(linea):
                inicio = i
                break
    if inicio is None:
        return []
    for j in range(inicio, len(lineas)):
        if any(p.lower() in lineas[j].lower() for p in V2_FIN_TABLA_PATTERNS):
            fin = j
            break
    if fin is None:
        fin = len(lineas)
    return lineas[inicio:fin]


def reconstruir_filas_desde_lineas(lineas_tabla: List[str]) -> List[str]:
    filas: List[str] = []
    actual = ""
    for linea in lineas_tabla:
        linea = re.sub(r"\s+", " ", linea).strip()
        if not linea:
            continue
        if V2_REGEX_CODIGO_INICIO.match(linea):
            if actual:
                filas.append(actual.strip())
            actual = linea
        elif actual:
            actual += f" {linea}"
    if actual:
        filas.append(actual.strip())
    return filas


def normalizar_fila_original(fila: str) -> str:
    fila = re.sub(r"\s+", " ", fila).strip()
    fila = fila.replace("ECOFIBRASSUCURSAL", "ECOFIBRAS SUCURSAL")
    fila = fila.replace("PUERTOMONTT", "PUERTO MONTT")
    fila = fila.replace("ECOFIBRASSUCURSALPUERTOMONTT", "ECOFIBRAS SUCURSAL PUERTO MONTT")
    fila = fila.replace("carton", "cartón")
    fila = fila.replace("anaerobica", "anaeróbica")
    fila = fila.replace("construccion", "construcción")
    fila = fila.replace("disposicion final", "disposición final")
    fila = fila.replace("recepcion de lodos en ptas", "recepción de lodos en ptas")
    fila = fila.replace("PTA", "PTAS")
    fila = fila.replace("Recepción de Lodos en PTA", "Recepción de Lodos en PTAS")
    fila = fila.replace("Recepcion de Lodos en PTA", "Recepción de Lodos en PTAS")
    return fila


def encontrar_tratamiento_en_texto(texto: str) -> Optional[str]:
    texto_norm = re.sub(r"\s+", " ", texto).strip().lower()
    texto_folded = _norm(texto_norm)
    for t in TRATAMIENTOS_CONOCIDOS_V2:
        if _norm(t) in texto_folded:
            return t
    if "degradación" in texto_norm and "anaeróbica" in texto_norm:
        return "Degradación Anaeróbica"
    if "degradacion" in texto_norm and "anaerobica" in texto_norm:
        return "Degradación Anaeróbica"
    if "sitio de escombros" in texto_norm and "construcción" in texto_norm:
        return "Sitio de Escombros de la Construcción"
    if "monorelleno" in texto_norm:
        return "Monorelleno"
    if "compostaje" in texto_norm:
        return "Compostaje"
    if "pretratamiento" in texto_norm:
        return "Pretratamiento"
    if "relleno sanitario" in texto_norm:
        return "Relleno sanitario"
    if "disposición final" in texto_norm or "disposicion final" in texto_norm:
        return "Disposición final"
    if "reciclaje de plásticos" in texto_norm or "reciclaje de plasticos" in texto_norm:
        return "Reciclaje de plásticos"
    if "reciclaje de metales" in texto_norm:
        return "Reciclaje de metales"
    if "recepción de lodos en ptas" in texto_norm or "recepcion de lodos en ptas" in texto_norm:
        return "Recepción de Lodos en PTAS"
    if "residuos municipales" in texto_norm and "asimilables a domiciliarios" in texto_norm:
        return "Residuos municipales asimilables a domiciliarios"
    return None


def inferir_tratamiento_por_codigo(codigo: str, fila_completa: str = "", resto_post_cantidad: str = "") -> Optional[str]:
    fila_norm = re.sub(r"\s+", " ", fila_completa).strip().lower()
    resto_norm = re.sub(r"\s+", " ", resto_post_cantidad).strip().lower()
    combinado = f"{fila_norm} {resto_norm}"
    if codigo == "15 01 02":
        if (
            "reciclaje de plásticos" in combinado
            or "reciclaje de plasticos" in combinado
            or "plástico" in fila_norm
            or "plastico" in fila_norm
        ):
            return "Reciclaje de plásticos"
    if codigo == "15 01 04":
        if (
            "reciclaje de metales" in combinado
            or "metálico" in fila_norm
            or "metalico" in fila_norm
            or "metales" in combinado
        ):
            return "Reciclaje de metales"
    if codigo == "19 08 05":
        if (
            "recepción de lodos" in combinado
            or "recepcion de lodos" in combinado
            or "planta de tratamiento" in combinado
            or "aguas servidas" in combinado
            or "tratamiento de aguas" in combinado
            or "ptas" in combinado
        ):
            return "Recepción de Lodos en PTAS"
    return None


def parsear_fila_metodo_1(fila: str) -> Optional[Dict[str, object]]:
    fila = normalizar_fila_original(fila)
    m_codigo = V2_REGEX_CODIGO_INICIO.match(fila)
    if not m_codigo:
        return None
    codigo = m_codigo.group(1).strip()
    resto = fila[m_codigo.end():].strip()
    m_cantidad = V2_REGEX_CANTIDAD.search(resto)
    if not m_cantidad:
        return None
    cantidad = _to_float_kg(m_cantidad.group(1).replace(",", "."))
    residuo = _clean_cell(resto[:m_cantidad.start()]).strip(" -|,")
    resto_post = _clean_cell(resto[m_cantidad.end():])
    tratamiento = encontrar_tratamiento_en_texto(resto_post)
    if tratamiento is None:
        tratamiento = inferir_tratamiento_por_codigo(codigo=codigo, fila_completa=fila, resto_post_cantidad=resto_post)
    if tratamiento is None and codigo == "15 01 01":
        resto_post_norm = _norm(resto_post)
        if ("reciclaje" in resto_post_norm) or ("papel" in resto_post_norm and "carton" in resto_post_norm and "productos" in resto_post_norm):
            tratamiento = "Reciclaje de papel, cartón y productos de papel"
    return {
        "codigo_residuo": codigo,
        "residuo": residuo,
        "cantidad_kg": cantidad,
        "tratamiento": tratamiento,
        "fila_original": fila,
        "metodo_usado": "v2_metodo_1",
    }


def parsear_fila_metodo_2_rescate(fila: str) -> Optional[Dict[str, object]]:
    fila = normalizar_fila_original(fila)
    m_codigo = V2_REGEX_CODIGO_INICIO.match(fila)
    if not m_codigo:
        return None
    codigo = m_codigo.group(1).strip()
    resto = fila[m_codigo.end():].strip()
    m_cantidad = V2_REGEX_CANTIDAD.search(resto)
    if not m_cantidad:
        return None
    cantidad = _to_float_kg(m_cantidad.group(1).replace(",", "."))
    residuo = _clean_cell(resto[:m_cantidad.start()]).strip(" -|,")
    resto_post = _clean_cell(resto[m_cantidad.end():])
    tratamiento = encontrar_tratamiento_en_texto(fila)
    if tratamiento is None:
        tratamiento = inferir_tratamiento_por_codigo(codigo=codigo, fila_completa=fila, resto_post_cantidad=resto_post)
    if tratamiento is None and codigo == "15 01 01":
        fila_norm = _norm(fila)
        resto_post_norm = _norm(resto_post)
        if (
            ("reciclaje" in fila_norm or "reciclaje" in resto_post_norm)
            and ("papel" in fila_norm or "papel" in resto_post_norm)
            and ("carton" in fila_norm or "carton" in resto_post_norm)
        ):
            tratamiento = "Reciclaje de papel, cartón y productos de papel"
    if tratamiento:
        residuo = _clean_cell(re.sub(re.escape(tratamiento), " ", residuo, flags=re.IGNORECASE)).strip(" -|,")
    return {
        "codigo_residuo": codigo,
        "residuo": residuo,
        "cantidad_kg": cantidad,
        "tratamiento": tratamiento,
        "fila_original": fila,
        "metodo_usado": "v2_metodo_2_rescate",
    }


def parsear_fila(fila: str) -> Optional[Dict[str, object]]:
    resultado_1 = parsear_fila_metodo_1(fila)
    if resultado_1 is None:
        return None
    if resultado_1["tratamiento"] is not None:
        resultado_1["requiere_revision"] = False
        return resultado_1
    resultado_2 = parsear_fila_metodo_2_rescate(fila)
    if resultado_2 is not None and resultado_2["tratamiento"] is not None:
        resultado_2["requiere_revision"] = True
        return resultado_2
    resultado_1["requiere_revision"] = True
    return resultado_1


def procesar_pdf(pdf_path: str) -> List[Dict[str, str]]:
    lineas = extraer_lineas_por_coordenadas(pdf_path)
    bloque_tabla = aislar_bloque_tabla_desde_lineas(lineas)
    if not bloque_tabla:
        return []
    texto_tabla = " ".join(bloque_tabla)
    if "Período Sin Movimientos" in texto_tabla or "Periodo Sin Movimientos" in texto_tabla:
        return []
    filas = reconstruir_filas_desde_lineas(bloque_tabla)
    resultados: List[Dict[str, str]] = []
    for fila in filas:
        parsed = parsear_fila(fila)
        if parsed is None:
            resultados.append({
                "Código principal": "",
                "Descripción Residuo": "",
                "Cantidad (Kg)": "",
                "Tratamiento": "",
                "Destino": "",
                "Transportista": "",
                "Patente": "",
                "Peligrosidad": "",
                "Estado contenedor": "",
                "Contenedor": "",
                "Texto fila original": fila,
                "Parsing_OK": "NO",
                "Tratamiento_confiable": "NO",
                "Destino_confiable": "NO",
                "Row_strategy": "v2_text",
            })
            continue
        trt = _clean_cell(parsed.get("tratamiento"))
        qty = parsed.get("cantidad_kg")
        qty_txt = "" if qty is None else (str(int(qty)) if isinstance(qty, float) and qty.is_integer() else str(qty))
        resultados.append({
            "Código principal": _clean_cell(parsed.get("codigo_residuo")),
            "Descripción Residuo": _clean_cell(parsed.get("residuo")),
            "Cantidad (Kg)": qty_txt,
            "Tratamiento": trt,
            "Destino": "",
            "Transportista": "",
            "Patente": "",
            "Peligrosidad": "",
            "Estado contenedor": "",
            "Contenedor": "",
            "Texto fila original": _clean_cell(parsed.get("fila_original")),
            "Parsing_OK": "SI" if _clean_cell(parsed.get("codigo_residuo")) and qty is not None else "NO",
            "Tratamiento_confiable": "SI" if trt else "NO",
            "Destino_confiable": "NO",
            "Row_strategy": _clean_cell(parsed.get("metodo_usado")) or "v2_text",
        })
    uniq = {}
    for r in resultados:
        key = (r.get("Código principal", ""), _norm(r.get("Descripción Residuo", "")), _clean_cell(r.get("Cantidad (Kg)", "")))
        uniq.setdefault(key, r)
    return list(uniq.values())


def parse_sinader_rows_from_tables(pdf_path: str) -> List[Dict[str, str]]:
    rows_out: List[Dict[str, str]] = []
    known_treatments = load_treatment_level3_terms()
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables(table_settings=STREAM_SETTINGS) or []
            except Exception:
                tables = page.extract_tables() or []
            for tb in tables:
                if not tb or not tb[0]:
                    continue
                if not _looks_like_sinader_table(tb):
                    continue
                raw_lines: List[str] = []
                for r in tb[1:]:
                    cells = [_clean_cell(c) for c in (r or []) if _clean_cell(c)]
                    if not cells:
                        continue
                    raw_lines.append(" | ".join(cells))
                blocks = _reconstruct_row_blocks_from_lines(raw_lines)
                for block in blocks:
                    parsed = _parse_reconstructed_row_block(block, known_treatments)
                    if not parsed:
                        continue
                    rows_out.append(parsed)
    uniq = {}
    for r in rows_out:
        key = (r.get("Código principal", ""), _norm(r.get("Descripción Residuo", "")), _clean_cell(r.get("Cantidad (Kg)", "")))
        uniq.setdefault(key, r)
    return list(uniq.values())


def parse_sinader_rows_from_text(full_text: str) -> List[Dict[str, str]]:
    rows_out: List[Dict[str, str]] = []
    if not full_text:
        return rows_out
    known_treatments = load_treatment_level3_terms()
    block_text = _extract_table_text_block(full_text)
    lines = [_clean_cell(x) for x in block_text.splitlines() if _clean_cell(x)]
    blocks = _reconstruct_row_blocks_from_lines(lines)
    for block in blocks:
        parsed = _parse_reconstructed_row_block(block, known_treatments)
        if parsed:
            rows_out.append(parsed)
    uniq = {}
    for r in rows_out:
        key = (r.get("Código principal", ""), _norm(r.get("Descripción Residuo", "")), _clean_cell(r.get("Cantidad (Kg)", "")))
        uniq.setdefault(key, r)
    return list(uniq.values())


def render_pdf_page_to_image(doc, page_index: int, dpi: int = 220, pdfplumber_page=None):
    if np is None:
        return None
    if fitz is not None and doc is not None:
        zoom = dpi / 72.0
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        buffer = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
        if pix.n == 3:
            img_bgr = buffer[:, :, ::-1].copy()
        else:
            img_bgr = cv2.cvtColor(buffer, cv2.COLOR_RGBA2BGR) if cv2 is not None else buffer[:, :, :3].copy()
        scale_x = pix.width / float(page.rect.width)
        scale_y = pix.height / float(page.rect.height)
        return img_bgr, page.rect.width, page.rect.height, scale_x, scale_y
    if pdfplumber_page is not None:
        page_im = pdfplumber_page.to_image(resolution=dpi).original
        arr = np.array(page_im)
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        if arr.shape[-1] == 4:
            arr = arr[:, :, :3]
        img_bgr = arr[:, :, ::-1].copy()
        scale_x = img_bgr.shape[1] / float(pdfplumber_page.width)
        scale_y = img_bgr.shape[0] / float(pdfplumber_page.height)
        return img_bgr, pdfplumber_page.width, pdfplumber_page.height, scale_x, scale_y
    return None


def detect_table_bbox_from_image(img_bgr):
    if np is None or img_bgr is None:
        return None
    if cv2 is None:
        rgb = img_bgr[:, :, ::-1]
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype("uint8")
        ink = gray < 220
        row_density = np.sum(ink, axis=1)
        active_rows = np.where(row_density > np.percentile(row_density, 70))[0]
        if active_rows.size == 0:
            return None
        y0 = max(0, int(active_rows.min()) - 10)
        y1 = min(gray.shape[0] - 1, int(active_rows.max()) + 10)
        sub = ink[y0:y1 + 1, :]
        col_density = np.sum(sub, axis=0)
        active_cols = np.where(col_density > np.percentile(col_density, 60))[0]
        if active_cols.size == 0:
            return (0, y0, gray.shape[1] - 1, y1)
        x0 = max(0, int(active_cols.min()) - 8)
        x1 = min(gray.shape[1] - 1, int(active_cols.max()) + 8)
        return (x0, y0, x1, y1)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    th = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    h, w = th.shape[:2]
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 35), 1))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, h // 60)))
    horiz = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel_h)
    vert = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel_v)
    table_map = cv2.bitwise_or(horiz, vert)
    contours, _ = cv2.findContours(table_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for cnt in contours:
        x, y, ww, hh = cv2.boundingRect(cnt)
        area = ww * hh
        if ww < int(w * 0.55) or hh < int(h * 0.12):
            continue
        if area > best_area:
            best_area = area
            best = (x, y, x + ww, y + hh)
    if best:
        return best
    # fallback: banda con mayor densidad de tinta (evita fallar cuando no hay líneas de tabla)
    row_density = np.sum(th > 0, axis=1)
    active = np.where(row_density > np.percentile(row_density, 70))[0]
    if active.size == 0:
        return None
    y0 = max(0, int(active.min()) - 10)
    y1 = min(h - 1, int(active.max()) + 10)
    return (0, y0, w - 1, y1)


def segment_row_bboxes_from_image(img_bgr, table_bbox):
    if np is None or img_bgr is None or table_bbox is None:
        return []
    x0, y0, x1, y1 = table_bbox
    roi = img_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return []
    if cv2 is None:
        rgb = roi[:, :, ::-1]
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype("uint8")
        ink = gray < 220
        row_proj = np.sum(ink, axis=1)
        threshold = max(3, int((x1 - x0) * 0.01))
        active = row_proj > threshold
        spans: List[Tuple[int, int]] = []
        start = None
        for i, on in enumerate(active):
            if on and start is None:
                start = i
            elif not on and start is not None:
                spans.append((start, i - 1))
                start = None
        if start is not None:
            spans.append((start, len(active) - 1))
        merged: List[Tuple[int, int]] = []
        for s, e in spans:
            if not merged:
                merged.append((s, e))
            elif s - merged[-1][1] <= 12:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        out = []
        for s, e in merged:
            if e - s + 1 < 12:
                continue
            out.append((x0, y0 + max(0, s - 3), x1, y0 + min((y1 - y0) - 1, e + 3)))
        return out
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 11)
    # une caracteres por línea
    line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, (x1 - x0) // 40), 2))
    connected = cv2.morphologyEx(th, cv2.MORPH_CLOSE, line_kernel)
    row_proj = np.sum(connected > 0, axis=1)
    threshold = max(2, int((x1 - x0) * 0.015))
    active = row_proj > threshold
    spans: List[Tuple[int, int]] = []
    start = None
    for i, on in enumerate(active):
        if on and start is None:
            start = i
        elif not on and start is not None:
            spans.append((start, i - 1))
            start = None
    if start is not None:
        spans.append((start, len(active) - 1))
    # merge gaps cortos para mantener filas multilínea juntas
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if not merged:
            merged.append((s, e))
            continue
        ps, pe = merged[-1]
        if s - pe <= 7:
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))
    row_boxes = []
    min_h = max(8, int((y1 - y0) * 0.012))
    for s, e in merged:
        if (e - s + 1) < min_h:
            continue
        row_boxes.append((x0, y0 + max(0, s - 2), x1, y0 + min((y1 - y0) - 1, e + 2)))
    return row_boxes


def detect_column_boundaries_from_image(img_bgr, table_bbox):
    if np is None or img_bgr is None or table_bbox is None:
        return []
    x0, y0, x1, y1 = table_bbox
    roi = img_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return []
    if cv2 is not None:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        bin_img = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 11)
        col_proj = np.sum(bin_img > 0, axis=0).astype(float)
    else:
        rgb = roi[:, :, ::-1]
        gray = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype("uint8")
        col_proj = np.sum(gray < 220, axis=0).astype(float)
    if col_proj.size < 30:
        return []
    smooth = np.convolve(col_proj, np.ones(11) / 11.0, mode="same")
    w = smooth.size
    expected = [0.0, 0.52, 0.64, 0.77, 0.90, 0.96, 1.0]
    bounds = [0]
    for r in expected[1:-1]:
        center = int(w * r)
        win = max(10, int(w * 0.08))
        lo = max(1, center - win)
        hi = min(w - 2, center + win)
        idx = int(np.argmin(smooth[lo:hi + 1])) + lo
        bounds.append(idx)
    bounds.append(w - 1)
    bounds = sorted(set(int(b) for b in bounds))
    if len(bounds) < 7:
        return []
    # normalizar a coordenadas absolutas
    return [x0 + b for b in bounds]


def detect_row_boundaries_from_image(img_bgr, table_bbox):
    # devuelve límites [y0,y1,...] para construir bandas de fila estables
    row_boxes = segment_row_bboxes_from_image(img_bgr, table_bbox)
    if not row_boxes:
        return []
    y_bounds = [row_boxes[0][1]]
    for b in row_boxes:
        y_bounds.append(b[3])
    # dedupe / orden
    y_bounds = sorted(set(y_bounds))
    return y_bounds


def build_cell_bboxes(row_boxes, column_bounds):
    if not row_boxes or not column_bounds or len(column_bounds) < 7:
        return []
    cells = []
    for rb in row_boxes:
        x0r, y0r, x1r, y1r = rb
        row_cells = []
        for i in range(len(column_bounds) - 1):
            cx0 = max(x0r, column_bounds[i])
            cx1 = min(x1r, column_bounds[i + 1])
            if cx1 <= cx0:
                continue
            row_cells.append((cx0, y0r, cx1, y1r))
        if len(row_cells) >= 4:
            cells.append(row_cells)
    return cells


def extract_text_from_cell_bboxes(page, img_bgr, row_cell_bboxes, scale_x, scale_y, page_is_pdfplumber=False):
    out = []
    for row_cells in row_cell_bboxes:
        row_texts = []
        for ci, cell_bbox in enumerate(row_cells):
            native = extract_pdf_text_from_bbox(page, cell_bbox, scale_x, scale_y, page_is_pdfplumber=page_is_pdfplumber)
            used = native
            ocr = ""
            critical = ci in {0, 1, 2, 3}
            if critical and _row_text_is_incoherent(native):
                ocr = ocr_text_from_bbox(img_bgr, cell_bbox)
                if ocr and (not native or len(ocr) > len(native) * 0.7):
                    used = ocr
            row_texts.append({"native": native, "ocr": ocr, "used": used, "bbox": cell_bbox})
        out.append(row_texts)
    return out


def parse_sinader_table_from_cells(cell_rows, known_treatments: Optional[List[str]] = None):
    rows_out = []
    for row_idx, row_cells in enumerate(cell_rows):
        if not row_cells:
            continue
        used = [_clean_cell(c.get("used", "")) for c in row_cells]
        # mapeo esperado: Residuo, Cantidad, Tratamiento, Destino, Transportista, Patente
        residuo = used[0] if len(used) > 0 else ""
        cantidad = used[1] if len(used) > 1 else ""
        tratamiento_cell = used[2] if len(used) > 2 else ""
        destino_cell = used[3] if len(used) > 3 else ""
        transportista_cell = used[4] if len(used) > 4 else ""
        patente_cell = used[5] if len(used) > 5 else ""

        code, desc = _split_code_and_desc(residuo)
        if not code:
            m = re.match(r"^\s*(\d{2}\s+\d{2}\s+\d{2})\s+(.*)$", residuo)
            if m:
                code, desc = _clean_cell(m.group(1)), _clean_cell(m.group(2))
        qty_match = re.search(r"(\d[\d\.,]*)", cantidad)
        qty = _clean_cell(qty_match.group(1)) if qty_match else ""

        trt, dst, trp, pat = _sanitize_treatment_and_logistics(
            tratamiento_cell,
            destino_cell,
            transportista_cell,
            patente_cell,
            qty,
            known_treatments,
            desc,
        )
        treatment_catalog = {_norm(x) for x in (STRONG_TREATMENT_CATALOG + (known_treatments or []))}
        destination_catalog = {_norm(x) for x in KNOWN_DESTINATIONS}
        trt_ok = bool(trt) and _norm(trt) in treatment_catalog and not any(_norm(f) in _norm(trt) for f in TREATMENT_NOISE_FRAGMENTS)
        dst_ok = bool(dst) and (_norm(dst) in destination_catalog or any(k in _norm(dst) for k in destination_catalog if k)) and not any(_norm(f) in _norm(dst) for f in DESTINATION_NOISE_FRAGMENTS)
        code_ok = bool(re.match(r"^\d{2}\s+\d{2}\s+\d{2}$", _clean_cell(code)))
        qty_ok = _to_float_kg(qty) is not None
        parsing_ok = bool(code_ok and qty_ok and (trt_ok or dst_ok))

        if code or desc or qty or trt or dst:
            confidence_reasons = []
            if not code_ok:
                confidence_reasons.append("code")
            if not qty_ok:
                confidence_reasons.append("qty")
            if not trt_ok:
                confidence_reasons.append("trt")
            if not dst_ok:
                confidence_reasons.append("dst")
            rows_out.append({
                "Código principal": code,
                "Descripción Residuo": desc,
                "Cantidad (Kg)": qty,
                "Tratamiento": trt,
                "Destino": dst,
                "Transportista": trp,
                "Patente": pat,
                "Peligrosidad": "",
                "Estado contenedor": "",
                "Contenedor": "",
                "Texto fila original": _clean_cell(" | ".join(used)),
                "Parsing_OK": "SI" if parsing_ok else "NO",
                "Tratamiento_confiable": "SI" if trt_ok else "NO",
                "Destino_confiable": "SI" if dst_ok else "NO",
                "_row_index": row_idx,
                "_cell_confidence_reason": ",".join(confidence_reasons),
            })
    return rows_out


def extract_pdf_text_from_bbox(page, bbox_img, scale_x: float, scale_y: float, page_is_pdfplumber: bool = False) -> str:
    x0i, y0i, x1i, y1i = bbox_img
    x0 = max(0.0, x0i / scale_x)
    y0 = max(0.0, y0i / scale_y)
    page_width = float(page.width) if page_is_pdfplumber else float(page.rect.width)
    page_height = float(page.height) if page_is_pdfplumber else float(page.rect.height)
    x1 = min(page_width, x1i / scale_x)
    y1 = min(page_height, y1i / scale_y)
    if page_is_pdfplumber:
        text = page.within_bbox((x0, y0, x1, y1)).extract_text() or ""
    else:
        rect = fitz.Rect(x0, y0, x1, y1)
        text = page.get_text("text", clip=rect) if fitz is not None else ""
    return _clean_cell(text)


def ocr_text_from_bbox(img_bgr, bbox_img) -> str:
    if pytesseract is None or img_bgr is None or np is None:
        return ""
    x0, y0, x1, y1 = bbox_img
    crop = img_bgr[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.size == 0:
        return ""
    if cv2 is not None:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        txt = pytesseract.image_to_string(gray, lang="spa+eng")
    elif PIL_Image is not None:
        rgb = crop[:, :, ::-1]
        txt = pytesseract.image_to_string(PIL_Image.fromarray(rgb), lang="spa+eng")
    else:
        txt = ""
    return _clean_cell(txt)


def _row_text_is_incoherent(text: str) -> bool:
    t = _clean_cell(text)
    if len(t) < 18:
        return True
    has_code = bool(re.search(r"\b\d{2}\s+\d{2}\s+\d{2}\b", t))
    has_kg = bool(re.search(r"\d[\d\.,]*\s*kg\b", t, flags=re.IGNORECASE))
    has_keywords = any(k in _norm(t) for k in ["reciclaje", "relleno", "compostaje", "destino", "planta", "escombrera", "ecofibras"])
    return (not (has_code and has_kg)) or (len(t) < 45 and not has_keywords)


def _save_visual_debug_page(
    debug_dir: Path,
    page_index: int,
    img_bgr,
    table_bbox,
    row_boxes,
    row_texts: List[Dict[str, str]],
    column_bounds: Optional[List[int]] = None,
    cell_rows: Optional[List[List[Dict[str, str]]]] = None,
) -> None:
    if PIL_Image is None or np is None:
        return
    rgb = img_bgr[:, :, ::-1]
    img = PIL_Image.fromarray(rgb)
    if PIL_ImageDraw is not None:
        draw = PIL_ImageDraw.Draw(img)
        if table_bbox:
            draw.rectangle(table_bbox, outline="red", width=3)
        if column_bounds:
            h = img.size[1]
            for x in column_bounds:
                draw.line([(x, 0), (x, h)], fill="cyan", width=1)
        for i, b in enumerate(row_boxes):
            draw.rectangle(b, outline="lime", width=2)
            draw.text((b[0] + 2, b[1] + 2), str(i + 1), fill="yellow")
        if cell_rows:
            for row in cell_rows:
                for cell in row:
                    cb = cell.get("bbox")
                    if cb:
                        draw.rectangle(cb, outline="magenta", width=1)
    img.save(debug_dir / f"page_{page_index+1:02d}_bboxes.png")
    txt_path = debug_dir / f"page_{page_index+1:02d}_rows.txt"
    with txt_path.open("w", encoding="utf-8") as f:
        for idx, row in enumerate(row_texts, start=1):
            f.write(f"[ROW {idx}] native={row.get('native','')} | ocr={row.get('ocr','')} | used={row.get('used','')}\n")
    summary = {
        "page": page_index + 1,
        "table_bbox": table_bbox,
        "rows_detected": len(row_boxes or []),
        "columns_detected": max(0, (len(column_bounds or []) - 1)),
        "column_bounds": column_bounds or [],
        "cells_detected": int(sum(len(r) for r in (cell_rows or []))),
    }
    (debug_dir / f"page_{page_index+1:02d}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_sinader_rows_visual_segmented(pdf_path: str) -> List[Dict[str, str]]:
    rows_out: List[Dict[str, str]] = []
    known_treatments = load_treatment_level3_terms()
    if np is None:
        return rows_out
    debug_dir_env = os.getenv("SINADER_VISUAL_DEBUG_DIR", "").strip()
    debug_target_name = os.getenv("SINADER_VISUAL_DEBUG_PDF", "").strip().lower()
    debug_enabled = bool(debug_dir_env) and (not debug_target_name or debug_target_name in Path(pdf_path).name.lower())
    debug_dir = Path(debug_dir_env) / Path(pdf_path).stem if debug_enabled else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path) if fitz is not None else None
    plumber_pdf = pdfplumber.open(pdf_path)
    try:
        page_count = doc.page_count if doc is not None else len(plumber_pdf.pages)
        for page_index in range(page_count):
            plumber_page = plumber_pdf.pages[page_index]
            rendered = render_pdf_page_to_image(doc, page_index, dpi=220, pdfplumber_page=plumber_page)
            if not rendered:
                continue
            img_bgr, _, _, scale_x, scale_y = rendered
            table_bbox = detect_table_bbox_from_image(img_bgr)
            if not table_bbox:
                continue
            row_boxes = segment_row_bboxes_from_image(img_bgr, table_bbox)
            if not row_boxes:
                continue
            column_bounds = detect_column_boundaries_from_image(img_bgr, table_bbox)
            page = doc.load_page(page_index) if doc is not None else plumber_page
            row_debug_texts: List[Dict[str, str]] = []
            parsed_rows = []
            cell_debug_rows: List[List[Dict[str, str]]] = []
            accepted_cell_rows = 0
            rejected_cell_rows = 0
            fallback_rows_used = 0
            if column_bounds and len(column_bounds) >= 7:
                row_cells = build_cell_bboxes(row_boxes, column_bounds)
                cell_rows = extract_text_from_cell_bboxes(page, img_bgr, row_cells, scale_x, scale_y, page_is_pdfplumber=(doc is None))
                cell_debug_rows = cell_rows
                parsed_from_cells = parse_sinader_table_from_cells(cell_rows, known_treatments)
                # Criterio estricto de aceptación por celdas:
                # - código y cantidad válidos
                # - Parsing_OK afirmativo
                # - al menos una señal confiable (tratamiento/destino)
                # Si una fila queda dudosa, se fuerza fallback por fila (no se acepta por celdas).
                rejected_row_indexes = set()
                for r in parsed_from_cells:
                    code_ok = bool(re.match(r"^\d{2}\s+\d{2}\s+\d{2}$", _clean_cell(r.get("Código principal", ""))))
                    qty_ok = _to_float_kg(r.get("Cantidad (Kg)", "")) is not None
                    parsing_ok = _norm(_clean_cell(r.get("Parsing_OK", ""))) in {"si", "yes", "true"}
                    trt_ok = _norm(_clean_cell(r.get("Tratamiento_confiable", ""))) in {"si", "yes", "true"}
                    dst_ok = _norm(_clean_cell(r.get("Destino_confiable", ""))) in {"si", "yes", "true"}
                    row_idx = int(r.get("_row_index", -1))
                    if code_ok and qty_ok and parsing_ok and (trt_ok or dst_ok):
                        r["Row_strategy"] = "cells_primary"
                        r.pop("_row_index", None)
                        r.pop("_cell_confidence_reason", None)
                        parsed_rows.append(r)
                        accepted_cell_rows += 1
                    else:
                        rejected_cell_rows += 1
                        if row_idx >= 0:
                            rejected_row_indexes.add(row_idx)

                for row_idx in sorted(rejected_row_indexes):
                    if row_idx >= len(row_boxes):
                        continue
                    bbox_img = row_boxes[row_idx]
                    text_native = extract_pdf_text_from_bbox(page, bbox_img, scale_x, scale_y, page_is_pdfplumber=(doc is None))
                    text_used = text_native
                    text_ocr = ""
                    if _row_text_is_incoherent(text_native):
                        text_ocr = ocr_text_from_bbox(img_bgr, bbox_img)
                        if text_ocr and (not text_native or len(text_ocr) > len(text_native) * 0.7):
                            text_used = text_ocr
                    row_debug_texts.append({"native": text_native, "ocr": text_ocr, "used": text_used})
                    parsed = _parse_reconstructed_row_block(text_used, known_treatments)
                    if parsed:
                        parsed["Texto fila original"] = text_used
                        parsed["Row_strategy"] = "row_visual_fallback"
                        parsed_rows.append(parsed)
                        fallback_rows_used += 1

            if not parsed_rows:
                for bbox_img in row_boxes:
                    text_native = extract_pdf_text_from_bbox(page, bbox_img, scale_x, scale_y, page_is_pdfplumber=(doc is None))
                    text_used = text_native
                    text_ocr = ""
                    if _row_text_is_incoherent(text_native):
                        text_ocr = ocr_text_from_bbox(img_bgr, bbox_img)
                        if text_ocr and (not text_native or len(text_ocr) > len(text_native) * 0.7):
                            text_used = text_ocr
                    row_debug_texts.append({"native": text_native, "ocr": text_ocr, "used": text_used})
                    parsed = _parse_reconstructed_row_block(text_used, known_treatments)
                    if parsed:
                        parsed["Texto fila original"] = text_used
                        parsed["Row_strategy"] = "row_visual_fallback"
                        parsed_rows.append(parsed)
                        fallback_rows_used += 1
            rows_out.extend(parsed_rows)
            if debug_dir is not None:
                _save_visual_debug_page(
                    debug_dir,
                    page_index,
                    img_bgr,
                    table_bbox,
                    row_boxes,
                    row_debug_texts,
                    column_bounds=column_bounds,
                    cell_rows=cell_debug_rows,
                )
                page_extra = {
                    "cells_accepted": accepted_cell_rows,
                    "cells_rejected": rejected_cell_rows,
                    "rows_fallback_used": fallback_rows_used,
                    "rows_final": len(parsed_rows),
                }
                (debug_dir / f"page_{page_index+1:02d}_row_strategy.json").write_text(json.dumps(page_extra, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if doc is not None:
            doc.close()
        plumber_pdf.close()
    uniq = {}
    for r in rows_out:
        key = (r.get("Código principal", ""), _norm(r.get("Descripción Residuo", "")), _clean_cell(r.get("Cantidad (Kg)", "")))
        uniq.setdefault(key, r)
    return list(uniq.values())


def parse_sinader_rows_hybrid(pdf_path: str) -> List[Dict[str, str]]:
    return parse_sinader_rows_visual_segmented(pdf_path)


def extract_global_treatment_from_text(full_text: str, known_treatments: Optional[List[str]] = None) -> str:
    text = _cell_join_multiline(full_text or "")
    if not text:
        return ""
    text_norm = _norm(text)
    if known_treatments:
        for term in sorted(known_treatments, key=lambda x: len(x), reverse=True):
            term_norm = _norm(term)
            if term_norm and term_norm in text_norm:
                return term
    patterns = [
        r"(?:tipo\s*tratamiento|tratamiento)\s*[:\-]?\s*(reutilizaci[oó]n|reciclaje|combusti[oó]n|vertedero|anaerobic digestion)",
        r"(?:tipo\s*tratamiento|tratamiento)\s*[:\-]?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]{4,60})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return _clean_cell(m.group(1))
    return ""


def _sanitize_treatment_and_logistics(
    tratamiento: str,
    destino: str,
    transportista: str,
    patente: str,
    cantidad: str = "",
    known_treatments: Optional[List[str]] = None,
    descripcion: str = "",
) -> Tuple[str, str, str, str]:
    def _extract_treatment_phrase(text: str) -> str:
        if not text:
            return ""
        text_norm = _norm(text)
        if known_treatments:
            for term in sorted(known_treatments, key=lambda x: len(x), reverse=True):
                term_norm = _norm(term)
                if not term_norm:
                    continue
                if term_norm in text_norm:
                    return term
                term_tokens = [t for t in term_norm.split() if len(t) > 3]
                if term_tokens and all(t in text_norm for t in term_tokens):
                    return term
        if "degradacion" in text_norm and "anaerobica" in text_norm:
            return "Degradación Anaeróbica"
        if "anaerobica" in text_norm:
            return "Degradación Anaeróbica"
        candidates = [
            r"pretratamiento\s+de\s+papel,\s*cart[oó]n\s+y\s+productos\s+de\s+papel",
            r"pretratamiento\s+de\s+pinturas,\s*tintas,\s*adhesivos\s+y\s+resinas\s+que\s+no\s+contienen\s+sustancias\s+peligrosas",
            r"pretratamiento\s+de\s+detergentes\s+que\s+no\s+contienen\s+sustancias\s+peligrosas",
            r"reciclaje\s+de\s+residuos\s+de\s+pastas\s+o\s+productos\s+alimenticios\s+para\s+consumo\s+animal",
            r"reciclaje\s+de\s+residuos\s+hidrobiol[oó]gicos\s+para\s+consumo\s+animal",
            r"residuos\s+municipales\s+asimilables\s+a\s+domiciliarios",
            r"reciclaje\s+de\s+papel,\s*cart[oó]n\s+y\s+productos\s+de\s+papel",
            r"sitio\s+de\s+escombros\s+de\s+la\s+construcci[oó]n",
            r"recepci[oó]n\s+de\s+lodos\s+en\s+ptas",
            r"incineraci[oó]n\s+con\s+recuperaci[oó]n\s+de\s+energ[ií]a",
            r"pretratamiento\s+de\s+aparatos\s+el[eé]ctricos\s+y\s+electr[oó]nicos",
            r"pretratamiento\s+de\s+aceites\s+y\s+grasas\s+comestibles",
            r"pretratamiento\s+de\s+madera\s+que\s+no\s+contiene\s+sustancias\s+peligrosas",
            r"pretratamiento\s+de\s+residuos\s+voluminosos",
            r"pretratamiento\s+de\s+materiales\s+el[eé]ctricos",
            r"pretratamiento\s+de\s+neum[aá]ticos\s+fuera\s+de\s+uso",
            r"pretratamiento\s+de\s+textil,\s*cuero\s+y\s+piel",
            r"pretratamiento\s+de\s+pl[aá]sticos",
            r"pretratamiento\s+de\s+metales",
            r"pretratamiento\s+de\s+caucho\s+y\s+goma",
            r"pretratamiento\s+de\s+ropa",
            r"pretratamiento\s+vidrio",
            r"degradaci[oó]n\s+anaer[oó]bica",
            r"reciclaje\s+de\s+aparatos\s+el[eé]ctricos\s+y\s+electr[oó]nicos",
            r"reciclaje\s+de\s+neum[aá]ticos\s+fuera\s+de\s+uso",
            r"reciclaje\s+de\s+pl[aá]sticos",
            r"reciclaje\s+de\s+metales",
            r"reciclaje\s+de\s+textiles",
            r"reciclaje\s+de\s+vidrio",
            r"relleno\s+sanitario",
            r"disposici[oó]n\s+final",
            r"coincineraci[oó]n",
            r"aplicaci[oó]n\s+a\s+suelo",
            r"monorelleno",
            r"compostaje",
            r"pretratamiento",
            r"residuos\s+voluminosos",
            r"reutilizaci[oó]n",
            r"combusti[oó]n",
            r"anaerobic\s+digestion",
            r"reciclaje",
        ]
        for pat in candidates:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return _clean_cell(m.group(0))
        return ""

    trt = _clean_cell(tratamiento)
    dst = _clean_cell(destino)
    trp = _clean_cell(transportista)
    pat = _clean_cell(patente)
    raw_combined = f"{trt} {dst}".strip()

    if trt or dst:
        def _is_placeholder_destination(value: str) -> bool:
            v = _norm(value)
            return (not v) or v.startswith("in situ") or v.startswith("situ de efluentes")

        def _clean_destination_noise(value: str) -> str:
            cleaned = _clean_cell(value)
            original_norm = _norm(cleaned)
            for known_dst in sorted(KNOWN_DESTINATIONS, key=lambda x: len(x), reverse=True):
                kd_norm = _norm(known_dst)
                if kd_norm and kd_norm in original_norm:
                    return known_dst
            if "estacion de transferencia" in original_norm or "estación de transferencia" in original_norm:
                return "ESTACIÓN DE TRANSFERENCIA"
            if "collipulli" in original_norm:
                return "CONSORCIO COLLIPULLI"
            if "lautaro" in original_norm:
                return "PLANTA DE TRATAMIENTO DE RESIDUOS DOMICILIARIOS LAUTARO"
            if "ecobio" in original_norm:
                return "ECOBIO"
            for frag in DESTINATION_NOISE_FRAGMENTS:
                cleaned = re.sub(re.escape(frag), " ", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\bespecificad[ao]s?\b", " ", cleaned, flags=re.IGNORECASE)
            if descripcion:
                desc_norm = _norm(descripcion)
                for token in [t for t in desc_norm.split() if len(t) >= 7]:
                    if token in {"tratamiento", "residuos", "categoria", "especificadas"}:
                        continue
                    cleaned = re.sub(rf"\b{re.escape(token)}\b", " ", cleaned, flags=re.IGNORECASE)
            cleaned = _clean_cell(re.sub(r"\s+", " ", cleaned))
            cleaned_norm = _norm(cleaned)
            for known_dst in sorted(KNOWN_DESTINATIONS, key=lambda x: len(x), reverse=True):
                kd_norm = _norm(known_dst)
                if kd_norm and kd_norm in cleaned_norm:
                    return known_dst
            generic_dst_tokens = {
                "planta", "tratamiento", "residuos", "domiciliarios", "relleno", "sanitario", "sucursal",
                "centro", "cancha", "compostaje", "puerto", "sur", "isla", "los", "las", "del", "de",
            }
            cleaned_tokens = set(t for t in cleaned_norm.split() if len(t) >= 4 and t not in generic_dst_tokens)
            best_dst = ""
            best_score = 0
            for known_dst in KNOWN_DESTINATIONS:
                kd_norm = _norm(known_dst)
                kd_tokens = set(t for t in kd_norm.split() if len(t) >= 4 and t not in generic_dst_tokens)
                if not kd_tokens:
                    continue
                overlap = len(cleaned_tokens.intersection(kd_tokens))
                if overlap > best_score:
                    best_score = overlap
                    best_dst = known_dst
            if best_score >= 2 and len(cleaned_tokens) <= 4:
                return best_dst
            return cleaned

        def _tail_after_qty_kg(text: str, qty_value: str) -> str:
            if not text:
                return ""
            qty_digits = re.sub(r"\D", "", _clean_cell(qty_value))
            matches = list(re.finditer(r"(\d[\d\.,]*)\s*kg\b", text, flags=re.IGNORECASE))
            if not matches:
                return ""
            if not qty_digits:
                return _clean_cell(text[matches[0].end():])
            for m in matches:
                m_digits = re.sub(r"\D", "", m.group(1))
                if m_digits == qty_digits:
                    return _clean_cell(text[m.end():])
            return ""

        tail_by_qty = _tail_after_qty_kg(raw_combined, cantidad)
        if tail_by_qty:
            phrase_from_tail = _extract_treatment_phrase(tail_by_qty)
            if phrase_from_tail:
                trt = phrase_from_tail
                remainder_tail = _clean_cell(re.sub(re.escape(phrase_from_tail), "", tail_by_qty, count=1, flags=re.IGNORECASE))
                if remainder_tail and _is_placeholder_destination(dst):
                    dst = remainder_tail

        kg_split = re.search(r"^(?P<prefix>.*?)(?P<qty>\d[\d\.,]*)\s*kg\s*(?P<after>.*)$", trt, flags=re.IGNORECASE)
        if kg_split:
            trt = _clean_cell(kg_split.group("prefix"))
            trailing = _clean_cell(kg_split.group("after"))
            if trailing and _is_placeholder_destination(dst):
                dst = trailing
        trt = re.sub(r"^\d[\d\.,]*\s*(kg|kgs?)\s*", "", trt, flags=re.IGNORECASE).strip()
        if _norm(trt) in {"destino transportista patente", "destino transportista", "transportista patente"}:
            trt = ""
        if "|" in trt and not dst:
            left, right = [x.strip() for x in trt.split("|", 1)]
            if right:
                trt = left
                dst = right

        labeled = re.search(
            r"(?:^|\s)destino\s*[:\-]?\s*(?P<dst>.*?)(?:\s+transportista\s*[:\-]?\s*(?P<trp>.*?))?(?:\s+patente\s*[:\-]?\s*(?P<pat>.*))?$",
            trt,
            flags=re.IGNORECASE,
        )
        if labeled:
            if not dst:
                dst = _clean_cell(labeled.group("dst") or "")
            if not trp:
                trp = _clean_cell(labeled.group("trp") or "")
            if not pat:
                pat = _clean_cell(labeled.group("pat") or "")
            trt = ""

        phrase = _extract_treatment_phrase(trt)
        if not phrase and raw_combined:
            phrase = _extract_treatment_phrase(raw_combined)
        if phrase:
            remainder = _clean_cell(re.sub(re.escape(phrase), "", trt, count=1, flags=re.IGNORECASE))
            if remainder and _is_placeholder_destination(dst):
                dst = remainder
            if dst:
                dst = _clean_cell(re.sub(re.escape(phrase), "", dst, count=1, flags=re.IGNORECASE))
            trt = phrase

        combined_all = _clean_cell(" ".join(x for x in [trt, dst, trp, pat] if _clean_cell(x)))
        phrase_all = _extract_treatment_phrase(combined_all) if combined_all else ""
        if phrase_all:
            trt = phrase_all
            cleaned = combined_all
            cleaned = re.sub(re.escape(phrase_all), " ", cleaned, count=1, flags=re.IGNORECASE)
            cleaned = re.sub(r"\d[\d\.,]*\s*kg\b", " ", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\b(destino|transportista|patente)\b", " ", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\bin\s*situ\s*de\s*efluentes\b", " ", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\d+\|", " ", cleaned)
            cleaned = re.sub(r"\b\d+\b", " ", cleaned)
            if not pat:
                m_pat = re.search(r"\b((?=[A-Z0-9-]*\d)(?:[A-Z]{2,4}-[A-Z0-9]{2,4}|[A-Z]{2,4}[0-9]{2,4}))\b", cleaned)
                if m_pat:
                    pat = m_pat.group(1)
                    cleaned = cleaned.replace(m_pat.group(1), " ")
            candidate_dst = _clean_cell(re.sub(r"\s+", " ", cleaned).strip(" |-_/"))
            if candidate_dst and _is_placeholder_destination(dst):
                dst = candidate_dst

        if dst:
            dst = re.sub(r"\d+\|", " ", dst)
            dst = _clean_destination_noise(_clean_cell(re.sub(r"\s+", " ", dst)))

    return trt, dst, trp, pat


def extract_sinader_from_pdf(pdf_path: str) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join([(p.extract_text() or "") for p in pdf.pages])
    meta = extract_sinader_metadata(full_text, pdf_path)
    if sinader_has_no_movements(full_text):
        return [{
            "N.": "0",
            "Descripción Residuo": "PERÍODO SIN MOVIMIENTOS",
            "Código principal": "",
            "Peligrosidad": "",
            "Cantidad (Kg)": "0",
            "Estado contenedor": "",
            "Contenedor": "",
            "Tratamiento": "",
            "Destino": "",
            "Transportista": "",
            "Patente": "",
            "Sin movimientos": "SI",
            "Texto fila original": "",
            "Parsing_OK": "SI",
            "Tratamiento_confiable": "NO",
            "Destino_confiable": "NO",
        }], meta
    rows_from_v2_text = procesar_pdf(pdf_path)
    rows_from_tables = parse_sinader_rows_from_tables(pdf_path)
    rows_from_text = parse_sinader_rows_from_text(full_text)

    def _score_rows(rows: List[Dict[str, str]]) -> tuple[float, int, int]:
        if not rows:
            return (-9999.0, 0, 0)
        score = 0.0
        strong_rows = 0
        for r in rows:
            code = _clean_cell(r.get("Código principal", ""))
            qty = _clean_cell(r.get("Cantidad (Kg)", ""))
            desc = _clean_cell(r.get("Descripción Residuo", ""))
            dst = _clean_cell(r.get("Destino", ""))
            trt = _clean_cell(r.get("Tratamiento", ""))
            code_ok = bool(re.match(r"^\d{2}\s+\d{2}\s+\d{2}$", code))
            qty_ok = _to_float_kg(qty) is not None
            desc_ok = bool(desc and len(desc) >= 8)
            if code_ok and qty_ok and desc_ok:
                strong_rows += 1
                score += 4.0
            if re.match(r"^\d{2}\s+\d{2}\s+\d{2}$", code):
                score += 3.0
            if _to_float_kg(qty) is not None:
                score += 2.5
            if desc_ok:
                score += 2.2
            if _norm(_clean_cell(r.get("Parsing_OK", ""))) in {"si", "yes", "true"}:
                score += 1.5
            if _norm(_clean_cell(r.get("Tratamiento_confiable", ""))) in {"si", "yes", "true"}:
                score += 0.4
            if _norm(_clean_cell(r.get("Destino_confiable", ""))) in {"si", "yes", "true"}:
                score += 0.4
            dst_norm = _norm(dst)
            trt_norm = _norm(trt)
            if any(_norm(f) in dst_norm for f in DESTINATION_NOISE_FRAGMENTS):
                score -= 2.5
            if any(_norm(f) in trt_norm for f in TREATMENT_NOISE_FRAGMENTS):
                score -= 2.5
            desc_tokens = [t for t in _norm(desc).split() if len(t) >= 8]
            if dst_norm and sum(1 for t in desc_tokens if t in dst_norm) >= 2:
                score -= 2.0
            if trt_norm and sum(1 for t in desc_tokens if t in trt_norm) >= 2:
                score -= 2.0
        return (score, strong_rows, len(rows))

    if rows_from_v2_text:
        method, detail_rows = "v2_text_primary", rows_from_v2_text
    else:
        base_candidates = [("tables", rows_from_tables), ("text", rows_from_text)]
        method, detail_rows = max(base_candidates, key=lambda x: _score_rows(x[1]))
    base_score, base_strong, base_total = _score_rows(detail_rows)
    base_ok = bool(detail_rows) and (base_strong >= max(2, int(0.5 * max(1, base_total))))

    if not base_ok:
        base_candidates = [("tables", rows_from_tables), ("text", rows_from_text)]
        method_alt, rows_alt = max(base_candidates, key=lambda x: _score_rows(x[1]))
        alt_score, alt_strong, alt_total = _score_rows(rows_alt)
        alt_ok = bool(rows_alt) and (alt_strong >= max(2, int(0.5 * max(1, alt_total))))
        if alt_ok and alt_score > base_score:
            method, detail_rows = f"{method_alt}_fallback_text", rows_alt
            base_score, base_strong, base_total = alt_score, alt_strong, alt_total
            base_ok = True
    if not base_ok:
        rows_from_hybrid = parse_sinader_rows_hybrid(pdf_path)
        visual_score, visual_strong, visual_total = _score_rows(rows_from_hybrid)
        visual_ok = bool(rows_from_hybrid) and (visual_strong >= max(2, int(0.5 * max(1, visual_total))))
        # Estrategia visual solo como fallback cuando la base textual falla claramente.
        if visual_ok or visual_score > (base_score + 8.0):
            method, detail_rows = "visual_fallback", rows_from_hybrid
        else:
            method = f"{method}_base_low_confidence"

    if not detail_rows:
        relaxed_lines = [_clean_cell(x) for x in full_text.splitlines() if _clean_cell(x)]
        relaxed_blocks = _reconstruct_row_blocks_from_lines(relaxed_lines)
        known_treatments_relaxed = load_treatment_level3_terms()
        detail_rows = [p for p in (_parse_reconstructed_row_block(b, known_treatments_relaxed) for b in relaxed_blocks) if p]
        if detail_rows:
            method = "relaxed_text"
    selected_score, selected_strong, selected_total = _score_rows(detail_rows)
    logger.info(
        "Método SINADER seleccionado: %s (filas=%s, score=%.2f, strong=%s/%s)",
        method,
        len(detail_rows),
        selected_score,
        selected_strong,
        selected_total,
    )

    known_treatments = load_treatment_level3_terms()
    global_treatment = extract_global_treatment_from_text(full_text, known_treatments)
    out_rows = []
    for i, r in enumerate(detail_rows, start=1):
        row_treatment = _clean_cell(r.get("Tratamiento", ""))
        if not row_treatment and global_treatment:
            row_treatment = global_treatment
        row_treatment, row_destino, row_transportista, row_patente = _sanitize_treatment_and_logistics(
            row_treatment,
            r.get("Destino", ""),
            r.get("Transportista", ""),
            r.get("Patente", ""),
            r.get("Cantidad (Kg)", ""),
            known_treatments,
            r.get("Descripción Residuo", ""),
        )
        out_rows.append({
            "N.": str(i),
            "Descripción Residuo": r.get("Descripción Residuo", ""),
            "Código principal": r.get("Código principal", ""),
            "Peligrosidad": r.get("Peligrosidad", ""),
            "Cantidad (Kg)": r.get("Cantidad (Kg)", ""),
            "Estado contenedor": r.get("Estado contenedor", ""),
            "Contenedor": r.get("Contenedor", ""),
            "Tratamiento": row_treatment,
            "Destino": row_destino,
            "Transportista": row_transportista,
            "Patente": row_patente,
            "Sin movimientos": "NO",
            "Texto fila original": r.get("Texto fila original", ""),
            "Parsing_OK": r.get("Parsing_OK", "NO"),
            "Tratamiento_confiable": r.get("Tratamiento_confiable", "NO"),
            "Destino_confiable": r.get("Destino_confiable", "NO"),
        })
    return out_rows, meta


def _normalize_for_match(s: str) -> str:
    s = _strip_accents(s or "").lower().replace("|", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _prefix_similarity(extracted_text: str, canonical_text: str) -> float:
    a = _normalize_for_match(extracted_text)
    b = _normalize_for_match(canonical_text)
    if not a or not b:
        return 0.0
    if b.startswith(a):
        return 1.0
    best = SequenceMatcher(None, a, b).ratio()
    words = b.split()
    for i in range(1, len(words) + 1):
        best = max(best, SequenceMatcher(None, a, " ".join(words[:i])).ratio())
    return best


def choose_canonical_description(extracted_desc: str, codigo: str, catalog: Dict[str, List[str]], threshold: float = 0.72) -> str:
    extracted_desc = _clean_cell(extracted_desc)
    codigo = _clean_cell(codigo)
    if not extracted_desc:
        return extracted_desc
    same_code_candidates = catalog.get(codigo, [])
    if len(same_code_candidates) == 1:
        return same_code_candidates[0]
    if same_code_candidates:
        scored = sorted([(cand, _prefix_similarity(extracted_desc, cand)) for cand in same_code_candidates], key=lambda x: x[1], reverse=True)
        if scored[0][1] >= threshold:
            return scored[0][0]
    # Blindaje: si no hay candidatos para el mismo código, no forzar reemplazo por similitud global.
    # Esto evita contaminaciones como 15 01 06 -> "Envases metálicos" por fuzzy matching agresivo.
    return extracted_desc


def _build_catalog_from_dataframe(df: pd.DataFrame) -> Dict[str, List[str]]:
    if df.empty:
        return {}
    normalized_cols = {_norm(c): c for c in df.columns}
    code_col = None
    desc_col = None
    code_candidates = [
        "codigo principal",
        "código principal",
        "codigo",
        "código",
        "codigo residuo",
        "código residuo",
        "codigo sinader",
        "código sinader",
        "codigo ler",
        "código ler",
    ]
    desc_candidates = [
        "descripcion residuo",
        "descripción residuo",
        "descripcion",
        "descripción",
        "residuo",
        "entry official name (en)",
        "entry official name",
        "capitulo oficial sinader (es)",
        "subchapter official name (en)",
    ]
    declarable_col = None
    declarable_candidates = [
        "declarable en sinader?",
        "¿declarable en sinader?",
        "declarable en sinader",
    ]
    for candidate in code_candidates:
        if candidate in normalized_cols:
            code_col = normalized_cols[candidate]
            break
    for candidate in desc_candidates:
        if candidate in normalized_cols:
            desc_col = normalized_cols[candidate]
            break
    for candidate in declarable_candidates:
        if candidate in normalized_cols:
            declarable_col = normalized_cols[candidate]
            break
    if not code_col or not desc_col:
        return {}
    catalog: Dict[str, List[str]] = {}
    required_cols = [code_col, desc_col] + ([declarable_col] if declarable_col else [])
    for _, row in df[required_cols].dropna(subset=[code_col, desc_col]).iterrows():
        if declarable_col:
            declarable_value = _norm(_clean_cell(row.get(declarable_col, "")))
            if declarable_value and declarable_value not in {"si", "sí", "s", "yes", "true"}:
                continue
        code = _normalize_code(row[code_col])
        desc = _clean_cell(row[desc_col])
        if not code or not desc:
            continue
        catalog.setdefault(code, [])
        if desc not in catalog[code]:
            catalog[code].append(desc)
    return catalog


def load_residuo_catalog(catalog_path: Optional[str] = None) -> Dict[str, List[str]]:
    configured_path = (catalog_path or os.getenv("SINADER_CATALOG_PATH", "")).strip()
    candidate_paths = [Path(configured_path)] if configured_path else []
    candidate_paths.append(DEFAULT_CATALOG_PATH)
    for path in candidate_paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            excel_file = pd.ExcelFile(path)
            sheet_candidates = [s for s in PREFERRED_CATALOG_SHEETS if s in excel_file.sheet_names]
            sheet_candidates.extend([s for s in excel_file.sheet_names if s not in sheet_candidates])
            for sheet_name in sheet_candidates:
                df = pd.read_excel(path, sheet_name=sheet_name)
                catalog = _build_catalog_from_dataframe(df)
                if catalog:
                    logger.info("Catálogo SINADER cargado desde %s (hoja=%s, códigos=%s)", path, sheet_name, len(catalog))
                    return catalog
            logger.warning("Catálogo SINADER en %s no tiene columnas válidas de código/descripcion", path)
        except Exception as exc:
            logger.warning("No se pudo cargar catálogo SINADER en %s: %s", path, exc)
    return MASTER_RESIDUOS


def _build_treatment_defra_map_from_dataframe(df: pd.DataFrame) -> Dict[str, str]:
    if df.empty:
        return {}
    normalized_cols = {_norm(c): c for c in df.columns}
    defra_col = None
    treatment_col = None
    defra_candidates = [
        "defra",
        "nombre defra",
        "nombre defra (lista 1 exacta)",
        "defra name",
    ]
    treatment_candidates = [
        "tratamiento sinader",
        "tratamiento",
        "tratamiento sinader (es)",
        "tipo tratamiento",
        "sinader",
    ]
    for candidate in defra_candidates:
        if candidate in normalized_cols:
            defra_col = normalized_cols[candidate]
            break
    for candidate in treatment_candidates:
        if candidate in normalized_cols:
            treatment_col = normalized_cols[candidate]
            break
    if not defra_col or not treatment_col:
        cols = list(df.columns[:2])
        if len(cols) >= 2:
            defra_col, treatment_col = cols[0], cols[1]
        else:
            return {}
    mapping: Dict[str, str] = {}
    for _, row in df[[defra_col, treatment_col]].dropna(how="any").iterrows():
        defra_name = _clean_cell(row[defra_col])
        treatment_name = _norm(_clean_cell(row[treatment_col]))
        if not defra_name or not treatment_name:
            continue
        mapping[treatment_name] = defra_name
    return mapping


def load_treatment_defra_map(catalog_path: Optional[str] = None) -> Dict[str, str]:
    configured_path = (catalog_path or os.getenv("SINADER_CATALOG_PATH", "")).strip()
    candidate_paths = [Path(configured_path)] if configured_path else []
    candidate_paths.append(DEFAULT_CATALOG_PATH)
    for path in candidate_paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            excel_file = pd.ExcelFile(path)
            if TREATMENT_CATALOG_SHEET not in excel_file.sheet_names:
                continue
            df = pd.read_excel(path, sheet_name=TREATMENT_CATALOG_SHEET)
            mapping = _build_treatment_defra_map_from_dataframe(df)
            if mapping:
                logger.info("Mapa Tratamiento->DEFRA cargado desde %s (hoja=%s, filas=%s)", path, TREATMENT_CATALOG_SHEET, len(mapping))
                return mapping
        except Exception as exc:
            logger.warning("No se pudo cargar mapa de tratamientos SINADER en %s: %s", path, exc)
    return dict(DEFAULT_TREATMENT_DEFRA_MAP)


def load_treatment_level3_terms(catalog_path: Optional[str] = None) -> List[str]:
    configured_path = (catalog_path or os.getenv("SINADER_CATALOG_PATH", "")).strip()
    candidate_paths = [Path(configured_path)] if configured_path else []
    candidate_paths.append(DEFAULT_CATALOG_PATH)
    for path in candidate_paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            excel_file = pd.ExcelFile(path)
            if TREATMENT_CATALOG_SHEET not in excel_file.sheet_names:
                continue
            df = pd.read_excel(path, sheet_name=TREATMENT_CATALOG_SHEET)
            normalized_cols = {_norm(c): c for c in df.columns}
            level3_col = None
            for candidate in ["nivel 3", "nivel3", "level 3", "tratamiento", "treatment"]:
                if candidate in normalized_cols:
                    level3_col = normalized_cols[candidate]
                    break
            if not level3_col:
                continue
            values = []
            for value in df[level3_col].dropna().tolist():
                text = _clean_cell(value)
                if text:
                    values.append(text)
            unique_values = sorted(set(values), key=lambda x: len(x), reverse=True)
            if unique_values:
                logger.info("Tratamientos Nivel 3 cargados desde %s (hoja=%s, filas=%s)", path, TREATMENT_CATALOG_SHEET, len(unique_values))
                return unique_values
        except Exception as exc:
            logger.warning("No se pudo cargar tratamientos Nivel 3 desde %s: %s", path, exc)
    return []


def map_treatment_to_defra(tratamiento: str, treatment_map: Dict[str, str]) -> str:
    normalized_treatment = _norm(tratamiento)
    if not normalized_treatment:
        return ""
    if normalized_treatment in treatment_map:
        return treatment_map[normalized_treatment]
    for key, defra_value in treatment_map.items():
        if key in normalized_treatment:
            return defra_value
    return ""


def choose_canonical_treatment(extracted_treatment: str, known_treatments: List[str], threshold: float = 0.58) -> str:
    raw = _clean_cell(extracted_treatment)
    if not raw or not known_treatments:
        return raw
    a = _normalize_for_match(raw)
    if not a:
        return raw
    best_term = raw
    best_score = 0.0
    for term in known_treatments:
        b = _normalize_for_match(term)
        if not b:
            continue
        score = 1.0 if (a in b or b in a) else SequenceMatcher(None, a, b).ratio()
        if score > best_score:
            best_score = score
            best_term = term
    return best_term if best_score >= threshold else raw


def load_treatment_alias_map(training_files: Optional[List[str]] = None) -> Dict[str, str]:
    files = training_files or sorted(glob.glob(DEFAULT_TREATMENT_TRAINING_GLOB))
    alias_map: Dict[str, str] = {}
    for file_path in files:
        try:
            df = pd.read_excel(file_path)
        except Exception:
            continue
        if df.empty:
            continue
        normalized_cols = {_norm(c): c for c in df.columns}
        extracted_col = None
        expected_col = None
        extracted_candidates = ["tratamiento", "tratamiento actual", "tratamiento extraido", "tratamiento extraído"]
        expected_candidates = ["tratamiento esperado", "esperado tratamiento", "tratamiento objetivo", "tratamiento correcto"]
        for c in extracted_candidates:
            if c in normalized_cols:
                extracted_col = normalized_cols[c]
                break
        for c in expected_candidates:
            if c in normalized_cols:
                expected_col = normalized_cols[c]
                break
        if not extracted_col or not expected_col:
            continue
        for _, row in df[[extracted_col, expected_col]].dropna(how="any").iterrows():
            src = _norm(_clean_cell(row[extracted_col]))
            dst = _clean_cell(row[expected_col])
            if src and dst:
                alias_map[src] = dst
    if alias_map:
        logger.info("Mapa de alias de tratamiento cargado desde salidas históricas (%s reglas)", len(alias_map))
    return alias_map


def apply_residuo_dictionary_correction(df: pd.DataFrame, catalog: Dict[str, List[str]]) -> pd.DataFrame:
    if "Descripción Residuo" not in df.columns or "Código principal" not in df.columns:
        return df
    df = df.copy()
    if "Descripción Residuo Original" not in df.columns:
        df["Descripción Residuo Original"] = df["Descripción Residuo"]
    df["Código principal"] = df["Código principal"].apply(_normalize_code)
    df["Descripción Residuo"] = df.apply(
        lambda r: choose_canonical_description(
            r.get("Descripción Residuo", ""),
            r.get("Código principal", ""),
            catalog,
        ),
        axis=1,
    )
    return df


def defra_classification(desc_residuo: str, sin_movimientos: str = "", codigo_principal: str = "", tratamiento: str = "", destino: str = "") -> str:
    d = _norm(desc_residuo)
    cod = _clean_cell(codigo_principal)
    t = _norm(tratamiento)
    dst = _norm(destino)
    ctx = " ".join(x for x in [d, t, dst] if x).strip()
    if _norm(sin_movimientos) in ("si", "sí") or "periodo sin movimientos" in d or "período sin movimientos" in d:
        return "NA"
    def has_any(*terms: str) -> bool:
        return any(_norm(term) in ctx for term in terms)
    if cod == "15 01 01":
        return "Paper and board: mixed"
    if cod == "15 01 04":
        return "Metals"
    if cod == "21 04 04":
        if has_any("hdpe"):
            return "Plastics: HDPE (incl. forming)"
        if has_any("ldpe", "lldpe", "pee"):
            return "Plastics: LDPE and LLDPE (incl. forming)"
        if has_any("pet", "pete"):
            return "Plastics: PET (incl. forming)"
        if has_any("pp"):
            return "Plastics: PP (incl. forming)"
        if has_any("ps"):
            return "Plastics: PS (incl. forming)"
        if has_any("pvc"):
            return "Plastics: PVC (incl. forming)"
        return "Plastics: average plastics"
    if cod == "15 01 02":
        if has_any("film", "lamina", "lámina", "saco", "bolsa", "stretch"):
            return "Plastics: average plastic film"
        if has_any("hdpe"):
            return "Plastics: HDPE (incl. forming)"
        if has_any("ldpe", "lldpe", "pee"):
            return "Plastics: LDPE and LLDPE (incl. forming)"
        if has_any("pet", "pete"):
            return "Plastics: PET (incl. forming)"
        if has_any("pp"):
            return "Plastics: PP (incl. forming)"
        if has_any("ps"):
            return "Plastics: PS (incl. forming)"
        if has_any("pvc"):
            return "Plastics: PVC (incl. forming)"
        return "Plastics: average plastic rigid"
    if cod == "02 01 99":
        return "Organic: mixed food and garden waste"
    if cod == "02 01 02":
        return "Organic: food and drink waste"
    if cod == "02 02 02":
        return "Organic: food and drink waste"
    if cod == "02 02 03":
        return "Organic: food and drink waste"
    if cod == "02 02 04":
        return "Organic: food and drink waste"
    if cod == "20 01 39":
        return "Plastics: average plastics"
    if cod == "15 01 06":
        return "Commercial and industrial waste"
    if cod == "21 07 09":
        return "Organic: mixed food and garden waste"
    if cod == "21 07 01":
        return "Organic: mixed food and garden waste"
    if cod == "19 08 05":
        return "Commercial and industrial waste"
    if cod == "20 01 99":
        return "Household residual waste" if has_any("relleno sanitario", "residuo domiciliario", "residual") else "Commercial and industrial waste"
    if cod == "10 01 01":
        return "Commercial and industrial waste"
    if has_any("envases de papel y carton", "envases de papel y cartón"):
        return "Paper and board: mixed"
    if has_any("papel") and not has_any("carton", "cartón", "board"):
        return "Paper and board: paper"
    if has_any("carton", "cartón", "board") and not has_any("papel"):
        return "Paper and board: board"
    if has_any("envases metalicos", "envases metálicos", "metal", "acero", "chatarra"):
        return "Metals"
    if has_any("hdpe"):
        return "Plastics: HDPE (incl. forming)"
    if has_any("ldpe", "lldpe", "pee"):
        return "Plastics: LDPE and LLDPE (incl. forming)"
    if has_any("pet", "pete"):
        return "Plastics: PET (incl. forming)"
    if has_any("pp"):
        return "Plastics: PP (incl. forming)"
    if has_any("ps"):
        return "Plastics: PS (incl. forming)"
    if has_any("pvc"):
        return "Plastics: PVC (incl. forming)"
    if has_any("plastico", "plástico"):
        if has_any("film", "lamina", "lámina", "saco", "bolsa", "stretch"):
            return "Plastics: average plastic film"
        if has_any("envase", "bidon", "bidón", "tambor", "contenedor"):
            return "Plastics: average plastic rigid"
        return "Plastics: average plastics"
    if has_any("compost", "organico", "orgánico", "resto de alimento", "restos de alimento"):
        return "Organic: mixed food and garden waste"
    if has_any("lodos del tratamiento in situ de efluentes", "degradacion anaerobica", "degradación anaeróbica"):
        return "Organic: food and drink waste"
    if has_any("aguas residuales urbanas", "ptas"):
        return "Commercial and industrial waste"
    if has_any("ceniza", "escoria", "caldera"):
        return "Commercial and industrial waste"
    if has_any("relleno sanitario", "fracciones no especificadas", "residuos no especificados"):
        return "Household residual waste"
    return "Commercial and industrial waste"


def extract_any_pdf(pdf_path: str) -> Tuple[List[Dict[str, str]], Dict[str, str], str]:
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join([(p.extract_text() or "") for p in pdf.pages])
    # En este módulo se procesan certificados SINADER; siempre intentamos el parser SINADER
    # incluso si el texto nativo viene pobre (PDF escaneado / extracción textual limitada).
    rows, meta = extract_sinader_from_pdf(pdf_path)
    kind = "SINADER" if rows or is_sinader_pdf(full_text) else "UNKNOWN"
    return rows, meta, kind


DEFRA_RESIDUO_CATEGORIAS = [
    "Aggregates",
    "Average construction",
    "Asbestos",
    "Asphalt",
    "Bricks",
    "Concrete",
    "Insulation",
    "Metals",
    "Soils",
    "Mineral oil",
    "Plasterboard",
    "Tyres",
    "Wood",
    "Books",
    "Glass",
    "Clothing",
    "Household residual waste",
    "Organic: food and drink waste",
    "Organic: garden waste",
    "Organic: mixed food and garden waste",
    "Commercial and industrial waste",
    "WEEE - fridges and freezers",
    "WEEE - large",
    "WEEE - mixed",
    "WEEE - small",
    "Batteries",
    "Metal: aluminium cans and foil (excl. forming)",
    "Metal: mixed cans",
    "Metal: scrap metal",
    "Metal: steel cans",
    "Plastics: average plastics",
    "Plastics: average plastic film",
    "Plastics: average plastic rigid",
    "Plastics: HDPE (incl. forming)",
    "Plastics: LDPE and LLDPE (incl. forming)",
    "Plastics: PET (incl. forming)",
    "Plastics: PP (incl. forming)",
    "Plastics: PS (incl. forming)",
    "Plastics: PVC (incl. forming)",
    "Paper and board: board",
    "Paper and board: mixed",
    "Paper and board: paper",
]

DEFRA_RESIDUO_ES = {
    "Aggregates": "Áridos",
    "Average construction": "Residuos de construcción promedio",
    "Asbestos": "Asbesto",
    "Asphalt": "Asfalto",
    "Bricks": "Ladrillos",
    "Concrete": "Hormigón",
    "Insulation": "Aislantes",
    "Metals": "Metales",
    "Soils": "Suelos",
    "Mineral oil": "Aceite mineral",
    "Plasterboard": "Yeso-cartón",
    "Tyres": "Neumáticos",
    "Wood": "Madera",
    "Books": "Libros",
    "Glass": "Vidrio",
    "Clothing": "Ropa",
    "Household residual waste": "Residuos domiciliarios residuales",
    "Organic: food and drink waste": "Residuos orgánicos de alimentos y bebidas",
    "Organic: garden waste": "Residuos orgánicos de jardín",
    "Organic: mixed food and garden waste": "Residuos orgánicos mixtos (alimentos y jardín)",
    "Commercial and industrial waste": "Residuos comerciales e industriales",
    "WEEE - fridges and freezers": "RAEE - refrigeradores y congeladores",
    "WEEE - large": "RAEE - equipos grandes",
    "WEEE - mixed": "RAEE - mixto",
    "WEEE - small": "RAEE - equipos pequeños",
    "Batteries": "Baterías",
    "Metal: aluminium cans and foil (excl. forming)": "Metal: latas y foil de aluminio (excluye conformado)",
    "Metal: mixed cans": "Metal: latas mixtas",
    "Metal: scrap metal": "Metal: chatarra",
    "Metal: steel cans": "Metal: latas de acero",
    "Plastics: average plastics": "Plásticos: promedio",
    "Plastics: average plastic film": "Plásticos: film promedio",
    "Plastics: average plastic rigid": "Plásticos: rígido promedio",
    "Plastics: HDPE (incl. forming)": "Plásticos: HDPE (incluye conformado)",
    "Plastics: LDPE and LLDPE (incl. forming)": "Plásticos: LDPE y LLDPE (incluye conformado)",
    "Plastics: PET (incl. forming)": "Plásticos: PET (incluye conformado)",
    "Plastics: PP (incl. forming)": "Plásticos: PP (incluye conformado)",
    "Plastics: PS (incl. forming)": "Plásticos: PS (incluye conformado)",
    "Plastics: PVC (incl. forming)": "Plásticos: PVC (incluye conformado)",
    "Paper and board: board": "Papel y cartón: cartón",
    "Paper and board: mixed": "Papel y cartón mixto",
    "Paper and board: paper": "Papel",
}

DEFRA_TRATAMIENTO_ES = {
    "Re-use": "Reutilización",
    "Open-loop": "Reciclaje circuito abierto",
    "Closed-loop": "Reciclaje circuito cerrado",
    "Combustion": "Combustión",
    "Landfill": "Relleno sanitario / disposición en vertedero",
    "Anaerobic digestion": "Digestión anaeróbica",
}


def derivar_ano_mes(periodo_declarado: str, fuente_pdf: str) -> Tuple[Optional[str], Optional[str]]:
    periodo_txt = _clean_cell(periodo_declarado)
    m = re.search(r"\b(\d{1,2})\s*[/\-]\s*(20\d{2})\b", periodo_txt)
    if m:
        mes = int(m.group(1))
        anio = m.group(2)
        if 1 <= mes <= 12:
            return anio, f"{mes:02d}"
    periodo_norm = _norm(periodo_txt)
    year_match = re.search(r"\b(20\d{2})\b", periodo_norm)
    month_num = _parse_spanish_month(periodo_norm)
    if year_match and month_num:
        return year_match.group(1), f"{month_num:02d}"
    parsed = parse_period_from_filename(_clean_cell(fuente_pdf))
    if parsed:
        mm, yyyy = parsed.split("/")
        return yyyy, mm
    fuente_norm = _norm(fuente_pdf)
    year_match_file = re.search(r"(20\d{2})", fuente_norm)
    month_file = _parse_spanish_month(fuente_norm)
    if year_match_file and month_file:
        return year_match_file.group(1), f"{month_file:02d}"
    return None, None


def clasificar_defra_residuo(codigo_residuo: str, residuo_oficial: str) -> Optional[str]:
    codigo = _normalize_code(codigo_residuo)
    residuo_norm = _norm(residuo_oficial)
    # Reglas determinísticas por código SINADER observado en corpus.
    if codigo == "15 01 01":
        return "Paper and board: mixed"
    if codigo == "15 01 02":
        return "Plastics: average plastics"
    if codigo == "15 01 04":
        # Convención única elegida: latas/metales de envases -> "Metal: mixed cans".
        return "Metal: mixed cans"
    if codigo == "15 01 06":
        # Envases mezclados se reportan como flujo mixto C&I para mantener consistencia.
        return "Commercial and industrial waste"
    if codigo in {"19 08 05", "02 02 04"}:
        # Lodos de PTAS/efluentes: flujo industrial para este proyecto.
        return "Commercial and industrial waste"
    if codigo in {"21 07 01", "21 07 09"}:
        return "Organic: food and drink waste"
    if codigo == "20 01 99":
        return "Commercial and industrial waste"
    if codigo == "10 01 01":
        return "Commercial and industrial waste"
    if codigo == "21 04 04":
        return "Plastics: average plastics"
    if codigo == "02 01 99":
        return "Organic: mixed food and garden waste"
    # Fallback determinístico por residuo oficial (sin fuzzy aleatorio).
    if "papel" in residuo_norm or "carton" in residuo_norm:
        return "Paper and board: mixed"
    if "plast" in residuo_norm:
        return "Plastics: average plastics"
    if "metal" in residuo_norm:
        return "Metal: mixed cans"
    if "organ" in residuo_norm or "lodo" in residuo_norm:
        return "Organic: food and drink waste"
    if "ceniza" in residuo_norm or "escoria" in residuo_norm:
        return "Commercial and industrial waste"
    if "residuo no especificado" in residuo_norm or "fracciones no especificadas" in residuo_norm:
        return "Commercial and industrial waste"
    return None


def traducir_defra_residuo(defra_residuo: str) -> Optional[str]:
    key = _clean_cell(defra_residuo)
    if not key:
        return None
    return DEFRA_RESIDUO_ES.get(key)


def clasificar_defra_tratamiento(tratamiento: str) -> Optional[str]:
    txt = _norm(tratamiento)
    if not txt:
        return None
    if "degradacion anaerobica" in txt or "anaerobica" in txt or "compostaje" in txt:
        return "Anaerobic digestion"
    if "relleno sanitario" in txt or "disposicion final" in txt or "monorelleno" in txt:
        return "Landfill"
    if "residuos municipales asimilables a domiciliarios" in txt or "sitio de escombros de la construccion" in txt:
        return "Landfill"
    if "reciclaje" in txt:
        return "Open-loop"
    if "pretratamiento" in txt:
        # Regla documentada: pretratamiento se aproxima a flujo previo de reciclaje.
        return "Open-loop"
    return None


def traducir_defra_tratamiento(defra_tratamiento: str) -> Optional[str]:
    key = _clean_cell(defra_tratamiento)
    if not key:
        return None
    return DEFRA_TRATAMIENTO_ES.get(key)


def process_folder(input_folder: str, output_excel: str) -> pd.DataFrame:
    pdf_paths = sorted([str(p) for p in Path(input_folder).rglob("*.pdf")])
    if not pdf_paths:
        raise FileNotFoundError(f"No se encontraron PDFs en: {input_folder}")
    all_rows: List[Dict[str, str]] = []
    for p in pdf_paths:
        try:
            rows, meta, kind = extract_any_pdf(p)
            logger.info("Procesado: %s | tipo=%s | filas_detalle=%s", Path(p).name, kind, len(rows))
            if kind == "SINADER" and not rows:
                rows = [{
                    "N.": "0",
                    "Descripción Residuo": "SIN FILAS DETECTADAS",
                    "Código principal": "",
                    "Peligrosidad": "",
                    "Cantidad (Kg)": "",
                    "Estado contenedor": "",
                    "Contenedor": "",
                    "Tratamiento": "",
                    "Destino": "",
                    "Transportista": "",
                    "Patente": "",
                    "Sin movimientos": "NO",
                    "Texto fila original": "",
                    "Parsing_OK": "NO",
                    "Tratamiento_confiable": "NO",
                    "Destino_confiable": "NO",
                }]
            for r in rows:
                merged = dict(meta)
                merged.update(r)
                merged["TipoPDF"] = kind
                all_rows.append(merged)
        except Exception as e:
            logger.exception("Error procesando %s: %s", p, e)
    df = pd.DataFrame(all_rows)
    preferred_cols = [
        "FuentePDF", "TipoPDF", "Periodo declarado", "Folio", "Establecimiento", "Razón social",
        "RUT Titular", "Realizado por", "Tipo", "Estado", "Código identificador", "Región", "Comuna",
        "Sin movimientos", "N.", "Descripción Residuo", "Descripción Residuo Original", "Código principal",
        "Peligrosidad", "Cantidad (Kg)", "Tratamiento", "Destino", "Transportista", "Patente",
        "Contenedor", "Estado contenedor", "Texto fila original", "Parsing_OK", "Tratamiento_confiable", "Destino_confiable", "DEFRA_base", "DEFRA", "DEFRA_source", "DEFRA_confiable",
    ]
    cols = [c for c in preferred_cols if c in df.columns] + [c for c in df.columns if c not in preferred_cols]
    df = df[cols] if not df.empty else pd.DataFrame(columns=preferred_cols)
    if not df.empty and df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()].copy()
    if "Cantidad (Kg)" in df.columns:
        df["Cantidad (Kg)"] = df["Cantidad (Kg)"].apply(_to_float_kg)
    catalog = load_residuo_catalog()
    df = apply_residuo_dictionary_correction(df, catalog)
    known_treatments = load_treatment_level3_terms()
    treatment_alias_map = load_treatment_alias_map()
    if "Tratamiento" in df.columns and known_treatments:
        if "Tratamiento Original" not in df.columns:
            df["Tratamiento Original"] = df["Tratamiento"]
        df["Tratamiento"] = df["Tratamiento"].apply(
            lambda x: treatment_alias_map.get(_norm(_clean_cell(x)), choose_canonical_treatment(x, known_treatments))
        )
    treatment_defra_map = load_treatment_defra_map()
    if "DEFRA" not in df.columns:
        df["DEFRA"] = ""
    if "DEFRA_source" not in df.columns:
        df["DEFRA_source"] = ""
    if "DEFRA_confiable" not in df.columns:
        df["DEFRA_confiable"] = "NO"
    defra_base_values = [
        defra_classification(
            desc_residuo=r.get("Descripción Residuo", ""),
            sin_movimientos=r.get("Sin movimientos", ""),
            codigo_principal=r.get("Código principal", ""),
            tratamiento="",
            destino="",
        )
        for _, r in df.iterrows()
    ]
    df["DEFRA_base"] = pd.Series(defra_base_values, index=df.index, dtype="object")
    df["DEFRA"] = df["DEFRA_base"]
    df["DEFRA_source"] = df["DEFRA_base"].apply(lambda x: "heredada_base" if _clean_cell(x) else "sin_clasificar")
    if "Tratamiento" in df.columns:
        def _treatment_is_reliable(row) -> bool:
            t = _norm(_clean_cell(row.get("Tratamiento", "")))
            txt = _norm(_clean_cell(row.get("Texto fila original", "")))
            flag = _norm(_clean_cell(row.get("Tratamiento_confiable", "no")))
            parsing_flag = _norm(_clean_cell(row.get("Parsing_OK", "no")))
            if flag not in {"si", "yes", "true"}:
                return False
            if parsing_flag not in {"si", "yes", "true"}:
                return False
            if not t:
                return False
            bad_tokens = ["cantidad residuo", "tipo tratamiento destino", "transportista patente", "destino transportista"]
            if any(b in t for b in bad_tokens):
                return False
            if any(b in txt for b in bad_tokens):
                return False
            return True

        def _resolve_defra(row: pd.Series) -> Tuple[str, str]:
            base_value = _clean_cell(row.get("DEFRA_base", ""))
            treatment_value = _clean_cell(row.get("Tratamiento", ""))
            if _treatment_is_reliable(row):
                mapped = _clean_cell(map_treatment_to_defra(treatment_value, treatment_defra_map))
                if mapped:
                    return mapped, "ajustada_tratamiento"
            if base_value:
                return base_value, "heredada_base"
            fallback = _clean_cell(defra_classification(
                desc_residuo=row.get("Descripción Residuo", ""),
                sin_movimientos=row.get("Sin movimientos", ""),
                codigo_principal=row.get("Código principal", ""),
                tratamiento="",
                destino="",
            ))
            if fallback:
                return fallback, "ajustada_regla_codigo"
            return "", "sin_clasificar"

        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated()].copy()
        resolved = [_resolve_defra(row) for _, row in df.iterrows()]
        df["DEFRA"] = pd.Series([x[0] for x in resolved], index=df.index, dtype="object")
        df["DEFRA_source"] = pd.Series([x[1] for x in resolved], index=df.index, dtype="object")
    df["DEFRA_confiable"] = df["DEFRA_source"].apply(
        lambda x: "SI" if _clean_cell(x) in {"heredada_base", "ajustada_tratamiento", "ajustada_regla_codigo"} else "NO"
    )
    df_v2 = pd.DataFrame({
        "FuentePDF": df.get("FuentePDF", pd.Series(dtype=str)).fillna("").astype(str),
        "Folio": df.get("Folio", pd.Series(dtype=str)).fillna(""),
        "Establecimiento": df.get("Establecimiento", pd.Series(dtype=str)).fillna(""),
        "Periodo declarado": df.get("Periodo declarado", pd.Series(dtype=str)).fillna(""),
        "sin_movimientos": df.get("Sin movimientos", pd.Series(dtype=str)).fillna("").astype(str).str.upper().map(
            {"SI": True, "TRUE": True, "YES": True, "NO": False, "FALSE": False}
        ),
        "Código principal": df.get("Código principal", pd.Series(dtype=str)).fillna(""),
        "Residuo extraído": df.get("Descripción Residuo", pd.Series(dtype=str)).fillna(""),
        "Cantidad (Kg)": df.get("Cantidad (Kg)", pd.Series(dtype=float)).astype("float64"),
        "Tratamiento": df.get("Tratamiento", pd.Series(dtype=str)).fillna(""),
        "metodo_usado": df.get("Row_strategy", pd.Series(dtype=str)).fillna(""),
        "requiere_revision": (
            (df.get("Parsing_OK", pd.Series(dtype=str)).fillna("NO").astype(str).str.upper() != "SI")
            | (df.get("Tratamiento", pd.Series(dtype=str)).fillna("").astype(str).str.strip() == "")
        ),
        "observacion": df.get("Parsing_OK", pd.Series(dtype=str)).fillna("NO").astype(str).str.upper().map(
            {"SI": "", "NO": "Fila no parseada"}
        ),
        "fila_original": df.get("Texto fila original", pd.Series(dtype=str)).fillna(""),
    })
    df_v2.loc[df_v2["sin_movimientos"] == True, "observacion"] = "Período Sin Movimientos"
    df_v2["metodo_usado"] = df_v2["metodo_usado"].where(
        df_v2["metodo_usado"].astype(str).str.strip() != "",
        df.get("_treatment_source", pd.Series(dtype=str)).fillna(""),
    )
    def _normalizar_tratamiento_v2(x: str) -> str:
        txt = _clean_cell(x)
        if not txt:
            return ""
        txt_norm = _norm(txt)
        for t in TRATAMIENTOS_CONOCIDOS_V2:
            if _norm(t) in txt_norm:
                return t
        return txt
    df_v2["Tratamiento"] = df_v2["Tratamiento"].apply(_normalizar_tratamiento_v2)
    df_v2["Residuo oficial"] = df_v2["Código principal"].map(lambda x: MAPA_RESIDUOS_SINADER_V2.get(_normalize_code(x), ""))
    df_v2["Residuo oficial"] = df_v2["Residuo oficial"].where(
        df_v2["Residuo oficial"].astype(str).str.strip() != "",
        df_v2["Residuo extraído"],
    )
    df_v2["codigo_sin_mapa_residuo"] = (
        df_v2["Código principal"].astype(str).str.strip() != ""
    ) & (~df_v2["Código principal"].astype(str).map(lambda x: _normalize_code(x) in MAPA_RESIDUOS_SINADER_V2))
    # Año/Mes desde Periodo declarado; fallback al nombre del archivo.
    anio_mes = [
        derivar_ano_mes(periodo_declarado=r.get("Periodo declarado", ""), fuente_pdf=r.get("FuentePDF", ""))
        for _, r in df_v2.iterrows()
    ]
    df_v2["Año"] = [x[0] for x in anio_mes]
    df_v2["Mes"] = [x[1] for x in anio_mes]
    # Clasificación DEFRA por residuo y tratamiento (determinística para códigos del corpus SINADER).
    df_v2["DEFRA_Residuo"] = df_v2.apply(
        lambda r: clasificar_defra_residuo(r.get("Código principal", ""), r.get("Residuo oficial", "")),
        axis=1,
    )
    df_v2["DEFRA_Residuo_ES"] = df_v2["DEFRA_Residuo"].apply(traducir_defra_residuo)
    df_v2["DEFRA_Tratamiento"] = df_v2["Tratamiento"].apply(clasificar_defra_tratamiento)
    df_v2["DEFRA_Tratamiento_ES"] = df_v2["DEFRA_Tratamiento"].apply(traducir_defra_tratamiento)
    df_v2["requiere_revision"] = (
        df_v2["requiere_revision"]
        | df_v2["codigo_sin_mapa_residuo"]
        | df_v2["DEFRA_Residuo"].isna()
        | df_v2["DEFRA_Tratamiento"].isna()
        | df_v2["observacion"].astype(str).str.strip().ne("")
    )
    df_v2["observacion"] = df_v2["observacion"].astype(str)
    df_v2.loc[df_v2["DEFRA_Residuo"].isna(), "observacion"] = (
        df_v2["observacion"].where(df_v2["observacion"].astype(str).str.strip() != "", "Pendiente DEFRA_Residuo")
    )
    df_v2.loc[df_v2["DEFRA_Tratamiento"].isna(), "observacion"] = (
        df_v2["observacion"].where(df_v2["observacion"].astype(str).str.strip() != "", "Pendiente DEFRA_Tratamiento")
    )
    df_v2["sin_movimientos"] = df_v2["sin_movimientos"].where(df_v2["sin_movimientos"].notna(), False)
    export_cols = [
        "Año", "Mes", "Código principal", "Residuo oficial", "Cantidad (Kg)", "Tratamiento",
        "DEFRA_Residuo", "DEFRA_Residuo_ES", "DEFRA_Tratamiento", "DEFRA_Tratamiento_ES",
        "FuentePDF", "Folio", "Establecimiento", "fila_original", "metodo_usado",
        "requiere_revision", "observacion",
        "Periodo declarado", "sin_movimientos", "Residuo extraído", "codigo_sin_mapa_residuo",
    ]
    df_v2 = df_v2[export_cols]
    df_ok = df_v2[(df_v2["sin_movimientos"] == False) & (df_v2["Código principal"].astype(str).str.strip() != "")].copy()
    df_revision = df_v2[
        (df_v2["observacion"].astype(str).str.strip() != "")
        | (df_v2["Tratamiento"].astype(str).str.strip() == "")
        | (df_v2["requiere_revision"] == True)
        | (df_v2["codigo_sin_mapa_residuo"] == True)
    ].copy()
    Path(output_excel).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_v2.to_excel(writer, sheet_name="Completo", index=False)
        df_ok.to_excel(writer, sheet_name="OK", index=False)
        df_revision.to_excel(writer, sheet_name="Revision", index=False)
    logger.info("Excel generado: %s | filas=%s", output_excel, len(df_v2))
    return df_v2


def summarize_parsing_quality(df: pd.DataFrame, known_treatments: Optional[List[str]] = None) -> Dict[str, int]:
    if df is None or df.empty:
        return {
            "rows_total": 0,
            "rows_parsing_no": 0,
            "rows_destino_contaminado": 0,
            "rows_tratamiento_fuera_catalogo": 0,
            "rows_defra_vacia": 0,
            "rows_defra_distinta_base": 0,
        }
    known_treatments = known_treatments or load_treatment_level3_terms() or STRONG_TREATMENT_CATALOG
    known_treatment_norm = {_norm(t) for t in known_treatments if _clean_cell(t)}
    forbidden_dst = [_norm(x) for x in DESTINATION_NOISE_FRAGMENTS]
    forbidden_trt = [_norm(x) for x in TREATMENT_NOISE_FRAGMENTS]

    def _dst_contaminated(row: pd.Series) -> bool:
        dst = _norm(_clean_cell(row.get("Destino", "")))
        if not dst:
            return False
        if any(f and f in dst for f in forbidden_dst):
            return True
        desc = _norm(_clean_cell(row.get("Descripción Residuo", "")))
        desc_tokens = [tok for tok in desc.split() if len(tok) >= 8]
        return sum(1 for tok in desc_tokens if tok in dst) >= 2

    def _trt_outside_catalog(row: pd.Series) -> bool:
        trt = _norm(_clean_cell(row.get("Tratamiento", "")))
        if not trt:
            return False
        if any(f and f in trt for f in forbidden_trt):
            return True
        return trt not in known_treatment_norm

    return {
        "rows_total": int(len(df)),
        "rows_parsing_no": int((df.get("Parsing_OK", pd.Series(dtype=str)).fillna("NO").astype(str).str.upper() != "SI").sum()),
        "rows_destino_contaminado": int(df.apply(_dst_contaminated, axis=1).sum()),
        "rows_tratamiento_fuera_catalogo": int(df.apply(_trt_outside_catalog, axis=1).sum()),
        "rows_defra_vacia": int((df.get("DEFRA", pd.Series(dtype=str)).fillna("").astype(str).str.strip() == "").sum()),
        "rows_defra_distinta_base": int((df.get("DEFRA", pd.Series(dtype=str)).fillna("").astype(str).str.strip() != df.get("DEFRA_base", pd.Series(dtype=str)).fillna("").astype(str).str.strip()).sum()),
    }


def _selfcheck_reconstruction_samples() -> Dict[str, bool]:
    sample_lines = [
        "Residuo Cantidad (kg) Tipo Tratamiento Destino Transportista Patente",
        "02 02 04 | Lodos del tratamiento in",
        "situ de efluentes 26450 kg Degradación",
        "Anaeróbica ECOPRIAL 1|",
        "19 08 05 | Lodos del tratamiento de aguas residuales urbanas 84180 kg Recepción de Lodos en PTAS PLANTA DE TRATAMIENTO DE AGUAS SERVIDAS DE CASTRO 1|",
        "15 01 01 | Envases de papel y cartón 165 kg Reciclaje de papel, cartón y productos de papel ECOFIBRAS SUCURSAL PUERTO MONTT 1|",
        "10 01 01 | Cenizas del hogar 4260 kg Sitio de Escombros de la Construcción ESCOMBRERA TRESOL 1|",
        "20 01 99 | Otras fracciones no especificadas en otra categoría 4210 kg Relleno sanitario CONSORCIO COLLIPULLI 1|",
        "21 04 04 | Residuos de plásticos (HDPE, PEE, PETE, PVC) excepto planzas, boyas, flotadores, redes y cabos 29756 kg Reciclaje de plásticos PLASTICOS DEL SUR SPA 1|",
        "02 01 99 | Residuos no especificados en otra categoría 8620 kg Compostaje Centro Crucero 1|",
        "02 02 03 | Subproductos hidrobiológicos 9000 kg Reciclaje de residuos hidrobiológicos para consumo animal SALMONOIL S.A. 1|",
        "20 01 39 | Plásticos mixtos 1450 kg Pretratamiento de plásticos REPLACAR 1|",
        "15 01 06 | Residuos mixtos 3400 kg Residuos municipales asimilables a domiciliarios Estación de transferencia 1|",
        "15 01 06 | Residuos mixtos 900 kg Disposición final ECOBIO 1|",
        "21 07 09 | Biosólidos 700 kg Compostaje Cancha compostaje Los Rebalses del Sur 1|",
    ]
    blocks = _reconstruct_row_blocks_from_lines(sample_lines)
    parsed = [_parse_reconstructed_row_block(b, STRONG_TREATMENT_CATALOG) for b in blocks]
    parsed = [p for p in parsed if p]
    p_by_code = {p["Código principal"]: p for p in parsed}
    return {
        "multiline_row_reconstructed": len(parsed) >= 5 and p_by_code.get("02 02 04", {}).get("Tratamiento") == "Degradación Anaeróbica",
        "code_desc_qty_split": bool(parsed and parsed[0]["Descripción Residuo"] and parsed[0]["Cantidad (Kg)"]),
        "cross_page_like_continuity": len(blocks) >= 5,
        "header_line_filtered": all("residuo cantidad" not in _norm(p.get("Texto fila original", "")) for p in parsed),
        "known_cases_treatment_destination": (
            p_by_code.get("19 08 05", {}).get("Tratamiento") == "Recepción de Lodos en PTAS"
            and "PLANTA DE TRATAMIENTO" in p_by_code.get("19 08 05", {}).get("Destino", "")
            and p_by_code.get("15 01 01", {}).get("Tratamiento") == "Reciclaje de papel, cartón y productos de papel"
            and "ECOFIBRAS SUCURSAL PUERTO MONTT" in p_by_code.get("15 01 01", {}).get("Destino", "")
            and p_by_code.get("10 01 01", {}).get("Tratamiento") == "Sitio de Escombros de la Construcción"
            and "ESCOMBRERA TRESOL" in p_by_code.get("10 01 01", {}).get("Destino", "")
            and p_by_code.get("20 01 99", {}).get("Tratamiento") == "Relleno sanitario"
            and "CONSORCIO COLLIPULLI" in p_by_code.get("20 01 99", {}).get("Destino", "")
            and p_by_code.get("21 04 04", {}).get("Destino") == "PLASTICOS DEL SUR SPA"
            and _norm(p_by_code.get("02 01 99", {}).get("Destino", "")) == _norm("Centro Crucero")
            and p_by_code.get("02 02 03", {}).get("Tratamiento") == "Reciclaje de residuos hidrobiológicos para consumo animal"
            and p_by_code.get("20 01 39", {}).get("Destino") == "REPLACAR"
            and _norm(p_by_code.get("15 01 06", {}).get("Destino", "")) in {_norm("Estación de transferencia"), _norm("ECOBIO")}
            and _norm(p_by_code.get("21 07 09", {}).get("Destino", "")) in {
                _norm("CANCHA COMPOSTAJE LOS REBALSES DEL SUR"),
                _norm("CANCHA LOS REBALSES DEL SUR"),
            }
        ),
        "confidence_flags_working": (
            p_by_code.get("19 08 05", {}).get("Tratamiento_confiable") == "SI"
            and p_by_code.get("19 08 05", {}).get("Destino_confiable") == "SI"
            and p_by_code.get("15 01 01", {}).get("Tratamiento_confiable") == "SI"
            and p_by_code.get("10 01 01", {}).get("Destino_confiable") == "SI"
        ),
    }


# =========================
# API PARA APP.PY
# =========================
def extract_sinader_data(pdf_path: str) -> "pd.DataFrame":
    """Función principal llamada por app.py. Devuelve DataFrame con columnas estándar."""
    try:
        detail_rows, meta = extract_sinader_from_pdf(pdf_path)
        if not detail_rows:
            return pd.DataFrame()
        df = pd.DataFrame(detail_rows)
        if "Cantidad (Kg)" in df.columns:
            df["Cantidad (Kg)"] = df["Cantidad (Kg)"].apply(_to_float_kg)
        df = df.rename(columns={
            "Descripción Residuo": "Residuo",
            "Tratamiento": "Tipo Tratamiento",
            "Cantidad (Kg)": "Cantidad (kg)",
        })
        df["Periodo"] = meta.get("Periodo declarado", "")
        df["Establecimiento"] = meta.get("Establecimiento", "")
        return df
    except Exception as e:
        logger.error(f"Error procesando SINADER: {str(e)}")
        return pd.DataFrame()

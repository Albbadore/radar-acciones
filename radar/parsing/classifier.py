"""Objective classification of SEC filings and news into catalyst categories."""
from __future__ import annotations

import re

# 8-K items -> (categoria, impacto). 9.01 (exhibits) is ignored.
ITEM_CATEGORIES: dict[str, tuple[str, str]] = {
    "1.01": ("Contrato/acuerdo material", "alto"),
    "1.02": ("Terminacion de acuerdo material", "medio"),
    "1.03": ("Quiebra o concurso", "alto"),
    "2.01": ("Adquisicion/venta de activos", "alto"),
    "2.02": ("Resultados empresariales", "alto"),
    "2.03": ("Deuda/obligacion financiera", "bajo"),
    "2.04": ("Aceleracion de deuda", "medio"),
    "2.06": ("Deterioro de activos", "bajo"),
    "3.01": ("Cumplimiento/incumplimiento de listado (Nasdaq/NYSE)", "medio"),
    "3.02": ("Financiacion (venta no registrada de acciones)", "bajo"),
    "4.01": ("Cambio de auditor", "bajo"),
    "4.02": ("Reexpresion de estados financieros", "medio"),
    "5.01": ("Cambio de control", "alto"),
    "5.02": ("Cambios en consejo/directivos", "bajo"),
    "7.01": ("Comunicado oficial (Reg FD)", "medio"),
    "8.01": ("Otros hechos relevantes (comunicado oficial)", "medio"),
}

# Items that on their own usually just implement a reverse split
# (already counted by signal 1, so not a catalyst by themselves).
SPLIT_ONLY_ITEMS = {"3.03", "5.03", "5.07", "9.01"}

FORM_CATEGORIES: list[tuple[re.Pattern, tuple[str, str]]] = [
    (re.compile(r"^(424B\d?|S-1|S-3|F-1|F-3)(/A)?$"), ("Financiacion/oferta de acciones", "bajo")),
    (re.compile(r"^(10-Q|10-K|20-F|40-F)(/A)?$"), ("Resultados/informe periodico", "medio")),
    (re.compile(r"^6-K(/A)?$"), ("Comunicado oficial (6-K)", "medio")),
    (re.compile(r"^(S-4|DEFM14A|PREM14A|SC TO-T|SC 14D9)(/A)?$"), ("Adquisicion/fusion", "alto")),
    (re.compile(r"^(SC 13D|SC 13G|SCHEDULE 13D|SCHEDULE 13G)(/A)?$"), ("Participacion significativa (13D/13G)", "medio")),
    (re.compile(r"^25(-NSE)?$"), ("Exclusion de cotizacion", "alto")),
    (re.compile(r"^NT 10-[KQ]$"), ("Presentacion tardia de informe", "bajo")),
]

# Keyword categories for filing text and news headlines (most specific first).
TEXT_CATEGORIES: list[tuple[re.Pattern, tuple[str, str]]] = [
    (re.compile(r"complete response letter|\bCRL\b|refus(e|al) to file|FDA (declin|reject)", re.I),
     ("FDA: rechazo o respuesta negativa", "alto")),
    (re.compile(r"FDA (approv|clear|grant)|approv\w+ (by|from) the (U\.S\. )?FDA|510\(k\) clearance", re.I),
     ("FDA: aprobacion/autorizacion", "alto")),
    (re.compile(r"\bFDA\b|Food and Drug Administration|\bPDUFA\b|\bIND\b|\bNDA\b|\bBLA\b|fast track|"
                r"breakthrough (therapy|device)|orphan drug", re.I),
     ("FDA/regulatorio sanitario", "alto")),
    (re.compile(r"clinical trial|phase\s+(1|2|3|i{1,3})\b|topline|primary endpoint|first patient dosed", re.I),
     ("Ensayo clinico", "alto")),
    (re.compile(r"\bmerger agreement|business combination agreement|agreed to acquire|"
                r"definitive agreement to (acquire|be acquired)|tender offer for", re.I),
     ("Adquisicion/fusion", "alto")),
    (re.compile(r"\bcontract\b|purchase order|\bawarded\b|supply agreement|\border\b.{0,20}\$\d", re.I),
     ("Contrato", "alto")),
    (re.compile(r"strategic (partnership|collaboration|alliance|agreement)|partnership agreement|collaboration agreement|"
                r"licens\w+ agreement|memorandum of understanding|\bMOU\b|joint venture", re.I),
     ("Acuerdo estrategico", "alto")),
    (re.compile(r"(first|second|third|fourth) quarter|financial results|fiscal (year|quarter) \d{4}|"
                r"record revenue|earnings", re.I),
     ("Resultados empresariales", "alto")),
    (re.compile(r"\bpatent", re.I), ("Patente", "medio")),
    (re.compile(r"regulatory approval|CE mark|\bEMA\b|marketing authori[sz]ation|approval from", re.I),
     ("Resultado regulatorio", "alto")),
    (re.compile(r"minimum bid price|regained compliance|listing rule|delist|non-?compliance|hearings panel", re.I),
     ("Cumplimiento/incumplimiento de listado (Nasdaq/NYSE)", "medio")),
    (re.compile(r"registered direct|private placement|public offering|underwritten|at[- ]the[- ]market|"
                r"securities purchase agreement|\bPIPE\b|warrant (inducement|exercise)", re.I),
     ("Financiacion", "bajo")),
    (re.compile(r"convertible note|promissory note|credit facility|\bdebenture|term loan", re.I),
     ("Deuda", "bajo")),
]

PRESS_WIRES = (
    "globenewswire", "globe newswire", "pr newswire", "prnewswire", "business wire",
    "businesswire", "accesswire", "access newswire", "newsfile", "accesswire",
)


def classify_items(items: str | list[str]) -> list[tuple[str, str]]:
    codes = items.split(",") if isinstance(items, str) else list(items)
    codes = [c.strip() for c in codes if c.strip()]
    return [ITEM_CATEGORIES[c] for c in codes if c in ITEM_CATEGORIES]


def is_split_only(items: str | list[str]) -> bool:
    codes = items.split(",") if isinstance(items, str) else list(items)
    codes = {c.strip() for c in codes if c.strip()}
    return bool(codes) and codes <= SPLIT_ONLY_ITEMS


def classify_form(form: str) -> tuple[str, str] | None:
    for pattern, category in FORM_CATEGORIES:
        if pattern.match(form.strip().upper()):
            return category
    return None


_ITEM_START = re.compile(r"\bItem\s+\d\.\d{2}", re.I)
_SIGNATURES = re.compile(r"\bSIGNATURES?\b")


def filing_body(text: str) -> str:
    """Text between the first 'Item x.xx' and the signatures: skips the 8-K
    cover page, whose boilerplate (Rule 425, tender offer...) is not news."""
    start = _ITEM_START.search(text)
    body = text[start.start():] if start else text
    end = _SIGNATURES.search(body)
    return body[: end.start()] if end else body


def classify_text(text: str) -> list[tuple[str, str]]:
    """All matching categories (first match = most specific)."""
    seen: list[tuple[str, str]] = []
    for pattern, category in TEXT_CATEGORIES:
        if pattern.search(text) and category not in seen:
            seen.append(category)
    return seen


def is_press_wire(provider: str) -> bool:
    low = provider.lower()
    return any(w in low for w in PRESS_WIRES)

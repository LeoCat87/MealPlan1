import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from io import BytesIO
from datetime import date, timedelta
import json
from typing import List, Dict, Any
import gspread
from google.oauth2.service_account import Credentials
import re
import textwrap
import time
import hashlib
import json as _json  # per evitare conflitto col json importato
import requests  # <-- per scaricare immagini lato server

# -----------------------------
# Helper per immagini (download lato server)
# -----------------------------
def _resolve_image_url(u: str) -> str:
    """Prova a trasformare link non diretti (Drive/Dropbox) e ottimizza Unsplash."""
    if not u:
        return u
    u = u.strip()

    # Forza HTTPS quando possibile
    if u.startswith("http://"):
        u = "https://" + u[7:]

    # Google Drive: /file/d/<ID>/view  -> uc?export=view&id=<ID>
    if "drive.google.com" in u:
        m = re.search(r"/d/([a-zA-Z0-9_-]{10,})", u)
        if m:
            return f"https://drive.google.com/uc?export=view&id={m.group(1)}"

    # Dropbox: aggiungi ?raw=1
    if "dropbox.com" in u:
        if "?dl=0" in u or "?dl=1" in u:
            u = u.replace("?dl=0", "?raw=1").replace("?dl=1", "?raw=1")
        elif "?raw=1" not in u:
            u += "?raw=1"
        return u

    # Unsplash: assicurati host images.unsplash.com + parametri utili
    if "images.unsplash.com" in u:
        sep = "&" if "?" in u else "?"
        if "auto=" not in u:
            u += f"{sep}auto=format"
            sep = "&"
        if "fm=" not in u:
            u += f"{sep}fm=jpg"

    return u

def _fetch_image_bytes(u: str) -> bytes | None:
    """Scarica l'immagine e restituisce bytes (non BytesIO), per compatibilità con st.image."""
    try:
        url = _resolve_image_url(u)
        if not url or not url.startswith("http"):
            return None
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        data = r.content
        # piccolo sanity check (qualche CDN risponde HTML)
        if not data or len(data) < 32:
            return None
        return data  # <--- bytes, non BytesIO
    except Exception:
        return None


# -----------------------------
# Secrets / Service Account utilities
# -----------------------------
def _normalize_private_key(pk: str) -> str:
    if pk is None:
        return ""
    # 1) \n letterali -> newline reali
    if "\\n" in pk:
        pk = pk.replace("\\n", "\n")
    # 2) rimuovi CR e spazi laterali
    pk = pk.strip().replace("\r", "")
    # 3) togli spazi di indentazione accidentali
    pk = "\n".join([ln.strip() for ln in pk.split("\n")])
    return pk

def _secrets_healthcheck():
    try:
        info = st.secrets["gcp_service_account"]
    except Exception as e:
        st.error(f"❌ Nessuna sezione [gcp_service_account] nei Secrets: {e}")
        return

    required = ["type","project_id","private_key_id","private_key","client_email","client_id","token_uri"]
    missing = [k for k in required if k not in info]
    if missing:
        st.error(f"❌ Mancano questi campi nei Secrets: {', '.join(missing)}")
        return

    pk_raw = str(info.get("private_key", ""))
    pk = _normalize_private_key(pk_raw)

    st.markdown("### Verifica `private_key`")
    st.write(f"- Lunghezza normalizzata: **{len(pk)}** caratteri")
    st.write(f"- Inizia con BEGIN: **{'YES' if pk.startswith('-----BEGIN PRIVATE KEY-----') else 'NO'}**")
    st.write(f"- Finisce con END: **{'YES' if pk.endswith('-----END PRIVATE KEY-----') else 'NO'}**")

    lines = pk.split("\n")
    if lines and lines[0] != "-----BEGIN PRIVATE KEY-----":
        st.warning("⚠️ La prima riga non è esattamente '-----BEGIN PRIVATE KEY-----'")
    if lines and lines[-1] != "-----END PRIVATE KEY-----":
        st.warning("⚠️ L’ultima riga non è esattamente '-----END PRIVATE KEY-----'")

    if len(lines) >= 3:
        body = "".join(lines[1:-1])
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", body):
            st.warning("⚠️ Il corpo della chiave contiene caratteri non base64 (forse spazi o caratteri tipografici).")
        if len(body) % 4 != 0:
            st.warning("⚠️ Lunghezza del body non multipla di 4 → tipico errore di padding.")
        else:
            st.success("✅ Lunghezza del body sembra corretta (multipla di 4).")

    # Test credenziali reali
    try:
        info_fixed = dict(info)
        info_fixed["private_key"] = pk
        creds = Credentials.from_service_account_info(
            info_fixed,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        st.success("✅ Credenziali valide: autenticazione riuscita.")
    except Exception as e:
        st.error(f"❌ Autenticazione fallita: {e}")
        st.info("Suggerimento: usa TOML con triple virgolette e a-capo REALI, oppure lascia \\n e usa la normalizzazione nel codice.")

# (redef) più robusta; verrà usata a runtime
def _normalize_private_key(pk: str) -> str:
    # 1) se ci sono backslash-n letterali, convertili in veri a-capo
    if "\\n" in pk:
        pk = pk.replace("\\n", "\n")
    # 2) rimuovi spazi laterali e CR (\r) parassiti
    pk = pk.strip().replace("\r", "")
    # 3) assicurati che righe BEGIN/END siano isolate e senza spazi
    lines = [ln.strip() for ln in pk.split("\n") if ln.strip() != ""]
    # Se non sono già su righe dedicate, ricostruisci il PEM
    if not lines or "BEGIN PRIVATE KEY-----" not in lines[0]:
        try:
            start = next(i for i,l in enumerate(lines) if "BEGIN PRIVATE KEY" in l)
            end   = next(i for i,l in enumerate(lines) if "END PRIVATE KEY"   in l)
            body  = [l for l in lines[start+1:end] if "PRIVATE KEY" not in l]
            pk = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END PRIVATE KEY-----"
        except StopIteration:
            pass
    else:
        pk = "\n".join(lines)
    return pk

def _get_sheet_client():
    info = dict(st.secrets["gcp_service_account"])  # copia mutabile
    pk = info.get("private_key", "")
    info["private_key"] = _normalize_private_key(pk)
    if not (info["private_key"].startswith("-----BEGIN PRIVATE KEY-----") and
            info["private_key"].endswith("-----END PRIVATE KEY-----")):
        st.error("private_key nei Secrets non è nel formato PEM atteso.")
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)

# -----------------------------
# Config / Costanti
# -----------------------------
APP_TITLE = "MealPlanner"
DAYS_LABELS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MEALS = ["Pranzo", "Cena"]
UNITS = ["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]
DATA_FILE = "mealplanner_data.json"
SPREADSHEET_NAME = "MealPlannerDB"  # <-- il nome del tuo Google Sheet

# -----------------------------
# Utilità numeriche
# -----------------------------
def _safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

# -----------------------------
# Stato iniziale
# -----------------------------
def _init_state():
    if "recipes" not in st.session_state:
        st.session_state.recipes = _demo_recipes()
    if "planner" not in st.session_state:
        st.session_state.planner = _empty_week()
    if "week_start" not in st.session_state:
        today = date.today()
        st.session_state.week_start = today - timedelta(days=today.weekday())
    if "recipe_form_mode" not in st.session_state:
        st.session_state.recipe_form_mode = "add"  # add | edit
    if "editing_recipe_id" not in st.session_state:
        st.session_state.editing_recipe_id = None
    # normalizza planner (utile se hai vecchie chiavi LUNCH/DINNER)
    st.session_state.planner = _normalize_planner_meal_keys(st.session_state.planner, MEALS)

def _empty_week(start: date | None = None):
    if start is None:
        today = date.today()
        start = today - timedelta(days=today.weekday())
    week = {"start": str(start), "days": []}
    for i in range(7):
        day_date = start + timedelta(days=i)
        day_slots = {m: {"recipe_id": None, "servings": 2} for m in MEALS}
        week["days"].append({
            "date": str(day_date),
            **day_slots,
        })
    return week

def _demo_recipes():
    return [
        {
            "id": 1,
            "name": "Spaghetti alla Carbonara",
            "category": "Italiana",
            "time": 25,
            "servings": 2,
            "description": "Pasta con uova, pancetta e parmigiano.",
            "image": "https://images.unsplash.com/photo-1523986371872-9d3ba2e2f642?w=1200",
            "ingredients": [
                {"name": "Spaghetti", "qty": 200, "unit": "g"},
                {"name": "Uova", "qty": 2, "unit": "pcs"},
                {"name": "Pancetta", "qty": 100, "unit": "g"},
                {"name": "Parmigiano", "qty": 50, "unit": "g"},
                {"name": "Pepe nero", "qty": 1, "unit": "tsp"},
            ],
            "instructions": "Cuoci la pasta, rosola la pancetta, unisci fuori dal fuoco uova e formaggio.",
        },
        {
            "id": 2,
            "name": "Stir Fry di Verdure",
            "category": "Vegetariana",
            "time": 20,
            "servings": 2,
            "description": "Verdure saltate con salsa di soia e zenzero.",
            "image": "https://images.unsplash.com/photo-1505575972945-280be642cfac?w=1200",
            "ingredients": [
                {"name": "Broccoli", "qty": 200, "unit": "g"},
                {"name": "Carote", "qty": 2, "unit": "pcs"},
                {"name": "Peperoni", "qty": 2, "unit": "pcs"},
                {"name": "Salsa di soia", "qty": 3, "unit": "tbsp"},
                {"name": "Zenzero", "qty": 10, "unit": "g"},
            ],
            "instructions": "Salta le verdure e aggiungi salsa di soia e zenzero.",
        },
    ]

def _get_new_recipe_id() -> int:
    if not st.session_state.recipes:
        return 1
    return max(r["id"] for r in st.session_state.recipes) + 1

def _get_recipe_options():
    # mappa: label -> id
    return {f'{r["name"]} · {r.get("time","-")} min': r["id"] for r in st.session_state.recipes}

def _find_recipe(rid):
    if rid is None:
        return None
    for r in st.session_state.recipes:
        if r["id"] == rid:
            return r
    return None

# -----------------------------
# Normalizzazione planner
# -----------------------------
def _normalize_planner_meal_keys(planner, expected_meals):
    if not planner or "days" not in planner:
        return planner
    synonyms = {"lunch": "Pranzo", "dinner": "Cena", "pranzo": "Pranzo", "cena": "Cena"}
    new_days = []
    for day in planner.get("days", []):
        new_day = {"date": day.get("date")}
        lower_map = {k.lower(): k for k in day.keys() if k not in ("date",)}
        for m in expected_meals:
            if m in day:
                new_day[m] = day[m]
                continue
            inv = None
            for k_lower, orig in lower_map.items():
                if synonyms.get(k_lower) == m:
                    inv = orig
                    break
            if inv:
                new_day[m] = day.get(inv, {"recipe_id": None, "servings": 2})
            else:
                new_day[m] = {"recipe_id": None, "servings": 2}
        new_days.append(new_day)
    planner["days"] = new_days
    return planner

# -----------------------------
# Persistenza dati (Google Sheets)
# -----------------------------
def _planner_fingerprint(planner: dict) -> str:
    # fingerprint deterministico dei soli campi rilevanti per la spesa
    canon = []
    for d in planner.get("days", []):
        row = {"date": d["date"]}
        for meal, slot in d.items():
            if meal == "date":
                continue
            row[meal] = {"recipe_id": slot.get("recipe_id"), "servings": slot.get("servings", 2)}
        canon.append(row)
    payload = _json.dumps(canon, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def _save_planner_if_changed(debounce_sec: float = 2.0):
    # salva su Sheets se il planner è cambiato rispetto all'ultimo salvataggio
    if "planner" not in st.session_state:
        return
    fp = _planner_fingerprint(st.session_state.planner)
    last_fp = st.session_state.get("_last_saved_planner_fp")
    last_ts = st.session_state.get("_last_saved_ts", 0.0)
    now = time.time()

    if fp != last_fp and (now - last_ts) >= debounce_sec:
        try:
            save_to_sheets()  # usa la tua funzione già definita
            st.session_state["_last_saved_planner_fp"] = fp
            st.session_state["_last_saved_ts"] = now
            st.toast("Planner salvato ✓")
        except Exception as e:
            st.warning(f"Impossibile salvare adesso: {e}")

def load_from_sheets():
    gc = _get_sheet_client()
    sh = gc.open(SPREADSHEET_NAME)

    # carica ricette
    ws_recipes = sh.worksheet("recipes")
    rows = ws_recipes.get_all_records()
    st.session_state.recipes = []
    for r in rows:
        r["ingredients"] = json.loads(r.get("ingredients_json", "[]"))
        st.session_state.recipes.append(r)

    # carica planner
    ws_slots = sh.worksheet("planner_slots")
    slots = ws_slots.get_all_records()
    planner = {"start": None, "days": []}
    for s in slots:
        date_str = s["date"]
        meal = s["meal"]
        rid = int(s["recipe_id"]) if s["recipe_id"] else None
        servings = int(s["servings"])
        planner.setdefault("days", []).append({
            "date": date_str,
            meal: {"recipe_id": rid, "servings": servings}
        })
    st.session_state.planner = planner
    st.success("✅ Dati caricati da Google Sheets")

def save_to_sheets():
    gc = _get_sheet_client()
    sh = gc.open(SPREADSHEET_NAME)

    # salva ricette
    ws_recipes = sh.worksheet("recipes")
    rows = []
    for r in st.session_state.recipes:
        rows.append({
            "id": r["id"],
            "name": r["name"],
            "category": r.get("category", ""),
            "time": r.get("time", 0),
            "servings": r.get("servings", 2),
            "image": r.get("image", ""),
            "description": r.get("description", ""),
            "instructions": r.get("instructions", ""),
            "ingredients_json": json.dumps(r.get("ingredients", []), ensure_ascii=False)
        })
    ws_recipes.clear()
    if rows:
        ws_recipes.update([list(rows[0].keys())] + [list(x.values()) for x in rows])

    # salva planner
    ws_slots = sh.worksheet("planner_slots")
    slots = []
    for d in st.session_state.planner["days"]:
        date_str = d["date"]
        for meal, slot in d.items():
            if meal == "date":
                continue
            slots.append({
                "week_start": st.session_state.week_start.isoformat(),
                "date": date_str,
                "meal": meal,
                "recipe_id": slot.get("recipe_id"),
                "servings": slot.get("servings", 2)
            })
    ws_slots.clear()
    if slots:
        ws_slots.update([list(slots[0].keys())] + [list(x.values()) for x in slots])

# ---- Export / Import ricette (indipendenti dal backend) ----
def export_recipes_json() -> bytes:
    payload = {"recipes": st.session_state.get("recipes", [])}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

def import_recipes_json(file_bytes: bytes):
    try:
        data = json.loads(file_bytes.decode("utf-8"))
        incoming = data.get("recipes", [])
        # assegna ID nuovi se mancano o se collidono
        existing_ids = {r.get("id") for r in st.session_state.get("recipes", []) if r.get("id") is not None}
        def _get_new_recipe_id_local():
            if not st.session_state.get("recipes"):
                return 1
            return max([r.get("id", 0) for r in st.session_state.recipes] or [0]) + 1

        for r in incoming:
            if "id" not in r or r["id"] in existing_ids:
                r["id"] = _get_new_recipe_id_local()
        st.session_state.recipes = (st.session_state.get("recipes", []) or []) + incoming
        st.success(f"Importate {len(incoming)} ricette.")
    except Exception as e:
        st.error(f"Import fallita: {e}")

# ---------- Shopping list helpers (per-settimana) ----------
def _week_key():
    # chiave univoca della settimana corrente
    return st.session_state.week_start.isoformat()

def _aggregate_shopping_list_from_planner() -> pd.DataFrame:
    """Calcola la lista della spesa a partire dal planner della settimana corrente."""
    to_base = {
        "g": ("g", 1), "kg": ("g", 1000),
        "ml": ("ml", 1), "l": ("ml", 1000),
        "pcs": ("pcs", 1), "tbsp": ("tbsp", 1), "tsp": ("tsp", 1)
    }
    agg_base = {}
    for d in st.session_state.planner["days"]:
        for meal in MEALS:
            slot = d[meal]
            rid = slot.get("recipe_id")
            servings_needed = slot.get("servings", 0)
            recipe = _find_recipe(rid)
            if not recipe:
                continue
            base_serv = max(1, recipe.get("servings", 1))
            scale = (servings_needed or 0) / base_serv
            for ing in recipe.get("ingredients", []):
                name = str(ing.get("name", "")).strip().title()
                if not name:
                    continue
                unit = str(ing.get("unit", "")).lower()
                qty = float(ing.get("qty", 0)) * scale
                base_unit, factor = to_base.get(unit, (unit, 1))
                qty_base = qty * factor
                key = (name, base_unit)
                agg_base[key] = agg_base.get(key, 0) + qty_base

    rows = []
    for (name, base_unit), qty_base in agg_base.items():
        if base_unit == "g" and qty_base >= 1000:
            qty, unit = round(qty_base / 1000, 2), "kg"
        elif base_unit == "ml" and qty_base >= 1000:
            qty, unit = round(qty_base / 1000, 2), "l"
        else:
            qty, unit = round(qty_base, 2), base_unit
        rows.append({"Ingrediente": name, "Quantità": qty, "Unità": unit})
    rows.sort(key=lambda x: (x["Ingrediente"], x["Unità"]))
    # ✅ garantisci le colonne anche se non ci sono righe
    if not rows:
        return pd.DataFrame(columns=["Ingrediente", "Quantità", "Unità"])
    return pd.DataFrame(rows)

def _ensure_week_checklist():
    """Inizializza o riallinea la checklist della settimana corrente mantenendo i flag 'Comprato' quando possibile."""
    wk = _week_key()
    df_new = _aggregate_shopping_list_from_planner()

    if "shopping_checklists" not in st.session_state:
        st.session_state.shopping_checklists = {}

    # ✅ se la lista è vuota: inizializza/azzera in modo sicuro e termina
    if df_new.empty:
        st.session_state.shopping_checklists[wk] = []
        return

    current = st.session_state.shopping_checklists.get(wk)
    if current is None or len(current) == 0:
        # prima volta: crea con Comprato=False
        df_new["Comprato"] = False
        st.session_state.shopping_checklists[wk] = df_new.to_dict("records")
        return

    # merge “intelligente” per preservare Comprato
    prev = { (r["Ingrediente"], r["Unità"]) : r.get("Comprato", False) for r in current }
    df_new["Comprato"] = df_new.apply(lambda r: prev.get((r["Ingrediente"], r["Unità"]), False), axis=1)
    st.session_state.shopping_checklists[wk] = df_new.to_dict("records")

def _render_shopping_list_ui(embed: bool = True):
    """Mostra la UI della lista (checkbox + export) per la settimana corrente."""
    _ensure_week_checklist()
    wk = _week_key()
    recs = st.session_state.shopping_checklists[wk]

    if embed:
        st.subheader("🧾 Lista della spesa — settimana corrente")
        st.caption(f"{st.session_state.week_start.strftime('%d/%m/%Y')} → {(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}")

    # elenco con checkbox
    for idx, row in enumerate(recs):
        cols = st.columns([0.08, 0.62, 0.15, 0.15])
        with cols[0]:
            bought = st.checkbox("", value=row.get("Comprato", False), key=f"buy_{wk}_{idx}")
        with cols[1]:
            st.write(row["Ingrediente"])
        with cols[2]:
            st.write(row["Quantità"])
        with cols[3]:
            st.write(row["Unità"])
        recs[idx]["Comprato"] = bought

    # esportazioni sempre allineate
    df_export = pd.DataFrame(recs)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df_export.to_excel(writer, index=False, sheet_name="ShoppingList")
    st.download_button("⬇️ Scarica (Excel)", buffer.getvalue(), "shopping_list.xlsx", use_container_width=True)
    st.download_button("⬇️ Scarica (CSV)", df_export.to_csv(index=False).encode("utf-8"), "shopping_list.csv", use_container_width=True)

# -----------------------------
# UI Base (stili)
# -----------------------------
st.set_page_config(page_title=APP_TITLE, page_icon="🍳", layout="wide")

st.markdown("""
<style>
/* ---------- layout & contenitori ---------- */
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
section[data-testid="stSidebar"] { width: 320px; border-right: 1px solid rgba(255,255,255,0.06); }
[data-testid="stHeader"] { background: transparent; }

/* ---------- tipografia ---------- */
h1, h2, h3 { letter-spacing: .2px; }
h3 { margin-bottom: .25rem; }

/* ---------- bottoni ---------- */
div.stButton > button {
  border-radius: 12px;
  padding: .5rem 1rem;
  font-weight: 600;
  border: 1px solid rgba(255,255,255,0.12);
  transition: transform .05s ease, filter .2s ease, border-color .2s ease;
}
div.stButton > button:hover { filter: brightness(1.05); }
div.stButton > button:active { transform: translateY(1px) scale(.997); }

/* bottoni "primari" (Streamlit li colora con primaryColor) */
div.stButton > button[kind="primary"] {
  border-color: rgba(255,255,255,0.18);
  box-shadow: 0 6px 16px rgba(34,197,94,.25);
}

/* link button & download button */
a[kind="link"] {
  border-radius: 12px !important;
  padding: .5rem 1rem !important;
  font-weight: 600 !important;
  border: 1px solid rgba(255,255,255,0.12);
}
div.stDownloadButton > button {
  border-radius: 12px;
  padding: .5rem 1rem;
  font-weight: 600;
  border: 1px solid rgba(255,255,255,0.12);
}

/* ---------- selectbox / textinput / numberinput ---------- */
div[data-baseweb="select"] > div {
  border-radius: 10px !important;
  min-height: 40px;
}
div[data-baseweb="select"]:hover { box-shadow: 0 0 0 1px rgba(255,255,255,0.18) inset; }
input[type="text"], input[type="number"], textarea, .st-af {
  border-radius: 10px !important;
}

/* ---------- radio & checkbox ---------- */
div[role="radiogroup"] > label {
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px;
  padding: .4rem .65rem;
  margin-right: .5rem;
}
div[role="radiogroup"] > label[data-checked="true"] {
  border-color: rgba(34,197,94,.6);
  background: rgba(34,197,94,.08);
}

/* ---------- expander ---------- */
details {
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  padding: .5rem .75rem 1rem;
}
div.streamlit-expanderHeader { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

_init_state()

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.title(APP_TITLE)
    pages = ["Pianificatore settimanale", "Ricette"]
    # fallback se avevi salvato una pagina non più esistente
    if "page" in st.session_state and st.session_state.page not in pages:
        st.session_state.page = "Pianificatore settimanale"
    page = st.radio("Sezioni", pages, index=0, key="page")
    st.divider()
    col_a, col_b = st.columns(2)
    if col_a.button("💾 Salva"):
        save_to_sheets()
    if col_b.button("📂 Carica"):
        load_from_sheets()
    st.divider()
    st.caption("Ricettario • Import/Export")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("⬇️ Export JSON", export_recipes_json(), file_name="recipes.json", use_container_width=True)
    with c2:
        up = st.file_uploader("Import JSON", type=["json"], label_visibility="collapsed")
        if up is not None:
            import_recipes_json(up.read())
    st.divider()
    if st.button("🔍 Diagnostica Secrets"):
        _secrets_healthcheck()

# -----------------------------
# PIANIFICATORE
# -----------------------------
if page == "Pianificatore settimanale":
    st.header("Pianificatore settimanale")

    nav_cols = st.columns([0.5, 1, 1, 1, 1, 1, 1, 1, 0.5])
    with nav_cols[0]:
        if st.button("◀︎", use_container_width=True, key="nav_prev"):
            st.session_state.week_start -= timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)
    with nav_cols[-1]:
        if st.button("▶︎", use_container_width=True, key="nav_next"):
            st.session_state.week_start += timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)

    st.caption(
        f"Settimana: {st.session_state.week_start.strftime('%d/%m/%Y')} - "
        f"{(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"
    )

    day_cols = nav_cols[1:-1]
    for i, c in enumerate(day_cols):
        day_date = st.session_state.week_start + timedelta(days=i)
        with c:
            st.markdown(f"### {DAYS_LABELS[i]}\n**{day_date.day}**")

            # 🔁 ciclo pasti
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]

                # opzioni ricette
                r_opts_map = _get_recipe_options()
                r_opts = ["-"] + list(r_opts_map.keys())

                # etichetta corrente (se già selezionato)
                current_label = "-"
                if slot.get("recipe_id"):
                    rec_cur = _find_recipe(slot["recipe_id"])
                    if rec_cur:
                        current_label = f'{rec_cur["name"]} · {rec_cur.get("time","-")} min'
                        if current_label not in r_opts:
                            r_opts.insert(1, current_label)

                # 🔑 chiavi UNICHE: includono indice, pasto e data
                sel_key  = f"planner_sel_{i}_{meal}_{day_date.isoformat()}"
                serv_key = f"planner_serv_{i}_{meal}_{day_date.isoformat()}"

                selected = st.selectbox(
                    label=meal,
                    options=r_opts,
                    index=r_opts.index(current_label) if current_label in r_opts else 0,
                    key=sel_key,
                )

                if selected != "-":
                    slot["recipe_id"] = r_opts_map.get(selected, slot.get("recipe_id"))
                    rec = _find_recipe(slot["recipe_id"])
                    if rec:
                        with st.expander("Dettagli", expanded=False):
                            if rec.get("image"):
                                img_bytes = _fetch_image_bytes(rec["image"])
                                if img_bytes:
                                    st.image(img_bytes, use_container_width=True)
                                else:
                                    st.info("Immagine non caricabile. Verifica che il link sia diretto o usa un host compatibile (Unsplash/Drive/Dropbox).")
                            st.caption(f"⏱ {rec['time']} min · Categoria: {rec.get('category','-')}")
                            st.write(rec.get("description", ""))
                        slot["servings"] = st.number_input(
                            "Porzioni",
                            min_value=1,
                            max_value=12,
                            value=slot.get("servings", 2),
                            key=serv_key,
                        )
                else:
                    slot["recipe_id"] = None

    # salva subito se ci sono modifiche (debounced)
    _save_planner_if_changed()

    # 👇 Lista della spesa della settimana, integrata nel planner
    with st.expander("Lista della spesa (settimana corrente)", expanded=True):
        _render_shopping_list_ui(embed=False)

# -----------------------------
# RICETTE (CRUD)
# -----------------------------
elif page == "Ricette":
    st.header("Ricettario")

    # ----- Anchor per scroll automatico sul form -----
    st.markdown('<div id="recipe_form_top"></div>', unsafe_allow_html=True)
    if st.session_state.get("scroll_to_form"):
        components.html(
            "<script>document.getElementById('recipe_form_top').scrollIntoView({behavior:'instant', block:'start'});</script>",
            height=0,
        )
        st.session_state.scroll_to_form = False

    # ---------- FORM (UNICO) PRIMA DELLA LISTA ----------
    st.subheader("Aggiungi / Modifica ricetta")

    mode = st.session_state.recipe_form_mode
    editing_recipe = _find_recipe(st.session_state.editing_recipe_id) if mode == "edit" else None

    # Prefisso UNICO per tutte le chiavi dinamiche del form (evita collisioni)
    def _form_prefix():
        return f"rf_{mode}_{st.session_state.editing_recipe_id or 'new'}"

    # Se è cambiata ricetta/modo, pulisci le vecchie chiavi dinamiche per ingredienti
    cur_prefix = _form_prefix()
    if st.session_state.get("_active_form_prefix") != cur_prefix:
        for k in list(st.session_state.keys()):
            if isinstance(k, str) and k.startswith("rf_"):
                try:
                    del st.session_state[k]
                except Exception:
                    pass
        st.session_state["_active_form_prefix"] = cur_prefix

    with st.form("recipe_form_main", clear_on_submit=(mode == "add")):
        name = st.text_input("Nome", value=editing_recipe["name"] if editing_recipe else "")
        category = st.text_input("Categoria", value=editing_recipe.get("category","") if editing_recipe else "")
        time_min = st.number_input("Tempo (minuti)", min_value=0, value=_safe_int(editing_recipe.get("time", 0)) if editing_recipe else 0)
        servings = st.number_input("Porzioni base", min_value=1, value=_safe_int(editing_recipe.get("servings", 2)) if editing_recipe else 2)
        image = st.text_input("URL immagine (opzionale)", value=editing_recipe.get("image","") if editing_recipe else "")
        description = st.text_area("Descrizione", value=editing_recipe.get("description","") if editing_recipe else "")

        st.markdown("**Ingredienti**")
        default_ingredients = editing_recipe.get("ingredients", []) if editing_recipe else []
        ingr_count = st.number_input(
            "Numero ingredienti",
            min_value=0, max_value=50,
            value=len(default_ingredients) if default_ingredients else 5,
            key=f"{cur_prefix}_ing_count"
        )

        ingredients: List[Dict[str, Any]] = []
        for idx in range(int(ingr_count)):
            col1, col2, col3 = st.columns([3, 1, 1])
            default = default_ingredients[idx] if idx < len(default_ingredients) else {"name": "", "qty": 0, "unit": UNITS[0]}
            name_i = col1.text_input(
                f"Ingrediente {idx+1} - nome",
                value=default.get("name",""),
                key=f"{cur_prefix}_ing_name_{idx}"
            )
            qty_i = col2.number_input(
                f"Quantità {idx+1}",
                min_value=0.0,
                value=float(default.get("qty", 0)),
                key=f"{cur_prefix}_ing_qty_{idx}"
            )
            unit_i = col3.selectbox(
                f"Unità {idx+1}",
                UNITS,
                index=(UNITS.index(default.get("unit")) if default.get("unit") in UNITS else 0),
                key=f"{cur_prefix}_ing_unit_{idx}"
            )
            if name_i:
                ingredients.append({"name": name_i, "qty": qty_i, "unit": unit_i})

        instructions = st.text_area(
            "Istruzioni",
            value=editing_recipe.get("instructions","") if editing_recipe else "",
            key=f"{cur_prefix}_instructions"
        )

        c_azioni = st.columns(3)
        with c_azioni[0]:
            submit = st.form_submit_button("💾 Salva ricetta")
        with c_azioni[1]:
            new_btn = st.form_submit_button("➕ Nuova (svuota)")
        with c_azioni[2]:
            cancel_btn = st.form_submit_button("❌ Annulla modifica")

        if submit:
            if not name.strip():
                st.error("Il nome è obbligatorio.")
            else:
                payload = {
                    "name": name.strip(),
                    "category": category.strip(),
                    "time": int(time_min),
                    "servings": int(servings),
                    "image": image.strip(),
                    "description": description.strip(),
                    "ingredients": ingredients,
                    "instructions": instructions.strip(),
                }
                if mode == "edit" and editing_recipe:
                    editing_recipe.update(payload)
                    st.success(f"Ricetta '{name}' aggiornata.")
                else:
                    payload["id"] = _get_new_recipe_id()
                    st.session_state.recipes.append(payload)
                    st.success(f"Ricetta '{name}' aggiunta.")
                st.session_state.recipe_form_mode = "add"
                st.session_state.editing_recipe_id = None
                st.session_state.scroll_to_form = True  # resta vicino al form

        if new_btn:
            st.session_state.recipe_form_mode = "add"
            st.session_state.editing_recipe_id = None
            st.session_state.scroll_to_form = True
            st.experimental_rerun()

        if cancel_btn:
            st.session_state.recipe_form_mode = "add"
            st.session_state.editing_recipe_id = None
            st.info("Modifica annullata.")

    st.divider()

    # ---------- Filtri (dopo il form) ----------
    with st.container():
        f1, f2, f3 = st.columns([2, 1, 1])
        text_query = f1.text_input("Cerca per nome/descrizione", "")
        categories = sorted({r.get("category", "").strip() for r in st.session_state.recipes if r.get("category")})
        cat = f2.selectbox("Categoria", ["Tutte"] + categories)
        max_time = f3.number_input("Tempo max (min)", min_value=0, value=0)

    # ---------- Lista ricette filtrata ----------
    def _passes_filters(r):
        if text_query:
            q = text_query.lower()
            if q not in r.get("name", "").lower() and q not in r.get("description", "").lower():
                return False
        if cat != "Tutte" and r.get("category") != cat:
            return False
        if max_time and _safe_int(r.get("time", 0)) > max_time:
            return False
        return True

    filtered = [r for r in st.session_state.recipes if _passes_filters(r)]
    st.caption(f"{len(filtered)} ricette trovate")

    for r in filtered:
        with st.container(border=True):
            c1, c2 = st.columns([1, 2])
            with c1:
                if r.get("image"):
                    img_bytes = _fetch_image_bytes(r["image"])  # deve restituire BYTES (fix precedente)
                    if img_bytes:
                        st.image(img_bytes, use_container_width=True)
                    else:
                        st.write("Nessuna anteprima (link non diretto o bloccato dal CDN)")
            with c2:
                st.subheader(r["name"])
                st.caption(f"Categoria: {r.get('category','-')} · ⏱ {r.get('time','-')} min · Porzioni base: {r.get('servings','-')}")
                if r.get("description"):
                    st.write(r["description"])
                with st.expander("Ingredienti"):
                    ingr = pd.DataFrame(r.get("ingredients", []))
                    if not ingr.empty:
                        st.dataframe(ingr, hide_index=True, use_container_width=True)
                if r.get("instructions"):
                    with st.expander("Istruzioni"):
                        st.write(r["instructions"])

                b1, b2 = st.columns(2)
                if b1.button("✏️ Modifica", key=f"edit_{r['id']}"):
                    st.session_state.recipe_form_mode = "edit"
                    st.session_state.editing_recipe_id = r["id"]
                    st.session_state.scroll_to_form = True
                    st.experimental_rerun()
                if b2.button("🗑️ Elimina", key=f"del_{r['id']}"):
                    st.session_state.recipes = [x for x in st.session_state.recipes if x["id"] != r["id"]]
                    st.toast(f"Ricetta '{r['name']}' eliminata")


    st.divider()
    st.subheader("Aggiungi / Modifica ricetta")

    mode = st.session_state.recipe_form_mode
    editing_recipe = _find_recipe(st.session_state.editing_recipe_id) if mode == "edit" else None

    with st.form("recipe_form", clear_on_submit=(mode == "add")):
        name = st.text_input("Nome", value=editing_recipe["name"] if editing_recipe else "")
        category = st.text_input("Categoria", value=editing_recipe.get("category","") if editing_recipe else "")
        time_min = st.number_input("Tempo (minuti)", min_value=0, value=_safe_int(editing_recipe.get("time", 0)) if editing_recipe else 0)
        servings = st.number_input("Porzioni base", min_value=1, value=_safe_int(editing_recipe.get("servings", 2)) if editing_recipe else 2)
        image = st.text_input("URL immagine (opzionale)", value=editing_recipe.get("image","") if editing_recipe else "")
        description = st.text_area("Descrizione", value=editing_recipe.get("description","") if editing_recipe else "")

        st.markdown("**Ingredienti**")
        ingr_container = st.container()
        default_ingredients = editing_recipe.get("ingredients", []) if editing_recipe else []
        # gestiamo dinamicamente gli ingredienti con un numero controllato
        ingr_count = st.number_input("Numero ingredienti", min_value=0, max_value=50, value=len(default_ingredients) if default_ingredients else 5)
        ingredients: List[Dict[str, Any]] = []
        for idx in range(int(ingr_count)):
            col1, col2, col3 = st.columns([3, 1, 1])
            default = default_ingredients[idx] if idx < len(default_ingredients) else {"name": "", "qty": 0, "unit": UNITS[0]}
            name_i = col1.text_input(f"Ingrediente {idx+1} - nome", value=default.get("name",""), key=f"ing_name_{idx}")
            qty_i = col2.number_input(f"Quantità {idx+1}", min_value=0.0, value=float(default.get("qty", 0)), key=f"ing_qty_{idx}")
            unit_i = col3.selectbox(f"Unità {idx+1}", UNITS, index=(UNITS.index(default.get("unit")) if default.get("unit") in UNITS else 0), key=f"ing_unit_{idx}")
            if name_i:
                ingredients.append({"name": name_i, "qty": qty_i, "unit": unit_i})

        instructions = st.text_area("Istruzioni", value=editing_recipe.get("instructions","") if editing_recipe else "")

        c_azioni = st.columns(3)
        with c_azioni[0]:
            submit = st.form_submit_button("💾 Salva ricetta")
        with c_azioni[1]:
            new_btn = st.form_submit_button("➕ Nuova (svuota)")
        with c_azioni[2]:
            cancel_btn = st.form_submit_button("❌ Annulla modifica")

        if submit:
            if not name.strip():
                st.error("Il nome è obbligatorio.")
            else:
                payload = {
                    "name": name.strip(),
                    "category": category.strip(),
                    "time": int(time_min),
                    "servings": int(servings),
                    "image": image.strip(),
                    "description": description.strip(),
                    "ingredients": ingredients,
                    "instructions": instructions.strip(),
                }
                if mode == "edit" and editing_recipe:
                    # aggiorna in place
                    editing_recipe.update(payload)
                    st.success(f"Ricetta '{name}' aggiornata.")
                else:
                    payload["id"] = _get_new_recipe_id()
                    st.session_state.recipes.append(payload)
                    st.success(f"Ricetta '{name}' aggiunta.")
                st.session_state.recipe_form_mode = "add"
                st.session_state.editing_recipe_id = None

        if new_btn:
            st.session_state.recipe_form_mode = "add"
            st.session_state.editing_recipe_id = None
            st.experimental_rerun()

        if cancel_btn:
            st.session_state.recipe_form_mode = "add"
            st.session_state.editing_recipe_id = None
            st.info("Modifica annullata.")

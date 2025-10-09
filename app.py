# app.py — MealPlanner (profilo + autosave + immagini lato server) — versione completa e ottimizzata

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from io import BytesIO
from datetime import date, timedelta
from typing import List, Dict, Any
import json
import json as _json
import time, hashlib, re, requests
import gspread
from google.oauth2.service_account import Credentials

# =========================
# CONFIG / COSTANTI
# =========================
APP_TITLE = "MealPlanner"
DAYS_LABELS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MEALS = ["Pranzo", "Cena"]
UNITS = ["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]
SPREADSHEET_NAME = "MealPlannerDB"  # nome del Google Sheet

# =========================
# UTILITY: rerun compatibile
# =========================
def _rerun():
    # Streamlit >= 1.27 usa st.rerun(); versioni precedenti st.experimental_rerun()
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

# =========================
# IMMAGINI (download lato server)
# =========================
def _resolve_image_url(u: str) -> str:
    if not u:
        return u
    u = u.strip()
    if u.startswith("http://"):
        u = "https://" + u[7:]

    # Google Drive: /file/d/<ID>/view  -> uc?export=view&id=<ID>
    if "drive.google.com" in u:
        m = re.search(r"/d/([a-zA-Z0-9_-]{10,})", u)
        if m:
            return f"https://drive.google.com/uc?export=view&id={m.group(1)}"

    # Dropbox: ?dl=0/1 -> ?raw=1
    if "dropbox.com" in u:
        if "?dl=0" in u or "?dl=1" in u:
            u = u.replace("?dl=0", "?raw=1").replace("?dl=1", "?raw=1")
        elif "?raw=1" not in u:
            u += "?raw=1"

    # Unsplash CDN: forza parametri comodi
    if "images.unsplash.com" in u:
        sep = "&" if "?" in u else "?"
        if "auto=" not in u:
            u += f"{sep}auto=format"; sep = "&"
        if "fm=" not in u:
            u += f"{sep}fm=jpg"

    return u

def _fetch_image_bytes(u: str) -> bytes | None:
    try:
        url = _resolve_image_url(u)
        if not url or not url.startswith("http"):
            return None
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }, timeout=8)
        r.raise_for_status()
        data = r.content
        return data if data and len(data) >= 32 else None
    except Exception:
        return None

# =========================
# GOOGLE AUTH / SECRETS
# =========================
def _normalize_private_key(pk: str) -> str:
    if pk is None:
        return ""
    if "\\n" in pk:
        pk = pk.replace("\\n", "\n")
    pk = pk.strip().replace("\r", "")
    lines = [ln.strip() for ln in pk.split("\n") if ln.strip()]
    if not lines or "BEGIN PRIVATE KEY-----" not in lines[0]:
        try:
            s = next(i for i,l in enumerate(lines) if "BEGIN PRIVATE KEY" in l)
            e = next(i for i,l in enumerate(lines) if "END PRIVATE KEY" in l)
            body = [l for l in lines[s+1:e] if "PRIVATE KEY" not in l]
            pk = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END PRIVATE KEY-----"
        except StopIteration:
            pass
    else:
        pk = "\n".join(lines)
    return pk

def _get_sheet_client_and_error():
    """Ritorna (client, error_string). client=None se fallisce."""
    try:
        info = dict(st.secrets["gcp_service_account"])
    except Exception as e:
        return None, "Sezione [gcp_service_account] assente nei secrets."

    try:
        # Validazioni base utili
        missing = [k for k in ["type","project_id","private_key_id","private_key","client_email","client_id","token_uri"] if k not in info]
        if missing:
            return None, f"Mancano campi nei secrets: {', '.join(missing)}"

        pk = info.get("private_key","")
        if not isinstance(pk, str) or "PRIVATE KEY" not in pk:
            return None, "private_key non sembra una chiave PEM valida."

        info["private_key"] = _normalize_private_key(pk)
        creds = Credentials.from_service_account_info(
            info,
            scopes=[ "https://www.googleapis.com/auth/spreadsheets",
                     "https://www.googleapis.com/auth/drive.readonly" ]  # ok anche se poi apri per ID
        )
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def _get_sheet_client():
    client, _ = _get_sheet_client_and_error()
    return client


def _secrets_healthcheck():
    ok = _get_sheet_client() is not None
    if ok:
        st.success("✅ Credenziali Google valide.")
    else:
        st.error("❌ Credenziali Google non disponibili o non valide. Controlla i Secrets.")

# =========================
# PROFILI (worksheet per profilo)
# =========================
def _sheet_name_for(base: str, profile: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (profile or "Default").strip())
    return f"{base}__{safe}" if safe.lower() != "default" else base

def _get_or_create_ws(sh, title: str, headers: list[str]):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=400, cols=max(10, len(headers)))
        if headers:
            ws.update([headers])
    return ws

# =========================
# UTIL / STATO
# =========================
def _safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def _demo_recipes():
    return [
        {
            "id": 1, "name": "Spaghetti alla Carbonara", "category": "Italiana",
            "time": 25, "servings": 2,
            "description": "Pasta con uova, pancetta e parmigiano.",
            "image": "https://images.unsplash.com/photo-1523986371872-9d3ba2e2f642?w=1200",
            "ingredients": [
                {"name":"Spaghetti","qty":200,"unit":"g"},
                {"name":"Uova","qty":2,"unit":"pcs"},
                {"name":"Pancetta","qty":100,"unit":"g"},
                {"name":"Parmigiano","qty":50,"unit":"g"},
                {"name":"Pepe nero","qty":1,"unit":"tsp"},
            ],
            "instructions": "Cuoci la pasta, rosola la pancetta, unisci fuori dal fuoco uova e formaggio.",
        },
        {
            "id": 2, "name": "Stir Fry di Verdure", "category": "Vegetariana",
            "time": 20, "servings": 2,
            "description": "Verdure saltate con salsa di soia e zenzero.",
            "image": "https://images.unsplash.com/photo-1505575972945-280be642cfac?w=1200",
            "ingredients": [
                {"name":"Broccoli","qty":200,"unit":"g"},
                {"name":"Carote","qty":2,"unit":"pcs"},
                {"name":"Peperoni","qty":2,"unit":"pcs"},
                {"name":"Salsa di soia","qty":3,"unit":"tbsp"},
                {"name":"Zenzero","qty":10,"unit":"g"},
            ],
            "instructions": "Salta le verdure e aggiungi salsa di soia e zenzero.",
        },
    ]

def _empty_week(start: date | None = None):
    if start is None:
        today = date.today()
        start = today - timedelta(days=today.weekday())
    week = {"start": str(start), "days": []}
    for i in range(7):
        d = start + timedelta(days=i)
        week["days"].append({"date": str(d), **{m: {"recipe_id": None, "servings": 2} for m in MEALS}})
    return week

def _find_recipe(rid):
    if rid is None:
        return None
    for r in st.session_state.recipes:
        if r["id"] == rid:
            return r
    return None

def _get_new_recipe_id() -> int:
    return (max((r["id"] for r in st.session_state.recipes), default=0) + 1)

def _get_recipe_options():
    return {f'{r["name"]} · {r.get("time","-")} min': r["id"] for r in st.session_state.recipes}

def _normalize_planner_meal_keys(planner, expected_meals):
    if not planner or "days" not in planner:
        return planner
    synonyms = {"lunch":"Pranzo","dinner":"Cena","pranzo":"Pranzo","cena":"Cena"}
    new_days=[]
    for day in planner.get("days", []):
        nd={"date": day.get("date")}
        low={k.lower():k for k in day if k!="date"}
        for m in expected_meals:
            if m in day:
                nd[m]=day[m]; continue
            inv=None
            for kl,orig in low.items():
                if synonyms.get(kl)==m: inv=orig; break
            nd[m]=day.get(inv, {"recipe_id":None,"servings":2}) if inv else {"recipe_id":None,"servings":2}
        new_days.append(nd)
    planner["days"]=new_days
    return planner

def _init_state():
    if "profiles" not in st.session_state: st.session_state.profiles=["Default"]
    if "current_profile" not in st.session_state: st.session_state.current_profile="Default"
    if "recipes" not in st.session_state: st.session_state.recipes=_demo_recipes()
    if "planner" not in st.session_state: st.session_state.planner=_empty_week()
    if "week_start" not in st.session_state:
        today=date.today(); st.session_state.week_start=today - timedelta(days=today.weekday())
    if "recipe_form_mode" not in st.session_state: st.session_state.recipe_form_mode="add"
    if "editing_recipe_id" not in st.session_state: st.session_state.editing_recipe_id=None
    if "page" not in st.session_state: st.session_state.page="Pianificatore settimanale"
    st.session_state.planner=_normalize_planner_meal_keys(st.session_state.planner, MEALS)

# =========================
# PERSISTENZA planner debounced
# =========================
def _planner_fingerprint(planner: dict) -> str:
    canon=[]
    for d in planner.get("days", []):
        row={"date": d["date"]}
        for meal,slot in d.items():
            if meal=="date": continue
            row[meal]={"recipe_id":slot.get("recipe_id"),"servings":slot.get("servings",2)}
        canon.append(row)
    return hashlib.sha256(_json.dumps(canon, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

def _save_planner_if_changed(debounce_sec: float = 2.0):
    if "planner" not in st.session_state: return
    fp=_planner_fingerprint(st.session_state.planner)
    last_fp=st.session_state.get("_last_saved_planner_fp"); last_ts=st.session_state.get("_last_saved_ts",0.0)
    now=time.time()
    if fp!=last_fp and (now-last_ts)>=debounce_sec:
        try:
            save_to_sheets()
            st.session_state["_last_saved_planner_fp"]=fp
            st.session_state["_last_saved_ts"]=now
            st.toast("Planner salvato ✓")
        except Exception as e:
            st.warning(f"Impossibile salvare: {e}")

# =========================
# EXPORT/IMPORT ricette (JSON)
# =========================
def export_recipes_json() -> bytes:
    return json.dumps({"recipes": st.session_state.get("recipes", [])}, ensure_ascii=False, indent=2).encode("utf-8")

def import_recipes_json(file_bytes: bytes):
    try:
        data=json.loads(file_bytes.decode("utf-8"))
        incoming=data.get("recipes", [])
        existing_ids={r.get("id") for r in st.session_state.get("recipes", []) if r.get("id") is not None}
        def _new_id_local():
            return max([r.get("id",0) for r in st.session_state.get("recipes", [])] + [0]) + 1
        for r in incoming:
            if "id" not in r or r["id"] in existing_ids: r["id"]=_new_id_local()
        st.session_state.recipes=(st.session_state.get("recipes", []) or []) + incoming
        st.success(f"Importate {len(incoming)} ricette.")
    except Exception as e:
        st.error(f"Import fallita: {e}")

# =========================
# LISTA SPESA (profilo + settimana)
# =========================
def _week_key():
    return f"{st.session_state.get('current_profile','Default')}::{st.session_state.week_start.isoformat()}"

def _aggregate_shopping_list_from_planner() -> pd.DataFrame:
    to_base={"g":("g",1),"kg":("g",1000),"ml":("ml",1),"l":("ml",1000),"pcs":("pcs",1),"tbsp":("tbsp",1),"tsp":("tsp",1)}
    agg={}
    for d in st.session_state.planner["days"]:
        for meal in MEALS:
            slot=d[meal]; rid=slot.get("recipe_id"); serv=slot.get("servings",0)
            rec=_find_recipe(rid)
            if not rec: continue
            base=max(1, rec.get("servings",1)); scale=(serv or 0)/base
            for ing in rec.get("ingredients", []):
                name=str(ing.get("name","")).strip().title()
                if not name: continue
                unit=str(ing.get("unit","")).lower(); qty=float(ing.get("qty",0))*scale
                base_u,f=to_base.get(unit,(unit,1)); qty_b=qty*f
                agg[(name,base_u)]=agg.get((name,base_u),0)+qty_b
    rows=[]
    for (name,bu),qtyb in agg.items():
        if bu=="g" and qtyb>=1000: rows.append({"Ingrediente":name,"Quantità":round(qtyb/1000,2),"Unità":"kg"})
        elif bu=="ml" and qtyb>=1000: rows.append({"Ingrediente":name,"Quantità":round(qtyb/1000,2),"Unità":"l"})
        else: rows.append({"Ingrediente":name,"Quantità":round(qtyb,2),"Unità":bu})
    rows.sort(key=lambda x:(x["Ingrediente"],x["Unità"]))
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["Ingrediente","Quantità","Unità"])

def _ensure_week_checklist():
    wk=_week_key(); df=_aggregate_shopping_list_from_planner()
    if "shopping_checklists" not in st.session_state: st.session_state.shopping_checklists={}
    if df.empty: st.session_state.shopping_checklists[wk]=[]; return
    cur=st.session_state.shopping_checklists.get(wk)
    if not cur:
        df["Comprato"]=False; st.session_state.shopping_checklists[wk]=df.to_dict("records"); return
    prev={(r["Ingrediente"],r["Unità"]): r.get("Comprato",False) for r in cur}
    df["Comprato"]=df.apply(lambda r: prev.get((r["Ingrediente"],r["Unità"]),False), axis=1)
    st.session_state.shopping_checklists[wk]=df.to_dict("records")

def _render_shopping_list_ui(embed: bool=True):
    _ensure_week_checklist()
    wk=_week_key(); recs=st.session_state.shopping_checklists[wk]
    if embed:
        st.subheader("🧾 Lista della spesa — settimana corrente")
        st.caption(f"{st.session_state.week_start.strftime('%d/%m/%Y')} → {(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}")
    for idx,row in enumerate(recs):
        c=st.columns([0.08,0.62,0.15,0.15])
        with c[0]: bought=st.checkbox("", value=row.get("Comprato",False), key=f"buy_{wk}_{idx}")
        with c[1]: st.write(row["Ingrediente"])
        with c[2]: st.write(row["Quantità"])
        with c[3]: st.write(row["Unità"])
        recs[idx]["Comprato"]=bought
    df=pd.DataFrame(recs)
    buf=BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="ShoppingList")
    st.download_button("⬇️ Scarica (Excel)", buf.getvalue(), "shopping_list.xlsx", use_container_width=True)
    st.download_button("⬇️ Scarica (CSV)", df.to_csv(index=False).encode("utf-8"), "shopping_list.csv", use_container_width=True)

# =========================
# GOOGLE SHEETS: LOAD / SAVE (profilo) — SAFE (non crasha se credenziali mancanti)
# =========================
def load_from_sheets():
    gc=_get_sheet_client()
    if gc is None:
        st.warning("Caricamento da Google Sheets non disponibile (credenziali mancanti o non valide).")
        return
    sh=gc.open(SPREADSHEET_NAME)
    prof=st.session_state.get("current_profile","Default")

    ws_recipes=_get_or_create_ws(sh, _sheet_name_for("recipes",prof),
        ["id","name","category","time","servings","image","description","instructions","ingredients_json"])
    rows=ws_recipes.get_all_records()
    st.session_state.recipes=[]
    for r in rows:
        try: ings=json.loads(r.get("ingredients_json","[]"))
        except Exception: ings=[]
        st.session_state.recipes.append({
            "id": _safe_int(r.get("id",0)) or _get_new_recipe_id(),
            "name": r.get("name",""), "category": r.get("category",""),
            "time": _safe_int(r.get("time",0)), "servings": _safe_int(r.get("servings",2)) or 2,
            "image": r.get("image",""), "description": r.get("description",""),
            "instructions": r.get("instructions",""), "ingredients": ings,
        })

    ws_slots=_get_or_create_ws(sh, _sheet_name_for("planner_slots",prof),
        ["week_start","date","meal","recipe_id","servings"])
    slots=ws_slots.get_all_records()
    day_map={}
    for s in slots:
        d=str(s.get("date","")).strip(); m=str(s.get("meal","")).strip()
        if not d or not m: continue
        rid=_safe_int(s.get("recipe_id")) if str(s.get("recipe_id","")).strip() else None
        serv=_safe_int(s.get("servings",2)) or 2
        day_map.setdefault(d, {"date":d})
        day_map[d][m]={"recipe_id":rid,"servings":serv}
    planner={"start":None,"days":[]}
    for d in sorted(day_map.keys()):
        day={"date":d}
        for m in MEALS: day[m]=day_map[d].get(m, {"recipe_id":None,"servings":2})
        planner["days"].append(day)
    st.session_state.planner=_normalize_planner_meal_keys(planner, MEALS)
    st.success(f"✅ Dati caricati per profilo: {prof}")

def save_to_sheets():
    gc=_get_sheet_client()
    if gc is None:
        st.warning("Salvataggio su Google Sheets non disponibile (credenziali mancanti o non valide).")
        return
    sh=gc.open(SPREADSHEET_NAME)
    prof=st.session_state.get("current_profile","Default")

    ws_recipes=_get_or_create_ws(sh, _sheet_name_for("recipes",prof),
        ["id","name","category","time","servings","image","description","instructions","ingredients_json"])
    rows=[{
        "id": r["id"], "name": r.get("name",""), "category": r.get("category",""),
        "time": int(r.get("time",0) or 0), "servings": int(r.get("servings",2) or 2),
        "image": r.get("image",""), "description": r.get("description",""),
        "instructions": r.get("instructions",""),
        "ingredients_json": json.dumps(r.get("ingredients", []), ensure_ascii=False),
    } for r in st.session_state.get("recipes", [])]
    ws_recipes.clear()
    if rows: ws_recipes.update([list(rows[0].keys())] + [list(x.values()) for x in rows])

    ws_slots=_get_or_create_ws(sh, _sheet_name_for("planner_slots",prof),
        ["week_start","date","meal","recipe_id","servings"])
    slots=[]
    for d in st.session_state.get("planner",{}).get("days", []):
        for meal,slot in d.items():
            if meal=="date": continue
            slots.append({
                "week_start": st.session_state.week_start.isoformat(),
                "date": d["date"], "meal": meal,
                "recipe_id": slot.get("recipe_id"), "servings": slot.get("servings",2)
            })
    ws_slots.clear()
    if slots: ws_slots.update([list(slots[0].keys())] + [list(x.values()) for x in slots])

    st.toast(f"Dati salvati per profilo: {prof} ✓")

# =========================
# UI BASE / STILI
# =========================
st.set_page_config(page_title=APP_TITLE, page_icon="🍳", layout="wide")
st.markdown("""
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
section[data-testid="stSidebar"] { width: 320px; border-right: 1px solid rgba(255,255,255,0.06); }
[data-testid="stHeader"] { background: transparent; }
div.stButton > button, div.stDownloadButton > button, a[kind="link"] {
  border-radius: 12px; padding: .5rem 1rem; font-weight: 600; border: 1px solid rgba(255,255,255,0.12);
}
div[data-baseweb="select"] > div { border-radius: 10px !important; min-height: 40px; }
input[type="text"], input[type="number"], textarea, .st-af { border-radius: 10px !important; }
@media (max-width: 640px){ .stButton>button, .stDownloadButton>button { width: 100%; } }
</style>
""", unsafe_allow_html=True)

_init_state()

# =========================
# Diagnostica (opzionale)
# =========================
with st.expander("🩺 Diagnostica (clicca per dettagli)", expanded=False):
    client, err = _get_sheet_client_and_error()
    if client:
        st.write("Google Sheets client: ✅ disponibile")
    else:
        st.write("Google Sheets client: ❌ non disponibile")
        if err:
            st.warning(f"Motivo: {err}")
        st.info("Controlla i secrets in Streamlit Cloud → Settings → Secrets, sezione [gcp_service_account].")

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    st.title(APP_TITLE)

    # PROFILO: pulizia input PRIMA del widget, creazione profilo con rerun sicuro
    st.caption("Profilo")

    if st.session_state.get("_clear_new_profile"):
        st.session_state["new_profile_name"] = ""
        del st.session_state["_clear_new_profile"]

    np_c1, np_c2 = st.columns([2,1])
    with np_c1:
        new_profile_name = st.text_input(
            "Nuovo profilo", key="new_profile_name",
            placeholder="Es. Famiglia", label_visibility="collapsed"
        )
    with np_c2:
        if st.button("Crea"):
            name = (new_profile_name or "").strip()
            if name:
                if name not in st.session_state.profiles:
                    st.session_state.profiles.append(name)
                st.session_state.current_profile = name
                st.session_state["_clear_new_profile"] = True
                _rerun()

    st.selectbox(
        "Seleziona profilo",
        st.session_state.profiles,
        index=st.session_state.profiles.index(st.session_state.current_profile)
              if st.session_state.current_profile in st.session_state.profiles else 0,
        key="current_profile",
        label_visibility="collapsed",
    )

    st.divider()
    pages = ["Pianificatore settimanale", "Ricette"]
    if st.session_state.page not in pages:
        st.session_state.page = "Pianificatore settimanale"
    st.radio("Sezioni", pages, index=pages.index(st.session_state.page), key="page")

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

# Variabile locale sicura anche se ci sono rerun
page = st.session_state.get("page", "Pianificatore settimanale")

# =========================
# PIANIFICATORE
# =========================
if page == "Pianificatore settimanale":
    st.header("Pianificatore settimanale")

    nav = st.columns([0.5,1,1,1,1,1,1,1,0.5])
    with nav[0]:
        if st.button("◀︎", use_container_width=True, key="nav_prev"):
            st.session_state.week_start -= timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)
    with nav[-1]:
        if st.button("▶︎", use_container_width=True, key="nav_next"):
            st.session_state.week_start += timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)

    st.caption(
        f"Settimana: {st.session_state.week_start.strftime('%d/%m/%Y')} - "
        f"{(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"
    )

    day_cols = nav[1:-1]
    for i, col in enumerate(day_cols):
        d = st.session_state.week_start + timedelta(days=i)
        with col:
            st.markdown(f"### {DAYS_LABELS[i]}\n**{d.day}**")
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]
                opts_map=_get_recipe_options()
                opts=["-"] + list(opts_map.keys())
                current="-"
                if slot.get("recipe_id"):
                    cur=_find_recipe(slot["recipe_id"])
                    if cur:
                        current=f'{cur["name"]} · {cur.get("time","-")} min'
                        if current not in opts: opts.insert(1,current)
                sel_key=f"planner_sel_{i}_{meal}_{d.isoformat()}"
                serv_key=f"planner_serv_{i}_{meal}_{d.isoformat()}"
                selected = st.selectbox(meal, opts, index=opts.index(current) if current in opts else 0, key=sel_key)
                if selected != "-":
                    slot["recipe_id"] = opts_map.get(selected, slot.get("recipe_id"))
                    rec=_find_recipe(slot["recipe_id"])
                    if rec:
                        with st.expander("Dettagli", expanded=False):
                            if rec.get("image"):
                                img=_fetch_image_bytes(rec["image"])
                                st.image(img, use_container_width=True) if img else st.info("Immagine non caricabile.")
                            st.caption(f"⏱ {rec['time']} min · Categoria: {rec.get('category','-')}")
                            st.write(rec.get("description",""))
                        slot["servings"] = st.number_input("Porzioni", 1, 12, value=slot.get("servings",2), key=serv_key)
                else:
                    slot["recipe_id"]=None

    _save_planner_if_changed()
    with st.expander("Lista della spesa (settimana corrente)", expanded=True):
        _render_shopping_list_ui(embed=False)

# =========================
# RICETTE (form unico + autosave)
# =========================
elif page == "Ricette":
    st.header("Ricettario")

    # Anchor per scroll immediato al form dopo "Modifica"
    st.markdown('<div id="recipe_form_top"></div>', unsafe_allow_html=True)
    if st.session_state.get("scroll_to_form"):
        components.html(
            "<script>document.getElementById('recipe_form_top').scrollIntoView({behavior:'instant',block:'start'});</script>",
            height=0
        )
        st.session_state.scroll_to_form=False

    # ---------- FORM (UNICO) ----------
    st.subheader("Aggiungi / Modifica ricetta")
    mode=st.session_state.recipe_form_mode
    editing=_find_recipe(st.session_state.editing_recipe_id) if mode=="edit" else None

    def _form_prefix(): return f"rf_{mode}_{st.session_state.editing_recipe_id or 'new'}"
    cur_prefix=_form_prefix()
    if st.session_state.get("_active_form_prefix") != cur_prefix:
        for k in list(st.session_state.keys()):
            if isinstance(k,str) and k.startswith("rf_"):
                try: del st.session_state[k]
                except Exception: pass
        st.session_state["_active_form_prefix"]=cur_prefix

    with st.form("recipe_form_main", clear_on_submit=(mode=="add")):
        name = st.text_input("Nome", value=editing["name"] if editing else "")
        category = st.text_input("Categoria", value=editing.get("category","") if editing else "")
        time_min = st.number_input("Tempo (minuti)", min_value=0, value=_safe_int(editing.get("time",0)) if editing else 0)
        servings = st.number_input("Porzioni base", min_value=1, value=_safe_int(editing.get("servings",2)) if editing else 2)
        image = st.text_input("URL immagine (opzionale)", value=editing.get("image","") if editing else "")
        description = st.text_area("Descrizione", value=editing.get("description","") if editing else "")

        st.markdown("**Ingredienti**")
        defaults = editing.get("ingredients", []) if editing else []
        ingr_count = st.number_input("Numero ingredienti", 0, 50, value=len(defaults) if defaults else 5, key=f"{cur_prefix}_ing_count")

        ingredients: List[Dict[str, Any]] = []
        for idx in range(int(ingr_count)):
            c1,c2,c3=st.columns([3,1,1])
            d = defaults[idx] if idx < len(defaults) else {"name":"","qty":0,"unit":UNITS[0]}
            name_i = c1.text_input(f"Ingrediente {idx+1} - nome", value=d.get("name",""), key=f"{cur_prefix}_ing_name_{idx}")
            qty_i = c2.number_input(f"Quantità {idx+1}", min_value=0.0, value=float(d.get("qty",0)), key=f"{cur_prefix}_ing_qty_{idx}")
            unit_i = c3.selectbox(f"Unità {idx+1}", UNITS, index=(UNITS.index(d.get("unit")) if d.get("unit") in UNITS else 0), key=f"{cur_prefix}_ing_unit_{idx}")
            if name_i: ingredients.append({"name":name_i,"qty":qty_i,"unit":unit_i})

        instructions = st.text_area("Istruzioni", value=editing.get("instructions","") if editing else "", key=f"{cur_prefix}_instructions")

        ca=st.columns(3)
        with ca[0]: submit = st.form_submit_button("💾 Salva ricetta")
        with ca[1]: new_btn = st.form_submit_button("➕ Nuova (svuota)")
        with ca[2]: cancel_btn = st.form_submit_button("❌ Annulla modifica")

        if submit:
            if not name.strip():
                st.error("Il nome è obbligatorio.")
            else:
                payload = {
                    "name": name.strip(), "category": category.strip(),
                    "time": int(time_min), "servings": int(servings),
                    "image": image.strip(), "description": description.strip(),
                    "ingredients": ingredients, "instructions": instructions.strip(),
                }
                if mode=="edit" and editing:
                    editing.update(payload)
                    st.success(f"Ricetta '{name}' aggiornata.")
                else:
                    payload["id"]=_get_new_recipe_id()
                    st.session_state.recipes.append(payload)
                    st.success(f"Ricetta '{name}' aggiunta.")
                # Autosave su Sheets
                try:
                    save_to_sheets()
                    st.toast("Ricette salvate su Google Sheets ✓")
                except Exception as e:
                    st.warning(f"Salvataggio su Sheets non riuscito ora: {e}")
                st.session_state.recipe_form_mode="add"
                st.session_state.editing_recipe_id=None

        if new_btn:
            st.session_state.recipe_form_mode="add"
            st.session_state.editing_recipe_id=None
            st.session_state.scroll_to_form=True
            _rerun()

        if cancel_btn:
            st.session_state.recipe_form_mode="add"
            st.session_state.editing_recipe_id=None
            st.info("Modifica annullata.")

    st.divider()

    # ---------- FILTRI ----------
    f1,f2,f3=st.columns([2,1,1])
    text_query=f1.text_input("Cerca per nome/descrizione","")
    categories=sorted({r.get("category","").strip() for r in st.session_state.recipes if r.get("category")})
    cat=f2.selectbox("Categoria", ["Tutte"]+categories)
    max_time=f3.number_input("Tempo max (min)", min_value=0, value=0)

    def _passes_filters(r):
        if text_query:
            q=text_query.lower()
            if q not in r.get("name","").lower() and q not in r.get("description","").lower():
                return False
        if cat!="Tutte" and r.get("category")!=cat:
            return False
        if max_time and _safe_int(r.get("time",0))>max_time:
            return False
        return True

    filtered=[r for r in st.session_state.recipes if _passes_filters(r)]
    st.caption(f"{len(filtered)} ricette trovate")

    # ---------- LISTA ----------
    for r in filtered:
        with st.container(border=True):
            c1,c2=st.columns([1,2])
            with c1:
                if r.get("image"):
                    img=_fetch_image_bytes(r["image"])
                    st.image(img, use_container_width=True) if img else st.write("Nessuna anteprima")
            with c2:
                st.subheader(r["name"])
                st.caption(f"Categoria: {r.get('category','-')} · ⏱ {r.get('time','-')} min · Porzioni base: {r.get('servings','-')}")
                if r.get("description"): st.write(r["description"])
                with st.expander("Ingredienti"):
                    df=pd.DataFrame(r.get("ingredients", []))
                    if not df.empty: st.dataframe(df, hide_index=True, use_container_width=True)
                if r.get("instructions"):
                    with st.expander("Istruzioni"): st.write(r["instructions"])
                b1,b2=st.columns(2)
                if b1.button("✏️ Modifica", key=f"edit_{r['id']}"):
                    st.session_state.recipe_form_mode="edit"
                    st.session_state.editing_recipe_id=r["id"]
                    st.session_state.scroll_to_form=True
                    _rerun()
                if b2.button("🗑️ Elimina", key=f"del_{r['id']}"):
                    st.session_state.recipes=[x for x in st.session_state.recipes if x["id"]!=r["id"]]
                    try: save_to_sheets()
                    except Exception: pass
                    st.toast(f"Ricetta '{r['name']}' eliminata")

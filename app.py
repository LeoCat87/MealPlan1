# app.py — MealPlanner (profilo + autosave + immagini lato server)
# Versione ottimizzata:
# - Profili persistenti su Google Sheet (_profiles)
# - Parser boolean robusto per "favorite"
# - ID ricette garantiti unici anche se mancano su più righe
# - CSS stabile (no classi Emotion fragili)
# - Immagini: fallback silenzioso
# - Flush salvataggio planner quando si cambia settimana
# - Freeze header nelle worksheet create
# - Lista spesa: elementi "Comprato" in fondo, export solo Excel
# - Form ricette: pulsante "Clona"
# - Diagnostica UI rimossa
# - FIX: "Numero ingredienti" fuori dalla form (rerun immediato) + nessuna chiave duplicata

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
from gspread.exceptions import APIError

# =========================
# HELPERS / ERRORI
# =========================
def _gs_errmsg(e: Exception) -> str:
    """Estrae un messaggio leggibile da APIError di gspread/Google."""
    try:
        if isinstance(e, APIError):
            try:
                j = e.response.json()
                return j.get("error", {}).get("message") or e.response.text or str(e)
            except Exception:
                return getattr(e.response, "text", "") or str(e)
        return str(e)
    except Exception:
        return str(e)

def _safe_int(x, default=0):  # usato in vari punti
    try:
        return int(x)
    except Exception:
        return default

def _to_bool(x) -> bool:
    """Converte in booleano da varie rappresentazioni ('TRUE','false','1', ecc.)."""
    if isinstance(x, bool):
        return x
    if x is None:
        return False
    s = str(x).strip().lower()
    return s in {"true", "1", "yes", "y", "on"}

# =========================
# ENV & COSTANTI
# =========================
ENV = st.secrets.get("env", "prod")
SPREADSHEET_NAME = "MealPlannerDB_prod" if ENV == "prod" else "MealPlannerDB_dev"
SHOW_ENV_BANNER = (ENV == "dev")

APP_TITLE = "MealPlanner"
DAYS_LABELS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MEALS = ["Pranzo", "Cena"]
UNITS = ["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]

# =========================
# RERUN compat
# =========================
def _rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

# =========================
# IMMAGINI (download lato server, con cache)
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
        if "?raw=1" not in u:
            u = u.replace("?dl=0", "?raw=1").replace("?dl=1", "?raw=1")

    # Unsplash CDN: auto format jpg
    if "images.unsplash.com" in u:
        sep = "&" if "?" in u else "?"
        if "auto=" not in u:
            u += f"{sep}auto=format"; sep = "&"
        if "fm=" not in u:
            u += f"{sep}fm=jpg"
    return u

@st.cache_data(show_spinner=False, ttl=60*60*24)
def _fetch_image_bytes(u: str) -> bytes | None:
    try:
        url = _resolve_image_url(u)
        if not url or not url.startswith("http"):
            return None
        r = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=8,
        )
        r.raise_for_status()
        data = r.content
        return data if data and len(data) >= 32 else None
    except Exception:
        return None

def _render_image_from_url(url: str):
    """Scarica e mostra un'immagine; ritorna True se mostrata.
       Fallback silenzioso per non inquinare la UI con messaggi."""
    if not url:
        st.empty()
        return False
    img = _fetch_image_bytes(url)
    if img:
        st.image(img, use_container_width=True)
        return True
    # placeholder basso profilo
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    return False

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
            s = next(i for i, l in enumerate(lines) if "BEGIN PRIVATE KEY" in l)
            e = next(i for i, l in enumerate(lines) if "END PRIVATE KEY" in l)
            body = [l for l in lines[s + 1 : e] if "PRIVATE KEY" not in l]
            pk = (
                "-----BEGIN PRIVATE KEY-----\n"
                + "\n".join(body)
                + "\n-----END PRIVATE KEY-----"
            )
        except StopIteration:
            pass
    else:
        pk = "\n".join(lines)
    return pk

def _get_sheet_client_and_error():
    """Ritorna (client, error_string). client=None se fallisce. (no cache: per diagnostica)"""
    try:
        info = dict(st.secrets["gcp_service_account"])
    except Exception:
        return None, "Sezione [gcp_service_account] assente nei secrets."

    try:
        missing = [
            k for k in [
                "type","project_id","private_key_id","private_key","client_email","client_id","token_uri"
            ] if k not in info
        ]
        if missing:
            return None, f"Mancano campi nei secrets: {', '.join(missing)}"

        pk = info.get("private_key", "")
        if not isinstance(pk, str) or "PRIVATE KEY" not in pk:
            return None, "private_key non sembra una chiave PEM valida."

        info["private_key"] = _normalize_private_key(pk)
        creds = Credentials.from_service_account_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly",
            ],
        )
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

@st.cache_resource(show_spinner=False)
def _get_sheet_client_cached():
    """Client Sheets con cache (uso in load/save)."""
    client, _ = _get_sheet_client_and_error()
    return client

def _get_sheet_client():
    return _get_sheet_client_cached()

def _secrets_healthcheck():
    ok_client, err = _get_sheet_client_and_error()
    if ok_client:
        st.success("✅ Credenziali Google valide.")
    else:
        st.error("❌ Credenziali Google non disponibili o non valide.")
        if err:
            st.caption(err)

# =========================
# PROFILI (worksheet per profilo) + Lista profili persistente
# =========================
def _sheet_name_for(base: str, profile: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (profile or "Default").strip())
    return f"{base}__{safe}" if safe.lower() != "default" else base

def _get_or_create_ws(sh, title: str, headers: List[str]):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=400, cols=max(10, len(headers) or 10))
        if headers:
            ws.update([headers])
            try:
                ws.freeze(rows=1)
            except Exception:
                pass
    return ws

def _safe_update(ws, rows: List[dict] | List[list]):
    """
    Aggiorna un worksheet scrivendo da A1 headers + righe.
    Accetta:
      - lista di dict (usa le key del primo come headers)
      - lista di liste (prima sottolista = headers)
    Ridimensiona prima per evitare errori di range.
    """
    if not rows:
        ws.clear()
        return
    if isinstance(rows[0], dict):
        headers = list(rows[0].keys())
        values = [headers] + [[row.get(h, "") for h in headers] for row in rows]
    else:
        values = rows
        headers = rows[0] if rows else []
    ws.resize(rows=len(values), cols=len(headers))
    ws.update("A1", values, value_input_option="RAW")

def _load_profiles_from_sheet():
    gc = _get_sheet_client()
    if gc is None:
        return
    sh = gc.open(SPREADSHEET_NAME)
    ws = _get_or_create_ws(sh, "_profiles", ["profile"])
    try:
        rows = ws.get_all_records()
    except Exception:
        rows = []
    plist = [r.get("profile", "").strip() for r in rows if r.get("profile")]
    if plist:
        st.session_state.profiles = sorted(set(["Default"] + plist))

def _save_profiles_to_sheet():
    gc = _get_sheet_client()
    if gc is None:
        return
    sh = gc.open(SPREADSHEET_NAME)
    ws = _get_or_create_ws(sh, "_profiles", ["profile"])
    rows = [{"profile": p} for p in st.session_state.profiles if p.strip().lower() != "default"]
    _safe_update(ws, rows)

def delete_profile(profile: str):
    """Elimina le worksheet del profilo dallo Sheet e lo rimuove dalla lista profili."""
    if profile.strip().lower() == "default":
        st.warning("Non è possibile eliminare il profilo Default.")
        return

    gc = _get_sheet_client()
    if gc is None:
        st.error("Impossibile connettersi a Google Sheets. Controlla i secrets.")
        return

    sh = gc.open(SPREADSHEET_NAME)
    targets = [
        _sheet_name_for("recipes", profile),
        _sheet_name_for("planner_slots", profile),
    ]
    deleted = []
    for title in targets:
        try:
            ws = sh.worksheet(title)
            sh.del_worksheet(ws)
            deleted.append(title)
        except gspread.WorksheetNotFound:
            pass
        except Exception as e:
            st.error(f"Errore eliminando '{title}': {e}")

    st.session_state.profiles = [p for p in st.session_state.profiles if p != profile]

    if st.session_state.get("current_profile") == profile:
        st.session_state.current_profile = "Default"
        try:
            load_from_sheets()
        except Exception:
            pass

    try:
        _save_profiles_to_sheet()
    except Exception:
        pass

    st.success(
        f"Profilo '{profile}' eliminato. Schede rimosse: "
        f"{', '.join(deleted) if deleted else 'nessuna trovata'}."
    )

# =========================
# DEMO / STATO
# =========================
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
            "favorite": False,
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
            "favorite": False,
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

def _get_new_recipe_id_from(used: set[int]) -> int:
    m = max(used) if used else 0
    return m + 1

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
    st.session_state.setdefault("profiles", ["Default"])
    st.session_state.setdefault("current_profile", "Default")
    st.session_state.setdefault("recipes", _demo_recipes())
    st.session_state.setdefault("planner", _empty_week())
    if "week_start" not in st.session_state:
        today = date.today()
        st.session_state.week_start = today - timedelta(days=today.weekday())
    st.session_state.setdefault("recipe_form_mode", "add")
    st.session_state.setdefault("editing_recipe_id", None)
    st.session_state.setdefault("page", "Pianificatore settimanale")
    st.session_state.setdefault("_show_diag_default", SHOW_ENV_BANNER)
    st.session_state.planner = _normalize_planner_meal_keys(st.session_state.planner, MEALS)

# =========================
# PERSISTENZA planner (debounced)
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
    if "planner" not in st.session_state:
        return
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
    st.session_state.setdefault("shopping_checklists", {})
    if df.empty:
        st.session_state.shopping_checklists[wk]=[]
        return
    cur=st.session_state.shopping_checklists.get(wk)
    if not cur:
        df["Comprato"]=False
        st.session_state.shopping_checklists[wk]=df.to_dict("records")
    else:
        prev={(r["Ingrediente"],r["Unità"]): r.get("Comprato",False) for r in cur}
        df["Comprato"]=df.apply(lambda r: prev.get((r["Ingrediente"],r["Unità"]),False), axis=1)
        st.session_state.shopping_checklists[wk]=df.to_dict("records")
    # ordina: comprati in fondo
    recs = st.session_state.shopping_checklists[wk]
    recs.sort(key=lambda r: (r.get("Comprato", False), r["Ingrediente"], r["Unità"]))
    st.session_state.shopping_checklists[wk] = recs

def _render_shopping_list_ui(embed: bool=True):
    _ensure_week_checklist()
    wk=_week_key(); recs=st.session_state.shopping_checklists[wk]
    if embed:
        st.subheader("🧾 Lista della spesa — settimana corrente")
        st.caption(f"{st.session_state.week_start.strftime('%d/%m/%Y')} → {(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}")
    for idx, row in enumerate(recs):
        # card con bordo e checkbox più facile da toccare
        with st.container(border=True):
            cols = [0.18, 0.52, 0.15, 0.15] if st.session_state.get("is_mobile") else [0.14, 0.56, 0.15, 0.15]
            c = st.columns(cols)
            with c[0]:
                bought = st.checkbox(" ", value=row.get("Comprato", False), key=f"buy_{wk}_{idx}")
            with c[1]:
                st.write(f"**{row['Ingrediente']}**")
            with c[2]:
                st.write(row["Quantità"])
            with c[3]:
                st.write(row["Unità"])
            recs[idx]["Comprato"] = bought

    df=pd.DataFrame(recs)
    buf=BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="ShoppingList")

    # Solo export Excel (CSV rimosso)
    st.markdown('<div class="sticky-bottom">', unsafe_allow_html=True)
    st.download_button("⬇️ Excel", buf.getvalue(), "shopping_list.xlsx", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# =========================
# GOOGLE SHEETS: LOAD / SAVE (profilo) — SAFE
# =========================
def load_from_sheets():
    gc=_get_sheet_client()
    if gc is None:
        st.warning("Caricamento da Google Sheets non disponibile (credenziali mancanti o non valide).")
        return
    sh=gc.open(SPREADSHEET_NAME)
    prof=st.session_state.get("current_profile","Default")

    # --- Ricette
    ws_recipes=_get_or_create_ws(
        sh, _sheet_name_for("recipes",prof),
        ["id","name","category","time","servings","image","description","instructions","ingredients_json","favorite"]
    )
    rows=ws_recipes.get_all_records()
    st.session_state.recipes=[]
    used_ids = set()
    for r in rows:
        try:
            ings=json.loads(r.get("ingredients_json","[]"))
        except Exception:
            ings=[]
        rid = _safe_int(r.get("id",0))
        if not rid or rid in used_ids:
            rid = _get_new_recipe_id_from(used_ids)
        used_ids.add(rid)
        st.session_state.recipes.append({
            "id": rid,
            "name": r.get("name",""), "category": r.get("category",""),
            "time": _safe_int(r.get("time",0)), "servings": _safe_int(r.get("servings",2)) or 2,
            "image": r.get("image",""), "description": r.get("description",""),
            "instructions": r.get("instructions",""), "ingredients": ings,
            "favorite": _to_bool(r.get("favorite", False)),
        })

    # Se non ci sono ricette, popola con demo per evitare UI “vuota”
    if not st.session_state.recipes:
        st.session_state.recipes = _demo_recipes()

    # --- Planner: SOLO settimana corrente
    ws_slots=_get_or_create_ws(sh, _sheet_name_for("planner_slots",prof), ["week_start","date","meal","recipe_id","servings"])
    slots=ws_slots.get_all_records()
    wk_start=st.session_state.week_start
    planner=_empty_week(wk_start)
    by_date={(wk_start + timedelta(days=i)).isoformat(): i for i in range(7)}

    for s in slots:
        d=str(s.get("date","")).strip()
        if not d or d not in by_date: continue
        i=by_date[d]
        meal=str(s.get("meal","")).strip()
        if meal not in MEALS: continue
        rid=_safe_int(s.get("recipe_id")) if str(s.get("recipe_id","")).strip() else None
        serv=_safe_int(s.get("servings",2)) or 2
        planner["days"][i][meal]={"recipe_id":rid,"servings":serv}

    st.session_state.planner=planner
    st.success(f"✅ Dati caricati per profilo: {prof}")

def save_to_sheets():
    gc = _get_sheet_client()
    if gc is None:
        st.warning("Salvataggio su Google Sheets non disponibile (credenziali mancanti o non valide).")
        return

    sh = gc.open(SPREADSHEET_NAME)
    prof = st.session_state.get("current_profile", "Default")

    try:
        # ----- RICETTE (overwrite intero profilo)
        ws_recipes = _get_or_create_ws(
            sh, _sheet_name_for("recipes", prof),
            ["id","name","category","time","servings","image","description","instructions","ingredients_json","favorite"]
        )
        rows_recipes = [{
            "id": r["id"],
            "name": r.get("name",""),
            "category": r.get("category",""),
            "time": int(r.get("time",0) or 0),
            "servings": int(r.get("servings",2) or 2),
            "image": r.get("image",""),
            "description": r.get("description",""),
            "instructions": r.get("instructions",""),
            "ingredients_json": json.dumps(r.get("ingredients", []), ensure_ascii=False),
            "favorite": "TRUE" if bool(r.get("favorite", False)) else "FALSE",
        } for r in st.session_state.get("recipes", [])]
        _safe_update(ws_recipes, rows_recipes)

        # ----- PLANNER (storico preservato: sostituisce solo la settimana corrente)
        ws_slots = _get_or_create_ws(
            sh, _sheet_name_for("planner_slots", prof),
            ["week_start","date","meal","recipe_id","servings"]
        )

        existing = ws_slots.get_all_records()
        wk_start = st.session_state.week_start
        week_dates = {(wk_start + timedelta(days=i)).isoformat() for i in range(7)}

        kept = [row for row in existing if str(row.get("date","")).strip() not in week_dates]

        new_slots = []
        for d in st.session_state.get("planner", {}).get("days", []):
            the_date = d["date"]
            for meal, slot in d.items():
                if meal == "date":
                    continue
                new_slots.append({
                    "week_start": st.session_state.week_start.isoformat(),
                    "date": the_date,
                    "meal": meal,
                    "recipe_id": slot.get("recipe_id"),
                    "servings": slot.get("servings", 2),
                })

        combined = kept + new_slots
        _safe_update(ws_slots, combined)

        st.toast(f"Dati salvati (storico preservato) per profilo: {prof} ✓")

    except APIError as e:
        st.error(f"Errore Google Sheets: {_gs_errmsg(e)}")
        raise
    except Exception as e:
        st.error(f"Errore imprevisto nel salvataggio: {e}")
        raise

def _sheets_write_probe():
    """Prova di scrittura: aggiunge una riga con timestamp in un foglio '_diagnostics'."""
    gc = _get_sheet_client()
    if gc is None:
        raise RuntimeError("Client Google Sheets non disponibile (controlla i secrets).")
    sh = gc.open(SPREADSHEET_NAME)
    ws = _get_or_create_ws(sh, "_diagnostics", ["ts", "note"])
    ws.append_row([time.strftime("%Y-%m-%d %H:%M:%S"), "probe write OK"], value_input_option="RAW")

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
/* immagini: fit e bordo */
.stImage > img { object-fit: cover; width: 100%; max-height: 220px; border-radius: 12px; }

/* container border: fallback generico, evita classi Emotion */
div[role="region"][tabindex="0"] { padding: .6rem; }

/* sticky bars su mobile */
@media (max-width: 640px){
  .stButton>button, .stDownloadButton>button { width: 100%; }
}
</style>
""", unsafe_allow_html=True)

# --- Mobile mode (toggle) + CSS mobile-first
if "is_mobile" not in st.session_state:
    st.session_state.is_mobile = False  # puoi metterlo True se pubblichi solo per smartphone

with st.sidebar:
    st.toggle("📱 Modalità mobile", key="is_mobile", help="Usa layout verticale, bottoni più grandi, meno scroll")

st.markdown("""
<style>
/* spacing generali */
.block-container { padding-top: .6rem; padding-bottom: 1.2rem; }

/* sidebar più stretta su mobile */
@media (max-width: 768px){
  section[data-testid="stSidebar"] { width: 280px !important; min-width: 280px !important; }
}

/* header trasparente già presente */
[data-testid="stHeader"] { background: transparent; }

/* pulsanti e select: touch-friendly */
div.stButton > button, div.stDownloadButton > button, a[kind="link"] {
  border-radius: 12px; padding: .8rem 1rem; font-weight: 600; border: 1px solid rgba(255,255,255,0.12);
}
div[data-baseweb="select"] > div { border-radius: 12px !important; min-height: 48px; }
input[type="text"], input[type="number"], textarea, .st-af { border-radius: 12px !important; }

/* immagini: rapporto costante e taglio per ridurre altezza */
.stImage > img { object-fit: cover; width: 100%; max-height: 220px; border-radius: 12px; }

/* titoli compatti su mobile */
@media (max-width: 768px){
  h1, h2, h3 { line-height: 1.2; }
  .stMarkdown p { margin-bottom: .3rem; }
  .st-expanderContent { padding-top: .4rem; padding-bottom: .4rem; }
}

/* top navigator sticky */
#weekbar { position: sticky; top: 0; z-index: 20; backdrop-filter: blur(6px); padding: .4rem 0; }

/* bottom actions sticky su mobile (download lista ecc.) */
@media (max-width: 768px){
  .sticky-bottom {
    position: sticky; bottom: 0; z-index: 25; padding: .5rem 0 .3rem 0;
    background: linear-gradient(180deg, transparent, rgba(0,0,0,.10));
  }
  .sticky-bottom > div > button { width: 100% !important; }
}
</style>
""", unsafe_allow_html=True)

_init_state()

# bootstrap auto-load una sola volta
if not st.session_state.get("_boot_loaded"):
    try:
        _load_profiles_from_sheet()
        load_from_sheets()
    except Exception:
        st.info("Avvio con dati locali (nessun Google Sheet disponibile).")
    st.session_state["_boot_loaded"] = True

# =========================
# SIDEBAR (auto)
# =========================
with st.sidebar:
    if SHOW_ENV_BANNER:
        st.warning("🧪 AMBIENTE: DEV (usa dati di test)")
    st.caption(f"ENV: {ENV} · Sheet: {SPREADSHEET_NAME}")
    st.title(APP_TITLE)

    st.caption("Profilo")

    if st.session_state.get("_clear_new_profile"):
        st.session_state["new_profile_name"] = ""
        del st.session_state["_clear_new_profile"]

    def _on_profile_change():
        try:
            load_from_sheets()
            st.toast("Profilo caricato ✓")
        except Exception:
            st.info("Caricamento automatico non disponibile (secrets mancanti?)")

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
                try:
                    _save_profiles_to_sheet()
                    save_to_sheets()
                except Exception:
                    pass
                st.session_state["_clear_new_profile"] = True
                _rerun()

    st.selectbox(
        "Seleziona profilo",
        st.session_state.profiles,
        key="current_profile",
        label_visibility="collapsed",
        on_change=_on_profile_change,
    )

    st.divider()
    pages = ["Pianificatore settimanale", "Ricette"]
    if st.session_state.page not in pages:
        st.session_state.page = "Pianificatore settimanale"
    st.radio("Sezioni", pages, index=pages.index(st.session_state.page), key="page")

    st.divider()
    st.caption("Gestione profili")
    deletable = [p for p in st.session_state.profiles if p.strip().lower() != "default"]
    if deletable:
        colx, coly = st.columns([2,1])
        with colx:
            prof_to_delete = st.selectbox("Elimina profilo", deletable, key="delete_profile_select")
        with coly:
            confirm = st.text_input("Conferma", placeholder="Scrivi ELIMINA", label_visibility="collapsed", key="delete_profile_confirm")
        if st.button("❌ Elimina profilo", use_container_width=True):
            if (confirm or "").strip().upper() == "ELIMINA":
                delete_profile(prof_to_delete)
            else:
                st.warning("Digita 'ELIMINA' nel campo di conferma per procedere.")
    else:
        st.info("Nessun profilo eliminabile (solo 'Default' presente).")

# Variabile locale sicura anche se ci sono rerun
page = st.session_state.get("page", "Pianificatore settimanale")

# =========================
# PIANIFICATORE
# =========================
if page == "Pianificatore settimanale":
    st.header("Pianificatore settimanale")

    # --- barra settimana sticky (sempre comoda su mobile)
    st.markdown('<div id="weekbar"></div>', unsafe_allow_html=True)
    nb = st.columns([1, 3, 1])
    with nb[0]:
        if st.button("◀︎", use_container_width=True, key="nav_prev"):
            _save_planner_if_changed(debounce_sec=0)  # flush prima di cambiare settimana
            st.session_state.week_start -= timedelta(days=7)
            try:
                load_from_sheets()
            except Exception:
                st.session_state.planner = _empty_week(st.session_state.week_start)
    with nb[1]:
        st.caption(
            f"Settimana: {st.session_state.week_start.strftime('%d/%m/%Y')} - "
            f"{(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"
        )
    with nb[2]:
        if st.button("▶︎", use_container_width=True, key="nav_next"):
            _save_planner_if_changed(debounce_sec=0)  # flush prima di cambiare settimana
            st.session_state.week_start += timedelta(days=7)
            try:
                load_from_sheets()
            except Exception:
                st.session_state.planner = _empty_week(st.session_state.week_start)

    # Precalcolo opzioni ricette (come prima)
    recipes = st.session_state.recipes
    opts_map = {f'{r["name"]} · {r.get("time","-")} min': r["id"] for r in recipes}
    id_to_label = {v:k for k,v in opts_map.items()}
    base_opts = ["-"] + list(opts_map.keys())

    # --- DESKTOP: 7 colonne come già facevi
    if not st.session_state.is_mobile:
        day_cols = st.columns([0.5,1,1,1,1,1,1,1,0.5])[1:-1]
        for i, col in enumerate(day_cols):
            d = st.session_state.week_start + timedelta(days=i)
            with col:
                st.markdown(f"### {DAYS_LABELS[i]}\n**{d.day}**")
                for meal in MEALS:
                    slot = st.session_state.planner["days"][i][meal]
                    current = "-" if not slot.get("recipe_id") else id_to_label.get(slot["recipe_id"], "-")
                    opts = base_opts if current == "-" else (["-"] + ([current] if current not in base_opts else []) + [o for o in base_opts if o != current and o != "-"])
                    sel_key=f"planner_sel_{i}_{meal}_{d.isoformat()}"
                    serv_key=f"planner_serv_{i}_{meal}_{d.isoformat()}"
                    selected = st.selectbox(meal, opts, index=opts.index(current) if current in opts else 0, key=sel_key, label_visibility="visible")
                    if selected != "-":
                        slot["recipe_id"] = opts_map.get(selected, slot.get("recipe_id"))
                        rec=_find_recipe(slot["recipe_id"])
                        if rec:
                            with st.expander("Dettagli", expanded=False):
                                _render_image_from_url(rec.get("image"))
                                st.caption(f"⏱ {rec['time']} min · Categoria: {rec.get('category','-')}")
                                st.write(rec.get("description",""))
                            slot["servings"] = st.number_input("Porzioni", 1, 12, value=slot.get("servings",2), key=serv_key)
                    else:
                        slot["recipe_id"]=None

    # --- MOBILE: lista verticale per giorno con accordion compatti
    else:
        for i in range(7):
            d = st.session_state.week_start + timedelta(days=i)
            st.markdown(f"### {DAYS_LABELS[i]} · **{d.day}**")
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]
                current = "-" if not slot.get("recipe_id") else id_to_label.get(slot["recipe_id"], "-")
                opts = base_opts if current == "-" else (["-"] + ([current] if current not in base_opts else []) + [o for o in base_opts if o != current and o != "-"])
                sel_key=f"m_planner_sel_{i}_{meal}_{d.isoformat()}"
                serv_key=f"m_planner_serv_{i}_{meal}_{d.isoformat()}"

                # riga compatta: titolo pasto + select + stepper porzioni in linea
                c1, c2 = st.columns([2, 1])
                with c1:
                    selected = st.selectbox(
                        f"{meal}", opts, index=opts.index(current) if current in opts else 0,
                        key=sel_key, label_visibility="visible"
                    )
                with c2:
                    slot["servings"] = st.number_input("Porz.", 1, 12, value=slot.get("servings",2), key=serv_key, label_visibility="visible")

                if selected != "-":
                    slot["recipe_id"] = opts_map.get(selected, slot.get("recipe_id"))
                    rec = _find_recipe(slot["recipe_id"])
                    if rec:
                        with st.expander("Dettagli ricetta"):
                            _render_image_from_url(rec.get("image"))
                            st.caption(f"⏱ {rec['time']} min · Categoria: {rec.get('category','-')}")
                            if rec.get("description"): st.write(rec.get("description"))
                else:
                    slot["recipe_id"] = None

            st.divider()

    _save_planner_if_changed()

    # Lista spesa: su mobile chiusa di default + azioni sticky in basso
    with st.expander("🧾 Lista della spesa (settimana corrente)", expanded=not st.session_state.is_mobile):
        _render_shopping_list_ui(embed=False)

# =========================
# RICETTE (form unico + autosave) — con contatore ingredienti fuori dalla form
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

    # ---------- SETUP E CONTATORE (FUORI DALLA FORM) ----------
    st.subheader("Aggiungi / Modifica ricetta")
    mode=st.session_state.recipe_form_mode
    editing=_find_recipe(st.session_state.editing_recipe_id) if mode=="edit" else None

    def _form_prefix(): return f"rf_{mode}_{st.session_state.editing_recipe_id or 'new'}"
    cur_prefix=_form_prefix()

    # reset state dei widget rf_* quando cambia il form
    if st.session_state.get("_active_form_prefix") != cur_prefix:
        for k in list(st.session_state.keys()):
            if isinstance(k,str) and k.startswith("rf_"):
                try: del st.session_state[k]
                except Exception: pass
        st.session_state["_active_form_prefix"]=cur_prefix

    defaults = editing.get("ingredients", []) if editing else []

    count_key = f"{cur_prefix}_ing_count_live"
    default_count = st.session_state.get(count_key, len(defaults) if defaults else 5)
    
    ingr_count = st.number_input(
        "Numero ingredienti",
        min_value=0,
        max_value=50,
        value=default_count,
        step=1,
        key=count_key,
    )
    
    live_count = int(ingr_count)   # oppure: int(st.session_state[count_key])

    # ---------- FORM ----------
    with st.form("recipe_form_main", clear_on_submit=(mode=="add")):
        name = st.text_input("Nome", value=editing["name"] if editing else "")
        category = st.text_input("Categoria", value=editing.get("category","") if editing else "")
        time_min = st.number_input("Tempo (minuti)", min_value=0, value=_safe_int(editing.get("time",0)) if editing else 0)
        servings = st.number_input("Porzioni base", min_value=1, value=_safe_int(editing.get("servings",2)) if editing else 2)
        image = st.text_input("URL immagine (opzionale)", value=editing.get("image","") if editing else "")
        description = st.text_area("Descrizione", value=editing.get("description","") if editing else "")

        exp_label = "Ingredienti (tocca per aprire)" if st.session_state.is_mobile else "Ingredienti"
        with st.expander(exp_label, expanded=not st.session_state.is_mobile):
            # NON mettere number_input qui dentro
            ingredients: List[Dict[str, Any]] = []
            for idx in range(live_count):
                c1, c2, c3 = st.columns([3, 1, 1])
                d = defaults[idx] if idx < len(defaults) else {"name": "", "qty": 0, "unit": UNITS[0]}
                name_i = c1.text_input(f"Ingrediente {idx+1} - nome", value=d.get("name",""), key=f"{cur_prefix}_ing_name_{idx}")
                qty_i  = c2.number_input(f"Quantità {idx+1}", min_value=0.0, value=float(d.get("qty",0)), key=f"{cur_prefix}_ing_qty_{idx}")
                unit_i = c3.selectbox(f"Unità {idx+1}", UNITS, index=(UNITS.index(d.get("unit")) if d.get("unit") in UNITS else 0), key=f"{cur_prefix}_ing_unit_{idx}")
                if name_i:
                    ingredients.append({"name": name_i, "qty": qty_i, "unit": unit_i})

        instructions = st.text_area("Istruzioni", value=editing.get("instructions","") if editing else "", key=f"{cur_prefix}_instructions")

        ca=st.columns(4)
        with ca[0]: submit = st.form_submit_button("💾 Salva ricetta")
        with ca[1]: new_btn = st.form_submit_button("➕ Nuova (svuota)")
        with ca[2]: cancel_btn = st.form_submit_button("❌ Annulla modifica")
        with ca[3]: clone_btn = st.form_submit_button("📄 Clona", help="Duplica la ricetta corrente")

        if submit:
            if not name.strip():
                st.error("Il nome è obbligatorio.")
            else:
                payload = {
                    "name": name.strip(), "category": category.strip(),
                    "time": int(time_min), "servings": int(servings),
                    "image": image.strip(), "description": description.strip(),
                    "ingredients": ingredients, "instructions": instructions.strip(),
                    "favorite": bool(editing.get("favorite", False)) if editing else False,
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
                except APIError as e:
                    st.error(f"Google Sheets APIError: {_gs_errmsg(e)}")
                except Exception as e:
                    st.error(f"Salvataggio non riuscito: {e}")

                st.session_state.recipe_form_mode="add"
                st.session_state.editing_recipe_id=None

        if clone_btn and editing:
            clone = dict(editing)
            clone["id"] = _get_new_recipe_id()
            clone["name"] = f"{editing['name']} (copia)"
            st.session_state.recipes.append(clone)
            try:
                save_to_sheets()
                st.toast("Ricetta clonata ✓")
            except Exception as e:
                st.error(f"Salvataggio non riuscito: {e}")

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
                _render_image_from_url(r.get("image"))
            with c2:
                st.subheader(r["name"])
                st.caption(f"Categoria: {r.get('category','-')} · ⏱ {r.get('time','-')} min · Porzioni base: {r.get('servings','-')}")
                if r.get("description"): st.write(r["description"])
                with st.expander("Ingredienti", expanded=not st.session_state.is_mobile):
                    df = pd.DataFrame(r.get("ingredients", []))
                    if not df.empty:
                        st.dataframe(df, hide_index=True, use_container_width=True)
                
                if r.get("instructions"):
                    with st.expander("Istruzioni", expanded=False):
                        st.write(r["instructions"])

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

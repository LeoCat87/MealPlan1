import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import date, timedelta
import json
from typing import List, Dict, Any
import gspread
from google.oauth2.service_account import Credentials

# -----------------------------
# Config / Costanti
# -----------------------------
APP_TITLE = "MealPlanner"
DAYS_LABELS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MEALS = ["Pranzo", "Cena"]
UNITS = ["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]
DATA_FILE = "mealplanner_data.json"

# -----------------------------
# Utilità
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
# Persistenza dati
# -----------------------------
def _get_sheet_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)

SPREADSHEET_NAME = "MealPlannerDB"  # <-- il nome del tuo Google Sheet

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

# -----------------------------
# UI Base
# -----------------------------
st.set_page_config(page_title=APP_TITLE, page_icon="🍳", layout="wide")
_init_state()

with st.sidebar:
    st.title(APP_TITLE)
    page = st.radio("Sezioni", ["Pianificatore settimanale", "Ricette", "Lista della spesa"], index=0)
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
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]
                r_opts_map = _get_recipe_options()
                r_opts = ["-"] + list(r_opts_map.keys())
                current_label = "-"
                if slot.get("recipe_id"):
                    rec = _find_recipe(slot["recipe_id"])
                    if rec:
                        current_label = f'{rec["name"]} · {rec.get("time","-")} min'
                        if current_label not in r_opts:
                            r_opts.insert(1, current_label)
                selected = st.selectbox(
                    meal,
                    r_opts,
                    index=r_opts.index(current_label) if current_label in r_opts else 0,
                    key=f"sel_{i}_{meal}",
                )
                if selected != "-":
                    slot["recipe_id"] = r_opts_map.get(selected, slot.get("recipe_id"))
                    rec = _find_recipe(slot["recipe_id"])
                    if rec:
                        with st.expander("Dettagli", expanded=False):
                            if rec.get("image"):
                                st.image(rec["image"], use_container_width=True)
                            st.caption(f"⏱ {rec['time']} min · Categoria: {rec.get('category','-')}")
                            st.write(rec.get("description", ""))
                            st.caption("Ingredienti (per porzioni base):")
                            ingr = pd.DataFrame(rec.get("ingredients", []))
                            if not ingr.empty:
                                st.dataframe(ingr, hide_index=True, use_container_width=True)
                        slot["servings"] = st.number_input(
                            "Porzioni", min_value=1, max_value=12, value=slot.get("servings", 2), key=f"serv_{i}_{meal}"
                        )
                else:
                    slot["recipe_id"] = None

# -----------------------------
# RICETTE (CRUD)
# -----------------------------
elif page == "Ricette":
    st.header("Ricettario")

    # Filtri
    with st.container():
        f1, f2, f3 = st.columns([2, 1, 1])
        text_query = f1.text_input("Cerca per nome/descrizione", "")
        categories = sorted({r.get("category", "").strip() for r in st.session_state.recipes if r.get("category")})
        cat = f2.selectbox("Categoria", ["Tutte"] + categories)
        max_time = f3.number_input("Tempo max (min)", min_value=0, value=0)

    # Lista ricette filtrata
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
                    try:
                        st.image(r["image"], use_container_width=True)
                    except Exception:
                        st.write("Nessuna anteprima")
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

# -----------------------------
# LISTA DELLA SPESA
# -----------------------------
else:
    st.header("Lista della spesa")

    def aggregate_shopping_list():
        to_base = {"g": ("g", 1), "kg": ("g", 1000),
                   "ml": ("ml", 1), "l": ("ml", 1000),
                   "pcs": ("pcs", 1), "tbsp": ("tbsp", 1), "tsp": ("tsp", 1)}
        agg_base = {}
        for d in st.session_state.planner["days"]:
            for meal in MEALS:
                slot = d[meal]
                rid = slot["recipe_id"]
                servings_needed = slot["servings"]
                recipe = _find_recipe(rid)
                if not recipe:
                    continue
                scale = servings_needed / max(1, recipe.get("servings", 1))
                for ing in recipe.get("ingredients", []):
                    name = ing["name"].strip().title()
                    unit = str(ing["unit"]).lower()
                    qty = _safe_float(ing["qty"]) * scale
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
            rows.append({"Ingrediente": name, "Quantità": qty, "Unità": unit, "Comprato": False})
        rows.sort(key=lambda x: x["Ingrediente"])
        return pd.DataFrame(rows)

    # Stato checklist persistente nella sessione per la settimana corrente
    key_checklist = f"shopping_{st.session_state.week_start.isoformat()}"
    if key_checklist not in st.session_state:
        df_tmp = aggregate_shopping_list()
        st.session_state[key_checklist] = df_tmp.to_dict("records")

    # Visualizza e permette di spuntare
    df_records = st.session_state[key_checklist]
    st.caption("Spunta gli elementi acquistati:")
    for idx, row in enumerate(df_records):
        cols = st.columns([0.06, 0.64, 0.15, 0.15])
        with cols[0]:
            bought = st.checkbox("", value=row.get("Comprato", False), key=f"buy_{idx}")
        with cols[1]:
            st.write(row["Ingrediente"])
        with cols[2]:
            st.write(row["Quantità"])
        with cols[3]:
            st.write(row["Unità"])
        df_records[idx]["Comprato"] = bought

    # Pulsante per ricalcolare (se cambiano i pasti/porzioni)
    if st.button("🔄 Ricalcola da planner"):
        st.session_state[key_checklist] = aggregate_shopping_list().to_dict("records")
        st.toast("Lista aggiornata.")

    # Esportazioni
    df_export = pd.DataFrame(st.session_state[key_checklist])
    if not df_export.empty:
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            df_export.to_excel(writer, index=False, sheet_name="ShoppingList")
        st.download_button("⬇️ Scarica lista (Excel)", buffer.getvalue(), "shopping_list.xlsx", use_container_width=True)
        st.download_button("⬇️ Scarica lista (CSV)", df_export.to_csv(index=False).encode("utf-8"), "shopping_list.csv", use_container_width=True)

st.caption("Creato con Streamlit · MVP+")

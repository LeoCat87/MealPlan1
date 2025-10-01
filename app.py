import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import date, timedelta
import json

# -----------------------------
# Config / Costanti
# -----------------------------
APP_TITLE = "MealPlanner"
DAYS_LABELS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MEALS = ["Pranzo", "Cena"]
UNITS = ["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]
DATA_FILE = "mealplanner_data.json"


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


def _get_recipe_options():
    return {r["name"]: r["id"] for r in st.session_state.recipes}


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
def save_to_file():
    data = {
        "recipes": st.session_state.recipes,
        "planner": st.session_state.planner,
        "week_start": str(st.session_state.week_start)
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_from_file():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        st.session_state.recipes = data.get("recipes", st.session_state.recipes)
        st.session_state.planner = data.get("planner", st.session_state.planner)
        if "week_start" in data:
            st.session_state.week_start = date.fromisoformat(data["week_start"])
        st.session_state.planner = _normalize_planner_meal_keys(st.session_state.planner, MEALS)
        st.success("Dati caricati dal file locale.")
    except FileNotFoundError:
        st.warning("Nessun file locale trovato: verranno usati i dati demo.")


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title=APP_TITLE, page_icon="🍳", layout="wide")
_init_state()

with st.sidebar:
    st.title(APP_TITLE)
    page = st.radio("Sezioni", ["Pianificatore settimanale", "Ricette", "Lista della spesa"], index=0)
    st.divider()
    col_a, col_b = st.columns(2)
    if col_a.button("💾 Salva"):
        save_to_file()
    if col_b.button("📂 Carica"):
        load_from_file()

# -----------------------------
# PIANIFICATORE
# -----------------------------
if page == "Pianificatore settimanale":
    st.header("Pianificatore settimanale")

    nav_cols = st.columns([0.4, 1, 1, 1, 1, 1, 1, 1, 0.4])
    with nav_cols[0]:
        if st.button("◀︎", use_container_width=True):
            st.session_state.week_start -= timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)
    with nav_cols[-1]:
        if st.button("▶︎", use_container_width=True):
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
            st.markdown(f"### {DAYS_LABELS[i]}\\n**{day_date.day}**")
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]
                r_opts = ["-"] + list(_get_recipe_options().keys())
                selected = st.selectbox(" ", r_opts, key=f"sel_{i}_{meal}", label_visibility="collapsed")
                if selected != "-":
                    slot["recipe_id"] = _get_recipe_options()[selected]
                    rec = _find_recipe(slot["recipe_id"])
                    if rec:
                        st.caption(f"⏱ {rec['time']} min · Porzioni:")
                        slot["servings"] = st.number_input(
                            "Porzioni", min_value=1, max_value=12, value=slot["servings"], key=f"serv_{i}_{meal}"
                        )
                else:
                    slot["recipe_id"] = None

# -----------------------------
# RICETTE
# -----------------------------
elif page == "Ricette":
    st.header("Ricettario")
    st.write("Gestisci qui le tue ricette.")

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
                    unit = ing["unit"].lower()
                    qty = float(ing["qty"]) * scale
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
        return pd.DataFrame(rows)

    df = aggregate_shopping_list()
    st.dataframe(df, use_container_width=True, hide_index=True)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="ShoppingList")
    st.download_button("Scarica lista (Excel)", buffer.getvalue(), "shopping_list.xlsx")
    st.download_button("Scarica lista (CSV)", df.to_csv(index=False).encode("utf-8"), "shopping_list.csv")

st.caption("Creato con Streamlit · MVP")

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
    """Crea una settimana vuota a partire dal lunedì indicato."""
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
    """Ricette demo per iniziare."""
    return [
        {
            "id": 1,
            "name": "Spaghetti alla Carbonara",
            "category": "Italiana",
            "time": 25,
            "servings": 2,
            "description": "Pasta con uova, guanciale/pancetta e parmigiano.",
            "image": "https://images.unsplash.com/photo-1523986371872-9d3ba2e2f642?w=1200",
            "ingredients": [
                {"name": "Spaghetti", "qty": 200, "unit": "g"},
                {"name": "Uova", "qty": 2, "unit": "pcs"},
                {"name": "Pancetta", "qty": 100, "unit": "g"},
                {"name": "Parmigiano", "qty": 50, "unit": "g"},
                {"name": "Pepe nero", "qty": 1, "unit": "tsp"},
            ],
            "instructions": "Cuoci la pasta. Rosola la pancetta. Mescola uova+formaggio, unisci fuori dal fuoco, pepe.",
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
            "instructions": "Salta le verdure, aggiungi salsa di soia e zenzero.",
        },
        {
            "id": 3,
            "name": "Salmone alla griglia con verdure",
            "category": "Mediterranea",
            "time": 30,
            "servings": 2,
            "description": "Verdure al forno con filetto di salmone alla griglia.",
            "image": "https://images.unsplash.com/photo-1516683037151-9d97f1d7d33d?w=1200",
            "ingredients": [
                {"name": "Filetto di salmone", "qty": 2, "unit": "pcs"},
                {"name": "Zucchine", "qty": 200, "unit": "g"},
                {"name": "Pomodorini", "qty": 200, "unit": "g"},
                {"name": "Olio d'oliva", "qty": 2, "unit": "tbsp"},
            ],
            "instructions": "Griglia il salmone, forno per le verdure condite con olio.",
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
# Persistenza dati (JSON locale)
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
        st.success("Dati salvati su file locale.")
    if col_b.button("📂 Carica"):
        load_from_file()
    st.caption("Suggerimento: usa Salva/Carica per mantenere i tuoi dati.")

st.markdown(
    """
    <style>
    .meal-col{background:#fff7f0;border-radius:16px;padding:10px;border:1px solid #ffa65433;margin-bottom:6px}
    .ghost{border:1px dashed #bbb;border-radius:12px;padding:10px;text-align:center;color:#777;margin-bottom:6px}
    .recipe-img{border-radius:12px;width:100%;height:200px;object-fit:cover}
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# PIANIFICATORE SETTIMANALE
# -----------------------------
if page == "Pianificatore settimanale":
    st.header("Pianificatore settimanale")

    # layout: freccia sinistra | 7 giorni | freccia destra
    nav_cols = st.columns([0.4, 1, 1, 1, 1, 1, 1, 1, 0.4])
    with nav_cols[0]:
        if st.button("◀︎", use_container_width=True, key="prev_week"):
            st.session_state.week_start -= timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)
    with nav_cols[-1]:
        if st.button("▶︎", use_container_width=True, key="next_week"):
            st.session_state.week_start += timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)

    st.caption(
        f"Settimana: {st.session_state.week_start.strftime('%d/%m/%Y')} - "
        f"{(st.session_state.week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"
    )
    if st.button("🧹 Svuota settimana"):
        st.session_state.planner = _empty_week(st.session_state.week_start)

    day_cols = nav_cols[1:-1]
    for i, c in enumerate(day_cols):
        day_date = st.session_state.week_start + timedelta(days=i)
        with c:
            st.markdown(f"### {DAYS_LABELS[i]}\n**{day_date.day}**")
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]
                st.markdown(f"<div class='meal-col'><b>{meal}</b></div>", unsafe_allow_html=True)
                r_opts = ["-"] + list(_get_recipe_options().keys())
                if slot["recipe_id"] is None:
                    sel_index = 0
                else:
                    rec_name = _find_recipe(slot["recipe_id"])["name"]
                    sel_index = r_opts.index(rec_name) if rec_name in r_opts else 0
                selected = st.selectbox(
                    " ", r_opts, index=sel_index, key=f"sel_{i}_{meal}", label_visibility="collapsed"
                )
                if selected != "-":
                    slot["recipe_id"] = _get_recipe_options()[selected]
                    rec = _find_recipe(slot["recipe_id"]) or {}
                    st.caption(f"⏱ {rec.get('time', '-') } min  ·  Porzioni:")
                    slot["servings"] = st.number_input(
                        "Porzioni", min_value=1, max_value=12, value=slot["servings"], key=f"serv_{i}_{meal}"
                    )
                    if st.button("✖ Rimuovi", key=f"rm_{i}_{meal}"):
                        slot["recipe_id"], slot["servings"] = None, 2
                else:
                    slot["recipe_id"] = None
                    st.markdown("<div class='ghost'>+ Aggiungi ricetta</div>", unsafe_allow_html=True)

# -----------------------------
# RICETTE
# -----------------------------
elif page == "Ricette":
    st.header("Ricettario")

    grid = st.columns(3)
    recipes = st.session_state.recipes

    def card(r, container):
        with container:
            if r.get("image"):
                st.image(r["image"], use_container_width=True)
            else:
                st.image("https://via.placeholder.com/400x200.png?text=Nessuna+immagine", use_container_width=True)
            st.subheader(r["name"])
            st.caption(f"{r['category']} · {r['time']} min · per {r['servings']} persone")
            st.write(r.get("description", ""))
            cols = st.columns(2)
            if cols[0].button("Modifica", key=f"edit_{r['id']}"):
                st.session_state["edit_id"] = r["id"]
            if cols[1].button("Elimina", key=f"del_{r['id']}"):
                st.session_state.recipes = [x for x in recipes if x["id"] != r["id"]]
                st.experimental_rerun()

    for idx, r in enumerate(recipes):
        card(r, grid[idx % 3])

    st.divider()
    st.subheader("Nuova ricetta")

    with st.form("new_recipe"):
        name = st.text_input("Nome ricetta *")
        category = st.text_input("Categoria", value="")
        time_m = st.number_input("Tempo (minuti)", 0, 240, 30)
        servings = st.number_input("Porzioni", 1, 20, 2)
        description = st.text_area("Descrizione", value="")
        image = st.text_input("URL immagine", value="")

        st.markdown("**Ingredienti**")
        ing_df = st.data_editor(
            pd.DataFrame([{"Ingredient": "", "Qty": 0, "Unit": UNITS[0]}]),
            num_rows="dynamic",
            use_container_width=True,
            key="new_ing_table",
        )

        instructions = st.text_area("Istruzioni")
        submitted = st.form_submit_button("Aggiungi ricetta")

        if submitted:
            ings = []
            for _, row in ing_df.iterrows():
                if str(row["Ingredient"]).strip():
                    ings.append({"name": row["Ingredient"], "qty": float(row["Qty"]), "unit": row["Unit"]})
            if not name or not ings:
                st.error("Nome e almeno 1 ingrediente sono obbligatori.")
            else:
                next_id = max([r["id"] for r in st.session_state.recipes] + [0]) + 1
                st.session_state.recipes.append({
                    "id": next_id,
                    "name": name,
                    "category": category,
                    "time": int(time_m),
                    "servings": int(servings),
                    "description": description,
                    "image": image,
                    "ingredients": ings,
                    "instructions": instructions,
                })
                st.success(f"Aggiunta {name}!")
                st.experimental_rerun()

    edit_id = st.session_state.get("edit_id")
    if edit_id is not None:
        r = _find_recipe(edit_id)
        st.divider()
        st.subheader(f"Modifica: {r['name']}")
        with st.form("edit_form"):
            name = st.text_input("Nome ricetta *", value=r["name"])
            category = st.text_input("Categoria", value=r.get("category", ""))
            time_m = st.number_input("Tempo (minuti)", 0, 240, r.get("time", 30))
            servings = st.number_input("Porzioni", 1, 20, r.get("servings", 2))
            description = st.text_area("Descrizione", value=r.get("description", ""))
            image = st.text_input("URL immagine", value=r.get("image", ""))
            st.markdown("**Ingredienti**")
            ing_df = pd.DataFrame(
                [{"Ingredient": i["name"], "Qty": i["qty"], "Unit": i["unit"]} for i in r.get("ingredients", [])]
            )
            ing_df = st.data_editor(
                ing_df if not ing_df.empty else pd.DataFrame([{"Ingredient": "", "Qty": 0, "Unit": UNITS[0]}]),
                num_rows="dynamic", use_container_width=True, key="edit_ing_table"
            )
            instructions = st.text_area("Istruzioni", value=r.get("instructions", ""))
            c1, c2 = st.columns(2)
            if c1.form_submit_button("Salva"):
                new_ings = []
                for _, row in ing_df.iterrows():
                    if str(row["Ingredient"]).strip():
                        new_ings.append({"name": row["Ingredient"], "qty": float(row["Qty"]), "unit": row["Unit"]})
                r.update({
                    "name": name,
                    "category": category,
                    "time": int(time_m),
                    "servings": int(servings),
                    "description": description,
                    "image": image,
                    "ingredients": new_ings,
                    "instructions": instructions,
                })
                st.session_state["edit_id"] = None
                st.success("Ricetta aggiornata.")
                st.experimental_rerun()
            if c2.form_submit_button("Annulla"):
                st.session_state["edit_id"] = None
                st.experimental_rerun()

# -----------------------------
# LISTA DELLA SPESA
# -----------------------------
else:
    st.header("Lista della spesa")

    def aggregate_shopping_list():
        to_base = {
            "g": ("g", 1), "kg": ("g", 1000),
            "ml": ("ml", 1), "l": ("ml", 1000),
            "pcs": ("pcs", 1), "tbsp": ("tbsp", 1), "tsp": ("tsp", 1)
        }
        agg_base: dict[tuple[str, str], float] = {}
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

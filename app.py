import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import date, timedelta
import json

# -----------------------------
# Helpers
# -----------------------------
APP_TITLE = "MealPlanner"
DAYS_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MEALS = ["LUNCH", "DINNER"]
UNITS = ["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]
DATA_FILE = "mealplanner_data.json"


def _init_state():
    if "recipes" not in st.session_state:
        st.session_state.recipes = _demo_recipes()
    if "planner" not in st.session_state:
        st.session_state.planner = _empty_week()
    if "week_start" not in st.session_state:
        # set to the last Monday
        today = date.today()
        st.session_state.week_start = today - timedelta(days=today.weekday())


def _empty_week(start: date | None = None):
    if start is None:
        today = date.today()
        start = today - timedelta(days=today.weekday())
    week = {
        "start": str(start),
        "days": []
    }
    for i in range(7):
        day_date = start + timedelta(days=i)
        week["days"].append({
            "date": str(day_date),
            "LUNCH": {"recipe_id": None, "servings": 2},
            "DINNER": {"recipe_id": None, "servings": 2},
        })
    return week


def _demo_recipes():
    return [
        {
            "id": 1,
            "name": "Spaghetti Carbonara",
            "category": "Italian",
            "time": 25,
            "servings": 2,
            "description": "Classic pasta with eggs, pancetta, and parmesan.",
            "image": "https://images.unsplash.com/photo-1523986371872-9d3ba2e2f642?w=1200",
            "ingredients": [
                {"name": "Spaghetti", "qty": 200, "unit": "g"},
                {"name": "Eggs", "qty": 2, "unit": "pcs"},
                {"name": "Pancetta", "qty": 100, "unit": "g"},
                {"name": "Parmesan cheese", "qty": 50, "unit": "g"},
                {"name": "Black pepper", "qty": 1, "unit": "tsp"},
            ],
            "instructions": "Boil pasta. Fry pancetta. Mix eggs + cheese, combine off heat, season.",
        },
        {
            "id": 2,
            "name": "Vegetable Stir Fry",
            "category": "Vegetarian",
            "time": 20,
            "servings": 2,
            "description": "Mixed veggies stir fried with soy sauce.",
            "image": "https://images.unsplash.com/photo-1505575972945-280be642cfac?w=1200",
            "ingredients": [
                {"name": "Broccoli", "qty": 200, "unit": "g"},
                {"name": "Carrots", "qty": 2, "unit": "pcs"},
                {"name": "Bell peppers", "qty": 2, "unit": "pcs"},
                {"name": "Soy sauce", "qty": 3, "unit": "tbsp"},
                {"name": "Ginger", "qty": 10, "unit": "g"},
            ],
            "instructions": "Stir fry vegetables, add soy sauce and ginger.",
        },
        {
            "id": 3,
            "name": "Grilled Salmon with Veggies",
            "category": "Mediterranean",
            "time": 30,
            "servings": 2,
            "description": "Roasted vegetables with grilled salmon.",
            "image": "https://images.unsplash.com/photo-1516683037151-9d97f1d7d33d?w=1200",
            "ingredients": [
                {"name": "Salmon fillet", "qty": 2, "unit": "pcs"},
                {"name": "Zucchini", "qty": 200, "unit": "g"},
                {"name": "Cherry tomatoes", "qty": 200, "unit": "g"},
                {"name": "Olive oil", "qty": 2, "unit": "tbsp"},
            ],
            "instructions": "Grill salmon, roast veggies with olive oil.",
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
# Data persistence (local JSON)
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
    page = st.radio("Navigate", ["Weekly Planner", "Recipes", "Shopping List"], index=0)
    st.divider()
    col_a, col_b = st.columns(2)
    if col_a.button("💾 Salva"):
        save_to_file()
        st.success("Dati salvati su file locale.")
    if col_b.button("📂 Carica"):
        load_from_file()
    st.caption("Suggerimento: usa Salva/Carica per mantenere i tuoi dati.")

# Small CSS polish (mobile-friendly paddings & cards)
st.markdown(
    """
    <style>
    .meal-card{border:1px solid #eee;border-radius:12px;padding:12px;background:#fff}
    .meal-col{background:#fff7f0;border-radius:16px;padding:10px;border:1px solid #ffa65433}
    .ghost{border:1px dashed #bbb;border-radius:12px;padding:10px;text-align:center;color:#777}
    .recipe-img{border-radius:12px;width:100%;height:200px;object-fit:cover}
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# WEEKLY PLANNER
# -----------------------------
if page == "Weekly Planner":
    st.header("Weekly Planner")
    cols = st.columns([1, 1, 1, 1, 1, 1, 1, 0.8])

    # Week navigation
    with cols[-1]:
        if st.button("◀︎", use_container_width=True):
            st.session_state.week_start -= timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)
        if st.button("▶︎", use_container_width=True):
            st.session_state.week_start += timedelta(days=7)
            st.session_state.planner = _empty_week(st.session_state.week_start)

    st.caption(f"Week: {st.session_state.week_start.strftime('%b %d, %Y')} - "
               f"{(st.session_state.week_start + timedelta(days=6)).strftime('%b %d, %Y')}")

    # Day columns
    day_cols = st.columns(7)
    for i, c in enumerate(day_cols):
        day_date = st.session_state.week_start + timedelta(days=i)
        with c:
            st.markdown(f"### {DAYS_LABELS[i]}\n**{day_date.day}**")
            for meal in MEALS:
                slot = st.session_state.planner["days"][i][meal]
                st.markdown(f"<div class='meal-col'><b>{meal}</b></div>", unsafe_allow_html=True)
                r_opts = ["-"] + list(_get_recipe_options().keys())
                selected = st.selectbox(
                    " ", r_opts, index=0 if slot["recipe_id"] is None else r_opts.index(_find_recipe(slot["recipe_id"])['name']),
                    key=f"sel_{i}_{meal}", label_visibility="collapsed")
                if selected != "-":
                    slot["recipe_id"] = _get_recipe_options()[selected]
                    rec = _find_recipe(slot["recipe_id"]) or {}
                    st.caption(f"⏱ {rec.get('time', '-') } min  ·  Servings:")
                    slot["servings"] = st.number_input("Servings", min_value=1, max_value=12, value=slot["servings"], key=f"serv_{i}_{meal}")
                    if st.button("✖ Remove", key=f"rm_{i}_{meal}"):
                        slot["recipe_id"], slot["servings"] = None, 2
                else:
                    slot["recipe_id"] = None
                    st.markdown("<div class='ghost'>+ Add Recipe</div>", unsafe_allow_html=True)

# -----------------------------
# RECIPES (CRUD)
# -----------------------------
elif page == "Recipes":
    st.header("Recipe Library")

    # Grid of recipe cards
    grid = st.columns(3)
    recipes = st.session_state.recipes

    def card(r, container):
        with container:
            if r.get("image"):
                st.image(r["image"], use_column_width=True)
            st.subheader(r["name"])
            st.caption(f"{r['category']} · {r['time']} min · serves {r['servings']}")
            st.write(r.get("description", ""))
            cols = st.columns(2)
            if cols[0].button("Edit", key=f"edit_{r['id']}"):
                st.session_state["edit_id"] = r["id"]
            if cols[1].button("Delete", key=f"del_{r['id']}"):
                st.session_state.recipes = [x for x in recipes if x["id"] != r["id"]]
                st.experimental_rerun()

    for idx, r in enumerate(recipes):
        card(r, grid[idx % 3])

    st.divider()
    st.subheader("New Recipe")
    with st.form("new_recipe"):
        name = st.text_input("Recipe Name *")
        category = st.text_input("Category", value="")
        time_m = st.number_input("Cooking Time (minutes)", 0, 240, 30)
        servings = st.number_input("Servings", 1, 20, 2)
        description = st.text_area("Description", value="")
        image = st.text_input("Image URL", value="")
        st.markdown("**Ingredients**")
        ing_df = st.data_editor(
            pd.DataFrame([{"Ingredient": "", "Qty": 0, "Unit": UNITS[0]}]),
            num_rows="dynamic",
            use_container_width=True,
            key="new_ing_table",
        )
        instructions = st.text_area("Instructions")
        submitted = st.form_submit_button("Add Recipe")
        if submitted:
            ings = []
            for _, row in ing_df.iterrows():
                if str(row["Ingredient"]).strip():
                    ings.append({"name": row["Ingredient"], "qty": float(row["Qty"]), "unit": row["Unit"]})
            if not name or not ings:
                st.error("Name and at least 1 ingredient are required.")
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
                st.success(f"Added {name}!")
                st.experimental_rerun()

    # Edit modal
    edit_id = st.session_state.get("edit_id")
    if edit_id is not None:
        r = _find_recipe(edit_id)
        st.divider()
        st.subheader(f"Edit: {r['name']}")
        with st.form("edit_form"):
            name = st.text_input("Recipe Name *", value=r["name"])
            category = st.text_input("Category", value=r.get("category", ""))
            time_m = st.number_input("Cooking Time (minutes)", 0, 240, r.get("time", 30))
            servings = st.number_input("Servings", 1, 20, r.get("servings", 2))
            description = st.text_area("Description", value=r.get("description", ""))
            image = st.text_input("Image URL", value=r.get("image", ""))
            st.markdown("**Ingredients**")
            ing_df = pd.DataFrame(
                [{"Ingredient": i["name"], "Qty": i["qty"], "Unit": i["unit"]} for i in r.get("ingredients", [])]
            )
            ing_df = st.data_editor(ing_df if not ing_df.empty else pd.DataFrame([{"Ingredient": "", "Qty": 0, "Unit": UNITS[0]}]),
                                    num_rows="dynamic", use_container_width=True, key="edit_ing_table")
            instructions = st.text_area("Instructions", value=r.get("instructions", ""))
            c1, c2 = st.columns(2)
            if c1.form_submit_button("Save"):
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
                st.success("Recipe updated.")
                st.experimental_rerun()
            if c2.form_submit_button("Cancel"):
                st.session_state["edit_id"] = None
                st.experimental_rerun()

# -----------------------------
# SHOPPING LIST
# -----------------------------
else:
    st.header("Shopping List")

    def aggregate_shopping_list():
        agg: dict[tuple[str, str], float] = {}
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
                    key = (ing["name"].strip().title(), ing["unit"])  # same unit aggregation
                    agg[key] = agg.get(key, 0) + ing["qty"] * scale
        # Present as DataFrame sorted by ingredient
        rows = [
            {"Ingredient": k[0], "Qty": round(v, 2), "Unit": k[1]} for k, v in sorted(agg.items(), key=lambda x: x[0][0])
        ]
        return pd.DataFrame(rows)

    df = aggregate_shopping_list()
    st.subheader(f"Ingredients · {len(df)} items")
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Export Excel
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="ShoppingList")
    st.download_button("Export List (Excel)", data=buffer.getvalue(), file_name="shopping_list.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.caption("Made with Streamlit · MVP")

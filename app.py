from flask import Flask, render_template, request, redirect, session, url_for, make_response, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
from datetime import date
from io import BytesIO
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from openai import OpenAI

app = Flask(__name__, template_folder='Templates')
app.secret_key = "change-this-to-any-random-secret"

ADMIN_INITIAL_BUDGET = 1_000_000

# 1) DB + OpenAI config
# On Render you will set DATABASE_URL to the *internal* Render Postgres URL.
DATABASE_URL = os.getenv("DATABASE_URL")

# Local fallback (only used when DATABASE_URL is NOT set, e.g. on your laptop)
if not DATABASE_URL:
    DB_HOST = os.getenv("PGHOST", "localhost")
    DB_USER = os.getenv("PGUSER", "postgres")
    DB_PASS = os.getenv("PGPASSWORD", "")
    DB_NAME = os.getenv("PGDATABASE", "grandguard_db")
    DB_PORT = int(os.getenv("PGPORT", "5432"))

# OpenAI config (used by AI checks)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None
    print("WARNING: OPENAI_API_KEY is not set. AI checks will be skipped.")

# 2) get_db() ✅ uses DATABASE_URL when present
def get_db():
    try:
        if DATABASE_URL:
            conn = psycopg2.connect(DATABASE_URL)
        else:
            conn = psycopg2.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASS,
                database=DB_NAME,
                port=DB_PORT,
            )
        return conn
    except Exception as e:
        print(f"DB connect error: {e}")
        return None

def run_award_ai_check(award_row: dict) -> dict:
    """
    Call OpenAI to check whether this award complies with
    university, sponsor, and federal policies.

    Returns a dict like:
    {"decision": "approve" or "decline" or "skip", "reason": "..."}
    """
    # If no API key / client, just skip
    if client is None:
        return {
            "decision": "skip",
            "reason": "AI check skipped (no API key configured)."
        }

    # Join all three policies into one text block
    policies_text = f"""
UNIVERSITY POLICY
-----------------
{POLICIES.get("university", "")}

SPONSOR POLICY
--------------
{POLICIES.get("sponsor", "")}

FEDERAL POLICY
--------------
{POLICIES.get("federal", "")}
"""

    # Short summary of the grant for the model
    grant_summary = f"""
Title: {award_row.get('title')}
Sponsor type: {award_row.get('sponsor_type')}
Amount: {award_row.get('amount')}
Department: {award_row.get('department')}
College: {award_row.get('college')}
Contact email: {award_row.get('contact_email')}
Abstract:
{award_row.get('abstract')}

Keywords: {award_row.get('keywords')}
Collaborators: {award_row.get('collaborators')}
"""

    # 🔹 THIS IS THE PROMPT YOU ASKED ABOUT
    prompt = f"""
You are an AI compliance assistant for university research grants.

Your job is to decide if the grant below clearly follows ALL THREE policy documents
(University, Sponsor, and Federal).

Be very strict:

- Only return "approve" if the grant is clearly compliant AND you see no possible conflicts.
- If there is missing information, uncertainty, or any possible conflict, you MUST return "decline".

Respond strictly in valid JSON:
{{
  "decision": "approve" or "decline",
  "reason": "short explanation of why you approved or declined, referencing specific policies if possible"
}}

=== POLICIES ===
{policies_text}

=== GRANT PROPOSAL ===
{grant_summary}
"""

    try:
        # Call OpenAI (chat completions style)
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",   # or any other model you prefer
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )

        raw_content = resp.choices[0].message.content.strip()

        # Try to parse JSON from the model output
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            # If it wrapped JSON in text, try to extract the JSON part
            start = raw_content.find("{")
            end = raw_content.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(raw_content[start:end+1])
            else:
                # Fall back: treat as decline
                return {
                    "decision": "decline",
                    "reason": f"Model returned non-JSON response: {raw_content}"
                }

        decision = data.get("decision", "").lower()
        reason = data.get("reason", "")

        if decision not in ("approve", "decline"):
            # Safety fallback
            return {
                "decision": "decline",
                "reason": f"Invalid decision from model: {decision!r}. Raw: {raw_content}"
            }

        return {
            "decision": decision,
            "reason": reason or "No reason provided by model."
        }

    except Exception as e:
        # If API call fails, treat as decline so nothing unsafe is auto-approved
        return {
            "decision": "decline",
            "reason": f"AI check failed with error: {e}"
        }
def init_db_if_needed():
    """Initialize database schema if tables don't exist."""
    conn = get_db()
    if conn is None:
        print("Warning: Could not connect to database. Schema initialization skipped.")
        return

    try:
        cur = conn.cursor()
        schema_file = os.path.join(os.path.dirname(__file__), "schema_postgresql.sql")
        if os.path.exists(schema_file):
            with open(schema_file, 'r') as f:
                schema_sql = f.read()
            cur.execute(schema_sql)
            conn.commit()
            print("✓ Database schema initialized")
        else:
            print(f"Warning: Schema file {schema_file} not found")
        cur.close()
    except Exception as e:
        print(f"DB init error: {e}")
        conn.rollback()
    finally:
        conn.close()

# -------- Policy files --------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POLICY_DIR = os.path.join(BASE_DIR, "policies")


def _read_policy(filename: str) -> str:
    """Read a policy text file from the policies/ folder."""
    path = os.path.join(POLICY_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # For now just return empty text if missing
        return ""

POLICIES = {
    "university": _read_policy("university_policy.txt"),
    "sponsor": _read_policy("sponsor_policy.txt"),
    "federal": _read_policy("federal_policy.txt"),
}
def _build_award_summary(award: dict) -> str:
    """Turn an award row into a compact text summary for the AI."""
    parts = [
        f"Title: {award.get('title', '')}",
        f"Abstract: {award.get('abstract', '')}",
        f"Sponsor type: {award.get('sponsor_type', '')}",
        f"Department: {award.get('department', '')}",
        f"College: {award.get('college', '')}",
        f"Total amount: {award.get('amount', '')}",
        f"Start date: {award.get('start_date', '')}",
        f"End date: {award.get('end_date', '')}",
        f"Keywords: {award.get('keywords', '')}",
        f"Collaborators: {award.get('collaborators', '')}",
    ]
    return "\n".join(parts)


def _run_ai_policy_check(award: dict):
    """
    Call OpenAI to check if the grant follows
    university + sponsor + federal policies.

    Returns:
      ("pass" | "fail" | "unknown", reason_text)
    """
    if client is None:
        msg = "AI check skipped (no OPENAI_API_KEY configured)."
        print(msg)
        return "unknown", msg

    uni_policy = POLICIES.get("university", "")
    sponsor_policy = POLICIES.get("sponsor", "")
    federal_policy = POLICIES.get("federal", "")

    grant_text = _build_award_summary(award)

    prompt = f"""
You are an assistant checking if a research grant follows three policy sets.

UNIVERSITY POLICY:
{uni_policy}

SPONSOR POLICY:
{sponsor_policy}

FEDERAL POLICY:
{federal_policy}

GRANT APPLICATION TO REVIEW:
{grant_text}

TASK:
1. Decide if the grant clearly follows all three policy sets.
2. Reply in this exact format:

DECISION: APPROVE or REJECT
REASON: short explanation (2-4 sentences)
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict compliance checker for research grant policies."
                },
                {
                    "role": "user",
                    "content": prompt.strip()
                },
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content or ""
        print("AI raw response:\n", content)          # DEBUG: show full reply
    except Exception as e:
        msg = f"AI error: {e}"
        print(msg)                                     # DEBUG: show error
        return "unknown", msg

    first_line = content.splitlines()[0].strip().upper()
    print("AI first line for decision:", first_line)   # DEBUG: show parsed line

    if "REJECT" in first_line or "DECLINE" in first_line:
        return "fail", content
    elif "APPROVE" in first_line:
        return "pass", content
    else:
        # Could not confidently parse decision
        return "unknown", content

@app.route("/")
def home():
    return render_template("index.html")


# ========== Auth ==========

@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not email or not password:
        return make_response("Missing credentials", 400)

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT name, role FROM users WHERE email=%s AND password=%s",
            (email, password),
        )
        user = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Query error: {e}")
        return make_response("DB query failed", 500)

    if not user:
        return make_response("Invalid email or password", 401)

    session["user"] = {"name": user["name"], "role": user["role"], "email": email}
    return "Welcome"


@app.route("/signup", methods=["POST"])
def signup():
    first = request.form.get("firstName", "").strip()
    last = request.form.get("lastName", "").strip()
    email = request.form.get("email", "").strip() or request.form.get("signupEmail", "").strip()
    password = request.form.get("password", "").strip() or request.form.get("signupPassword", "").strip()

    if not first or not last or not email or not password:
        return make_response("Missing required fields", 400)

    full_name = f"{first} {last}".strip()

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor()
        # Everyone who signs up is a PI by default
        cur.execute(
            """
            INSERT INTO users (name, email, role, password)
            VALUES (%s, %s, 'PI', %s)
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
            """,
            (full_name, email, password),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB insert user error: {e}")
        conn.rollback()
        return make_response(f"DB insert failed: {e}", 500)
    finally:
        conn.close()

    session["user"] = {"name": full_name, "role": "PI", "email": email}
    return "Signed up"


# ========== Dashboard (PI + Admin) ==========

@app.route("/dashboard")
def dashboard():
    """
    - If PI: show their own awards (PI dashboard).
    - If Admin: show all awards + budget info (Admin dashboard).
    """
    u = session.get("user")
    if not u:
        return render_template("dashboard.html", name="User", role="Guest", awards=[])

    # ---------- Admin dashboard ----------
    if u["role"] == "Admin":
        awards = []
        ready_awards = []  # AI Passed
        total_approved = 0.0
        conn = get_db()
        if conn is not None:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                # All awards, from all PIs
                cur.execute(
                    """
                    SELECT award_id, title, created_by_email, sponsor_type,
                           amount, start_date, end_date, status, created_at,
                           ai_review_notes
                    FROM awards
                    ORDER BY created_at DESC
                    """
                )
                awards = cur.fetchall()

                # Subset that has passed AI
                ready_awards = [
                    a for a in awards
                    if (a.get("status") or "").lower() == "ai passed"
                ]

                # Sum of approved amounts
                cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total_approved
                FROM awards
                WHERE status = 'Approved'
                """
            )
                row = cur.fetchone()
                total_approved = 0.0
                if row and row.get("total_approved") is not None:
                    total_approved = float(row["total_approved"])
                cur.close()
            except Exception as e:
                print(f"DB fetch awards (admin) error: {e}")
            finally:
                conn.close()

        budget_initial = float(ADMIN_INITIAL_BUDGET)
        budget_remaining = budget_initial - total_approved

        return render_template(
            "dashboard_admin.html",
            name=u["name"],
            role=u["role"],
            awards=awards,
            ready_awards=ready_awards,   # ✅ new
            budget_initial=budget_initial,
            budget_remaining=budget_remaining,
        )

    # ---------- PI dashboard ----------
    awards = []
    conn = get_db()
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT award_id, title, sponsor_type, amount,
                       start_date, end_date, status, created_at
                FROM awards
                WHERE created_by_email=%s
                ORDER BY created_at DESC
                """,
                (u["email"],),
            )
            awards = cur.fetchall()
            cur.close()
        except Exception as e:
            print(f"DB fetch awards error: {e}")
        finally:
            conn.close()

    return render_template("dashboard.html", name=u["name"], role=u["role"], awards=awards)


# ========== Grants / Awards (PI side) ==========

@app.route("/awards/new")
def awards_new():
    """Show empty 'Generate Grants' form (PI only)."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    if u["role"] != "PI":
        # Admins shouldn't create grants
        return redirect(url_for("dashboard"))
    return render_template("awards_new.html", award=None)

def _get_award_for_export(award_id, user):
    """
    Load a single award plus JSON budget fields and return:
    award, personnel, domestic_travel, international_travel, materials
    """
    conn = get_db()
    if conn is None:
        return None, [], [], [], []

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Admin can see all, PI only their own
        if user["role"] == "Admin":
            cur.execute("SELECT * FROM awards WHERE award_id=%s", (award_id,))
        else:
            cur.execute(
                "SELECT * FROM awards WHERE award_id=%s AND created_by_email=%s",
                (award_id, user["email"]),
            )

        award = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"_get_award_for_export query error: {e}")
        return None, [], [], [], []

    if not award:
        return None, [], [], [], []

    # Parse JSON blobs exactly like award_view
    def parse_json(field_name):
        raw = award.get(field_name)
        if raw is None:
            return []
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return []

    personnel = parse_json("personnel_json")
    domestic_travel = parse_json("domestic_travel_json")
    international_travel = parse_json("international_travel_json")
    materials = parse_json("materials_json")

    return award, personnel, domestic_travel, international_travel, materials

def _parse_json_field(field_value):
    """Helper: safely parse a JSON array field from the form."""
    if not field_value:
        return []
    try:
        data = json.loads(field_value)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        print("JSON decode error for field:", field_value[:200])
        return []


@app.route("/awards", methods=["POST"])
def awards_create():
    """Create a new award (PI submits; status defaults to 'Pending')."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    if u["role"] != "PI":
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    sponsor = None  # reserved
    sponsor_type = request.form.get("sponsor_type", "").strip()
    department = request.form.get("department", "").strip()
    college = request.form.get("college", "").strip()
    contact_email = request.form.get("contact_email", "").strip()
    amount = request.form.get("amount", "").strip()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    abstract = request.form.get("abstract", "").strip()
    keywords = request.form.get("keywords", "").strip()
    collaborators = request.form.get("collaborators", "").strip()

    # JSON strings for detailed budget sections
    personnel_json_str = request.form.get("personnel_json", "")
    domestic_travel_json_str = request.form.get("domestic_travel_json", "")
    international_travel_json_str = request.form.get("international_travel_json", "")
    materials_json_str = request.form.get("materials_json", "")

    # Parse into Python lists (from the JS format)
    pers_list = _parse_json_field(personnel_json_str)
    dom_list = _parse_json_field(domestic_travel_json_str)
    intl_list = _parse_json_field(international_travel_json_str)
    mat_list = _parse_json_field(materials_json_str)

    if not title or not sponsor_type or not amount or not start_date or not end_date:
        return make_response("Missing required fields", 400)

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor()

        # Insert into awards and get award_id
        cur.execute(
            """
            INSERT INTO awards (
              created_by_email, title, sponsor, sponsor_type,
              department, college, contact_email,
              amount, start_date, end_date,
              abstract, keywords, collaborators,
              personnel_json, domestic_travel_json,
              international_travel_json, materials_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
            RETURNING award_id
            """,
            (
                u["email"], title, sponsor, sponsor_type or None,
                department or None, college or None, contact_email or None,
                amount, start_date, end_date,
                abstract or None, keywords or None, collaborators or None,
                json.dumps(pers_list),
                json.dumps(dom_list),
                json.dumps(intl_list),
                json.dumps(mat_list),
            ),
        )
        award_id = cur.fetchone()[0]

        # ---- Insert personnel rows (summary into personnel_expenses) ----
        for p in pers_list:
            name_val = (p.get("name") or "").strip()
            if not name_val:
                continue
            position = (p.get("position") or "").strip()
            same_each_year = bool(p.get("same_each_year", False))

            # p["hours"] is an array of {year, hours}
            hours_for_years = None
            hrs = p.get("hours")
            if isinstance(hrs, list) and hrs:
                total = 0.0
                for h in hrs:
                    try:
                        total += float(h.get("hours", 0) or 0)
                    except Exception:
                        pass
                if total > 0:
                    hours_for_years = total

            cur.execute(
                """
                INSERT INTO personnel_expenses
                  (award_id, person_name, position_title, hours_for_years, same_each_year)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (award_id, name_val, position or None, hours_for_years, same_each_year),
            )

        # Helper for numeric conversion
        def num(v):
            try:
                return float(v) if v not in (None, "", "null") else None
            except (TypeError, ValueError):
                return None

        # ---- Insert domestic travel rows ----
        for t in dom_list:
            travel_name = (t.get("travel_name") or t.get("name") or "").strip()
            if not travel_name:
                continue
            desc = (t.get("description") or "").strip()
            year = t.get("year")
            start = t.get("start_date") or t.get("depart") or None
            end = t.get("end_date") or t.get("arrive") or None

            flight = num(t.get("flight_cost") or t.get("flight"))
            taxi = num(t.get("taxi_per_day"))
            food = num(t.get("food_lodge_per_day") or t.get("food_per_day"))
            days = None
            try:
                dval = t.get("days")
                days = int(dval) if dval not in (None, "", "null") else None
            except (TypeError, ValueError):
                days = None

            cur.execute(
                """
                INSERT INTO travel_expenses
                  (award_id, travel_type, travel_name, description, year,
                   start_date, end_date, flight_cost, taxi_per_day,
                   food_lodge_per_day, num_days)
                VALUES (%s, 'Domestic', %s, %s, %s,
                        %s, %s, %s, %s, %s, %s)
                """,
                (
                    award_id, travel_name, desc or None, year,
                    start, end, flight, taxi, food, days,
                ),
            )

        # ---- Insert international travel rows ----
        for t in intl_list:
            travel_name = (t.get("travel_name") or t.get("name") or "").strip()
            if not travel_name:
                continue
            desc = (t.get("description") or "").strip()
            year = t.get("year")
            start = t.get("start_date") or t.get("depart") or None
            end = t.get("end_date") or t.get("arrive") or None

            flight = num(t.get("flight_cost") or t.get("flight"))
            taxi = num(t.get("taxi_per_day"))
            food = num(t.get("food_lodge_per_day") or t.get("food_per_day"))
            days = None
            try:
                dval = t.get("days")
                days = int(dval) if dval not in (None, "", "null") else None
            except (TypeError, ValueError):
                days = None

            cur.execute(
                """
                INSERT INTO travel_expenses
                  (award_id, travel_type, travel_name, description, year,
                   start_date, end_date, flight_cost, taxi_per_day,
                   food_lodge_per_day, num_days)
                VALUES (%s, 'International', %s, %s, %s,
                        %s, %s, %s, %s, %s, %s)
                """,
                (
                    award_id, travel_name, desc or None, year,
                    start, end, flight, taxi, food, days,
                ),
            )

        # ---- Insert materials/supplies rows ----
        for m in mat_list:
            mtype = (m.get("material_type") or m.get("category") or "").strip()
            if not mtype:
                continue
            desc = (m.get("description") or "").strip()
            year = m.get("year")
            try:
                cost_val = float(m.get("cost")) if m.get("cost") not in (None, "", "null") else None
            except (TypeError, ValueError):
                cost_val = None

            cur.execute(
                """
                INSERT INTO material_supplies
                  (award_id, material_type, cost, description, year)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (award_id, mtype, cost_val, desc or None, year),
            )

        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB insert award error: {e}")
        conn.rollback()
        return make_response(f"DB insert failed: {e}", 500)
    finally:
        conn.close()

    return redirect(url_for("dashboard"))


@app.route("/awards/<int:award_id>/view")
def award_view(award_id):
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Admin can see all, PI only their own
        if u["role"] == "Admin":
            cur.execute("SELECT * FROM awards WHERE award_id=%s", (award_id,))
        else:
            cur.execute(
                "SELECT * FROM awards WHERE award_id=%s AND created_by_email=%s",
                (award_id, u["email"]),
            )

        award = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB fetch single award error: {e}")
        return make_response("DB query failed", 500)

    if not award:
        return "Award not found", 404

    # --- Parse JSON blobs safely ---
    def parse_json(field_name):
        raw = award.get(field_name)
        if raw is None:
            return []
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return []

    personnel = parse_json("personnel_json")
    domestic_travel = parse_json("domestic_travel_json")
    international_travel = parse_json("international_travel_json")
    materials = parse_json("materials_json")

    # --- Compute period & year list for tables ---
    start = award.get("start_date")
    end = award.get("end_date")

    years = []
    duration_years = None
    if isinstance(start, date) and isinstance(end, date) and end >= start:
        years = list(range(start.year, end.year + 1))
        duration_years = end.year - start.year + 1

    return render_template(
        "award_view.html",
        award=award,
        personnel=personnel,
        domestic_travel=domestic_travel,
        international_travel=international_travel,
        materials=materials,
        years=years,
        duration_years=duration_years,
    )


# ========== EXPORTS: Excel + PDF ==========

@app.route("/awards/<int:award_id>/download/pdf")
def download_award_pdf(award_id):
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))

    award, personnel, domestic_travel, international_travel, materials = _get_award_for_export(award_id, u)
    if not award:
        return "Award not found", 404

    # -------- helpers ----------
    def hours_text(hours_list):
        if not hours_list:
            return ""
        parts = []
        for h in hours_list:
            year = h.get("year")
            hrs = h.get("hours")
            if year and hrs not in (None, ""):
                parts.append(f"{year}: {hrs} hrs")
        return ", ".join(parts)

    def travel_row(travel_type, t):
        return [
            travel_type,
            t.get("year"),
            t.get("travel_name") or t.get("name") or "",
            t.get("description") or "",
            t.get("start_date") or t.get("depart") or "",
            t.get("end_date") or t.get("arrive") or "",
            t.get("flight_cost") or t.get("flight") or "",
            t.get("taxi_per_day") or "",
            t.get("food_lodge_per_day") or t.get("food_per_day") or "",
            t.get("days") or t.get("num_days") or "",
        ]

    # -------- basic fields ----------
    title = award.get("title") or "Grant"
    funding = award.get("sponsor_type") or "N/A"
    amount = float(award.get("amount") or 0)
    dept = award.get("department") or "N/A"
    college = award.get("college") or "N/A"
    email = award.get("contact_email") or award.get("created_by_email") or "N/A"
    status = award.get("status") or "Pending"
    start = award.get("start_date")
    end = award.get("end_date")
    abstract = award.get("abstract") or "N/A"
    keywords = award.get("keywords") or "N/A"
    collaborators = award.get("collaborators") or "N/A"

    if start and end:
        period_str = f"{start} \u2192 {end}"
    else:
        period_str = "N/A"

    # -------- build the PDF with tables ----------
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    title_style = styles["Title"]

    elements = []

    # Top title
    elements.append(Paragraph(title, title_style))
    elements.append(Spacer(1, 8))

    # Summary block – similar to the top of the HTML view
    summary_lines = [
        f"<b>Funding Agency:</b> {funding}",
        f"<b>Amount:</b> ${amount:,.2f}",
        f"<b>Period:</b> {period_str}",
        f"<b>Status:</b> {status}",
        f"<b>Department:</b> {dept}",
        f"<b>College:</b> {college}",
        f"<b>Contact Email:</b> {email}",
    ]
    for line in summary_lines:
        elements.append(Paragraph(line, normal))
    elements.append(Spacer(1, 10))

    # Abstract / keywords / collaborators
    elements.append(Paragraph("<b>Abstract:</b>", normal))
    elements.append(Paragraph(abstract, normal))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Keywords:</b> {keywords}", normal))
    elements.append(Paragraph(f"<b>Collaborators:</b> {collaborators}", normal))
    elements.append(Spacer(1, 12))

    # -------- Personnel table ----------
    if personnel:
        elements.append(Paragraph("Personnel", styles["Heading3"]))
        data = [["Name", "Position", "Hours for year(s)", "Same Each Year?"]]
        for p in personnel:
            data.append([
                p.get("name") or "",
                p.get("position") or "",
                hours_text(p.get("hours")),
                "Yes" if p.get("same_each_year") else "No",
            ])

        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E0E0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 12))

    # -------- Travel table (domestic + international) ----------
    if domestic_travel or international_travel:
        elements.append(Paragraph("Travel Information", styles["Heading3"]))
        data = [
            [
                "Type", "Year", "Name", "Description",
                "Departure", "Arrival", "Flight Cost",
                "Taxi/Day", "Food & Lodge/Day", "Days",
            ]
        ]
        for t_dom in domestic_travel:
            data.append(travel_row("Domestic", t_dom))
        for t_int in international_travel:
            data.append(travel_row("International", t_int))

        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E0E0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 12))

    # -------- Materials table ----------
    if materials:
        elements.append(Paragraph("Materials and Supplies", styles["Heading3"]))
        data = [["Category", "Year", "Description", "Cost"]]
        for m in materials:
            data.append([
                m.get("material_type") or m.get("category") or "",
                m.get("year") or "",
                m.get("description") or "",
                m.get("cost") or "",
            ])

        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E0E0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(t)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    filename = f"grant_{award.get('award_id', award_id)}.pdf"
    resp = make_response(buffer.getvalue())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename=\"{filename}\"'
    return resp

@app.route("/awards/<int:award_id>/download/excel")
def download_award_excel(award_id):
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))

    award, personnel, domestic_travel, international_travel, materials = _get_award_for_export(award_id, u)
    if not award:
        return "Award not found", 404

    wb = Workbook()
    ws = wb.active
    ws.title = "Grant Budget"

    # Styles
    header_fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")
    bold = Font(bold=True)
    center = Alignment(horizontal="center")
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Set some decent column widths
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 18
    ws.column_dimensions["G"].width = 12
    ws.column_dimensions["H"].width = 12
    ws.column_dimensions["I"].width = 16
    ws.column_dimensions["J"].width = 10

    row = 1

    def write_kv(label, value):
        nonlocal row
        ws.cell(row=row, column=1, value=label).font = bold
        ws.cell(row=row, column=2, value=value)
        row += 1

    title = award.get("title") or ""
    funding = award.get("sponsor_type") or ""
    amount = float(award.get("amount") or 0)
    dept = award.get("department") or ""
    college = award.get("college") or ""
    email = award.get("contact_email") or award.get("created_by_email") or ""
    status = award.get("status") or ""
    start = award.get("start_date")
    end = award.get("end_date")
    abstract = award.get("abstract") or ""
    keywords = award.get("keywords") or ""
    collaborators = award.get("collaborators") or ""

    write_kv("Title", title)
    write_kv("Funding Agency", funding)
    write_kv("Amount", amount)
    write_kv("Department", dept)
    write_kv("College", college)
    write_kv("Contact Email", email)
    write_kv("Start Date", start)
    write_kv("End Date", end)
    write_kv("Status", status)
    row += 1
    write_kv("Abstract", abstract)
    write_kv("Keywords", keywords)
    write_kv("Collaborators", collaborators)

    row += 2

    # Personnel section
    if personnel:
        ws.cell(row=row, column=1, value="Personnel").font = bold
        row += 1
        headers = ["Name", "Position", "Hours by Year", "Same Each Year?"]
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = bold
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        row += 1

        def hours_text(hours_list):
            if not hours_list:
                return ""
            parts = []
            for h in hours_list:
                year = h.get("year")
                hrs = h.get("hours")
                if year and hrs not in (None, ""):
                    parts.append(f"{year}: {hrs} hrs")
            return ", ".join(parts)

        for p in personnel:
            ws.cell(row=row, column=1, value=p.get("name") or "").border = border
            ws.cell(row=row, column=2, value=p.get("position") or "").border = border
            ws.cell(row=row, column=3, value=hours_text(p.get("hours"))).border = border
            ws.cell(
                row=row,
                column=4,
                value="Yes" if p.get("same_each_year") else "No",
            ).border = border
            row += 1
        row += 2

    # Travel section (domestic + international)
    if domestic_travel or international_travel:
        ws.cell(row=row, column=1, value="Travel").font = bold
        row += 1
        headers = [
            "Type", "Year", "Name", "Description",
            "Departure", "Arrival", "Flight Cost",
            "Taxi/Day", "Food & Lodge/Day", "Days"
        ]
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = bold
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        row += 1

        def add_travel_row(travel_type, t):
            nonlocal row
            cols = [
                travel_type,
                t.get("year"),
                t.get("travel_name") or t.get("name") or "",
                t.get("description") or "",
                t.get("start_date") or t.get("depart") or "",
                t.get("end_date") or t.get("arrive") or "",
                t.get("flight_cost") or t.get("flight") or "",
                t.get("taxi_per_day") or "",
                t.get("food_lodge_per_day") or t.get("food_per_day") or "",
                t.get("days") or t.get("num_days") or "",
            ]
            for col, val in enumerate(cols, start=1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.border = border
            row += 1

        for t in domestic_travel:
            add_travel_row("Domestic", t)
        for t in international_travel:
            add_travel_row("International", t)
        row += 2

    # Materials section
    if materials:
        ws.cell(row=row, column=1, value="Materials and Supplies").font = bold
        row += 1
        headers = ["Category", "Year", "Description", "Cost"]
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = bold
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        row += 1

        for m in materials:
            cols = [
                m.get("material_type") or m.get("category") or "",
                m.get("year"),
                m.get("description") or "",
                m.get("cost") or "",
            ]
            for col, val in enumerate(cols, start=1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.border = border
            row += 1

    # Save to memory and return
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"grant_{award.get('award_id', award_id)}.xlsx"
    resp = make_response(bio.getvalue())
    resp.headers["Content-Type"] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

# ========== Edit / Delete / Submit / Admin ==========

@app.route("/awards/<int:award_id>/edit", methods=["GET", "POST"])
def award_edit(award_id):
    """Edit an existing award (PI only). Reuses awards_new.html."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    if u["role"] != "PI":
        return redirect(url_for("dashboard"))

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        sponsor_type = request.form.get("sponsor_type", "").strip()
        department = request.form.get("department", "").strip()
        college = request.form.get("college", "").strip()
        contact_email = request.form.get("contact_email", "").strip()
        amount = request.form.get("amount", "").strip()
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        abstract = request.form.get("abstract", "").strip()
        keywords = request.form.get("keywords", "").strip()
        collaborators = request.form.get("collaborators", "").strip()

        personnel_json_str = request.form.get("personnel_json", "")
        domestic_travel_json_str = request.form.get("domestic_travel_json", "")
        international_travel_json_str = request.form.get("international_travel_json", "")
        materials_json_str = request.form.get("materials_json", "")

        pers_list = _parse_json_field(personnel_json_str)
        dom_list = _parse_json_field(domestic_travel_json_str)
        intl_list = _parse_json_field(international_travel_json_str)
        mat_list = _parse_json_field(materials_json_str)

        if not title or not sponsor_type or not amount or not start_date or not end_date:
            return make_response("Missing required fields", 400)

        try:
            cur = conn.cursor()

            # Update master award + JSON blobs
            cur.execute(
                """
                UPDATE awards
                SET title=%s,
                    sponsor_type=%s,
                    department=%s,
                    college=%s,
                    contact_email=%s,
                    amount=%s,
                    start_date=%s,
                    end_date=%s,
                    abstract=%s,
                    keywords=%s,
                    collaborators=%s,
                    personnel_json=%s::jsonb,
                    domestic_travel_json=%s::jsonb,
                    international_travel_json=%s::jsonb,
                    materials_json=%s::jsonb
                WHERE award_id=%s AND created_by_email=%s
                """,
                (
                    title, sponsor_type or None,
                    department or None, college or None, contact_email or None,
                    amount, start_date, end_date,
                    abstract or None, keywords or None, collaborators or None,
                    json.dumps(pers_list),
                    json.dumps(dom_list),
                    json.dumps(intl_list),
                    json.dumps(mat_list),
                    award_id, u["email"],
                ),
            )

            # Wipe existing detail rows and re-insert
            cur.execute("DELETE FROM personnel_expenses WHERE award_id=%s", (award_id,))
            cur.execute("DELETE FROM travel_expenses WHERE award_id=%s", (award_id,))
            cur.execute("DELETE FROM material_supplies WHERE award_id=%s", (award_id,))

            # Helper for numeric
            def num(v):
                try:
                    return float(v) if v not in (None, "", "null") else None
                except (TypeError, ValueError):
                    return None

            # Re-insert personnel
            for p in pers_list:
                name_val = (p.get("name") or "").strip()
                if not name_val:
                    continue
                position = (p.get("position") or "").strip()
                same_each_year = bool(p.get("same_each_year", False))

                hours_for_years = None
                hrs = p.get("hours")
                if isinstance(hrs, list) and hrs:
                    total = 0.0
                    for h in hrs:
                        try:
                            total += float(h.get("hours", 0) or 0)
                        except Exception:
                            pass
                    if total > 0:
                        hours_for_years = total

                cur.execute(
                    """
                    INSERT INTO personnel_expenses
                      (award_id, person_name, position_title, hours_for_years, same_each_year)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (award_id, name_val, position or None, hours_for_years, same_each_year),
                )

            # Re-insert domestic travel
            for t in dom_list:
                travel_name = (t.get("travel_name") or t.get("name") or "").strip()
                if not travel_name:
                    continue
                desc = (t.get("description") or "").strip()
                year = t.get("year")
                start = t.get("start_date") or t.get("depart") or None
                end = t.get("end_date") or t.get("arrive") or None

                flight = num(t.get("flight_cost") or t.get("flight"))
                taxi = num(t.get("taxi_per_day"))
                food = num(t.get("food_lodge_per_day") or t.get("food_per_day"))
                days = None
                try:
                    dval = t.get("days")
                    days = int(dval) if dval not in (None, "", "null") else None
                except (TypeError, ValueError):
                    days = None

                cur.execute(
                    """
                    INSERT INTO travel_expenses
                      (award_id, travel_type, travel_name, description, year,
                       start_date, end_date, flight_cost, taxi_per_day,
                       food_lodge_per_day, num_days)
                    VALUES (%s, 'Domestic', %s, %s, %s,
                            %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        award_id, travel_name, desc or None, year,
                        start, end, flight, taxi, food, days,
                    ),
                )

            # Re-insert international travel
            for t in intl_list:
                travel_name = (t.get("travel_name") or t.get("name") or "").strip()
                if not travel_name:
                    continue
                desc = (t.get("description") or "").strip()
                year = t.get("year")
                start = t.get("start_date") or t.get("depart") or None
                end = t.get("end_date") or t.get("arrive") or None

                flight = num(t.get("flight_cost") or t.get("flight"))
                taxi = num(t.get("taxi_per_day"))
                food = num(t.get("food_lodge_per_day") or t.get("food_per_day"))
                days = None
                try:
                    dval = t.get("days")
                    days = int(dval) if dval not in (None, "", "null") else None
                except (TypeError, ValueError):
                    days = None

                cur.execute(
                    """
                    INSERT INTO travel_expenses
                      (award_id, travel_type, travel_name, description, year,
                       start_date, end_date, flight_cost, taxi_per_day,
                       food_lodge_per_day, num_days)
                    VALUES (%s, 'International', %s, %s, %s,
                            %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        award_id, travel_name, desc or None, year,
                        start, end, flight, taxi, food, days,
                    ),
                )

            # Re-insert materials/supplies
            for m in mat_list:
                mtype = (m.get("material_type") or m.get("category") or "").strip()
                if not mtype:
                    continue
                desc = (m.get("description") or "").strip()
                year = m.get("year")
                try:
                    cost_val = float(m.get("cost")) if m.get("cost") not in (None, "", "null") else None
                except (TypeError, ValueError):
                    cost_val = None

                cur.execute(
                    """
                    INSERT INTO material_supplies
                      (award_id, material_type, cost, description, year)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (award_id, mtype, cost_val, desc or None, year),
                )

            conn.commit()
            cur.close()
        except Exception as e:
            print(f"DB update award error: {e}")
            conn.rollback()
            return make_response("Update failed", 500)
        finally:
            conn.close()

        return redirect(url_for("dashboard"))

    # GET: load existing award
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM awards WHERE award_id=%s AND created_by_email=%s",
            (award_id, u["email"]),
        )
        award = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB fetch single award error: {e}")
        return make_response("DB query failed", 500)

    if not award:
        return "Award not found", 404

    # For now we only prefill the master fields (form JS could later prefill JSON details)
    return render_template("awards_new.html", award=award)


@app.route("/awards/<int:award_id>/delete", methods=["POST"])
def award_delete(award_id):
    """Delete an award (PI only)."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    if u["role"] != "PI":
        return redirect(url_for("dashboard"))

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM awards WHERE award_id=%s AND created_by_email=%s",
            (award_id, u["email"]),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB delete award error: {e}")
        conn.rollback()
        return make_response("Delete failed", 500)
    finally:
        conn.close()

    return redirect(url_for("dashboard"))

@app.route("/awards/<int:award_id>/submit", methods=["POST"])
def award_submit(award_id):
    """
    PI clicks Submit on dashboard.
    Flow:
      Draft -> (AI review) -> AI Declined or AI Passed (or Pending if AI unknown)
    """
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    if u["role"] != "PI":
        return redirect(url_for("dashboard"))

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        # 1) Load the award as a dict for AI
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM awards WHERE award_id=%s AND created_by_email=%s",
            (award_id, u["email"]),
        )
        award = cur.fetchone()
        cur.close()

        if not award:
            conn.close()
            return "Award not found", 404

        # 2) Run AI policy check
        decision, reason = _run_ai_policy_check(award)
        print("AI DECISION:", decision)   # DEBUG
        print("AI REASON:", reason)       # DEBUG

        if decision == "pass":
            new_status = "AI Passed"
        elif decision == "fail":
            new_status = "AI Declined"
        else:  # "unknown" or anything else
            new_status = "Pending"

        # 3) Update award status + store AI explanation
        cur2 = conn.cursor()
        cur2.execute(
            """
            UPDATE awards
            SET status = %s,
                ai_review_notes = %s
            WHERE award_id = %s
              AND created_by_email = %s
            """,
            (new_status, reason, award_id, u["email"]),
        )
        conn.commit()
        cur2.close()

    except Exception as e:
        print(f"DB submit/AI check error: {e}")
        conn.rollback()
        return make_response("Submit failed", 500)
    finally:
        conn.close()

    return redirect(url_for("dashboard"))

# ========== Admin actions: approve / decline ==========

@app.route("/awards/<int:award_id>/approve", methods=["POST"])
def award_approve(award_id):
    u = session.get("user")
    if not u or u.get("role") != "Admin":
        return redirect(url_for("home"))

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor()

        # Amount of this award
        cur.execute("SELECT amount FROM awards WHERE award_id=%s", (award_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return "Award not found", 404

        amount_val = row[0]
        amount = float(amount_val) if amount_val is not None else 0.0

        # Current approved total
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM awards WHERE status='Approved'")
        row = cur.fetchone()
        total_approved = float(row[0]) if row and row[0] is not None else 0.0
        remaining = float(ADMIN_INITIAL_BUDGET) - total_approved

        if remaining < amount:
            cur.close()
            conn.close()
            return make_response("Not enough remaining admin budget to approve this award.", 400)

        cur.execute(
            "UPDATE awards SET status='Approved' WHERE award_id=%s",
            (award_id,),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB approve award error: {e}")
        conn.rollback()
        return make_response("Approve failed", 500)
    finally:
        conn.close()

    return redirect(url_for("dashboard"))


@app.route("/awards/<int:award_id>/decline", methods=["POST"])
def award_decline(award_id):
    u = session.get("user")
    if not u or u.get("role") != "Admin":
        return redirect(url_for("home"))

    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)

    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE awards SET status='Declined' WHERE award_id=%s",
            (award_id,),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB decline award error: {e}")
        conn.rollback()
        return make_response("Decline failed", 500)
    finally:
        conn.close()

    return redirect(url_for("dashboard"))


# ========== Other pages ==========

@app.route("/subawards")
def subawards():
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    return render_template("subawards.html")


@app.route("/settings")
def settings():
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    return render_template("settings.html")


@app.route("/profile")
def profile():
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    return render_template("profile.html")

@app.route("/policies/university")
def university_policies():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM policies WHERE policy_level = 'University'")
        policies = cur.fetchall()
        cur.close()
        conn.close()
        return render_template("policies_university.html", policies=policies)
    except Exception as e:
        return f"Database error: {e}"


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    init_db_if_needed()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)


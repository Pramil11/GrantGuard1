from flask import Flask, render_template, request, redirect, session, url_for, make_response, send_file
import psycopg2
from psycopg2 import errors as psycopg2_errors
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

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, template_folder='Templates')
app.secret_key = "change-this-to-any-random-secret"  # needed for session

# Add custom Jinja2 filter for JSON parsing
@app.template_filter('from_json')
def from_json_filter(value):
    """Parse JSON string in templates."""
    if not value:
        return None
    try:
        if isinstance(value, str):
            return json.loads(value)
        return value
    except (json.JSONDecodeError, TypeError):
        return None

# ---- Admin global budget (1 million) ----
ADMIN_INITIAL_BUDGET = 1_000_000

# ---- DB config (use environment variables in deployment) ----
DATABASE_URL = os.getenv("DATABASE_URL")  # Render provides this

if DATABASE_URL:
    # Parse DATABASE_URL: postgresql://user:pass@host:port/dbname
    import urllib.parse as urlparse
    urlparse.uses_netloc.append("postgresql")
    url = urlparse.urlparse(DATABASE_URL)
    DB_HOST = url.hostname
    DB_USER = url.username
    DB_PASS = url.password
    DB_NAME = url.path[1:]  # Remove leading /
    DB_PORT = url.port or 5432
else:
    # Local / fallback creds
    DB_HOST = os.getenv("PGHOST", "dpg-d426gaje5dus73bfka20-a.oregon-postgres.render.com")
    DB_USER = os.getenv("PGUSER", "admin_user")
    DB_PASS = os.getenv("PGPASSWORD", "NXUMDSA8WjBCkn5xBKFkxQGaKGaxNie8")
    DB_NAME = os.getenv("PGDATABASE", "grandguard")
    DB_PORT = int(os.getenv("PGPORT", "5432"))


def get_db():
    """Create a connection per request. No app-start crash if creds are wrong."""
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
            # Execute the entire schema file
            # PostgreSQL can handle multiple statements if we use execute with the full SQL
            try:
                # Remove comments and clean up
                lines = schema_sql.split('\n')
                cleaned_lines = []
                for line in lines:
                    # Skip comment lines and empty lines
                    stripped = line.strip()
                    if stripped and not stripped.startswith('--'):
                        cleaned_lines.append(line)
                cleaned_sql = '\n'.join(cleaned_lines)
                
                # Execute the cleaned SQL
                cur.execute(cleaned_sql)
                conn.commit()
                print("✓ Database schema initialized")
            except Exception as schema_error:
                # If full execution fails, try executing statement by statement
                print(f"Full schema execution failed, trying statement by statement: {schema_error}")
                statements = schema_sql.split(';')
                for statement in statements:
                    statement = statement.strip()
                    if statement and not statement.startswith('--') and not statement.upper().startswith('SELECT'):
                        try:
                            cur.execute(statement)
                        except Exception as stmt_error:
                            # Some statements might fail if tables already exist, that's okay
                            error_msg = str(stmt_error)
                            if 'already exists' not in error_msg.lower() and 'duplicate' not in error_msg.lower():
                                print(f"Statement execution note: {stmt_error}")
                conn.commit()
                print("✓ Database schema initialized (statement by statement)")
        else:
            print(f"Warning: Schema file {schema_file} not found")
        cur.close()
    except Exception as e:
        print(f"DB init error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
    finally:
        conn.close()


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
        total_approved = 0.0
        conn = get_db()
        if conn is not None:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute(
                    """
                    SELECT award_id, title, created_by_email, sponsor_type,
                        amount, start_date, end_date, status, created_at, ai_review_notes
                    FROM awards
                    WHERE status <> 'Draft'
                    ORDER BY created_at DESC
                    """
                )
                awards = cur.fetchall()

                # Calculate total approved - sum all approved awards
                cur.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) as total
                    FROM awards 
                    WHERE status = 'Approved'
                    """
                )
                row = cur.fetchone()
                if row:
                    total_approved = float(row['total']) if row['total'] is not None else 0.0
                else:
                    total_approved = 0.0
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
    award, personnel, domestic_travel, international_travel, materials, equpiment, other_direct
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
    equipment = json.loads(award.get("equipment_json") or "[]")
    other_direct = json.loads(award.get("other_direct_json") or "[]")

    return award, personnel, domestic_travel, international_travel, materials, equipment, other_direct 

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
    equipment = json.loads(award.get("equipment_json") or "[]")
    other_direct = json.loads(award.get("other_direct_json") or "[]")

    # --- Compute period & year list for tables ---
    start = award.get("start_date")
    end = award.get("end_date")

    years = []
    duration_years = None
    if isinstance(start, date) and isinstance(end, date) and end >= start:
        years = list(range(start.year, end.year + 1))
        duration_years = end.year - start.year + 1

    # Load AI compliance results if they exist
    compliance_results = None
    if award.get('ai_review_notes'):
        try:
            compliance_results = json.loads(award['ai_review_notes'])
        except (json.JSONDecodeError, TypeError):
            compliance_results = None

    return render_template(
        "award_view.html",
        award=award,
        personnel=personnel,
        domestic_travel=domestic_travel,
        international_travel=international_travel,
        materials=materials,
        years=years,
        duration_years=duration_years,
        compliance_results=compliance_results,
        user=u,
    )


# ========== EXPORTS: Excel + PDF ==========

@app.route("/awards/<int:award_id>/download/pdf")
def download_award_pdf(award_id):
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))

    award, personnel, domestic_travel, international_travel, materials, equipment, other_direct = _get_award_for_export(award_id, u)
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
            t.get("description") or "",
            t.get("flight_cost") or t.get("flight") or "",
            t.get("taxi_per_day") or "",
            t.get("food_lodge_per_day") or t.get("food_per_day") or "",
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
                "Type", "Description",
                "Flight Cost",
                "Taxi/Day", "Food & Lodge/Day",
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
        data = [["Description", "Cost"]]
        for m in materials:
            data.append([
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
        # -------- Equipment ----------
    if equipment:
        elements.append(Paragraph("Equipment", styles["Heading3"]))
        data = [["Description", "Cost"]]

        for e in equipment:
            data.append([
                e.get("description") or "",
                e.get("cost") or "",
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
    # -------- Other Direct Costs ----------
    if other_direct:
        elements.append(Paragraph("Other Direct Costs", styles["Heading3"]))
        data = [["Description", "Cost"]]

        for d in other_direct:
            data.append([
                d.get("description") or "",
                d.get("cost") or "",
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

    award, personnel, domestic_travel, international_travel, materials, equipment, other_direct = _get_award_for_export(award_id, u)
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
            "Type", "Description",
            "Flight Cost",
            "Taxi/Day", "Food & Lodge/Day",
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
                t.get("description") or "",
                t.get("flight_cost") or t.get("flight") or "",
                t.get("taxi_per_day") or "",
                t.get("food_lodge_per_day") or t.get("food_per_day") or "",
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
        headers = ["Description", "Cost"]
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = bold
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        row += 1

        for m in materials:
            cols = [
                m.get("description") or "",
                m.get("cost") or "",
            ]
            for col, val in enumerate(cols, start=1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.border = border
            row += 1
    # Equipment section
    if equipment:
        row += 2
        ws.cell(row=row, column=1, value="Equipment").font = bold
        row += 1
        headers = ["Description", "Cost"]

        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = bold
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        row += 1

        for e in equipment:
            ws.cell(row=row, column=1, value=e.get("description") or "").border = border
            ws.cell(row=row, column=2, value=e.get("cost") or "").border = border
            row += 1
    # Other Direct Costs
    if other_direct:
        row += 2
        ws.cell(row=row, column=1, value="Other Direct Costs").font = bold
        row += 1
        headers = ["Description", "Cost"]

        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = bold
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        row += 1

        for d in other_direct:
            ws.cell(row=row, column=1, value=d.get("description") or "").border = border
            ws.cell(row=row, column=2, value=d.get("cost") or "").border = border
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
    PI clicks Submit on dashboard – mark award as 'Pending'.
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
        cur = conn.cursor()
        cur.execute(
            "UPDATE awards SET status = %s WHERE award_id = %s AND created_by_email = %s",
            ("Pending", award_id, u["email"]),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB submit award error: {e}")
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
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Amount of this award
        cur.execute("SELECT amount FROM awards WHERE award_id=%s", (award_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return "Award not found", 404

        amount_val = row['amount']
        amount = float(amount_val) if amount_val is not None else 0.0

        # Current approved total (EXCLUDE the current award being approved)
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) as total
            FROM awards 
            WHERE status = 'Approved' AND award_id != %s
            """,
            (award_id,)
        )
        row = cur.fetchone()
        if row:
            total_approved = float(row['total']) if row['total'] is not None else 0.0
        else:
            total_approved = 0.0
        remaining = float(ADMIN_INITIAL_BUDGET) - total_approved

        if remaining < amount:
            cur.close()
            conn.close()
            return make_response(f"Not enough remaining admin budget to approve this award. Remaining: ${remaining:,.2f}, Required: ${amount:,.2f}", 400)

        # Update status to Approved
        cur.execute(
            "UPDATE awards SET status='Approved' WHERE award_id=%s",
            (award_id,),
        )
        conn.commit()
                        # Initialize budget lines for approved award (must happen after commit)
        initialize_budget_lines(award_id)
        cur.close()
        conn.close()
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

# ========== SUBAWARDS SYSTEM ==========

@app.route("/subawards")
def subawards():
    """List all subawards for the user."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    conn = get_db()
    subawards_list = []
    awards_map = {}
    
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            if u["role"] == "Admin":
                # Admin sees all subawards
                cur.execute(
                    """
                    SELECT s.*, a.title as award_title, a.status as award_status
                    FROM subawards s
                    LEFT JOIN awards a ON s.award_id = a.award_id
                    ORDER BY s.created_at DESC
                    """
                )
            else:
                # PI sees only subawards for their awards
                cur.execute(
                    """
                    SELECT s.*, a.title as award_title, a.status as award_status
                    FROM subawards s
                    INNER JOIN awards a ON s.award_id = a.award_id
                    WHERE a.created_by_email = %s
                    ORDER BY s.created_at DESC
                    """,
                    (u["email"],)
                )
            subawards_list = cur.fetchall()
            # Convert amounts to float for template rendering
            for sub in subawards_list:
                if sub.get('amount'):
                    sub['amount'] = float(sub['amount'])
            
            # Get available awards for creating new subawards
            cur.execute(
                """
                SELECT award_id, title, amount, status
                FROM awards
                WHERE status = 'Approved'
                ORDER BY title
                """
            )
            awards = cur.fetchall()
            # Convert amounts to float for template rendering
            for award in awards:
                if award.get('amount'):
                    award['amount'] = float(award['amount'])
            awards_map = {a['award_id']: a for a in awards}
            
            cur.close()
        except psycopg2_errors.UndefinedTable:
            # Tables don't exist - just show empty list, no error message
            subawards_list = []
            awards_map = {}
        except Exception as e:
            print(f"DB fetch subawards error: {e}")
            # Return empty list on error
            subawards_list = []
            awards_map = {}
        finally:
            conn.close()
    
    return render_template("subawards.html", subawards=subawards_list, awards_map=awards_map, user=u)


@app.route("/subawards/new")
def subaward_new():
    """Show form to create a new subaward."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    award_id = request.args.get("award_id", type=int)
    
    conn = get_db()
    awards = []
    selected_award = None
    
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            if u["role"] == "Admin":
                cur.execute(
                    """
                    SELECT award_id, title, amount, status
                    FROM awards
                    WHERE status = 'Approved'
                    ORDER BY title
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT award_id, title, amount, status
                    FROM awards
                    WHERE status = 'Approved' AND created_by_email = %s
                    ORDER BY title
                    """,
                    (u["email"],)
                )
            awards = cur.fetchall()
            # Convert amounts to float for template rendering
            for award in awards:
                if award.get('amount'):
                    award['amount'] = float(award['amount'])
            
            if award_id:
                # Verify the award exists and user has permission
                if u["role"] == "Admin":
                    cur.execute(
                        "SELECT * FROM awards WHERE award_id = %s",
                        (award_id,)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM awards WHERE award_id = %s AND created_by_email = %s",
                        (award_id, u["email"])
                    )
                selected_award = cur.fetchone()
            
            cur.close()
        except Exception as e:
            print(f"DB fetch awards error: {e}")
        finally:
            conn.close()
    
    return render_template("subaward_new.html", awards=awards, selected_award=selected_award, user=u)


@app.route("/subawards", methods=["POST"])
def subaward_create():
    """Create a new subaward."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    award_id = request.form.get("award_id", type=int)
    subrecipient_name = request.form.get("subrecipient_name", "").strip()
    subrecipient_contact = request.form.get("subrecipient_contact", "").strip()
    subrecipient_email = request.form.get("subrecipient_email", "").strip()
    amount = request.form.get("amount", "").strip()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    description = request.form.get("description", "").strip()
    
    if not award_id or not subrecipient_name or not amount:
        return make_response("Missing required fields", 400)
    
    try:
        amount_val = float(amount)
        if amount_val <= 0:
            return make_response("Amount must be positive", 400)
    except ValueError:
        return make_response("Invalid amount", 400)
    
    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Verify award exists and is approved
        cur.execute(
            """
            SELECT award_id, amount, status, created_by_email
            FROM awards
            WHERE award_id = %s
            """,
            (award_id,)
        )
        award = cur.fetchone()
        
        if not award:
            cur.close()
            conn.close()
            return "Award not found", 404
        
        if award['status'] != 'Approved':
            cur.close()
            conn.close()
            return "Only approved awards can have subawards", 400
        
        # Check permissions
        if u["role"] != "Admin" and award['created_by_email'] != u["email"]:
            cur.close()
            conn.close()
            return "Unauthorized", 403
        
        # Check if subaward amount exceeds award amount
        try:
            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0) as total_subawards
                FROM subawards
                WHERE award_id = %s AND status != 'Declined'
                """,
                (award_id,)
            )
            result = cur.fetchone()
            total_subawards = float(result['total_subawards'] or 0) if result else 0
        except Exception as table_error:
            # Table might not exist
            print(f"Table access error (subawards might not exist): {table_error}")
            cur.close()
            conn.close()
            return make_response("Database tables not initialized. Please run the schema migration.", 500)
        
        if total_subawards + amount_val > float(award['amount'] or 0):
            cur.close()
            conn.close()
            return make_response("Subaward total exceeds award amount", 400)
        
        # Insert subaward
        cur.execute(
            """
            INSERT INTO subawards (
                award_id, subrecipient_name, subrecipient_contact,
                subrecipient_email, amount, start_date, end_date,
                description, status, created_by_email
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending', %s)
            RETURNING subaward_id
            """,
            (
                award_id, subrecipient_name, subrecipient_contact or None,
                subrecipient_email or None, amount_val,
                start_date or None, end_date or None,
                description or None, u["email"]
            )
        )
        subaward_id = cur.fetchone()['subaward_id']
        
        conn.commit()
        cur.close()
        
    except psycopg2_errors.UndefinedTable as table_error:
        print(f"Table does not exist: {table_error}")
        conn.rollback()
        if conn:
            conn.close()
        return redirect(url_for("admin_init_db"))
    except Exception as e:
        print(f"DB create subaward error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        return make_response(f"Subaward creation failed: {str(e)}", 500)
    finally:
        if conn:
            conn.close()
    
    return redirect(url_for("subaward_view", subaward_id=subaward_id))


@app.route("/subawards/<int:subaward_id>")
def subaward_view(subaward_id):
    """View a subaward details."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    conn = get_db()
    subaward = None
    award = None
    transactions = []
    budget_status = {}
    
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get subaward
            try:
                cur.execute(
                    """
                    SELECT s.*, a.title as award_title, a.status as award_status
                    FROM subawards s
                    LEFT JOIN awards a ON s.award_id = a.award_id
                    WHERE s.subaward_id = %s
                    """,
                    (subaward_id,)
                )
                subaward = cur.fetchone()
                # Convert amount to float for template rendering
                if subaward and subaward.get('amount'):
                    subaward['amount'] = float(subaward['amount'])
            except psycopg2_errors.UndefinedTable:
                cur.close()
                conn.close()
                return redirect(url_for("admin_init_db"))
            except Exception as e:
                print(f"Error fetching subaward: {e}")
                cur.close()
                conn.close()
                return f"Error loading subaward: {str(e)}", 500
            
            if not subaward:
                cur.close()
                conn.close()
                return "Subaward not found", 404
            
            # Get parent award first (needed for permission check)
            cur.execute(
                "SELECT * FROM awards WHERE award_id = %s",
                (subaward['award_id'],)
            )
            award = cur.fetchone()
            
            if not award:
                return "Parent award not found", 404
            
            # Check permissions
            if u["role"] != "Admin" and subaward.get('created_by_email') != u["email"]:
                # Also check if user owns the parent award
                if award.get('created_by_email') != u["email"]:
                    return "Unauthorized", 403
            
            # Get transactions (table might not exist, so catch error)
            try:
                cur.execute(
                    """
                    SELECT t.*, u.name as user_name
                    FROM subaward_transactions t
                    LEFT JOIN users u ON t.user_id = u.user_id
                    WHERE t.subaward_id = %s
                    ORDER BY t.date_submitted DESC
                    """,
                    (subaward_id,)
                )
                transactions = cur.fetchall()
                # Convert amounts to float for template rendering
                for txn in transactions:
                    if txn.get('amount'):
                        txn['amount'] = float(txn['amount'])
            except psycopg2_errors.UndefinedTable:
                transactions = []
            
            # Get budget status (table might not exist, so catch error)
            try:
                cur.execute(
                    """
                    SELECT category, allocated_amount, spent_amount, committed_amount
                    FROM subaward_budget_lines
                    WHERE subaward_id = %s
                    """,
                    (subaward_id,)
                )
                budget_lines = cur.fetchall()
            except psycopg2_errors.UndefinedTable:
                budget_lines = []
            
            for line in budget_lines:
                cat = line['category'] or 'Other'
                budget_status[cat] = {
                    'allocated': float(line['allocated_amount'] or 0),
                    'spent': float(line['spent_amount'] or 0),
                    'committed': float(line['committed_amount'] or 0),
                }
            
            # Add transactions to budget
            for txn in transactions:
                cat = txn['category'] or 'Other'
                amount = float(txn['amount'] or 0)
                
                if cat not in budget_status:
                    budget_status[cat] = {'allocated': 0, 'spent': 0, 'committed': 0}
                
                if txn['status'] == 'Approved':
                    budget_status[cat]['spent'] += amount
                elif txn['status'] == 'Pending':
                    budget_status[cat]['committed'] += amount
            
            # Calculate remaining
            for cat in budget_status:
                budget_status[cat]['remaining'] = (
                    budget_status[cat]['allocated'] -
                    budget_status[cat]['spent'] -
                    budget_status[cat]['committed']
                )
            
            cur.close()
        except Exception as e:
            print(f"DB fetch subaward error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if conn:
                conn.close()
    
    # Calculate totals safely
    totals = {
        'allocated': sum(cat.get('allocated', 0) for cat in budget_status.values()) if budget_status else 0,
        'spent': sum(cat.get('spent', 0) for cat in budget_status.values()) if budget_status else 0,
        'committed': sum(cat.get('committed', 0) for cat in budget_status.values()) if budget_status else 0,
        'remaining': sum(cat.get('remaining', 0) for cat in budget_status.values()) if budget_status else 0,
    }
    
    return render_template(
        "subaward_view.html",
        subaward=subaward,
        award=award,
        transactions=transactions,
        budget_status=budget_status,
        totals=totals,
        user=u
    )


@app.route("/subawards/<int:subaward_id>/approve", methods=["POST"])
def subaward_approve(subaward_id):
    """Approve a subaward (Admin only)."""
    u = session.get("user")
    if not u or u.get("role") != "Admin":
        return redirect(url_for("home"))
    
    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Check if subaward exists
        cur.execute(
            "SELECT subaward_id FROM subawards WHERE subaward_id = %s",
            (subaward_id,)
        )
        if not cur.fetchone():
            return "Subaward not found", 404
        
        cur.execute(
            "UPDATE subawards SET status = 'Approved' WHERE subaward_id = %s",
            (subaward_id,)
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"DB approve subaward error: {e}")
        conn.rollback()
        return make_response("Approve failed", 500)
    finally:
        conn.close()
    
    return redirect(url_for("subaward_view", subaward_id=subaward_id))


@app.route("/subawards/<int:subaward_id>/delete", methods=["POST"])
def subaward_delete(subaward_id):
    """Delete a subaward."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check permissions
        cur.execute(
            """
            SELECT s.*, a.created_by_email as award_owner
            FROM subawards s
            LEFT JOIN awards a ON s.award_id = a.award_id
            WHERE s.subaward_id = %s
            """,
            (subaward_id,)
        )
        subaward = cur.fetchone()
        
        if not subaward:
            return "Subaward not found", 404
        
        if u["role"] != "Admin" and subaward['created_by_email'] != u["email"] and subaward.get('award_owner') != u["email"]:
            return "Unauthorized", 403
        
        cur.execute("DELETE FROM subawards WHERE subaward_id = %s", (subaward_id,))
        conn.commit()
        cur.close()
        
    except Exception as e:
        print(f"DB delete subaward error: {e}")
        conn.rollback()
        return make_response("Delete failed", 500)
    finally:
        conn.close()
    
    return redirect(url_for("subawards"))


# ========== TRANSACTION SYSTEM ==========

def get_budget_status(award_id):
    """Calculate budget status for an award: allocated, committed, spent, remaining by category."""
    conn = get_db()
    if conn is None:
        return {}
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get budget lines (allocated amounts by category)
        cur.execute(
            """
            SELECT category, allocated_amount, spent_amount, committed_amount
            FROM budget_lines
            WHERE award_id = %s
            """,
            (award_id,)
        )
        budget_lines = cur.fetchall()
        
        # Get transactions
        cur.execute(
            """
            SELECT category, amount, status
            FROM transactions
            WHERE award_id = %s
            """,
            (award_id,)
        )
        transactions = cur.fetchall()
        
        # Calculate by category
        categories = {}
        pending_by_category = {}
        
        # Initialize from budget_lines (allocated amounts)
        for line in budget_lines:
            cat = line['category'] or 'Other'
            # Skip "Total" category - we calculate totals separately
            if cat == 'Total':
                continue
            categories[cat] = {
                "allocated": float(line.get("allocated_amount") or 0),
                "spent": float(line.get("spent_amount") or 0),
                "committed": float(line.get("committed_amount") or 0),
            }

        # Calculate spent and committed from transactions (source of truth)
        for txn in transactions:
            cat = txn['category'] or 'Other'
            amount = float(txn['amount'] or 0)
            status = txn['status']
            
            if cat not in categories:
                categories[cat] = {'allocated': 0, 'spent': 0, 'committed': 0}
            
            if status == 'Approved':
                categories[cat]['spent'] += amount
            elif status == 'Pending':
                pending_by_category[cat] = pending_by_category.get(cat, 0) + amount
        # Calculate remaining
        for cat, vals in categories.items():
            pending_amt = pending_by_category.get(cat, 0.0)
            # Committed = spent + pending
            vals['committed'] = vals['spent'] + pending_amt
            # Remaining = allocated - spent (pending does not reduce remaining)
            vals['remaining'] = vals['allocated'] - vals['spent']
        
        cur.close()
        return categories
        
    except Exception as e:
        print(f"Budget status calculation error: {e}")
        return {}
    finally:
        conn.close()


def initialize_budget_lines(award_id):
    """Initialize budget_lines from award's budget breakdown."""
    conn = get_db()
    if conn is None:
        return False
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get award details
        cur.execute(
            """
            SELECT amount, budget_personnel, budget_equipment, 
                   budget_travel, budget_materials, personnel_json,
                   domestic_travel_json, international_travel_json, materials_json
            FROM awards
            WHERE award_id = %s
            """,
            (award_id,)
        )
        award = cur.fetchone()
        
        if not award:
            cur.close()
            return False
        
        # Calculate budget by category from JSON data
        categories = {}
        total_award = float(award['amount'] or 0)
        def parse_json_field(raw):
            if not raw:
                return []
            if isinstance(raw, (list, dict)):
                return raw
            try:
                return json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return []
        # Personnel
        personnel = json.loads(award['personnel_json']) if award['personnel_json'] else []
        personnel_total = 0
        for p in personnel:
            if isinstance(p, dict):
                hours_list = p.get('hours', [])
                if isinstance(hours_list, list):
                    for h in hours_list:
                        if isinstance(h, dict):
                            hrs = float(h.get('hours', 0) or 0)
                            # Assume $50/hour average (should come from cost_rates table later)
                            personnel_total += hrs * 50
        categories['Personnel'] = personnel_total
        
        # Travel
        dom_travel = json.loads(award['domestic_travel_json']) if award['domestic_travel_json'] else []
        intl_travel = json.loads(award['international_travel_json']) if award['international_travel_json'] else []
        travel_total = 0
        for t in dom_travel + intl_travel:
            if isinstance(t, dict):
                flight = float(
                    t.get("flight_cost") or t.get("flight") or 0
                )
                taxi = float(
                    t.get("taxi_per_day") or t.get("taxi") or 0
                )
                food = float(
                    t.get("food_lodge_per_day")
                    or t.get("food_per_day")
                    or t.get("food")
                    or 0
                )
                days = float(t.get("days", 0) or 0)
                travel_total += flight + (taxi + food) * days
        categories["Travel"] = travel_total
        
        # Materials
        materials = json.loads(award['materials_json']) if award['materials_json'] else []
        materials_total = 0
        for m in materials:
            if isinstance(m, dict):
                cost = float(m.get('cost', 0) or 0)
                materials_total += cost
        categories['Materials'] = materials_total
        
        # Equipment (from budget_equipment field)
        categories["Equipment"] = float(award.get("budget_equipment") or 0)
        
        # Other (remaining from total)
        total_allocated = sum(categories.values())
        categories['Other'] = max(0, total_award - total_allocated)
        
        # If no detailed breakdown, allocate everything to "Other"
        if total_allocated == 0 and total_award > 0:
            categories = {"Other": total_award}
        
        # Insert/update budget_lines
        for category, amount in categories.items():
            if amount > 0:
                # Check if exists
                cur.execute(
                    """
                    SELECT line_id FROM budget_lines
                    WHERE award_id = %s AND category = %s
                    """,
                    (award_id, category)
                )
                exists = cur.fetchone()
                
                if exists:
                    # Update allocated amount (don't overwrite spent/committed)
                    cur.execute(
                        """
                        UPDATE budget_lines
                        SET allocated_amount = %s
                        WHERE award_id = %s AND category = %s
                        """,
                        (amount, award_id, category)
                    )
                else:
                    # Insert new budget line
                    cur.execute(
                        """
                        INSERT INTO budget_lines (award_id, category, allocated_amount, spent_amount, committed_amount)
                        VALUES (%s, %s, %s, 0, 0)
                        """,
                        (award_id, category, amount)
                    )
        
        
        conn.commit()
        cur.close()
        return True
        
    except Exception as e:
        print(f"Initialize budget lines error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


@app.route("/transactions/new")
def transaction_new():
    """Show form to create a new transaction."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    award_id = request.args.get("award_id", type=int)
    if not award_id:
        return redirect(url_for("dashboard"))
    
    # Get award details
    conn = get_db()
    award = None
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT award_id, title, amount, status
                FROM awards
                WHERE award_id = %s AND (created_by_email = %s OR %s = 'Admin')
                """,
                (award_id, u["email"], u["role"])
            )
            award = cur.fetchone()
            cur.close()
        except Exception as e:
            print(f"DB fetch award error: {e}")
        finally:
            conn.close()
    
    if not award:
        return "Award not found", 404
    
    if award['status'] != 'Approved':
        return "Only approved awards can have transactions", 400
    
    # Get budget status
    budget_status = get_budget_status(award_id)
    
    return render_template("transaction_new.html", award=award, budget_status=budget_status, user=u)


@app.route("/transactions", methods=["POST"])
def transaction_create():
    """Create a new transaction."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    award_id = request.form.get("award_id", type=int)
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    amount = request.form.get("amount", "").strip()
    date_submitted = request.form.get("date_submitted", "").strip()
    
    if not award_id or not category or not description or not amount or not date_submitted:
        return make_response("Missing required fields", 400)
    
    try:
        amount_val = float(amount)
        if amount_val <= 0:
            return make_response("Amount must be positive", 400)
    except ValueError:
        return make_response("Invalid amount", 400)
    
    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Verify award exists and is approved
        cur.execute(
            """
            SELECT award_id, status, created_by_email
            FROM awards
            WHERE award_id = %s
            """,
            (award_id,)
        )
        award = cur.fetchone()
        
        if not award:
            return "Award not found", 404
        
        if award['status'] != 'Approved':
            return "Only approved awards can have transactions", 400
        
        # Check permissions
        if u["role"] != "Admin" and award['created_by_email'] != u["email"]:
            return "Unauthorized", 403
        
        # Get user_id
        cur.execute("SELECT user_id FROM users WHERE email = %s", (u["email"],))
        user_row = cur.fetchone()
        user_id = user_row['user_id'] if user_row else None
        
        # Check budget availability (only if budget has been allocated)
        budget_status = get_budget_status(award_id)
        cat_budget = budget_status.get(category, {})
        allocated = cat_budget.get('allocated', 0)
        remaining = cat_budget.get('remaining', 0)
        
        # Only check budget if there's an allocated amount for this category
        # If no budget allocated yet, allow the transaction (it will create the budget line)
        if allocated > 0 and amount_val > remaining:
            return make_response(f"Insufficient budget. Remaining: ${remaining:,.2f}", 400)
        
        # Insert transaction
        cur.execute(
            """
            INSERT INTO transactions (award_id, user_id, category, description, amount, date_submitted, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'Pending')
            RETURNING transaction_id
            """,
            (award_id, user_id, category, description, amount_val, date_submitted)
        )
        transaction_id = cur.fetchone()['transaction_id']
        
        # Update committed amount in budget_lines
        # Check if budget line exists
        cur.execute(
            """
            SELECT line_id FROM budget_lines
            WHERE award_id = %s AND category = %s
            """,
            (award_id, category)
        )
        exists = cur.fetchone()
        
        if exists:
            cur.execute(
                """
                UPDATE budget_lines
                SET committed_amount = committed_amount + %s
                WHERE award_id = %s AND category = %s
                """,
                (amount_val, award_id, category)
            )
        else:
            # Create budget line if it doesn't exist
            cur.execute(
                """
                INSERT INTO budget_lines (award_id, category, allocated_amount, spent_amount, committed_amount)
                VALUES (%s, %s, 0, 0, %s)
                """,
                (award_id, category, amount_val)
            )
        
        conn.commit()
        cur.close()
        
    except Exception as e:
        print(f"DB create transaction error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        return make_response(f"Transaction creation failed: {str(e)}", 500)
    finally:
        conn.close()
    
    return redirect(url_for("transactions_list", award_id=award_id))


@app.route("/transactions")
def transactions_list():
    """List all transactions for an award or user."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    award_id = request.args.get("award_id", type=int)
    
    conn = get_db()
    transactions = []
    award = None
    
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            if award_id:
                # Get transactions for specific award
                cur.execute(
                    """
                    SELECT t.*, a.title as award_title, u.name as user_name
                    FROM transactions t
                    LEFT JOIN awards a ON t.award_id = a.award_id
                    LEFT JOIN users u ON t.user_id = u.user_id
                    WHERE t.award_id = %s
                    ORDER BY t.date_submitted DESC, t.transaction_id DESC
                    """,
                    (award_id,)
                )
                transactions = cur.fetchall()
                
                # Get award details
                cur.execute("SELECT * FROM awards WHERE award_id = %s", (award_id,))
                award = cur.fetchone()
            else:
                # Get all transactions for user
                if u["role"] == "Admin":
                    cur.execute(
                        """
                        SELECT t.*, a.title as award_title, u.name as user_name
                        FROM transactions t
                        LEFT JOIN awards a ON t.award_id = a.award_id
                        LEFT JOIN users u ON t.user_id = u.user_id
                        ORDER BY t.date_submitted DESC, t.transaction_id DESC
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT t.*, a.title as award_title, u.name as user_name
                        FROM transactions t
                        LEFT JOIN awards a ON t.award_id = a.award_id
                        LEFT JOIN users u ON t.user_id = u.user_id
                        WHERE a.created_by_email = %s
                        ORDER BY t.date_submitted DESC, t.transaction_id DESC
                        """,
                        (u["email"],)
                    )
                transactions = cur.fetchall()
            
            cur.close()
        except Exception as e:
            print(f"DB fetch transactions error: {e}")
        finally:
            conn.close()
    
    return render_template("transactions_list.html", transactions=transactions, award=award, user=u)


@app.route("/awards/<int:award_id>/budget")
def budget_status(award_id):
    """Show budget status dashboard for an award."""
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    
    conn = get_db()
    award = None
    budget_status_data = {}
    
    if conn is not None:
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT * FROM awards
                WHERE award_id = %s AND (created_by_email = %s OR %s = 'Admin')
                """,
                (award_id, u["email"], u["role"])
            )
            award = cur.fetchone()
            cur.close()
        except Exception as e:
            print(f"DB fetch award error: {e}")
        finally:
            conn.close()
    
    if not award:
        return "Award not found", 404
    
    # Initialize budget lines if award is approved and lines don't exist
    if award['status'] == 'Approved':
        # Always try to initialize - it will update if exists, create if not
        initialize_budget_lines(award_id)
    
    budget_status_data = get_budget_status(award_id)
    
    # Calculate totals
    totals = {
        'allocated': sum(cat.get('allocated', 0) for cat in budget_status_data.values()),
        'spent': sum(cat.get('spent', 0) for cat in budget_status_data.values()),
        'committed': sum(cat.get('committed', 0) for cat in budget_status_data.values()),
        'remaining': sum(cat.get('remaining', 0) for cat in budget_status_data.values()),
    }
    
    u = session.get("user")
    return render_template(
        "budget_status.html",
        award=award,
        budget_status=budget_status_data,
        totals=totals,
        user=u or {}
    )


@app.route("/transactions/<int:transaction_id>/approve", methods=["POST"])
def transaction_approve(transaction_id):
    """Approve a transaction (Admin/Finance only)."""
    u = session.get("user")
    if not u or u.get("role") not in ("Admin", "Finance"):
        return redirect(url_for("home"))
    
    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get transaction details
        cur.execute(
            """
            SELECT t.*, a.status as award_status
            FROM transactions t
            JOIN awards a ON t.award_id = a.award_id
            WHERE t.transaction_id = %s
            """,
            (transaction_id,)
        )
        txn = cur.fetchone()
        
        if not txn:
            return "Transaction not found", 404
        
        if txn['status'] != 'Pending':
            return "Transaction already processed", 400
        
        if txn['award_status'] != 'Approved':
            return "Award must be approved", 400
        
        # Update transaction status
        cur.execute(
            "UPDATE transactions SET status = 'Approved' WHERE transaction_id = %s",
            (transaction_id,)
        )
        
        # Move from committed to spent in budget_lines
        # First ensure budget line exists
        cur.execute(
            """
            SELECT line_id FROM budget_lines
            WHERE award_id = %s AND category = %s
            """,
            (txn['award_id'], txn['category'])
        )
        budget_line = cur.fetchone()
        
        if budget_line:
            cur.execute(
                """
                UPDATE budget_lines
                SET committed_amount = GREATEST(0, committed_amount - %s),
                    spent_amount = spent_amount + %s
                WHERE award_id = %s AND category = %s
                """,
                (txn['amount'], txn['amount'], txn['award_id'], txn['category'])
            )
        else:
            # Create budget line if it doesn't exist
            cur.execute(
                """
                INSERT INTO budget_lines (award_id, category, allocated_amount, spent_amount, committed_amount)
                VALUES (%s, %s, 0, %s, 0)
                """,
                (txn['award_id'], txn['category'], txn['amount'])
            )
        
        conn.commit()
        cur.close()
        
    except Exception as e:
        print(f"DB approve transaction error: {e}")
        conn.rollback()
        return make_response("Approve failed", 500)
    finally:
        conn.close()
    
    return redirect(url_for("transactions_list", award_id=txn['award_id']))


@app.route("/transactions/<int:transaction_id>/decline", methods=["POST"])
def transaction_decline(transaction_id):
    """Decline a transaction (Admin/Finance only)."""
    u = session.get("user")
    if not u or u.get("role") not in ("Admin", "Finance"):
        return redirect(url_for("home"))
    
    conn = get_db()
    if conn is None:
        return make_response("DB connection failed", 500)
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get transaction details
        cur.execute(
            "SELECT * FROM transactions WHERE transaction_id = %s",
            (transaction_id,)
        )
        txn = cur.fetchone()
        
        if not txn:
            return "Transaction not found", 404
        
        if txn['status'] != 'Pending':
            return "Transaction already processed", 400
        
        award_id = txn['award_id']
        
        # Update transaction status
        cur.execute(
            "UPDATE transactions SET status = 'Declined' WHERE transaction_id = %s",
            (transaction_id,)
        )
        
        # Remove from committed amount in budget_lines
        cur.execute(
            """
            SELECT line_id FROM budget_lines
            WHERE award_id = %s AND category = %s
            """,
            (award_id, txn['category'] or 'Other')
        )
        budget_line = cur.fetchone()
        
        if budget_line:
            cur.execute(
                """
                UPDATE budget_lines
                SET committed_amount = GREATEST(0, committed_amount - %s)
                WHERE award_id = %s AND category = %s
                """,
                (txn['amount'], award_id, txn['category'] or 'Other')
            )
        
        conn.commit()
        cur.close()
        
    except Exception as e:
        print(f"DB decline transaction error: {e}")
        conn.rollback()
        return make_response("Decline failed", 500)
    finally:
        conn.close()
    
    return redirect(url_for("transactions_list", award_id=award_id))


@app.route("/settings")
def settings():
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    return render_template("settings.html", user=u)


@app.route("/profile")
def profile():
    u = session.get("user")
    if not u:
        return redirect(url_for("home"))
    awards = []
    conn = get_db()
    if conn is not None:
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """
                SELECT award_id, title, sponsor_type, amount, start_date, end_date, status, created_at
                FROM awards
                WHERE created_by_email=%s
                ORDER BY created_at DESC
                """,
                (u["email"],),
            )
            awards = cur.fetchall()
            cur.close()
        except Exception as e:
            print(f"DB fetch awards (profile) error: {e}")
        finally:
            conn.close()
    stats = {
        "total_awards": len(awards),
        "active_awards": sum(1 for a in awards if (a.get("status") or "").lower() == "active"),
        "latest_award": awards[0] if awards else None,
    }
    return render_template("profile.html", user=u, awards=awards, stats=stats)


@app.route("/policies/university")
def university_policies():
    policy_data = [
        {
            "level": "University",
            "title": "University Research Budget Policy",
            "sections": [
                {
                    "heading": "Personnel (Salary)",
                    "rules": [
                        "Salaries are only allowed for people who directly work on research tasks.",
                        "Paying anyone who does not contribute to the research is not allowed.",
                        "Administrative staff cannot be charged unless 100% dedicated to the project.",
                        "Inflating effort or hours is a violation.",
                    ],
                },
                {
                    "heading": "Equipment",
                    "rules": [
                        "Only research-related equipment can be purchased.",
                        "Equipment under $5,000 is allowed.",
                        "Equipment over $5,000 requires university approval.",
                        "Personal-use electronics are not allowed.",
                        "Buying equipment not needed for the project is a violation.",
                    ],
                },
                {
                    "heading": "Travel",
                    "rules": [
                        "Travel must be directly related to the project.",
                        "Only economy-class travel is allowed.",
                        "Upgraded seats are not allowed.",
                        "Personal or vacation travel is not allowed.",
                        "Charging unrelated travel is a violation.",
                    ],
                },
                {
                    "heading": "Materials & Supplies",
                    "rules": [
                        "Only supplies used for research activities are allowed.",
                        "Office supplies are not allowed.",
                        "Decorations are not allowed.",
                        "Non-research purchases will be treated as violations.",
                    ],
                },
                {
                    "heading": "Other Direct Costs",
                    "rules": [
                        "Participant incentives are allowed.",
                        "Publication fees are allowed.",
                        "Research-related software is allowed.",
                        "Membership fees are not allowed unless required.",
                        "Charging unrelated services is a violation.",
                    ],
                },
            ],
        },
        {
            "level": "Sponsor",
            "title": "Sponsor Research Budget Policy",
            "sections": [
                {
                    "heading": "Personnel (Salary)",
                    "rules": [
                        "Only salaries listed in the sponsor-approved proposal are allowed.",
                        "Adding new personnel without approval is not allowed.",
                        "Admin/clerical salaries require written sponsor approval.",
                        "Charging unapproved salary lines is a violation.",
                    ],
                },
                {
                    "heading": "Equipment",
                    "rules": [
                        "Only equipment approved in the proposal is allowed.",
                        "New or unplanned equipment purchases require sponsor approval.",
                        "General-purpose equipment is not allowed.",
                        "Buying items not listed in the proposal is a violation.",
                    ],
                },
                {
                    "heading": "Travel",
                    "rules": [
                        "Only travel included in the proposal budget is allowed.",
                        "Sponsor-required travel is allowed.",
                        "Foreign travel requires approval.",
                        "Non-research travel is not allowed.",
                        "Charging personal travel is a violation.",
                    ],
                },
                {
                    "heading": "Participant Support Costs (PSC)",
                    "rules": [
                        "PSC can only be used for participant stipends, travel, lodging, and meals.",
                        "PSC cannot be used to pay PI or staff salaries.",
                        "PSC cannot be rebudgeted without sponsor approval.",
                        "Using PSC funds for equipment is a violation.",
                    ],
                },
                {
                    "heading": "Subawards",
                    "rules": [
                        "Only approved subawards listed in the proposal are allowed.",
                        "Informal payments without agreements are not allowed.",
                        "Adding new partners requires sponsor approval.",
                        "Paying unapproved subrecipients is a violation.",
                    ],
                },
            ],
        },
        {
            "level": "Federal",
            "title": "Federal Policy",
            "sections": [
                {
                    "heading": "Personnel (Salary)",
                    "rules": [
                        "Salaries must match the percentage of time worked on the project.",
                        "Paying someone more than their normal rate is not allowed.",
                        "Charging unrelated work to the project is a violation.",
                        "Administrative salaries are only allowed in special circumstances.",
                    ],
                },
                {
                    "heading": "Equipment",
                    "rules": [
                        "Equipment must be necessary for the project.",
                        "Federal procurement rules must be followed.",
                        "Splitting purchases to avoid bidding rules is not allowed.",
                        "Buying equipment for future projects is a violation.",
                    ],
                },
                {
                    "heading": "Travel",
                    "rules": [
                        "Only economy travel is allowed.",
                        "Travel must support project goals.",
                        "Foreign travel must follow the Fly America Act.",
                        "Business/first-class travel is not allowed.",
                        "Charging personal travel is a violation.",
                    ],
                },
                {
                    "heading": "Subawards & Procurement",
                    "rules": [
                        "Vendors must be selected competitively when required.",
                        "Sole-source purchases must be documented.",
                        "Personal relationships cannot influence vendor selection.",
                        "Paying individuals without contracts is a violation.",
                    ],
                },
                {
                    "heading": "Documentation",
                    "rules": [
                        "Receipts are required for all expenses.",
                        "Justifications must clearly show project benefit.",
                        "Missing documentation is not allowed.",
                        "Vague explanations are considered violations.",
                    ],
                },
            ],
        },
    ]
    u = session.get("user")
    return render_template("policies_university.html", policies=policy_data, user=u or {})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ========== LLM POLICY COMPLIANCE CHECKING ==========

def read_policy_file(policy_name):
    """Read policy text from file."""
    policy_path = os.path.join("policies", f"{policy_name}_policy.txt")
    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Policy file not found: {policy_path}")
        return ""
    except Exception as e:
        print(f"Error reading policy file {policy_path}: {e}")
        return ""


def format_award_for_llm(award, personnel, domestic_travel, international_travel, materials):
    """Format award data into a structured text for LLM analysis."""
    award_text = f"""
AWARD INFORMATION:
- Title: {award.get('title', 'N/A')}
- Sponsor Type: {award.get('sponsor_type', 'N/A')}
- Total Amount: ${float(award.get('amount', 0) or 0):,.2f}
- Start Date: {award.get('start_date', 'N/A')}
- End Date: {award.get('end_date', 'N/A')}
- Department: {award.get('department', 'N/A')}
- College: {award.get('college', 'N/A')}

BUDGET BREAKDOWN:
- Personnel Budget: ${float(award.get('budget_personnel', 0) or 0):,.2f}
- Equipment Budget: ${float(award.get('budget_equipment', 0) or 0):,.2f}
- Travel Budget: ${float(award.get('budget_travel', 0) or 0):,.2f}
- Materials Budget: ${float(award.get('budget_materials', 0) or 0):,.2f}
"""
    
    if personnel:
        award_text += "\nPERSONNEL DETAILS:\n"
        for p in personnel:
            if isinstance(p, dict):
                name = p.get('name', 'Unknown')
                role = p.get('position', 'N/A') or p.get('role', 'N/A')
                hours_list = p.get('hours', [])
                total_hours = 0
                if isinstance(hours_list, list):
                    for h in hours_list:
                        if isinstance(h, dict):
                            total_hours += float(h.get('hours', 0) or 0)
                award_text += f"- {name} ({role}): {total_hours} hours\n"
    
    if domestic_travel:
        award_text += "\nDOMESTIC TRAVEL:\n"
        for t in domestic_travel:
            if isinstance(t, dict):
                description = t.get('description', 'N/A')
                flight = t.get('flight', 0) or t.get('flight_cost', 0)
                taxi = t.get('taxi', 0) or t.get('taxi_per_day', 0)
                food = t.get('food', 0) or t.get('food_per_day', 0)
                days = t.get('days', 0) or t.get('num_days', 0)
                award_text += f"- {description}: Flight: ${float(flight or 0):,.2f}, Taxi/day: ${float(taxi or 0):,.2f}, Food/day: ${float(food or 0):,.2f}, Days: {days}\n"
    
    if international_travel:
        award_text += "\nINTERNATIONAL TRAVEL:\n"
        for t in international_travel:
            if isinstance(t, dict):
                description = t.get('description', 'N/A')
                flight = t.get('flight', 0) or t.get('flight_cost', 0)
                taxi = t.get('taxi', 0) or t.get('taxi_per_day', 0)
                food = t.get('food', 0) or t.get('food_per_day', 0)
                days = t.get('days', 0) or t.get('num_days', 0)
                award_text += f"- {description}: Flight: ${float(flight or 0):,.2f}, Taxi/day: ${float(taxi or 0):,.2f}, Food/day: ${float(food or 0):,.2f}, Days: {days}\n"
    
    if materials:
        award_text += "\nMATERIALS & SUPPLIES:\n"
        for m in materials:
            if isinstance(m, dict):
                description = m.get('description', 'N/A')
                category = m.get('category', '') or m.get('material_type', '') or m.get('type', '')
                cost = m.get('cost', 0)
                award_text += f"- {category}: {description}, Cost: ${float(cost or 0):,.2f}\n"
    
    return award_text


def check_policy_compliance(award, personnel, domestic_travel, international_travel, materials):
    """
    Check award compliance against University, Sponsor, and Federal policies using LLM.
    Returns a dict with compliance results for each policy level.
    """
    # Read policy files
    university_policy = read_policy_file("university")
    federal_policy = read_policy_file("federal")
    sponsor_policy = read_policy_file("sponsor")
    
    # Format award data
    award_text = format_award_for_llm(award, personnel, domestic_travel, international_travel, materials)
    
    # Get OpenAI API key from environment
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "error": "OpenAI API key not configured",
            "university": {"result": "unknown", "reason": "API key missing"},
            "federal": {"result": "unknown", "reason": "API key missing"},
            "sponsor": {"result": "unknown", "reason": "API key missing"}
        }
    
    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    
    results = {}
    
    # Check each policy level
    policy_checks = [
        ("university", "University", university_policy),
        ("federal", "Federal", federal_policy),
        ("sponsor", "Sponsor", sponsor_policy)
    ]
    
    for key, name, policy_text in policy_checks:
        if not policy_text:
            results[key] = {"result": "unknown", "reason": f"{name} policy text not available"}
            continue
        
        try:
            # Determine priority level for context
            priority_note = ""
            if name == "Federal":
                priority_note = "NOTE: Federal policy has HIGHEST PRIORITY. Any violation must result in 'non-compliant'."
            elif name == "Sponsor":
                priority_note = "NOTE: Sponsor policy must follow Federal requirements. Check both Federal and Sponsor rules."
            elif name == "University":
                priority_note = "NOTE: University policy is lowest priority but must still be followed. Check if it conflicts with Federal/Sponsor rules."
            
            prompt = f"""You are an AI Policy Compliance Officer for a Post-Award Research Budget Management System.

Your job is to check whether a research award complies with {name} policy.

CRITICAL: You must base every decision ONLY on the policy text provided. Do not assume or invent any rules.

{priority_note}

POLICY TEXT:
{policy_text}

AWARD DATA:
{award_text}

Analyze the award against the {name} policy above. Your output must be a JSON object in this exact format:
{{
  "result": "compliant" | "non-compliant" | "unknown",
  "reason": "Short and clear explanation referencing specific policy text and section numbers."
}}

Only return the JSON object, nothing else."""

            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Using gpt-4o-mini for cost efficiency
                messages=[
                    {"role": "system", "content": "You are a policy compliance officer. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent results
                max_tokens=500
            )
            
            # Parse JSON response
            response_text = response.choices[0].message.content.strip()
            # Remove markdown code blocks if present
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()
            
            compliance_result = json.loads(response_text)
            results[key] = compliance_result
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response for {name} policy: {e}")
            response_text_str = response_text if 'response_text' in locals() else "No response received"
            print(f"Response was: {response_text_str}")
            results[key] = {"result": "unknown", "reason": f"Error parsing LLM response: {str(e)}"}
        except Exception as e:
            print(f"Error checking {name} policy compliance: {e}")
            import traceback
            traceback.print_exc()
            results[key] = {"result": "unknown", "reason": f"Error: {str(e)}"}
    
    return results


@app.route("/awards/<int:award_id>/check-compliance", methods=["POST"])
def check_award_compliance(award_id):
    """Check policy compliance for an award using LLM."""
    u = session.get("user")
    if not u:
        return make_response(json.dumps({"error": "Not authenticated"}), 401, {"Content-Type": "application/json"})
    
    # Only Admin can check compliance
    if u.get("role") != "Admin":
        return make_response(json.dumps({"error": "Unauthorized"}), 403, {"Content-Type": "application/json"})
    
    conn = get_db()
    if conn is None:
        return make_response(json.dumps({"error": "DB connection failed"}), 500, {"Content-Type": "application/json"})
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get award
        cur.execute("SELECT * FROM awards WHERE award_id=%s", (award_id,))
        award = cur.fetchone()
        
        if not award:
            cur.close()
            conn.close()
            return make_response(json.dumps({"error": "Award not found"}), 404, {"Content-Type": "application/json"})
        
        # Parse JSON fields
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
        
        cur.close()
        conn.close()
        
        # Check compliance
        compliance_results = check_policy_compliance(award, personnel, domestic_travel, international_travel, materials)
        
        # Store results in database (optional - update ai_review_notes)
        if "error" not in compliance_results:
            conn = get_db()
            if conn:
                try:
                    cur = conn.cursor()
                    notes = json.dumps(compliance_results)
                    cur.execute(
                        "UPDATE awards SET ai_review_notes = %s WHERE award_id = %s",
                        (notes, award_id)
                    )
                    conn.commit()
                    cur.close()
                except Exception as e:
                    print(f"Error saving compliance results: {e}")
                finally:
                    conn.close()
        
        return make_response(json.dumps(compliance_results, indent=2), 200, {"Content-Type": "application/json"})
        
    except Exception as e:
        print(f"Error checking compliance: {e}")
        return make_response(json.dumps({"error": str(e)}), 500, {"Content-Type": "application/json"})


@app.route("/admin/init-db", methods=["GET", "POST"])
def admin_init_db():
    """Admin route to manually initialize database schema."""
    u = session.get("user")
    if not u or u.get("role") != "Admin":
        return "Unauthorized - Admin access required", 403
    
    if request.method == "POST":
        conn = get_db()
        if conn is None:
            return "Database connection failed", 500
        
        try:
            cur = conn.cursor()
            schema_file = os.path.join(os.path.dirname(__file__), "schema_postgresql.sql")
            if os.path.exists(schema_file):
                with open(schema_file, 'r') as f:
                    schema_sql = f.read()
                
                # Remove comments and execute
                lines = schema_sql.split('\n')
                cleaned_lines = []
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('--'):
                        cleaned_lines.append(line)
                cleaned_sql = '\n'.join(cleaned_lines)
                
                try:
                    cur.execute(cleaned_sql)
                    conn.commit()
                    message = "✓ Database schema initialized successfully!"
                except Exception as full_error:
                    # Try statement by statement
                    statements = schema_sql.split(';')
                    executed = 0
                    for statement in statements:
                        statement = statement.strip()
                        if statement and not statement.startswith('--') and not statement.upper().startswith('SELECT'):
                            try:
                                cur.execute(statement)
                                executed += 1
                            except Exception as stmt_error:
                                error_msg = str(stmt_error)
                                if 'already exists' not in error_msg.lower():
                                    print(f"Statement error: {stmt_error}")
                    conn.commit()
                    message = f"✓ Database schema initialized! ({executed} statements executed)"
                
                cur.close()
                conn.close()
                # Redirect to subawards page after successful initialization
                return redirect(url_for("subawards"))
            else:
                return f"Schema file not found: {schema_file}", 404
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Error initializing database: {str(e)}", 500
    
    # GET request - automatically initialize and redirect
    # No confirmation page, just do it
    conn = get_db()
    if conn is None:
        return "Database connection failed", 500
    
    try:
        cur = conn.cursor()
        schema_file = os.path.join(os.path.dirname(__file__), "schema_postgresql.sql")
        if os.path.exists(schema_file):
            with open(schema_file, 'r') as f:
                schema_sql = f.read()
            
            # Execute statements
            statements = schema_sql.split(';')
            for statement in statements:
                statement = statement.strip()
                if statement and not statement.startswith('--') and not statement.upper().startswith('SELECT'):
                    try:
                        cur.execute(statement)
                    except Exception:
                        pass  # Ignore errors (tables might already exist)
            conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass
    
    # Always redirect to subawards
    return redirect(url_for("subawards"))


if __name__ == "__main__":
    init_db_if_needed()
    app.run(debug=True, port=8000)

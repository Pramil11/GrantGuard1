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

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, template_folder='Templates')
app.secret_key = "change-this-to-any-random-secret"  # needed for session

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
                        amount, start_date, end_date, status, created_at
                    FROM awards
                    WHERE status <> 'Draft'
                    ORDER BY created_at DESC
                    """
                )
                awards = cur.fetchall()

                cur.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM awards WHERE status = 'Approved'"
                )
                row = cur.fetchone()
                total_approved = float(row[0]) if row and row[0] is not None else 0.0
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
    return render_template("policies_university.html", policies=policy_data)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    init_db_if_needed()
    app.run(debug=True, port=8000)

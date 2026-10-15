import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from sqlalchemy import inspect, text, func
from models import db, User, Ticket, KnowledgeArticle
from ai_analyzer import analyze_ticket


app = Flask(__name__)

app.config["SECRET_KEY"] = "dev-secret-key"

app.config["SQLALCHEMY_DATABASE_URI"] = (
    "sqlite:///helpdesk.db"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Profile picture uploads
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "avatars")
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_AVATAR_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


db.init_app(app)


login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def ensure_ticket_ai_columns():
    existing_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("ticket")
    }

    columns_to_add = {
        "ai_diagnosis": "TEXT",
        "ai_solution": "TEXT",
        "ai_analyzed": "BOOLEAN DEFAULT 0",
        "ai_error": "TEXT"
    }

    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            db.session.execute(
                text(
                    f"ALTER TABLE ticket ADD COLUMN {column_name} {column_type}"
                )
            )

    db.session.commit()

def ensure_ticket_assigned_to_column():

    existing_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("ticket")
    }

    if "assigned_to" not in existing_columns:

        db.session.execute(
            text(
                "ALTER TABLE ticket "
                "ADD COLUMN assigned_to INTEGER"
            )
        )

        db.session.commit()

def ensure_user_role_column():
    existing_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("user")
    }

    if "role" not in existing_columns:
        db.session.execute(
            text(
                "ALTER TABLE user "
                "ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"
            )
        )
        db.session.commit()


def ensure_user_avatar_column():
    existing_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("user")
    }

    if "avatar" not in existing_columns:
        db.session.execute(
            text(
                "ALTER TABLE user "
                "ADD COLUMN avatar VARCHAR(255)"
            )
        )
        db.session.commit()


with app.app_context():
    db.create_all()
    ensure_user_role_column()
    ensure_user_avatar_column()
    ensure_ticket_ai_columns()
    ensure_ticket_assigned_to_column()


def allowed_avatar_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_AVATAR_EXTENSIONS
    )


def save_avatar(file_storage):
    """Validate and store an uploaded avatar, returning its filename.

    Returns a tuple of (filename, error_message). On success the error
    message is None; on failure the filename is None.
    """
    if not file_storage or file_storage.filename == "":
        return None, None

    original_name = secure_filename(file_storage.filename)

    if not allowed_avatar_file(original_name):
        return None, "Profile picture must be a PNG, JPG, JPEG, GIF, or WEBP image."

    extension = original_name.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{extension}"
    destination = os.path.join(UPLOAD_FOLDER, unique_name)
    file_storage.save(destination)

    return unique_name, None


def remove_avatar(filename):
    """Delete a previously stored avatar file if it exists."""
    if not filename:
        return
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, filename))
    except OSError:
        pass


@app.route("/uploads/avatars/<path:filename>")
def uploaded_avatar(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


def get_agents_with_stats():
    """Return all IT agents with a summary of their assigned tickets."""
    agents = User.query.filter_by(role="agent").order_by(
        User.name.asc()
    ).all()

    team = []
    for agent in agents:
        assigned = Ticket.query.filter_by(assigned_to=agent.id).all()
        active = sum(
            1 for t in assigned if t.status in ("Open", "In Progress")
        )
        resolved = sum(
            1 for t in assigned if t.status in ("Resolved", "Closed")
        )
        team.append({
            "id": agent.id,
            "name": agent.name,
            "email": agent.email,
            "avatar": agent.avatar,
            "total": len(assigned),
            "active": active,
            "resolved": resolved,
        })

    return team


def get_users_with_stats():
    """Return all regular users with a summary of their tickets."""
    users = User.query.filter_by(role="user").order_by(
        User.name.asc()
    ).all()

    people = []
    for user in users:
        tickets = Ticket.query.filter_by(user_id=user.id).all()
        active = sum(
            1 for t in tickets if t.status in ("Open", "In Progress")
        )
        resolved = sum(
            1 for t in tickets if t.status in ("Resolved", "Closed")
        )
        people.append({
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "avatar": user.avatar,
            "total": len(tickets),
            "active": active,
            "resolved": resolved,
        })

    return people


@app.route("/")
@login_required
def home():
    user_tickets = Ticket.query.filter_by(
        user_id=current_user.id
    ).order_by(
        Ticket.created_at.desc()
    ).all()

    return render_template(
        "dashboard.html",
        tickets=user_tickets,
        team=get_agents_with_stats()
    )


@app.route("/admin/agents/create", methods=["GET", "POST"])
@login_required
def create_agent():

    if current_user.role != "admin":
        flash("You are not authorised to create IT agents.")
        return redirect(url_for("home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please complete all fields.")
            return redirect(url_for("create_agent"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return redirect(url_for("create_agent"))

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for("create_agent"))

        avatar_filename, avatar_error = save_avatar(
            request.files.get("avatar")
        )

        if avatar_error:
            flash(avatar_error)
            return redirect(url_for("create_agent"))

        agent = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role="agent",
            avatar=avatar_filename
        )

        db.session.add(agent)
        db.session.commit()

        flash("IT agent account created successfully.")
        return redirect(url_for("admin_dashboard"))

    return render_template("create_agent.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "remove_avatar":
            remove_avatar(current_user.avatar)
            current_user.avatar = None
            db.session.commit()
            flash("Profile picture removed.")
            return redirect(url_for("profile"))

        avatar_file = request.files.get("avatar")

        if not avatar_file or avatar_file.filename == "":
            flash("Please choose an image to upload.")
            return redirect(url_for("profile"))

        avatar_filename, avatar_error = save_avatar(avatar_file)

        if avatar_error:
            flash(avatar_error)
            return redirect(url_for("profile"))

        # Replace any previous avatar file.
        remove_avatar(current_user.avatar)
        current_user.avatar = avatar_filename
        db.session.commit()

        flash("Profile picture updated.")
        return redirect(url_for("profile"))

    return render_template("profile.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("All fields are required.")
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:
            flash("An account with this email already exists.")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        user = User(
            name=name,
            email=email,
            password=hashed_password
        )

        db.session.add(user)
        db.session.commit()

        flash("Account created successfully. Please log in.")

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(
            email=email
        ).first()

        if user and check_password_hash(
            user.password,
            password
        ):
            login_user(user)
            if user .role == "admin":
                return redirect(url_for("admin_dashboard"))
            
            elif user.role == "agent":
                return redirect(url_for("agent_dashboard"))
            
            return redirect(url_for("home"))


        flash("Invalid email or password.")

    return render_template("login.html")


def get_reset_serializer():
    return URLSafeTimedSerializer(
        app.config["SECRET_KEY"],
        salt="password-reset",
    )


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    # Already signed-in users don't need to reset a password.
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    reset_url = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Please enter your email address.")
            return redirect(url_for("forgot_password"))

        user = User.query.filter_by(email=email).first()

        if user:
            token = get_reset_serializer().dumps(user.email)
            reset_url = url_for(
                "reset_password",
                token=token,
                _external=True,
            )
            flash("Reset link generated for your account.")
        else:
            # Do not reveal whether an account exists.
            flash("Reset link generated for your account.")

    return render_template(
        "forgot_password.html",
        reset_url=reset_url,
    )


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    if current_user.is_authenticated:
        return redirect(url_for("home"))

    try:
        email = get_reset_serializer().loads(
            token,
            max_age=3600,  # link valid for 1 hour
        )
    except (SignatureExpired, BadSignature):
        flash("This reset link is invalid or has expired.")
        return redirect(url_for("forgot_password"))

    user = User.query.filter_by(email=email).first()

    if not user:
        flash("This reset link is invalid or has expired.")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Passwords do not match.")
            return render_template("reset_password.html", token=token)

        user.password = generate_password_hash(password)
        db.session.commit()

        flash("Your password has been reset. Please sign in.")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)

@app.route("/tickets/create", methods=["GET", "POST"])
@login_required
def create_ticket():

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Other")
        priority = request.form.get("priority", "Medium")

        if not title or not description:
            flash("Title and description are required.")
            return render_template("create_ticket.html")

        # Create the ticket first
        ticket = Ticket(
            title=title,
            description=description,
            category=category,
            priority=priority,
            user_id=current_user.id
        )

        db.session.add(ticket)

        # Run AI analysis
        analysis = analyze_ticket(
            title,
            description
        )

        if "error" not in analysis:

            # Let AI improve category and priority
            ticket.category = str(analysis.get(
                "category"
            ) or category)

            ticket.priority = str(analysis.get(
                "priority"
            ) or priority)

            ticket.ai_diagnosis = str(analysis.get(
                "diagnosis"
            ) or "")

            # Store solution steps as text safely
            raw_solution = analysis.get("solution")
            if isinstance(raw_solution, list):
                ticket.ai_solution = "\n".join(
                    str(step).strip()
                    for step in raw_solution
                    if str(step).strip()
                )
            elif isinstance(raw_solution, str):
                ticket.ai_solution = raw_solution.strip()
            else:
                ticket.ai_solution = ""

            ticket.ai_analyzed = True
            ticket.ai_error = None

        else:

            ticket.ai_analyzed = False

            ticket.ai_error = str(analysis.get(
                "error"
            ) or "Unknown AI error")

        db.session.commit()

        flash("Ticket created successfully.")

        return redirect(
            url_for(
                "ticket_detail",
                ticket_id=ticket.id
            )
        )

    return render_template(
        "create_ticket.html"
    )

@app.route("/tickets")
@login_required
def tickets():

    user_tickets = Ticket.query.filter_by(
        user_id=current_user.id
    ).order_by(
        Ticket.created_at.desc()
    ).all()

    return render_template(
        "tickets.html",
        tickets=user_tickets
    )

def staff_dashboard_url():
    """Return the correct dashboard for the current staff member."""
    if current_user.role == "agent":
        return url_for("agent_dashboard")
    return url_for("admin_dashboard")


@app.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):

    ticket = db.get_or_404(Ticket, ticket_id)

    # Normal users can only view their own tickets.
    # Admins and agents can view all tickets.
    if (
        ticket.user_id != current_user.id
        and current_user.role not in ("admin", "agent")
    ):
        flash("You are not authorised to view this ticket.")
        return redirect(url_for("tickets"))

    return render_template(
        "ticket.html",
        ticket=ticket
    ) 


@app.route("/admin/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
def update_ticket_status(ticket_id):

    if current_user.role not in ("admin", "agent"):
        flash("You are not authorised to perform this action.")
        return redirect(url_for("home"))

    ticket = db.get_or_404(Ticket, ticket_id)

    new_status = request.form["status"]

    allowed_statuses = [
        "Open",
        "In Progress",
        "Resolved",
        "Closed"
    ]

    if new_status not in allowed_statuses:
        flash("Invalid ticket status.")
        return redirect(staff_dashboard_url())

    ticket.status = new_status

    db.session.commit()

    flash(
        f"Ticket #{ticket.id} status updated to {new_status}."
    )

    return redirect(staff_dashboard_url())

@app.route("/admin/tickets/<int:ticket_id>/assign", methods=["POST"])
@login_required
def assign_ticket(ticket_id):

    if current_user.role not in ("admin", "agent"):
        flash("You are not authorised to assign tickets.")
        return redirect(url_for("home"))

    ticket = Ticket.query.get_or_404(ticket_id)

    agent_id = request.form.get("assigned_to", "").strip()

    if agent_id == "":
        ticket.assigned_to = None
    else:
        try:
            agent_id = int(agent_id)
        except ValueError:
            flash("Invalid agent selection.")
            return redirect(staff_dashboard_url())

        agent = User.query.filter_by(
            id=agent_id,
            role="agent"
        ).first()

        if not agent:
            flash("Selected IT agent does not exist.")
            return redirect(staff_dashboard_url())

        ticket.assigned_to = agent.id

    db.session.commit()

    flash(f"Ticket #{ticket.id} assignment updated.")
    return redirect(staff_dashboard_url())


@app.route("/agent/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
def agent_update_ticket_status(ticket_id):

    if current_user.role != "agent":
        flash("Only IT agents can use this action.")
        return redirect(url_for("home"))

    ticket = Ticket.query.get_or_404(ticket_id)

    if ticket.assigned_to != current_user.id:
        flash("This ticket is not assigned to you.")
        return redirect(url_for("agent_dashboard"))

    new_status = request.form.get("status", "")

    allowed_statuses = [
        "Open",
        "In Progress",
        "Resolved"
    ]

    if new_status not in allowed_statuses:
        flash("Invalid ticket status.")
        return redirect(url_for("agent_dashboard"))

    ticket.status = new_status
    db.session.commit()

    flash(f"Ticket #{ticket.id} updated to {new_status}.")
    return redirect(url_for("agent_dashboard"))


@app.route("/agent")
@login_required
def agent_dashboard():

    if current_user.role != "agent":
        flash("You are not authorised to access the agent dashboard.")
        return redirect(url_for("home"))

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    agent_id = request.args.get("agent_id", "")

    query = Ticket.query

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            db.or_(
                Ticket.title.ilike(search_term),
                Ticket.description.ilike(search_term)
            )
        )

    if status in ["Open", "In Progress", "Resolved", "Closed"]:
        query = query.filter_by(status=status)

    if priority in ["Low", "Medium", "High", "Critical"]:
        query = query.filter_by(priority=priority)

    if agent_id == "unassigned":
        query = query.filter(Ticket.assigned_to.is_(None))
    elif agent_id.isdigit():
        query = query.filter_by(assigned_to=int(agent_id))

    all_tickets = query.order_by(
        Ticket.created_at.desc()
    ).all()

    agents = User.query.filter_by(role="agent").order_by(
        User.name.asc()
    ).all()

    return render_template(
        "agent_dashboard.html",
        tickets=all_tickets,
        total_tickets=Ticket.query.count(),
        open_tickets=Ticket.query.filter_by(status="Open").count(),
        high_priority=Ticket.query.filter(
            Ticket.priority.in_(["High", "Critical"])
        ).count(),
        resolved_tickets=Ticket.query.filter_by(status="Resolved").count(),
        agents=agents,
        users=get_users_with_stats(),
        search=search,
        selected_status=status,
        selected_priority=priority,
        selected_agent_id=agent_id
    )


@app.route("/admin")
@login_required
def admin_dashboard():

    if current_user.role != "admin":
        flash("You are not authorised to access the admin dashboard.")
        return redirect(url_for("home"))

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    agent_id = request.args.get("agent_id", "")

    query = Ticket.query

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            db.or_(
                Ticket.title.ilike(search_term),
                Ticket.description.ilike(search_term)
            )
        )

    if status in ["Open", "In Progress", "Resolved", "Closed"]:
        query = query.filter_by(status=status)

    if priority in ["Low", "Medium", "High", "Critical"]:
        query = query.filter_by(priority=priority)

    if agent_id == "unassigned":
        query = query.filter(Ticket.assigned_to.is_(None))
    elif agent_id.isdigit():
        query = query.filter_by(assigned_to=int(agent_id))

    all_tickets = query.order_by(
        Ticket.created_at.desc()
    ).all()

    agents = User.query.filter_by(role="agent").order_by(
        User.name.asc()
    ).all()

    return render_template(
        "admin_dashboard.html",
        tickets=all_tickets,
        total_tickets=Ticket.query.count(),
        open_tickets=Ticket.query.filter_by(status="Open").count(),
        high_priority=Ticket.query.filter(
            Ticket.priority.in_(["High", "Critical"])
        ).count(),
        resolved_tickets=Ticket.query.filter_by(status="Resolved").count(),
        agents=agents,
        team=get_agents_with_stats(),
        users=get_users_with_stats(),
        search=search,
        selected_status=status,
        selected_priority=priority,
        selected_agent_id=agent_id
    )


@app.route("/admin/analytics")
@login_required
def analytics():

    if current_user.role != "admin":
        flash("You are not authorised to view analytics.")
        return redirect(url_for("home"))

    statuses = ["Open", "In Progress", "Resolved", "Closed"]
    priorities = ["Low", "Medium", "High", "Critical"]

    status_counts = dict(
        db.session.query(Ticket.status, func.count(Ticket.id))
        .group_by(Ticket.status)
        .all()
    )

    priority_counts = dict(
        db.session.query(Ticket.priority, func.count(Ticket.id))
        .group_by(Ticket.priority)
        .all()
    )

    category_rows = (
        db.session.query(Ticket.category, func.count(Ticket.id))
        .group_by(Ticket.category)
        .all()
    )

    agent_rows = (
        db.session.query(User.name, func.count(Ticket.id))
        .join(Ticket, Ticket.assigned_to == User.id)
        .filter(User.role == "agent")
        .group_by(User.id, User.name)
        .all()
    )

    return render_template(
        "analytics.html",
        total_tickets=Ticket.query.count(),
        open_tickets=Ticket.query.filter_by(status="Open").count(),
        resolved_tickets=Ticket.query.filter_by(status="Resolved").count(),
        agent_count=User.query.filter_by(role="agent").count(),
        status_labels=statuses,
        status_values=[status_counts.get(s, 0) for s in statuses],
        priority_labels=priorities,
        priority_values=[priority_counts.get(p, 0) for p in priorities],
        category_labels=[r[0] or "Uncategorised" for r in category_rows],
        category_values=[r[1] for r in category_rows],
        agent_labels=[r[0] for r in agent_rows],
        agent_values=[r[1] for r in agent_rows]
    )


@app.route("/admin/knowledge-base")
@login_required
def knowledge_base():

    if current_user.role != "admin":
        flash("You are not authorised to manage the knowledge base.")
        return redirect(url_for("home"))

    search = request.args.get("search", "").strip()
    selected_category = request.args.get("category", "")

    query = KnowledgeArticle.query.filter_by(is_active=True)

    if search:
        term = f"%{search}%"
        query = query.filter(
            db.or_(
                KnowledgeArticle.title.ilike(term),
                KnowledgeArticle.content.ilike(term)
            )
        )

    valid_categories = [
        "Hardware", "Software", "Network",
        "Account & Access", "Security", "Other"
    ]

    if selected_category in valid_categories:
        query = query.filter_by(category=selected_category)

    articles = query.order_by(
        KnowledgeArticle.updated_at.desc()
    ).all()

    return render_template(
        "knowledge_base.html",
        articles=articles,
        search=search,
        selected_category=selected_category,
        categories=valid_categories
    )


@app.route("/admin/knowledge-base/create", methods=["GET", "POST"])
@login_required
def create_article():

    if current_user.role != "admin":
        flash("You are not authorised to create articles.")
        return redirect(url_for("home"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        content = request.form.get("content", "").strip()

        valid_categories = [
            "Hardware", "Software", "Network",
            "Account & Access", "Security", "Other"
        ]

        if not title or not content or category not in valid_categories:
            flash("Please provide a title, valid category, and instructions.")
            return redirect(url_for("create_article"))

        article = KnowledgeArticle(
            title=title,
            category=category,
            content=content,
            created_by=current_user.id
        )

        db.session.add(article)
        db.session.commit()

        flash("Knowledge article created successfully.")
        return redirect(url_for("knowledge_base"))

    return render_template("create_article.html")


@app.route("/logout")
@login_required
def logout():

    logout_user()

    return redirect(url_for("login"))


if __name__ == "__main__":
    # Port 5000 is often occupied on macOS by "AirPlay Receiver".
    # Use 5001 to avoid the conflict.
    app.run(debug=True, port=5001)


#./venv/bin/python -c "from app import app, db; from models import User; app.app_context().push(); u = User.query.filter_by(email='sushilbhurtel21@gmail.com').first(); u.role = 'admin'; db.session.commit(); print(f'{sushilbhurtel21@gmail.com} is now an admin.')"
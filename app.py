import os
import uuid
import json
from datetime import datetime, timezone, timedelta
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_from_directory,
    jsonify,
    Response,
    stream_with_context,
    abort,
    has_request_context,
)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from sqlalchemy import inspect, text, func
from models import (
    db,
    User,
    Ticket,
    KnowledgeArticle,
    TicketComment,
    ChatConversation,
    ChatMessage,
    AgentChat,
    AgentChatMessage,
    Attachment,
)
from ai_analyzer import (
    analyze_ticket,
    stream_chat,
    suggest_title,
    ollama_is_available,
    draft_agent_reply,
    summarise_thread,
)
import notifications



app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

# The database location can be overridden so the automated tests can run
# against their own throwaway file instead of the real workspace data.
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "HELIX_DATABASE_URI",
    "sqlite:///helpdesk.db",
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Email notifications. Leave MAIL_SERVER empty to disable them.
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = (
    os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
)
app.config["MAIL_USE_SSL"] = (
    os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
)
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get(
    "MAIL_DEFAULT_SENDER",
    "Helix IT Helpdesk <no-reply@helix.local>",
)
app.config["MAIL_TIMEOUT"] = int(os.environ.get("MAIL_TIMEOUT", "10"))
app.config["MAIL_ASYNC"] = True


def send_notification(notify_function, *args):
    """Run a notification helper without ever breaking the request."""
    try:
        notify_function(app, *args)
    except Exception:
        app.logger.exception("Ticket notification failed")

# Profile picture uploads
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "avatars")
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB

# Ticket attachment uploads
ATTACHMENT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "attachments")
ALLOWED_ATTACHMENT_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "txt", "rtf", "csv", "zip", "rar", "7z",
    "png", "jpg", "jpeg", "gif", "webp", "svg"
}
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_TICKET_ATTACHMENTS = 5

app.config["ATTACHMENT_UPLOAD_FOLDER"] = ATTACHMENT_UPLOAD_FOLDER
app.config["MAX_ATTACHMENT_CONTENT_LENGTH"] = MAX_ATTACHMENT_SIZE
app.config["MAX_CONTENT_LENGTH"] = MAX_ATTACHMENT_SIZE * MAX_TICKET_ATTACHMENTS

os.makedirs(ATTACHMENT_UPLOAD_FOLDER, exist_ok=True)


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


def ensure_ticket_sla_columns():

    existing_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("ticket")
    }

    columns_to_add = {
        "due_date": "DATETIME",
        "sla_level": "VARCHAR(20) DEFAULT 'Standard'",
        "sla_breached": "BOOLEAN DEFAULT 0",
        "sla_breach_warning_sent": "BOOLEAN DEFAULT 0",
    }

    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            db.session.execute(
                text(
                    f"ALTER TABLE ticket ADD COLUMN {column_name} {column_type}"
                )
            )

    db.session.commit()


def ensure_ticket_satisfaction_columns():

    existing_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("ticket")
    }

    columns_to_add = {
        "satisfaction_rating": "INTEGER",
        "satisfaction_comment": "TEXT",
        "satisfaction_rated_at": "DATETIME",
    }

    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            db.session.execute(
                text(
                    f"ALTER TABLE ticket ADD COLUMN {column_name} {column_type}"
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
    ensure_ticket_sla_columns()
    ensure_ticket_satisfaction_columns()


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


def allowed_attachment_file(filename):
    """Check if a file has an allowed extension for ticket attachments."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS
    )


def save_attachment(file_storage):
    """Validate and store an upload, returning its Attachment record or error."""
    if not file_storage or not file_storage.filename:
        return None, None

    safe_name = secure_filename(file_storage.filename)
    if not safe_name or not allowed_attachment_file(safe_name):
        return None, "That file type is not allowed."

    stream = file_storage.stream
    stream.seek(0, os.SEEK_END)
    file_size = stream.tell()
    stream.seek(0)
    if file_size > MAX_ATTACHMENT_SIZE:
        return None, "Each attachment must be 10 MB or smaller."

    storage_name = f"{uuid.uuid4().hex}.{safe_name.rsplit('.', 1)[1].lower()}"
    file_storage.save(os.path.join(ATTACHMENT_UPLOAD_FOLDER, storage_name))
    attachment = Attachment(
        filename=storage_name,
        original_filename=safe_name,
        file_size=file_size,
        content_type=file_storage.mimetype or None,
        storage_filename=storage_name,
    )
    return attachment, None


def remove_attachment(filename):
    """Delete a previously stored attachment file if it exists."""
    if not filename:
        return
    try:
        os.remove(os.path.join(ATTACHMENT_UPLOAD_FOLDER, filename))
    except OSError:
        pass


@app.route("/tickets/attachments/<int:attachment_id>")
@login_required
def uploaded_attachment(attachment_id):
    attachment = db.get_or_404(Attachment, attachment_id)
    ticket = attachment.ticket
    if ticket.user_id != current_user.id and current_user.role not in ("admin", "agent"):
        abort(403)
    return send_from_directory(
        ATTACHMENT_UPLOAD_FOLDER,
        attachment.storage_filename,
        as_attachment=True,
        download_name=attachment.original_filename,
    )


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


def delete_user_account(target, actor):
    """
    Delete a user account without destroying the support history.

    Tickets are handed over to the staff member performing the delete so
    the ticket log, analytics and audit trail survive. Ticket comments
    are anonymised rather than removed. The user's AI chat history and
    profile picture are personal data and are deleted outright.
    """
    # Their tickets are reassigned so no support history is lost.
    for ticket in Ticket.query.filter_by(
        user_id=target.id
    ).all():
        ticket.user_id = actor.id

    # Tickets they were working on fall back to unassigned.
    for ticket in Ticket.query.filter_by(
        assigned_to=target.id
    ).all():
        ticket.assigned_to = None

    # Comments stay on the ticket but lose the personal author link.
    for comment in TicketComment.query.filter_by(
        author_id=target.id
    ).all():
        comment.author_id = None

    # Knowledge articles keep their content but lose the author link.
    for article in KnowledgeArticle.query.filter_by(
        created_by=target.id
    ).all():
        article.created_by = None

    # AI assistant conversations are personal and are removed entirely.
    for conversation in ChatConversation.query.filter_by(
        user_id=target.id
    ).all():
        ChatMessage.query.filter_by(
            conversation_id=conversation.id
        ).delete(synchronize_session=False)

        db.session.delete(conversation)

    # Live chats with the support team are personal too, so they go.
    for chat in AgentChat.query.filter_by(user_id=target.id).all():
        AgentChatMessage.query.filter_by(
            chat_id=chat.id
        ).delete(synchronize_session=False)

        db.session.delete(chat)

    # Remove the stored profile picture.
    remove_avatar(target.avatar)

    db.session.delete(target)
    db.session.commit()


@app.route("/staff/users/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):

    if current_user.role not in ("admin", "agent"):
        flash("You are not authorised to delete users.")
        return redirect(url_for("home"))

    target = db.get_or_404(User, user_id)

    # Never leave the workspace without an administrator.
    if target.role == "admin":
        if User.query.filter_by(role="admin").count() <= 1:
            flash("You cannot delete the last administrator.")
            return redirect(staff_dashboard_url())

    # Nobody may remove their own account through the staff tools.
    if target.id == current_user.id:
        flash("You cannot delete your own account.")
        return redirect(staff_dashboard_url())

    # Agents may only remove regular users. Removing staff is an
    # administrator job.
    if current_user.role == "agent" and target.role != "user":
        flash("Only an administrator can delete another staff member.")
        return redirect(staff_dashboard_url())

    name = target.name
    role = target.role

    delete_user_account(target, current_user)

    if role == "user":
        flash(
            f"{name} has been deleted. "
            "Their tickets were reassigned to you."
        )
    else:
        flash(
            f"The {role} account for {name} has been deleted. "
            "Their tickets were reassigned to you."
        )

    return redirect(staff_dashboard_url())


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

            # Send the reset email
            send_email(
                app,
                user.email,
                "Helix - Password Reset Request",
                f"""Hello {user.name},

You've requested a password reset for your Helix account.

Click the link below to reset your password (valid for 1 hour):
{reset_url}

If you didn't request this, please ignore this email.

Thanks,
Helix IT Helpdesk Team"""
            )

            flash("Password reset link sent to your email.")
        else:
            # Do not reveal whether an account exists.
            flash("Password reset link sent to your email.")

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

        uploads = [
            upload for upload in request.files.getlist("attachments")
            if upload and upload.filename
        ]
        if len(uploads) > MAX_TICKET_ATTACHMENTS:
            flash(f"You can attach up to {MAX_TICKET_ATTACHMENTS} files.")
            return render_template("create_ticket.html")

        attachments = []
        for upload in uploads:
            attachment, error = save_attachment(upload)
            if error:
                for saved_attachment in attachments:
                    remove_attachment(saved_attachment.storage_filename)
                flash(error)
                return render_template("create_ticket.html")
            attachments.append(attachment)

        # Create the ticket first
        ticket = Ticket(
            title=title,
            description=description,
            category=category,
            priority=priority,
            user_id=current_user.id
        )
        ticket.attachments.extend(attachments)

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

        if "error" not in analysis:

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
            ticket.ai_error = str(analysis.get("error") or "Unknown AI error")

        # Set SLA due date based on priority
        response_hours = ticket.sla_response_hours
        ticket.due_date = utc_now() + timedelta(hours=response_hours)
        ticket.sla_level = ticket.sla_level_label

        db.session.commit()

        send_notification(
            notifications.notify_ticket_created,
            ticket,
            current_user
        )

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

    comments = TicketComment.query.filter_by(
        ticket_id=ticket.id
    ).order_by(
        TicketComment.id.asc()
    ).all()

    return render_template(
        "ticket.html",
        ticket=ticket,
        comments=comments,
        is_staff=current_user.role in ("admin", "agent")
    )


def build_thread_for_ai(ticket):
    """Flatten a ticket's comments into plain data for the AI."""
    comments = TicketComment.query.filter_by(
        ticket_id=ticket.id
    ).order_by(
        TicketComment.id.asc()
    ).all()

    thread = []

    for comment in comments:
        if comment.is_ai:
            author = "AI assistant"
            role = "assistant"
        elif comment.author is None:
            author = "Former user"
            role = "former user"
        elif comment.author.role in ("admin", "agent"):
            author = comment.author.name
            role = "support agent"
        else:
            author = comment.author.name
            role = "customer"

        thread.append({
            "author": author,
            "role": role,
            "body": comment.body
        })

    return thread


def ticket_info_for_ai(ticket):
    """Flatten a ticket into plain data for the AI."""
    return {
        "id": ticket.id,
        "title": ticket.title,
        "description": ticket.description,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "ai_diagnosis": ticket.ai_diagnosis or "",
        "ai_solution": ticket.ai_solution or "",
    }


@app.route("/tickets/<int:ticket_id>/comments", methods=["POST"])
@login_required
def ticket_add_comment(ticket_id):

    ticket = db.get_or_404(Ticket, ticket_id)

    if (
        ticket.user_id != current_user.id
        and current_user.role not in ("admin", "agent")
    ):
        flash("You are not authorised to reply to this ticket.")
        return redirect(url_for("tickets"))

    body = (request.form.get("body") or "").strip()

    if not body:
        flash("Write a message before posting your reply.")
        return redirect(url_for(
            "ticket_detail",
            ticket_id=ticket.id
        ))

    if len(body) > 5000:
        flash("Your message is too long (5000 characters maximum).")
        return redirect(url_for(
            "ticket_detail",
            ticket_id=ticket.id
        ))

    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=current_user.id,
        body=body
    )

    db.session.add(comment)

    # The first reply from the support team moves the ticket along.
    if (
        current_user.role in ("admin", "agent")
        and ticket.status == "Open"
    ):
        ticket.status = "In Progress"

    db.session.commit()

    send_notification(
        notifications.notify_new_comment,
        ticket,
        comment,
        current_user
    )

    flash("Reply posted.")

    return redirect(url_for(
        "ticket_detail",
        ticket_id=ticket.id
    ))


@app.route("/tickets/<int:ticket_id>/ai-draft", methods=["POST"])
@login_required
def ticket_ai_draft(ticket_id):

    ticket = db.get_or_404(Ticket, ticket_id)

    if current_user.role not in ("admin", "agent"):
        return jsonify({
            "error": "Only support staff can draft replies."
        }), 403

    thread = build_thread_for_ai(ticket)

    articles = find_relevant_articles(
        f"{ticket.title} {ticket.description}"
    )

    kb_context = "\n".join(
        f"- {article.title} ({article.category}): "
        f"{article.content[:600]}"
        for article in articles
    )

    try:
        draft = draft_agent_reply(
            ticket_info_for_ai(ticket),
            thread,
            kb_context
        )
    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 502

    if not draft or not draft.strip():
        return jsonify({
            "error": "The AI did not return a draft. Please try again."
        }), 502

    return jsonify({
        "success": True,
        "draft": draft
    })


@app.route("/tickets/<int:ticket_id>/ai-summary", methods=["POST"])
@login_required
def ticket_ai_summary(ticket_id):

    ticket = db.get_or_404(Ticket, ticket_id)

    if (
        ticket.user_id != current_user.id
        and current_user.role not in ("admin", "agent")
    ):
        return jsonify({
            "error": "You are not authorised to view this ticket."
        }), 403

    thread = build_thread_for_ai(ticket)

    try:
        summary = summarise_thread(
            ticket_info_for_ai(ticket),
            thread
        )
    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 502

    if not summary or not summary.strip():
        return jsonify({
            "error": "The AI did not return a summary. Please try again."
        }), 502

    return jsonify({
        "success": True,
        "summary": summary
    })


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

    old_status = ticket.status
    ticket.status = new_status

    db.session.commit()

    send_notification(
        notifications.notify_status_changed,
        ticket,
        old_status,
        current_user
    )

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
    newly_assigned = None

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

        if ticket.assigned_to != agent.id:
            newly_assigned = agent

        ticket.assigned_to = agent.id

    db.session.commit()

    if newly_assigned:
        send_notification(
            notifications.notify_ticket_assigned,
            ticket,
            newly_assigned,
            current_user
        )

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

    old_status = ticket.status
    ticket.status = new_status
    db.session.commit()

    send_notification(
        notifications.notify_status_changed,
        ticket,
        old_status,
        current_user
    )

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


# ---------------------------------------------------------------------------
# Live chat with a human support agent
# ---------------------------------------------------------------------------

def user_can_chat(chat, user):
    """
    Decide whether a user may take part in a live chat.

    The customer who started it can always join, the assigned agent can
    reply, and an administrator may look in on any conversation.
    """
    if user.id == chat.user_id:
        return True

    if user.role == "admin":
        return True

    if chat.agent_id and user.id == chat.agent_id:
        return True

    return False


def get_agents_for_chat():
    """Return the support agents with their current chat load."""
    agents = User.query.filter_by(role="agent").order_by(
        User.name.asc()
    ).all()

    people = []

    for agent in agents:
        active = AgentChat.query.filter_by(
            agent_id=agent.id,
            status="active"
        ).count()

        people.append({
            "id": agent.id,
            "name": agent.name,
            "avatar": agent.avatar,
            "active_chats": active,
            "is_free": active == 0,
        })

    return people


def agent_chat_url(chat):
    """Return the right chat URL for whoever is viewing it."""
    if current_user.role in ("admin", "agent"):
        return url_for("staff_chat", chat=chat.id)

    return url_for("support_chat", chat=chat.id)


@app.route("/support/chat")
@login_required
def support_chat():
    """The customer side of live chat with the support team."""
    if current_user.role in ("admin", "agent"):
        return redirect(url_for("staff_chat"))

    chats = AgentChat.query.filter_by(
        user_id=current_user.id
    ).order_by(
        AgentChat.updated_at.desc()
    ).all()

    active_chat = None
    chat_id = request.args.get("chat", type=int)

    if chat_id:
        active_chat = db.session.get(AgentChat, chat_id)

        if active_chat and active_chat.user_id != current_user.id:
            active_chat = None

    # Mark the agent's messages as read.
    if active_chat:
        AgentChatMessage.query.filter_by(
            chat_id=active_chat.id,
            is_read=False,
        ).update(
            {"is_read": True},
            synchronize_session=False,
        )

        db.session.commit()

    # The chat can be started from a ticket so the agent has context.
    linked_ticket = None
    ticket_id = request.args.get("ticket", type=int)

    if ticket_id:
        linked_ticket = db.session.get(Ticket, ticket_id)

        if linked_ticket and linked_ticket.user_id != current_user.id:
            linked_ticket = None

    # The customer may pick a specific agent to talk to.
    selected_agent_id = request.args.get("agent", type=int)
    available_agents = get_agents_for_chat()

    if selected_agent_id and not any(
        agent["id"] == selected_agent_id for agent in available_agents
    ):
        selected_agent_id = None

    return render_template(
        "support_chat.html",
        chats=chats,
        active_chat=active_chat,
        agents=available_agents,
        linked_ticket=linked_ticket,
        selected_agent_id=selected_agent_id,
    )


@app.route("/support/chat/start", methods=["POST"])
@login_required
def support_chat_start():
    """Start a new live chat with the support team."""
    if current_user.role in ("admin", "agent"):
        flash("Staff accounts use the support inbox.")
        return redirect(url_for("staff_chat"))

    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    agent_id = request.form.get("agent_id", type=int)
    ticket_id = request.form.get("ticket_id", type=int)

    if not subject:
        subject = "Support request"

    if not body:
        flash("Write a message so the agent knows what you need.")
        return redirect(url_for("support_chat"))

    if len(body) > 4000:
        flash("Your message is too long (4000 characters maximum).")
        return redirect(url_for("support_chat"))

    # The customer can pick a specific agent, or let the first free
    # agent pick the chat up from the waiting queue.
    agent = None

    if agent_id:
        agent = User.query.filter_by(
            id=agent_id,
            role="agent"
        ).first()

    # Only link the chat to a ticket the customer actually owns.
    linked_ticket = None

    if ticket_id:
        linked_ticket = db.session.get(Ticket, ticket_id)

        if linked_ticket and linked_ticket.user_id != current_user.id:
            linked_ticket = None

    chat = AgentChat(
        subject=subject[:200],
        user_id=current_user.id,
        agent_id=agent.id if agent else None,
        status="active" if agent else "waiting",
        ticket_id=linked_ticket.id if linked_ticket else None,
    )

    db.session.add(chat)
    db.session.commit()

    db.session.add(AgentChatMessage(
        chat_id=chat.id,
        sender_id=current_user.id,
        body=body,
        is_read=False,
    ))

    chat.updated_at = datetime.now(timezone.utc)

    db.session.commit()

    flash("Your chat has started. An agent will reply shortly.")
    return redirect(url_for("support_chat", chat=chat.id))


@app.route("/chat/<int:chat_id>/messages", methods=["POST"])
@login_required
def support_chat_send(chat_id):
    """Post a message into a live chat."""
    chat = db.get_or_404(AgentChat, chat_id)

    if not user_can_chat(chat, current_user):
        abort(403)

    if chat.status == "closed":
        flash("This chat has been closed.")
        return redirect(agent_chat_url(chat))

    body = (request.form.get("body") or "").strip()

    if not body:
        flash("Write a message before sending.")
        return redirect(agent_chat_url(chat))

    if len(body) > 4000:
        flash("Your message is too long (4000 characters maximum).")
        return redirect(agent_chat_url(chat))

    db.session.add(AgentChatMessage(
        chat_id=chat.id,
        sender_id=current_user.id,
        body=body,
        is_read=False,
    ))

    chat.updated_at = datetime.now(timezone.utc)

    db.session.commit()

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"success": True})

    return redirect(agent_chat_url(chat))


@app.route("/chat/<int:chat_id>/messages", methods=["GET"])
@login_required
def support_chat_messages(chat_id):
    """Return new messages so the page can poll for updates."""
    chat = db.get_or_404(AgentChat, chat_id)

    if not user_can_chat(chat, current_user):
        abort(403)

    since = request.args.get("since", type=int) or 0

    rows = AgentChatMessage.query.filter(
        AgentChatMessage.chat_id == chat.id,
        AgentChatMessage.id > since,
    ).order_by(
        AgentChatMessage.id.asc()
    ).all()

    # Anything the other side sent is now read.
    for row in rows:
        if row.sender_id != current_user.id:
            row.is_read = True

    db.session.commit()

    return jsonify({
        "messages": [
            {
                "id": row.id,
                "body": row.body,
                "sender_id": row.sender_id,
                "sender_name": (
                    row.sender.name if row.sender else "Former user"
                ),
                "mine": row.sender_id == current_user.id,
                "created_at": (
                    row.created_at.isoformat()
                    if row.created_at else None
                ),
            }
            for row in rows
        ],
        "status": chat.status,
        "agent_name": chat.agent.name if chat.agent else None,
        "subject": chat.subject,
    })


@app.route("/support/chat/<int:chat_id>/close", methods=["POST"])
@login_required
def support_chat_close(chat_id):
    """Let the customer close their own chat."""
    chat = db.get_or_404(AgentChat, chat_id)

    if chat.user_id != current_user.id:
        abort(403)

    chat.status = "closed"
    chat.updated_at = datetime.now(timezone.utc)

    db.session.commit()

    flash("Chat closed.")
    return redirect(url_for("support_chat", chat=chat.id))


@app.route("/staff/chat")
@login_required
def staff_chat():
    """The support team's live chat inbox."""
    if current_user.role not in ("admin", "agent"):
        abort(403)

    waiting = AgentChat.query.filter_by(status="waiting").order_by(
        AgentChat.created_at.asc()
    ).all()

    active = AgentChat.query.filter_by(status="active").order_by(
        AgentChat.updated_at.desc()
    ).all()

    closed = AgentChat.query.filter_by(status="closed").order_by(
        AgentChat.updated_at.desc()
    ).limit(20).all()

    active_chat = None
    chat_id = request.args.get("chat", type=int)

    if chat_id:
        active_chat = db.session.get(AgentChat, chat_id)

        if active_chat and not user_can_chat(active_chat, current_user):
            active_chat = None

    if active_chat:
        AgentChatMessage.query.filter_by(
            chat_id=active_chat.id,
            is_read=False,
        ).update(
            {"is_read": True},
            synchronize_session=False,
        )

        db.session.commit()

    return render_template(
        "staff_chat.html",
        waiting=waiting,
        active=active,
        closed=closed,
        active_chat=active_chat,
        agents=get_agents_for_chat(),
    )


@app.route("/staff/chat/<int:chat_id>/claim", methods=["POST"])
@login_required
def staff_chat_claim(chat_id):
    """Pick up a waiting chat from the queue."""
    if current_user.role not in ("admin", "agent"):
        abort(403)

    chat = db.get_or_404(AgentChat, chat_id)

    if chat.status == "closed":
        flash("This chat has already been closed.")
        return redirect(url_for("staff_chat"))

    if chat.agent_id and chat.agent_id != current_user.id:
        flash("Another agent is already handling this chat.")
        return redirect(url_for("staff_chat"))

    chat.agent_id = current_user.id
    chat.status = "active"
    chat.updated_at = datetime.now(timezone.utc)

    db.session.commit()

    flash("You are now handling this chat.")
    return redirect(url_for("staff_chat", chat=chat.id))


@app.route("/staff/chat/<int:chat_id>/close", methods=["POST"])
@login_required
def staff_chat_close(chat_id):
    """Close a live chat once the issue is resolved."""
    if current_user.role not in ("admin", "agent"):
        abort(403)

    chat = db.get_or_404(AgentChat, chat_id)

    if not user_can_chat(chat, current_user):
        abort(403)

    chat.status = "closed"
    chat.updated_at = datetime.now(timezone.utc)

    db.session.commit()

    flash("Chat closed.")
    return redirect(url_for("staff_chat"))


# ---------------------------------------------------------------------------
# AI assistant chat
# ---------------------------------------------------------------------------

AI_SUGGESTIONS = [
    "My laptop will not connect to Wi-Fi",
    "I forgot my password and cannot sign in",
    "My computer is running very slowly",
    "Outlook cannot send or receive email",
    "I need to set up a VPN on my phone",
]

STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "you", "your",
    "are", "was", "were", "not", "but", "can", "cant", "cannot",
    "have", "has", "had", "get", "got", "its", "it's", "from",
    "how", "what", "when", "why", "who", "there", "their", "they",
    "will", "would", "could", "should", "been", "being", "into",
    "about", "after", "before", "just", "like", "some", "than",
    "then", "them", "these", "those", "my", "me", "i", "we", "our",
}


def tokenise(text):
    """Split text into lowercase keywords, ignoring common stop words."""
    import re

    words = re.findall(r"[a-z0-9]+", (text or "").lower())

    return [
        word for word in words
        if word not in STOP_WORDS and len(word) > 2
    ]


def find_relevant_articles(query, limit=3):
    """Return knowledge base articles that best match the query."""
    keywords = set(tokenise(query))

    if not keywords:
        return []

    articles = KnowledgeArticle.query.filter_by(
        is_active=True
    ).all()

    scored = []

    for article in articles:
        haystack = f"{article.title} {article.content}".lower()
        score = sum(
            1 for keyword in keywords if keyword in haystack
        )

        if score:
            scored.append((score, article))

    scored.sort(key=lambda item: item[0], reverse=True)

    return [article for _, article in scored[:limit]]


def build_assistant_context(conversation, query, user_id=None):
    """
    Assemble the extra context handed to the AI assistant.

    Includes relevant knowledge base articles, the linked ticket and a
    summary of the user's open tickets so the AI can give advice that
    fits the user's actual situation.
    """
    sections = []

    articles = find_relevant_articles(query)

    if articles:
        lines = [
            f"- {article.title} ({article.category}): "
            f"{article.content[:600]}"
            for article in articles
        ]
        sections.append(
            "REFERENCE KNOWLEDGE BASE ARTICLES:\n" + "\n".join(lines)
        )

    if conversation and conversation.ticket_id:
        ticket = db.session.get(Ticket, conversation.ticket_id)

        if ticket:
            ticket_lines = [
                f"Ticket #{ticket.id}: {ticket.title}",
                f"Category: {ticket.category}",
                f"Priority: {ticket.priority}",
                f"Status: {ticket.status}",
                f"Description: {ticket.description}",
            ]

            if ticket.ai_analyzed and ticket.ai_diagnosis:
                ticket_lines.append(
                    f"Earlier AI diagnosis: {ticket.ai_diagnosis}"
                )

            if ticket.ai_solution:
                ticket_lines.append(
                    f"Earlier AI suggested steps:\n{ticket.ai_solution}"
                )

            sections.append(
                "TICKET THIS CONVERSATION IS ABOUT:\n"
                + "\n".join(ticket_lines)
            )

    if user_id is None and has_request_context():
        if current_user.is_authenticated:
            user_id = current_user.id

    if user_id is not None:
        open_tickets = Ticket.query.filter(
            Ticket.user_id == user_id,
            Ticket.status.in_(["Open", "In Progress"])
        ).order_by(
            Ticket.created_at.desc()
        ).limit(5).all()

        if open_tickets:
            ticket_lines = [
                f"- #{ticket.id} [{ticket.status}] {ticket.title}"
                for ticket in open_tickets
            ]
            sections.append(
                "OTHER OPEN TICKETS BELONGING TO THIS USER:\n"
                + "\n".join(ticket_lines)
            )

    return "\n\n".join(sections)


def get_conversation_or_none(conversation_id):
    """Fetch a conversation owned by the current user, or None."""
    if not conversation_id:
        return None

    conversation = db.session.get(ChatConversation, conversation_id)

    if conversation and conversation.user_id != current_user.id:
        return None

    return conversation


@app.route("/assistant")
@login_required
def assistant():

    conversations = ChatConversation.query.filter_by(
        user_id=current_user.id
    ).order_by(
        ChatConversation.updated_at.desc()
    ).all()

    conversation = get_conversation_or_none(
        request.args.get("conversation", type=int)
    )

    messages = []

    if conversation:
        messages = ChatMessage.query.filter_by(
            conversation_id=conversation.id
        ).order_by(
            ChatMessage.id.asc()
        ).all()

    return render_template(
        "assistant.html",
        conversations=conversations,
        conversation=conversation,
        messages=messages,
        suggestions=AI_SUGGESTIONS,
        ollama_online=ollama_is_available(),
    )


@app.route("/assistant/api/chat", methods=["POST"])
@login_required
def assistant_chat_api():

    payload = request.get_json(silent=True) or {}

    message = (payload.get("message") or "").strip()

    if not message:
        return jsonify({
            "error": "Please type a message before sending."
        }), 400

    if len(message) > 4000:
        return jsonify({
            "error": "That message is too long. Please keep it under 4000 characters."
        }), 400

    conversation = get_conversation_or_none(
        payload.get("conversation_id")
    )

    is_new = conversation is None

    if is_new:
        conversation = ChatConversation(
            user_id=current_user.id,
            title=suggest_title(message),
            ticket_id=payload.get("ticket_id"),
        )
        db.session.add(conversation)
        db.session.commit()

    db.session.add(ChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=message
    ))
    db.session.commit()

    # Build the model history from the stored conversation.
    history_rows = ChatMessage.query.filter_by(
        conversation_id=conversation.id
    ).order_by(
        ChatMessage.id.asc()
    ).all()

    history = [
        {
            "role": row.role,
            "content": row.content
        }
        for row in history_rows[-20:]
    ]

    context = build_assistant_context(
        conversation,
        message,
        current_user.id
    )

    conversation_id = conversation.id
    conversation_title = conversation.title

    def generate():
        collected = []

        def emit(payload):
            """Serialise one NDJSON event for the browser."""
            return json.dumps(payload) + "\n"

        try:
            for chunk in stream_chat(history, context):
                collected.append(chunk)
                yield emit({
                    "type": "chunk",
                    "content": chunk
                })
        except Exception as error:
            yield emit({
                "type": "error",
                "message": str(error)
            })
            return

        reply = "".join(collected).strip()

        if not reply:
            yield emit({
                "type": "error",
                "message": "The AI assistant returned an empty reply. Please try again."
            })
            return

        db.session.add(ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=reply
        ))
        db.session.commit()

        yield emit({
            "type": "done",
            "conversation_id": conversation_id,
            "title": conversation_title,
            "is_new": is_new
        })

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/assistant/api/rename", methods=["POST"])
@login_required
def assistant_rename_api():

    payload = request.get_json(silent=True) or {}

    conversation = get_conversation_or_none(
        payload.get("conversation_id")
    )

    if not conversation:
        return jsonify({"error": "Conversation not found."}), 404

    title = (payload.get("title") or "").strip()

    if not title:
        return jsonify({"error": "A title is required."}), 400

    conversation.title = title[:120]
    db.session.commit()

    return jsonify({"success": True, "title": conversation.title})


@app.route("/assistant/api/delete", methods=["POST"])
@login_required
def assistant_delete_api():

    payload = request.get_json(silent=True) or {}

    conversation = get_conversation_or_none(
        payload.get("conversation_id")
    )

    if not conversation:
        return jsonify({"error": "Conversation not found."}), 404

    db.session.delete(conversation)
    db.session.commit()

    return jsonify({"success": True})


@app.route("/assistant/api/escalate", methods=["POST"])
@login_required
def assistant_escalate_api():

    payload = request.get_json(silent=True) or {}

    conversation = get_conversation_or_none(
        payload.get("conversation_id")
    )

    if not conversation:
        return jsonify({"error": "Conversation not found."}), 404

    if conversation.ticket_id:
        return jsonify({
            "success": True,
            "ticket_id": conversation.ticket_id,
            "already_linked": True,
        })

    messages = ChatMessage.query.filter_by(
        conversation_id=conversation.id
    ).order_by(
        ChatMessage.id.asc()
    ).all()

    if not messages:
        return jsonify({
            "error": "There is nothing to escalate yet."
        }), 400

    transcript_lines = []

    for row in messages:
        speaker = "User" if row.role == "user" else "AI assistant"
        transcript_lines.append(f"{speaker}: {row.content}")

    description = (
        "Raised from an AI assistant conversation.\n\n"
        + "\n\n".join(transcript_lines)
    )

    ticket = Ticket(
        title=conversation.title[:200],
        description=description,
        category="Other",
        priority="Medium",
        user_id=current_user.id
    )

    db.session.add(ticket)

    analysis = analyze_ticket(ticket.title, ticket.description)

    if "error" not in analysis:
        ticket.category = str(analysis.get("category") or ticket.category)
        ticket.priority = str(analysis.get("priority") or ticket.priority)
        ticket.ai_diagnosis = str(analysis.get("diagnosis") or "")

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
        ticket.ai_error = str(
            analysis.get("error") or "Unknown AI error"
        )

    db.session.commit()

    conversation.ticket_id = ticket.id
    db.session.commit()

    send_notification(
        notifications.notify_ticket_created,
        ticket,
        current_user
    )

    return jsonify({
        "success": True,
        "ticket_id": ticket.id,
    })


@app.route("/assistant/ticket/<int:ticket_id>")
@login_required
def assistant_from_ticket(ticket_id):

    ticket = db.get_or_404(Ticket, ticket_id)

    if (
        ticket.user_id != current_user.id
        and current_user.role not in ("admin", "agent")
    ):
        flash("You are not authorised to view this ticket.")
        return redirect(url_for("tickets"))

    conversation = ChatConversation(
        user_id=current_user.id,
        title=f"Ticket #{ticket.id}: {ticket.title}"[:120],
        ticket_id=ticket.id,
    )

    db.session.add(conversation)
    db.session.commit()

    return redirect(url_for(
        "assistant",
        conversation=conversation.id
    ))


@app.route("/tickets/<int:ticket_id>/rate", methods=["POST"])
@login_required
def rate_ticket(ticket_id):

    ticket = db.get_or_404(Ticket, ticket_id)

    # Only the ticket owner can rate
    if ticket.user_id != current_user.id:
        flash("You are not authorised to rate this ticket.")
        return redirect(url_for("ticket_detail", ticket_id=ticket.id))

    rating = request.form.get("rating", type=int)
    comment = (request.form.get("comment") or "").strip()

    if not rating or rating < 1 or rating > 5:
        flash("Please provide a valid rating between 1 and 5.")
        return redirect(url_for("ticket_detail", ticket_id=ticket.id))

    ticket.satisfaction_rating = rating
    ticket.satisfaction_comment = comment if comment else None
    ticket.satisfaction_rated_at = datetime.now(timezone.utc)

    db.session.commit()

    flash(f"Thank you for rating this ticket {rating}/5!")

    return redirect(url_for("ticket_detail", ticket_id=ticket.id))


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

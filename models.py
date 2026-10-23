from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone, timedelta


def utc_now():
    return datetime.now(timezone.utc)


db = SQLAlchemy()


class User(UserMixin, db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(100),
        nullable=False
    )

    email = db.Column(
        db.String(120),
        unique=True,
        nullable=False
    )

    password = db.Column(
        db.String(200),
        nullable=False
    )

    role = db.Column(
        db.String(20),
        default="user",
        nullable=False
    )

    avatar = db.Column(
        db.String(255),
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    tickets = db.relationship(
        "Ticket",
        foreign_keys="Ticket.user_id",
        backref="user",
        lazy=True
    )

    assigned_tickets = db.relationship(
        "Ticket",
        foreign_keys="Ticket.assigned_to",
        backref="assigned_agent",
        lazy=True
    )


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String(200), nullable=False)

    description = db.Column(
        db.Text,
        nullable=False
    )

    category = db.Column(
        db.String(50),
        default="Uncategorised"
    )

    priority = db.Column(
        db.String(20),
        default="Medium"
    )

    status = db.Column(
        db.String(30),
        default="Open"
    )

    # SLA and due date tracking
    due_date = db.Column(
        db.DateTime,
        nullable=True
    )

    sla_level = db.Column(
        db.String(20),
        default="Standard"
    )

    sla_breached = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    sla_breach_warning_sent = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    # Customer satisfaction rating
    satisfaction_rating = db.Column(
        db.Integer,
        nullable=True
    )

    satisfaction_comment = db.Column(
        db.Text,
        nullable=True
    )

    satisfaction_rated_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # AI-generated information
    ai_diagnosis = db.Column(
        db.Text,
        nullable=True
    )

    ai_solution = db.Column(
        db.Text,
        nullable=True
    )

    ai_analyzed = db.Column(
        db.Boolean,
        default=False
    )

    ai_error = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    assigned_to = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    comments = db.relationship(
        "TicketComment",
        backref="ticket",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="TicketComment.id"
    )

    attachments = db.relationship(
        "Attachment",
        backref="ticket",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="Attachment.id"
    )

    @property
    def sla_response_hours(self):
        """Return the SLA response time in hours based on priority."""
        mapping = {
            "Critical": 4,
            "High": 8,
            "Medium": 24,
            "Low": 48,
        }
        return mapping.get(self.priority, 24)

    @property
    def sla_level_label(self):
        """Return a human-readable SLA level label."""
        mapping = {
            "Critical": "Critical",
            "High": "Expedited",
            "Medium": "Standard",
            "Low": "Standard",
        }
        return mapping.get(self.priority, "Standard")

    @property
    def is_sla_overdue(self):
        """Return True if the ticket is past its due date and not resolved."""
        if self.sla_breached:
            return True
        if self.due_date and self.status not in ("Resolved", "Closed"):
            return utc_now() > self.due_date
        return False

    @property
    def is_sla_due_soon(self):
        """Return True if the ticket is due within the next 4 hours and not resolved."""
        if self.status in ("Resolved", "Closed"):
            return False
        if not self.due_date:
            return False
        now = utc_now()
        return now < self.due_date <= now + timedelta(hours=4)

    @property
    def time_remaining(self):
        """Return a human-readable time remaining string, or None if no due date."""
        if not self.due_date:
            return None
        now = utc_now()
        delta = self.due_date - now
        if delta.total_seconds() <= 0:
            return "Overdue"
        total_minutes = int(delta.total_seconds() // 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 0:
            return f"{hours}h {minutes}m remaining"
        return f"{minutes}m remaining"

    @property
    def sla_status(self):
        """Return the SLA status string: 'overdue', 'due_soon', 'on_track', or None."""
        if self.status in ("Resolved", "Closed"):
            return "resolved"
        if self.is_sla_overdue:
            return "overdue"
        if self.is_sla_due_soon:
            return "due_soon"
        return "on_track"


class Attachment(db.Model):
    """A file attached to a support ticket."""

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    filename = db.Column(
        db.String(255),
        nullable=False
    )

    original_filename = db.Column(
        db.String(255),
        nullable=False
    )

    file_size = db.Column(
        db.Integer,
        nullable=False
    )

    content_type = db.Column(
        db.String(100),
        nullable=True
    )

    uploaded_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey("ticket.id"),
        nullable=False
    )

    # The file is stored in the uploads directory with a unique name.
    storage_filename = db.Column(
        db.String(255),
        nullable=False
    )


class KnowledgeArticle(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    title = db.Column(
        db.String(200),
        nullable=False
    )

    category = db.Column(
        db.String(50),
        nullable=False
    )

    content = db.Column(
        db.Text,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    created_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False
    )


class TicketComment(db.Model):
    """A message in the conversation thread attached to a ticket."""

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    body = db.Column(
        db.Text,
        nullable=False
    )

    # True when the comment was drafted by the AI assistant.
    is_ai = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey("ticket.id"),
        nullable=False
    )

    # The author of the message. NULL for AI generated content.
    author_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    author = db.relationship(
        "User",
        foreign_keys=[author_id],
        lazy=True
    )


class ChatConversation(db.Model):
    """A live conversation between a user and the AI assistant."""

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    title = db.Column(
        db.String(120),
        nullable=False,
        default="New conversation"
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    updated_at = db.Column(
        db.DateTime,
        default=utc_now,
        onupdate=utc_now
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    # Optional link to a support ticket for contextual conversations.
    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey("ticket.id"),
        nullable=True
    )

    messages = db.relationship(
        "ChatMessage",
        backref="conversation",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ChatMessage.id"
    )


class ChatMessage(db.Model):
    """A single message inside an AI assistant conversation."""

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    role = db.Column(
        db.String(20),
        nullable=False
    )

    content = db.Column(
        db.Text,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("chat_conversation.id"),
        nullable=False
    )


class AgentChat(db.Model):
    """A live chat between a customer and a human support agent."""

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    subject = db.Column(
        db.String(200),
        nullable=False,
        default="Support request"
    )

    # "waiting" = no agent has picked it up yet, "active" = an agent
    # is chatting, "closed" = the conversation has finished.
    status = db.Column(
        db.String(20),
        nullable=False,
        default="waiting"
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    updated_at = db.Column(
        db.DateTime,
        default=utc_now,
        onupdate=utc_now
    )

    # The customer who started the chat.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    # The support agent handling it. NULL while it is still waiting.
    agent_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    # Optional link back to the ticket being discussed.
    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey("ticket.id"),
        nullable=True
    )

    messages = db.relationship(
        "AgentChatMessage",
        backref="chat",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="AgentChatMessage.id"
    )

    customer = db.relationship(
        "User",
        foreign_keys=[user_id],
        lazy=True
    )

    agent = db.relationship(
        "User",
        foreign_keys=[agent_id],
        lazy=True
    )


class AgentChatMessage(db.Model):
    """A single message inside a customer to agent chat."""

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    body = db.Column(
        db.Text,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=utc_now
    )

    # False until the other side has seen it.
    is_read = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    chat_id = db.Column(
        db.Integer,
        db.ForeignKey("agent_chat.id"),
        nullable=False
    )

    sender_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    sender = db.relationship(
        "User",
        foreign_keys=[sender_id],
        lazy=True
    )
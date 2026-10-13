from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone


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

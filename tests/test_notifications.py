import unittest
from unittest import mock

from werkzeug.security import generate_password_hash

# Points the app at the throwaway test database before it gets imported.
import tests  # noqa: F401

import app as app_module
import notifications
from app import app, db
from models import User, Ticket, TicketComment


class NotificationTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True

        self.original_server = app.config["MAIL_SERVER"]
        app.config["MAIL_SERVER"] = "smtp.test.local"

        # Capture outgoing mail instead of opening a real connection.
        self.sent = []
        patcher = mock.patch.object(
            notifications,
            "_deliver",
            side_effect=lambda config, message: self.sent.append(message),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.customer_id = self.ensure_user(
            "notify-customer@example.com", "Notify Customer", "user"
        )
        self.agent_id = self.ensure_user(
            "notify-agent@example.com", "Notify Agent", "agent"
        )
        self.admin_id = self.ensure_user(
            "notify-admin@example.com", "Notify Admin", "admin"
        )

        with app.app_context():
            ticket = Ticket(
                title="Notification test ticket",
                description="Cannot print.",
                category="Hardware",
                priority="Medium",
                status="Open",
                user_id=self.customer_id,
                assigned_to=self.agent_id,
            )
            db.session.add(ticket)
            db.session.commit()
            self.ticket_id = ticket.id

    def tearDown(self):
        app.config["MAIL_SERVER"] = self.original_server

        with app.app_context():
            ids = [
                t.id for t in Ticket.query.filter_by(
                    title="Notification test ticket"
                ).all()
            ]

            if ids:
                TicketComment.query.filter(
                    TicketComment.ticket_id.in_(ids)
                ).delete(synchronize_session=False)
                Ticket.query.filter(
                    Ticket.id.in_(ids)
                ).delete(synchronize_session=False)

            db.session.commit()

    def ensure_user(self, email, name, role):
        with app.app_context():
            user = User.query.filter_by(email=email).first()

            if not user:
                user = User(
                    name=name,
                    email=email,
                    password=generate_password_hash("password"),
                    role=role,
                )
                db.session.add(user)
                db.session.commit()

            return user.id

    def login(self, client, email):
        client.post(
            "/login",
            data={"email": email, "password": "password"},
            follow_redirects=True,
        )

    # --- Replies ---------------------------------------------------------

    def test_agent_reply_emails_the_customer(self):
        with app.test_client() as client:
            self.login(client, "notify-agent@example.com")
            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Try restarting the spooler."},
            )

        self.assertEqual(len(self.sent), 1)
        message = self.sent[0]
        self.assertEqual(message["To"], "notify-customer@example.com")
        self.assertIn(f"[Ticket #{self.ticket_id}]", message["Subject"])
        self.assertIn("Try restarting the spooler.", message.get_content())
        self.assertIn(f"/tickets/{self.ticket_id}", message.get_content())

    def test_customer_reply_emails_the_assigned_agent(self):
        with app.test_client() as client:
            self.login(client, "notify-customer@example.com")
            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "That did not help."},
            )

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(
            self.sent[0]["To"], "notify-agent@example.com"
        )

    def test_customer_reply_on_unassigned_ticket_sends_nothing(self):
        with app.app_context():
            ticket = db.session.get(Ticket, self.ticket_id)
            ticket.assigned_to = None
            db.session.commit()

        with app.test_client() as client:
            self.login(client, "notify-customer@example.com")
            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Anyone there?"},
            )

        self.assertEqual(self.sent, [])

    # --- Status ----------------------------------------------------------

    def test_status_change_emails_the_customer(self):
        with app.test_client() as client:
            self.login(client, "notify-admin@example.com")
            client.post(
                f"/admin/tickets/{self.ticket_id}/status",
                data={"status": "Resolved"},
            )

        self.assertEqual(len(self.sent), 1)
        message = self.sent[0]
        self.assertEqual(message["To"], "notify-customer@example.com")
        self.assertIn("Resolved", message["Subject"])

    def test_unchanged_status_sends_nothing(self):
        with app.test_client() as client:
            self.login(client, "notify-admin@example.com")
            client.post(
                f"/admin/tickets/{self.ticket_id}/status",
                data={"status": "Open"},
            )

        self.assertEqual(self.sent, [])

    def test_agent_status_change_emails_the_customer(self):
        with app.test_client() as client:
            self.login(client, "notify-agent@example.com")
            client.post(
                f"/agent/tickets/{self.ticket_id}/status",
                data={"status": "In Progress"},
            )

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(
            self.sent[0]["To"], "notify-customer@example.com"
        )

    # --- Assignment ------------------------------------------------------

    def test_assignment_emails_the_new_agent(self):
        with app.app_context():
            ticket = db.session.get(Ticket, self.ticket_id)
            ticket.assigned_to = None
            db.session.commit()

        with app.test_client() as client:
            self.login(client, "notify-admin@example.com")
            client.post(
                f"/admin/tickets/{self.ticket_id}/assign",
                data={"assigned_to": str(self.agent_id)},
            )

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(
            self.sent[0]["To"], "notify-agent@example.com"
        )

    def test_reassigning_to_same_agent_sends_nothing(self):
        with app.test_client() as client:
            self.login(client, "notify-admin@example.com")
            client.post(
                f"/admin/tickets/{self.ticket_id}/assign",
                data={"assigned_to": str(self.agent_id)},
            )

        self.assertEqual(self.sent, [])

    # --- Configuration and resilience ------------------------------------

    def test_no_email_when_mail_server_not_configured(self):
        app.config["MAIL_SERVER"] = ""

        with app.test_client() as client:
            self.login(client, "notify-agent@example.com")
            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Hello"},
            )

        self.assertEqual(self.sent, [])

    def test_mail_failure_does_not_break_the_request(self):
        with mock.patch.object(
            notifications, "_deliver", side_effect=OSError("smtp down")
        ):
            with app.test_client() as client:
                self.login(client, "notify-agent@example.com")
                response = client.post(
                    f"/tickets/{self.ticket_id}/comments",
                    data={"body": "Still saved"},
                    follow_redirects=True,
                )

        self.assertEqual(response.status_code, 200)

        with app.app_context():
            self.assertEqual(
                TicketComment.query.filter_by(
                    ticket_id=self.ticket_id
                ).count(),
                1,
            )

    def test_ticket_created_email_goes_to_the_owner(self):
        with app.test_request_context("/"):
            with app.app_context():
                ticket = db.session.get(Ticket, self.ticket_id)
                owner = db.session.get(User, self.customer_id)
                app_module.send_notification(
                    notifications.notify_ticket_created, ticket, owner
                )

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(
            self.sent[0]["To"], "notify-customer@example.com"
        )


if __name__ == "__main__":
    unittest.main()

import json
import unittest

from werkzeug.security import generate_password_hash

# Points the app at the throwaway test database before it gets imported.
import tests  # noqa: F401

import app as app_module
from app import app, db
from models import User, Ticket, TicketComment


class TicketThreadTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True

        self.original_draft = app_module.draft_agent_reply
        self.original_summarise = app_module.summarise_thread

        app_module.draft_agent_reply = fake_draft_agent_reply
        app_module.summarise_thread = fake_summarise_thread

        self.customer_id = self.ensure_user(
            "thread-customer@example.com",
            "Thread Customer",
            role="user",
        )

        self.agent_id = self.ensure_user(
            "thread-agent@example.com",
            "Thread Agent",
            role="agent",
        )

        with app.app_context():
            self.ticket = Ticket(
                title="Thread test ticket",
                description="The printer keeps jamming.",
                category="Hardware",
                priority="Medium",
                status="Open",
                user_id=self.customer_id,
            )
            db.session.add(self.ticket)
            db.session.commit()
            self.ticket_id = self.ticket.id

    def tearDown(self):
        with app.app_context():
            ticket_ids = [
                row.id
                for row in Ticket.query.filter(
                    Ticket.title == "Thread test ticket"
                ).all()
            ]

            if ticket_ids:
                TicketComment.query.filter(
                    TicketComment.ticket_id.in_(ticket_ids)
                ).delete(synchronize_session=False)

                Ticket.query.filter(
                    Ticket.id.in_(ticket_ids)
                ).delete(synchronize_session=False)

            db.session.commit()

        app_module.draft_agent_reply = self.original_draft
        app_module.summarise_thread = self.original_summarise

    def ensure_user(self, email, name, role="user"):
        """Create the user if needed and return their id."""
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
        return client.post(
            "/login",
            data={"email": email, "password": "password"},
            follow_redirects=True,
        )

    # --- Posting comments ------------------------------------------------

    def test_comment_requires_login(self):
        with app.test_client() as client:
            response = client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Hello"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_customer_can_reply_to_own_ticket(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "It started after the last update."},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)

        with app.app_context():
            comment = TicketComment.query.filter_by(
                ticket_id=self.ticket_id
            ).first()

            self.assertIsNotNone(comment)
            self.assertEqual(
                comment.body,
                "It started after the last update."
            )
            self.assertEqual(comment.author_id, self.customer_id)
            self.assertFalse(comment.is_ai)

    def test_agent_reply_is_shown_on_the_ticket_page(self):
        with app.test_client() as client:
            self.login(client, "thread-agent@example.com")

            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Please try restarting the print spooler."},
                follow_redirects=True,
            )

            page = client.get(f"/tickets/{self.ticket_id}")

        html = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn("Please try restarting the print spooler.", html)
        self.assertIn("Thread Agent", html)
        self.assertIn("Support", html)

    def test_first_agent_reply_moves_ticket_to_in_progress(self):
        with app.test_client() as client:
            self.login(client, "thread-agent@example.com")

            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Looking into this now."},
                follow_redirects=True,
            )

        with app.app_context():
            ticket = db.session.get(Ticket, self.ticket_id)
            self.assertEqual(ticket.status, "In Progress")

    def test_customer_reply_does_not_change_status(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "Any update?"},
                follow_redirects=True,
            )

        with app.app_context():
            ticket = db.session.get(Ticket, self.ticket_id)
            self.assertEqual(ticket.status, "Open")

    def test_empty_comment_is_rejected(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "   "},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Write a message",
            response.get_data(as_text=True)
        )

        with app.app_context():
            self.assertEqual(
                TicketComment.query.filter_by(
                    ticket_id=self.ticket_id
                ).count(),
                0
            )

    def test_overlong_comment_is_rejected(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "x" * 5001},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "too long",
            response.get_data(as_text=True).lower()
        )

        with app.app_context():
            self.assertEqual(
                TicketComment.query.filter_by(
                    ticket_id=self.ticket_id
                ).count(),
                0
            )

    def test_cannot_comment_on_another_users_ticket(self):
        other_id = self.ensure_user(
            "thread-other@example.com",
            "Other Person",
        )

        with app.app_context():
            their_ticket = Ticket(
                title="Thread test ticket",
                description="Not yours.",
                user_id=other_id,
            )
            db.session.add(their_ticket)
            db.session.commit()
            their_ticket_id = their_ticket.id

        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            response = client.post(
                f"/tickets/{their_ticket_id}/comments",
                data={"body": "Let me in."},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "not authorised",
            response.get_data(as_text=True).lower()
        )

        with app.app_context():
            self.assertEqual(
                TicketComment.query.filter_by(
                    ticket_id=their_ticket_id
                ).count(),
                0
            )

    def test_ticket_owner_cannot_see_another_users_ticket(self):
        other_id = self.ensure_user(
            "thread-other2@example.com",
            "Other Person Two",
        )

        with app.app_context():
            their_ticket = Ticket(
                title="Thread test ticket",
                description="Private.",
                user_id=other_id,
            )
            db.session.add(their_ticket)
            db.session.commit()
            their_ticket_id = their_ticket.id

        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")
            response = client.get(f"/tickets/{their_ticket_id}")

        self.assertEqual(response.status_code, 302)

    # --- AI drafting ------------------------------------------------------

    def test_regular_user_cannot_request_an_ai_draft(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/ai-draft",
                json={},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.get_json())

    def test_agent_can_request_an_ai_draft(self):
        with app.test_client() as client:
            self.login(client, "thread-agent@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/ai-draft",
                json={},
            )

        self.assertEqual(response.status_code, 200)

        data = response.get_json()

        self.assertTrue(data["success"])
        self.assertIn("restart", data["draft"].lower())

    def test_ai_draft_receives_ticket_and_thread_context(self):
        with app.test_client() as client:
            self.login(client, "thread-agent@example.com")

            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "The customer already rebooted twice."},
                follow_redirects=True,
            )

            client.post(
                f"/tickets/{self.ticket_id}/ai-draft",
                json={},
            )

        self.assertIn("The printer keeps jamming.", LAST_DRAFT_CALL["ticket"]["description"])
        self.assertIn(
            "The customer already rebooted twice.",
            " ".join(item["body"] for item in LAST_DRAFT_CALL["thread"]),
        )

    def test_ai_summary_is_available_to_the_ticket_owner(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/ai-summary",
                json={},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.get_json())

    def test_ai_summary_is_blocked_for_other_users(self):
        self.ensure_user(
            "thread-outsider@example.com",
            "Outsider",
        )

        with app.test_client() as client:
            self.login(client, "thread-outsider@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/ai-summary",
                json={},
            )

        self.assertEqual(response.status_code, 403)

    def test_agent_can_summarise_any_ticket(self):
        """Support staff can read every ticket, so the summary is allowed."""
        with app.test_client() as client:
            self.login(client, "thread-agent@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/ai-summary",
                json={},
            )

        self.assertEqual(response.status_code, 200)

    def test_ai_draft_failure_returns_a_readable_error(self):
        app_module.draft_agent_reply = failing_draft

        with app.test_client() as client:
            self.login(client, "thread-agent@example.com")

            response = client.post(
                f"/tickets/{self.ticket_id}/ai-draft",
                json={},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn(
            "engine is unavailable",
            response.get_json()["error"]
        )

    # --- Housekeeping -----------------------------------------------------

    def test_deleting_a_ticket_removes_its_comments(self):
        with app.test_client() as client:
            self.login(client, "thread-customer@example.com")

            client.post(
                f"/tickets/{self.ticket_id}/comments",
                data={"body": "A reply that should be cleaned up."},
                follow_redirects=True,
            )

        with app.app_context():
            ticket = db.session.get(Ticket, self.ticket_id)
            db.session.delete(ticket)
            db.session.commit()

            self.assertEqual(
                TicketComment.query.filter_by(
                    ticket_id=self.ticket_id
                ).count(),
                0
            )


LAST_DRAFT_CALL = {}


def fake_draft_agent_reply(ticket_info, thread, kb_context=""):
    """Stand-in for the Ollama reply drafting engine."""
    LAST_DRAFT_CALL["ticket"] = ticket_info
    LAST_DRAFT_CALL["thread"] = thread
    LAST_DRAFT_CALL["kb_context"] = kb_context

    return (
        "Hi, thanks for reporting this.\n\n"
        "1. Restart the print spooler.\n"
        "2. Print a test page.\n\n"
        "Let me know the outcome."
    )


def fake_summarise_thread(ticket_info, thread):
    """Stand-in for the Ollama thread summariser."""
    return (
        "The customer reported a jamming printer. "
        "No action has been taken yet. "
        "Next step is to restart the print spooler."
    )


def failing_draft(ticket_info, thread, kb_context=""):
    """Simulate the local AI engine being unavailable."""
    raise RuntimeError("The AI engine is unavailable.")


if __name__ == "__main__":
    unittest.main()

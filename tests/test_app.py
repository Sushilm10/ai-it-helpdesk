import unittest

from werkzeug.security import generate_password_hash

# Points the app at the throwaway test database before it gets imported.
import tests  # noqa: F401

import app as app_module
from app import app, db
from models import Ticket, User


class HelpdeskRouteTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True

        self.original_analyze_ticket = app_module.analyze_ticket
        app_module.analyze_ticket = lambda title, description: {
            "category": "Network",
            "priority": "High",
            "diagnosis": "Test AI diagnosis.",
            "solution": [
                "Check the connection.",
                "Restart the device."
            ]
        }

        with app.app_context():
            self.user = User.query.filter_by(
                email="route-test@example.com"
            ).first()

            if not self.user:
                self.user = User(
                    name="Route Test",
                    email="route-test@example.com",
                    password=generate_password_hash("password")
                )
                db.session.add(self.user)
                db.session.commit()

            self.user_id = self.user.id

            self.admin = User.query.filter_by(
                email="admin-test@example.com"
            ).first()

            if not self.admin:
                self.admin = User(
                    name="Admin Test",
                    email="admin-test@example.com",
                    password=generate_password_hash("password"),
                    role="admin"
                )
                db.session.add(self.admin)
                db.session.commit()

            self.admin_id = self.admin.id

    def tearDown(self):
        with app.app_context():
            Ticket.query.filter_by(
                title="Route test ticket",
                user_id=self.user_id
            ).delete()
            Ticket.query.filter_by(
                title="Admin status test ticket",
                user_id=self.user_id
            ).delete()
            db.session.commit()

        app_module.analyze_ticket = self.original_analyze_ticket

    def login(self, client):
        return client.post(
            "/login",
            data={
                "email": "route-test@example.com",
                "password": "password"
            },
            follow_redirects=True
        )

    def login_admin(self, client):
        return client.post(
            "/login",
            data={
                "email": "admin-test@example.com",
                "password": "password"
            },
            follow_redirects=True
        )

    def test_home_dashboard_is_user_dashboard(self):
        with app.test_client() as client:
            response = self.login(client)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/tickets/create", response.data)
        self.assertIn(b"/tickets", response.data)
        self.assertNotIn(b"Dashboard Statistics", response.data)

    def test_create_ticket_shows_ai_analysis(self):
        with app.test_client() as client:
            self.login(client)

            response = client.post(
                "/tickets/create",
                data={
                    "title": "Route test ticket",
                    "description": "Testing ticket creation.",
                    "category": "Other",
                    "priority": "Low"
                },
                follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Test AI diagnosis.", response.data)
        self.assertIn(b"Check the connection.", response.data)

    def test_admin_dashboard_requires_admin_role(self):
        with app.test_client() as client:
            self.login(client)
            response = client.get("/admin", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_admin_dashboard_renders_for_admin(self):
        with app.test_client() as client:
            self.login_admin(client)
            response = client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Support overview", response.data)
        self.assertIn(b"Support tickets", response.data)

    def test_admin_can_update_ticket_status(self):
        with app.app_context():
            ticket = Ticket(
                title="Admin status test ticket",
                description="Testing admin status updates.",
                category="Other",
                priority="Medium",
                user_id=self.user_id
            )
            db.session.add(ticket)
            db.session.commit()
            ticket_id = ticket.id

        with app.test_client() as client:
            self.login_admin(client)
            response = client.post(
                f"/admin/tickets/{ticket_id}/status",
                data={"status": "Resolved"},
                follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)

        with app.app_context():
            ticket = db.session.get(Ticket, ticket_id)
            self.assertEqual(ticket.status, "Resolved")

    def test_create_ticket_with_null_solution(self):
        app_module.analyze_ticket = lambda title, desc: {
            "category": "Hardware",
            "priority": "Low",
            "diagnosis": "Peripheral unplugged",
            "solution": None
        }

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/tickets/create",
                data={
                    "title": "Route test ticket",
                    "description": "Mouse not working.",
                    "category": "Hardware",
                    "priority": "Low"
                },
                follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ticket created successfully.", response.data)
        self.assertIn(b"Peripheral unplugged", response.data)
        self.assertIn(b"No solution steps provided.", response.data)

    def test_create_ticket_with_ai_error(self):
        app_module.analyze_ticket = lambda title, desc: {
            "error": "Ollama service unavailable."
        }

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/tickets/create",
                data={
                    "title": "Route test ticket",
                    "description": "VPN issue.",
                    "category": "Network",
                    "priority": "Medium"
                },
                follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ticket created successfully.", response.data)
        self.assertIn(b"Ollama service unavailable.", response.data)

    def test_admin_status_update_displays_flash_message(self):
        with app.app_context():
            ticket = Ticket(
                title="Admin status test ticket",
                description="Testing flash message.",
                category="Other",
                priority="Medium",
                user_id=self.user_id
            )
            db.session.add(ticket)
            db.session.commit()
            ticket_id = ticket.id

        with app.test_client() as client:
            self.login_admin(client)
            response = client.post(
                f"/admin/tickets/{ticket_id}/status",
                data={"status": "Closed"},
                follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"status updated to Closed", response.data)


if __name__ == "__main__":
    unittest.main()


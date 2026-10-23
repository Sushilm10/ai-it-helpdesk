import unittest

from werkzeug.security import generate_password_hash

# Points the app at the throwaway test database before it gets imported.
import tests  # noqa: F401

from app import app, db
from models import (
    User,
    Ticket,
    TicketComment,
    KnowledgeArticle,
    ChatConversation,
    ChatMessage,
)


class UserDeletionTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True

        self.admin_id = self.ensure_user(
            "delete-admin@example.com",
            "Delete Admin",
            role="admin",
        )

        self.second_admin_id = self.ensure_user(
            "delete-admin2@example.com",
            "Second Admin",
            role="admin",
        )

        self.agent_id = self.ensure_user(
            "delete-agent@example.com",
            "Delete Agent",
            role="agent",
        )

        self.customer_id = self.ensure_user(
            "delete-customer@example.com",
            "Delete Customer",
            role="user",
        )

        self.victim_id = self.ensure_user(
            "delete-victim@example.com",
            "Delete Victim",
            role="user",
        )

        self.victim_agent_id = self.ensure_user(
            "delete-victim-agent@example.com",
            "Delete Victim Agent",
            role="agent",
        )

    def tearDown(self):
        with app.app_context():
            # Only clean up the rows these tests create so the real
            # workspace data is left untouched.
            ticket_ids = [
                row.id
                for row in Ticket.query.filter(
                    Ticket.title == "Delete test ticket"
                ).all()
            ]

            if ticket_ids:
                TicketComment.query.filter(
                    TicketComment.ticket_id.in_(ticket_ids)
                ).delete(synchronize_session=False)

                Ticket.query.filter(
                    Ticket.id.in_(ticket_ids)
                ).delete(synchronize_session=False)

            KnowledgeArticle.query.filter(
                KnowledgeArticle.title == "Resetting a printer"
            ).delete(synchronize_session=False)

            for conversation in ChatConversation.query.filter(
                ChatConversation.title == "Private chat"
            ).all():
                ChatMessage.query.filter_by(
                    conversation_id=conversation.id
                ).delete(synchronize_session=False)

                db.session.delete(conversation)

            db.session.commit()

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

    def make_ticket(self, owner_id, title="Delete test ticket"):
        """Create a ticket owned by the given user."""
        with app.app_context():
            ticket = Ticket(
                title=title,
                description="Something is broken.",
                category="Hardware",
                priority="Medium",
                status="Open",
                user_id=owner_id,
            )
            db.session.add(ticket)
            db.session.commit()

            return ticket.id

    # -- permissions ---------------------------------------------------

    def test_regular_user_cannot_delete_anyone(self):
        """A customer has no delete rights at all."""
        with app.test_client() as client:
            self.login(client, "delete-customer@example.com")

            response = client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            still_there = db.session.get(User, self.victim_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(still_there)

    def test_agent_can_delete_a_regular_user(self):
        """An agent may remove a customer account."""
        with app.test_client() as client:
            self.login(client, "delete-agent@example.com")

            response = client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            deleted = db.session.get(User, self.victim_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(deleted)
        self.assertIn("has been deleted", body)

    def test_admin_can_delete_a_regular_user(self):
        """An admin may remove a customer account."""
        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            response = client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            deleted = db.session.get(User, self.victim_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(deleted)

    def test_agent_cannot_delete_another_agent(self):
        """Agents may not remove staff accounts."""
        with app.test_client() as client:
            self.login(client, "delete-agent@example.com")

            response = client.post(
                f"/staff/users/{self.victim_agent_id}/delete",
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            still_there = db.session.get(User, self.victim_agent_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(still_there)
        self.assertIn("Only an administrator", body)

    def test_admin_can_delete_an_agent(self):
        """An admin may remove an IT agent account."""
        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            response = client.post(
                f"/staff/users/{self.victim_agent_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            deleted = db.session.get(User, self.victim_agent_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(deleted)

    def test_staff_cannot_delete_themselves(self):
        """Nobody can remove their own account from the staff tools."""
        with app.test_client() as client:
            self.login(client, "delete-agent@example.com")

            response = client.post(
                f"/staff/users/{self.agent_id}/delete",
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            still_there = db.session.get(User, self.agent_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(still_there)
        self.assertIn("cannot delete your own account", body)

    def test_last_admin_cannot_be_deleted(self):
        """The workspace must always keep at least one administrator."""
        demoted = {}

        with app.app_context():
            # Isolate the test so the test admin is the only one left,
            # then put everyone else back afterwards.
            for other in User.query.filter(
                User.role == "admin",
                User.id != self.admin_id,
            ).all():
                demoted[other.id] = other.role
                other.role = "user"

            db.session.commit()

        try:
            with app.test_client() as client:
                self.login(client, "delete-admin@example.com")

                response = client.post(
                    f"/staff/users/{self.admin_id}/delete",
                    follow_redirects=True,
                )

                body = response.get_data(as_text=True)

            with app.app_context():
                still_there = db.session.get(User, self.admin_id)

            self.assertEqual(response.status_code, 200)
            self.assertIsNotNone(still_there)
            self.assertIn("last administrator", body)
        finally:
            with app.app_context():
                for user_id, role in demoted.items():
                    user = db.session.get(User, user_id)

                    if user:
                        user.role = role

                db.session.commit()

    def test_deleting_a_missing_user_returns_404(self):
        """An unknown user id is reported as not found."""
        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            response = client.post("/staff/users/999999/delete")

        self.assertEqual(response.status_code, 404)

    # -- data handling -------------------------------------------------

    def test_tickets_are_reassigned_to_the_deleting_staff(self):
        """Support history survives the account removal."""
        ticket_id = self.make_ticket(self.victim_id)

        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            ticket = db.session.get(Ticket, ticket_id)

            self.assertIsNotNone(ticket)
            self.assertEqual(ticket.user_id, self.admin_id)

    def test_comments_are_kept_but_anonymised(self):
        """Conversation history is preserved without the personal link."""
        ticket_id = self.make_ticket(self.victim_id)

        with app.app_context():
            comment = TicketComment(
                ticket_id=ticket_id,
                author_id=self.victim_id,
                body="My printer keeps jamming.",
            )
            db.session.add(comment)
            db.session.commit()
            comment_id = comment.id

        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            comment = db.session.get(TicketComment, comment_id)

            self.assertIsNotNone(comment)
            self.assertIsNone(comment.author_id)
            self.assertEqual(comment.body, "My printer keeps jamming.")

    def test_assigned_tickets_are_unassigned(self):
        """Tickets assigned to the removed agent become unassigned."""
        ticket_id = self.make_ticket(self.customer_id)

        with app.app_context():
            ticket = db.session.get(Ticket, ticket_id)
            ticket.assigned_to = self.victim_agent_id
            db.session.commit()

        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            client.post(
                f"/staff/users/{self.victim_agent_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            ticket = db.session.get(Ticket, ticket_id)

            self.assertIsNotNone(ticket)
            self.assertIsNone(ticket.assigned_to)

    def test_chat_conversations_are_removed(self):
        """The removed user's AI chat history is deleted outright."""
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.victim_id,
                title="Private chat",
            )
            db.session.add(conversation)
            db.session.commit()

            conversation_id = conversation.id

            message = ChatMessage(
                conversation_id=conversation_id,
                role="user",
                content="My laptop will not connect to Wi-Fi",
            )
            db.session.add(message)
            db.session.commit()
            message_id = message.id

        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            self.assertIsNone(
                db.session.get(ChatConversation, conversation_id)
            )
            self.assertIsNone(db.session.get(ChatMessage, message_id))

    def test_knowledge_articles_survive_without_the_author(self):
        """Articles keep their content but lose the author link."""
        with app.app_context():
            article = KnowledgeArticle(
                title="Resetting a printer",
                category="Hardware",
                content="Turn the printer off, wait 30 seconds, turn it on.",
                created_by=self.victim_id,
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

        with app.app_context():
            article = db.session.get(KnowledgeArticle, article_id)

            self.assertIsNotNone(article)
            self.assertIsNone(article.created_by)
            self.assertIn("Resetting a printer", article.title)

    # -- user interface ------------------------------------------------

    def test_admin_dashboard_shows_delete_buttons(self):
        """The admin overview offers a delete control for each member."""
        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            response = client.get("/admin")

            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'action="/staff/users/{self.customer_id}/delete"',
            body,
        )
        self.assertIn("member-delete-button", body)

    def test_agent_dashboard_shows_delete_buttons(self):
        """The agent workspace offers a delete control for each user."""
        with app.test_client() as client:
            self.login(client, "delete-agent@example.com")

            response = client.get("/agent")

            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'action="/staff/users/{self.customer_id}/delete"',
            body,
        )
        self.assertIn("member-delete-button", body)

    def test_regular_user_dashboard_has_no_delete_buttons(self):
        """A customer never sees a delete control."""
        with app.test_client() as client:
            self.login(client, "delete-customer@example.com")

            response = client.get("/")

            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("member-delete-button", body)

    def test_ticket_page_labels_anonymised_comments(self):
        """A comment from a removed account shows as 'Former user'."""
        ticket_id = self.make_ticket(self.customer_id)

        with app.app_context():
            comment = TicketComment(
                ticket_id=ticket_id,
                author_id=self.victim_id,
                body="This reply came from a removed account.",
            )
            db.session.add(comment)
            db.session.commit()

        # Remove the account that wrote the comment.
        with app.test_client() as client:
            self.login(client, "delete-admin@example.com")

            client.post(
                f"/staff/users/{self.victim_id}/delete",
                follow_redirects=True,
            )

            response = client.get(f"/tickets/{ticket_id}")

            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Former user", body)
        self.assertIn("This reply came from a removed account.", body)


if __name__ == "__main__":
    unittest.main()

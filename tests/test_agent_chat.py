import json
import unittest

from werkzeug.security import generate_password_hash

# Points the app at the throwaway test database before it gets imported.
import tests  # noqa: F401

from app import app, db
from models import (
    User,
    Ticket,
    AgentChat,
    AgentChatMessage,
)


class AgentChatTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True

        self.customer_id = self.ensure_user(
            "chat-customer@example.com",
            "Chat Customer",
            role="user",
        )

        self.other_customer_id = self.ensure_user(
            "chat-other-customer@example.com",
            "Other Chat Customer",
            role="user",
        )

        self.agent_id = self.ensure_user(
            "chat-agent@example.com",
            "Chat Agent",
            role="agent",
        )

        self.other_agent_id = self.ensure_user(
            "chat-other-agent@example.com",
            "Other Chat Agent",
            role="agent",
        )

        self.admin_id = self.ensure_user(
            "chat-admin@example.com",
            "Chat Admin",
            role="admin",
        )

    def tearDown(self):
        with app.app_context():
            chats = AgentChat.query.filter(
                AgentChat.subject.like("Live chat test%")
            ).all()

            chat_ids = [chat.id for chat in chats]

            if chat_ids:
                AgentChatMessage.query.filter(
                    AgentChatMessage.chat_id.in_(chat_ids)
                ).delete(synchronize_session=False)

                AgentChat.query.filter(
                    AgentChat.id.in_(chat_ids)
                ).delete(synchronize_session=False)

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

    def make_chat(self, subject="Live chat test chat", agent_id=None,
                  user_id=None, status="active"):
        """Create a chat directly in the database."""
        with app.app_context():
            chat = AgentChat(
                subject=subject,
                user_id=user_id or self.customer_id,
                agent_id=agent_id,
                status=status,
            )
            db.session.add(chat)
            db.session.commit()

            chat_id = chat.id

            db.session.add(AgentChatMessage(
                chat_id=chat_id,
                sender_id=user_id or self.customer_id,
                body="Hello, I need some help please.",
            ))
            db.session.commit()

            return chat_id

    # -- starting a chat ----------------------------------------------

    def test_customer_can_start_a_chat_in_the_queue(self):
        """Without picking an agent the chat waits in the queue."""
        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            response = client.post(
                "/support/chat/start",
                data={
                    "subject": "Live chat test queue",
                    "body": "My printer is offline.",
                },
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = AgentChat.query.filter_by(
                subject="Live chat test queue"
            ).first()

            self.assertIsNotNone(chat)
            self.assertEqual(chat.status, "waiting")
            self.assertIsNone(chat.agent_id)
            self.assertEqual(chat.user_id, self.customer_id)
            self.assertEqual(len(chat.messages), 1)
            self.assertEqual(
                chat.messages[0].body, "My printer is offline."
            )

        self.assertIn("Your chat has started", body)

    def test_customer_can_pick_a_specific_agent(self):
        """Choosing an agent starts the chat straight away."""
        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            client.post(
                "/support/chat/start",
                data={
                    "subject": "Live chat test picked agent",
                    "body": "I need to reset my password.",
                    "agent_id": str(self.agent_id),
                },
                follow_redirects=True,
            )

        with app.app_context():
            chat = AgentChat.query.filter_by(
                subject="Live chat test picked agent"
            ).first()

            self.assertIsNotNone(chat)
            self.assertEqual(chat.status, "active")
            self.assertEqual(chat.agent_id, self.agent_id)

    def test_customer_cannot_start_an_empty_chat(self):
        """A chat needs an opening message."""
        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            response = client.post(
                "/support/chat/start",
                data={
                    "subject": "Live chat test empty",
                    "body": "   ",
                },
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = AgentChat.query.filter_by(
                subject="Live chat test empty"
            ).first()

            self.assertIsNone(chat)

        self.assertIn("Write a message", body)

    def test_staff_cannot_start_a_customer_chat(self):
        """Staff accounts are redirected to the support inbox."""
        with app.test_client() as client:
            self.login(client, "chat-agent@example.com")

            response = client.post(
                "/support/chat/start",
                data={
                    "subject": "Live chat test staff start",
                    "body": "Hello?",
                },
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = AgentChat.query.filter_by(
                subject="Live chat test staff start"
            ).first()

            self.assertIsNone(chat)

        self.assertIn("support inbox", body)

    def test_chat_can_be_linked_to_an_owned_ticket(self):
        """A chat started from a ticket keeps the link."""
        with app.app_context():
            ticket = Ticket(
                title="Live chat test ticket",
                description="The scanner is not connecting.",
                category="Hardware",
                priority="Medium",
                status="Open",
                user_id=self.customer_id,
            )
            db.session.add(ticket)
            db.session.commit()

            ticket_id = ticket.id

        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            client.post(
                "/support/chat/start",
                data={
                    "subject": "Live chat test linked",
                    "body": "Following up on my ticket.",
                    "ticket_id": str(ticket_id),
                },
                follow_redirects=True,
            )

        with app.app_context():
            chat = AgentChat.query.filter_by(
                subject="Live chat test linked"
            ).first()

            self.assertIsNotNone(chat)
            self.assertEqual(chat.ticket_id, ticket_id)

            Ticket.query.filter_by(id=ticket_id).delete(
                synchronize_session=False
            )
            db.session.commit()

    def test_chat_cannot_be_linked_to_someone_elses_ticket(self):
        """A customer cannot attach a chat to another person's ticket."""
        with app.app_context():
            ticket = Ticket(
                title="Live chat test other ticket",
                description="Someone else's issue.",
                category="Hardware",
                priority="Medium",
                status="Open",
                user_id=self.other_customer_id,
            )
            db.session.add(ticket)
            db.session.commit()

            ticket_id = ticket.id

        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            client.post(
                "/support/chat/start",
                data={
                    "subject": "Live chat test bad link",
                    "body": "Trying to attach the wrong ticket.",
                    "ticket_id": str(ticket_id),
                },
                follow_redirects=True,
            )

        with app.app_context():
            chat = AgentChat.query.filter_by(
                subject="Live chat test bad link"
            ).first()

            self.assertIsNotNone(chat)
            self.assertIsNone(chat.ticket_id)

            Ticket.query.filter_by(id=ticket_id).delete(
                synchronize_session=False
            )
            db.session.commit()

    # -- the staff inbox -----------------------------------------------

    def test_agent_can_claim_a_waiting_chat(self):
        """Claiming a chat assigns it to the agent."""
        chat_id = self.make_chat(
            subject="Live chat test to claim",
            agent_id=None,
            status="waiting",
        )

        with app.test_client() as client:
            self.login(client, "chat-agent@example.com")

            response = client.post(
                f"/staff/chat/{chat_id}/claim",
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = db.session.get(AgentChat, chat_id)

            self.assertEqual(chat.status, "active")
            self.assertEqual(chat.agent_id, self.agent_id)

        self.assertIn("You are now handling this chat", body)

    def test_second_agent_cannot_claim_a_claimed_chat(self):
        """Once an agent owns a chat nobody else can take it."""
        chat_id = self.make_chat(
            subject="Live chat test already claimed",
            agent_id=self.agent_id,
            status="active",
        )

        with app.test_client() as client:
            self.login(client, "chat-other-agent@example.com")

            response = client.post(
                f"/staff/chat/{chat_id}/claim",
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = db.session.get(AgentChat, chat_id)

            self.assertEqual(chat.agent_id, self.agent_id)

        self.assertIn("already handling this chat", body)

    def test_staff_inbox_lists_waiting_chats(self):
        """The queue shows chats that nobody has picked up."""
        chat_id = self.make_chat(
            subject="Live chat test in queue",
            agent_id=None,
            status="waiting",
        )

        with app.test_client() as client:
            self.login(client, "chat-agent@example.com")

            response = client.get("/staff/chat")

            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Live chat test in queue", body)
        self.assertIn(f"/staff/chat/{chat_id}/claim", body)

    def test_customer_cannot_open_the_staff_inbox(self):
        """The support inbox is closed to customers."""
        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            response = client.get("/staff/chat")

        self.assertEqual(response.status_code, 403)

    def test_staff_are_redirected_from_the_customer_chat_page(self):
        """Staff land in their own inbox instead."""
        with app.test_client() as client:
            self.login(client, "chat-agent@example.com")

            response = client.get("/support/chat")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/staff/chat", response.headers["Location"])

    # -- sending messages ----------------------------------------------

    def test_agent_can_reply_to_their_chat(self):
        """The assigned agent can post a reply."""
        chat_id = self.make_chat(
            subject="Live chat test reply",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-agent@example.com")

            client.post(
                f"/chat/{chat_id}/messages",
                data={"body": "I am on it, please restart the router."},
                follow_redirects=True,
            )

        with app.app_context():
            chat = db.session.get(AgentChat, chat_id)

            self.assertEqual(len(chat.messages), 2)
            self.assertEqual(
                chat.messages[1].body,
                "I am on it, please restart the router.",
            )
            self.assertEqual(chat.messages[1].sender_id, self.agent_id)

    def test_customer_can_reply_in_their_chat(self):
        """The customer can post a follow-up."""
        chat_id = self.make_chat(
            subject="Live chat test customer reply",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            client.post(
                f"/chat/{chat_id}/messages",
                data={"body": "Thanks, that did not work."},
                follow_redirects=True,
            )

        with app.app_context():
            chat = db.session.get(AgentChat, chat_id)

            self.assertEqual(len(chat.messages), 2)
            self.assertEqual(
                chat.messages[1].body,
                "Thanks, that did not work.",
            )

    def test_empty_message_is_rejected(self):
        """A blank message is not stored."""
        chat_id = self.make_chat(
            subject="Live chat test blank message",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            response = client.post(
                f"/chat/{chat_id}/messages",
                data={"body": "   "},
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = db.session.get(AgentChat, chat_id)

            self.assertEqual(len(chat.messages), 1)

        self.assertIn("Write a message", body)

    def test_closed_chat_cannot_receive_messages(self):
        """Once a chat is closed it is read only."""
        chat_id = self.make_chat(
            subject="Live chat test closed message",
            agent_id=self.agent_id,
            status="closed",
        )

        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            response = client.post(
                f"/chat/{chat_id}/messages",
                data={"body": "One more thing."},
                follow_redirects=True,
            )

            body = response.get_data(as_text=True)

        with app.app_context():
            chat = db.session.get(AgentChat, chat_id)

            self.assertEqual(len(chat.messages), 1)

        self.assertIn("This chat has been closed", body)

    # -- access control -------------------------------------------------

    def test_another_customer_cannot_read_the_chat(self):
        """Chats are private to their owner and staff."""
        chat_id = self.make_chat(
            subject="Live chat test private",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-other-customer@example.com")

            response = client.get(f"/chat/{chat_id}/messages")

        self.assertEqual(response.status_code, 403)

    def test_unassigned_agent_cannot_read_the_chat(self):
        """An agent who does not own the chat stays out."""
        chat_id = self.make_chat(
            subject="Live chat test not mine",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-other-agent@example.com")

            response = client.get(f"/chat/{chat_id}/messages")

        self.assertEqual(response.status_code, 403)

    def test_admin_can_read_any_chat(self):
        """Administrators can look in on any conversation."""
        chat_id = self.make_chat(
            subject="Live chat test admin view",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-admin@example.com")

            response = client.get(f"/chat/{chat_id}/messages")

            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Live chat test admin view", body)

    def test_another_customer_cannot_open_the_chat_page(self):
        """The chat page is protected as well."""
        chat_id = self.make_chat(
            subject="Live chat test private page",
            agent_id=self.agent_id,
        )

        with app.test_client() as client:
            self.login(client, "chat-other-customer@example.com")

            response = client.post(
                f"/chat/{chat_id}/messages",
                data={"body": "Let me in."},
            )

        self.assertEqual(response.status_code, 403)

    # -- polling --------------------------------------------------------

    def test_polling_returns_only_new_messages(self):
        """The poll endpoint sends back messages after the given id."""
        chat_id = self.make_chat(
            subject="Live chat test polling",
            agent_id=self.agent_id,
        )

        with app.app_context():
            first_id = AgentChatMessage.query.filter_by(
                chat_id=chat_id
            ).first().id

            db.session.add(AgentChatMessage(
                chat_id=chat_id,
                sender_id=self.agent_id,
                body="A brand new message.",
            ))
            db.session.commit()

        with app.test_client() as client:
            self.login(client, "chat-customer@example.com")

            response = client.get(
                f"/chat/{chat_id}/messages?since={first_id}"
            )

            data = json.loads(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["body"], "A brand new message.")
        self.assertFalse(data["messages"][0]["mine"])
        self.assertEqual(data["agent_name"], "Chat Agent")


if __name__ == "__main__":
    unittest.main()

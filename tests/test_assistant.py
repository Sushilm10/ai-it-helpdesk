import json
import re
import unittest

from werkzeug.security import generate_password_hash

# Points the app at the throwaway test database before it gets imported.
import tests  # noqa: F401

import app as app_module
from app import app, db
from models import (
    Ticket,
    User,
    KnowledgeArticle,
    ChatConversation,
    ChatMessage,
)


class AssistantRouteTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True

        self.original_stream_chat = app_module.stream_chat
        self.original_analyze_ticket = app_module.analyze_ticket

        self.original_ollama_available = app_module.ollama_is_available
        app_module.ollama_is_available = lambda: True

        app_module.stream_chat = fake_stream_chat
        app_module.analyze_ticket = lambda title, description: {
            "category": "Network",
            "priority": "High",
            "diagnosis": "Escalated diagnosis.",
            "solution": [
                "Restart the network adapter.",
                "Reconnect to the network."
            ]
        }

        with app.app_context():
            self.user = User.query.filter_by(
                email="assistant-test@example.com"
            ).first()

            if not self.user:
                self.user = User(
                    name="Assistant Test",
                    email="assistant-test@example.com",
                    password=generate_password_hash("password")
                )
                db.session.add(self.user)
                db.session.commit()

            self.user_id = self.user.id

            self.other = User.query.filter_by(
                email="assistant-other@example.com"
            ).first()

            if not self.other:
                self.other = User(
                    name="Other Test",
                    email="assistant-other@example.com",
                    password=generate_password_hash("password")
                )
                db.session.add(self.other)
                db.session.commit()

            self.other_id = self.other.id

    def tearDown(self):
        with app.app_context():
            conversation_ids = [
                row.id for row in ChatConversation.query.all()
            ]

            if conversation_ids:
                ChatMessage.query.filter(
                    ChatMessage.conversation_id.in_(conversation_ids)
                ).delete(synchronize_session=False)
                ChatConversation.query.filter(
                    ChatConversation.id.in_(conversation_ids)
                ).delete(synchronize_session=False)

            Ticket.query.filter(
                Ticket.title.like("%Assistant escalation%")
            ).delete(synchronize_session=False)

            KnowledgeArticle.query.filter(
                KnowledgeArticle.title == "Resetting a laptop network stack"
            ).delete(synchronize_session=False)

            db.session.commit()

        app_module.stream_chat = self.original_stream_chat
        app_module.analyze_ticket = self.original_analyze_ticket
        app_module.ollama_is_available = self.original_ollama_available

    def login(self, client, email="assistant-test@example.com"):
        return client.post(
            "/login",
            data={
                "email": email,
                "password": "password"
            },
            follow_redirects=True
        )

    # --- Page -----------------------------------------------------------

    def test_assistant_requires_login(self):
        with app.test_client() as client:
            response = client.get("/assistant")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_assistant_page_renders_for_user(self):
        with app.test_client() as client:
            self.login(client)
            response = client.get("/assistant")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"AI assistant", response.data)
        self.assertIn(b"How can I help?", response.data)
        self.assertIn(b"New conversation", response.data)

    def test_new_chat_keeps_action_buttons_in_markup(self):
        """The rename/escalate buttons must exist even before a chat starts."""
        with app.test_client() as client:
            self.login(client)
            response = client.get("/assistant")

        html = response.get_data(as_text=True)

        for button_id in ("renameConversationButton", "escalateButton"):
            match = re.search(
                r"<button[^>]*id=\"" + button_id + r"\"[^>]*>",
                html,
                re.DOTALL,
            )

            self.assertIsNotNone(
                match,
                f"{button_id} missing from the assistant page"
            )

            # They start hidden because there is no conversation yet.
            self.assertIn(
                "hidden",
                match.group(0),
                f"{button_id} should be hidden for a new chat"
            )

    def test_existing_chat_shows_action_buttons(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.user_id,
                title="Existing chat"
            )
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)
            response = client.get(
                f"/assistant?conversation={conversation_id}"
            )

        html = response.get_data(as_text=True)

        for button_id in ("renameConversationButton", "escalateButton"):
            match = re.search(
                r"<button[^>]*id=\"" + button_id + r"\"[^>]*>",
                html,
                re.DOTALL,
            )

            self.assertIsNotNone(match)
            self.assertNotIn(
                "hidden",
                match.group(0),
                f"{button_id} should be visible for an existing chat"
            )

    # --- Chat -----------------------------------------------------------

    def test_chat_rejects_empty_message(self):
        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/chat",
                json={"message": "   "}
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "type a message",
            response.get_json()["error"].lower()
        )

    def test_chat_streams_reply_and_persists_messages(self):
        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/chat",
                json={"message": "My Wi-Fi is not working"}
            )

        self.assertEqual(response.status_code, 200)

        payloads = [
            json.loads(line)
            for line in response.get_data(as_text=True).splitlines()
            if line.strip()
        ]

        types = [item["type"] for item in payloads]

        self.assertIn("chunk", types)
        self.assertIn("done", types)

        reply = "".join(
            item.get("content", "")
            for item in payloads
            if item["type"] == "chunk"
        )

        self.assertIn("restart the network adapter", reply.lower())

        done = next(
            item for item in payloads if item["type"] == "done"
        )

        with app.app_context():
            conversation = db.session.get(
                ChatConversation,
                done["conversation_id"]
            )

            self.assertIsNotNone(conversation)
            self.assertEqual(conversation.user_id, self.user_id)
            self.assertEqual(
                conversation.title,
                "My Wi-Fi is not working"
            )

            messages = ChatMessage.query.filter_by(
                conversation_id=conversation.id
            ).order_by(ChatMessage.id.asc()).all()

            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].role, "user")
            self.assertEqual(messages[1].role, "assistant")
            self.assertIn("restart the network adapter", messages[1].content.lower())

    def test_chat_continues_existing_conversation(self):
        with app.test_client() as client:
            self.login(client)

            first = client.post(
                "/assistant/api/chat",
                json={"message": "First question"}
            )

            payloads = [
                json.loads(line)
                for line in first.get_data(as_text=True).splitlines()
                if line.strip()
            ]

            conversation_id = next(
                item for item in payloads if item["type"] == "done"
            )["conversation_id"]

            second = client.post(
                "/assistant/api/chat",
                json={
                    "message": "Follow up question",
                    "conversation_id": conversation_id
                }
            )

            second_payloads = [
                json.loads(line)
                for line in second.get_data(as_text=True).splitlines()
                if line.strip()
            ]

            done = next(
                item for item in second_payloads
                if item["type"] == "done"
            )

            self.assertEqual(done["conversation_id"], conversation_id)
            self.assertFalse(done["is_new"])

        with app.app_context():
            messages = ChatMessage.query.filter_by(
                conversation_id=conversation_id
            ).all()

            self.assertEqual(len(messages), 4)

    def test_chat_reports_ai_failure(self):
        app_module.stream_chat = failing_stream_chat

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/chat",
                json={"message": "Something breaks"}
            )

        payloads = [
            json.loads(line)
            for line in response.get_data(as_text=True).splitlines()
            if line.strip()
        ]

        errors = [
            item for item in payloads if item["type"] == "error"
        ]

        self.assertTrue(errors)
        self.assertIn("engine is unavailable", errors[0]["message"])

    # --- Ownership ------------------------------------------------------

    def test_cannot_read_another_users_conversation(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.other_id,
                title="Private conversation"
            )
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)

            response = client.post(
                "/assistant/api/rename",
                json={
                    "conversation_id": conversation_id,
                    "title": "Hijacked"
                }
            )

            page = client.get(
                f"/assistant?conversation={conversation_id}"
            )

        self.assertEqual(response.status_code, 404)

        # The other user's conversation must not leak into the page.
        self.assertNotIn(b"Private conversation", page.data)
        self.assertNotIn(b"Hijacked", page.data)

    def test_cannot_delete_another_users_conversation(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.other_id,
                title="Keep me"
            )
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/delete",
                json={"conversation_id": conversation_id}
            )

        self.assertEqual(response.status_code, 404)

        with app.app_context():
            still_there = db.session.get(
                ChatConversation,
                conversation_id
            )
            self.assertIsNotNone(still_there)

    # --- Rename / delete ------------------------------------------------

    def test_rename_conversation(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.user_id,
                title="Old title"
            )
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/rename",
                json={
                    "conversation_id": conversation_id,
                    "title": "Wi-Fi troubleshooting"
                }
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["title"],
            "Wi-Fi troubleshooting"
        )

        with app.app_context():
            conversation = db.session.get(
                ChatConversation,
                conversation_id
            )
            self.assertEqual(
                conversation.title,
                "Wi-Fi troubleshooting"
            )

    def test_delete_conversation_removes_messages(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.user_id,
                title="Delete me"
            )
            db.session.add(conversation)
            db.session.commit()

            db.session.add(ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content="Hello"
            ))
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/delete",
                json={"conversation_id": conversation_id}
            )

        self.assertEqual(response.status_code, 200)

        with app.app_context():
            self.assertIsNone(
                db.session.get(ChatConversation, conversation_id)
            )
            self.assertEqual(
                ChatMessage.query.filter_by(
                    conversation_id=conversation_id
                ).count(),
                0
            )

    # --- Ticket linking -------------------------------------------------

    def test_assistant_from_ticket_links_conversation(self):
        with app.app_context():
            ticket = Ticket(
                title="Assistant linked ticket",
                description="Printer keeps jamming.",
                category="Hardware",
                priority="Medium",
                user_id=self.user_id
            )
            db.session.add(ticket)
            db.session.commit()
            ticket_id = ticket.id

        with app.test_client() as client:
            self.login(client)
            response = client.get(f"/assistant/ticket/{ticket_id}")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/assistant?conversation=", response.headers["Location"])

        with app.app_context():
            conversation = ChatConversation.query.filter_by(
                ticket_id=ticket_id
            ).first()

            self.assertIsNotNone(conversation)
            self.assertEqual(conversation.user_id, self.user_id)

            db.session.delete(ticket)
            db.session.commit()

    def test_escalate_conversation_creates_ticket(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.user_id,
                title="Assistant escalation: printer jam"
            )
            db.session.add(conversation)
            db.session.commit()

            db.session.add(ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content="The printer keeps jamming."
            ))
            db.session.add(ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="Open the tray and clear the paper."
            ))
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/escalate",
                json={"conversation_id": conversation_id}
            )

        self.assertEqual(response.status_code, 200)
        ticket_id = response.get_json()["ticket_id"]

        with app.app_context():
            ticket = db.session.get(Ticket, ticket_id)

            self.assertIsNotNone(ticket)
            self.assertEqual(ticket.user_id, self.user_id)
            self.assertIn("printer keeps jamming", ticket.description.lower())
            self.assertTrue(ticket.ai_analyzed)
            self.assertEqual(ticket.ai_diagnosis, "Escalated diagnosis.")

            conversation = db.session.get(
                ChatConversation,
                conversation_id
            )
            self.assertEqual(conversation.ticket_id, ticket.id)

            db.session.delete(ticket)
            db.session.commit()

    def test_escalate_without_messages_returns_error(self):
        with app.app_context():
            conversation = ChatConversation(
                user_id=self.user_id,
                title="Empty conversation"
            )
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id

        with app.test_client() as client:
            self.login(client)
            response = client.post(
                "/assistant/api/escalate",
                json={"conversation_id": conversation_id}
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("nothing to escalate", response.get_json()["error"])

    # --- Context building ------------------------------------------------

    def test_context_includes_matching_knowledge_article(self):
        with app.app_context():
            article = KnowledgeArticle(
                title="Resetting a laptop network stack",
                category="Network",
                content="Run ipconfig /flushdns to clear the DNS cache."
            )
            db.session.add(article)
            db.session.commit()

            context = app_module.build_assistant_context(
                None,
                "my network is broken, can you reset it"
            )

        self.assertIn(
            "Resetting a laptop network stack",
            context
        )
        self.assertIn("flushdns", context)

    def test_context_includes_linked_ticket(self):
        with app.app_context():
            ticket = Ticket(
                title="Assistant context ticket",
                description="VPN drops every hour.",
                category="Network",
                priority="High",
                status="Open",
                user_id=self.user_id
            )
            db.session.add(ticket)
            db.session.commit()
            ticket_id = ticket.id

            conversation = ChatConversation(
                user_id=self.user_id,
                title="Context conversation",
                ticket_id=ticket_id
            )
            db.session.add(conversation)
            db.session.commit()

            context = app_module.build_assistant_context(
                conversation,
                "the vpn keeps dropping"
            )

            self.assertIn("Assistant context ticket", context)
            self.assertIn("VPN drops every hour", context)

            db.session.delete(ticket)
            db.session.commit()


def fake_stream_chat(history, context=""):
    """Stand-in for the Ollama streaming generator."""
    yield "It sounds like a network issue. "
    yield "1. Restart the network adapter.\n"
    yield "2. Reconnect to the Wi-Fi.\n\n"
    yield "NEXT: Try the steps above and tell me what happens."


def failing_stream_chat(history, context=""):
    """Simulate the local AI engine being unavailable."""
    raise RuntimeError("The AI engine is unavailable.")
    yield ""  # pragma: no cover


if __name__ == "__main__":
    unittest.main()

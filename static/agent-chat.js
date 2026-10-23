/* =====================================
   Helix — live chat with a support agent
   ===================================== */

(function () {
    "use strict";

    const POLL_INTERVAL = 4000;

    const chatWindow = document.getElementById("chatWindow");
    const thread = document.getElementById("chatThread");
    const form = document.getElementById("chatForm");
    const input = document.getElementById("chatInput");

    if (!chatWindow || !thread) {
        return;
    }

    const messagesUrl = chatWindow.dataset.messagesUrl;
    let lastMessageId = parseInt(
        chatWindow.dataset.lastMessageId || "0",
        10
    );
    let isAtBottom = true;
    let pollTimer = null;

    function formatTime(value) {
        if (!value) {
            return "";
        }

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return "";
        }

        return date.toLocaleString(undefined, {
            day: "2-digit",
            month: "short",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    function isScrolledToBottom() {
        const distance =
            chatWindow.scrollHeight - chatWindow.scrollTop -
            chatWindow.clientHeight;

        return distance < 80;
    }

    function scrollToBottom(force) {
        if (force || isAtBottom) {
            chatWindow.scrollTop = chatWindow.scrollHeight;
        }
    }

    function buildMessage(message) {
        const row = document.createElement("div");
        row.className =
            "chat-row " + (message.mine ? "is-mine" : "is-theirs");

        const bubble = document.createElement("div");
        bubble.className = "chat-bubble";

        const head = document.createElement("div");
        head.className = "chat-bubble-head";

        const name = document.createElement("strong");
        name.textContent = message.mine ? "You" : message.sender_name;

        const time = document.createElement("time");
        time.textContent = formatTime(message.created_at);

        head.appendChild(name);
        head.appendChild(time);

        const body = document.createElement("p");
        body.textContent = message.body;

        bubble.appendChild(head);
        bubble.appendChild(body);
        row.appendChild(bubble);

        return row;
    }

    function appendMessages(messages) {
        if (!messages.length) {
            return;
        }

        const emptyState = thread.querySelector(".chat-empty");

        if (emptyState) {
            emptyState.remove();
        }

        messages.forEach((message) => {
            thread.appendChild(buildMessage(message));
            lastMessageId = Math.max(lastMessageId, message.id);
        });

        scrollToBottom(false);
    }

    async function poll() {
        try {
            const response = await fetch(
                messagesUrl + "?since=" + lastMessageId,
                {
                    headers: { "X-Requested-With": "fetch" },
                    credentials: "same-origin",
                }
            );

            if (!response.ok) {
                return;
            }

            const data = await response.json();

            appendMessages(data.messages || []);
        } catch (error) {
            // A dropped poll is not worth alarming the user about.
        }
    }

    function startPolling() {
        stopPolling();
        pollTimer = window.setInterval(poll, POLL_INTERVAL);
    }

    function stopPolling() {
        if (pollTimer) {
            window.clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    chatWindow.addEventListener("scroll", () => {
        isAtBottom = isScrolledToBottom();
    });

    if (form && input) {
        // Enter sends the message, Shift + Enter adds a new line.
        input.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                form.requestSubmit();
            }
        });

        form.addEventListener("submit", async (event) => {
            event.preventDefault();

            const body = input.value.trim();

            if (!body) {
                return;
            }

            const sendButton = document.getElementById("chatSend");

            if (sendButton) {
                sendButton.disabled = true;
            }

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                        "X-Requested-With": "fetch",
                    },
                    credentials: "same-origin",
                    body: "body=" + encodeURIComponent(body),
                });

                if (response.ok) {
                    input.value = "";
                    await poll();
                    scrollToBottom(true);
                }
            } catch (error) {
                // Fall back to a normal submit so nothing is lost.
                form.submit();
            } finally {
                if (sendButton) {
                    sendButton.disabled = false;
                }
            }
        });
    }

    scrollToBottom(true);
    startPolling();

    // Stop polling while the tab is hidden to save requests.
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            stopPolling();
        } else {
            poll();
            startPolling();
        }
    });
})();

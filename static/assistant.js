/* Helix AI assistant — chat front-end */

(function () {
    "use strict";

    const form = document.getElementById("assistantForm");

    if (!form) {
        return;
    }

    const input = document.getElementById("assistantInput");
    const sendButton = document.getElementById("assistantSend");
    const messagesPanel = document.getElementById("assistantMessages");
    const errorPanel = document.getElementById("assistantError");
    const chatTitle = document.getElementById("assistantChatTitle");
    const chatSubtitle = document.getElementById("assistantChatSubtitle");
    const renameButton = document.getElementById("renameConversationButton");
    const escalateButton = document.getElementById("escalateButton");

    const chatUrl = form.dataset.chatUrl;
    const renameUrl = form.dataset.renameUrl;
    const deleteUrl = form.dataset.deleteUrl;
    const escalateUrl = form.dataset.escalateUrl;
    const ticketBase = form.dataset.ticketBase;

    let conversationId = form.dataset.conversationId || null;
    let ticketId = form.dataset.ticketId || null;
    let busy = false;

    /* ------------------------------------------------------------------
       Rendering helpers
    ------------------------------------------------------------------ */

    function escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value == null ? "" : String(value);
        return div.innerHTML;
    }

    function inlineMarkdown(text) {
        return text
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
            .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    }

    /**
     * Render the assistant reply using a small markdown subset:
     * headings, ordered/unordered lists, bold, inline code and the
     * trailing NEXT: hint line.
     */
    function renderMarkdown(raw) {
        const lines = String(raw || "").split("\n");
        const html = [];
        let listType = null;

        function closeList() {
            if (listType) {
                html.push("</" + listType + ">");
                listType = null;
            }
        }

        function openList(type) {
            if (listType !== type) {
                closeList();
                html.push("<" + type + ">");
                listType = type;
            }
        }

        lines.forEach(function (line) {
            const trimmed = line.trim();

            if (!trimmed) {
                closeList();
                return;
            }

            if (/^NEXT\s*:/i.test(trimmed)) {
                closeList();
                html.push(
                    '<div class="chat-next-step">' +
                    escapeHtml(trimmed.replace(/^NEXT\s*:\s*/i, "Next: ")) +
                    "</div>"
                );
                return;
            }

            const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);

            if (heading) {
                closeList();
                const level = Math.min(heading[1].length + 2, 6);
                html.push(
                    "<h" + level + ">" +
                    inlineMarkdown(escapeHtml(heading[2])) +
                    "</h" + level + ">"
                );
                return;
            }

            const ordered = trimmed.match(/^(\d+)[.)]\s+(.*)$/);

            if (ordered) {
                openList("ol");
                html.push(
                    "<li>" + inlineMarkdown(escapeHtml(ordered[2])) + "</li>"
                );
                return;
            }

            const bullet = trimmed.match(/^[-*•]\s+(.*)$/);

            if (bullet) {
                openList("ul");
                html.push(
                    "<li>" + inlineMarkdown(escapeHtml(bullet[1])) + "</li>"
                );
                return;
            }

            closeList();
            html.push(
                "<p>" + inlineMarkdown(escapeHtml(trimmed)) + "</p>"
            );
        });

        closeList();

        return html.join("");
    }

    function scrollToBottom() {
        messagesPanel.scrollTop = messagesPanel.scrollHeight;
    }

    function clearWelcome() {
        const welcome = document.getElementById("assistantWelcome");

        if (welcome) {
            welcome.remove();
        }
    }

    function showError(message) {
        errorPanel.textContent = message;
        errorPanel.hidden = false;
        errorPanel.scrollIntoView({ block: "nearest" });
    }

    function clearError() {
        errorPanel.textContent = "";
        errorPanel.hidden = true;
    }

    function addUserBubble(text) {
        clearWelcome();

        const row = document.createElement("div");
        row.className = "chat-row chat-row-user";

        const bubble = document.createElement("div");
        bubble.className = "chat-bubble chat-bubble-user";
        bubble.textContent = text;

        const avatar = document.createElement("div");
        avatar.className = "chat-avatar chat-avatar-user";
        avatar.textContent = form.dataset.userInitial || "•";

        row.appendChild(bubble);
        row.appendChild(avatar);
        messagesPanel.appendChild(row);
        scrollToBottom();
    }

    function addAssistantBubble() {
        clearWelcome();

        const row = document.createElement("div");
        row.className = "chat-row chat-row-assistant";

        const avatar = document.createElement("div");
        avatar.className = "chat-avatar chat-avatar-ai";
        avatar.textContent = "✦";

        const bubble = document.createElement("div");
        bubble.className = "chat-bubble chat-bubble-ai is-streaming";
        bubble.innerHTML =
            '<span class="typing-indicator">' +
            "<i></i><i></i><i></i></span>";

        row.appendChild(avatar);
        row.appendChild(bubble);
        messagesPanel.appendChild(row);
        scrollToBottom();

        return bubble;
    }

    /* ------------------------------------------------------------------
       Sending messages
    ------------------------------------------------------------------ */

    async function sendMessage(text) {
        if (busy || !text.trim()) {
            return;
        }

        busy = true;
        clearError();

        addUserBubble(text);
        input.value = "";
        autoGrow();
        setBusy(true);

        const bubble = addAssistantBubble();
        const replyTarget = bubble;
        let reply = "";

        try {
            const response = await fetch(chatUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    message: text,
                    conversation_id: conversationId,
                    ticket_id: ticketId
                })
            });

            if (!response.ok) {
                let detail = "";

                try {
                    const data = await response.json();
                    detail = data.error || "";
                } catch (parseError) {
                    detail = "";
                }

                throw new Error(
                    detail ||
                    "The assistant could not be reached (HTTP " +
                    response.status +
                    ")."
                );
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { value, done } = await reader.read();

                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split("\n");
                buffer = lines.pop() || "";

                lines.forEach(function (line) {
                    const trimmed = line.trim();

                    if (!trimmed) {
                        return;
                    }

                    let payload;

                    try {
                        payload = JSON.parse(trimmed);
                    } catch (parseError) {
                        return;
                    }

                    if (payload.type === "chunk") {
                        reply += payload.content;
                        replyTarget.innerHTML = renderMarkdown(reply);
                        scrollToBottom();
                    } else if (payload.type === "error") {
                        throw new Error(payload.message);
                    } else if (payload.type === "done") {
                        if (payload.conversation_id) {
                            conversationId = payload.conversation_id;
                            form.dataset.conversationId = conversationId;

                            if (payload.is_new) {
                                addConversationToSidebar(
                                    payload.conversation_id,
                                    payload.title
                                );
                                enableConversationActions();
                                syncUrlWithConversation(
                                    payload.conversation_id
                                );

                                if (chatTitle) {
                                    chatTitle.textContent = payload.title;
                                }
                            }
                        }
                    }
                });
            }

            if (!reply.trim()) {
                replyTarget.innerHTML =
                    '<p class="chat-empty-reply">' +
                    "The assistant did not return a reply. Please try again." +
                    "</p>";
            } else {
                replyTarget.innerHTML = renderMarkdown(reply);
            }
        } catch (error) {
            replyTarget.innerHTML =
                '<p class="chat-error-reply">' +
                escapeHtml(error.message || "Something went wrong.") +
                "</p>";
            showError(error.message || "Something went wrong.");
        } finally {
            replyTarget.classList.remove("is-streaming");
            setBusy(false);
            scrollToBottom();
            input.focus();
        }
    }

    function setBusy(state) {
        busy = state;

        if (sendButton) {
            sendButton.disabled = state;
            sendButton.classList.toggle("is-busy", state);
        }

        input.disabled = state;
    }

    /* ------------------------------------------------------------------
       Conversation list helpers
    ------------------------------------------------------------------ */

    function addConversationToSidebar(id, title) {
        const list = document.querySelector(".conversation-list");

        if (!list) {
            return;
        }

        const empty = list.querySelector(".conversation-empty");

        if (empty) {
            empty.remove();
        }

        const item = document.createElement("div");
        item.className = "conversation-item is-active";

        const link = document.createElement("a");
        link.className = "conversation-link";
        link.href = form.dataset.assistantUrl || "/assistant";
        link.href += "?conversation=" + id;
        link.title = title || "";

        const icon = document.createElement("span");
        icon.className = "conversation-icon";
        icon.textContent = "✦";

        const label = document.createElement("span");
        label.className = "conversation-title";
        label.textContent = title || "New conversation";

        link.appendChild(icon);
        link.appendChild(label);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "conversation-delete";
        remove.dataset.deleteConversation = id;
        remove.title = "Delete conversation";
        remove.setAttribute("aria-label", "Delete conversation");
        remove.textContent = "×";

        item.appendChild(link);
        item.appendChild(remove);

        list.insertBefore(item, list.firstChild);
    }

    function enableConversationActions() {
        if (renameButton) {
            renameButton.hidden = false;
        }

        if (escalateButton) {
            escalateButton.hidden = false;
        }
    }

    function syncUrlWithConversation(id) {
        /* Keep the address bar in step so a refresh keeps the chat. */
        const base = form.dataset.assistantUrl || "/assistant";

        if (window.history && window.history.replaceState) {
            window.history.replaceState(
                {},
                "",
                base + "?conversation=" + id
            );
        }
    }

    /* ------------------------------------------------------------------
       Composer behaviour
    ------------------------------------------------------------------ */

    function autoGrow() {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 180) + "px";
    }

    input.addEventListener("input", autoGrow);

    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        sendMessage(input.value);
    });

    document.querySelectorAll("[data-suggestion]").forEach(function (chip) {
        chip.addEventListener("click", function () {
            input.value = chip.dataset.suggestion;
            autoGrow();
            input.focus();
        });
    });

    /* ------------------------------------------------------------------
       Rename / delete / escalate
    ------------------------------------------------------------------ */

    if (renameButton) {
        renameButton.addEventListener("click", async function () {
            if (!conversationId) {
                return;
            }

            const current = chatTitle ? chatTitle.textContent.trim() : "";
            const next = window.prompt(
                "Rename this conversation:",
                current
            );

            if (next === null) {
                return;
            }

            const title = next.trim();

            if (!title) {
                return;
            }

            try {
                const response = await fetch(renameUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        conversation_id: conversationId,
                        title: title
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || "Could not rename.");
                }

                if (chatTitle) {
                    chatTitle.textContent = data.title;
                }

                const label = document.querySelector(
                    ".conversation-item.is-active .conversation-title"
                );

                if (label) {
                    label.textContent = data.title;
                }
            } catch (error) {
                showError(error.message);
            }
        });
    }

    document.querySelectorAll("[data-delete-conversation]").forEach(
        function (button) {
            button.addEventListener("click", async function (event) {
                event.preventDefault();
                event.stopPropagation();

                const id = button.dataset.deleteConversation;

                if (!window.confirm(
                    "Delete this conversation and its messages?"
                )) {
                    return;
                }

                try {
                    const response = await fetch(deleteUrl, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify({
                            conversation_id: id
                        })
                    });

                    const data = await response.json();

                    if (!response.ok) {
                        throw new Error(data.error || "Could not delete.");
                    }

                    const item = button.closest(".conversation-item");

                    if (item) {
                        item.remove();
                    }

                    if (String(id) === String(conversationId)) {
                        window.location.href =
                            form.dataset.assistantUrl || "/assistant";
                    }
                } catch (error) {
                    showError(error.message);
                }
            });
        }
    );

    if (escalateButton) {
        escalateButton.addEventListener("click", async function () {
            if (!conversationId) {
                return;
            }

            escalateButton.disabled = true;
            escalateButton.textContent = "Creating…";

            try {
                const response = await fetch(escalateUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        conversation_id: conversationId
                    })
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.error || "Could not create a ticket.");
                }

                ticketId = data.ticket_id;
                form.dataset.ticketId = ticketId;

                if (chatSubtitle) {
                    chatSubtitle.textContent = "Linked to ticket #" + ticketId;
                }

                escalateButton.textContent = "Ticket #" + data.ticket_id + " created";
                escalateButton.disabled = true;

                window.setTimeout(function () {
                    window.location.href =
                        ticketBase + "/" + data.ticket_id;
                }, 900);
            } catch (error) {
                showError(error.message);
                escalateButton.disabled = false;
                escalateButton.textContent = "Raise a ticket";
            }
        });
    }

    /* ------------------------------------------------------------------
       Boot
    ------------------------------------------------------------------ */

    if (!messagesPanel.dataset.rendered) {
        messagesPanel
            .querySelectorAll('[data-role="assistant"]')
            .forEach(function (bubble) {
                const source = bubble.textContent;
                bubble.innerHTML = renderMarkdown(source);
            });

        messagesPanel.dataset.rendered = "true";
    }

    autoGrow();
    scrollToBottom();
    input.focus();
})();

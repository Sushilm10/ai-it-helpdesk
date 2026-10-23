/* Helix — ticket conversation thread + AI drafting */

(function () {
    "use strict";

    const form = document.getElementById("threadForm");

    if (!form) {
        return;
    }

    const input = document.getElementById("threadInput");
    const sendButton = document.getElementById("threadSend");
    const errorPanel = document.getElementById("threadError");
    const draftButton = document.getElementById("aiDraftButton");
    const summaryButton = document.getElementById("aiSummaryButton");

    function showError(message) {
        errorPanel.textContent = message;
        errorPanel.hidden = false;
        errorPanel.scrollIntoView({ block: "nearest" });
    }

    function clearError() {
        errorPanel.textContent = "";
        errorPanel.hidden = true;
    }

    function autoGrow() {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 260) + "px";
    }

    input.addEventListener("input", autoGrow);

    /* ------------------------------------------------------------------
       Composer: Enter sends, Shift + Enter adds a newline
    ------------------------------------------------------------------ */

    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener("submit", function () {
        clearError();
        sendButton.disabled = true;
        sendButton.textContent = "Posting…";
    });

    /* ------------------------------------------------------------------
       AI: draft a reply for the agent
    ------------------------------------------------------------------ */

    if (draftButton) {
        draftButton.addEventListener("click", async function () {
            const original = draftButton.innerHTML;

            draftButton.disabled = true;
            draftButton.innerHTML =
                '<span class="thread-toolbar-icon">✦</span> Drafting…';
            clearError();

            try {
                const response = await fetch(
                    draftButton.dataset.draftUrl,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify({})
                    }
                );

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.error || "Could not draft a reply."
                    );
                }

                if (input.value.trim()) {
                    if (
                        !window.confirm(
                            "Replace the text you have already written with the AI draft?"
                        )
                    ) {
                        return;
                    }
                }

                input.value = data.draft;
                autoGrow();
                input.focus();

                /* Highlight the draft so the agent knows to review it. */
                input.setSelectionRange(
                    input.value.length,
                    input.value.length
                );
            } catch (error) {
                showError(error.message || "Something went wrong.");
            } finally {
                draftButton.disabled = false;
                draftButton.innerHTML = original;
            }
        });
    }

    /* ------------------------------------------------------------------
       AI: summarise the conversation
    ------------------------------------------------------------------ */

    if (summaryButton) {
        summaryButton.addEventListener("click", async function () {
            const original = summaryButton.innerHTML;

            summaryButton.disabled = true;
            summaryButton.innerHTML =
                '<span class="thread-toolbar-icon">✦</span> Summarising…';
            clearError();

            try {
                const response = await fetch(
                    summaryButton.dataset.summaryUrl,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify({})
                    }
                );

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.error || "Could not summarise the thread."
                    );
                }

                showSummary(data.summary);
            } catch (error) {
                showError(error.message || "Something went wrong.");
            } finally {
                summaryButton.disabled = false;
                summaryButton.innerHTML = original;
            }
        });
    }

    function showSummary(summary) {
        let panel = document.getElementById("threadSummary");

        if (!panel) {
            panel = document.createElement("div");
            panel.id = "threadSummary";
            panel.className = "thread-summary";

            const messages = document.getElementById("threadMessages");

            if (messages && messages.parentNode) {
                messages.parentNode.insertBefore(panel, messages);
            }
        }

        panel.innerHTML =
            '<div class="thread-summary-head">' +
            "<span>✦</span> AI SUMMARY" +
            "</div>" +
            '<div class="thread-summary-body"></div>';

        panel.querySelector(".thread-summary-body").textContent = summary;

        panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    autoGrow();
})();

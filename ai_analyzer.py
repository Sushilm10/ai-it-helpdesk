import subprocess
import json
import os
import re
import urllib.error
import urllib.request


MODEL = "qwen2.5:7b"

OLLAMA_HOST = os.environ.get(
    "OLLAMA_HOST",
    "http://127.0.0.1:11434"
)

CHAT_MODEL = os.environ.get(
    "OLLAMA_CHAT_MODEL",
    MODEL
)

CHAT_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

SYSTEM_PROMPT = """You are Helix, the AI assistant built into an IT helpdesk.

Your goal is to help the user solve their technical problem in this conversation.

Rules:
- Answer in clear, practical language. Keep responses focused and scannable.
- Break troubleshooting into short numbered steps the user can follow one by one.
- If you need more detail to diagnose the problem, ask one or two focused questions.
- If the issue clearly needs a human support engineer, say so and suggest opening a ticket.
- Never ask for or repeat passwords, MFA codes, API keys, or other credentials.
- Prefer safe, reversible steps before anything destructive.
- Use markdown sparingly: short paragraphs, bullet lists and numbered steps.
- Do not invent policy details, ticket numbers or system names that were not given to you.
- End your reply with a single short line beginning with "NEXT:" that tells the user what to do next (or says the issue looks resolved).
"""


def clean_ollama_output(response):
    """
    Remove terminal ANSI escape sequences, markdown code fences,
    and reasoning/think blocks from Ollama output.
    """
    # Remove thinking tags if present
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)

    # Remove ANSI escape sequences
    response = re.sub(
        r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])",
        "",
        response
    )

    # Remove markdown code fences (```json or ```)
    response = re.sub(r"```(?:json)?", "", response, flags=re.IGNORECASE)
    response = response.replace("```", "")

    return response.strip()


def extract_and_parse_json(text):
    """
    Extract the first valid JSON object from LLM output, handling
    trailing commas, raw newlines, and surrounding conversational text.
    """
    cleaned = clean_ollama_output(text)

    # First attempt: outer braces
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidate = cleaned[start:end + 1]
        # Remove trailing commas
        fixed_candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
        try:
            return json.loads(fixed_candidate, strict=False)
        except Exception:
            pass

    # Second attempt: find balanced { ... } block
    depth = 0
    s_idx = None
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                s_idx = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and s_idx is not None:
                candidate = cleaned[s_idx:i + 1]
                fixed_candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
                try:
                    return json.loads(fixed_candidate, strict=False)
                except Exception:
                    pass

    raise json.JSONDecodeError("Could not parse JSON from AI response", cleaned, 0)


def analyze_ticket(title, description):
    """
    Send an IT support ticket to Ollama and return
    structured AI analysis.
    """

    prompt = f"""
You are an experienced IT helpdesk technician.

Analyze the following IT support ticket.

Ticket title:
{title}

Ticket description:
{description}

Return ONLY valid JSON using exactly this structure:

{{
    "category": "Hardware, Software, Network, Account & Access, Security, or Other",
    "priority": "Low, Medium, High, or Critical",
    "diagnosis": "Short explanation of the likely problem",
    "solution": [
        "Step 1",
        "Step 2",
        "Step 3"
    ]
}}

Rules:
- Return JSON only.
- Do not use markdown.
- Do not use ```json.
- Do not add explanations outside the JSON.
"""

    try:

        result = subprocess.run(
            [
                "ollama",
                "run",
                MODEL,
                prompt
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or f"Ollama failed with code {result.returncode}"
            return {"error": err}

        return extract_and_parse_json(result.stdout)

    except FileNotFoundError:
        return {
            "error": "Ollama is not installed or not found in system PATH."
        }

    except subprocess.TimeoutExpired:
        return {
            "error": "AI analysis timed out."
        }

    except json.JSONDecodeError:
        raw = clean_ollama_output(result.stdout if "result" in locals() else "")
        return {
            "error": "AI returned invalid JSON.",
            "raw_response": raw
        }

    except Exception as error:
        return {
            "error": str(error)
        }


def ollama_is_available():
    """Return True when the Ollama HTTP server is reachable."""
    try:
        with urllib.request.urlopen(
            f"{OLLAMA_HOST}/api/tags",
            timeout=3
        ):
            return True
    except Exception:
        return False


def _build_chat_messages(history, context=""):
    """Combine the system prompt, optional context and chat history."""
    system = SYSTEM_PROMPT

    if context:
        system = f"{system}\n\nCONTEXT FROM THE HELPDESK:\n{context}"

    messages = [{"role": "system", "content": system}]

    for item in history:
        role = item.get("role", "user")

        if role not in ("user", "assistant"):
            continue

        content = (item.get("content") or "").strip()

        if content:
            messages.append({
                "role": role,
                "content": content
            })

    return messages


def stream_chat(history, context=""):
    """
    Stream an AI reply token by token.

    Yields text chunks as they are produced by the model. Falls back to
    the Ollama CLI when the HTTP API is not reachable.
    """
    messages = _build_chat_messages(history, context)

    payload = json.dumps({
        "model": CHAT_MODEL,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": 0.4,
            "num_predict": 800
        }
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=CHAT_TIMEOUT
        ) as response:

            for raw_line in response:
                line = raw_line.decode(
                    "utf-8",
                    errors="ignore"
                ).strip()

                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = (
                    chunk.get("message") or {}
                ).get("content", "")

                if content:
                    yield content

                if chunk.get("done"):
                    break

        return

    except urllib.error.URLError:
        # The HTTP API is not reachable, fall back to the CLI.
        pass

    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Ollama returned HTTP {error.code}."
        ) from error

    except Exception as error:
        raise RuntimeError(
            f"AI chat failed: {error}"
        ) from error

    # --- CLI fallback -------------------------------------------------

    prompt_parts = []

    if context:
        prompt_parts.append(
            f"CONTEXT FROM THE HELPDESK:\n{context}"
        )

    for item in messages[1:]:
        speaker = (
            "User" if item["role"] == "user" else "Assistant"
        )
        prompt_parts.append(
            f"{speaker}: {item['content']}"
        )

    prompt_parts.append("Assistant:")

    try:
        result = subprocess.run(
            [
                "ollama",
                "run",
                CHAT_MODEL,
                "\n\n".join(prompt_parts)
            ],
            capture_output=True,
            text=True,
            timeout=CHAT_TIMEOUT
        )

    except FileNotFoundError as error:
        raise RuntimeError(
            "Ollama is not installed or not found in system PATH."
        ) from error

    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "The AI assistant took too long to respond."
        ) from error

    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Ollama failed."
        )
        raise RuntimeError(message)

    reply = clean_ollama_output(result.stdout)

    if not reply:
        raise RuntimeError("The AI assistant returned an empty reply.")

    # Emit the fallback reply in small chunks so the UI still streams.
    for index in range(0, len(reply), 40):
        yield reply[index:index + 40]


def chat_once(history, context=""):
    """Return the full AI reply as a single string."""
    return "".join(stream_chat(history, context)).strip()


REPLY_SYSTEM_PROMPT = """You are a senior IT support engineer drafting a reply to a customer ticket.

Write the reply that the support engineer will send to the customer.

Structure:
- Open with a short greeting that names the problem.
- Restate the key facts you rely on so the customer can confirm them.
- Give the troubleshooting steps as a numbered list, one action per step.
- Explain in a short sentence why each step helps.
- Close with what happens next, or one clear question that unblocks you.

Rules:
- Plain text only. Do not use markdown headings or code fences.
- Use a numbered list for the steps.
- Never ask for, repeat or guess passwords, MFA codes or API keys.
- Keep it under 250 words.
- Do not invent ticket numbers, policies, names or system names that were not given to you.
- If the ticket clearly needs a human engineer, say so plainly.
"""


SUMMARY_SYSTEM_PROMPT = """You are an IT support analyst briefing a support engineer.

Summarise the ticket conversation for an engineer who has not read it.

Cover:
1. One sentence stating the reported problem.
2. The current status of the ticket.
3. The last action that was taken.
4. Exactly what is needed from the customer next.

Rules:
- Plain text only, no markdown headings.
- Keep it under 120 words.
- Do not invent details that are not in the conversation.
"""


def _complete_via_cli(messages):
    """Run a one-shot completion through the Ollama CLI."""
    parts = []

    for item in messages:
        speaker = "System" if item["role"] == "system" else "User"
        parts.append(f"{speaker}: {item['content']}")

    parts.append("Assistant:")

    try:
        result = subprocess.run(
            [
                "ollama",
                "run",
                CHAT_MODEL,
                "\n\n".join(parts)
            ],
            capture_output=True,
            text=True,
            timeout=CHAT_TIMEOUT
        )

    except FileNotFoundError as error:
        raise RuntimeError(
            "Ollama is not installed or not found in system PATH."
        ) from error

    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "The AI assistant took too long to respond."
        ) from error

    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Ollama failed."
        )
        raise RuntimeError(message)

    reply = clean_ollama_output(result.stdout)

    if not reply:
        raise RuntimeError("The AI assistant returned an empty reply.")

    return reply


def complete(prompt, system="", timeout=None):
    """
    Run a one-shot completion through Ollama and return the text.

    Uses the HTTP API first and falls back to the CLI when the API is
    not reachable.
    """
    messages = []

    if system:
        messages.append({
            "role": "system",
            "content": system
        })

    messages.append({
        "role": "user",
        "content": prompt
    })

    payload = json.dumps({
        "model": CHAT_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 700
        }
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout or CHAT_TIMEOUT
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="ignore"
            )

            data = json.loads(raw)

            content = (
                data.get("message") or {}
            ).get("content", "")

            text = clean_ollama_output(content)

            if text:
                return text

    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Ollama returned HTTP {error.code}."
        ) from error

    except Exception:
        # The HTTP API is not reachable, fall back to the CLI.
        pass

    return _complete_via_cli(messages)


def _format_thread(thread):
    """Render a thread of messages as plain text for the model."""
    lines = []

    for item in thread:
        lines.append(
            f"{item.get('author', 'Unknown')} "
            f"({item.get('role', 'user')}): "
            f"{item.get('body', '')}"
        )

    return "\n\n".join(lines)


def draft_agent_reply(ticket_info, thread, kb_context=""):
    """
    Draft a reply that a support engineer can review, edit and send.

    The draft is never sent automatically - the agent always stays in
    control of what the customer sees.
    """
    sections = [
        "TICKET:\n"
        f"Title: {ticket_info.get('title', '')}\n"
        f"Category: {ticket_info.get('category', '')}\n"
        f"Priority: {ticket_info.get('priority', '')}\n"
        f"Status: {ticket_info.get('status', '')}\n"
        f"Description:\n{ticket_info.get('description', '')}"
    ]

    if ticket_info.get("ai_diagnosis"):
        sections.append(
            "EARLIER AI DIAGNOSIS:\n"
            + str(ticket_info["ai_diagnosis"])
        )

    if ticket_info.get("ai_solution"):
        sections.append(
            "EARLIER AI SUGGESTED STEPS:\n"
            + str(ticket_info["ai_solution"])
        )

    if kb_context:
        sections.append(
            "REFERENCE KNOWLEDGE BASE:\n" + kb_context
        )

    if thread:
        sections.append(
            "CONVERSATION SO FAR:\n" + _format_thread(thread)
        )

    prompt = "\n\n".join(sections)

    prompt += (
        "\n\nWrite the reply to send to the customer now."
    )

    return complete(prompt, REPLY_SYSTEM_PROMPT)


def summarise_thread(ticket_info, thread):
    """Summarise a ticket conversation for a support engineer."""
    sections = [
        "TICKET:\n"
        f"Title: {ticket_info.get('title', '')}\n"
        f"Status: {ticket_info.get('status', '')}\n"
        f"Description:\n{ticket_info.get('description', '')}"
    ]

    if thread:
        sections.append(
            "CONVERSATION:\n" + _format_thread(thread)
        )

    prompt = "\n\n".join(sections)

    prompt += (
        "\n\nSummarise this ticket for a support engineer."
    )

    return complete(prompt, SUMMARY_SYSTEM_PROMPT)


def suggest_title(message):
    """Build a short conversation title from the first user message."""
    cleaned = re.sub(r"\s+", " ", (message or "").strip())

    if not cleaned:
        return "New conversation"

    if len(cleaned) <= 60:
        return cleaned

    return cleaned[:57].rstrip() + "..."


if __name__ == "__main__":

    result = analyze_ticket(
        "Cannot connect to Wi-Fi",
        "My laptop suddenly stopped connecting to the office Wi-Fi. I restarted the laptop but the problem remains."
    )

    print(json.dumps(result, indent=4))

import subprocess
import json
import re


MODEL = "qwen2.5:7b"


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


if __name__ == "__main__":

    result = analyze_ticket(
        "Cannot connect to Wi-Fi",
        "My laptop suddenly stopped connecting to the office Wi-Fi. I restarted the laptop but the problem remains."
    )

    print(json.dumps(result, indent=4))

import os
import requests


def ask_chatgpt(
    prompt: str,
    access_token: str,
    system_prompt: str = "",
    api_config: dict = None,
) -> str:
    if api_config is None:
        api_config = {}

    use_ollama = os.getenv("USE_OLLAMA", "false").lower() == "true"

    if use_ollama:
        return _ask_ollama(prompt, system_prompt)
    else:
        return _ask_openai(prompt, access_token, system_prompt, api_config)


def _ask_ollama(prompt: str, system_prompt: str = "") -> str:
    """Call local Ollama instance."""
    endpoint = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/chat")
    model    = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

    # Trim prompt to avoid overloading local model
    max_chars = 3000
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars] + "\n\n[...truncated for local model...]"

    messages = []
    if system_prompt:
        # Keep system prompt short too
        messages.append({"role": "system", "content": system_prompt[:500]})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(
            endpoint,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "num_predict": 1024,  # limit output tokens
                    "temperature": 0.0,
                }
            },
            timeout=300,  # 5 minutes
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()

    except requests.exceptions.ReadTimeout:
        return (
            "⚠️ Ollama timed out. Try:\n"
            "1. Use a lighter model: set OLLAMA_MODEL=llama3.2:3b in .env\n"
            "2. Reduce your dataset size before generating summary."
        )
    except requests.exceptions.ConnectionError:
        return (
            "⚠️ Cannot connect to Ollama. Make sure:\n"
            "1. Ollama is running (check system tray)\n"
            "2. Run `ollama serve` in CMD"
        )
    except Exception as e:
        return f"⚠️ Ollama error: {str(e)}"


def _ask_openai(
    prompt: str,
    access_token: str,
    system_prompt: str = "",
    api_config: dict = None,
) -> str:
    """Call OpenAI / Azure OpenAI endpoint."""
    if api_config is None:
        api_config = {}

    endpoint = api_config.get("chat_endpoint", "")
    if not endpoint:
        raise RuntimeError("chat_endpoint not set in api_config.")

    model   = api_config.get("model", "gpt-4o")
    timeout = int(api_config.get("timeout", 120))

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": api_config.get("max_tokens", 4096),
        "temperature": api_config.get("temperature", 0.0),
    }

    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    api_key = api_config.get("api_key") or api_config.get("appkey") or ""
    if api_key and not access_token:
        headers["api-key"] = api_key

    resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        pass
    try:
        return data["content"][0]["text"].strip()
    except (KeyError, IndexError):
        pass

    return str(data)
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

def load_theme_style():
    theme = {
        "primary_color": os.getenv("THEME_PRIMARY_COLOR", "#3E69A8"),
        "background_color": os.getenv("THEME_BG_COLOR", "#969696"),
    }
    style = {
        "font_family": os.getenv("STYLE_FONT", "Inter"),
    }
    return theme, style


def load_api_config():
    return {
        "chat_endpoint": os.getenv("CHAT_ENDPOINT", ""),
        "api_key":       os.getenv("API_KEY", ""),
        "appkey":        os.getenv("APPKEY", ""),
        "auth_endpoint": os.getenv("AUTH_ENDPOINT", ""),
        "client_id":     os.getenv("CLIENT_ID", ""),
        "client_secret": os.getenv("CLIENT_SECRET", ""),
        "model":         os.getenv("MODEL", "gpt-4o"),
        "max_tokens":    int(os.getenv("MAX_TOKENS", "4096")),
        "temperature":   float(os.getenv("TEMPERATURE", "0.0")),
    }


def generate_auth_token(api_config: dict) -> str:
    auth_endpoint = api_config.get("auth_endpoint", "")
    client_id     = api_config.get("client_id", "")
    client_secret = api_config.get("client_secret", "")

    if auth_endpoint and client_id and client_secret:
        import requests
        resp = requests.post(
            auth_endpoint,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("access_token", "")

    return api_config.get("api_key") or api_config.get("appkey") or ""
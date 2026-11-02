"""Stdlib JSON-over-HTTP POST shared by the engine runner and the GenAI-service handlers."""
import json
import urllib.request


def post_json(base_url, path, body, *, timeout, env_name, allow_empty):
    """POST ``body`` as JSON to ``base_url + path``; return decoded JSON (``None`` for an empty reply if allowed)."""
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"{env_name} must be http(s)")
    req = urllib.request.Request(f"{base_url}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - scheme validated above, URL is operator config
        data = r.read()
    if allow_empty and not data:
        return None
    return json.loads(data)

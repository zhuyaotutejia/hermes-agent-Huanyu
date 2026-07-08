"""Agnes image generation backend.

Supports text-to-image and image-to-image via the Agnes AI API hub
(``POST https://apihub.agnes-ai.com/v1/images/generations``).

API key is resolved from (in order):
  1. ``AGNES_API_KEY`` env var
  2. ``providers.agnes.api_key`` in config.yaml (shared with the text provider)

Base URL is resolved from:
  1. ``AGNES_BASE_URL`` env var
  2. ``providers.agnes.base_url`` in config.yaml
  3. ``https://apihub.agnes-ai.com/v1`` (default)
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
_REQUEST_TIMEOUT = 180  # Agnes docs recommend 60s–360s

_MODELS: Dict[str, Dict[str, Any]] = {
    "agnes-image-2.1-flash": {
        "display": "Agnes Image 2.1 Flash",
        "speed": "~10-30s",
        "strengths": "High-density images, complex composition, text-to-image + image-to-image. Currently free ($0/image).",
        "price": "$0/image",
    },
    "agnes-image-2.0-flash": {
        "display": "Agnes Image 2.0 Flash",
        "speed": "~10-30s",
        "strengths": "Standard text-to-image and image-to-image generation.",
        "price": "$0.003/image",
    },
}
DEFAULT_MODEL = "agnes-image-2.1-flash"

_SIZE_MAP: Dict[str, str] = {
    "landscape": "1024x768",
    "square": "1024x1024",
    "portrait": "768x1024",
}


def _load_image_gen_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _load_providers_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        providers = cfg.get("providers") if isinstance(cfg, dict) else None
        return providers if isinstance(providers, dict) else {}
    except Exception as exc:
        logger.debug("Could not load providers config: %s", exc)
        return {}


def _resolve_api_key() -> str:
    env_key = os.environ.get("AGNES_API_KEY", "").strip()
    if env_key:
        return env_key
    providers = _load_providers_config()
    agnes_cfg = providers.get("agnes") if isinstance(providers.get("agnes"), dict) else {}
    return str(agnes_cfg.get("api_key") or "").strip()


def _resolve_base_url() -> str:
    env_url = os.environ.get("AGNES_BASE_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    providers = _load_providers_config()
    agnes_cfg = providers.get("agnes") if isinstance(providers.get("agnes"), dict) else {}
    url = str(agnes_cfg.get("base_url") or "").strip().rstrip("/")
    return url or _DEFAULT_BASE_URL


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    env_model = os.environ.get("AGNES_IMAGE_MODEL", "").strip()
    if env_model and env_model in _MODELS:
        return env_model, _MODELS[env_model]

    cfg = _load_image_gen_config()
    scoped = cfg.get("agnes") if isinstance(cfg.get("agnes"), dict) else {}
    candidate = scoped.get("model") if isinstance(scoped.get("model"), str) else None
    if candidate and candidate in _MODELS:
        return candidate, _MODELS[candidate]

    top = cfg.get("model")
    if isinstance(top, str) and top in _MODELS:
        return top, _MODELS[top]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _file_to_data_uri(path: str) -> str:
    p = Path(path)
    ext = p.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


class AgnesImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "agnes"

    @property
    def display_name(self) -> str:
        return "Agnes"

    def is_available(self) -> bool:
        return bool(_resolve_api_key())

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": mid,
                "display": meta["display"],
                "speed": meta.get("speed", ""),
                "strengths": meta.get("strengths", ""),
                "price": meta.get("price", ""),
            }
            for mid, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Agnes",
            "badge": "free",
            "tag": "Agnes image generation — text-to-image + image-to-image (agnes-image-2.1-flash)",
            "env_vars": [
                {
                    "key": "AGNES_API_KEY",
                    "prompt": "Agnes API key (optional — falls back to providers.agnes.api_key in config.yaml)",
                    "url": "https://agnes-ai.com",
                }
            ],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "max_reference_images": 1,
            "max_source_images": 1,
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        refs = normalize_reference_images(reference_image_urls)

        if not prompt:
            return error_response(
                error="Prompt is required for Agnes image generation.",
                error_type="invalid_argument",
                provider="agnes",
                aspect_ratio=aspect,
            )

        api_key = _resolve_api_key()
        if not api_key:
            return error_response(
                error=(
                    "AGNES_API_KEY env var not set and providers.agnes.api_key not found in config.yaml. "
                    "Set one of them to use the Agnes image backend."
                ),
                error_type="auth_required",
                provider="agnes",
                prompt=prompt,
                aspect_ratio=aspect,
            )

        base_url = _resolve_base_url()
        model_id, _meta = _resolve_model()
        size = _SIZE_MAP.get(aspect, _SIZE_MAP["landscape"])

        is_img2img = bool(image_url)
        modality = "image" if is_img2img else "text"

        source_images: List[str] = []
        if image_url:
            source_images.append(image_url)
        if refs:
            source_images.extend(refs)

        for i, src in enumerate(source_images):
            if src and Path(src).is_file():
                source_images[i] = _file_to_data_uri(src)

        import json as _json
        import urllib.request as _urlreq

        endpoint = f"{base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        proxy_url = (
            os.environ.get("HTTPS_PROXY", "").strip()
            or os.environ.get("HTTP_PROXY", "").strip()
            or os.environ.get("https_proxy", "").strip()
            or os.environ.get("http_proxy", "").strip()
            or os.environ.get("ALL_PROXY", "").strip()
            or os.environ.get("all_proxy", "").strip()
        )

        if is_img2img:
            payload: Dict[str, Any] = {
                "model": model_id,
                "prompt": prompt,
                "size": size,
                "extra_body": {
                    "image": source_images,
                    "response_format": "b64_json",
                },
            }
        else:
            payload = {
                "model": model_id,
                "prompt": prompt,
                "size": size,
                "return_base64": True,
                "extra_body": {
                    "response_format": "b64_json",
                },
            }

        logger.info(
            "Agnes image gen: model=%s size=%s modality=%s prompt_len=%d proxy=%s",
            model_id, size, modality, len(prompt), bool(proxy_url),
        )

        body_bytes = _json.dumps(payload).encode("utf-8")
        req = _urlreq.Request(endpoint, data=body_bytes, headers=headers, method="POST")

        proxy_handlers: List[_urlreq.BaseHandler] = []
        if proxy_url:
            proxy_handlers.append(_urlreq.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = _urlreq.build_opener(*proxy_handlers) if proxy_handlers else _urlreq.build_opener()

        try:
            with opener.open(req, timeout=_REQUEST_TIMEOUT) as resp_raw:
                resp_status = resp_raw.status
                resp_body = resp_raw.read().decode("utf-8")
        except _urlreq.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
            return error_response(
                error=f"Agnes API HTTP {exc.code}: {detail}",
                error_type="provider_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except _urlreq.URLError as exc:
            return error_response(
                error=f"Agnes API connection error: {exc.reason}",
                error_type="connection_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except Exception as exc:
            return error_response(
                error=f"Agnes API error: {exc}",
                error_type="provider_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if resp_status != 200:
            return error_response(
                error=f"Agnes API HTTP {resp_status}: {resp_body[:500]}",
                error_type="provider_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            data = _json.loads(resp_body)
        except ValueError:
            return error_response(
                error="Agnes API returned non-JSON response.",
                error_type="provider_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        images = data.get("data") or []
        if not images or not isinstance(images, list):
            return error_response(
                error="Agnes API returned no image data.",
                error_type="provider_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = images[0] if isinstance(images[0], dict) else {}
        b64 = first.get("b64_json")
        url = first.get("url")

        try:
            if b64:
                saved = save_b64_image(b64, prefix=f"agnes_{model_id.replace('.', '_')}")
            elif url:
                import base64 as _b64mod

                img_req = _urlreq.Request(url)
                img_opener = _urlreq.build_opener(*proxy_handlers) if proxy_handlers else _urlreq.build_opener()
                with img_opener.open(img_req, timeout=120) as img_resp:
                    b64_from_url = _b64mod.b64encode(img_resp.read()).decode("ascii")
                saved = save_b64_image(b64_from_url, prefix=f"agnes_{model_id.replace('.', '_')}")
            else:
                return error_response(
                    error="Agnes API response missing both b64_json and url.",
                    error_type="provider_error",
                    provider="agnes",
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
        except Exception as exc:
            return error_response(
                error=f"Failed to save Agnes image: {exc}",
                error_type="provider_error",
                provider="agnes",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=str(saved),
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="agnes",
            modality=modality,
            extra={"size": size, "agnes_created": data.get("created")},
        )


def register(ctx) -> None:
    ctx.register_image_gen_provider(AgnesImageGenProvider())

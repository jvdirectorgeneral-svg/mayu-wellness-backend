from io import BytesIO
import os
import base64
import builtins

from PIL import Image
from starlette.routing import request_response

from embedded_card_assets import LEVEL_1_CARD_COBRE_JPEG_BASE64


LEVEL_1_WALLET_VISUAL_VERSION = "nivel1_cobre_20260718"
_original_image_open = Image.open
_original_import = builtins.__import__


def _level_1_wallet_png_bytes():
    image = _original_image_open(
        BytesIO(base64.b64decode(LEVEL_1_CARD_COBRE_JPEG_BASE64))
    ).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _level_1_wallet_response(response_cls):
    return response_cls(
        content=_level_1_wallet_png_bytes(),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _mayu_image_open(fp, *args, **kwargs):
    path = os.fspath(fp) if isinstance(fp, (str, bytes, os.PathLike)) else ""
    normalized_path = path.replace("\\", "/")

    if normalized_path.endswith("/assets/wallet_cobre.png"):
        image_bytes = base64.b64decode(LEVEL_1_CARD_COBRE_JPEG_BASE64)
        return _original_image_open(BytesIO(image_bytes), *args, **kwargs)

    return _original_image_open(fp, *args, **kwargs)


def _with_level_1_wallet_version(uri: str):
    if not uri or LEVEL_1_WALLET_VISUAL_VERSION in uri:
        return uri
    separator = "&" if "?" in uri else "?"
    return f"{uri}{separator}wallet_visual={LEVEL_1_WALLET_VISUAL_VERSION}"


def _patch_asset_route(module, get_wallet_asset):
    for route in getattr(module.router, "routes", []) or []:
        if getattr(route, "path", "") == "/assets/{filename}":
            route.endpoint = get_wallet_asset
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = get_wallet_asset
            try:
                route.app = request_response(route.get_route_handler())
            except Exception:
                pass


def _patch_member_cards(module):
    if getattr(module, "_mayu_level_1_wallet_visual_patch", False):
        return module

    original_get_wallet_asset = module.get_wallet_asset
    original_member_apple_serial = module.member_apple_serial
    original_build_google_wallet_object_body = module.build_google_wallet_object_body

    def get_wallet_asset(filename: str):
        if filename == "wallet_cobre.png":
            return _level_1_wallet_response(module.Response)
        return original_get_wallet_asset(filename)

    def member_apple_serial(card):
        serial = original_member_apple_serial(card)
        if getattr(card, "level_snapshot", None) == 1 and LEVEL_1_WALLET_VISUAL_VERSION not in serial:
            return f"{serial}-{LEVEL_1_WALLET_VISUAL_VERSION}".lower()
        return serial

    def build_google_wallet_object_body(*args, **kwargs):
        body = original_build_google_wallet_object_body(*args, **kwargs)
        card = args[1] if len(args) > 1 else kwargs.get("card")
        issuer_id = args[2] if len(args) > 2 else kwargs.get("issuer_id")

        if getattr(card, "level_snapshot", None) == 1:
            suffix = LEVEL_1_WALLET_VISUAL_VERSION
            if issuer_id and body.get("id", "").startswith(f"{issuer_id}.") and not body["id"].endswith(f"_{suffix}"):
                body["id"] = f"{body['id']}_{suffix}"

            hero = body.get("heroImage") or {}
            hero_source = hero.get("sourceUri") or {}
            if hero_source.get("uri"):
                hero_source["uri"] = _with_level_1_wallet_version(hero_source["uri"])

            for image_module in body.get("imageModulesData", []) or []:
                source = ((image_module.get("mainImage") or {}).get("sourceUri") or {})
                if source.get("uri"):
                    source["uri"] = _with_level_1_wallet_version(source["uri"])

        return body

    module.get_wallet_asset = get_wallet_asset
    module.member_apple_serial = member_apple_serial
    module.build_google_wallet_object_body = build_google_wallet_object_body
    _patch_asset_route(module, get_wallet_asset)
    module._mayu_level_1_wallet_visual_patch = True
    return module


def _mayu_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _original_import(name, globals, locals, fromlist, level)
    if name == "member_cards":
        _patch_member_cards(module)
    return module


Image.open = _mayu_image_open
builtins.__import__ = _mayu_import

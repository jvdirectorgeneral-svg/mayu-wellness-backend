from io import BytesIO
import os
import base64

from PIL import Image

from embedded_card_assets import LEVEL_1_CARD_COBRE_JPEG_BASE64


_original_image_open = Image.open


def _mayu_image_open(fp, *args, **kwargs):
    path = os.fspath(fp) if isinstance(fp, (str, bytes, os.PathLike)) else ""
    normalized_path = path.replace("\\", "/")

    if normalized_path.endswith("/assets/wallet_cobre.png"):
        image_bytes = base64.b64decode(LEVEL_1_CARD_COBRE_JPEG_BASE64)
        return _original_image_open(BytesIO(image_bytes), *args, **kwargs)

    return _original_image_open(fp, *args, **kwargs)


Image.open = _mayu_image_open

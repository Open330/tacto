"""Bounded explicit PNG disclosure over the existing provider relay."""
import base64

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def encode_image(png):
    if (not isinstance(png, bytes) or not 0 < len(png) <= MAX_IMAGE_BYTES
            or not png.startswith(b'\x89PNG\r\n\x1a\n')):
        raise ValueError('a PNG of at most 5 MiB is required')
    return {'media_type': 'image/png', 'data': base64.b64encode(png).decode('ascii')}


def decode_image(image):
    if (not isinstance(image, dict) or set(image) != {'media_type', 'data'}
            or image['media_type'] != 'image/png' or not isinstance(image['data'], str)
            or len(image['data']) > 4 * ((MAX_IMAGE_BYTES + 2) // 3)):
        raise ValueError('invalid image envelope')
    try:
        png = base64.b64decode(image['data'], validate=True)
    except (ValueError, UnicodeEncodeError):
        raise ValueError('invalid image envelope') from None
    if encode_image(png) != image:
        raise ValueError('noncanonical image envelope')
    return png


def validate_model(model):
    if model is not None and (not isinstance(model, str) or not model.strip()
            or len(model.encode('utf8')) > 128 or model.startswith('-')
            or any(ord(c) < 32 or ord(c) == 127 for c in model)):
        raise ValueError('invalid requested image model')
    return model

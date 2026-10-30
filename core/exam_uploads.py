"""One validation policy for all clinical image uploads."""
from io import BytesIO
import warnings

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_COUNT = 10
MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 16_000_000
FORMATS = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}


def validate_exam_image(upload):
    extension = (upload.name or '').rsplit('.', 1)[-1].lower()
    if extension not in FORMATS:
        raise ValidationError('Ảnh khám chỉ nhận JPG, JPEG, PNG hoặc WEBP.')
    if upload.size > MAX_IMAGE_BYTES:
        raise ValidationError('Mỗi ảnh khám tối đa 5 MB.')
    try:
        upload.seek(0)
        content = upload.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValidationError('Mỗi ảnh khám tối đa 5 MB.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                if (width < 1 or height < 1 or max(width, height) > MAX_DIMENSION
                        or width * height > MAX_PIXELS):
                    raise ValidationError('Ảnh khám tối đa 4096 px mỗi chiều và 16 triệu pixel.')
                if image.format != FORMATS[extension] or getattr(image, 'n_frames', 1) != 1:
                    raise ValidationError('Nội dung ảnh không khớp định dạng hoặc là ảnh động.')
                image.verify()
            # verify() alone does not decode every format, notably truncated JPEG.
            with Image.open(BytesIO(content)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValidationError('File không phải ảnh hợp lệ hoặc nội dung ảnh bị hỏng.') from exc
    finally:
        upload.seek(0)


def validate_exam_images(uploads, *, existing_count=0):
    if existing_count + len(uploads) > MAX_IMAGE_COUNT:
        raise ValidationError('Mỗi phiếu khám tối đa 10 ảnh, tính cả ảnh đang giữ lại.')
    if sum(upload.size for upload in uploads) > MAX_BATCH_BYTES:
        raise ValidationError('Tổng ảnh tải lên trong một lần tối đa 20 MB.')
    for upload in uploads:
        validate_exam_image(upload)

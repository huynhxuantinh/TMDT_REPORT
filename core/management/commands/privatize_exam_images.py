"""Move legacy medical images out of public media without changing stored names."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.private_media import private_exam_storage


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


class Command(BaseCommand):
    help = 'Kiểm tra/chuyển ảnh phiếu khám cũ sang private media. Dùng --apply để chuyển.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        public = Path(settings.MEDIA_ROOT).resolve()
        private = Path(private_exam_storage.location).resolve()
        source_root = public / 'phieu_kham'
        if not source_root.exists():
            self.stdout.write('Không có ảnh bệnh án trong media công khai.')
            return
        pairs = []
        for source in source_root.rglob('*'):
            if source.is_symlink():
                raise CommandError('Phát hiện symlink trong thư mục ảnh; cần kiểm tra thủ công.')
            if not source.is_file():
                continue
            resolved = source.resolve()
            target = (private / source.relative_to(public)).resolve()
            if public not in resolved.parents or private not in target.parents:
                raise CommandError('Đường dẫn ảnh nằm ngoài thư mục được phép.')
            if target.exists() and digest(resolved) != digest(target):
                raise CommandError('Ảnh đích đã tồn tại nhưng khác nội dung; không ghi đè.')
            pairs.append((resolved, target))
        if not options['apply']:
            self.stdout.write(f'Có {len(pairs)} ảnh cần chuyển. Chạy lại với --apply.')
            return
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.moving-')
                os.close(fd)
                try:
                    shutil.copyfile(source, temporary)
                    if digest(source) != digest(Path(temporary)):
                        raise CommandError('Kiểm tra nội dung ảnh sao chép thất bại; giữ ảnh gốc.')
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            if digest(source) != digest(target):
                raise CommandError('Ảnh thay đổi trong lúc chuyển; giữ ảnh gốc.')
            source.unlink()
        self.stdout.write(f'Đã chuyển và kiểm tra nội dung {len(pairs)} ảnh. Không đổi dữ liệu DB.')

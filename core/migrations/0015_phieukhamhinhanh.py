import core.models
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_benhvienhinhanh"),
    ]

    operations = [
        migrations.CreateModel(
            name="PhieuKhamHinhAnh",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "hinh_anh",
                    models.ImageField(
                        upload_to=core.models.phieu_kham_image_upload_to,
                        validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
                    ),
                ),
                ("mo_ta", models.CharField(blank=True, default="", max_length=255)),
                ("thu_tu", models.PositiveIntegerField(default=0)),
                ("ngay_tao", models.DateTimeField(auto_now_add=True)),
                (
                    "phieu_kham",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hinh_anhs", to="core.phieukham"),
                ),
            ],
            options={
                "verbose_name": "Hinh anh phieu kham",
                "verbose_name_plural": "Hinh anh phieu kham",
                "ordering": ["thu_tu", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="phieukhamhinhanh",
            index=models.Index(fields=["phieu_kham", "thu_tu"], name="core_phieuk_phieu_k_2177bf_idx"),
        ),
    ]

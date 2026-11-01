from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0032_hoadon_quan_tri_he_thong_permission')]

    operations = [
        migrations.AddField(
            model_name='hoadon', name='bhyt_da_xac_minh',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='hoadon', name='bhyt_ty_le_xac_minh',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]

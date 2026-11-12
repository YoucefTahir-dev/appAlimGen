from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0004_companysettings_article_number_companysettings_nis'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuthRateLimitBucket',
            fields=[
                ('key', models.CharField(editable=False, max_length=64, primary_key=True, serialize=False)),
                ('hits', models.PositiveIntegerField(default=0)),
                ('expires_at', models.DateTimeField()),
            ],
            options={
                'default_permissions': (),
                'indexes': [models.Index(fields=['expires_at'], name='auth_bucket_expiry_idx')],
            },
        ),
    ]

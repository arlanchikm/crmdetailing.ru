from django.conf import settings
from django.db import migrations
import uuid


def separate_owner(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Access = apps.get_model('tenants', 'PlatformAccess')
    Membership = apps.get_model('tenants', 'Membership')
    RecoveryCode = apps.get_model('accounts', 'RecoveryCode')
    platform_owner_email = settings.PLATFORM_OWNER_EMAIL
    access = Access.objects.select_related('user').filter(user__email__iexact=platform_owner_email).first()
    for row in Access.objects.all():
        if not row.email:
            row.email = row.user.email
            row.save(update_fields=['email'])
    if not access:
        return
    source = access.user
    if Access.objects.filter(email__iexact=platform_owner_email).exclude(pk=access.pk).exists():
        raise RuntimeError('Platform email is already in use; migration stopped.')
    # The internal Django identity has no memberships. Login/delivery use Access.email.
    target = User.objects.create(email=f'platform-{uuid.uuid4().hex}@accounts.invalid', password=source.password,
        first_name=source.first_name, last_name=source.last_name, is_active=True,
        email_2fa=source.email_2fa or access.email_2fa)
    RecoveryCode.objects.bulk_create([
        RecoveryCode(user=target, code_hash=code.code_hash, used_at=code.used_at)
        for code in RecoveryCode.objects.filter(user=source)
    ])
    access.email = platform_owner_email
    access.user = target
    access.email_2fa = False
    access.save(update_fields=['user', 'email_2fa', 'email'])


class Migration(migrations.Migration):
    dependencies = [('tenants', '0014_separate_platform_owner')]
    operations = [migrations.RunPython(separate_owner)]

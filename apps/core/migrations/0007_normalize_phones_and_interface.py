from django.db import migrations


def migrate_data(apps, schema_editor):
    from apps.core.phones import format_phone, phone_digits
    from django.core.exceptions import ValidationError
    for app,model in [('customers','Client'),('core','IntegrationLead'),('tenants','NetworkRequest')]:
        Model=apps.get_model(app,model)
        for row in Model.objects.only('pk','phone').iterator(chunk_size=500):
            values={}
            try: values={'phone':format_phone(row.phone),'phone_needs_review':False}
            except ValidationError: values={'phone_needs_review':True}
            if model=='Client': values['normalized_phone']=phone_digits(row.phone)
            Model.objects.filter(pk=row.pk).update(**values)
    apps.get_model('tenants','Tenant').objects.exclude(surface_color='#FFFFFF').update(surface_color='#FFFFFF')
    User=apps.get_model('accounts','User')
    for access in apps.get_model('tenants','PlatformAccess').objects.filter(email_2fa=True).iterator():
        User.objects.filter(pk=access.user_id).update(email_2fa=True)


class Migration(migrations.Migration):
    dependencies=[('core','0006_webpushconfig_integrationlead_phone_needs_review_and_more'),
                  ('customers','0008_client_phone_needs_review'),
                  ('tenants','0012_networkrequest_phone_needs_review_and_more'),
                  ('accounts','0003_authenticationattempt_and_more')]
    operations=[migrations.RunPython(migrate_data,migrations.RunPython.noop)]

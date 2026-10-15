from django.contrib import admin

from .models import AuditEvent, IntegrationChannel, IntegrationLead

admin.site.register(AuditEvent)
admin.site.register(IntegrationChannel)
admin.site.register(IntegrationLead)

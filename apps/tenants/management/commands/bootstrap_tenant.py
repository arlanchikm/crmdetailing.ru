import os
from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.tenants.models import Membership, Tenant
from apps.staff.models import ensure_default_roles
from apps.inventory.models import ensure_default_categories


class Command(BaseCommand):
    help = "Создает первую компанию и учетную запись владельца"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--owner-email", required=True)
        parser.add_argument("--owner-password")

    @transaction.atomic
    def handle(self, *args, **options):
        tenant, tenant_created = Tenant.objects.get_or_create(
            slug=options["slug"].lower(),
            defaults={"name": options["name"]},
        )
        User = get_user_model()
        email = options["owner_email"].lower()
        user, user_created = User.objects.get_or_create(email=email)
        if user_created or options["owner_password"]:
            password = (
                options["owner_password"]
                or os.getenv("BOOTSTRAP_OWNER_PASSWORD")
                or getpass("Пароль владельца: ")
            )
            if len(password) < 10:
                raise CommandError("Пароль должен содержать не менее 10 символов")
            user.set_password(password)
            user.save(update_fields=("password",))
        membership, membership_created = Membership.objects.update_or_create(
            tenant=tenant,
            user=user,
            defaults={"role": Membership.Role.OWNER, "status": Membership.Status.ACTIVE},
        )
        ensure_default_roles(tenant)
        ensure_default_categories(tenant)

        self.stdout.write(
            self.style.SUCCESS(
                f"Компания {tenant.slug}, владелец {user.email}; "
                f"created={tenant_created}/{user_created}/{membership_created}"
            )
        )

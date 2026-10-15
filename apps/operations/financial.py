from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from .models import Appointment, CashShift, PaymentSettings, PaymentTransaction


def payment_fee_rate(tenant, method):
    settings, _ = PaymentSettings.objects.get_or_create(tenant=tenant)
    return settings.rate_for(method)


def recalculate_payment_state(appointment):
    payment_rows = appointment.payments.values("kind").annotate(total=Sum("amount"), fees=Sum("fee_amount"))
    paid = refunded = fees = Decimal("0")
    for row in payment_rows:
        if row["kind"] == PaymentTransaction.Kind.PAYMENT:
            paid += row["total"] or 0
            fees += row["fees"] or 0
        else:
            refunded += row["total"] or 0
            fees -= row["fees"] or 0
    net = paid - refunded
    appointment.amount_paid = net
    appointment.payment_fee = fees
    if net >= appointment.total and appointment.total > 0:
        appointment.payment_status = Appointment.PaymentStatus.PAID
    elif net > 0:
        appointment.payment_status = Appointment.PaymentStatus.PARTIAL
    elif refunded > 0:
        appointment.payment_status = Appointment.PaymentStatus.REFUNDED
    else:
        appointment.payment_status = Appointment.PaymentStatus.UNPAID
    appointment.save(update_fields=("amount_paid", "payment_fee", "payment_status", "updated_at"))
    return appointment


@transaction.atomic
def add_payment_transaction(*, appointment, kind, method, amount, comment, actor):
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if kind == PaymentTransaction.Kind.REFUND and amount > appointment.amount_paid:
        from django.core.exceptions import ValidationError
        raise ValidationError("Возврат не может превышать оплаченную сумму.")
    rate = payment_fee_rate(appointment.tenant, method)
    fee = (amount * rate / Decimal("100")).quantize(Decimal("0.01"))
    shift = CashShift.objects.select_for_update().filter(
        tenant=appointment.tenant, closed_at__isnull=True
    ).first() if method == Appointment.PaymentMethod.CASH else None
    if method == Appointment.PaymentMethod.CASH and not shift:
        try:
            with transaction.atomic():
                shift = CashShift.objects.create(
                    tenant=appointment.tenant,
                    opened_by=actor,
                    opening_cash=Decimal("0"),
                    expected_cash=Decimal("0"),
                )
        except IntegrityError:
            shift = CashShift.objects.select_for_update().get(
                tenant=appointment.tenant, closed_at__isnull=True
            )
    transaction_row = PaymentTransaction.objects.create(
        tenant=appointment.tenant, appointment=appointment, cash_shift=shift,
        kind=kind, method=method, amount=amount, fee_amount=fee,
        comment=comment, created_by=actor,
    )
    if shift:
        direction = Decimal("1") if kind == PaymentTransaction.Kind.PAYMENT else Decimal("-1")
        shift.expected_cash = shift.expected_cash + amount * direction
        shift.save(update_fields=("expected_cash",))
    appointment.payment_method = method
    appointment.save(update_fields=("payment_method", "updated_at"))
    recalculate_payment_state(appointment)
    return transaction_row


@transaction.atomic
def delete_payment_transaction(*, payment):
    payment = PaymentTransaction.objects.select_for_update().get(pk=payment.pk)
    appointment = Appointment.objects.select_for_update().get(pk=payment.appointment_id)
    if payment.cash_shift_id:
        shift = CashShift.objects.select_for_update().get(pk=payment.cash_shift_id)
        direction = Decimal("-1") if payment.kind == PaymentTransaction.Kind.PAYMENT else Decimal("1")
        shift.expected_cash += payment.amount * direction
        fields = ["expected_cash"]
        if shift.actual_cash is not None:
            shift.difference = shift.actual_cash - shift.expected_cash
            fields.append("difference")
        shift.save(update_fields=fields)
    payment.delete()
    last_payment = appointment.payments.order_by("-created_at").first()
    appointment.payment_method = last_payment.method if last_payment else ""
    appointment.save(update_fields=("payment_method", "updated_at"))
    recalculate_payment_state(appointment)
    return appointment

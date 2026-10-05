from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orders.models import (
    Order,
    OrderStatus,
    PaymentStatus,
    PaymentTransaction,
)


User = get_user_model()


class OrderSuccessViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="customer@example.com",
            password="test-password-123",
        )

        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="test-password-123",
        )

        self.order = Order.objects.create(
            user=self.user,
            order_number="ORD-TEST-001",
            customer_email="customer@example.com",
            subtotal=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("10.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("110.00"),
            currency="TRY",
            status=OrderStatus.PAID,

            shipping_full_name="Test Customer",
            shipping_phone="05550000000",
            shipping_address_line1="Test Mahallesi 1",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Istanbul",
            shipping_postal_code="34000",

            billing_full_name="Test Customer",
            billing_phone="05550000000",
            billing_address_line1="Test Mahallesi 1",
            billing_address_line2="",
            billing_city="Istanbul",
            billing_state="Istanbul",
            billing_postal_code="34000",
        )

        self.payment_transaction = (
            PaymentTransaction.objects.create(
                order=self.order,
                provider="iyzico",
                conversation_id="CONV-TEST-001",
                payment_id="PAYMENT-TEST-001",
                status=PaymentStatus.SUCCESS,
                paid_price=Decimal("110.00"),
                currency="TRY",
            )
        )

    # =====================================================================
    # AUTHENTICATED USER
    # =====================================================================

    def test_authenticated_user_can_view_own_success_page(self):
        self.client.force_login(self.user)

        url = reverse(
            "orders:order_success",
            kwargs={
                "order_number": self.order.order_number,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertTemplateUsed(
            response,
            "orders/order_success.html",
        )

        self.assertEqual(
            response.context["order"],
            self.order,
        )

        self.assertEqual(
            response.context["payment_transaction"],
            self.payment_transaction,
        )

    def test_authenticated_user_cannot_view_another_users_order(self):
        self.client.force_login(self.other_user)

        url = reverse(
            "orders:order_success",
            kwargs={
                "order_number": self.order.order_number,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    # =====================================================================
    # PAYMENT VALIDATION
    # =====================================================================

    def test_success_page_requires_successful_payment(self):
        self.payment_transaction.status = (
            PaymentStatus.FAILED
        )

        self.payment_transaction.save(
            update_fields=["status"]
        )

        self.client.force_login(self.user)

        url = reverse(
            "orders:order_success",
            kwargs={
                "order_number": self.order.order_number,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    # =====================================================================
    # GUEST
    # =====================================================================

    def test_guest_can_view_order_bound_to_session(self):
        guest_order = Order.objects.create(
            user=None,
            order_number="ORD-GUEST-001",
            customer_email="guest@example.com",
            subtotal=Decimal("50.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("50.00"),
            currency="TRY",
            status=OrderStatus.PAID,

            shipping_full_name="Guest Customer",
            shipping_phone="05550000001",
            shipping_address_line1="Guest Address",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Istanbul",
            shipping_postal_code="34000",

            billing_full_name="Guest Customer",
            billing_phone="05550000001",
            billing_address_line1="Guest Address",
            billing_address_line2="",
            billing_city="Istanbul",
            billing_state="Istanbul",
            billing_postal_code="34000",
        )

        guest_payment = PaymentTransaction.objects.create(
            order=guest_order,
            provider="iyzico",
            conversation_id="CONV-GUEST-001",
            payment_id="PAYMENT-GUEST-001",
            status=PaymentStatus.SUCCESS,
            paid_price=Decimal("50.00"),
            currency="TRY",
        )

        session = self.client.session

        session["checkout_order_number"] = (
            guest_order.order_number
        )

        session.save()

        url = reverse(
            "orders:order_success",
            kwargs={
                "order_number": guest_order.order_number,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.context["order"],
            guest_order,
        )

        self.assertEqual(
            response.context["payment_transaction"],
            guest_payment,
        )

    def test_guest_cannot_view_order_not_bound_to_session(self):
        guest_order = Order.objects.create(
            user=None,
            order_number="ORD-GUEST-002",
            customer_email="guest@example.com",
            subtotal=Decimal("50.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("50.00"),
            currency="TRY",
            status=OrderStatus.PAID,

            shipping_full_name="Guest Customer",
            shipping_phone="05550000001",
            shipping_address_line1="Guest Address",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Istanbul",
            shipping_postal_code="34000",

            billing_full_name="Guest Customer",
            billing_phone="05550000001",
            billing_address_line1="Guest Address",
            billing_address_line2="",
            billing_city="Istanbul",
            billing_state="Istanbul",
            billing_postal_code="34000",
        )

        PaymentTransaction.objects.create(
            order=guest_order,
            provider="iyzico",
            conversation_id="CONV-GUEST-002",
            payment_id="PAYMENT-GUEST-002",
            status=PaymentStatus.SUCCESS,
            paid_price=Decimal("50.00"),
            currency="TRY",
        )

        session = self.client.session

        session["checkout_order_number"] = (
            "SOME-OTHER-ORDER"
        )

        session.save()

        url = reverse(
            "orders:order_success",
            kwargs={
                "order_number": guest_order.order_number,
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )

    # =====================================================================
    # ORDER NOT FOUND
    # =====================================================================

    def test_unknown_order_returns_404(self):
        self.client.force_login(self.user)

        url = reverse(
            "orders:order_success",
            kwargs={
                "order_number": "DOES-NOT-EXIST",
            },
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            404,
        )
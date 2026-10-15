from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase

import iyzipay
from django.urls import reverse
from django.utils import timezone

from accounts.models import SellerProfile
from orders.models import (
    CargoCompany,
    Order,
    OrderItem,
    OrderStatus,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
    SubOrder,
    SubOrderStatus,
    ReturnDeliverySource,
    PaymentTransaction, PaymentStatus, PaymentTransactionItem, PaymentTransactionItemType, PaymentRefund, PaymentRefundItem,
    RefundStatus
)
from orders.services.returns import ReturnService
from orders.services.refund import RefundService
from products.models import (
    Product,
    ProductVariant,
    StoreProduct,
    StoreProductStatus,
    ProductStatus,
)
from store.models import Store


User = get_user_model()


class CustomerReturnViewTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create(
            email="view-customer@example.com",
            first_name="Test",
            last_name="Customer",
        )

        self.other_customer = User.objects.create(
            email="view-other@example.com",
            first_name="Other",
            last_name="Customer",
        )

        self.seller_user = User.objects.create(
            email="view-seller@example.com",
            first_name="Test",
            last_name="Seller",
        )

        seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            company_name="View Test Seller",
            company_address="Test Address",
            iban="TR00000000000000000000000000",
            is_approved=True,
        )

        self.store = Store.objects.create(
            seller=seller_profile,
            store_name="View Test Store",
            slug="view-test-store",
            contact_email="view-store@example.com",
            contact_phone="5551234567",
            address="Test Address",
            is_active=True,
        )

        product = Product.objects.create(
            name="View Test Product",
            description="Test product",
            status=ProductStatus.ACTIVE,
        )

        variant = ProductVariant.objects.create(
            product=product,
            is_active=True,
        )

        self.store_product = StoreProduct.objects.create(
            store=self.store,
            variant=variant,
            sku="VIEW-RETURN-SKU",
            price=Decimal("100.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        self.order = self._create_order(user=self.customer)

        self.suborder = self._create_suborder(self.order)

        self.order_item = OrderItem.objects.create(
            sub_order=self.suborder,
            store_product=self.store_product,
            product_name_snapshot="View Test Product",
            variant_snapshot={"Varyant": "Test"},
            variant_display="Varyant: Test",
            sku_snapshot=self.store_product.sku,
            quantity=2,
            unit_price=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
        )

        self.create_url = reverse(
            "accounts:customer_return_create",
            kwargs={
                "order_number": self.order.order_number,
                "order_item_id": self.order_item.pk,
            },
        )

        self.shipment_url = lambda return_request: reverse(
            "accounts:customer_return_shipment",
            kwargs={
                "order_number": self.order.order_number,
                "return_request_id": return_request.pk,
            },
        )

    def _create_order(self, *, user):
        return Order.objects.create(
            user=user,
            buyer_identity_number="12345678901",
            customer_email=user.email,
            customer_phone="5551234567",
            subtotal=Decimal("200.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            currency="TRY",
            status=OrderStatus.DELIVERED,
            shipping_full_name="Test Customer",
            shipping_phone="5551234567",
            shipping_address_line1="Test Street 1",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Kadikoy",
            shipping_postal_code="34710",
            billing_full_name="Test Customer",
            billing_phone="5551234567",
            billing_address_line1="Test Street 1",
            billing_address_line2="",
            billing_city="Istanbul",
            billing_state="Kadikoy",
            billing_postal_code="34710",
        )

    def _create_suborder(self, order):
        return SubOrder.objects.create(
            order=order,
            store=self.store,
            store_name_snapshot=self.store.store_name,
            subtotal=Decimal("200.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            status=SubOrderStatus.DELIVERED,
            delivered_at=timezone.now(),
        )

    def _create_return_request(self):
        return ReturnService.create_return_request(
            order_item_id=self.order_item.pk,
            requested_by=self.customer,
            reason=ReturnReason.WITHDRAWAL,
        )

    # ---------------------------------------------------------
    # CREATE RETURN
    # ---------------------------------------------------------

    def test_customer_can_create_return_request(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            self.create_url,
            {
                "reason": ReturnReason.WITHDRAWAL,
                "customer_note": "",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "accounts:order_detail",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        return_request = ReturnRequest.objects.get(
            order_item=self.order_item,
        )

        self.assertEqual(
            return_request.requested_by_id,
            self.customer.pk,
        )
        self.assertEqual(
            return_request.reason,
            ReturnReason.WITHDRAWAL,
        )
        self.assertEqual(
            return_request.status,
            ReturnStatus.AUTHORIZED,
        )
        self.assertEqual(return_request.unit_number, 1)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.post(
            self.create_url,
            {
                "reason": ReturnReason.WITHDRAWAL,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

        self.assertFalse(
            ReturnRequest.objects.filter(
                order_item=self.order_item,
            ).exists()
        )

    def test_customer_cannot_create_return_for_another_customer_order(self):
        self.client.force_login(self.other_customer)

        response = self.client.post(
            self.create_url,
            {
                "reason": ReturnReason.WITHDRAWAL,
            },
        )

        self.assertEqual(response.status_code, 404)

        self.assertFalse(
            ReturnRequest.objects.filter(
                order_item=self.order_item,
            ).exists()
        )

    def test_invalid_reason_does_not_create_return_request(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            self.create_url,
            {
                "reason": "invalid_reason",
                "customer_note": "",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "accounts:order_detail",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.assertFalse(
            ReturnRequest.objects.filter(
                order_item=self.order_item,
            ).exists()
        )

    def test_other_reason_requires_customer_note(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            self.create_url,
            {
                "reason": ReturnReason.OTHER,
                "customer_note": " ",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "accounts:order_detail",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        self.assertFalse(
            ReturnRequest.objects.filter(
                order_item=self.order_item,
            ).exists()
        )

    # ---------------------------------------------------------
    # RETURN SHIPMENT
    # ---------------------------------------------------------

    def test_customer_can_submit_return_shipment(self):
        self.client.force_login(self.customer)
        return_request = self._create_return_request()

        response = self.client.post(
            self.shipment_url(return_request),
            {
                "cargo_company": CargoCompany.values[0],
                "tracking_number": "VIEW-TRACK-123",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "accounts:order_detail",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.IN_TRANSIT,
        )
        self.assertEqual(
            return_request.return_tracking_number,
            "VIEW-TRACK-123",
        )
        self.assertIsNotNone(return_request.return_shipped_at)

    def test_other_customer_cannot_submit_return_shipment(self):
        return_request = self._create_return_request()
        self.client.force_login(self.other_customer)

        response = self.client.post(
            self.shipment_url(return_request),
            {
                "cargo_company": CargoCompany.values[0],
                "tracking_number": "VIEW-TRACK-456",
            },
        )

        self.assertEqual(response.status_code, 404)

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.AUTHORIZED,
        )
        self.assertFalse(return_request.return_tracking_number)

    def test_invalid_tracking_number_does_not_change_return(self):
        self.client.force_login(self.customer)
        return_request = self._create_return_request()

        response = self.client.post(
            self.shipment_url(return_request),
            {
                "cargo_company": CargoCompany.values[0],
                "tracking_number": " ",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "accounts:order_detail",
                kwargs={"order_number": self.order.order_number},
            ),
        )

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.AUTHORIZED,
        )
        self.assertFalse(return_request.return_tracking_number)


class SellerReturnViewTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create(
            email="seller-return-customer@example.com",
            first_name="Test",
            last_name="Customer",
        )

        self.seller_user = User.objects.create(
            email="seller-return-seller@example.com",
            first_name="Test",
            last_name="Seller",
            is_seller=True,
        )

        self.seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            company_name="Seller Return Test",
            company_address="Test Address",
            iban="TR00000000000000000000000000",
            is_approved=True,
        )

        self.store = Store.objects.create(
            seller=self.seller_profile,
            store_name="Seller Return Store",
            slug="seller-return-store",
            contact_email="seller-return-store@example.com",
            contact_phone="5551234567",
            address="Test Address",
            is_active=True,
            status="approved",
        )

        # Aynı satıcının ikinci mağazası.
        self.other_store = Store.objects.create(
            seller=self.seller_profile,
            store_name="Other Return Store",
            slug="other-return-store",
            contact_email="other-return-store@example.com",
            contact_phone="5551234568",
            address="Other Address",
            is_active=True,
            status="approved",
        )

        product = Product.objects.create(
            name="Seller Return Product",
            description="Test product",
            status=ProductStatus.ACTIVE,
        )

        variant = ProductVariant.objects.create(
            product=product,
            is_active=True,
        )

        self.store_product = StoreProduct.objects.create(
            store=self.store,
            variant=variant,
            sku="SELLER-RETURN-SKU",
            price=Decimal("100.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        self.order = self._create_order()
        self.suborder = self._create_suborder(
            order=self.order,
            store=self.store,
        )
        self.order_item = self._create_order_item()

        self.client.force_login(self.seller_user)

    # ---------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------

    def _create_order(self):
        return Order.objects.create(
            user=self.customer,
            buyer_identity_number="12345678901",
            customer_email=self.customer.email,
            customer_phone="5551234567",
            subtotal=Decimal("200.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            currency="TRY",
            status=OrderStatus.DELIVERED,
            shipping_full_name="Test Customer",
            shipping_phone="5551234567",
            shipping_address_line1="Test Street 1",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Kadikoy",
            shipping_postal_code="34710",
            billing_full_name="Test Customer",
            billing_phone="5551234567",
            billing_address_line1="Test Street 1",
            billing_address_line2="",
            billing_city="Istanbul",
            billing_state="Kadikoy",
            billing_postal_code="34710",
        )

    def _create_suborder(self, *, order, store):
        return SubOrder.objects.create(
            order=order,
            store=store,
            store_name_snapshot=store.store_name,
            subtotal=Decimal("200.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            status=SubOrderStatus.DELIVERED,
            delivered_at=timezone.now(),
        )

    def _create_order_item(self):
        return OrderItem.objects.create(
            sub_order=self.suborder,
            store_product=self.store_product,
            product_name_snapshot="Seller Return Product",
            variant_snapshot={"Varyant": "Test"},
            variant_display="Varyant: Test",
            sku_snapshot=self.store_product.sku,
            quantity=2,
            unit_price=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
        )

    def _confirm_url(self, return_request, *, store=None, suborder=None):
        return reverse(
            "store:store_return_delivery_confirm",
            kwargs={
                "store_slug": (store or self.store).slug,
                "suborder_number": (
                    suborder or self.suborder
                ).suborder_number,
                "return_request_id": return_request.pk,
            },
        )

    def _approve_url(self, return_request, *, store=None, suborder=None):
        return reverse(
            "store:store_return_approve",
            kwargs={
                "store_slug": (store or self.store).slug,
                "suborder_number": (
                    suborder or self.suborder
                ).suborder_number,
                "return_request_id": return_request.pk,
            },
        )

    def _create_return_request(self):
        return ReturnService.create_return_request(
            order_item_id=self.order_item.pk,
            requested_by=self.customer,
            reason=ReturnReason.WITHDRAWAL,
        )

    def _create_in_transit_return(self):
        return_request = self._create_return_request()

        return ReturnService.mark_return_shipped(
            return_request_id=return_request.pk,
            requested_by=self.customer,
            cargo_company=CargoCompany.values[0],
            tracking_number="SELLER-RETURN-TRACK-123",
        )

    def _create_reviewable_return(self):
        return_request = self._create_in_transit_return()

        return ReturnService.confirm_return_delivered(
            return_request_id=return_request.pk,
            source=ReturnDeliverySource.SELLER,
            store=self.store,
            confirmed_by=self.seller_user,
        )

    def _create_successful_payment(self):
        payment_transaction = PaymentTransaction.objects.create(
            order=self.order,
            provider=RefundService.PROVIDER,
            payment_id="PAY-" + uuid4().hex[:16],
            conversation_id=uuid4().hex,
            basket_id="BASKET-" + uuid4().hex[:16],
            status=PaymentStatus.SUCCESS,
            paid_price=Decimal("200.00"),
            currency="TRY",
        )

        transaction_item = PaymentTransactionItem.objects.create(
            payment_transaction=payment_transaction,
            suborder=self.suborder,
            order_item=self.order_item,
            item_type=PaymentTransactionItemType.PRODUCT,
            provider_item_id=f"OI-{self.order_item.pk}",
            provider_transaction_id="PTX-RETURN-UNIT",
            price=Decimal("200.00"),
            paid_price=Decimal("200.00"),
            transaction_status=1,
        )

        return payment_transaction, transaction_item

    # ---------------------------------------------------------
    # DELIVERY CONFIRMATION
    # ---------------------------------------------------------

    def test_seller_can_confirm_return_delivery(self):
        return_request = self._create_in_transit_return()

        response = self.client.post(
            self._confirm_url(return_request),
        )

        self.assertRedirects(
            response,
            reverse(
                "store:store_order_detail",
                kwargs={
                    "store_slug": self.store.slug,
                    "suborder_number": self.suborder.suborder_number,
                },
            ),
        )

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.AWAITING_SELLER_REVIEW,
        )
        self.assertEqual(
            return_request.delivery_confirmation_source,
            ReturnDeliverySource.SELLER,
        )
        self.assertEqual(
            return_request.delivery_confirmed_by_id,
            self.seller_user.pk,
        )
        self.assertIsNotNone(return_request.delivered_at)
        self.assertIsNotNone(return_request.seller_review_deadline)

    def test_seller_cannot_confirm_delivery_before_shipment(self):
        return_request = self._create_return_request()

        response = self.client.post(
            self._confirm_url(return_request),
        )

        self.assertEqual(response.status_code, 302)

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.AUTHORIZED,
        )
        self.assertIsNone(return_request.delivered_at)

    def test_seller_cannot_confirm_return_through_another_store(self):
        return_request = self._create_in_transit_return()

        response = self.client.post(
            self._confirm_url(
                return_request,
                store=self.other_store,
            ),
        )

        self.assertEqual(response.status_code, 404)

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.IN_TRANSIT,
        )
        self.assertIsNone(return_request.delivered_at)

    # ---------------------------------------------------------
    # RETURN APPROVAL
    # ---------------------------------------------------------

    def test_seller_cannot_approve_return_before_delivery_review(self):
        return_request = self._create_return_request()

        response = self.client.post(
            self._approve_url(return_request),
        )

        self.assertEqual(response.status_code, 302)

        return_request.refresh_from_db()

        self.assertEqual(
            return_request.status,
            ReturnStatus.AUTHORIZED,
        )
        self.assertIsNone(return_request.payment_refund_id)

        self.assertFalse(
            PaymentRefund.objects.filter(
                suborder=self.suborder,
            ).exists()
        )

    @patch.object(RefundService, "_call_provider")
    def test_seller_can_approve_return_and_process_refund(
        self,
        mock_provider,
    ):
        return_request = self._create_reviewable_return()
        payment_transaction, transaction_item = (
            self._create_successful_payment()
        )

        store_product = return_request.order_item.store_product
        store_product.sold_count = return_request.order_item.quantity
        store_product.save(update_fields=["sold_count"])

        def successful_refund_response(**kwargs):
            """RefundService'in doğrulama gereksinimlerini karşılayan mock."""
            amount = Decimal(str(kwargs["amount"]))
            response = {
                "status": "success",
                "paymentTransactionId": kwargs["payment_transaction_id"],
                "paymentId": payment_transaction.payment_id,
                "conversationId": kwargs["conversation_id"],
                # İki adetlik 200 TL satırın bir adetlik iadesi.
                "price": f"{amount:.2f}",
                "currency": kwargs["currency"],
                "refundHostReference": "HOST-RETURN-UNIT",
                "retryable": False,
            }

            # Üretim kodu iyzico imzasını paymentId, normalize edilmiş price,
            # currency ve conversationId alanlarıyla doğruluyor.
            signature_price = format(
                Decimal(response["price"]).normalize(),
                "f",
            )
            response["signature"] = (
                iyzipay.Refund().calculate_hmac_sha256_signature(
                    [
                        response["paymentId"],
                        signature_price,
                        response["currency"],
                        response["conversationId"],
                    ],
                    settings.IYZICO_SECRET_KEY,
                )
            )
            return response

        mock_provider.side_effect = successful_refund_response

        response = self.client.post(
            self._approve_url(return_request),
        )

        self.assertRedirects(
            response,
            reverse(
                "store:store_order_detail",
                kwargs={
                    "store_slug": self.store.slug,
                    "suborder_number": self.suborder.suborder_number,
                },
            ),
        )

        return_request.refresh_from_db()
        payment_transaction.refresh_from_db()

        self.assertIsNotNone(return_request.payment_refund_id)

        refund = PaymentRefund.objects.get(
            pk=return_request.payment_refund_id,
        )
        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            refund.amount,
            Decimal("100.00"),
        )
        self.assertEqual(
            refund_item.payment_transaction_item_id,
            transaction_item.pk,
        )
        self.assertEqual(
            refund_item.amount,
            Decimal("100.00"),
        )
        self.assertEqual(
            refund.status,
            RefundStatus.SUCCESS,
        )
        self.assertEqual(
            refund_item.status,
            RefundStatus.SUCCESS,
        )
        self.assertEqual(
            refund_item.provider_refund_id,
            "HOST-RETURN-UNIT",
        )

        mock_provider.assert_called_once()

    @patch.object(RefundService, "_call_provider")
    def test_approval_handles_provider_timeout(
        self,
        mock_provider,
    ):
        return_request = self._create_reviewable_return()
        self._create_successful_payment()

        store_product = return_request.order_item.store_product
        store_product.sold_count = return_request.order_item.quantity
        store_product.save(update_fields=["sold_count"])

        mock_provider.side_effect = TimeoutError(
            "Simulated provider timeout"
        )

        response = self.client.post(
            self._approve_url(return_request),
        )

        self.assertRedirects(
            response,
            reverse(
                "store:store_order_detail",
                kwargs={
                    "store_slug": self.store.slug,
                    "suborder_number": self.suborder.suborder_number,
                },
            ),
        )

        return_request.refresh_from_db()

        self.assertIsNotNone(return_request.payment_refund_id)

        refund = PaymentRefund.objects.get(
            pk=return_request.payment_refund_id,
        )
        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(
            refund_item.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        mock_provider.assert_called_once()
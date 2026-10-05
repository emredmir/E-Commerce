from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import SellerProfile
from store.models import Store
from products.models import (
    Product,
    ProductVariant,
    StoreProduct,
    StoreProductStatus,
    ProductStatus,
)
from orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    PaymentRefund,
    PaymentRefundItem,
    PaymentStatus,
    PaymentTransaction,
    PaymentTransactionItem,
    RefundStatus,
    SubOrder,
    SubOrderStatus,
    PaymentTransactionItemType,
)


from orders.services.refund import (
    RefundError,
    RefundService,
)
from datetime import timedelta

from django.utils import timezone


User = get_user_model()


class RefundServiceTests(TestCase):
    """
    RefundService integration tests.

    Test kapsamı:

        - Tek SubOrder tam refund
        - İlk SubOrder partial refund
        - İkinci SubOrder refund
        - Aynı refund'ın tekrar çalıştırılması
        - Provider timeout -> reconciliation
        - Retryable provider failure
        - Non-retryable provider failure
        - Refund amount validation
        - Stable conversation_id
        - PaymentTransaction status progression
    """

    # ======================================================================
    # SETUP
    # ======================================================================

    def setUp(self):
        self.user = User.objects.create(
            email="customer@example.com",
            first_name="Test",
            last_name="Customer",
        )

        self.seller = SellerProfile.objects.create(
            user=self.user,
            company_name="Test Seller",
            company_address="Test Address",
            iban="TR00000000000000000000000000",
            is_approved=True,
        )

        self.store_a = self._create_store(
            slug="store-a",
            name="Store A",
        )

        self.store_b = self._create_store(
            slug="store-b",
            name="Store B",
        )

        self.product = self._create_product()

        self.variant = self._create_variant(
            product=self.product,
        )

    # ======================================================================
    # HELPERS
    # ======================================================================
    def _create_product(self):
        return Product.objects.create(
            name="Test Product",
            description="Test product description",
            status=ProductStatus.ACTIVE,
        )


    def _create_variant(self, *, product):
        return ProductVariant.objects.create(
            product=product,
            is_active=True,
        )

    def _create_store_product(self, *, store):
        return StoreProduct.objects.create(
            store=store,
            variant=self.variant,
            sku=f"{store.slug}-TEST-SKU",
            price=Decimal("500.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )
    def _create_order_item(
        self,
        *,
        suborder,
        store_product,
        total,
    ):
        return OrderItem.objects.create(
            sub_order=suborder,
            store_product=store_product,

            product_name_snapshot=store_product.variant.product.name,
            variant_snapshot={
                "Varyant": "Test Variant",
            },
            variant_display="Varyant: Test Variant",
            sku_snapshot=store_product.sku or "TEST-SKU",

            quantity=1,
            unit_price=total,
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=total,
        )

    def _create_store(self, *, slug, name):
        return Store.objects.create(
            seller=self.seller,
            store_name=name,
            slug=slug,
            contact_email=f"{slug}@example.com",
            contact_phone="5551234567",
            address="Test Address",
            is_active=True,
        )


    def _create_order(
        self,
        *,
        total=Decimal("1000.00"),
        status=OrderStatus.PAID,
    ):
        return Order.objects.create(
            user=self.user,
            buyer_identity_number="12345678901",
            customer_email="customer@example.com",
            customer_phone="5551234567",

            subtotal=total,
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=total,
            currency="TRY",

            status=status,

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

    def _create_suborder(
        self,
        *,
        order,
        store,
        total,
        status=SubOrderStatus.CANCELLED,
    ):
        return SubOrder.objects.create(
            order=order,
            store=store,
            store_name_snapshot=store.store_name,

            subtotal=total,
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=total,

            status=status,
        )


    def _create_payment(
        self,
        *,
        order,
        amount=Decimal("1000.00"),
        status=PaymentStatus.SUCCESS,
    ):
        return PaymentTransaction.objects.create(
            order=order,
            provider="iyzico",
            payment_id="PAY-" + uuid4().hex[:16],
            conversation_id=uuid4().hex,
            basket_id="BASKET-" + uuid4().hex[:16],

            status=status,
            paid_price=amount,
            currency="TRY",
        )

    def _create_transaction_item(
        self,
        *,
        payment_transaction,
        order_item,
        suborder,
        price,
        provider_transaction_id,
    ):
        return PaymentTransactionItem.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder,
            order_item=order_item,
            item_type=PaymentTransactionItemType.PRODUCT,
    
            provider_item_id=f"OI-{order_item.pk}",
            provider_transaction_id=provider_transaction_id,
    
            price=price,
            paid_price=price,
    
            transaction_status=1,
        )

    def _create_refund(
        self,
        *,
        payment_transaction,
        suborder,
        amount,
        status=RefundStatus.PENDING,
    ):
        return PaymentRefund.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=amount,
            currency="TRY",
            status=status,
            reason="Test refund",
        )

    # ======================================================================
    # 1. SINGLE SUBORDER / FULL REFUND
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_single_suborder_full_refund(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )


        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        transaction_item = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-500",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "success",
            "paymentTransactionId": "PTX-500",
            "price": "500.00",
            "currency": "TRY",
            "refundHostReference": "HOST-REF-500",
            "retryable": False,
        }

        result = RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()
        payment_transaction.refresh_from_db()

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            result.status,
            RefundStatus.SUCCESS,
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
            "HOST-REF-500",
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.REFUNDED,
        )

        mock_provider.assert_called_once()

        call_kwargs = mock_provider.call_args.kwargs

        self.assertEqual(
            call_kwargs["payment_transaction_id"],
            transaction_item.provider_transaction_id,
        )

        self.assertEqual(
            call_kwargs["amount"],
            Decimal("500.00"),
        )

        self.assertEqual(
            call_kwargs["currency"],
            "TRY",
        )

    # ======================================================================
    # 2. FIRST SUBORDER / PARTIAL REFUND
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_first_suborder_partial_refund(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("1000.00"),
        )

        suborder_a = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        suborder_b = self._create_suborder(
            order=order,
            store=self.store_b,
            total=Decimal("500.00"),
        )

        store_product_a = self._create_store_product(
            store=self.store_a,
        )

        store_product_b = self._create_store_product(
            store=self.store_b,
        )

        item_a = self._create_order_item(
            suborder=suborder_a,
            store_product=store_product_a,
            total=Decimal("500.00"),
        )

        item_b = self._create_order_item(
            suborder=suborder_b,
            store_product=store_product_b,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("1000.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_a,
            suborder=suborder_a,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-A",
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_b,
            suborder=suborder_b,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-B",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder_a,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "success",
            "paymentTransactionId": "PTX-A",
            "price": "500.00",
            "currency": "TRY",
            "refundHostReference": "HOST-A",
            "retryable": False,
        }

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()
        payment_transaction.refresh_from_db()

        self.assertEqual(
            refund.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.PARTIALLY_REFUNDED,
        )

    # ======================================================================
    # 3. SECOND SUBORDER / REMAINING REFUND
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_second_suborder_refund_after_partial_refund(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("1000.00"),
        )

        suborder_a = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        suborder_b = self._create_suborder(
            order=order,
            store=self.store_b,
            total=Decimal("500.00"),
        )

        store_product_a = self._create_store_product(
            store=self.store_a,
        )
        
        store_product_b = self._create_store_product(
            store=self.store_b,
        )
        
        item_a = self._create_order_item(
            suborder=suborder_a,
            store_product=store_product_a,
            total=Decimal("500.00"),
        )
        
        item_b = self._create_order_item(
            suborder=suborder_b,
            store_product=store_product_b,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("1000.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_a,
            suborder=suborder_a,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-A",
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_b,
            suborder=suborder_b,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-B",
        )

        refund_a = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder_a,
            amount=Decimal("500.00"),
        )

        refund_b = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder_b,
            amount=Decimal("500.00"),
        )

        mock_provider.side_effect = [
            {
                "status": "success",
                "paymentTransactionId": "PTX-A",
                "price": "500.00",
                "currency": "TRY",
                "refundHostReference": "HOST-A",
                "retryable": False,
            },
            {
                "status": "success",
                "paymentTransactionId": "PTX-B",
                "price": "500.00",
                "currency": "TRY",
                "refundHostReference": "HOST-B",
                "retryable": False,
            },
        ]

        RefundService.process_refund(
            payment_refund_id=refund_a.pk,
        )

        payment_transaction.refresh_from_db()

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.PARTIALLY_REFUNDED,
        )

        RefundService.process_refund(
            payment_refund_id=refund_b.pk,
        )

        payment_transaction.refresh_from_db()
        refund_b.refresh_from_db()

        self.assertEqual(
            refund_b.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.REFUNDED,
        )

        self.assertEqual(
            mock_provider.call_count,
            2,
        )

    # ======================================================================
    # 4. IDEMPOTENCY
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_same_refund_does_not_call_provider_twice(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-IDEM",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "success",
            "paymentTransactionId": "PTX-IDEM",
            "price": "500.00",
            "currency": "TRY",
            "refundHostReference": "HOST-IDEM",
            "retryable": False,
        }

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        self.assertEqual(
            mock_provider.call_count,
            1,
        )

    # ======================================================================
    # 5. TIMEOUT / RECONCILIATION
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_provider_timeout_requires_reconciliation(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-TIMEOUT",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.side_effect = TimeoutError(
            "iyzico timeout"
        )

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            refund_item.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

    # ======================================================================
    # 6. RETRYABLE FAILURE
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_retryable_provider_failure_keeps_refund_pending(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )
        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-RETRY",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "failure",
            "errorCode": "TEMPORARY_ERROR",
            "errorMessage": "Temporary provider error",
            "retryable": True,
        }

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            refund_item.status,
            RefundStatus.FAILED,
        )

        self.assertTrue(
            refund_item.retryable,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.PENDING,
        )

    # ======================================================================
    # 7. NON-RETRYABLE FAILURE
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_non_retryable_provider_failure_fails_refund(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-FAIL",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "failure",
            "errorCode": "INVALID_REFUND",
            "errorMessage": "Invalid refund",
            "retryable": False,
        }

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            refund_item.status,
            RefundStatus.FAILED,
        )

        self.assertFalse(
            refund_item.retryable,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.FAILED,
        )

    # ======================================================================
    # 8. REFUND AMOUNT > ITEM REFUNDABLE AMOUNT
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_refund_amount_cannot_exceed_order_item_amount(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-LIMIT",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("600.00"),
        )

        with self.assertRaises(RefundError):
            RefundService.process_refund(
                payment_refund_id=refund.pk,
            )

        mock_provider.assert_not_called()

        self.assertEqual(
            PaymentRefundItem.objects.count(),
            0,
        )

    # ======================================================================
    # 9. STABLE CONVERSATION ID ACROSS RETRY
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_conversation_id_remains_stable_on_retry(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-STABLE",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.side_effect = [
            {
                "status": "failure",
                "errorCode": "TEMP",
                "errorMessage": "Temporary",
                "retryable": True,
            },
            {
                "status": "success",
                "paymentTransactionId": "PTX-STABLE",
                "price": "500.00",
                "currency": "TRY",
                "refundHostReference": "HOST-STABLE",
                "retryable": False,
            },
        ]

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        first_conversation_id = (
            refund_item.conversation_id
        )

        # Simüle edilen retry.
        refund_item.status = RefundStatus.PENDING
        refund_item.retryable = True
        refund_item.save(
            update_fields=[
                "status",
                "retryable",
                "updated_at",
            ]
        )

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund_item.refresh_from_db()

        second_conversation_id = (
            refund_item.conversation_id
        )

        self.assertEqual(
            first_conversation_id,
            second_conversation_id,
        )

        self.assertEqual(
            mock_provider.call_count,
            2,
        )

        first_call = mock_provider.call_args_list[0]
        second_call = mock_provider.call_args_list[1]

        self.assertEqual(
            first_call.kwargs["conversation_id"],
            second_call.kwargs["conversation_id"],
        )

    # ======================================================================
    # 10. SUCCESS RESPONSE MISMATCH -> RECONCILIATION
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_success_response_mismatch_requires_reconciliation(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-MISMATCH",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "success",

            # Bilerek yanlış.
            "paymentTransactionId": "PTX-WRONG",

            "price": "500.00",
            "currency": "TRY",
            "refundHostReference": "HOST-WRONG",
        }

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(
            refund_item.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

    # ======================================================================
    # 11. RETRYABLE FAILURE -> RETRY -> SUCCESS
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_retryable_failed_refund_can_be_retried_successfully(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        transaction_item = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-RETRY-SUCCESS",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
            status=RefundStatus.PENDING,
        )

        conversation_id = (
            f"RF-{refund.refund_reference}-existing"
        )

        refund_item = PaymentRefundItem.objects.create(
            payment_refund=refund,
            payment_transaction_item=transaction_item,
            amount=Decimal("500.00"),
            status=RefundStatus.FAILED,
            retryable=True,
            conversation_id=conversation_id,
        )

        mock_provider.return_value = {
            "status": "success",
            "paymentTransactionId": "PTX-RETRY-SUCCESS",
            "price": "500.00",
            "currency": "TRY",
            "refundHostReference": "HOST-RETRY-SUCCESS",
            "retryable": False,
        }

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()
        refund_item.refresh_from_db()
        payment_transaction.refresh_from_db()

        self.assertEqual(
            refund.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            refund_item.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            refund_item.retryable,
            False,
        )

        self.assertEqual(
            refund_item.conversation_id,
            conversation_id,
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.REFUNDED,
        )

        mock_provider.assert_called_once()

        call_kwargs = mock_provider.call_args.kwargs

        self.assertEqual(
            call_kwargs["payment_transaction_id"],
            transaction_item.provider_transaction_id,
        )

        self.assertEqual(
            call_kwargs["amount"],
            Decimal("500.00"),
        )

        self.assertEqual(
            call_kwargs["conversation_id"],
            conversation_id,
        )

    # ======================================================================
    # 12. RECONCILIATION REQUIRED -> NO AUTOMATIC RETRY
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_reconciliation_required_refund_is_not_retried(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-RECON",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.side_effect = TimeoutError(
            "iyzico timeout"
        )

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()

        self.assertEqual(
            refund.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        with self.assertRaises(RefundError):
            RefundService.process_refund(
                payment_refund_id=refund.pk,
            )

        self.assertEqual(
            mock_provider.call_count,
            1,
        )

    # ======================================================================
    # 13. STALE PROCESSING -> RECONCILIATION REQUIRED
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_stale_processing_item_requires_reconciliation(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        transaction_item = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-STALE",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        refund_item = PaymentRefundItem.objects.create(
            payment_refund=refund,
            payment_transaction_item=transaction_item,
            amount=Decimal("500.00"),
            status=RefundStatus.PROCESSING,
            conversation_id=f"RF-{refund.refund_reference}-stale",
        )

        stale_time = (
            timezone.now()
            - timedelta(
                minutes=RefundService.PROCESSING_TIMEOUT_MINUTES + 1
            )
        )

        PaymentRefundItem.objects.filter(
            pk=refund_item.pk,
        ).update(
            updated_at=stale_time,
        )

        RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()
        refund_item.refresh_from_db()

        self.assertEqual(
            refund_item.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        self.assertIsNone(
            refund_item.retryable,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        mock_provider.assert_not_called()

    # ======================================================================
    # 14. MULTI-ITEM SUBORDER -> MULTIPLE REFUND ITEMS
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_multi_item_suborder_creates_multiple_refund_items(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product_a = self._create_store_product(
            store=self.store_a,
        )

        variant_b = ProductVariant.objects.create(
            product=self.product,
            is_active=True,
        )
        
        store_product_b = StoreProduct.objects.create(
            store=self.store_a,
            variant=variant_b,
            sku="store-a-SECOND-SKU",
            price=Decimal("300.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        item_a = self._create_order_item(
            suborder=suborder,
            store_product=store_product_a,
            total=Decimal("200.00"),
        )

        item_b = self._create_order_item(
            suborder=suborder,
            store_product=store_product_b,
            total=Decimal("300.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        transaction_item_a = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_a,
            suborder=suborder,
            price=Decimal("200.00"),
            provider_transaction_id="PTX-MULTI-A",
        )

        transaction_item_b = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_b,
            suborder=suborder,
            price=Decimal("300.00"),
            provider_transaction_id="PTX-MULTI-B",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.side_effect = [
            {
                "status": "success",
                "paymentTransactionId": "PTX-MULTI-A",
                "price": "200.00",
                "currency": "TRY",
                "refundHostReference": "HOST-MULTI-A",
                "retryable": False,
            },
            {
                "status": "success",
                "paymentTransactionId": "PTX-MULTI-B",
                "price": "300.00",
                "currency": "TRY",
                "refundHostReference": "HOST-MULTI-B",
                "retryable": False,
            },
        ]

        result = RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()
        payment_transaction.refresh_from_db()

        refund_items = list(
            PaymentRefundItem.objects
            .filter(payment_refund=refund)
            .select_related("payment_transaction_item")
            .order_by("id")
        )

        self.assertEqual(
            len(refund_items),
            2,
        )

        self.assertEqual(
            result.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.REFUNDED,
        )

        self.assertEqual(
            refund_items[0].payment_transaction_item_id,
            transaction_item_a.pk,
        )

        self.assertEqual(
            refund_items[0].amount,
            Decimal("200.00"),
        )

        self.assertEqual(
            refund_items[0].status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            refund_items[0].provider_refund_id,
            "HOST-MULTI-A",
        )

        self.assertEqual(
            refund_items[1].payment_transaction_item_id,
            transaction_item_b.pk,
        )

        self.assertEqual(
            refund_items[1].amount,
            Decimal("300.00"),
        )

        self.assertEqual(
            refund_items[1].status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            refund_items[1].provider_refund_id,
            "HOST-MULTI-B",
        )

        self.assertEqual(
            sum(item.amount for item in refund_items),
            Decimal("500.00"),
        )

        self.assertEqual(
            mock_provider.call_count,
            2,
        )

        first_call = mock_provider.call_args_list[0].kwargs
        second_call = mock_provider.call_args_list[1].kwargs

        self.assertEqual(
            first_call["payment_transaction_id"],
            "PTX-MULTI-A",
        )

        self.assertEqual(
            first_call["amount"],
            Decimal("200.00"),
        )

        self.assertEqual(
            second_call["payment_transaction_id"],
            "PTX-MULTI-B",
        )

        self.assertEqual(
            second_call["amount"],
            Decimal("300.00"),
        )

    # ======================================================================
    # 15. PROVIDER SUCCESS + LOCAL DB APPLY FAILURE
    #     -> RECONCILIATION REQUIRED
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    @patch.object(
        RefundService,
        "_apply_provider_response",
        side_effect=Exception("Simulated local DB failure"),
    )
    def test_provider_success_but_local_apply_failure_requires_reconciliation(
        self,
        mock_apply_provider_response,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        store_product = self._create_store_product(
            store=self.store_a,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            store_product=store_product,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        transaction_item = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=order_item,
            suborder=suborder,
            price=Decimal("500.00"),
            provider_transaction_id="PTX-LOCAL-FAIL",
        )

        refund = self._create_refund(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
        )

        mock_provider.return_value = {
            "status": "success",
            "paymentTransactionId": "PTX-LOCAL-FAIL",
            "price": "500.00",
            "currency": "TRY",
            "refundHostReference": "HOST-LOCAL-FAIL",
            "retryable": False,
        }

        result = RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        payment_transaction.refresh_from_db()

        self.assertEqual(
            result.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        self.assertEqual(
            refund_item.status,
            RefundStatus.RECONCILIATION_REQUIRED,
        )

        self.assertIsNone(
            refund_item.provider_refund_id,
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.SUCCESS,
        )

        mock_provider.assert_called_once()

        mock_apply_provider_response.assert_called_once()

        call_kwargs = mock_provider.call_args.kwargs

        self.assertEqual(
            call_kwargs["payment_transaction_id"],
            transaction_item.provider_transaction_id,
        )

        self.assertEqual(
            call_kwargs["amount"],
            Decimal("500.00"),
        )

        self.assertEqual(
            call_kwargs["currency"],
            "TRY",
        )

    # ======================================================================
    # 16. MULTI-ITEM SUBORDER + SHIPPING -> MULTIPLE REFUND ITEMS
    # ======================================================================

    @patch.object(RefundService, "_call_provider")
    def test_multi_item_suborder_refunds_products_and_shipping(
        self,
        mock_provider,
    ):
        order = self._create_order(
            total=Decimal("500.00"),
        )

        suborder = SubOrder.objects.create(
            order=order,
            store=self.store_a,
            store_name_snapshot=self.store_a.store_name,

            subtotal=Decimal("400.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),

            status=SubOrderStatus.CANCELLED,
        )

        store_product_a = self._create_store_product(
            store=self.store_a,
        )

        variant_b = ProductVariant.objects.create(
            product=self.product,
            is_active=True,
        )

        store_product_b = StoreProduct.objects.create(
            store=self.store_a,
            variant=variant_b,
            sku="store-a-SHIPPING-SECOND-SKU",
            price=Decimal("200.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        item_a = self._create_order_item(
            suborder=suborder,
            store_product=store_product_a,
            total=Decimal("200.00"),
        )

        item_b = self._create_order_item(
            suborder=suborder,
            store_product=store_product_b,
            total=Decimal("200.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("500.00"),
        )

        transaction_item_a = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_a,
            suborder=suborder,
            price=Decimal("200.00"),
            provider_transaction_id="PTX-SHIP-A",
        )

        transaction_item_b = self._create_transaction_item(
            payment_transaction=payment_transaction,
            order_item=item_b,
            suborder=suborder,
            price=Decimal("200.00"),
            provider_transaction_id="PTX-SHIP-B",
        )

        shipping_transaction_item = PaymentTransactionItem.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder,
            order_item=None,
            item_type=PaymentTransactionItemType.SHIPPING,

            provider_item_id=f"SHIP-{suborder.pk}",
            provider_transaction_id="PTX-SHIP-C",

            price=Decimal("100.00"),
            paid_price=Decimal("100.00"),

            transaction_status=1,
        )

        refund = PaymentRefund.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder,
            amount=Decimal("500.00"),
            currency="TRY",
            status=RefundStatus.PENDING,
            reason="Test refund",
            refund_shipping=True,
        )

        def provider_response(**kwargs):
            responses = {
                "PTX-SHIP-A": {
                    "status": "success",
                    "paymentTransactionId": "PTX-SHIP-A",
                    "price": "200.00",
                    "currency": "TRY",
                    "refundHostReference": "HOST-SHIP-A",
                    "retryable": False,
                },
                "PTX-SHIP-B": {
                    "status": "success",
                    "paymentTransactionId": "PTX-SHIP-B",
                    "price": "200.00",
                    "currency": "TRY",
                    "refundHostReference": "HOST-SHIP-B",
                    "retryable": False,
                },
                "PTX-SHIP-C": {
                    "status": "success",
                    "paymentTransactionId": "PTX-SHIP-C",
                    "price": "100.00",
                    "currency": "TRY",
                    "refundHostReference": "HOST-SHIP-C",
                    "retryable": False,
                },
            }

            return responses[kwargs["payment_transaction_id"]]

        mock_provider.side_effect = provider_response

        result = RefundService.process_refund(
            payment_refund_id=refund.pk,
        )

        refund.refresh_from_db()
        payment_transaction.refresh_from_db()

        refund_items = list(
            PaymentRefundItem.objects
            .filter(payment_refund=refund)
            .select_related("payment_transaction_item")
            .order_by("id")
        )

        self.assertEqual(
            len(refund_items),
            3,
        )

        self.assertEqual(
            result.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            refund.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            payment_transaction.status,
            PaymentStatus.REFUNDED,
        )

        item_map = {
            item.payment_transaction_item_id: item
            for item in refund_items
        }

        product_refund_a = item_map[transaction_item_a.pk]
        product_refund_b = item_map[transaction_item_b.pk]
        shipping_refund = item_map[shipping_transaction_item.pk]

        self.assertEqual(
            product_refund_a.amount,
            Decimal("200.00"),
        )

        self.assertEqual(
            product_refund_a.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            product_refund_a.payment_transaction_item.item_type,
            PaymentTransactionItemType.PRODUCT,
        )

        self.assertEqual(
            product_refund_a.provider_refund_id,
            "HOST-SHIP-A",
        )

        self.assertEqual(
            product_refund_b.amount,
            Decimal("200.00"),
        )

        self.assertEqual(
            product_refund_b.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            product_refund_b.payment_transaction_item.item_type,
            PaymentTransactionItemType.PRODUCT,
        )

        self.assertEqual(
            product_refund_b.provider_refund_id,
            "HOST-SHIP-B",
        )

        self.assertEqual(
            shipping_refund.amount,
            Decimal("100.00"),
        )

        self.assertEqual(
            shipping_refund.status,
            RefundStatus.SUCCESS,
        )

        self.assertEqual(
            shipping_refund.payment_transaction_item.item_type,
            PaymentTransactionItemType.SHIPPING,
        )

        self.assertEqual(
            shipping_refund.provider_refund_id,
            "HOST-SHIP-C",
        )

        self.assertEqual(
            sum(item.amount for item in refund_items),
            Decimal("500.00"),
        )

        self.assertEqual(
            mock_provider.call_count,
            3,
        )
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import SellerProfile
from store.models import Store

from orders.exceptions import (
    CancellationError,
    ReservationError,
)

from orders.models import (
    Order,
    OrderStatus,
    PaymentRefund,
    PaymentStatus,
    PaymentTransaction,
    RefundStatus,
    SubOrder,
    SubOrderCancellation,
    SubOrderStatus,
    PaymentTransactionItem,
    PaymentRefundItem,
    OrderItem, PaymentTransactionItemType,
    SubOrderItemCancellation, SubOrderItemCancellationUnit,
    RefundType
)
from products.models import (
    Product,
    ProductVariant,
    StoreProduct,
    StoreProductStatus,
    ProductStatus,
)

from orders.services.refund import RefundService
from orders.services.cancellation import (
    CancellationService,
)


User = get_user_model()


class CancellationServiceTests(TestCase):

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

        self.store_a = Store.objects.create(
            seller=self.seller,
            store_name="Store A",
            slug="store-a",
            contact_email="store-a@example.com",
            contact_phone="5551234567",
            address="Test",
            is_active=True,
        )

        self.store_b = Store.objects.create(
            seller=self.seller,
            store_name="Store B",
            slug="store-b",
            contact_email="store-b@example.com",
            contact_phone="5551234567",
            address="Test",
            is_active=True,
        )

    # ======================================================================
    # HELPERS
    # ======================================================================

    
    def _create_order(self, total=Decimal("1000.00")):
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

            status=OrderStatus.PAID,

            shipping_full_name="Test Customer",
            shipping_phone="5551234567",
            shipping_address_line1="Test Street",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Kadikoy",
            shipping_postal_code="34710",

            billing_full_name="Test Customer",
            billing_phone="5551234567",
            billing_address_line1="Test Street",
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
        total=Decimal("500.00"),
        status=SubOrderStatus.PENDING,
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
        from uuid import uuid4

        return PaymentTransaction.objects.create(
            order=order,
            provider="iyzico",
            payment_id=uuid4().hex,
            conversation_id=uuid4().hex,
            basket_id=uuid4().hex,

            status=status,
            paid_price=amount,
            currency="TRY",
        )

    
    def _create_order_item(
        self,
        *,
        suborder,
        quantity,
        unit_price=Decimal("100.00"),
    ):
        product = Product.objects.create(
            name=f"Cancellation Product {suborder.pk}",
            description="Cancellation test product",
            status=ProductStatus.ACTIVE,
        )

        variant = ProductVariant.objects.create(
            product=product,
            is_active=True,
        )

        store_product = StoreProduct.objects.create(
            store=suborder.store,
            variant=variant,
            sku=f"CANCEL-SKU-{suborder.pk}",
            price=unit_price,
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        return OrderItem.objects.create(
            sub_order=suborder,
            store_product=store_product,
            product_name_snapshot=product.name,
            variant_snapshot={"Varyant": "Test"},
            variant_display="Varyant: Test",
            sku_snapshot=store_product.sku,
            quantity=quantity,
            unit_price=unit_price,
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=unit_price * quantity,
        )


    def _create_product_payment_item(
        self,
        *,
        payment_transaction,
        suborder,
        order_item,
        paid_price,
    ):
        return PaymentTransactionItem.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder,
            order_item=order_item,
            item_type=PaymentTransactionItemType.PRODUCT,
            provider_item_id=f"CANCEL-ITEM-{order_item.pk}",
            provider_transaction_id=f"CANCEL-PTX-{order_item.pk}",
            price=paid_price,
            paid_price=paid_price,
        )


    # ======================================================================
    # 1. NORMAL CANCELLATION
    # ======================================================================

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_cancel_suborder_creates_refund_and_snapshot(
        self,
        mock_restore,
    ):
        order = self._create_order()

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
        )

        order_item = self._create_order_item(
            suborder=suborder,
            quantity=5,
        )

        self._create_product_payment_item(
            payment_transaction=payment_transaction,
            suborder=suborder,
            order_item=order_item,
            paid_price=Decimal("500.00"),
        )

        cancellation = (
            CancellationService.cancel_suborder(
                suborder=suborder,
                cancelled_by=self.user,
                reason="Stok sebebiyle iptal",
            )
        )

        suborder.refresh_from_db()

        self.assertEqual(
            suborder.status,
            SubOrderStatus.CANCELLED,
        )

        self.assertIsNotNone(
            suborder.cancelled_at,
        )

        payment_refund = cancellation.payment_refund

        self.assertIsNotNone(
            payment_refund,
        )

        self.assertEqual(
            payment_refund.status,
            RefundStatus.PENDING,
        )

        self.assertEqual(
            payment_refund.amount,
            Decimal("500.00"),
        )

        cancellation_record = (
            SubOrderCancellation.objects.get(
                suborder=suborder,
            )
        )

        self.assertEqual(
            cancellation_record.refund_amount,
            Decimal("500.00"),
        )

        self.assertEqual(
            cancellation_record.payment_refund_id,
            payment_refund.pk,
        )

        mock_restore.assert_called_once()

        self.assertEqual(
            payment_refund.payment_transaction_id,
            payment_transaction.pk,
        )

    # ======================================================================
    # 2. SHIPPING SONRASI CANCELLATION YOK
    # ======================================================================

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_shipped_suborder_cannot_be_cancelled(
        self,
        mock_restore,
    ):
        order = self._create_order()

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            status=SubOrderStatus.SHIPPED,
        )

        self._create_payment(
            order=order,
        )

        with self.assertRaises(CancellationError):
            CancellationService.cancel_suborder(
                suborder=suborder,
                cancelled_by=self.user,
                reason="İptal",
            )

        suborder.refresh_from_db()

        self.assertEqual(
            suborder.status,
            SubOrderStatus.SHIPPED,
        )

        self.assertFalse(
            PaymentRefund.objects.exists(),
        )

        mock_restore.assert_not_called()

    # ======================================================================
    # 3. İKİNCİ KEZ CANCELLATION YOK
    # ======================================================================

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_already_cancelled_suborder_cannot_be_cancelled_again(
        self,
        mock_restore,
    ):
        order = self._create_order()

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            status=SubOrderStatus.CANCELLED,
        )

        self._create_payment(
            order=order,
        )

        with self.assertRaises(CancellationError):
            CancellationService.cancel_suborder(
                suborder=suborder,
                cancelled_by=self.user,
                reason="Tekrar iptal",
            )

        mock_restore.assert_not_called()

    # ======================================================================
    # 4. REFUND LIMIT
    # ======================================================================

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_cancellation_cannot_exceed_remaining_refund_amount(
        self,
        mock_restore,
    ):
        order = self._create_order(
            total=Decimal("1000.00"),
        )

        suborder_a = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("600.00"),
            status=SubOrderStatus.CANCELLED,
        )

        suborder_b = self._create_suborder(
            order=order,
            store=self.store_b,
            total=Decimal("500.00"),
            status=SubOrderStatus.PENDING,
        )

        payment_transaction = self._create_payment(
            order=order,
        )

        order_item_b = self._create_order_item(
            suborder=suborder_b,
            quantity=5,
        )

        self._create_product_payment_item(
            payment_transaction=payment_transaction,
            suborder=suborder_b,
            order_item=order_item_b,
            paid_price=Decimal("500.00"),
        )

        PaymentRefund.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder_a,
            amount=Decimal("600.00"),
            currency="TRY",
            status=RefundStatus.PENDING,
            reason="Previous cancellation",
        )

        with self.assertRaises(CancellationError):
            CancellationService.cancel_suborder(
                suborder=suborder_b,
                cancelled_by=self.user,
                reason="İptal",
            )

        suborder_b.refresh_from_db()

        self.assertEqual(
            suborder_b.status,
            SubOrderStatus.PENDING,
        )

        mock_restore.assert_not_called()

    # ======================================================================
    # 5. STOCK RESTORE HATASI -> ROLLBACK
    # ======================================================================

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_stock_restore_failure_rolls_back_cancellation(
        self,
        mock_restore,
    ):
        order = self._create_order(total=Decimal("1000.00"))

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
        )

        payment = self._create_payment(
            order=order,
            amount=Decimal("1000.00"),
        )

        order_item = self._create_order_item(
            suborder=suborder,
            quantity=5,
            unit_price=Decimal("100.00"),
        )

        self._create_product_payment_item(
            payment_transaction=payment,
            suborder=suborder,
            order_item=order_item,
            paid_price=Decimal("500.00"),
        )

        self._create_payment(
            order=order,
        )

        mock_restore.side_effect = ReservationError(
            "Stock restore failed",
        )

        with self.assertRaises(CancellationError):
            CancellationService.cancel_suborder(
                suborder=suborder,
                cancelled_by=self.user,
                reason="İptal",
            )

        suborder.refresh_from_db()

        self.assertEqual(
            suborder.status,
            SubOrderStatus.PENDING,
        )

        self.assertFalse(
            PaymentRefund.objects.exists(),
        )

        self.assertFalse(
            SubOrderCancellation.objects.exists(),
        )

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_second_suborder_can_be_cancelled_after_partial_refund(
        self,
        mock_restore,
    ):
        order = self._create_order()

        suborder_a = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("500.00"),
            status=SubOrderStatus.CANCELLED,
        )

        suborder_b = self._create_suborder(
            order=order,
            store=self.store_b,
            total=Decimal("500.00"),
            status=SubOrderStatus.PENDING,
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("1000.00"),
            status=PaymentStatus.PARTIALLY_REFUNDED,
        )

        order_item_b = self._create_order_item(
            suborder=suborder_b,
            quantity=5,
        )

        self._create_product_payment_item(
            payment_transaction=payment_transaction,
            suborder=suborder_b,
            order_item=order_item_b,
            paid_price=Decimal("500.00"),
        )

        PaymentRefund.objects.create(
            payment_transaction=payment_transaction,
            suborder=suborder_a,
            amount=Decimal("500.00"),
            currency="TRY",
            status=RefundStatus.SUCCESS,
            reason="First cancellation",
        )

        cancellation = (
            CancellationService.cancel_suborder(
                suborder=suborder_b,
                cancelled_by=self.user,
                reason="İkinci mağaza iptali",
            )
        )

        suborder_b.refresh_from_db()
        cancellation.refresh_from_db()

        self.assertEqual(
            suborder_b.status,
            SubOrderStatus.CANCELLED,
        )

        self.assertEqual(
            cancellation.refund_amount,
            Decimal("500.00"),
        )

        self.assertEqual(
            cancellation.payment_refund.status,
            RefundStatus.PENDING,
        )

        mock_restore.assert_called_once()

    @patch(
        "orders.services.cancellation."
        "StockReservationService.restore_cancelled_quantity"
    )
    def test_cancel_items_cancels_only_requested_quantity(
        self,
        mock_restore,
    ):
        order = self._create_order(
            total=Decimal("300.00"),
        )

        suborder = self._create_suborder(
            order=order,
            store=self.store_a,
            total=Decimal("300.00"),
        )

        payment_transaction = self._create_payment(
            order=order,
            amount=Decimal("300.00"),
        )

        order_item = self._create_order_item(
            suborder=suborder,
            quantity=3,
        )

        self._create_product_payment_item(
            payment_transaction=payment_transaction,
            suborder=suborder,
            order_item=order_item,
            paid_price=Decimal("300.00"),
        )

        CancellationService.cancel_items(
            suborder=suborder,
            items={order_item.pk: 1},
            cancelled_by=self.user,
            reason="Satıcı tarafından kısmi iptal",
        )

        cancellation = SubOrderItemCancellation.objects.get(
            order_item=order_item,
        )

        self.assertEqual(cancellation.quantity, 1)
        self.assertEqual(
            cancellation.refund_amount,
            Decimal("100.00"),
        )

        self.assertEqual(
            list(
                SubOrderItemCancellationUnit.objects.filter(
                    cancellation=cancellation,
                ).values_list("unit_number", flat=True)
            ),
            [1],
        )

        suborder.refresh_from_db()
        self.assertEqual(suborder.status, SubOrderStatus.PENDING)

        self.assertFalse(
            SubOrderCancellation.objects.filter(
                suborder=suborder,
            ).exists()
        )

        mock_restore.assert_called_once_with(
            order_item_id=order_item.pk,
            quantity=1,
        )

        refund = PaymentRefund.objects.get(
            suborder=suborder,
            refund_type=RefundType.SELLER_PARTIAL_CANCELLATION,
        )

        RefundService._ensure_refund_items(
            refund=refund,
            suborder=suborder,
            payment_transaction=payment_transaction,
        )

        refund_item = PaymentRefundItem.objects.get(
            payment_refund=refund,
        )

        self.assertEqual(refund_item.amount, Decimal("100.00"))
        self.assertEqual(
            refund_item.payment_transaction_item.order_item_id,
            order_item.pk,
        )


    
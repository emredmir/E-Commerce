from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from cart.models import CartItem
from cart.services.cart import CartService
from orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    PaymentTransaction,
    PaymentTransactionItem,
)
from orders.services.payment import PaymentService

from datetime import timedelta

from django.contrib.auth import get_user_model

from accounts.models import SellerProfile
from cart.models import Cart, CartItem
from cart.services.cart import CartService
from orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    SubOrder,
)
from products.models import (
    Product,
    ProductStatus,
    ProductVariant,
    StoreProduct,
    StoreProductStatus,
)
from store.models import Store, StoreStatus


class CartCleanupTests(TestCase):

    def _mock_order_items(self, order_items):
        manager = MagicMock()

        (
            manager
            .select_related.return_value
            .filter.return_value
            .order_by.return_value
        ) = order_items

        return manager

    def test_unchanged_cart_item_is_deleted(self):
        timestamp = timezone.now()

        cart_item = MagicMock()
        cart_item.updated_at = timestamp

        order_item = MagicMock()
        order_item.source_cart_item = cart_item
        order_item.source_cart_item_updated_at = timestamp

        manager = self._mock_order_items(
            [order_item]
        )

        with patch.object(
            OrderItem,
            "objects",
            manager,
        ):
            deleted_count = (
                CartService.clear_order_items(
                    order=MagicMock(pk=1),
                )
            )

        self.assertEqual(
            deleted_count,
            1,
        )

        cart_item.delete.assert_called_once()

    def test_changed_cart_item_is_not_deleted(self):
        checkout_timestamp = timezone.now()

        changed_timestamp = (
            checkout_timestamp
            + timedelta(seconds=1)
        )

        cart_item = MagicMock()
        cart_item.updated_at = changed_timestamp

        order_item = MagicMock()
        order_item.source_cart_item = cart_item
        order_item.source_cart_item_updated_at = (
            checkout_timestamp
        )

        manager = self._mock_order_items(
            [order_item]
        )

        with patch.object(
            OrderItem,
            "objects",
            manager,
        ):
            deleted_count = (
                CartService.clear_order_items(
                    order=MagicMock(pk=1),
                )
            )

        self.assertEqual(
            deleted_count,
            0,
        )

        cart_item.delete.assert_not_called()

    def test_already_deleted_cart_item_is_ignored(self):
        order_item = MagicMock()
        order_item.source_cart_item = None

        manager = self._mock_order_items(
            [order_item]
        )

        with patch.object(
            OrderItem,
            "objects",
            manager,
        ):
            deleted_count = (
                CartService.clear_order_items(
                    order=MagicMock(pk=1),
                )
            )

        self.assertEqual(
            deleted_count,
            0,
        )


class SuccessfulPaymentFinalizationTests(TestCase):

    def _build_payment_transaction(
        self,
        *,
        status=PaymentStatus.PENDING,
    ):
        order = SimpleNamespace(
            id=100,
            order_number="ORD-TEST-100",
            status=OrderStatus.PENDING_PAYMENT,
            total_amount=Decimal("100.00"),
            subtotal=Decimal("100.00"),
            currency="TRY",
            save=MagicMock(),
        )

        payment_tx = SimpleNamespace(
            id=200,
            order_id=order.id,
            order=order,
            provider="iyzico",
            conversation_id="CONV-100",
            basket_id="BASKET-100",
            payment_id=None,
            status=status,
            paid_price=Decimal("100.00"),
            currency="TRY",
            fraud_status=None,
            card_type=None,
            card_association=None,
            last_four_digits=None,
            succeeded_at=None,
            save=MagicMock(),
        )

        return payment_tx

    def _patch_payment_transaction(
        self,
        payment_tx,
    ):
        manager = MagicMock()

        (
            manager
            .select_for_update.return_value
            .select_related.return_value
            .get.return_value
        ) = payment_tx

        return patch.object(
            PaymentTransaction,
            "objects",
            manager,
        )

    def _patch_order(self, order):
        manager = MagicMock()

        (
            manager
            .select_for_update.return_value
            .get.return_value
        ) = order

        return patch.object(
            Order,
            "objects",
            manager,
        )

    def test_successful_payment_cleans_cart(self):
        payment_tx = self._build_payment_transaction()

        response = {
            "paymentId": "PAY-100",
            "conversationId": "CONV-100",
            "paidPrice": "100.00",
            "currency": "TRY",
            "itemTransactions": [],
        }

        with (
            self._patch_payment_transaction(payment_tx),
            self._patch_order(payment_tx.order),
            patch(
                "orders.services.payment."
                "PaymentService._persist_payment_transaction_items"
            ) as persist_items,
            patch(
                "orders.services.payment."
                "StockReservationService.consume_order"
            ) as consume_order,
            patch(
                "orders.services.payment."
                "CartService.clear_order_items"
            ) as clear_cart,
        ):
            result = (
                PaymentService._finalize_successful_payment(
                    payment_id="PAY-100",
                    conversation_id="CONV-100",
                    response=response,
                )
            )

        self.assertEqual(
            result.order_id,
            payment_tx.order_id,
        )

        self.assertEqual(
            payment_tx.status,
            PaymentStatus.SUCCESS,
        )

        self.assertEqual(
            payment_tx.order.status,
            OrderStatus.PAID,
        )

        persist_items.assert_called_once_with(
            payment_transaction=payment_tx,
            response=response,
        )

        consume_order.assert_called_once_with(
            order=payment_tx.order,
        )

        clear_cart.assert_called_once_with(
            order=payment_tx.order,
        )

    def test_second_successful_callback_is_idempotent(self):
        payment_tx = self._build_payment_transaction()

        response = {
            "paymentId": "PAY-100",
            "conversationId": "CONV-100",
            "paidPrice": "100.00",
            "currency": "TRY",
            "itemTransactions": [],
        }

        with (
            self._patch_payment_transaction(payment_tx),
            self._patch_order(payment_tx.order),
            patch(
                "orders.services.payment."
                "PaymentService._persist_payment_transaction_items"
            ) as persist_items,
            patch(
                "orders.services.payment."
                "StockReservationService.consume_order"
            ) as consume_order,
            patch(
                "orders.services.payment."
                "CartService.clear_order_items"
            ) as clear_cart,
        ):
            first_result = (
                PaymentService._finalize_successful_payment(
                    payment_id="PAY-100",
                    conversation_id="CONV-100",
                    response=response,
                )
            )

            second_result = (
                PaymentService._finalize_successful_payment(
                    payment_id="PAY-100",
                    conversation_id="CONV-100",
                    response=response,
                )
            )

        self.assertEqual(
            first_result.order_id,
            second_result.order_id,
        )

        self.assertEqual(
            first_result.payment_transaction_id,
            second_result.payment_transaction_id,
        )

        persist_items.assert_called_once_with(
            payment_transaction=payment_tx,
            response=response,
        )

        consume_order.assert_called_once_with(
            order=payment_tx.order,
        )

        clear_cart.assert_called_once_with(
            order=payment_tx.order,
        )

    def test_cart_cleanup_failure_does_not_fail_payment(self):
        payment_tx = self._build_payment_transaction()
    
        response = {
            "paymentId": "PAY-100",
            "conversationId": "CONV-100",
            "paidPrice": "100.00",
            "currency": "TRY",
            "itemTransactions": [],
        }
    
        with (
            self._patch_payment_transaction(payment_tx),
            self._patch_order(payment_tx.order),
            patch(
                "orders.services.payment."
                "PaymentService._persist_payment_transaction_items"
            ) as persist_items,
            patch(
                "orders.services.payment."
                "StockReservationService.consume_order"
            ) as consume_order,
            patch(
                "orders.services.payment."
                "CartService.clear_order_items",
                side_effect=RuntimeError(
                    "database cleanup failure"
                ),
            ) as clear_cart,
        ):
            result = (
                PaymentService._finalize_successful_payment(
                    payment_id="PAY-100",
                    conversation_id="CONV-100",
                    response=response,
                )
            )
    
        self.assertIsNotNone(result)
    
        self.assertEqual(
            payment_tx.status,
            PaymentStatus.SUCCESS,
        )
    
        self.assertEqual(
            payment_tx.order.status,
            OrderStatus.PAID,
        )
    
        persist_items.assert_called_once_with(
            payment_transaction=payment_tx,
            response=response,
        )
    
        consume_order.assert_called_once_with(
            order=payment_tx.order,
        )
    
        clear_cart.assert_called_once_with(
            order=payment_tx.order,
        )

    def test_complete_3ds_uses_common_finalizer(self):
        payment_tx = self._build_payment_transaction()

        payment_manager = MagicMock()

        (
            payment_manager
            .select_for_update.return_value
            .select_related.return_value
            .get.return_value
        ) = payment_tx

        response = {
            "paymentId": "PAY-100",
            "conversationId": "CONV-100",
            "paidPrice": "100.00",
            "price": "100.00",
            "currency": "TRY",
            "basketId": "BASKET-100",
            "status": "success",
            "paymentStatus": "SUCCESS",
        }

        expected_result = MagicMock()

        with (
            patch.object(
                PaymentTransaction,
                "objects",
                payment_manager,
            ),
            patch.object(
                PaymentService,
                "_validate_settings",
            ),
            patch.object(
                PaymentService,
                "_complete_remote",
                return_value=response,
            ),
            patch.object(
                PaymentService,
                "_validate_completion_response",
            ),
            patch.object(
                PaymentService,
                "_verify_completion_signature",
            ),
            patch.object(
                PaymentService,
                "_finalize_successful_payment",
                return_value=expected_result,
            ) as finalize,
        ):
            result = PaymentService.complete_3ds(
                payment_id="PAY-100",
                conversation_id="CONV-100",
                conversation_data="",
            )

        self.assertIs(
            result,
            expected_result,
        )

        finalize.assert_called_once_with(
            payment_id="PAY-100",
            conversation_id="CONV-100",
            response=response,
        )

class CartCleanupDatabaseIntegrationTests(TestCase):

    def _create_test_data(self):
        User = get_user_model()

        user = User.objects.create_user(
            email="cleanup-test@example.com",
            password="TestPassword123!",
        )

        seller_user = User.objects.create_user(
            email="seller-cleanup@example.com",
            password="TestPassword123!",
        )

        seller_profile = SellerProfile.objects.create(
            user=seller_user,
            company_name="Cleanup Test Company",
            company_address="Test Company Address",
            company_phone="5555555555",
            iban="TR330006100519786457841326",
            is_approved=True,
        )

        store = Store.objects.create(
            seller=seller_profile,
            store_name="Cleanup Test Store",
            contact_email="store-cleanup@example.com",
            contact_phone="5555555555",
            address="Test Store Address",
            status=StoreStatus.APPROVED,
            is_active=True,
        )

        product = Product.objects.create(
            name="Cleanup Test Product",
            description="Cleanup integration test product.",
            status=ProductStatus.ACTIVE,
        )

        variant = ProductVariant.objects.create(
            product=product,
            is_active=True,
        )

        store_product = StoreProduct.objects.create(
            store=store,
            variant=variant,
            sku="CLEANUP-001",
            price=Decimal("100.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        cart = Cart.objects.create(
            user=user,
        )

        cart_item = CartItem.objects.create(
            cart=cart,
            store_product=store_product,
            quantity=1,
            is_selected=True,
            last_seen_price=Decimal("100.00"),
        )

        order = Order.objects.create(
            user=user,
            customer_email=user.email,
            customer_phone="5555555555",
            subtotal=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            currency="TRY",
            status=OrderStatus.PENDING_PAYMENT,

            shipping_full_name="Test Customer",
            shipping_phone="5555555555",
            shipping_address_line1="Test Address",
            shipping_address_line2="",
            shipping_city="Istanbul",
            shipping_state="Kadikoy",
            shipping_postal_code="34710",

            billing_full_name="Test Customer",
            billing_phone="5555555555",
            billing_address_line1="Test Address",
            billing_address_line2="",
            billing_city="Istanbul",
            billing_state="Kadikoy",
            billing_postal_code="34710",
        )

        sub_order = SubOrder.objects.create(
            order=order,
            store=store,
            subtotal=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
        )

        checkout_timestamp = cart_item.updated_at

        order_item = OrderItem.objects.create(
            sub_order=sub_order,
            store_product=store_product,
        
            product_name_snapshot=product.name,
        
            variant_snapshot={
                "variant_id": variant.pk,
                "attributes": [],
            },
        
            variant_display="",
        
            sku_snapshot=store_product.sku or "",
            barcode_snapshot=variant.barcode or "",
        
            quantity=cart_item.quantity,
            unit_price=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
        
            source_cart_item=cart_item,
            source_cart_item_updated_at=checkout_timestamp,
        )

        return {
            "user": user,
            "seller_user": seller_user,
            "seller_profile": seller_profile,
            "store": store,
            "product": product,
            "variant": variant,
            "store_product": store_product,
            "cart": cart,
            "cart_item": cart_item,
            "order": order,
            "sub_order": sub_order,
            "order_item": order_item,
            "checkout_timestamp": checkout_timestamp,
        }

    def test_real_cart_item_is_deleted_from_database(self):
        data = self._create_test_data()

        cart_item_id = data["cart_item"].pk
        order_item_id = data["order_item"].pk
        order_id = data["order"].pk

        deleted_count = CartService.clear_order_items(
            order=data["order"],
        )

        self.assertEqual(
            deleted_count,
            1,
        )

        self.assertFalse(
            CartItem.objects.filter(
                pk=cart_item_id,
            ).exists(),
        )

        order_item = (
            OrderItem.objects
            .get(pk=order_item_id)
        )

        self.assertIsNone(
            order_item.source_cart_item_id,
        )

        self.assertEqual(
            order_item.sub_order.order_id,
            order_id,
        )

    def test_changed_cart_item_is_preserved_in_database(self):
        data = self._create_test_data()

        cart_item = data["cart_item"]

        checkout_timestamp = (
            data["checkout_timestamp"]
        )

        changed_timestamp = (
            checkout_timestamp
            + timedelta(seconds=1)
        )

        CartItem.objects.filter(
            pk=cart_item.pk,
        ).update(
            quantity=2,
            updated_at=changed_timestamp,
        )

        deleted_count = CartService.clear_order_items(
            order=data["order"],
        )

        self.assertEqual(
            deleted_count,
            0,
        )

        cart_item.refresh_from_db()

        self.assertEqual(
            cart_item.quantity,
            2,
        )

        self.assertEqual(
            cart_item.updated_at,
            changed_timestamp,
        )

        order_item = (
            OrderItem.objects
            .get(pk=data["order_item"].pk)
        )

        self.assertEqual(
            order_item.source_cart_item_id,
            cart_item.pk,
        )

    def test_deleted_cart_item_is_set_null_on_order_item(self):
        data = self._create_test_data()

        cart_item_id = data["cart_item"].pk
        order_item_id = data["order_item"].pk

        data["cart_item"].delete()

        order_item = (
            OrderItem.objects
            .get(pk=order_item_id)
        )

        self.assertIsNone(
            order_item.source_cart_item_id,
        )

        deleted_count = CartService.clear_order_items(
            order=data["order"],
        )

        self.assertEqual(
            deleted_count,
            0,
        )

        self.assertFalse(
            CartItem.objects.filter(
                pk=cart_item_id,
            ).exists(),
        )
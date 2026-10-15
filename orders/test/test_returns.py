from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings

from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from orders.exceptions import ReturnOperationError
from orders.models import (
    CargoCompany,
    ReturnDeliverySource,
    ReturnReason,
    Order,
    OrderItem,
    OrderStatus,
    ReturnRequest,
    ReturnStatus,
    SubOrder,
    SubOrderStatus,
    PaymentTransaction, PaymentStatus, PaymentTransactionItem, PaymentTransactionItemType, PaymentRefund, PaymentRefundItem,
    SubOrderItemCancellationUnit, SubOrderItemCancellation
)
from orders.services.returns import ReturnService

from products.models import (
    Product,
    ProductVariant,
    StoreProduct,
    StoreProductStatus,
    ProductStatus,
)

from accounts.models import SellerProfile
from store.models import Store



class ReturnServiceInputValidationTests(TestCase):
    """ReturnService giriş doğrulama testleri."""

    def setUp(self):
        # Bu kullanıcı nesnesi yalnızca erken doğrulamalarda kullanılıyor.
        # Gerçek kullanıcı kaydı oluşturulmasına gerek yok.
        self.authenticated_user = SimpleNamespace(
            pk=1,
            is_authenticated=True,
        )

    def test_create_rejects_invalid_reason(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "Geçersiz iade nedeni.",
        ):
            ReturnService.create_return_request(
                order_item_id=1,
                requested_by=self.authenticated_user,
                reason="invalid_reason",
            )

    def test_other_reason_requires_customer_note(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "Diğer seçeneğinde iade nedeni açıklanmalıdır.",
        ):
            ReturnService.create_return_request(
                order_item_id=1,
                requested_by=self.authenticated_user,
                reason=ReturnReason.OTHER,
                customer_note=" ",
            )

    def test_create_rejects_anonymous_user(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "İade talebi oluşturmak için giriş yapmalısınız.",
        ):
            ReturnService.create_return_request(
                order_item_id=1,
                requested_by=AnonymousUser(),
                reason=ReturnReason.WITHDRAWAL,
            )

    def test_ship_rejects_anonymous_user(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "İade kargo bilgisi girmek için giriş yapmalısınız.",
        ):
            ReturnService.mark_return_shipped(
                return_request_id=1,
                requested_by=AnonymousUser(),
                cargo_company=CargoCompany.values[0],
                tracking_number="TEST-123",
            )

    def test_ship_rejects_invalid_cargo_company(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "Geçersiz iade kargo firması.",
        ):
            ReturnService.mark_return_shipped(
                return_request_id=1,
                requested_by=self.authenticated_user,
                cargo_company="invalid_cargo",
                tracking_number="TEST-123",
            )

    def test_ship_requires_tracking_number(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "İade kargo takip numarası girilmelidir.",
        ):
            ReturnService.mark_return_shipped(
                return_request_id=1,
                requested_by=self.authenticated_user,
                cargo_company=CargoCompany.values[0],
                tracking_number=" ",
            )

    def test_ship_rejects_tracking_number_longer_than_100(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "İade kargo takip numarası çok uzun.",
        ):
            ReturnService.mark_return_shipped(
                return_request_id=1,
                requested_by=self.authenticated_user,
                cargo_company=CargoCompany.values[0],
                tracking_number="T" * 101,
            )

    def test_carrier_delivery_confirmation_is_not_supported_yet(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "Kargo firması üzerinden teslimat doğrulaması "
            "henüz desteklenmiyor.",
        ):
            ReturnService.confirm_return_delivered(
                return_request_id=1,
                source=ReturnDeliverySource.CARRIER,
            )

    def test_invalid_delivery_source_is_rejected(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "Geçersiz teslimat doğrulama kaynağı.",
        ):
            ReturnService.confirm_return_delivered(
                return_request_id=1,
                source="invalid_source",
            )

User = get_user_model()


class ReturnServiceIntegrationTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create(
            email="return-customer@example.com",
            first_name="Test",
            last_name="Customer",
        )

        self.other_customer = User.objects.create(
            email="other-customer@example.com",
            first_name="Other",
            last_name="Customer",
        )

        self.seller_user = User.objects.create(
            email="return-seller@example.com",
            first_name="Test",
            last_name="Seller",
        )

        seller_profile = SellerProfile.objects.create(
            user=self.seller_user,
            company_name="Return Test Seller",
            company_address="Test Address",
            iban="TR00000000000000000000000000",
            is_approved=True,
        )

        self.store = Store.objects.create(
            seller=seller_profile,
            store_name="Return Test Store",
            slug="return-test-store",
            contact_email="return-store@example.com",
            contact_phone="5551234567",
            address="Test Address",
            is_active=True,
        )

        product = Product.objects.create(
            name="Return Test Product",
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
            sku="RETURN-TEST-SKU",
            price=Decimal("100.00"),
            stock=10,
            status=StoreProductStatus.ACTIVE,
        )

        self.order = self._create_order(
            user=self.customer,
        )

        self.suborder = self._create_suborder(
            order=self.order,
            status=SubOrderStatus.DELIVERED,
        )

        self.order_item = self._create_order_item(
            suborder=self.suborder,
            quantity=2,
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

    def _create_suborder(
        self,
        *,
        order,
        status=SubOrderStatus.DELIVERED,
    ):
        return SubOrder.objects.create(
            order=order,
            store=self.store,
            store_name_snapshot=self.store.store_name,
            subtotal=Decimal("200.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            status=status,
            delivered_at=timezone.now()
            if status == SubOrderStatus.DELIVERED
            else None,
        )

    def _create_order_item(
        self,
        *,
        suborder,
        quantity=2,
    ):
        return OrderItem.objects.create(
            sub_order=suborder,
            store_product=self.store_product,
            product_name_snapshot="Return Test Product",
            variant_snapshot={"Varyant": "Test"},
            variant_display="Varyant: Test",
            sku_snapshot=self.store_product.sku,
            quantity=quantity,
            unit_price=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00") * quantity,
        )

    def _create_return(
        self,
        *,
        order_item=None,
        customer=None,
        reason=ReturnReason.WITHDRAWAL,
    ):
        return ReturnService.create_return_request(
            order_item_id=(order_item or self.order_item).pk,
            requested_by=customer or self.customer,
            reason=reason,
        )

    def _set_post_purchase_inventory(
        self,
        *,
        stock=8,
        sold_count=2,
    ):
        """
        İki adet satılmış ürünün satın alma sonrası stok durumunu hazırlar.
        """
        self.store_product.stock = stock
        self.store_product.sold_count = sold_count
        self.store_product.save(
            update_fields=["stock", "sold_count", "updated_at"],
        )


    def _create_successful_payment_records(self):
        """
        İade onayının ihtiyaç duyduğu başarılı ödeme ve ürün ödeme kalemini
        oluşturur. OrderItem.quantity = 2 olduğu için ürün kalemi toplamı
        200 TL, birim tutarı 100 TL'dir.
        """
        payment_transaction = PaymentTransaction.objects.create(
            order=self.order,
            provider="iyzico",
            payment_id=f"return-test-payment-id-{self.order.pk}",
            conversation_id=f"return-test-payment-{self.order.pk}",
            status=PaymentStatus.SUCCESS,
            paid_price=Decimal("200.00"),
            currency="TRY",
            succeeded_at=timezone.now(),
        )

        payment_item = PaymentTransactionItem.objects.create(
            payment_transaction=payment_transaction,
            suborder=self.suborder,
            order_item=self.order_item,
            item_type=PaymentTransactionItemType.PRODUCT,
            provider_item_id=f"return-test-item-{self.order_item.pk}",
            provider_transaction_id=(
                f"RETURN-TEST-PTX-{self.order_item.pk}"
            ),
            price=Decimal("200.00"),
            paid_price=Decimal("200.00"),
        )

        return payment_transaction, payment_item


    def _prepare_return_for_approval(
        self,
        *,
        reason=ReturnReason.WITHDRAWAL,
    ):
        """
        Başarılı ödeme kayıtlarını oluşturur ve iadeyi satıcı incelemesine taşır.
        """
        self._create_successful_payment_records()

        return_request = self._create_return(reason=reason)

        ReturnService.mark_return_shipped(
            return_request_id=return_request.pk,
            requested_by=self.customer,
            cargo_company=CargoCompany.values[0],
            tracking_number=f"RETURN-APPROVAL-{return_request.pk}",
        )

        return ReturnService.confirm_return_delivered(
            return_request_id=return_request.pk,
            source=ReturnDeliverySource.SELLER,
            store=self.store,
            confirmed_by=self.seller_user,
        )

    def test_customer_can_create_separate_requests_for_two_units(self):
        first = self._create_return()
        second = self._create_return()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.order_item_id, self.order_item.pk)
        self.assertEqual(second.order_item_id, self.order_item.pk)
        self.assertEqual(first.status, ReturnStatus.AUTHORIZED)
        self.assertEqual(second.status, ReturnStatus.AUTHORIZED)

        self.assertEqual(first.unit_number, 1)
        self.assertEqual(second.unit_number, 2)

        self.assertEqual(
            ReturnRequest.objects.filter(
                order_item=self.order_item,
            ).count(),
            2,
        )

    def test_customer_cannot_return_more_units_than_purchased(self):
        self._create_return()
        self._create_return()

        with self.assertRaisesMessage(
            ReturnOperationError,
            "Bu üründen iade edilebilecek başka bir adet kalmadı.",
        ):
            self._create_return()

    def test_other_customer_cannot_create_return(self):
        with self.assertRaisesMessage(
            ReturnOperationError,
            "Yalnızca kendi siparişinizdeki ürünler için "
            "iade talebi oluşturabilirsiniz.",
        ):
            self._create_return(customer=self.other_customer)

    def test_undelivered_suborder_cannot_be_returned(self):
        self.suborder.status = SubOrderStatus.PREPARING
        self.suborder.delivered_at = None
        self.suborder.save(update_fields=["status", "delivered_at"])

        with self.assertRaisesMessage(
            ReturnOperationError,
            "Yalnızca teslim edilmiş ürünler için "
            "iade talebi oluşturabilirsiniz.",
        ):
            self._create_return(order_item=self.order_item)
        
    def test_customer_can_mark_return_shipped(self):
        return_request = self._create_return()

        result = ReturnService.mark_return_shipped(
            return_request_id=return_request.pk,
            requested_by=self.customer,
            cargo_company=CargoCompany.values[0],
            tracking_number="RETURN-TRACK-123",
        )

        self.assertEqual(result.status, ReturnStatus.IN_TRANSIT)
        self.assertEqual(
            result.return_tracking_number,
            "RETURN-TRACK-123",
        )
        self.assertIsNotNone(result.return_shipped_at)

    def test_other_customer_cannot_mark_return_shipped(self):
        return_request = self._create_return()

        with self.assertRaisesMessage(
            ReturnOperationError,
            "Yalnızca kendi iade talebinizin kargo bilgisini "
            "girebilirsiniz.",
        ):
            ReturnService.mark_return_shipped(
                return_request_id=return_request.pk,
                requested_by=self.other_customer,
                cargo_company=CargoCompany.values[0],
                tracking_number="RETURN-TRACK-123",
            )

    def test_seller_can_confirm_delivery_for_own_store(self):
        return_request = self._create_return()

        ReturnService.mark_return_shipped(
            return_request_id=return_request.pk,
            requested_by=self.customer,
            cargo_company=CargoCompany.values[0],
            tracking_number="RETURN-TRACK-456",
        )

        result = ReturnService.confirm_return_delivered(
            return_request_id=return_request.pk,
            source=ReturnDeliverySource.SELLER,
            store=self.store,
            confirmed_by=self.seller_user,
        )

        self.assertEqual(
            result.status,
            ReturnStatus.AWAITING_SELLER_REVIEW,
        )
        self.assertEqual(
            result.delivery_confirmation_source,
            ReturnDeliverySource.SELLER,
        )
        self.assertEqual(
            result.delivery_confirmed_by_id,
            self.seller_user.pk,
        )
        self.assertIsNotNone(result.delivered_at)
        self.assertIsNotNone(result.seller_review_deadline)

    @override_settings(
        RETURN_SIMULATED_DELIVERY_ENABLED=True,
        RETURN_SIMULATED_DELIVERY_DELAY_MINUTES=10,
        RETURN_SELLER_REVIEW_TIMEOUT_MINUTES=10,
    )
    def test_simulated_delivery_is_rejected_before_deadline(self):
        return_request = self._create_return()

        ReturnService.mark_return_shipped(
            return_request_id=return_request.pk,
            requested_by=self.customer,
            cargo_company=CargoCompany.values[0],
            tracking_number="RETURN-TRACK-789",
        )

        with self.assertRaisesMessage(
            ReturnOperationError,
            "Otomatik teslimat için bekleme süresi dolmadı.",
        ):
            ReturnService.confirm_return_delivered(
                return_request_id=return_request.pk,
                source=ReturnDeliverySource.SIMULATED,
            )

    @override_settings(
        RETURN_SIMULATED_DELIVERY_ENABLED=True,
        RETURN_SIMULATED_DELIVERY_DELAY_MINUTES=10,
        RETURN_SELLER_REVIEW_TIMEOUT_MINUTES=10,
    )
    def test_simulated_delivery_succeeds_after_deadline(self):
        return_request = self._create_return()

        ReturnService.mark_return_shipped(
            return_request_id=return_request.pk,
            requested_by=self.customer,
            cargo_company=CargoCompany.values[0],
            tracking_number="RETURN-TRACK-999",
        )

        past_time = timezone.now() - timedelta(minutes=11)

        ReturnRequest.objects.filter(
            pk=return_request.pk,
        ).update(
            return_shipped_at=past_time,
        )

        result = ReturnService.confirm_return_delivered(
            return_request_id=return_request.pk,
            source=ReturnDeliverySource.SIMULATED,
        )

        self.assertEqual(
            result.status,
            ReturnStatus.AWAITING_SELLER_REVIEW,
        )
        self.assertEqual(
            result.delivery_confirmation_source,
            ReturnDeliverySource.SIMULATED,
        )
        self.assertIsNotNone(result.delivered_at)

    def test_withdrawal_return_approval_restores_one_unit_of_stock(self):
        self._set_post_purchase_inventory(
            stock=8,
            sold_count=2,
        )
        return_request = self._prepare_return_for_approval(
            reason=ReturnReason.WITHDRAWAL,
        )

        result = ReturnService.approve_return(
            return_request_id=return_request.pk,
            store=self.store,
            approved_by=self.seller_user,
        )

        self.store_product.refresh_from_db()
        result.refresh_from_db()

        self.assertEqual(self.store_product.stock, 9)
        self.assertEqual(self.store_product.sold_count, 1)
        self.assertIsNotNone(result.stock_restored_at)
        self.assertEqual(result.status, ReturnStatus.REFUND_PENDING)

        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertEqual(PaymentRefundItem.objects.count(), 1)

        # Tarihsel sipariş miktarı değişmemeli.
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.quantity, 2)


    def test_defective_return_approval_does_not_restore_stock(self):
        self._set_post_purchase_inventory(
            stock=8,
            sold_count=2,
        )
        return_request = self._prepare_return_for_approval(
            reason=ReturnReason.DEFECTIVE,
        )

        result = ReturnService.approve_return(
            return_request_id=return_request.pk,
            store=self.store,
            approved_by=self.seller_user,
        )

        self.store_product.refresh_from_db()
        result.refresh_from_db()

        self.assertEqual(self.store_product.stock, 8)
        self.assertEqual(self.store_product.sold_count, 1)
        self.assertIsNone(result.stock_restored_at)
        self.assertIsNotNone(result.sold_count_adjusted_at)
        self.assertEqual(result.status, ReturnStatus.REFUND_PENDING)

        # Stok iadesi yapılmasa da finansal iade hazırlanmalıdır.
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertEqual(PaymentRefundItem.objects.count(), 1)


    def test_same_return_cannot_be_approved_twice(self):
        self._set_post_purchase_inventory(
            stock=8,
            sold_count=2,
        )
        return_request = self._prepare_return_for_approval(
            reason=ReturnReason.WITHDRAWAL,
        )

        ReturnService.approve_return(
            return_request_id=return_request.pk,
            store=self.store,
            approved_by=self.seller_user,
        )

        with self.assertRaisesMessage(
            ReturnOperationError,
            "Yalnızca satıcı incelemesindeki iadeler onaylanabilir.",
        ):
            ReturnService.approve_return(
                return_request_id=return_request.pk,
                store=self.store,
                approved_by=self.seller_user,
            )

        self.store_product.refresh_from_db()
        return_request.refresh_from_db()

        # İkinci onay stok üzerinde ek bir değişiklik yapmamalı.
        self.assertEqual(self.store_product.stock, 9)
        self.assertEqual(self.store_product.sold_count, 1)
        self.assertIsNotNone(return_request.stock_restored_at)
        self.assertEqual(return_request.status, ReturnStatus.REFUND_PENDING)

        # İkinci bir finansal iade veya iade kalemi oluşmamalı.
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertEqual(PaymentRefundItem.objects.count(), 1)


    def test_withdrawal_approval_fails_when_sold_count_is_insufficient(self):
        self._set_post_purchase_inventory(
            stock=8,
            sold_count=0,
        )
        return_request = self._prepare_return_for_approval(
            reason=ReturnReason.WITHDRAWAL,
        )

        with self.assertRaises(ReturnOperationError):
            ReturnService.approve_return(
                return_request_id=return_request.pk,
                store=self.store,
                approved_by=self.seller_user,
            )

        self.store_product.refresh_from_db()
        return_request.refresh_from_db()

        # Hatalı stok durumu hiçbir şekilde kısmen güncellenmemeli.
        self.assertEqual(self.store_product.stock, 8)
        self.assertEqual(self.store_product.sold_count, 0)
        self.assertIsNone(return_request.stock_restored_at)

        # Onay ve finansal kayıtlar da rollback olmalı.
        self.assertEqual(
            return_request.status,
            ReturnStatus.AWAITING_SELLER_REVIEW,
        )
        self.assertIsNone(return_request.payment_refund_id)
        self.assertEqual(PaymentRefund.objects.count(), 0)
        self.assertEqual(PaymentRefundItem.objects.count(), 0)


    def test_stock_restore_is_rolled_back_if_refund_creation_fails(self):
        self._set_post_purchase_inventory(
            stock=8,
            sold_count=2,
        )
        return_request = self._prepare_return_for_approval(
            reason=ReturnReason.WITHDRAWAL,
        )

        with patch(
            "orders.services.returns.PaymentRefund.objects.create",
            side_effect=RuntimeError("Simulated refund creation failure"),
        ):
            with self.assertRaisesMessage(
                RuntimeError,
                "Simulated refund creation failure",
            ):
                ReturnService.approve_return(
                    return_request_id=return_request.pk,
                    store=self.store,
                    approved_by=self.seller_user,
                )

        self.store_product.refresh_from_db()
        return_request.refresh_from_db()

        # Stok güncellemesi, transaction başarısız olduğunda geri alınmalı.
        self.assertEqual(self.store_product.stock, 8)
        self.assertEqual(self.store_product.sold_count, 2)

        # ReturnRequest üzerinde de kısmi değişiklik kalmamalı.
        self.assertIsNone(return_request.stock_restored_at)
        self.assertEqual(
            return_request.status,
            ReturnStatus.AWAITING_SELLER_REVIEW,
        )
        self.assertIsNone(return_request.payment_refund_id)

        self.assertEqual(PaymentRefund.objects.count(), 0)
        self.assertEqual(PaymentRefundItem.objects.count(), 0)

    def test_customer_cannot_return_a_cancelled_unit(self):
        cancellation = SubOrderItemCancellation.objects.create(
            order_item=self.order_item,
            cancelled_by=self.seller_user,
            quantity=1,
            reason="seller_cancelled",
            refund_amount=Decimal("100.00"),
        )
    
        SubOrderItemCancellationUnit.objects.create(
            cancellation=cancellation,
            order_item=self.order_item,
            unit_number=1,
        )
    
        return_request = self._create_return()
    
        self.assertEqual(return_request.unit_number, 2)

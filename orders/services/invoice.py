import uuid

from orders.exceptions import (
    InvoiceAlreadyExistsError,
    InvoiceCreationError,
)
from orders.models import (
    Invoice,
    InvoiceItem,
    SubOrder,
)


class InvoiceService:
    """
    Invoice domain business logic.

    Sorumlulukları:
        - SubOrder'dan Invoice oluşturmak
        - Seller legal snapshot oluşturmak
        - Buyer billing snapshot oluşturmak
        - SubOrder financial snapshot oluşturmak
        - OrderItem'lardan InvoiceItem oluşturmak

    Sorumlu olmadığı işler:
        - PDF oluşturmak
        - E-Fatura / E-Arşiv provider iletişimi
        - HTTP response / redirect / message
        - Seller authorization
        - Transaction yönetmek

    Önemli:
        create_for_suborder() kendisine verilen SubOrder'ın
        çağıran servis tarafından transaction içinde kilitlenmiş
        olmasını bekler.

        Bu metod doğrudan kullanıcı isteğinden değil,
        OrderService.start_suborder_preparation()
        gibi transaction sahibi bir domain operasyonundan çağrılmalıdır.
    """

    @classmethod
    def create_for_suborder(
        cls,
        *,
        suborder: SubOrder,
    ) -> Invoice:
        """
        Kilitlenmiş bir SubOrder için Invoice oluşturur.

        Transaction ve row lock sahibi:
            OrderService.start_suborder_preparation()

        Invoice oluşturulduktan sonra:
            Invoice ve InvoiceItem kendi tarihsel snapshot
            verilerini taşır.
        """

        if Invoice.objects.filter(
            suborder=suborder,
        ).exists():
            raise InvoiceAlreadyExistsError(
                "Bu sipariş için zaten fatura oluşturulmuş."
            )

        seller_profile = suborder.store.seller

        order_items = list(
            suborder.items.all()
        )

        if not order_items:
            raise InvoiceCreationError(
                "Fatura oluşturmak için sipariş ürünü bulunamadı."
            )

        invoice = Invoice.objects.create(
            suborder=suborder,
            invoice_number=cls._generate_invoice_number(),

            # Seller snapshot
            seller_display_name=(
                suborder.store.store_name
            ),
            seller_legal_company_title=(
                seller_profile.legal_company_title or ""
            ),
            seller_address=(
                seller_profile.company_address or ""
            ),
            seller_phone=(
                seller_profile.company_phone or ""
            ),
            seller_tax_office=(
                seller_profile.tax_office or ""
            ),
            seller_tax_number=(
                seller_profile.tax_number or ""
            ),
            seller_identity_number=(
                seller_profile.identity_number or ""
            ),

            # Buyer snapshot
            buyer_full_name=(
                suborder.order.billing_full_name
            ),
            buyer_phone=(
                suborder.order.billing_phone or ""
            ),
            buyer_email=(
                suborder.order.customer_email or ""
            ),
            buyer_identity_number=(
                suborder.order.buyer_identity_number or ""
            ),

            buyer_billing_address_line1=(
                suborder.order.billing_address_line1
            ),
            buyer_billing_address_line2=(
                suborder.order.billing_address_line2 or ""
            ),
            buyer_billing_city=(
                suborder.order.billing_city
            ),
            buyer_billing_state=(
                suborder.order.billing_state
            ),
            buyer_billing_postal_code=(
                suborder.order.billing_postal_code
            ),

            # Financial snapshot
            subtotal=suborder.subtotal,
            discount_amount=suborder.discount_amount,
            shipping_amount=suborder.shipping_amount,
            tax_amount=suborder.tax_amount,
            total_amount=suborder.total_amount,
            currency=suborder.order.currency,
        )

        cls._create_invoice_items(
            invoice=invoice,
            order_items=order_items,
        )

        return invoice

    @staticmethod
    def _create_invoice_items(
        *,
        invoice: Invoice,
        order_items,
    ) -> None:
        invoice_items = [
            InvoiceItem(
                invoice=invoice,
                product_name=(
                    order_item.product_name_snapshot
                ),
                variant_display=(
                    order_item.variant_display or ""
                ),
                sku=(
                    order_item.sku_snapshot or ""
                ),
                barcode=(
                    order_item.barcode_snapshot or ""
                ),
                quantity=order_item.quantity,
                unit_price=order_item.unit_price,
                discount_amount=order_item.discount_amount,
                tax_amount=order_item.tax_amount,
                total_amount=order_item.total_amount,
            )
            for order_item in order_items
        ]

        InvoiceItem.objects.bulk_create(
            invoice_items
        )

    @staticmethod
    def _generate_invoice_number() -> str:
        return f"INV-{uuid.uuid4().hex.upper()}"
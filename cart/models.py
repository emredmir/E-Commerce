from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from products.models import StoreProduct


class Cart(models.Model):
    """Kullanıcının aktif alışveriş sepeti."""

    # Giriş yapmış kullanıcı için sepet
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart",
    )

    # Misafir kullanıcı için session
    session_key = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sepet"
        verbose_name_plural = "Sepetler"

        constraints = [
            # Sepet ya user'a ya da session'a ait olmalı.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        user__isnull=False,
                        session_key__isnull=True,
                    )
                    |
                    models.Q(
                        user__isnull=True,
                        session_key__isnull=False,
                    )
                ),
                name="cart_user_xor_session",
            ),

            # Aynı session için yalnızca bir guest cart.
            models.UniqueConstraint(
                fields=["session_key"],
                condition=models.Q(session_key__isnull=False),
                name="unique_cart_session",
            ),
        ]

    def __str__(self):
        if self.user_id:
            return f"Sepet - {self.user.email}"

        return f"Sepet - Misafir ({self.session_key})"

    @property
    def total_items_count(self):
        """Sepetteki tüm ürünlerin toplam adedi."""
        return sum(
            item.quantity
            for item in self.items.all()
        )

    @property
    def selected_items_count(self):
        """Seçili ürünlerin toplam adedi."""
        return sum(
            item.quantity
            for item in self.items.all()
            if item.is_selected
        )

    @property
    def total_cart_price(self):
        """Seçili ürünlerin güncel toplam fiyatı."""
        return sum(
            (
                item.total_price
                for item in self.items.all()
                if item.is_selected
            ),
            Decimal("0.00"),
        )


class CartItem(models.Model):
    """
    Sepetteki StoreProduct satırı.

    Fiyat burada sabit tutulmaz.
    StoreProduct.price üzerinden güncel fiyat alınır.
    """

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )

    # Doğrudan satıcının teklifine bağlanır.
    store_product = models.ForeignKey(
        StoreProduct,
        on_delete=models.CASCADE,
        related_name="cart_items",
    )

    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name="Adet",
    )

    # Checkout sırasında satın alınacak mı?
    is_selected = models.BooleanField(
        default=True,
        verbose_name="Satın Alınacak mı?",
    )

    # Kullanıcının fiyat değişikliğini en son gördüğü fiyat.
    # StoreProduct.price bundan farklıysa fiyat değişikliği vardır.
    last_seen_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Son Görülen Fiyat",
    )

    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sepet Ürünü"
        verbose_name_plural = "Sepet Ürünleri"
        ordering = ["-added_at"]

        constraints = [
            # Aynı ürün sepette tek satır olur.
            models.UniqueConstraint(
                fields=["cart", "store_product"],
                name="unique_cart_store_product",
            ),
        ]

    @property
    def unit_price(self):
        # StoreProduct fiyatı değişirse sepet fiyatı da güncellenir.
        return self.store_product.price

    @property
    def total_price(self):
        return self.unit_price * self.quantity

    @property
    def price_changed(self):
        """Kullanıcının son gördüğü fiyattan beri fiyat değişmiş mi?"""
        if self.last_seen_price is None:
            return False

        return self.last_seen_price != self.unit_price

    @property
    def previous_price(self):
        """Fiyat değiştiyse önceki fiyatı döndürür."""
        if not self.price_changed:
            return None

        return self.last_seen_price

    def __str__(self):
        return (
            f"{self.quantity}x "
            f"{self.store_product.variant.product.name} "
            f"({self.store_product.store.store_name})"
        )

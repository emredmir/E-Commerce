from decimal import Decimal, ROUND_HALF_UP


class ShippingService:
    """
    Checkout ve sipariş oluşturma sırasında kargo ücretini hesaplar.

    Kural:
        - Seçili ürünlerin ara toplamı 750 TL ve üzeriyse ücretsiz kargo.
        - 750 TL altındaysa standart kargo ücreti 99,99 TL.

    NOT:
        Bu service sadece kargo ücretini hesaplar.
        Sipariş oluşturma ve stok işlemleri burada yapılmaz.
    """

    FREE_SHIPPING_THRESHOLD = Decimal("750.00")
    STANDARD_SHIPPING_FEE = Decimal("99.99")
    MONEY_QUANTUM = Decimal("0.01")

    @classmethod
    def calculate_shipping(cls, *, subtotal):
        """
        Seçili ürünlerin ara toplamına göre kargo ücretini hesaplar.

        Args:
            subtotal:
                Checkout'taki seçili ürünlerin toplam tutarı.

        Returns:
            Decimal:
                Kargo ücreti.
        """

        subtotal = cls._normalize_money(subtotal)

        if subtotal >= cls.FREE_SHIPPING_THRESHOLD:
            return Decimal("0.00")

        return cls.STANDARD_SHIPPING_FEE

    @classmethod
    def _normalize_money(cls, value):
        """
        Para değerini güvenli şekilde Decimal'e çevirir.
        """

        try:
            amount = Decimal(str(value))
        except (TypeError, ValueError):
            raise ValueError(
                "Geçersiz para değeri."
            )

        return amount.quantize(
            cls.MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
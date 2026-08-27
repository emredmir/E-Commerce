from collections import defaultdict
from products.models import StoreProduct

from products.services.storefront_offers import (
    StorefrontOfferService,
)


class ProductDetailService:
    """
    Product Detail sayfasının business logic katmanı.

    Sorumlulukları:

    - Seçili varyantı belirlemek
    - Varyanta uygun görselleri belirlemek
    - StorefrontOfferService üzerinden teklifleri almak
    - BuyBox ve diğer satıcıları hazırlamak
    - Varyant seçeneklerini UI için hazırlamak
    """

    @classmethod
    def get_page_data(cls, *, product, variant_id=None, offer_id=None,):

        # =====================================================
        # 1. VARIANT SELECTION CONTEXT
        # =====================================================

        selection_context = (
            StorefrontOfferService
            .build_variant_selection_context(
                product=product,
            )
        )

        # =====================================================
        # 2. SELECTED VARIANT
        # =====================================================

        selected_variant = cls._select_variant(
            product=product,
            variant_id=variant_id,
            selection_context=selection_context,
        )

        # =====================================================
        # 3. OFFERS / BUYBOX
        # =====================================================

        offer_data = (
            StorefrontOfferService.get_variant_offers_data_from_context(
                variant=selected_variant,
                context=selection_context,
                offer_id=offer_id
            )
        )

        # =====================================================
        # 4. IMAGES
        # =====================================================

        display_images = cls._get_display_images(
            product=product,
            selected_variant=selected_variant,
        )

        # =====================================================
        # 5. VARIANT OPTIONS
        # =====================================================

        variant_attributes = cls._get_variant_attributes(
            product=product,
            selected_variant=selected_variant,
            selection_context=selection_context,
        )

        return {
            "product": product,
            "selected_variant": selected_variant,
            "display_images": display_images,
            
            "buy_box_offer": offer_data["active_offer"], # Ana ekranda kullanıcının seçtiği görünür
            "default_buybox": offer_data["default_buybox"], # URL'i temizlemek için asıl sahip lazım
            "cheapest_offer": offer_data["cheapest_offer"], # En Uygun Fiyat rozeti için
            "is_buybox_overridden": offer_data["is_buybox_overridden"], # Farklı satıcı uyarısı için
            
            "other_offers": offer_data["other_offers"],
            "offers": offer_data["offers"],
            "has_offers": offer_data["has_offers"],
            "variant_attributes": variant_attributes,
        }

    # =========================================================
    # VARIANT
    # =========================================================

    @staticmethod
    def _select_variant(*, product, variant_id=None, selection_context,):
        """
        Seçili ProductVariant'ı belirler.

        Öncelik:

        1. URL'den gelen aktif variant
        2. Product.default_variant
        3. En ucuz satın alınabilir variant
        4. Default / ilk aktif variant
        """

        active_variants = selection_context[
            "active_variants"
        ]
        if not active_variants:
            return None

        # 1. URL'den gelen (Kullanıcının özellikle tıkladığı) varyant
        if variant_id:
            selected = selection_context[
                "variant_by_id"
            ].get(
                int(variant_id)
                if str(variant_id).isdigit()
                else variant_id
            )

            if selected:
                return selected

        # 2. STOREFRONT BAŞLANGIÇ VARIANTI
        return StorefrontOfferService.get_initial_variant(
            product=product,
            context=selection_context,
        )

    # =========================================================
    # IMAGES
    # =========================================================

    @staticmethod
    def _get_display_images(*, product, selected_variant=None):
        """
        Seçili varyanta göre en spesifik ProductImageGroup'u bulur.

        Örnek:

            Variant:
                Siyah + 128GB

            Image Groups:
                Common
                Siyah
                Siyah + 128GB

        Sonuç:

            Siyah + 128GB

        Çünkü en fazla attribute ile eşleşen grup
        en spesifik gruptur.
        """

        image_groups = getattr(
            product,
            "cached_image_groups",
            [],
        )

        if not image_groups:
            return []

        # -----------------------------------------------------
        # Variant attribute ID'leri
        # -----------------------------------------------------

        variant_attribute_ids = set()

        if selected_variant:

            variant_attribute_ids = {
                value.pk
                for value in selected_variant.attribute_values.all()
            }

        common_images = []

        best_match_images = []

        best_match_count = -1

        # -----------------------------------------------------
        # Image Groups
        # -----------------------------------------------------

        for group in image_groups:

            group_attribute_ids = {
                value.pk
                for value in group.visual_attribute_values.all()
            }

            # -------------------------------------------------
            # Common Group
            # -------------------------------------------------

            if not group_attribute_ids:

                common_images = list(
                    group.images.all()
                )

                continue

            # Variant yoksa özel grup kullanma
            if not selected_variant:
                continue

            # -------------------------------------------------
            # Subset Matching
            # -------------------------------------------------

            if not group_attribute_ids.issubset(
                variant_attribute_ids
            ):
                continue

            match_count = len(group_attribute_ids)

            if match_count > best_match_count:

                best_match_count = match_count

                best_match_images = list(
                    group.images.all()
                )

        # -----------------------------------------------------
        # En spesifik grup
        # -----------------------------------------------------

        if best_match_images:
            # Varyanta özel resimleri başa koy, yanına ortak resimleri ekle
            return best_match_images + common_images

        # -----------------------------------------------------
        # Common
        # -----------------------------------------------------

        if common_images:
            return common_images

        # -----------------------------------------------------
        # Son fallback
        # -----------------------------------------------------

        for group in image_groups:

            images = list(
                group.images.all()
            )

            if images:
                return images

        return []

    # =========================================================
    # VARIANT ATTRIBUTES
    # =========================================================

    @classmethod
    def _get_variant_attributes(
        cls,
        *,
        product,
        selected_variant=None,
        selection_context,
    ):
        """
        Variant seçeneklerini UI için hazırlar.

        Her değer:

            id
            value
            is_selected
            is_available
            target_variant_id

        alanlarını içerir.

        target_variant_id, kullanıcının o değere tıklaması
        durumunda storefront algoritmasının seçtiği varianttır.
        """

        variant_data = selection_context[
            "variant_data"
        ]

        if not variant_data:
            return {}

        selected_ids = set()

        if selected_variant:

            selected_ids = {
                value.pk
                for value in selected_variant.attribute_values.all()
            }

        attributes = defaultdict(dict)

        # =====================================================
        # 1. ATTRIBUTE / VALUE'LARI TOPLA
        # =====================================================

        for item in variant_data:

            for value in item["values"]:

                attribute_name = value.attribute.name

                if value.pk not in attributes[attribute_name]:

                    attributes[attribute_name][value.pk] = {
                        "id": value.pk,
                        "value": value.value,
                        "is_selected": (
                            value.pk in selected_ids
                        ),
                        "is_available": False,
                        "target_variant_id": None,
                        "image_url": None,
                        "price": None,
                    }

        # =====================================================
        # 2. HER VALUE İÇİN HEDEF VARIANT'I BUL
        # =====================================================

        for attribute_name, values_dict in attributes.items():

            for value_id, data in values_dict.items():
                target_variant = None

                # ---------------------------------------------
                # Zaten seçiliyse
                # ---------------------------------------------

                if data["is_selected"]:

                    data["is_available"] = True

                    if selected_variant:
                        data["target_variant_id"] = selected_variant.pk
                        target_variant = selected_variant
                else:
                    # Kullanıcı bu value'ya tıklarsa hangi variant'a gider?
                    target_variant = StorefrontOfferService.get_variant_for_selection(
                        context=selection_context,
                        selected_variant=selected_variant,
                        value_id=value_id,
                    )
                    if target_variant:
                        data["is_available"] = True
                        data["target_variant_id"] = target_variant.pk

                # Eğer gidilecek bir varyant varsa, fotoğrafını ve o anki fiyatını çek.
                if target_variant:
                    # Fiyatı Bul
                    offer = selection_context["lowest_offer_by_variant"].get(target_variant.pk)
                    if offer:
                        data["price"] = offer.price
                    
                    # Resmi Bul
                    images = cls._get_display_images(product=product, selected_variant=target_variant)
                    if images:
                        data["image_url"] = images[0].image.url
        # =====================================================
        # 3. LIST'E ÇEVİR
        # =====================================================
        return {
            attribute_name: list(values.values())
            for attribute_name, values in attributes.items()
        }




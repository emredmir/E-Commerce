from django.db import transaction, IntegrityError


from products.models import (
    StoreProduct,
    StoreProductStatus,
    ProductVariant
)
from products.services.contribution import (
    VariantContributionService,
)


class OfferPublishService:
    @staticmethod
    def _validate_store_skus(
        *,
        store,
        offers_to_create,
        offers_to_update,
    ):
        """
        Aynı mağazada SKU tekrarını kontrol eder.
        """

        #
        # Kontrol edilecek SKU'lar
        #

        skus = set()

        for offer in offers_to_create:
            if offer.sku:
                skus.add(offer.sku)

        for offer in offers_to_update:
            if offer.sku:
                skus.add(offer.sku)

        if not skus:
            return

        #
        # DB'deki mevcut teklifler
        #

        existing_offers = (
            StoreProduct.objects.filter(
                store=store,
                sku__in=skus,
            )
        )

        #
        # Güncellenecek kayıtlar kendi kendileriyle
        # çakışmasın diye id -> offer map
        #

        update_lookup = {
            offer.pk: offer
            for offer in offers_to_update
        }

        for existing_offer in existing_offers:

            #
            # Update edilen aynı kayıt
            #

            updating_offer = update_lookup.get(
                existing_offer.pk,
            )

            if updating_offer:

                #
                # Aynı kayıt güncelleniyor.
                #

                continue

            raise ValueError(
                f'"{existing_offer.sku}" SKU mağazanızda zaten kullanılmaktadır.'
            )

    @staticmethod
    @transaction.atomic
    def publish(
        *,
        draft,
    ):

        if draft.matched_product is None:
            raise ValueError(
                "Eşleşen ürün bulunamadı."
            )

        catalog_lookup = {
            variant.attribute_signature: variant
            for variant in ProductVariant.objects.filter(
                product=draft.matched_product,
                is_active=True,
            ).prefetch_related(
                "attribute_values",
            )
        }

        draft_variants = list(
            draft.variants.filter(
                is_active=True,
            ).prefetch_related(
                "attribute_values",
            )
        )

        if not draft_variants:
            raise ValueError(
                "Yayınlanacak varyant bulunamadı."
            )

        #
        # Mevcut teklifler
        #

        existing_offers = {
            offer.variant_id: offer
            for offer in StoreProduct.objects.filter(
                store=draft.store,
                variant__product=draft.matched_product,
            )
        }

        offers_to_create = []

        offers_to_update = []

        for draft_variant in draft_variants:

            product_variant = (
                VariantContributionService.get_or_create_variant(
                    product=draft.matched_product,
                    draft_variant=draft_variant,
                    lookup=catalog_lookup,
                )
            )

            offer = existing_offers.get(
                product_variant.pk,
            )

            status = (
                StoreProductStatus.ACTIVE
                if draft_variant.stock > 0
                else StoreProductStatus.OUT_OF_STOCK
            )

            #
            # Güncelle
            #

            if offer:

                offer.price = draft_variant.price
                offer.stock = draft_variant.stock
                offer.sku = draft_variant.sku
                offer.status = status

                offers_to_update.append(
                    offer
                )

            #
            # Oluştur
            #

            else:

                offers_to_create.append(
                    StoreProduct(
                        store=draft.store,
                        variant=product_variant,
                        sku=draft_variant.sku,
                        price=draft_variant.price,
                        stock=draft_variant.stock,
                        status=status,
                    )
                )

        OfferPublishService._validate_store_skus(
            store=draft.store,
            offers_to_create=offers_to_create,
            offers_to_update=offers_to_update,
        )

        try:
            if offers_to_create:
                StoreProduct.objects.bulk_create(
                    offers_to_create,
                )

            if offers_to_update:
                StoreProduct.objects.bulk_update(
                    offers_to_update,
                    fields=[
                        "price",
                        "stock",
                        "sku",
                        "status",
                    ],
                )

        except IntegrityError:
        
            raise ValueError(
                "Girilen SKU mağazanızda başka bir üründe kullanılmaktadır."
            )

        draft.status = draft.Status.COMPLETED
        draft.match_status = draft.MatchStatus.ACCEPTED

        draft.save(
            update_fields=[
                "status",
                "match_status",
            ]
        )

        return {
            "created": len(offers_to_create),
            "updated": len(offers_to_update),
        }
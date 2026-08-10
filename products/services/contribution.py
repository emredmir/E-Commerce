from products.models import ProductVariant, ProductImage, ProductImageGroup
from products.services.variant import DraftVariantService

import os
from django.core.files.base import ContentFile


class VariantContributionService:
    """
    Katalog katkısı işlemlerini yönetir.

    Sorumlulukları:

    - Mevcut ProductVariant'ı bulmak
    - Gerekirse yeni ProductVariant oluşturmak
    """

    @staticmethod
    def get_or_create_variant(
        *,
        product,
        draft_variant,
        lookup,
    ):
        """
        Draft varyantını katalogdaki ProductVariant ile eşleştirir.

        Aynı attribute kombinasyonuna sahip varyant varsa onu döndürür.

        Yoksa yeni ProductVariant oluşturur.
        """

        signature = draft_variant.attribute_signature


        variant = lookup.get(signature)


        if variant:
            return variant

        variant = VariantContributionService.create_variant(
            product=product,
            draft_variant=draft_variant,
        )

        lookup[variant.attribute_signature] = variant

        return variant

    @staticmethod
    def create_variant(
        *,
        product,
        draft_variant,
    ):
        """
        Kataloğa yeni ProductVariant ekler.
        """


        variant = ProductVariant.objects.create(
            product=product,
            barcode=draft_variant.barcode,
            is_active=True,
        )

        variant.attribute_values.set(
            draft_variant.attribute_values.all(),
        )

        VariantContributionService._copy_draft_images_to_catalog(
            product=product,
            draft_variant=draft_variant,
        )

        

        return variant

    @staticmethod
    def _copy_draft_images_to_catalog(
        *,
        product,
        draft_variant,
    ):
        """
        Draft üzerinde oluşturulan yeni varyanta ait
        görselleri gerçek katalog tablolarına taşır.

        Sadece bu varyantla ilişkili görsel grubu kopyalanır.

        Görsel grubu bulunamazsa herhangi bir katalog görseli
        oluşturulmaz.
        """

        draft_signature = (
            draft_variant.attribute_signature
        )

        draft_groups = (
            draft_variant.draft.image_groups
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "visual_attribute_values",
                "images",
            )
            .order_by(
                "sort_order",
                "id",
            )
        )

        draft_group = None

        for group in draft_groups:

            group_signature = (
                DraftVariantService.build_variant_key(
                    group.visual_attribute_values.all()
                )
            )

            #
            # Görsel grubunun attribute'ları
            # varyantın attribute'larının alt kümesi olmalı.
            #
            # Örnek:
            #
            # Variant:
            # Siyah + 128GB
            #
            # Image Group:
            # Siyah
            #
            # => eşleşir.
            #

            if set(group_signature).issubset(
                set(draft_signature)
            ):
                draft_group = group
                break

        if draft_group is None:
            return None

        #
        # Katalogdaki gerçek image group
        #

        product_group = ProductImageGroup.objects.create(
            product=product,
            sort_order=draft_group.sort_order,
            is_active=True,
        )

        product_group.visual_attribute_values.set(
            draft_group.visual_attribute_values.all()
        )

        #
        # Görselleri kopyala
        #

        copied_images = []

        for draft_image in draft_group.images.all():

            if not draft_image.is_active:
                continue

            if not draft_image.image:
                continue

            file_name = os.path.basename(
                draft_image.image.name
            )

            product_image = ProductImage(
                group=product_group,
                alt_text=draft_image.alt_text,
                is_main=draft_image.is_main,
                sort_order=draft_image.sort_order,
            )

            product_image.image.save(
                file_name,
                ContentFile(
                    draft_image.image.read()
                ),
                save=False,
            )

            product_image.save()

            copied_images.append(
                product_image
            )

        if not copied_images:
            product_group.delete()
            return None

        return product_group
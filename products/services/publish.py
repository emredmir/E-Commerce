from django.db import transaction, IntegrityError
import os
from django.core.files.base import ContentFile

from products.services.review import DraftReviewService
from products.services.variant import DraftVariantService
from products.services.duplicate import DuplicateProductService
from products.services.search import SearchIndexingService

from products.tasks import async_copy_product_images


from products.models import (
    Product,
    ProductStatus,
    ProductVariant,
    StoreProduct,
    ProductImageGroup,
    ProductImage,
    StoreProductStatus
)


class DraftPublishService:
    """
    ProductDraft'i gerçek ürün kayıtlarına dönüştürür.

    Publish akışı:

    1 Draft doğrulanır
    2 Yeni Product oluşturulur
    3 ProductVariant'lar oluşturulur
    4 StoreProduct kayıtları oluşturulur
    5 Görseller aktarılır
    6 Draft publish edilir
    """

    @classmethod
    def publish(
        cls,
        *,
        draft,
        existing_product=None,
        force_create_new=False,
    ):
        """
        Taslağı yayınlar.

        Başarılı olduğunda oluşturulan
        Product nesnesini döndürür.
        """


        #
        # Son doğrulamalar
        #
        DraftReviewService.validate(
            draft=draft,
        )
        if existing_product is None and not force_create_new:
            match = DuplicateProductService.find_match(
                draft=draft,
            )
            if match:
            
                return {
                    "success": False,
                    "duplicate": True,
                    "matched_product": match,
                }
            
        with transaction.atomic():

            if existing_product is None:
                product = cls._publish_new_product(
                    draft=draft,
                )

                is_merge = False

                #
                # Görselleri aktar
                #

            else:
            
                product = cls._merge_into_existing_product(
                    draft=draft,
                    product=existing_product,
                )

                is_merge = True

            #
            # Taslağı tamamla
            #

            cls._finalize_draft(
                draft=draft,
                product=product,
            )

            # DİKKAT: Görsel aktarımını işlem başarılı olduktan VE VERİTABANINA YAZILDIKTAN
            # SONRA (on_commit) arka plana atıyoruz! Aksi takdirde Celery daha biz DB'ye yazmadan
            # ürünü bulmaya çalışıp "Product.DoesNotExist" hatası verebilir.
            transaction.on_commit(
                lambda: async_copy_product_images.delay(
                    draft_id=draft.pk, 
                    product_id=product.pk, 
                    is_merge=is_merge
                )
            )

            transaction.on_commit(
                lambda: SearchIndexingService.index_product_async(
                    product_id=product.pk
                )
            )

            return {
                "success": True,
                "product": product,
                "is_new_product": existing_product is None,
            }

    @classmethod
    def _publish_new_product(
        cls,
        *,
        draft,
    ):
        """
        Taslaktan yeni Product oluşturur.

        Ardından tüm ProductVariant ve
        StoreProduct kayıtlarını oluşturur.
        """
        try:
            product = Product.objects.create(
                category=draft.category,
                brand=draft.brand,
                name=draft.name,
                description=draft.description,
                normalized_key=draft.normalized_key,
                tokens=draft.tokens,
                status=ProductStatus.ACTIVE,
            )
        except IntegrityError:
            raise ValueError(
                "Ürün oluşturulurken benzersiz alan çakışması oluştu. Lütfen tekrar deneyin."
            )

        draft_variants = (
            draft.variants
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
            .order_by(
                "sort_order",
                "id",
            )
        )

        default_product_variant = None

        for draft_variant in draft_variants:

            if (
                draft_variant.barcode
                and ProductVariant.objects.filter(
                    barcode=draft_variant.barcode,
                ).exists()
            ):
                raise ValueError(
                    f"{draft_variant.barcode} barkodu zaten kullanılmaktadır."
                )

            # Race Condition Koruması
            try:
                with transaction.atomic():
                    product_variant = ProductVariant.objects.create(
                        product=product,
                        barcode=draft_variant.barcode,
                        is_active=True,
                    )
            except IntegrityError:
                raise ValueError(
                    f"Siz yayına alırken '{draft_variant.barcode}' barkodu saniyeler farkıyla "
                    f"başka bir sisteme kaydedildi. Lütfen barkodu kontrol edin."
                )

            product_variant = ProductVariant.objects.create(
                product=product,
                barcode=draft_variant.barcode,
                is_active=True,
            )

            # Varsayılan varyant seçimi
            if draft_variant.is_default:
                default_product_variant = product_variant

            product_variant.attribute_values.set(
                draft_variant.attribute_values.all()
            )

            cls._create_store_product(
                draft=draft,
                product_variant=product_variant,
                draft_variant=draft_variant,
            )

        if default_product_variant:
            product.default_variant = default_product_variant
            product.save(update_fields=['default_variant'])

        return product

    @staticmethod
    def _create_store_product(
        *,
        draft,
        product_variant,
        draft_variant,
    ):
        """
        Satıcının satış kaydını oluşturur.
        """
        existing_offer = StoreProduct.objects.filter(
            store=draft.store,
            variant=product_variant,
        ).first()


        if existing_offer:
            existing_offer.price = draft_variant.price
            existing_offer.stock = draft_variant.stock
            existing_offer.sku = draft_variant.sku
            existing_offer.status = StoreProductStatus.ACTIVE

            existing_offer.save()

            return existing_offer

    
        return StoreProduct.objects.create(
            store=draft.store,
            variant=product_variant,
            sku=draft_variant.sku,
            price=draft_variant.price,
            stock=draft_variant.stock,
            status=StoreProductStatus.ACTIVE,
        )


    @staticmethod
    def _copy_images(*, draft, product):
        """
        Taslak görsellerini gruplarıyla birlikte ürüne aktarır.
        """
        draft_groups = draft.image_groups.filter(is_active=True).prefetch_related('visual_attribute_values', 'images')

        for draft_group in draft_groups:
            # Gerçek görsel grubunu yarat
            product_group = ProductImageGroup.objects.create(
                product=product,
                sort_order=draft_group.sort_order,
                is_active=draft_group.is_active
            )

            # Grubun özellik değerlerini (örn: Renk=Kırmızı) kopyala
            product_group.visual_attribute_values.set(draft_group.visual_attribute_values.all())

            # Grubun içindeki resimleri kopyala
            product_images = []
            for draft_image in draft_group.images.filter(is_active=True):
                new_image = ProductImage(
                    group=product_group,
                    alt_text=draft_image.alt_text,
                    is_main=draft_image.is_main,
                    sort_order=draft_image.sort_order
                )
                
                # Fiziksel dosyayı okuyup yeni modele aktarma işlemi
                if draft_image.image:
                    file_name = os.path.basename(draft_image.image.name)
                    # save=False çok önemli, bulk_create ile biz kaydedeceğiz.
                    new_image.image.save(
                        file_name, 
                        ContentFile(draft_image.image.read()), 
                        save=False
                    )
                
                product_images.append(new_image)

            # Sadece o gruba ait resimleri veritabanına tek seferde (bulk) yaz.
            ProductImage.objects.bulk_create(product_images)


    @staticmethod
    def _finalize_draft(
        *,
        draft,
        product,
    ):
        """
        Taslağı yayınlanmış olarak işaretler.
        """

        draft.status = draft.Status.PUBLISHED
        draft.published_product = product
        draft.current_step = 5
        draft.last_completed_step = 5

        draft.save(
            update_fields=[
                "status",
                "published_product",
                "current_step",
                "last_completed_step",
            ],
        )

    @classmethod
    def _merge_into_existing_product(
        cls,
        *,
        draft,
        product,
    ):
        """
        Draft'taki verileri mevcut ürüne ekler.
        """

        created_variants = cls._merge_variants(
            draft=draft,
            product=product,
        )


        return product

    @classmethod
    def _merge_variants(
        cls,
        *,
        draft,
        product,
    ):
        """
        Draft varyantlarını mevcut ürün ile birleştirir.

        Aynı kombinasyona sahip ProductVariant varsa
        sadece StoreProduct oluşturulur.

        Yoksa yeni ProductVariant oluşturulur.
        """

        product_variants = (
            product.variants
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
        )

        existing_variants = {}

        for variant in product_variants:

            key = DraftVariantService.build_variant_key(
                variant.attribute_values.all()
            )

            existing_variants[key] = variant


        draft_variants = (
            draft.variants
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
            .order_by(
                "sort_order",
                "id",
            )
        )

        created_variants = []


        for draft_variant in draft_variants:

            key = DraftVariantService.build_variant_key(
                draft_variant.attribute_values.all()
            )

            product_variant = existing_variants.get(key)

            #
            # Aynı varyant varsa
            #
            if product_variant:

                cls._create_store_product(
                    draft=draft,
                    product_variant=product_variant,
                    draft_variant=draft_variant,
                )

            #
            # Yoksa oluştur
            #
            else:

                if (
                    draft_variant.barcode
                    and ProductVariant.objects.filter(
                        barcode=draft_variant.barcode,
                    ).exists()
                ):
                    raise ValueError(
                        f"{draft_variant.barcode} barkodu zaten kullanılmaktadır."
                    )

                # Race Condition Koruması
                try:
                    with transaction.atomic():
                        product_variant = ProductVariant.objects.create(
                            product=product,
                            barcode=draft_variant.barcode,
                            is_active=True,
                        )
                except IntegrityError:
                    raise ValueError(
                        f"Siz yayına alırken '{draft_variant.barcode}' barkodu saniyeler farkıyla "
                        f"başka bir sisteme kaydedildi. Lütfen barkodu kontrol edin."
                    )

                product_variant = ProductVariant.objects.create(
                    product=product,
                    barcode=draft_variant.barcode,
                    is_active=True,
                )

                product_variant.attribute_values.set(
                    draft_variant.attribute_values.all()
                )

                cls._create_store_product(
                    draft=draft,
                    product_variant=product_variant,
                    draft_variant=draft_variant,
                )

                created_variants.append(
                    product_variant
                )

                existing_variants[key] = product_variant


            #
            # Varsayılan varyant
            #
            if (
                draft_variant.is_default
                and product.default_variant_id is None
            ):
                product.default_variant = product_variant

                product.save(
                    update_fields=["default_variant"]
                )

        return created_variants

    @classmethod
    def _copy_missing_images(
        cls,
        *,
        draft,
        product,
    ):
        """
        Mevcut üründe bulunmayan görsel gruplarını
        taslaktan ürüne aktarır.
        """

        #
        # Üründeki mevcut image group key'leri
        #

        existing_groups = {}

        product_groups = (
            product.image_groups
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "visual_attribute_values",
            )
        )

        for group in product_groups:

            key = DraftVariantService.build_variant_key(
                group.visual_attribute_values.all(),
            )

            existing_groups[key] = group


        #
        # Draft image group'larını dolaş
        #

        draft_groups = (
            draft.image_groups
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


        for draft_group in draft_groups:

            key = DraftVariantService.build_variant_key(
                draft_group.visual_attribute_values.all(),
            )

            #
            # Aynı image group zaten varsa
            #
            if key in existing_groups:
                continue


            #
            # Yeni image group oluştur
            #

            product_group = ProductImageGroup.objects.create(
                product=product,
                sort_order=draft_group.sort_order,
                is_active=draft_group.is_active,
            )

            product_group.visual_attribute_values.set(
                draft_group.visual_attribute_values.all(),
            )


            #
            # Resimleri kopyala
            #

            product_images = []

            for draft_image in draft_group.images.filter(
                is_active=True,
            ):

                image = ProductImage(
                    group=product_group,
                    alt_text=draft_image.alt_text,
                    is_main=draft_image.is_main,
                    sort_order=draft_image.sort_order,
                )

                if draft_image.image:

                    file_name = os.path.basename(
                        draft_image.image.name,
                    )

                    image.image.save(
                        file_name,
                        ContentFile(
                            draft_image.image.read(),
                        ),
                        save=False,
                    )

                product_images.append(image)

            ProductImage.objects.bulk_create(
                product_images,
            )
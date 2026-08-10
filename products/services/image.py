from django.db import transaction
from django.db.models import Max, Prefetch, F, Count
import hashlib

from products.models import ProductDraftImage, AttributeValue, CategoryAttribute, ProductDraftImageGroup, ProductDraft


def _get_visual_values(
    *,
    draft,
    selections,
):
    """
    selections içindeki görsel attribute değerlerini doğrular
    ve AttributeValue queryset'i döndürür.

    selections örneği:

    {}

    {
        3: [12],
    }

    {
        3: [12],
        5: [28],
    }
    """

    if not selections:
        return AttributeValue.objects.none()

    value_ids = []

    for attribute_id, ids in selections.items():

        if len(ids) > 1:
            raise ValueError(
                "Görsel özellik için yalnızca bir değer seçilebilir.."
            )

        category_attribute = (
            CategoryAttribute.objects.filter(
                category=draft.category,
                attribute_id=attribute_id,
                is_visual=True,
            )
            .select_related("attribute")
            .first()
        )

        if category_attribute is None:
            raise ValueError(
                "Geçersiz görsel özellik."
            )

        if not ids:
            continue

        if len(ids) != len(set(ids)):
            raise ValueError(
                "Duplicate attribute values."
            )

        count = AttributeValue.objects.filter(
            attribute_id=attribute_id,
            id__in=ids,
        ).count()

        if count != len(ids):
            raise ValueError(
                "Invalid attribute values."
            )

        value_ids.extend(ids)

    return AttributeValue.objects.filter(
        id__in=value_ids,
    )

def _find_existing_group(
    *,
    draft,
    values,
):
    """
    Aynı visual attribute kombinasyonuna sahip
    mevcut grubu döndürür.

    Yoksa None döner.
    """

    value_ids = set(
        values.values_list(
            "id",
            flat=True,
        )
    )

    for group in draft.image_groups.prefetch_related(
        "visual_attribute_values",
    ).order_by("id"):

        group_value_ids = set(
            group.visual_attribute_values.values_list(
                "id",
                flat=True,
            )
        )

        if group_value_ids == value_ids:
            return group

    return None

@transaction.atomic
def create_group(
    *,
    draft,
    selections=None,
):
    """
    Yeni bir görsel grubu oluşturur.

    selections:

    {} -> ortak grup

    {
        3: [12],
    }

    {
        3: [12],
        5: [28],
    }
    """

    ProductDraft.objects.select_for_update().get(pk=draft.pk)

    values = _get_visual_values(
        draft=draft,
        selections=selections,
    )

    existing = _find_existing_group(
        draft=draft,
        values=values,
    )

    if existing is not None:
        raise ValueError(
            "Bu görsel grubu zaten var."
        )

    is_default_group = not bool(selections)

    if is_default_group:
        # Ortak grup oluşturuluyorsa mevcut diğer grupların sırasını 1 kaydır
        ProductDraftImageGroup.objects.filter(draft=draft).update(
            sort_order=F("sort_order") + 1
        )
        next_sort = 0
    else:
        # Spesifik bir varyant grubu oluşturuluyorsa en sona ekle
        last_sort = draft.image_groups.aggregate(
            Max("sort_order")
        )["sort_order__max"]

        next_sort = (
            0
            if last_sort is None
            else last_sort + 1
        )

    group = ProductDraftImageGroup.objects.create(
        draft=draft,
        sort_order=next_sort,
    )

    group.visual_attribute_values.set(values)

    return group

@transaction.atomic
def upload_images(
    *,
    group,
    files,
):
    """
    Görsel grubuna yeni görseller ekler.

    - Sıralamayı otomatik belirler.

    Aynı dosyalar tekrar yüklenmeye çalışılırsa atlar.
    """

    if not files:
        return []

    ProductDraftImageGroup.objects.select_for_update().get(pk=group.pk)

    # 1. Bu gruba ait MEVCUT resimlerin hash değerlerini al (Set kullanıyoruz ki arama hızlı olsun)
    existing_hashes = set(
        group.images.filter(file_hash__isnull=False)
        .values_list("file_hash", flat=True)
    )

    current = group.images.count()

    if current + len(files) > 20:
        raise ValueError(
            "Bu grupta en fazla 20 görsel olabilir."
        )

    last_sort = group.images.aggregate(
        Max("sort_order")
    )["sort_order__max"]

    next_sort = (
        0
        if last_sort is None
        else last_sort + 1
    )

    images = []

    for index, file in enumerate(files):
        # 2. Gelen dosyanın hash değerini hesapla
        file_hash = _calculate_file_hash(file)

        # 3. Eğer bu dosya (aynı fotoğraf) grupta zaten varsa işlemi atla
        if file_hash in existing_hashes:
            continue

        image = ProductDraftImage.objects.create(
            group=group,
            image=file,
            sort_order=next_sort + index,
            file_hash=file_hash,
        )

        # Eğer kullanıcı tek upload işleminde AYNı dosyayı 2 kere seçtiyse engellemek için existing_hashes listesine ekle
        existing_hashes.add(file_hash)

        images.append(image)
        next_sort += 1

    # 4. Eğer yüklenen HER ŞEY zaten mevcutsa (images dizisi boşsa) kullanıcıya uyarı fırlat
    if not images and files:
        raise ValueError("Seçtiğiniz görseller bu grupta zaten mevcut.")

    return images

@transaction.atomic
def update_image(
    *,
    image,
    is_main=None,
    alt_text=None,
):
    """
    Taslak görselini günceller.
    """

    group = (
        ProductDraftImageGroup.objects
        .select_for_update()
        .get(pk=image.group_id)
    )

    image = (
        ProductDraftImage.objects
        .select_for_update()
        .get(pk=image.pk)
    )

    update_fields = []

    if is_main is False and image.is_main:
        raise ValueError(
            "Mevcut kapak görselinin işareti kaldırılamaz. "
            "Lütfen bunun yerine başka bir görseli kapak olarak seçin."
        )

    if is_main is True:
        ProductDraftImage.objects.filter(
            group=group,
            is_main=True,
        ).exclude(
            pk=image.pk,
        ).update(
            is_main=False,
        )

        image.is_main = True
        update_fields.append("is_main")


    if alt_text is not None:
        update_fields.append("alt_text")
        image.alt_text = alt_text

    if update_fields:
        image.save(update_fields=update_fields)

    return image


@transaction.atomic
def delete_image(
    *,
    image,
):
    """
    Taslak görseli siler.

    Eğer silinen görsel kapak görseliyse,
    kalan ilk görsel otomatik olarak
    yeni kapak yapılır.
    """

    image = (
        ProductDraftImage.objects
        .select_for_update()
        .get(pk=image.pk)
    )

    group = image.group
    was_main = image.is_main

    file = image.image

    image.delete()

    transaction.on_commit(
        lambda: _delete_file(file)
    )

    if not was_main:
        return

    new_main = (
        ProductDraftImage.objects
        .filter(group=group)
        .order_by("sort_order", "id")
        .first()
    )

    if new_main:
        new_main.is_main = True

        new_main.save(
            update_fields=[
                "is_main",
            ],
        )


@transaction.atomic
def reorder_images(
    *,
    group,
    image_ids,
):
    """
    Bir görsel grubundaki görsellerin sıralamasını günceller.

    image_ids örneği:

    [
        15,
        8,
        21,
        5,
    ]
    """

    if not image_ids:
        return

    if len(image_ids) != len(set(image_ids)):
        raise ValueError(
            "Duplicate image ids."
        )


    images = {
        image.id: image
        for image in (
            group.images
            .select_for_update()
            .only(
                "id",
                "sort_order",
            )
        )
    }

    if len(images) != len(image_ids):
        raise ValueError(
            "Image count mismatch."
        )

    updated = []

    for order, image_id in enumerate(image_ids):

        image = images.get(image_id)

        if image is None:
            raise ValueError(
                f"Image {image_id} does not belong to group."
            )

        if image.sort_order != order:
            image.sort_order = order
            updated.append(image)

    ProductDraftImage.objects.bulk_update(
        updated,
        ["sort_order"],
    )

@transaction.atomic
def update_group(
    *,
    group,
    selections,
):
    """
    Görsel grubunun visual attribute kombinasyonunu günceller.

    selections:

    {} -> Ortak grup

    {
        3: [12],
    }

    {
        3: [12],
        5: [28],
    }
    """

    group = (
        ProductDraftImageGroup.objects
        .select_for_update()
        .select_related("draft")
        .prefetch_related("visual_attribute_values")
        .get(pk=group.pk)
    )

    if (
        len(group.visual_attribute_values.all()) == 0
        and selections
    ):
        raise ValueError(
            "Default image group cannot be modified."
        )

    values = _get_visual_values(
        draft=group.draft,
        selections=selections,
    )

    existing = _find_existing_group(
        draft=group.draft,
        values=values,
    )

    if existing is not None and existing.pk != group.pk:
        raise ValueError(
            "Bu görsel grubu zaten var."
        )

    group.visual_attribute_values.set(values)

    return group

@transaction.atomic
def delete_group(
    *,
    group,
    allow_default=False,
):
    """
    Görsel grubunu siler.

    Ortak (default) grup silinemez.
    """


    group = (
        ProductDraftImageGroup.objects
        .select_for_update()
        .get(pk=group.pk)
    )

    if (
        not allow_default
        and not group.visual_attribute_values.exists()
    ):  
        raise ValueError(
            "Ortak görsel grubu silinemez."
        )

    files = [
        image.image
        for image in group.images.only("image")
    ]

    group.delete()

    def delete_files():
        for file in files:
            _delete_file(file)
    
    transaction.on_commit(delete_files)

def _delete_file(image_field):
    """
    ImageField'e ait fiziksel dosyayı siler.

    Storage backend kullanıldığı için
    local ve cloud storage ile uyumludur.
    """

    if not image_field:
        return

    storage = image_field.storage
    name = image_field.name

    if name and storage.exists(name):
        storage.delete(name)

def get_group(
    *,
    draft,
    selections=None,
):
    """
    selections'e karşılık gelen görsel grubunu döndürür.

    selections=None veya {} ise ortak (default) grup döner.
    """

    values = _get_visual_values(
        draft=draft,
        selections=selections,
    )

    return _find_existing_group(
        draft=draft,
        values=values,
    )


def _calculate_file_hash(file):
    """Dosyanın MD5 parmak izini hesaplar."""
    hasher = hashlib.md5()
    # Dosyayı parçalar halinde (chunk) okuyoruz ki 10MB dosyalar RAM'i şişirmesin
    for chunk in file.chunks():
        hasher.update(chunk)
    file.seek(0)  # Okuma bittikten sonra dosya imlecini başa sarıyoruz
    return hasher.hexdigest()


class DraftImageService:

    @staticmethod
    def get_groups(
        *,
        draft,
    ):
        
        return (
            draft.image_groups
            .annotate(
                # Grubun kaç tane görsel özelliği olduğunu sayıyoruz
                # Ortak grupta bu değer 0, diğerlerinde 1 veya daha fazladır
                attr_count=Count("visual_attribute_values")
            )
            .prefetch_related(
                "visual_attribute_values",
                Prefetch(
                    "images",
                    queryset=ProductDraftImage.objects.order_by(
                        "sort_order",
                        "id",
                    ),
                ),
            )
            .order_by(
                "attr_count",
                "sort_order",
                "id",
            )
        )

    
    @staticmethod
    def validate_step3(
        *,
        draft,
    ):

        groups = ProductDraftImageGroup.objects.filter(
            draft=draft,
            is_active=True,
        )

        if not groups.exists():
            raise ValueError(
                "En az bir görsel grubu oluşturmalısınız."
            )

        if not ProductDraftImage.objects.filter(
            group__draft=draft,
            is_active=True,
        ).exists():
            raise ValueError(
                "En az bir görsel yüklemelisiniz."
            )

    @staticmethod
    @transaction.atomic
    def delete_all_for_draft(*, draft):
        """
        Kategori değişimi gibi durumlarda, taslağa ait
        TÜM görsel gruplarını ve fiziksel dosyalarını temizler.
        """
        # 1. Silinecek tüm fiziksel dosyaların yollarını hafızaya al
        files = [
            img.image 
            for img in ProductDraftImage.objects.filter(group__draft=draft).only("image")
        ]

        # 2. Veritabanı kayıtlarını tek sorguda sil (Görseller cascade ile silinir)
        draft.image_groups.all().delete()

        # 3. DB işlemi başarılı olursa, dosyaları S3/Local'den fiziksel olarak uçur
        def delete_files():
            for file in files:
                _delete_file(file)

        transaction.on_commit(delete_files)


    @staticmethod
    @transaction.atomic
    def clean_orphaned_groups(*, draft):
        """
        Varyant silindiğinde çalışır.
        Artık hiçbir varyantla eşleşmeyen (yetim kalan) görsel gruplarını
        tek sorguda tespit edip fiziksel dosyalarıyla birlikte temizler.
        (Ortak grup her zaman korunur.)
        """
        # 1. Kalan tüm varyantların özellik ID'lerini set() olarak topla
        valid_combinations = [
            set(variant.attribute_values.values_list("id", flat=True))
            for variant in draft.variants.filter(is_active=True).prefetch_related("attribute_values")
        ]

        orphaned_group_ids = []
        
        # 2. Sadece görsel gruplarını ve özelliklerini çek
        for group in draft.image_groups.prefetch_related("visual_attribute_values"):
            group_val_ids = set(group.visual_attribute_values.values_list("id", flat=True))
            
            # Ortak (default) grupsa atla (asla silinmez)
            if not group_val_ids:
                continue

            # Bu görsel grubunun özellikleri, kalan varyantlardan en az birinin alt kümesi mi?
            is_orphaned = True
            for variant_val_ids in valid_combinations:
                if group_val_ids.issubset(variant_val_ids):
                    is_orphaned = False
                    break
                    
            if is_orphaned:
                orphaned_group_ids.append(group.id)

        # Eğer yetim kalan grup yoksa işlemi bitir
        if not orphaned_group_ids:
            return

        # 3. Silinecek dosyaların yollarını tek sorguda hafızaya al
        files = [
            img.image 
            for img in ProductDraftImage.objects.filter(group_id__in=orphaned_group_ids).only("image")
        ]

        # 4. Veritabanından yetim grupları tek seferde sil (Görseller CASCADE ile silinir)
        draft.image_groups.filter(id__in=orphaned_group_ids).delete()

        # 5. DB işlemi başarılı olursa, dosyaları S3/Local'den fiziksel olarak uçur
        def delete_files():
            for file in files:
                _delete_file(file)
                
        transaction.on_commit(delete_files)

    

    @staticmethod
    def get_visual_variant_count(*, draft):
        """
        Aktif varyantların kaç farklı 'görsel kombinasyona' (imzaya) sahip olduğunu sayar.
        Örn: Siyah-S ve Siyah-M varyantları varsa, görsel imza sadece "Siyah" olduğu için 1 döner.
        """
        active_variants = draft.variants.filter(is_active=True).prefetch_related('attribute_values')
        if active_variants.count() == 0:
            return 0

        visual_attr_ids = CategoryAttribute.objects.filter(
            category=draft.category, is_visual=True
        ).values_list('attribute_id', flat=True)

        valid_signatures = set()
        for variant in active_variants:
            visual_vals = variant.attribute_values.filter(attribute_id__in=visual_attr_ids)
            sig = tuple(sorted(visual_vals.values_list('id', flat=True)))
            if sig:
                valid_signatures.add(sig)

        return len(valid_signatures)


    @staticmethod
    def get_groups(*, draft):
        return (
            draft.image_groups
            .prefetch_related(
                "visual_attribute_values",
                Prefetch(
                    "images",
                    queryset=ProductDraftImage.objects.order_by("sort_order", "id"),
                ),
            )
            .order_by("sort_order", "id")
        )

    
    @staticmethod
    @transaction.atomic
    def sync_draft_groups(*, draft):
        ProductDraft.objects.select_for_update().get(pk=draft.pk)
        
        active_variants = draft.variants.filter(is_active=True).prefetch_related('attribute_values')
        if active_variants.count() == 0:
            return

        visual_attr_ids = CategoryAttribute.objects.filter(
            category=draft.category, is_visual=True
        ).values_list('attribute_id', flat=True)

        valid_signatures = set()
        for variant in active_variants:
            visual_vals = variant.attribute_values.filter(attribute_id__in=visual_attr_ids)
            sig = tuple(sorted(visual_vals.values_list('id', flat=True)))
            if sig:
                valid_signatures.add(sig)

        # ARTIK TOPLAM VARYANT SAYISINA DEĞİL, GÖRSEL İMZA SAYISINA BAKIYORUZ
        visual_variant_count = len(valid_signatures)
        is_single_visual = (visual_variant_count <= 1)

        existing_groups = list(draft.image_groups.prefetch_related('visual_attribute_values').all())
        has_default = False

        for group in existing_groups:
            group_sig = tuple(sorted(group.visual_attribute_values.values_list('id', flat=True)))
            
            if is_single_visual:
                # 1 görsel varyant kaldıysa, ortak grup ve uyuşmayan diğer tüm grupları sil
                target_sig = list(valid_signatures)[0] if valid_signatures else ()
                if group_sig != target_sig:
                    delete_group(group=group, allow_default=True,)
            else:
                # Çoklu görsel varyant varsa: Ortak grup kalabilir, varyantı silinmiş özel gruplar silinmeli
                if group_sig == ():
                    has_default = True
                elif group_sig not in valid_signatures:
                    delete_group(group=group, allow_default=True,)

        # Görsel imza sayısı 1'e düştüyse ve ortada hiç grup kalmadıysa spesifik grubu otomatik aç
        if is_single_visual and draft.image_groups.count() == 0:
            if valid_signatures:
                target_sig = list(valid_signatures)[0]
                values = AttributeValue.objects.filter(id__in=target_sig)
                
                selections = {
                    value.attribute_id: [value.id]
                    for value in values
                }
                create_group(draft=draft, selections=selections)
            else:
                create_group(draft=draft, selections={})

        # Görsel varyant sayısı 1'den büyükse ve ortak grup yoksa ortak grubu oluştur
        elif visual_variant_count > 1 and not has_default:
            create_group(draft=draft, selections={})
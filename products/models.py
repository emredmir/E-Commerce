from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from store.models import Store
import uuid, os
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from products.utils import normalize_product_name
from decimal import Decimal

# ---------------------------------------------------------
# Kategori ve Marka Yönetimi
# ---------------------------------------------------------
class Category(models.Model):
    name = models.CharField(max_length=255, verbose_name="Kategori Adı")
    slug = models.SlugField(unique=True, blank=True)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children', verbose_name="Üst Kategori")
    is_active = models.BooleanField(default=True, verbose_name="Aktif mi?")

    class Meta:
        verbose_name = "Kategori"
        verbose_name_plural = "Kategoriler"

    def clean(self):
        super().clean()
        # Kategori Döngü Problemi Engelleme (Sonsuz Döngü Koruması)
        if self.parent and self.pk:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "Bir kategori kendisinin üst kategorisi olamaz."})
            
            # Derinlik öncelikli döngü kontrolü (Sonsuz döngü koruması)
            current = self.parent
            while current:
                if current.pk == self.pk:
                    raise ValidationError({"parent": "Kategoriler arasında döngüsel (üst-alt çakışması) bir bağ oluşturulamaz."})
                current = current.parent

    def save(self, *args, **kwargs):
        # Özel validasyonu (döngü kontrolünü) kayıttan önce zorunlu kılıyoruz
        self.clean() 
        if not self.slug:
            base_slug = slugify(self.name)
            # Emojili veya Latin dışı alfabelerde slugify boş döner. UUID Fallback.
            if not base_slug:
                base_slug = f"category-{uuid.uuid4().hex[:6]}"
            slug = base_slug
            counter = 1
            while Category.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Brand(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="Marka Adı")
    slug = models.SlugField(unique=True, blank=True)
    is_active = models.BooleanField(default=True, verbose_name="Aktif mi?")

    class Meta:
        verbose_name = "Marka"
        verbose_name_plural = "Markalar"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            if not base_slug:
                base_slug = f"brand-{uuid.uuid4().hex[:6]}"
            slug = base_slug
            counter = 1
            while Brand.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

from django.db import models


class CategoryBrand(models.Model):
    """
    Bir markanın hangi kategorilerde kullanılabileceğini belirtir.
    """

    category = models.ForeignKey(
        "Category",
        on_delete=models.CASCADE,
        related_name="category_brands",
    )

    brand = models.ForeignKey(
        "Brand",
        on_delete=models.CASCADE,
        related_name="brand_categories",
    )

    class Meta:
        verbose_name = "Kategori Markası"
        verbose_name_plural = "Kategori Markaları"

        constraints = [
            models.UniqueConstraint(
                fields=("category", "brand"),
                name="unique_category_brand",
            ),
        ]

        ordering = (
            "category__name",
            "brand__name",
        )
    
    def clean(self):
        super().clean()
        if self.category_id and self.category.parent is None:
            raise ValidationError(
                {
                    "category":
                    "Marka yalnızca alt kategorilere bağlanabilir."
                }
            )
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.category} → {self.brand}"

class BrandRequest(models.Model):
    """
    Satıcının sisteme yeni marka eklenmesini talep etmesi.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Bekliyor"
        APPROVED = "approved", "Onaylandı"
        REJECTED = "rejected", "Reddedildi"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="brand_requests",
    )

    category = models.ForeignKey(
        "Category",
        on_delete=models.CASCADE,
        related_name="brand_requests",
    )

    brand_name = models.CharField(
        max_length=120,
    )

    note = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_brand_requests",
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    last_activity_at = models.DateTimeField(
        auto_now=True,
    )

    

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Marka Talebi"
        verbose_name_plural = "Marka Talepleri"

        constraints = [
            models.UniqueConstraint(
                fields=("seller", "category", "brand_name"),
                name="unique_brand_request_per_seller",
            )
        ]

    def __str__(self):
        return self.brand_name


class ProductStatus(models.TextChoices):
    DRAFT = "draft", _("Taslak")
    ACTIVE = "active", _("Yayında")
    ARCHIVED = "archived", _("Arşivlendi")

class Product(models.Model):
    name = models.CharField(max_length=255, verbose_name="Ürün Adı") 
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(verbose_name="Genel Ürün Açıklaması")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=ProductStatus.choices, default=ProductStatus.DRAFT, db_index=True,)

    default_variant = models.ForeignKey("ProductVariant", null=True, blank=True, on_delete=models.SET_NULL, related_name="default_for_product",)

    created_by_store = models.ForeignKey(
        "store.Store",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_products",
        verbose_name="Oluşturan Mağaza",
    )

    normalized_name = models.CharField(
        max_length=255,
        editable=False,
        db_index=True,
    )

    normalized_key = models.CharField(
        max_length=255,
        editable=False,
        db_index=True,
    )

    tokens = models.JSONField(
        default=list,
        editable=False,
        verbose_name="Ürün Kelimeleri",
    )

    class Meta:
        verbose_name = "Global Ürün"
        verbose_name_plural = "Global Ürünler"

    @property
    def is_active(self):
        return self.status == ProductStatus.ACTIVE
    
    def clean(self):
        super().clean()
        if (self.default_variant and self.pk and self.default_variant.product_id != self.pk):
            raise ValidationError({"default_variant":"Varsayılan varyant bu ürüne ait olmalıdır."})

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            if not base_slug:
                base_slug = f"product-{uuid.uuid4().hex[:6]}"
            base_slug = base_slug[:200]
            
            slug = base_slug

            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{uuid.uuid4().hex[:4]}"

            self.slug = slug

        normalized = normalize_product_name(self.name)

        self.normalized_name = normalized["normalized_name"]
        self.normalized_key = normalized["normalized_key"]
        self.tokens = normalized["tokens"]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

# ---------------------------------------------------------
# EAV (Entity-Attribute-Value) Özellik Sistemi
# ---------------------------------------------------------
class Attribute(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="Özellik Adı (Örn: Renk, Beden)")
    is_active = models.BooleanField(default=True, verbose_name="Aktif mi?")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

class CategoryAttribute(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="category_attributes",
        verbose_name="Kategori"
    )

    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.CASCADE,
        related_name="category_attributes",
        verbose_name="Özellik"
    )

    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name="Sıralama"
    )

    is_filterable = models.BooleanField(
        default=True,
        verbose_name="Filtreleme yapılabilir mi?"
        )

    is_required = models.BooleanField(
        default=False,
        verbose_name="Zorunlu mu?"
    )

    is_variant = models.BooleanField(
        default=True,
        verbose_name="Varyant oluşturuyor mu?"
        )

    is_visual = models.BooleanField(default=False, verbose_name="Görselleri Değiştirir mi?", help_text="Örn: Renk, Ekran Boyutu, Kasa Tipi.")
    
    allow_custom_values = models.BooleanField(default=False, verbose_name="Satıcı yeni değer ekleyebilir mi?")

    class Meta:
        verbose_name = "Kategori Özelliği"
        verbose_name_plural = "Kategori Özellikleri"

        constraints = [
            models.UniqueConstraint(
                fields=["category", "attribute"],
                name="unique_category_attribute"
            )
        ]

        ordering = [
            "sort_order",
            "attribute__name",
        ]

    def __str__(self):
        return f"{self.category} → {self.attribute}"


class AttributeValue(models.Model):
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='values')
    value = models.CharField(max_length=100, verbose_name="Değer (Örn: Kırmızı, XL, 128GB)")
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Özellik Değeri"
        verbose_name_plural = "Özellik Değerleri"
        constraints = [
            models.UniqueConstraint(fields=['attribute', 'value'], name='unique_attribute_value')
        ]

    def save(self, *args, **kwargs):
        self.value = self.value.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.attribute.name}: {self.value.title()}"

class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    barcode = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="Barkod (EAN/UPC)")
    attribute_values = models.ManyToManyField(AttributeValue, related_name='variants', blank=True)
    is_active = models.BooleanField(default=True, verbose_name="Varyant Aktif mi?")

    @property
    def attribute_signature(self):
        return tuple(
        sorted(
            value.pk
            for value in self.attribute_values.all()
        )
    )
    
    class Meta:
        verbose_name = "Ürün Varyantı"
        verbose_name_plural = "Ürün Varyantları"
        ordering = ["id"]

    def __str__(self):
        # Admin listelemesinde N+1 performans patlamasını engellemek için maliyetsiz gösterim.
        # Özellikler admin list_display ile ayrıca gösterilmelidir.
        return f"{self.product.name} (Varyant ID: {self.pk})"


def product_image_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    product = instance.group.product
    return (
        f"products/"
        f"{product.pk}/"
        f"{filename}"
    )

class ProductImageGroup(models.Model):

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="image_groups",
    )

    visual_attribute_values = models.ManyToManyField(
        AttributeValue,
        related_name="image_groups",
        blank=True,
        help_text=(
            "Yalnızca is_visual=True olan attribute "
            "değerleri eklenmelidir."
        ),
    )

    sort_order = models.PositiveIntegerField(
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:

        ordering = [
            "sort_order",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "product",
                    "sort_order",
                ]
            ),
            models.Index(
                fields=[
                    "product",
                    "is_active",
                ]
            ),
        ]

    def clean(self):
        super().clean()

        if not self.product_id:
            return

        if self.pk is None:
            return

        values = list(
            self.visual_attribute_values.select_related(
                "attribute",
            )
        )

        # Ortak grup (boş) her zaman geçerlidir.
        if not values:
            return

        seen_attributes = set()

        for value in values:

            category_attribute = CategoryAttribute.objects.filter(
                category=self.product.category,
                attribute=value.attribute,
            ).first()

            if category_attribute is None:
                raise ValidationError(
                    {
                        "visual_attribute_values":
                        f"{value.attribute} bu kategoriye ait değil."
                    }
                )

            if not category_attribute.is_visual:
                raise ValidationError(
                    {
                        "visual_attribute_values":
                        f"{value.attribute} görsel özelliği değildir."
                    }
                )

            if value.attribute_id in seen_attributes:
                raise ValidationError(
                    {
                        "visual_attribute_values":
                        f"{value.attribute} yalnızca bir kez seçilebilir."
                    }
                )

            seen_attributes.add(value.attribute_id)

class ProductImage(models.Model):
    group = models.ForeignKey(ProductImageGroup, on_delete=models.CASCADE, related_name="images",)
    image = models.ImageField(upload_to=product_image_upload_to)
    alt_text = models.CharField(max_length=255, blank=True, verbose_name="Alt Metin")
    is_main = models.BooleanField(default=False, verbose_name="Kapak Fotoğrafı mı?")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Sıralama")

    class Meta:
        verbose_name = "Ürün Görseli"
        verbose_name_plural = "Ürün Görselleri"
        ordering = ['sort_order', "id"]

        constraints = [
            models.UniqueConstraint(fields=["group"], condition=Q(is_main=True), name="unique_main_product_image_per_group",)
        ]

    def save(self, *args, **kwargs):
        # Sadece objenin İLK YARATILIŞ anında (update değilken) çalışır.  yeni bir fotoğraf ekleniyorsa çalışır.
        # İlk yüklenen ürün görseli otomatik olarak kapak yapılır.
        if self.pk is None:
            if not ProductImage.objects.filter(
                group=self.group
            ).exists():
                self.is_main = True

        if self.is_main:
            ProductImage.objects.filter(group=self.group).exclude(pk=self.pk).update(is_main=False)

        super().save(*args, **kwargs)

    @property
    def product(self):
        return self.group.product

    def __str__(self):
        return f"{self.product.name} - {'Kapak' if self.is_main else 'Görsel'}"

# ---------------------------------------------------------
# Satıcı Teklifi (Buy Box Mimarisi) & State Machine
# ---------------------------------------------------------
class StoreProductStatus(models.TextChoices):
    ACTIVE = 'active', 'Yayında'
    DRAFT = 'draft', 'Taslak'
    OUT_OF_STOCK = 'out_of_stock', 'Tükendi'
    ARCHIVED = 'archived', 'Arşivlendi'

class StoreProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=StoreProductStatus.ACTIVE)

    def purchasable(self):
        # variant__product__status=ProductStatus.ACTIVE eklenerek üst ürünün aktifliği de şart koşuldu.
        # Ürün teklifi yayında, stok var, bağlı varyant aktif, üst ürün aktif VE mağaza aktif.
        return self.active().filter(
            stock__gt=0, 
            variant__is_active=True,
            variant__product__status=ProductStatus.ACTIVE,
            store__is_active=True
        )

class StoreProductManager(models.Manager):
    def get_queryset(self):
        return StoreProductQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def purchasable(self):
        return self.get_queryset().purchasable()

class StoreProduct(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='store_products')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='store_offers')
    
    sku = models.CharField(max_length=100, blank=True, null=True, db_index=True, verbose_name="Satıcı Stok Kodu (SKU)")
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Satış Fiyatı")
    stock = models.PositiveIntegerField(default=0, verbose_name="Stok Adedi")
    sold_count = models.PositiveIntegerField(default=0, verbose_name="Satış Sayısı")
    seller_notes = models.TextField(blank=True, verbose_name="Satıcıya Özel Notlar")
    
    status = models.CharField(max_length=20, choices=StoreProductStatus.choices, default=StoreProductStatus.DRAFT)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StoreProductManager()

    class Meta:
        verbose_name = "Satıcı Teklifi (Envanter)"
        verbose_name_plural = "Satıcı Teklifleri"
        constraints = [
            models.UniqueConstraint(fields=['store', 'variant'], name='unique_store_variant'),
            # SKU nullable olduğu için sadece dolu olanları baz alarak çakışmayı önleyen constraint
            models.UniqueConstraint(
                fields=['store', 'sku'], 
                name='unique_store_sku', 
                condition=Q(sku__isnull=False)
            ),
        ]
        indexes = [
            models.Index(fields=['variant', 'status', 'price']),
            models.Index(fields=['store', 'status']),
        ]

    @property
    def is_active(self):
        # Sadece UI / Template (instance bazlı) kullanımlar içindir.
        return self.status == StoreProductStatus.ACTIVE

    def save(self, *args, **kwargs):
        # Stok kontrolüne göre statü otomatiği
        if self.stock == 0 and self.status == StoreProductStatus.ACTIVE:
            self.status = StoreProductStatus.OUT_OF_STOCK
        elif self.stock > 0 and self.status == StoreProductStatus.OUT_OF_STOCK:
            self.status = StoreProductStatus.ACTIVE
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.store.store_name} | {self.variant}"

class ProductPriceHistory(models.Model):
    store_product = models.ForeignKey(StoreProduct, on_delete=models.CASCADE, related_name='price_history')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Değişen Fiyat")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Değişim Tarihi")

    class Meta:
        verbose_name = "Fiyat Geçmişi"
        verbose_name_plural = "Fiyat Geçmişleri"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.store_product} -> {self.price} TL"



class ProductDraft(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Taslak"
        COMPLETED = "completed", "Tamamlandı"
        PUBLISHED = "published", "Yayınlandı"
        # OFFER = "offer", "Teklife Dönüştü"
        CANCELED = "canceled", "İptal Edildi"

    class MatchStatus(models.TextChoices):
        NONE = "none", "Eşleşme Yok"
        PENDING = "pending", "Karar Bekleniyor"
        ACCEPTED = "accepted", "Mevcut Ürün Kullanılacak"
        REJECTED = "rejected", "Yeni Ürün Oluşturulacak"
        

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_drafts",
        verbose_name="Satıcı",
    )

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="product_drafts",
        verbose_name="Mağaza",
    )

    #
    # Step 1
    #

    name = models.CharField(
        max_length=255,
        verbose_name="Ürün Adı",
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="product_drafts",
        verbose_name="Kategori",
    )

    brand = models.ForeignKey(
        Brand,
        on_delete=models.PROTECT,
        related_name="product_drafts",
        null=True,
        blank=True,
        verbose_name="Marka",
    )

    description = models.TextField(
        blank=True,
        verbose_name="Açıklama",
    )

    normalized_name = models.CharField(
        max_length=255,
        editable=False,
        db_index=True,
    )

    normalized_key = models.CharField(
        max_length=255,
        editable=False,
        db_index=True,
    )

    tokens = models.JSONField(
        default=list,
        editable=False,
        verbose_name="Ürün Kelimeleri",
    )

    matched_product = models.ForeignKey(
        "Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="matched_drafts",
    )

    match_status = models.CharField(
        max_length=20,
        choices=MatchStatus.choices,
        default=MatchStatus.NONE,
        db_index=True,
        verbose_name="Eşleşme Durumu",
    )

    published_product = models.ForeignKey(
        "Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drafts",
    )

    last_completed_step = models.PositiveSmallIntegerField(
        default=1,
    )

    #
    # Wizard
    #

    current_step = models.PositiveSmallIntegerField(
        default=1,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(4),
        ],
        verbose_name="Mevcut Adım",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Durum",
    )

    #
    # Dates
    #

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Tamamlanma Tarihi",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme",
    )

    class Meta:

        verbose_name = "Ürün Taslağı"

        verbose_name_plural = "Ürün Taslakları"

        ordering = [
            "-updated_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "seller",
                    "status",
                ]
            ),
            models.Index(
                fields=[
                    "seller",
                    "match_status",
                ]
            ),
            models.Index(
                fields=[
                    "store",
                    "status",
                ]
            ),
            models.Index(
                fields=[
                    "category",
                    "brand",
                ]
            ),
            models.Index(
                fields=[
                    "normalized_name",
                    "brand",
                    "category",
                ]
            ),
            models.Index(
                fields=[
                    "seller",
                    "updated_at",
                ]
            ),
        ]

        constraints = [
            models.CheckConstraint(
                check=Q(current_step__gte=1) &
                     Q(current_step__lte=5),
                name="valid_product_draft_step",
            ),

            models.UniqueConstraint(
                fields=[
                    "seller",
                    "store",
                    "category",
                    "normalized_key",
                ],
                condition=Q(status="draft"),
                name="unique_active_product_draft",
            ),
        ]

    @property
    def active_variant_count(self):
        return self.variants.filter(is_active=True).count()

    def save(self, *args, **kwargs):
        normalized = normalize_product_name(self.name)

        self.normalized_name = normalized["normalized_name"]
        self.normalized_key = normalized["normalized_key"]
        self.tokens = normalized["tokens"]

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.store})"


class ProductDraftVariant(models.Model):
    """
    Wizard sırasında oluşturulan geçici varyant.

    Ürün yayınlandığında bunlardan
    gerçek ProductVariant oluşturulur.
    """

    draft = models.ForeignKey(
        ProductDraft,
        on_delete=models.CASCADE,
        related_name="variants",
        verbose_name="Taslak",
    )

    sku = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Satıcı Stok Kodu (SKU)",
    )

    barcode = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Barkod",
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Satış Fiyatı",
    )

    stock = models.PositiveIntegerField(
        default=0,
        verbose_name="Stok",
    )

    is_default = models.BooleanField(
        default=False,
        verbose_name="Varsayılan Varyant",
    )

    attribute_values = models.ManyToManyField(
        AttributeValue,
        related_name="draft_variants",
        blank=True,
        verbose_name="Özellikler",
    )

    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name="Sıralama",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Aktif",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    @property
    def attribute_signature(self):
        """
        Varyantın attribute kombinasyonunu döndürür.

        Örnek:
        (5, 12, 19)
        """

        return tuple(
        sorted(
            value.pk
            for value in self.attribute_values.all()
        )
    )

    
    class Meta:

        verbose_name = "Taslak Varyant"

        verbose_name_plural = "Taslak Varyantlar"

        ordering = [
            "sort_order",
            "id",
        ]

        indexes = [

            models.Index(
                fields=[
                    "draft",
                    "is_active",
                ]
            ),

            models.Index(
                fields=[
                    "draft",
                    "is_default",
                ]
            ),

            models.Index(
                fields=[
                    "draft",
                    "sort_order",
                ]
            ),

        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "draft",
                    "sort_order",
                ],
                name="unique_draft_variant_sort_order",
            ),

            models.UniqueConstraint(
                fields=[
                    "draft",
                    "barcode",
                ],
                condition=Q(barcode__isnull=False),
                name="unique_draft_variant_barcode",
            ),

            models.UniqueConstraint(
                fields=[
                    "draft",
                    "sku",
                ],
                condition=Q(sku__isnull=False),
                name="unique_draft_variant_sku",
            ),

            models.UniqueConstraint(
                fields=[
                    "draft",
                ],
                condition=Q(is_default=True),
                name="unique_default_draft_variant",
            ),
        ]

    def __str__(self):
        return (
            f"{self.draft.name} "
            f"(Taslak Varyant #{self.pk})"
        )
    


def draft_image_upload_to(instance, filename):

    ext = os.path.splitext(filename)[1]

    filename = f"{uuid.uuid4().hex}{ext}"

    draft = instance.group.draft

    return (
        f"drafts/"
        f"{draft.store_id}/"
        f"{draft.pk}/"
        f"{filename}"
    )


class ProductDraftImageGroup(models.Model):
    """
    Aynı görselleri paylaşan varyant grubunu temsil eder.

    Örnek:

    - Ortak görseller
    - Renk = Kırmızı
    - Renk = Siyah
    - Renk = Siyah + Boyut = 55"
    """

    draft = models.ForeignKey(
        ProductDraft,
        on_delete=models.CASCADE,
        related_name="image_groups",
        verbose_name="Taslak",
    )

    visual_attribute_values = models.ManyToManyField(
        AttributeValue,
        related_name="draft_image_groups",
        blank=True,
        verbose_name="Görsel Özellikleri",
        help_text=(
            "Yalnızca is_visual=True olan özellik değerleri "
            "eklenmelidir. Boş bırakılırsa ortak görsel grubudur."
        ),
    )

    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name="Sıralama",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Aktif mi?",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Taslak Görsel Grubu"
        verbose_name_plural = "Taslak Görsel Grupları"

        ordering = [
            "sort_order",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "draft",
                    "sort_order",
                ]
            ),
            models.Index(
                fields=[
                    "draft",
                    "is_active",
                ]
            ),
        ]

    def clean(self):
        super().clean()

        if not self.draft_id:
            return

        if self.pk is None:
            return

        values = list(
            self.visual_attribute_values.select_related(
                "attribute",
            )
        )

        # Ortak grup (boş) her zaman geçerlidir.
        if not values:
            return

        seen_attributes = set()

        for value in values:

            category_attribute = CategoryAttribute.objects.filter(
                category=self.draft.category,
                attribute=value.attribute,
            ).first()

            if category_attribute is None:
                raise ValidationError(
                    {
                        "visual_attribute_values":
                        f"{value.attribute} bu kategoriye ait değil."
                    }
                )

            if not category_attribute.is_visual:
                raise ValidationError(
                    {
                        "visual_attribute_values":
                        f"{value.attribute} görsel özelliği değildir."
                    }
                )

            if value.attribute_id in seen_attributes:
                raise ValidationError(
                    {
                        "visual_attribute_values":
                        f"{value.attribute} yalnızca bir kez seçilebilir."
                    }
                )

            seen_attributes.add(value.attribute_id)

    def __str__(self):
        return f"{self.draft.name} (Grup {self.pk})"

class ProductDraftImage(models.Model):

    group = models.ForeignKey(
        ProductDraftImageGroup,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="Görsel Grubu",
    )

    image = models.ImageField(
        upload_to=draft_image_upload_to,
        verbose_name="Görsel",
    )

    alt_text = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Alt Metin",
    )

    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name="Sıralama",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Aktif mi?",
    )

    is_main = models.BooleanField(
        default=False,
        verbose_name="Kapak Görseli mi?",
    )

    file_hash = models.CharField(
        max_length=32,
        null=True,
        blank=True, 
        editable=False, 
        db_index=True,
        verbose_name="Dosya Parmak İzi (MD5)"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:

        verbose_name = "Taslak Görsel"

        verbose_name_plural = "Taslak Görselleri"

        ordering = [
            "sort_order",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "group",
                    "sort_order",
                ]
            ),
            models.Index(
                fields=[
                    "group",
                    "is_active",
                ]
            ),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "group",
                ],
                condition=Q(
                    is_main=True,
                ),
                name="unique_main_draft_image_per_group",
            ),

            models.UniqueConstraint(
                fields=["group", "file_hash"],
                condition=Q(file_hash__isnull=False), # Sadece hash'i olanlar (boş olmayanlar) için kontrol et
                name="unique_image_hash_per_group",
            ),

        ]

    def save(self, *args, **kwargs):
        # Eğer bu gruba ilk kez fotoğraf ekleniyorsa, otomatik kapak yap
        if self.pk is None:
            if not ProductDraftImage.objects.filter(group=self.group).exists():
                self.is_main = True

        # Eğer bu fotoğraf kapak seçildiyse, gruptaki diğer fotoğraflardan kapak özelliğini kaldır
        if self.is_main:
            ProductDraftImage.objects.filter(group=self.group).exclude(pk=self.pk).update(is_main=False)

        super().save(*args, **kwargs)

    @property
    def draft(self):
        return self.group.draft

    def __str__(self):
        return (
            f"{self.group.draft.name} "
            f"(Resim {self.pk})"
        )


from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages
from django.db.models import Count, Min, Q
from .models import (
    Category, Brand, Product, Attribute, AttributeValue, 
    ProductVariant, ProductImage, StoreProduct, ProductPriceHistory,
    StoreProductStatus, CategoryAttribute
)

# ---------------------------------------------------------
# Inline Sınıfları
# Modelle ilişkili kayıtları aynı sayfada düzenlemek için kullanılır
# ---------------------------------------------------------


# Attributevalue kısmını Attribute tablosunda göster
class AttributeValueInline(admin.TabularInline): #TabularInline: Tablo şeklinde gösterir.
    model = AttributeValue
    extra = 1 # Boş form sayısını belirler.
    fields = ('value',)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    show_change_link = True
    fields = ('image', 'image_preview', 'variant', 'is_main', 'sort_order')
    readonly_fields = ('image_preview',)

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-width:100px; max-height:80px; object-fit:contain;" />',
                obj.image.url
            )
        return "—"
    image_preview.short_description = "Önizleme"


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ('barcode', 'is_active')
    # M2M(many-to-many) attribute_values inline'da yönetmek zor, Attribute Values'leri show_change_link ile detay sayfasına yönlendiriyoruz
    show_change_link = True


class ProductPriceHistoryInline(admin.TabularInline):
    model = ProductPriceHistory
    extra = 0
    readonly_fields = ('price', 'created_at')
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False

class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 1
    fields = ('attribute', 'is_required', 'is_variant', 'is_filterable', 'sort_order')
    ordering = ('sort_order',)


# ---------------------------------------------------------
# Kategori Admin
# ---------------------------------------------------------


@admin.register(Category) # Category modelini admin panelinde CategoryAdmin ayarlarıyla göster
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'is_active', 'children_count', 'product_count', 'slug')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('slug',)
    list_select_related = ('parent',) # performans içindir. SQL JOIN yapılır. Tek sorguda gelir. Yani her alt kategorinin parentini ayrrı sorgulamak yerine alt kategori sorgusunda parenti de çeker
    list_editable = ('is_active',)
    ordering = ('parent__name', 'name')
    inlines = [CategoryAttributeInline]
    actions = ['activate_categories', 'deactivate_categories'] # toplu işlem menüsüne bunları ekler.

    def get_queryset(self, request):
        qs = super().get_queryset(request) #SELECT * FROM category
        return qs.annotate( # mevcut sorguya yeni hesaplanmış alanlar ekler.
            children_count_annotated=Count('children', distinct=True), # alt kategori sayısı , distinct ile alt kategoriyi tek sayar/ birden fazla saymasını engeller
            product_count_annotated=Count('products', distinct=True) # ürünlerin sayısı, distinct: ürün1,ürün1 -> 1
        )

    def children_count(self, obj):
        return obj.children_count_annotated
    children_count.short_description = "Alt Kategori"
    children_count.admin_order_field = 'children_count_annotated' # sıralama yaparken children countu kullan

    def product_count(self, obj):
        return obj.product_count_annotated
    product_count.short_description = "Ürün Sayısı"
    product_count.admin_order_field = 'product_count_annotated'

    @admin.action(description="Seçilenleri aktif et")
    def activate_categories(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} kategori aktif edildi.")

    @admin.action(description="Seçilenleri pasif et")
    def deactivate_categories(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} kategori pasif edildi.", messages.WARNING)



# ---------------------------------------------------------
# Marka Admin
# ---------------------------------------------------------


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'product_count', 'slug')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('slug',)
    list_editable = ('is_active',)
    ordering = ("name",)
    actions = ['activate_brands', 'deactivate_brands']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(product_count_annotated=Count('products', distinct=True))

    def product_count(self, obj):
        return obj.product_count_annotated
    product_count.short_description = "Ürün Sayısı"
    product_count.admin_order_field = 'product_count_annotated'

    @admin.action(description="Seçilenleri aktif et")
    def activate_brands(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} marka aktif edildi.")

    @admin.action(description="Seçilenleri pasif et")
    def deactivate_brands(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} marka pasif edildi.", messages.WARNING)


# ---------------------------------------------------------
# Özellik Admin (EAV)
# ---------------------------------------------------------


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'value_count', 'category_count')
    list_filter = ('is_active',)
    search_fields = ('name',)
    list_editable = ('is_active',)
    ordering = ("name",)
    inlines = [AttributeValueInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            value_count_annotated=Count('values', distinct=True),
            category_count_annotated=Count('category_attributes', distinct=True)
            )

    def value_count(self, obj):
        return obj.value_count_annotated
    value_count.short_description = "Değer Sayısı"
    value_count.admin_order_field = 'value_count_annotated'

    def category_count(self, obj):
        return obj.category_count_annotated
    category_count.short_description = "Kategori Sayısı"
    category_count.admin_order_field = 'category_count_annotated'


# ---------------------------------------------------------
# Ürün Admin
# ---------------------------------------------------------


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'category', 'brand', 'variant_count', 'min_price', 'created_at')
    list_filter = ( 'category', 'brand', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('slug', 'created_at')
    list_select_related = ('category', 'brand')
    ordering = ('-created_at',)
    inlines = [ProductVariantInline, ProductImageInline]
    actions = ['activate_products', 'deactivate_products']

    fieldsets = (
        ('Temel Bilgiler', {
            'fields': ('name', 'slug', 'description', 'category', 'brand', 'status')
        }),
        ('Zaman Bilgileri', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request) 
        return qs.annotate(
            variant_count_annotated=Count('variants', distinct=True), # ürünün varyant sayısı
            # En düşük aktif teklif fiyatı → Buy Box önizlemesi
            min_price_annotated=Min('variants__store_offers__price',filter=Q(variants__store_offers__status=StoreProductStatus.ACTIVE)) # aktif ürünün en düşük fiyatı
        ).prefetch_related('images')

    def variant_count(self, obj):
        return obj.variant_count_annotated
    variant_count.short_description = "Varyant"
    variant_count.admin_order_field = 'variant_count_annotated'

    def min_price(self, obj):
        if obj.min_price_annotated:
            return f"{obj.min_price_annotated} ₺"
        return "—"
    min_price.short_description = "En Düşük Fiyat"
    min_price.admin_order_field = 'min_price_annotated'

    def thumbnail(self, obj):
        # prefetch_related sayesinde ekstra sorgu gitmez
        main_image = next((img for img in obj.images.all() if img.is_main), None)
        if main_image:
            return format_html(
                '<img src="{}" style="width:50px; height:50px; object-fit:contain;" />',
                main_image.image.url
            )
        return "—"
    thumbnail.short_description = "Görsel"



# ---------------------------------------------------------
# Ürün Varyantı Admin
# ---------------------------------------------------------


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ('product', 'barcode', 'attribute_values_display', 'is_active', 'offer_count')
    list_filter = ('is_active', 'product__category', 'product__brand')
    search_fields = ('product__name', 'barcode')
    list_select_related = ('product',)
    list_editable = ('is_active',)
    # M2M için çift panel widget → sol: mevcut değerler, sağ: seçilenler
    filter_horizontal = ('attribute_values',)
    actions = ['activate_variants', 'deactivate_variants']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('attribute_values').annotate( # prefetch_related olmasa her variant için attribute_values query çalışır M2M de kullanılır
            offer_count_annotated=Count('store_offers', distinct=True)
        )

    def attribute_values_display(self, obj):
        # prefetch_related sayesinde ekstra sorgu gitmez tek sorguda tüm attribute values ve tüm variantlar gelir
        attrs = ", ".join(str(v) for v in obj.attribute_values.all()) # [Kırmızı, XL, Pamuk] ; Kırmızı → "Kırmızı" XL → "XL" str yap ve join ile birleştir "Kırmızı, XL, Pamuk"
        return attrs or "—"
    attribute_values_display.short_description = "Özellikler"

    def offer_count(self, obj):
        return obj.offer_count_annotated
    offer_count.short_description = "Teklif Sayısı"
    offer_count.admin_order_field = 'offer_count_annotated'

    @admin.action(description="Seçilenleri aktif et")
    def activate_variants(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} varyant aktif edildi.")

    @admin.action(description="Seçilenleri pasif et")
    def deactivate_variants(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} varyant pasif edildi.", messages.WARNING)


# ---------------------------------------------------------
# Satıcı Teklifi Admin
# ---------------------------------------------------------


@admin.register(StoreProduct)
class StoreProductAdmin(admin.ModelAdmin):
    list_display = (
        'product_name', 'variant_attrs', 'store',
        'price', 'stock', 'sold_count', 'status_badge', 'created_at'
    )
    list_filter = ('status', 'store', 'created_at', 'variant__product__category', 'variant__product__brand')
    search_fields = ('variant__product__name', 'store__store_name', 'sku')
    autocomplete_fields = ("store","variant",) # dropdown yerine search box
    readonly_fields = ('sold_count', 'created_at', 'updated_at')
    list_select_related = ('store', 'variant', 'variant__product') # FK zaten sql join ile geliyor o yüzden select related oluyor bunlar için prefetch_related yok
    ordering = ('-created_at',)
    inlines = [ProductPriceHistoryInline]
    actions = ['activate_offers', 'archive_offers', 'mark_as_draft']

    fieldsets = (
        ('Temel Bilgiler', {
            'fields': ('store', 'variant', 'sku', 'status')
        }),
        ('Fiyat ve Stok', {
            'fields': ('price', 'stock', 'sold_count')
        }),
        ('Satıcı Notu', {
            'fields': ('seller_notes',),
            'classes': ('collapse',)
        }),
        ('Zaman Bilgileri', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # attribute_values M2M için prefetch, select_related ile birleşiyor
        return qs.prefetch_related('variant__attribute_values')

    def product_name(self, obj):
        return obj.variant.product.name
    product_name.short_description = "Ürün"
    product_name.admin_order_field = 'variant__product__name'

    def variant_attrs(self, obj):
        attrs = ", ".join(str(v) for v in obj.variant.attribute_values.all())
        return attrs or f"Varyant #{obj.variant.pk}" #Eğer attribute yoksa: Varyant #12
    variant_attrs.short_description = "Varyant"

    def status_badge(self, obj):
        colors = {
            StoreProductStatus.ACTIVE:       ('#d4edda', '#155724'),
            StoreProductStatus.DRAFT:        ('#e2e3e5', '#383d41'),
            StoreProductStatus.OUT_OF_STOCK: ('#fff3cd', '#856404'),
            StoreProductStatus.ARCHIVED:     ('#f8d7da', '#721c24'),
        }
        bg, text = colors.get(obj.status, ('#eee', '#333'))
        return format_html(
            '<span style="background:{}; color:{}; padding:3px 10px; border-radius:12px; font-size:0.85em; font-weight:500;">{}</span>',
            bg, text, obj.get_status_display()
        )
    status_badge.short_description = "Durum"

    @admin.action(description="Seçilenleri yayına al")
    def activate_offers(self, request, queryset):
        activated = out_of_stock = 0
        for offer in queryset.exclude(status=StoreProductStatus.ARCHIVED):
            offer.status = StoreProductStatus.ACTIVE
            offer.save()  # save() stok==0 ise otomatik OUT_OF_STOCK yapar
            if offer.status == StoreProductStatus.ACTIVE:
                activated += 1
            else:
                out_of_stock += 1

        msg = f"{activated} teklif yayına alındı."
        if out_of_stock:
            msg += f" {out_of_stock} teklifin stoğu tükendiği için yayına alınamadı."
        self.message_user(request, msg)

    @admin.action(description="Seçilenleri arşivle")
    def archive_offers(self, request, queryset):
        count = 0
        for offer in queryset.exclude(status=StoreProductStatus.ARCHIVED):
            offer.status = StoreProductStatus.ARCHIVED
            offer.save()
            count += 1
        self.message_user(request, f"{count} teklif arşivlendi.", messages.WARNING)

    @admin.action(description="Seçilenleri taslağa al")
    def mark_as_draft(self, request, queryset):
        count = 0
        for offer in queryset:
            offer.status = StoreProductStatus.DRAFT
            offer.save()
            count += 1
        self.message_user(request, f"{count} teklif taslağa alındı.")
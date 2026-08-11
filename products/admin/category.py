from django.contrib import admin, messages
from django.db.models import Count

from products.models import (
    Category,
    CategoryAttribute,
    CategoryBrand,
)

class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 1
    fields = ('attribute', 'is_required', 'is_variant', 'is_filterable', 'sort_order')
    ordering = ('sort_order',)

    def formfield_for_foreignkey(
        self,
        db_field,
        request,
        **kwargs,
    ):
        if db_field.name == "attribute":
            kwargs["queryset"] = (
                kwargs["queryset"]
                .filter(is_active=True)
                .order_by("name")
            )
    
        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs,
        )


class CategoryBrandInline(admin.TabularInline):
    model = CategoryBrand

    extra = 0
    
    show_change_link = True

    autocomplete_fields = (
        "brand",
    )

    ordering = (
        "brand__name",
    )

    def formfield_for_foreignkey(
        self,
        db_field,
        request,
        **kwargs,
    ):
        if db_field.name == "brand":
            kwargs["queryset"] = (
                kwargs["queryset"]
                .filter(is_active=True)
                .order_by("name")
            )
        
        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs,
        )



@admin.register(Category) # Category modelini admin panelinde CategoryAdmin ayarlarıyla göster
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'is_active', 'children_count', 'product_count', 'brand_count', 'slug')
    list_filter = ('is_active', "parent")
    search_fields = ('name',)
    readonly_fields = ('slug',)
    list_select_related = ('parent',) # performans içindir. SQL JOIN yapılır. Tek sorguda gelir. Yani her alt kategorinin parentini ayrrı sorgulamak yerine alt kategori sorgusunda parenti de çeker
    list_editable = ('is_active',)
    ordering = ('parent__name', 'name')
    inlines = [CategoryAttributeInline, CategoryBrandInline]
    actions = ['activate_categories', 'deactivate_categories'] # toplu işlem menüsüne bunları ekler.

    def get_queryset(self, request):
        qs = super().get_queryset(request) #SELECT * FROM category
        return qs.annotate( # mevcut sorguya yeni hesaplanmış alanlar ekler.
            children_count_annotated=Count('children', distinct=True), # alt kategori sayısı , distinct ile alt kategoriyi tek sayar/ birden fazla saymasını engeller
            product_count_annotated=Count('products', distinct=True), # ürünlerin sayısı, distinct: ürün1,ürün1 -> 1
            brand_count_annotated=Count("category_brands", distinct=True),
        )

    def children_count(self, obj):
        return obj.children_count_annotated
    children_count.short_description = "Alt Kategori"
    children_count.admin_order_field = 'children_count_annotated' # sıralama yaparken children countu kullan

    def product_count(self, obj):
        return obj.product_count_annotated
    product_count.short_description = "Ürün Sayısı"
    product_count.admin_order_field = 'product_count_annotated'

    def brand_count(self, obj):
        return obj.brand_count_annotated
    brand_count.short_description = "Marka Sayısı"
    brand_count.admin_order_field = 'brand_count_annotated'

    @admin.action(description="Seçilenleri aktif et")
    def activate_categories(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} kategori aktif edildi.")

    @admin.action(description="Seçilenleri pasif et")
    def deactivate_categories(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} kategori pasif edildi.", messages.WARNING)
    
    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)

        if obj and obj.parent is None:
            return [
                inline
                for inline in instances
                if not isinstance(inline, CategoryBrandInline)
            ]

        return instances



@admin.register(CategoryBrand)
class CategoryBrandAdmin(admin.ModelAdmin):

    list_display = (
        "parent_category",
        "category",
        "brand",
    )

    list_filter = (
        "category__parent",
        "category",
        "brand",
    )

    search_fields = (
        "category__name",
        "brand__name",
    )

    autocomplete_fields = (
        "category",
        "brand",
    )

    ordering = (
        "category__parent__name",
        "category__name",
        "brand__name",
    )

    list_select_related = (
        "category",
        "category__parent",
        "brand",
    )

    list_per_page = 50

    def parent_category(self, obj):
        return obj.category.parent.name if obj.category.parent else "-"

    parent_category.short_description = "Ana Kategori"
    parent_category.admin_order_field = "category__parent__name"

    def formfield_for_foreignkey(
        self,
        db_field,
        request,
        **kwargs,
    ):
        if db_field.name == "category":
            kwargs["queryset"] = (
                kwargs["queryset"]
                .filter(
                    parent__isnull=False,
                    is_active=True,
                )
                .order_by(
                    "parent__name",
                    "name",
                )
            )

        elif db_field.name == "brand":
            kwargs["queryset"] = (
                kwargs["queryset"]
                .filter(is_active=True)
                .order_by("name")
            )

        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs,
        )

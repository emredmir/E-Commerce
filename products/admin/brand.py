from django.contrib import admin, messages
from django.db.models import Count

from products.models import (
    Brand,
    CategoryBrand,
)

class CategoryBrandInline(admin.TabularInline):
    model = CategoryBrand

    extra = 1
    
    show_change_link = True

    autocomplete_fields = (
        "category",
    )

    ordering = (
        "category__parent__name",
        "category__name",
    )

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
        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs,
        )



@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'product_count', 'category_count', 'slug')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('slug',)
    list_editable = ('is_active',)
    ordering = ("name",)
    inlines = [CategoryBrandInline]
    actions = ['activate_brands', 'deactivate_brands']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            product_count_annotated=Count('products', distinct=True),
            category_count_annotated=Count('brand_categories', distinct=True),
            )

    def product_count(self, obj):
        return obj.product_count_annotated
    product_count.short_description = "Ürün Sayısı"
    product_count.admin_order_field = 'product_count_annotated'

    def category_count(self, obj):
        return obj.category_count_annotated
    category_count.short_description = "Kategori"
    category_count.admin_order_field = "category_count_annotated"

    @admin.action(description="Seçilenleri aktif et")
    def activate_brands(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} marka aktif edildi.")

    @admin.action(description="Seçilenleri pasif et")
    def deactivate_brands(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} marka pasif edildi.", messages.WARNING)

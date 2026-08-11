from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from django.db import transaction

from products.models import (
    Brand,
    BrandRequest,
    CategoryBrand,
)

@admin.register(BrandRequest)
class BrandRequestAdmin(admin.ModelAdmin):

    list_display = (
        "brand_name",
        "parent_category",
        "category",
        "seller",
        "short_note",
        "status_badge",
        "created_at",
    )
    

    list_filter = (
        "status",
        "category__parent",
        "category",
        "created_at",
    )

    search_fields = (
        "brand_name",
        "category__name",
        "seller__email",
        "seller__phone_number",
    )

    autocomplete_fields = (
        "seller",
        "reviewed_by",
        "category",
    )

    readonly_fields = (
        "seller",
        "category",
        "brand_name",
        "note",
        "created_at",
        "reviewed_by",
        "reviewed_at",
    )

    ordering = (
        "-created_at",
    )

    actions = (
        "approve_requests",
        "reject_requests",
    )

    fieldsets = (
        (
            "Talep",
            {
                "fields": (
                    "seller",
                    "category",
                    "brand_name",
                    "note",
                )
            },
        ),
        (
            "İnceleme",
            {
                "fields": (
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                )
            },
        ),
        (
            "Zaman",
            {
                "fields": (
                    "created_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    list_select_related = (
        "seller",
        "category",
        "category__parent",
        "reviewed_by",
    )

    

    @admin.action(description="Seçilen talepleri onayla")
    def approve_requests(
        self,
        request,
        queryset,
    ):
        approved = 0

        with transaction.atomic():
            for brand_request in queryset.filter(
                status=BrandRequest.Status.PENDING
            ):
            

                brand, _ = Brand.objects.get_or_create(
                    name = brand_request.brand_name.strip(),
                    defaults={
                        "is_active": True,
                    },
                )
                CategoryBrand.objects.get_or_create(
                    category=brand_request.category,
                    brand=brand,
                )
                brand_request.status = BrandRequest.Status.APPROVED
                brand_request.reviewed_by = request.user
                brand_request.reviewed_at = timezone.now()
                brand_request.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                    ]
                )
                approved += 1

        self.message_user(
            request,
            f"{approved} marka talebi onaylandı.",
            messages.SUCCESS,
        )

    @admin.action(description="Seçilen talepleri reddet")
    def reject_requests(
        self,
        request,
        queryset,
    ):
        updated = queryset.filter(
            status=BrandRequest.Status.PENDING
        ).update(
            status=BrandRequest.Status.REJECTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )

        self.message_user(
            request,
            f"{updated} marka talebi reddedildi.",
            messages.WARNING,
        )
    
    def status_badge(self, obj):
        colors = {
            BrandRequest.Status.PENDING: (
                "#fff3cd",
                "#856404",
            ),
            BrandRequest.Status.APPROVED: (
                "#d4edda",
                "#155724",
            ),
            BrandRequest.Status.REJECTED: (
                "#f8d7da",
                "#721c24",
            ),
        }

        bg, color = colors.get(obj.status,("#eee", "#333"),)

        return format_html(
            '<span style="background:{};color:{};padding:4px 10px;border-radius:12px;">{}</span>',
            bg,
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Durum"
    status_badge.admin_order_field = "status"

    def parent_category(self, obj):
        return obj.category.parent.name if obj.category.parent else "-"

    parent_category.short_description = "Ana Kategori"
    parent_category.admin_order_field = "category__parent__name"

    def short_note(self, obj):
        if not obj.note:
            return "-"

        if len(obj.note) <= 40:
            return obj.note

        return obj.note[:40] + "..."
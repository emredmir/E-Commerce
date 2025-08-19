from django.contrib import admin
from .models import CustomUser, SellerProfile

@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'is_approved', 'created_at')
    list_filter = ('is_approved',)
    search_fields = ('user__email', 'company_name')
    actions = ['approve_sellers']

    def approve_sellers(self, request, queryset):
        for seller in queryset:
            seller.is_approved = True
            seller.save()
        self.message_user(request, f"{queryset.count()} satıcı onaylandı.")
    approve_sellers.short_description = "Seçilen satıcıları onayla"
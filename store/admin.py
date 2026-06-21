from django.contrib import admin
from .models import Store, StoreUpdateRequest, StoreStatus
from django.utils.html import format_html
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count

@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = (
        'banner_preview',
        'logo_preview',
        'store_name',
        'seller_info',
        'seller_store_count',
        'contact_email',
        'contact_phone',
        #'address',
        'status',
        'is_active',
        'created_at',
    )

    list_filter = (
        'status', 
        'is_active', 
        'created_at'
        )
    
    search_fields = (
        'store_name', 
        'seller__user__email', 
        'seller__company_name', 
        )
    
    readonly_fields = (
        'slug',
        'created_at',
        'updated_at',
        'approved_at',
        'logo_preview_large',
        'banner_preview_large',)
    
    actions = [
        'approve_stores',
        'suspend_stores',
        'reject_store',
        'activate_stores',
        'deactivate_stores',
        ]
    
    # Performans Kilidi: Tek sorguda tüm ilişkili verileri getirir
    list_select_related = ('seller', 'seller__user')

    ordering = ('-created_at',)

    fieldsets = (
        ('Genel Bilgiler', {
            'fields': ('store_name', 'contact_email', 'contact_phone', 'address', 'slug', 'seller', 'status', 'is_active', )
        }),
        ('Görsel', {
            'fields': ('logo', 'logo_preview_large', 'banner', 'banner_preview_large'),
        }),
        ('Zaman Bilgileri', {
            'fields': ('approved_at', 'created_at', 'updated_at'),
            'classes': ('collapse',), # Gizlenebilir bölüm
        }),
    )

    def logo_preview(self, obj):
        if obj.logo:
            return format_html(
                '<img src="{}" style="width:40px; height:40px; object-fit:cover; border-radius:50%; border:1px solid #ccc;" />',
                obj.logo.url
            )
        return format_html('<span style="color: #999;">Görsel Yok</span>')
    logo_preview.short_description = "Logo"

    def logo_preview_large(self, obj):
        if obj.logo:
            return format_html(
                '<div style="background:#f8f8f8; padding:10px; display:inline-block; border:1px solid #ddd;">'
                '<img src="{}" style="max-width:300px; display:block;" />'
                '</div>',
                obj.logo.url
            )
        return "Henüz bir logo yüklenmedi."
    logo_preview_large.short_description = "Mevcut Logo Önizlemesi"

    def banner_preview(self, obj):
        if obj.banner:
            return format_html(
                '<img src="{}" style="width:60px; height:30px; object-fit:cover; border-radius:4px; border:1px solid #eee;" />',
                obj.banner.url
            )
        return format_html('<span style="color:#999;">Banner Yok</span>')
    banner_preview.short_description = "Banner"

    def banner_preview_large(self, obj):
        if obj.banner:
            return format_html(
                '<img src="{}" style="max-width:100%; border:1px solid #ddd;" />',
                obj.banner.url
            )
        return "Henüz bir banner yüklenmedi."
    banner_preview_large.short_description = "Mevcut Banner Önizlemesi"

    def seller_info(self, obj):
        return format_html(
            '<b>{}</b><br><span style="font-size:12px; color:#666;">{}</span>',
            obj.seller.company_name,
            obj.seller.user.email
        )
    seller_info.short_description = "Mağaza Sahibi"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(seller_store_count_annotated=Count('seller__stores', distinct=True))

    def seller_store_count(self, obj):
        return obj.seller_store_count_annotated
    seller_store_count.short_description = "Toplam Mağaza"

    @admin.action(description="Seçilen mağazaları onayla")
    def approve_stores(self, request, queryset):
        for store in queryset:
            store.approve()
        self.message_user(request, f"{queryset.count()} mağaza başarıyla onaylandı ve yayına alındı.", messages.SUCCESS)

    @admin.action(description="Seçilen mağazaları askıya al")
    def suspend_stores(self, request, queryset):
        for store in queryset:
            store.suspend()
        self.message_user(request, f"{queryset.count()} mağaza askıya alındı.", level=messages.WARNING)

    @admin.action(description="Seçilen mağazaları reddet")
    def reject_store(self, request, queryset):
        for store in queryset:
            store.reject()
        self.message_user(request, f"{queryset.count()} mağaza başvurusu reddedildi.", messages.ERROR)

    @admin.action(description="Seçilenleri Aktif Et (Görünür Yap)")
    def activate_stores(self, request, queryset):
        for store in queryset:
            store.approve()
        self.message_user(request, f"{queryset.count()} mağaza aktif edildi.")

    @admin.action(description="Seçilenleri Pasif Et (Gizle)")
    def deactivate_stores(self, request, queryset):
        for store in queryset:
            store.suspend() 
            
        self.message_user(request, f"{queryset.count()} mağaza pasif edildi.")

@admin.register(StoreUpdateRequest)
class StoreUpdateRequestAdmin(admin.ModelAdmin):
    list_display = ('store', 'created_at', 'status')
    list_filter = ('status',)
    list_select_related = ('store',)
    actions = ['approve_changes', 'reject_changes']

    @admin.action(description="Değişiklikleri Onayla ve Mağazaya Uygula")
    def approve_changes(self, request, queryset):
        count = 0
        for req in queryset:
            if req.status != StoreStatus.PENDING:
                continue
                
            store = req.store
            
            # 1. Yeni verileri asıl mağazaya aktar
            # Eğer alan doluysa güncelle, boşsa eskisini elleme
            if req.new_store_name:
                store.store_name = req.new_store_name
                store.slug = ""
                
            if req.new_logo:
                store.logo = req.new_logo
                
            if req.new_banner:
                store.banner = req.new_banner
            
            if req.new_contact_email:
                store.contact_email = req.new_contact_email
            
            if req.new_contact_phone:
                store.contact_phone = req.new_contact_phone

            if req.new_address:
                store.address = req.new_address
            
            store.save() # Asıl mağazayı güncelle
            
            # 2. İsteği tamamlandı olarak işaretle
            req.status = StoreStatus.APPROVED
            req.save()
            count += 1
            
        self.message_user(request, f"{count} değişiklik isteği onaylandı ve uygulandı.")
    
    @admin.action(description="Değişiklikleri Reddet")
    def reject_changes(self, request, queryset):
        count = queryset.filter(status= StoreStatus.PENDING).update(status= StoreStatus.REJECTED)
        self.message_user(request, f"{count} değişiklik isteği reddedildi.", messages.WARNING)
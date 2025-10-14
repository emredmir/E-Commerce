from django.contrib import admin
from .models import CustomUser, SellerProfile, Address
from django.contrib.auth.admin import UserAdmin
from .forms import CustomUserCreationForm
from django.utils.translation import gettext_lazy

@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'is_approved', 'created_at')
    list_filter = ('is_approved',)
    search_fields = ('user__email', 'company_name')
    readonly_fields = ('created_at',)
    actions = ['approve_sellers']

    def approve_sellers(self, request, queryset):
        for seller in queryset:
            seller.is_approved = True
            seller.save()
        self.message_user(request, f"{queryset.count()} satıcı onaylandı.")
    approve_sellers.short_description = "Seçilen satıcıları onayla"

class AddressInline(admin.TabularInline):
    model = Address
    extra = 1

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    model = CustomUser
    verbose_name_plural = "Addresses"
    
    # Kullanıcı listesi görünümü
    list_display = ('email', 'phone_number', 'first_name', 'last_name', 'is_seller', 'is_staff')
    list_filter = ('is_staff', 'is_superuser', 'is_seller')
    ordering = ('-date_joined',)
    search_fields = ('email', 'phone_number', 'first_name', 'last_name')
    readonly_fields = ('date_joined', 'last_login')

    # Kullanıcı detay görünümü (mevcut kullanıcılar için)
    fieldsets = (
        (gettext_lazy('Login info'), {'fields': ('email', 'phone_number', 'password')}),
        (gettext_lazy('Personal info'), {'fields': ('first_name', 'last_name', 'is_seller')}),
        (gettext_lazy('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        (gettext_lazy('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )

    # Kullanıcı ekleme görünümü
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'phone_number', 'first_name', 'last_name', 'password1', 'password2'),
        }),
    )
    
    # Kullanıcıya ait adresleri aynı sayfada göster
    inlines = [AddressInline]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('user','title','full_name','phone_number','address_line1','address_line2','city','state','postal_code','is_default','created_at')
    list_filter = ('is_default', 'city', 'state')
    search_fields = ('user__email', 'title', 'full_name','phone_number','address_line1','address_line2','city','state','postal_code')
    ordering = ['-is_default', '-created_at']
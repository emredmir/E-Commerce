from django.db import models
from accounts.models import SellerProfile
from django.utils.text import slugify
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid



class StoreStatus(models.TextChoices):
    PENDING = 'pending', 'Onay Bekliyor'
    APPROVED = 'approved', 'Onaylandı'
    REJECTED = 'rejected', 'Reddedildi'
    SUSPENDED = 'suspended', 'Askıya Alındı'
    ARCHIVED = 'archived', 'Arşivlendi'


def store_logo_path(instance, filename):
    slug = instance.slug or slugify(instance.store_name)

    if not slug:
        slug = f"temp-store-{uuid.uuid4().hex[:8]}"

    return f"stores/{slug}/logo/{filename}"

def store_banner_path(instance, filename):
    slug = instance.slug or slugify(instance.store_name)

    if not slug:
        slug = f"temp-store-{uuid.uuid4().hex[:8]}"
        
    return f"stores/{slug}/banner/{filename}"

class Store(models.Model):
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name='stores')
    store_name = models.CharField(max_length=255, verbose_name="Mağaza Adı")
    slug = models.SlugField(unique=True, blank=True)

    logo = models.ImageField(upload_to=store_logo_path, blank=True, null=True)
    banner = models.ImageField(upload_to=store_banner_path, blank=True, null=True)

    contact_email = models.EmailField(verbose_name="Mağaza E-posta")
    contact_phone = models.CharField(max_length=15, blank=True, verbose_name="Mağaza Telefon")

    address = models.CharField(max_length=255, blank=True, verbose_name="Adres Satırı")
    status = models.CharField(max_length=10, choices=StoreStatus.choices, default=StoreStatus.PENDING)
    is_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(blank=True, null=True)


    def clean(self):
        # Güncelleme yaparken sayıyı kontrol etmeye gerek yok
        if not self.pk: 
            # ModelForm form.is_valid() aşamasındayken 'seller' henüz atanmamış olabilir.
            # O yüzden önce seller_id'nin var olup olmadığına bakıyoruz.
        # 1. Senaryo: Arşivlenmiş olanlar dahil TOPLAM mağaza sayısı
            total_count = self.seller.stores.count()
            if total_count >= 5:
                raise ValidationError("Toplam mağaza açma sınırınıza (arşivlenenler dahil 5 adet) ulaştınız.")

            # 2. Senaryo: Sadece AKTİF/BEKLEYEN (Arşivlenmemiş) mağaza sayısı
            non_archived_count = self.seller.stores.exclude(status=StoreStatus.ARCHIVED).count()
            if non_archived_count >= 3:
                raise ValidationError("Aynı anda en fazla 3 adet aktif veya bekleyen mağazaya sahip olabilirsiniz.")
        super().clean()
    
    def archive(self):
        # Mağazayı kullanıcı sildiğinde arşive (Soft-Delete) al
        self.status = StoreStatus.ARCHIVED
        self.is_active = False
        self.save(update_fields=['status', 'is_active', 'updated_at'])


    def save(self, *args, **kwargs):
        # Model validation çalıştır # Kaydetmeden önce clean metodunu zorla çalıştır
        self.full_clean()

        if not self.slug:
            base_slug = slugify(self.store_name)
            if not base_slug:
                base_slug = f"store-{self.seller.id}-{uuid.uuid4().hex[:4]}"
            slug = base_slug
            counter = 1

            while Store.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            self.slug = slug
        
        self.is_active = self.status == StoreStatus.APPROVED

        if self.is_active and self.approved_at is None:
            self.approved_at = timezone.now()
        
        super().save(*args, **kwargs)
    
    def approve(self):
        """Mağazayı onayla ve tarihlendir."""
        self.status = StoreStatus.APPROVED
        self.is_active = True

        if self.approved_at is None:
            self.approved_at = timezone.now()
        self.save(update_fields=['status', 'is_active', 'approved_at', 'updated_at'])

    def suspend(self):
        """Mağazayı askıya al."""
        self.status = StoreStatus.SUSPENDED
        self.is_active = False
        self.save(update_fields=['status', 'is_active',  'updated_at'])

    def reject(self):
        """Mağazayı reddet."""
        self.status = StoreStatus.REJECTED
        self.is_active = False
        self.save(update_fields=['status', 'is_active', 'updated_at'])
    
    def __str__(self):
        return self.store_name
    
    class Meta:
        verbose_name = "Mağaza"
        verbose_name_plural = "Mağazalar"


class StoreUpdateRequest(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='change_requests')

    new_store_name = models.CharField(max_length=255, blank=True, verbose_name="Yeni Mağaza Adı")
    new_logo = models.ImageField(upload_to='store_changes/logos/', blank=True, null=True)
    new_banner = models.ImageField(upload_to='store_changes/banners/', blank=True, null=True)

    new_contact_email = models.EmailField(blank=True, verbose_name="Yeni E-posta")
    new_contact_phone = models.CharField(max_length=15, blank=True, verbose_name="Yeni Telefon")

    new_address = models.CharField(max_length=255, blank=True, verbose_name="Yeni Adres Satırı")

    status = models.CharField(max_length=10, choices=StoreStatus.choices, default=StoreStatus.PENDING)

    created_at = models.DateTimeField(auto_now_add=True) 
    #Model ilk kez veritabanına kaydedildiği anı otomatik olarak yazar ve bir daha asla değişmez.
    
    def __str__(self):
        return f"{self.store.store_name} - Değişiklik İsteği"
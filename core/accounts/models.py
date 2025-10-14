from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import CustomUserManager
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError

# Telefon numarası için düzenli ifade doğrulayıcısı
phone_validator = RegexValidator(
    regex=r'^\+?1?\d{9,15}$',
    message="Geçerli bir telefon numarası girin. Örn: +905xxxxxxxxx"
)

iban_validator = RegexValidator(
    regex=r'^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$',
    message="Geçerli bir IBAN girin."
)

class CustomUser(AbstractUser):
    username = None  # Django'nun varsayılan username alanını kaldırıyoruz
    email = models.EmailField(unique=True, blank=True, null=True)
    phone_number = models.CharField(max_length=15, unique=True, blank=True, null=True, validators=[phone_validator])
    is_seller = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    objects = CustomUserManager()

    def __str__(self):
        return self.email or self.phone_number or "Yeni Kullanıcı"
        
    class Meta:
        verbose_name = "Kullanıcı"
        verbose_name_plural = "Kullanıcılar"
    
    def clean(self):
    # Admin panel veya shell'de kullanıcı kaydederken validasyon
        if not self.email and not self.phone_number:
            raise ValidationError("En az bir iletişim bilgisi (email veya telefon) girilmelidir.")


class Address(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='addresses')
    title = models.CharField(max_length=100, help_text="Örnek: Ev, İş")
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=15, validators=[phone_validator])
    address_line1 = models.CharField("Adres Satırı 1", max_length=255)
    address_line2 = models.CharField("Adres Satırı 2", max_length=255, blank=True)
    city = models.CharField("Şehir", max_length=100)
    state = models.CharField("İlçe", max_length=100)
    postal_code = models.CharField("Posta Kodu", max_length=20)
    is_default = models.BooleanField("Varsayılan Adres", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Adres"
        verbose_name_plural = "Adresler"
        ordering = ["-is_default", "-id"]
        constraints = [
        models.UniqueConstraint(fields=['user', 'title'], name='unique_user_title')
    ]

    def save(self, *args, **kwargs):
        if self.is_default:
            # Aynı kullanıcının diğer adreslerini varsayılanlıktan kaldır
            Address.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} - {self.full_name}"


class SellerProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='seller_profile')
    company_name = models.CharField(max_length=255)
    company_address = models.TextField()
    company_phone = models.CharField(max_length=15, blank=True, null=True, validators=[phone_validator])
    iban = models.CharField(max_length=34, validators=[iban_validator], verbose_name="IBAN")
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Satıcı Profili"
        verbose_name_plural = "Satıcı Profilleri"

    def __str__(self):
        return f"{self.user.email} - {self.company_name}"
    
    def clean(self):
        super().clean()
        
        # Eğer IBAN alanı doldurulmuşsa
        if self.iban:
            # IBAN'ın ilk 2 karakterini alarak ülke kodunu kontrol et
            country_code = self.iban[:2].upper()
            
            # Türkiye IBAN'ı için özel kontrol
            if country_code == 'TR':
                if len(self.iban) != 26:
                    raise ValidationError("Türkiye'ye ait IBAN'lar 26 karakter uzunluğunda olmalıdır.")
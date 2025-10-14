from django.contrib.auth.base_user import BaseUserManager

class CustomUserManager(BaseUserManager):
    def create_user(self, email=None, phone_number=None, password=None, **extra_fields):
        if not email and not phone_number:
            raise ValueError("Email veya telefon numarası gereklidir.")
        
        user = self.model(
            email=self.normalize_email(email) if email else None,
            phone_number=phone_number,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if not extra_fields.get("is_staff") or not extra_fields.get("is_superuser"):
            raise ValueError("Superuser için is_staff=True ve is_superuser=True olmalı.")
        
        if not email:
            raise ValueError('Superuser için email gereklidir.')

        return self.create_user(email=email, phone_number=None, password=password, **extra_fields)
from django.contrib.auth.base_user import BaseUserManager

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email adresi zorunludur.")
        if not password:
            raise ValueError("Şifre zorunludur.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if not extra_fields.get("is_staff"):
            raise ValueError("Superuser is_staff=True olmalı.")
        if not extra_fields.get("is_superuser"):
            raise ValueError("Superuser is_superuser=True olmalı.")

        return self.create_user(email, password, **extra_fields)
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import SellerProfile

@receiver(post_save, sender=SellerProfile)
def update_user_is_seller(sender, instance, **kwargs):
    if instance.is_approved and not instance.user.is_seller:
        instance.user.is_seller = True
        instance.user.save(update_fields=["is_seller"])
    elif not instance.is_approved and instance.user.is_seller:
        instance.user.is_seller = False
        instance.user.save(update_fields=["is_seller"])
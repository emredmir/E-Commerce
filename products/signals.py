from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import StoreProduct, ProductPriceHistory, ProductImage

# ---------------------------------------------------------
# Sinyaller (Signals): Güvenli & Idempotent Fiyat Takip Mekanizması
# ---------------------------------------------------------
@receiver(pre_save, sender=StoreProduct)
def track_price_change(sender, instance, **kwargs):
    # .first() exception fırlatmaz, bulamazsa None döner.
    # Yeni kayıtlarda post_save created=True'dan geçeceği için else bloğuna ihtiyaç yoktur.
    if instance.pk:
        old_price = StoreProduct.objects.filter(pk=instance.pk).values_list('price', flat=True).first()
        if old_price is not None and old_price != instance.price:
            instance._price_changed = True

@receiver(post_save, sender=StoreProduct)
def create_price_history(sender, instance, created, **kwargs):
    if created or getattr(instance, '_price_changed', False):
        # DUPLICATE HISTORY KORUMASI: Aynı request içinde ardışık save() çağrıları olursa
        # veya admin işlemlerinde sahte history atılmasını engeller. son kayda bakılır.
        last_history = instance.price_history.order_by('-created_at').first()
        if not last_history or last_history.price != instance.price:
            ProductPriceHistory.objects.create(
                store_product=instance,
                price=instance.price
            )

        # Obje state'ini sıfırla ki multiple save()'lerde gereksiz DB yorgunluğu olmasın    
        if hasattr(instance, '_price_changed'):
            del instance._price_changed


@receiver(post_delete, sender=ProductImage)
def delete_product_image(sender, instance, **kwargs):
    """
    ProductImage silinince fiziksel dosyayı da sil.
    """
    if instance.image:
        instance.image.delete(save=False)


@receiver(pre_save, sender=ProductImage)
def delete_old_product_image(sender, instance, **kwargs):
    """
    Görsel değiştirilirse eski dosyayı sil.
    """
    if not instance.pk:
        return

    try:
        old = ProductImage.objects.get(pk=instance.pk)
    except ProductImage.DoesNotExist:
        return

    if old.image and old.image != instance.image:
        old.image.delete(save=False)
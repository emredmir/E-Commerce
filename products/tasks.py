import logging
from celery import shared_task
from django.db import transaction

logger = logging.getLogger(__name__)

@shared_task
def async_copy_product_images(draft_id, product_id, is_merge=False):
    """
    Ürün yayınlandıktan sonra ağır olan görsel kopyalama 
    işlemini arka planda (Asenkron) yapar.
    """
    from products.models import ProductDraft, Product
    from products.services.publish import DraftPublishService

    try:
        draft = ProductDraft.objects.get(pk=draft_id)
        product = Product.objects.get(pk=product_id)

        # Transaction bloğuna alıyoruz ki görsel kopyalarken hata çıkarsa yarıda kalmasın
        with transaction.atomic():
            if is_merge:
                DraftPublishService._copy_missing_images(draft=draft, product=product)
            else:
                DraftPublishService._copy_images(draft=draft, product=product)
                
        logger.info(f"Görseller başarıyla kopyalandı. Product ID: {product_id}")

    except Exception as e:
        logger.error(f"Görsel kopyalama taskı başarısız oldu! Draft ID: {draft_id} Hata: {str(e)}")
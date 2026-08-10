from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from products.models import ProductDraft, ProductDraftImage

class Command(BaseCommand):
    help = '30 günden eski tamamlanmış (PUBLISHED) veya iptal edilmiş (CANCELED) taslakları ve fiziksel dosyalarını temizler.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Kaç günden eski taslakların silineceğini belirtir (Varsayılan: 30)'
        )

    def handle(self, *args, **options):
        days = options['days']
        cutoff_date = timezone.now() - timedelta(days=days)

        # 1. Silinecek taslakları filtrele
        old_drafts = ProductDraft.objects.filter(
            status__in=[ProductDraft.Status.PUBLISHED, ProductDraft.Status.CANCELED],
            updated_at__lt=cutoff_date
        )

        drafts_count = old_drafts.count()

        if drafts_count == 0:
            self.stdout.write(self.style.SUCCESS('Silinecek eski taslak bulunamadı.'))
            return

        self.stdout.write(self.style.WARNING(f'{drafts_count} adet eski taslak siliniyor...'))

        # 2. Fiziksel dosyaları silmek için ilgili görselleri bul
        draft_images = ProductDraftImage.objects.filter(group__draft__in=old_drafts)
        images_count = draft_images.count()

        with transaction.atomic():
            # Önce fiziksel dosyaları storage'dan sil
            for draft_image in draft_images:
                if draft_image.image:
                    draft_image.image.delete(save=False)
            
            # Veritabanı kayıtlarını sil (cascade sayesinde gruplar ve varyantlar da silinir)
            old_drafts.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'Başarılı! {drafts_count} taslak ve {images_count} fiziksel görsel sunucudan temizlendi.'
            )
        )
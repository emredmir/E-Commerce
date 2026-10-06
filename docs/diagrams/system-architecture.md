# Sistem Mimarisi Diyagramı

> GitHub'ın normal Markdown görünümünde aşağıdaki Mermaid diyagramı kutular ve bağlantılar şeklinde render edilir. `Raw` görünümünde ise Mermaid kaynak kodu görülür.

```mermaid
flowchart TB
    C[Client / Browser]
    R[Django URL Router]
    V[Views / API Views]
    F[Forms / Input Validation]

    A[accounts]
    S[store]
    P[products]
    CT[cart]
    O[orders]
    CORE[core]

    ORM[Django ORM]
    DB["(Microsoft SQL Server)"]
    IY[iyzico]
    CEL[Celery Worker]
    MEDIA[Media / File Storage]

    C --> R
    R --> CORE
    R --> A
    R --> S
    R --> P
    R --> CT
    R --> O
    R --> IY

    A --> F
    P --> F
    S --> F

    A --> ORM
    S --> ORM
    P --> ORM
    CT --> ORM
    O --> ORM
    CORE --> ORM

    ORM --> DB

    O --> IY
    A --> IY
    P --> CEL
    CEL --> P
    P --> MEDIA
    S --> MEDIA
```

## Notlar

- `Client / Browser`, uygulamanın kullanıcı arayüzünü temsil eder; ayrı bir SPA frontend'i olarak modellenmemiştir.
- `Views / API Views`, HTML sayfaları ile JSON endpoint'lerini birlikte temsil eder.
- `Services`, domain servisleri uygulama içinde ilgili app'lerin altında bulunur. Diyagramın sade kalması için her service dosyası ayrı kutu yapılmamıştır.
- `iyzico` ödeme/sub-merchant/card/refund gibi dış servis entegrasyonlarını temsil eder.
- `Celery Worker`, mevcut async ürün görseli kopyalama task'ını temsil eder.
- `Media / File Storage`, Django `MEDIA_ROOT` tabanlı medya dosyalarını temsil eder; production storage topolojisi kaynak koddan kesinleştirilmemiştir.

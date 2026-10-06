# Dış Servis ve Asenkron İşlem Diyagramı

```mermaid
flowchart TB
    WEB[Django Web / API Layer]
    PS[Product Services]
    OS[Order / Payment Services]
    CEL[Celery Worker]
    IY[iyzico]
    DB["(MSSQL)"]
    FILES[Media Files]

    WEB --> PS
    WEB --> OS

    PS --> DB
    OS --> DB

    PS -->|async image copy| CEL
    CEL --> FILES

    OS -->|payment / 3DS / webhook / refund / submerchant| IY
    IY -->|callback / webhook| WEB
```

## Entegrasyonlar

### iyzico

Ödeme, 3DS callback, webhook, kayıtlı kart, refund ve seller sub-merchant işlemleri için kullanılır.

### Celery

Ürün yayınlama sonrasındaki ağır görsel kopyalama işlemini request akışından ayırmak için kullanılır.

### MSSQL

Uygulamanın kalıcı transactional verisinin ana kaynağıdır.

# Order ve SubOrder Yaşam Döngüsü

## Ana Order

```mermaid
stateDiagram-v2
    [*] --> PENDING_PAYMENT
    PENDING_PAYMENT --> PAID : başarılı payment finalization
    PAID --> PREPARING : mevcut kodda doğrudan merkezi transition yok
    PREPARING --> PARTIALLY_SHIPPED : gelecekte aggregate durum
    PREPARING --> SHIPPED : gelecekte aggregate durum
    SHIPPED --> DELIVERED : gelecekte aggregate durum
    DELIVERED --> COMPLETED : gelecekte aggregate durum
    PENDING_PAYMENT --> EXPIRED : reservation / payment süresi senaryosu
    PAID --> CANCELLED : uygulamanın ilgili cancellation kuralları
```

> Ana `OrderStatus` enum'unda bu değerlerin tamamı bulunur; ancak mevcut kodun merkezi bir Order aggregate/status transition motoru tüm sonraki geçişleri otomatik olarak uygulamamaktadır. Diyagramdaki bazı geçişler bu nedenle mevcut state vocabulary'sini gösteren dokümantasyon niteliğindedir.

## SubOrder

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> PREPARING : OrderService.start_suborder_preparation()
    PREPARING --> SHIPPED : ShippingService.ship_suborder()
    SHIPPED --> DELIVERED : ShippingService.mark_suborder_delivered()

    PENDING --> CANCELLED : CancellationService
    PREPARING --> CANCELLED : CancellationService

    DELIVERED --> [*]
    CANCELLED --> [*]
```
